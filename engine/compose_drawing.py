# -*- coding: utf-8 -*-
u"""
図枠合成エンジン(恒久モジュール・フェーズ2.5)。

図枠テンプレート(図枠/frame_template.dxf) + SWビュー投影DXF(phase2出力) + 表題欄記入 を
合成し、「寸法以外が揃った1枚の部品図DXF」を作る。

compose(views_dxf_path, meta_json_path, fields, scale, out_path) -> 検証情報dict

設計方針(再利用可能に。ハードコードは図枠バケツA固有の定数のみ):
  1. 図枠(frame_template.dxf)をそのまま読み込み、共通113エンティティを土台にする
     (AC1015/cp932を明示維持。CLAUDE.md知見: doc.encodingを明示しないとANSI_1252へ化ける)
  2. ビューDXF(調査/phase2_out_*.dxf)の4ビューをmeta jsonのoutline_mmで領域分類し、
     第三角法の相対整列(front-top同一x中心/front-right同一y中心)を保ったまま、
     A1由来の広いビュー間隔(40mm)をA3向けの狭い間隔(VIEW_GAP_MM)に詰め直して再配置する
     (「グループとして平行移動」のみだと A1:841x594 の間隔がA3の作図エリアに収まらないため、
     間隔だけを詰めて整列関係は保つ設計。詳細は 調査/phase2_5_compose_report.md 参照)
  3. scale(尺度)は幾何に一様スケールとして適用(scale=1なら無変形=実寸のまま)
  4. 表題欄・左上ノート・連番マークを fields.json のアンカー/スタイル/書式で記入
  5. ビューDXFのHIDDEN/PHANTOM等の線種定義を明示的に図枠doc内へ保証する
  6. 戻り値は検証情報dict(呼び出し側/本モジュールのCLIが数値検証・PNG化に使う)

使い方(CLI. TEST-001の生成もこれで行う):
    python engine/compose_drawing.py
"""
import io
import json
import os
import re
import sys
import datetime

import ezdxf
from ezdxf.addons.importer import Importer
from ezdxf.bbox import extents as bbox_extents
from ezdxf.math import Matrix44

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.frame_extract import subtract_frame  # noqa: E402

# ---------------------------------------------------------------------------
# 図枠バケツA(A3実寸410x287)の固定定数(図枠/frame_analysis.md, フェーズ1裁定より)
# ---------------------------------------------------------------------------
FRAME_TEMPLATE_DEFAULT = "図枠/frame_template.dxf"
FIELDS_JSON_DEFAULT = "図枠/fields.json"

FRAME_X0, FRAME_Y0, FRAME_X1, FRAME_Y1 = 5.0, 5.0, 415.0, 292.0
# 表題欄の実測上端は28.59mm(全113共通エンティティのLINE最大y)。余裕を見て30.0とする
TITLE_BLOCK_RECT = (FRAME_X0, FRAME_Y0, FRAME_X1, 30.0)
# 左上ノート(連番マーク丸 中心(19.324,278.844) r=10 + 材質/個数ノート)の占有域。
# 実測: 丸はx[9.3,29.3]/y[268.8,288.8]、自製品2行ノート(attach=middle-left)はy[268.3,291.7]付近。
# 購入品4行ノートはより幅広になり得るため、x方向は保守的に115mmまで確保する。
NOTE_ZONE_RECT = (FRAME_X0, 255.0, 115.0, FRAME_Y1)
# ビュー間の目標余白(A1由来の40mm gapをA3向けに詰め直す。第三角整列は保持)
VIEW_GAP_MM = 15.0

VIEW_KEYS = ("front", "top", "right", "iso")

ATTACH = {
    "top-left": 1, "top-center": 2, "top-right": 3,
    "middle-left": 4, "middle-center": 5, "middle-right": 6,
    "bottom-left": 7, "bottom-center": 8, "bottom-right": 9,
}

# fields.json記載の"align"が実データと不一致と判明した項目の上書き(ground truth)。
# 根拠: bucket_A実ファイル(25154-1-08_シャフト.dxf等)を実測。詳細は compose report 参照。
ALIGN_OVERRIDE = {
    # fields.json記載は"bottom-left"だが、実データ(複数ファイル)はattach=4(middle-left)
    "材質個数ノート_自製品": "middle-left",
    # fields.json記載は"bottom-center(推定)"だが実データはattach=7(bottom-left)+テキスト幅で
    # 手動芯出し。自動生成では決定論的にbottom-center(=8)へ寄せて丸の中心に必ず揃える
    "連番マーク_値": "bottom-center",
}


# ---------------------------------------------------------------------------
# 文字ユーティリティ
# ---------------------------------------------------------------------------
def to_zenkaku(s):
    u"""半角英数記号を全角へ変換(Unicode全角/半角形の規則的オフセット+0xFEE0を利用)。
    かな/カナ/漢字は対象外(すでに全角なのでそのまま通る)。"""
    out = []
    for ch in s:
        code = ord(ch)
        if ch == " ":
            out.append(u"\u3000")
        elif 0x21 <= code <= 0x7E:
            out.append(chr(code + 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


ZEN_DIGITS = u"０１２３４５６７８９"
_ZEN2HALF_DIGITS = str.maketrans(ZEN_DIGITS, "0123456789")


def _trailing_serial(text, width=2):
    u"""文字列末尾の数字列(半角/全角どちらでも)を取り出し、width桁にゼロ埋めし全角で返す。"""
    m = re.search(r"([0-9\uFF10-\uFF19]+)$", text or "")
    if not m:
        return to_zenkaku(str(0).zfill(width))
    half = m.group(1).translate(_ZEN2HALF_DIGITS)
    tail = half[-width:].rjust(width, "0")
    return to_zenkaku(tail)


# ---------------------------------------------------------------------------
# fields.json 読み込み・スタイル解決
# ---------------------------------------------------------------------------
def _load_field_specs(fields_json_path):
    with io.open(fields_json_path, encoding="utf-8") as f:
        spec = json.load(f)
    return {f["field"]: f for f in spec["fields"]}


def _resolve_align(field_name, spec):
    align = ALIGN_OVERRIDE.get(field_name, spec["align"])
    align = align.split("(")[0].strip()  # "bottom-center(推定...)" 等の注記を除去
    return ATTACH.get(align, 7)


def _ensure_style(doc, font, width_factor, oblique=0.0, tol=1e-4):
    u"""(font, width_factor, oblique) が実質一致する既存STYLEがあれば再利用し、
    無ければ新規作成する(CLAUDE.md知見: STYLE名は自動採番されるため名前でなく実体で比較)。"""
    for st in doc.styles:
        try:
            if (str(st.dxf.font).lower() == str(font).lower()
                    and abs(st.dxf.width - width_factor) < tol
                    and abs(st.dxf.oblique - oblique) < tol):
                return st.dxf.name
        except Exception:
            continue
    existing = {s.dxf.name for s in doc.styles}
    n = 1
    name = "VF%03d" % n
    while name in existing:
        n += 1
        name = "VF%03d" % n
    doc.styles.add(name, font=font, dxfattribs={"width": width_factor, "oblique": oblique})
    return name


def _add_field_mtext(doc, msp, field_name, spec, text, x=None, y=None, height=None, layer="0"):
    attach = _resolve_align(field_name, spec)
    fr = spec["style_resolved"]
    style_name = _ensure_style(doc, fr["font"], fr["width_factor"], fr.get("oblique", 0.0))
    ax, ay = spec["anchor"]
    if x is not None:
        ax = x
    if y is not None:
        ay = y
    h = height if height is not None else spec["height"]
    return msp.add_mtext(text, dxfattribs={
        "style": style_name,
        "char_height": h,
        "attachment_point": attach,
        "insert": (ax, ay, 0.0),
        "layer": layer,
    })


# ---------------------------------------------------------------------------
# 線種の保証(タスク要件5)
# ---------------------------------------------------------------------------
def _ensure_linetypes(doc, src_doc, importer, names=("HIDDEN", "PHANTOM", "CENTER")):
    u"""ビューDXFが使う線種が図枠doc側にも定義されていることを保証する。
    通常はImporterがエンティティ取り込み時に自動解決するが、明示的にlinetypesテーブルを
    先に取り込むことで確実にする(要件5の「保証」を満たすため明示チェック+補完)。"""
    added = []
    for name in names:
        if name in doc.linetypes:
            continue
        if src_doc is not None and name in src_doc.linetypes:
            importer.import_table("linetypes", entries=[name])
            added.append(name)
    still_missing = [n for n in names if n not in doc.linetypes]
    return added, still_missing


# ---------------------------------------------------------------------------
# ビュー分類・再配置
# ---------------------------------------------------------------------------
def _classify_entities(src_msp, outlines):
    per_view = {k: [] for k in VIEW_KEYS}
    unclassified = []
    for e in src_msp:
        bb = bbox_extents([e], fast=True)
        if bb is None or not bb.has_data:
            unclassified.append(e)
            continue
        cx = (bb.extmin.x + bb.extmax.x) / 2.0
        cy = (bb.extmin.y + bb.extmax.y) / 2.0
        placed = False
        for k in VIEW_KEYS:
            x0, y0, x1, y1 = outlines[k]
            if x0 - 1e-6 <= cx <= x1 + 1e-6 and y0 - 1e-6 <= cy <= y1 + 1e-6:
                per_view[k].append(e)
                placed = True
                break
        if not placed:
            unclassified.append(e)
    return per_view, unclassified


def _layout_targets(geoms, scale):
    u"""第三角整列(front-top同一x中心/front-right同一y中心)を保ったまま、
    A3向けの詰めた間隔で4ビューの新しい中心座標を計算する。"""
    sizes = {}
    centers = {}
    for k in VIEW_KEYS:
        x0, y0, x1, y1 = geoms[k]
        sizes[k] = ((x1 - x0) * scale, (y1 - y0) * scale)
        centers[k] = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    col0_w = max(sizes["front"][0], sizes["top"][0])
    col1_w = max(sizes["right"][0], sizes["iso"][0])
    row0_h = max(sizes["front"][1], sizes["right"][1])
    row1_h = max(sizes["top"][1], sizes["iso"][1])

    gap = VIEW_GAP_MM
    group_w = col0_w + gap + col1_w
    group_h = row0_h + gap + row1_h

    avail_x0, avail_y0 = FRAME_X0, TITLE_BLOCK_RECT[3]
    avail_x1, avail_y1 = FRAME_X1, FRAME_Y1
    avail_w = avail_x1 - avail_x0
    avail_h = avail_y1 - avail_y0
    if group_w > avail_w or group_h > avail_h:
        raise ValueError(
            "scale=%.4g ではビュー群(%.1fx%.1fmm)が図枠の作図エリア(%.1fx%.1fmm)に収まりません"
            % (scale, group_w, group_h, avail_w, avail_h))

    tx0 = avail_x0 + (avail_w - group_w) / 2.0
    ty0 = avail_y0 + (avail_h - group_h) / 2.0

    col0_cx = tx0 + col0_w / 2.0
    col1_cx = tx0 + col0_w + gap + col1_w / 2.0
    row0_cy = ty0 + row0_h / 2.0
    row1_cy = ty0 + row0_h + gap + row1_h / 2.0

    targets = {
        "front": (col0_cx, row0_cy),
        "right": (col1_cx, row0_cy),
        "top": (col0_cx, row1_cy),
        "iso": (col1_cx, row1_cy),
    }
    return targets, centers, sizes


def _rect_overlap(a, b):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def compose(views_dxf_path, meta_json_path, fields, scale, out_path,
            frame_template_path=FRAME_TEMPLATE_DEFAULT,
            fields_json_path=FIELDS_JSON_DEFAULT):
    u"""
    図枠+ビュー+表題欄を合成してDXFへ保存する。

    Args:
        views_dxf_path: SWビュー投影DXF(例: 調査/phase2_out_*.dxf)
        meta_json_path: 対応するmeta json(例: 調査/phase2_meta_*.json)
        fields: dict。以下のキーを受け付ける(*は必須):
            品名*, 図番*, 装置名*                : 表題欄の値(半角で渡してもよい。全角へ自動変換)
            材質, 材質形状, 個数                 : 左上ノート(自製品2行形式)の内容
            密度_kgm3                            : 重量再計算用の密度上書き(材質未設定モデル対策)
            連番                                 : 連番マーク(未指定なら図番末尾2桁から自動生成)
            製図者                               : 既定"ＡＩ"
            製図日付                             : 既定=実行日(YY.MM.DD)
        scale: 図の尺度(1.0=実寸)。ジオメトリに一様スケールとして適用する
        out_path: 出力DXFパス
        frame_template_path / fields_json_path: 読み取り専用の入力(既定値で通常上書き不要)

    Returns:
        検証情報dict(frame_check, geometry_check, view_regions, field_anchors,
        weight, linetypes, scale, warnings 等)
    """
    warnings = []

    with io.open(meta_json_path, encoding="utf-8") as f:
        meta = json.load(f)
    field_specs = _load_field_specs(fields_json_path)

    # 1) 図枠を土台として読み込む(AC1015/cp932維持)
    doc = ezdxf.readfile(frame_template_path)
    doc.encoding = "cp932"
    doc.header["$DWGCODEPAGE"] = doc.header.get("$DWGCODEPAGE", "ANSI_932")
    msp = doc.modelspace()
    frame_entity_count = len(msp)

    # 2) ビューDXF読み込み+領域分類
    src_doc = ezdxf.readfile(views_dxf_path)
    src_msp = src_doc.modelspace()
    outlines = {k: meta["views"][k]["outline_mm"] for k in VIEW_KEYS}
    geoms = {k: meta["views"][k]["geom_mm"] for k in VIEW_KEYS}
    per_view, unclassified = _classify_entities(src_msp, outlines)
    if unclassified:
        warnings.append(
            "ビュー領域に分類できなかったエンティティ %d 個(取り込み対象外)" % len(unclassified))

    # 3) 第三角整列を保ったままA3向けに再配置(平行移動+一様スケール)
    targets, centers, sizes = _layout_targets(geoms, scale)
    view_out_bbox = {}
    for k in VIEW_KEYS:
        ocx, ocy = centers[k]
        ncx, ncy = targets[k]
        m = Matrix44.chain(
            Matrix44.translate(-ocx, -ocy, 0.0),
            Matrix44.scale(scale, scale, 1.0),
            Matrix44.translate(ncx, ncy, 0.0),
        )
        for e in per_view[k]:
            e.transform(m)
        w, h = sizes[k]
        view_out_bbox[k] = (ncx - w / 2.0, ncy - h / 2.0, ncx + w / 2.0, ncy + h / 2.0)

    # 4) 線種の保証 → その後エンティティ取り込み
    importer = Importer(src_doc, doc)
    lt_added, lt_missing = _ensure_linetypes(doc, src_doc, importer)
    if lt_missing:
        warnings.append(u"線種未定義のまま(要手当): %s" % ", ".join(lt_missing))

    all_view_entities = [e for k in VIEW_KEYS for e in per_view[k]]
    importer.import_entities(all_view_entities, target_layout=msp)
    importer.finalize()

    # 5) 表題欄記入
    field_values = dict(fields or {})
    for req in ("品名", "図番", "装置名"):
        if not field_values.get(req):
            raise ValueError("fields['%s'] は必須です" % req)

    part_name = to_zenkaku(str(field_values["品名"]))
    drawing_no = to_zenkaku(str(field_values["図番"]))
    equipment = to_zenkaku(str(field_values["装置名"]))
    drafter = to_zenkaku(str(field_values.get("製図者", "AI")))
    today = datetime.date.today()
    draw_date_default = "%02d.%02d.%02d" % (today.year % 100, today.month, today.day)
    draw_date = to_zenkaku(str(field_values.get("製図日付", draw_date_default)))
    material = to_zenkaku(str(field_values.get("材質", "")))
    material_shape = to_zenkaku(str(field_values.get("材質形状", "")))
    count = to_zenkaku(str(field_values.get("個数", 1)))
    serial = field_values.get("連番")
    serial = to_zenkaku(str(serial)) if serial else _trailing_serial(str(field_values["図番"]))

    added_entities = {}
    added_entities["品名_値"] = _add_field_mtext(doc, msp, "品名_値", field_specs["品名_値"],
                                                 "\\T1.000000;" + part_name)
    added_entities["装置名_値"] = _add_field_mtext(doc, msp, "装置名_値", field_specs["装置名_値"],
                                                  "\\T1.000000;" + equipment)
    added_entities["図番_値"] = _add_field_mtext(doc, msp, "図番_値", field_specs["図番_値"],
                                                "\\T1.000000;" + drawing_no)
    added_entities["製図者_氏名"] = _add_field_mtext(doc, msp, "製図者_氏名", field_specs["製図者_氏名"],
                                                    "\\T1.000000;" + drafter)
    # 製図日付: fields.jsonに専用フィールド名は無いが、"REV_日付_3"のアンカー(207.705,22.586)は
    # 製図者_氏名(191.878,22.586)と同じy=22.586=同じ行(ラベル"製図"の行)にある実測値であり、
    # これが製図日付セルの実体である(調査/frame_template.dxfのTEXTラベル配置から確認)。
    added_entities["REV_日付_3"] = _add_field_mtext(doc, msp, "REV_日付_3", field_specs["REV_日付_3"],
                                                    "\\T1.000000;" + draw_date)
    # REV_日付_2は"更新"(改訂履歴)行。Q4裁定「REV=初版はプレースホルダのまま」に従い、
    # フレーム内の審査行と同じプレースホルダ書式("＊＊．＊＊．＊＊")を明示的に記入する
    # (フレーム側にこの行の共通エンティティは無いため、記入しないと空白になってしまう)。
    added_entities["REV_日付_2"] = _add_field_mtext(
        doc, msp, "REV_日付_2", field_specs["REV_日付_2"],
        "\\T1.000000;" + to_zenkaku("**.**.**"))

    scale_spec = field_specs["尺度_値"]
    added_entities["尺度_値"] = _add_field_mtext(doc, msp, "尺度_値", scale_spec,
                                                "\\T1.000000;" + _scale_text(scale))

    serial_spec = field_specs["連番マーク_値"]
    added_entities["連番マーク_値"] = _add_field_mtext(doc, msp, "連番マーク_値", serial_spec,
                                                     "\\T1.000000;" + serial)

    note_spec = field_specs["材質個数ノート_自製品"]
    note_text = ("\\T1.000000;\u6750\u8cea\u3000%s\u3000%s\\P\\T1.000000;\u500b\u6570\u3000%s"
                 % (material, material_shape, count))
    added_entities["材質個数ノート_自製品"] = _add_field_mtext(
        doc, msp, "材質個数ノート_自製品", note_spec, note_text)

    # 重量(材質未設定=水密度1000で出るSW既定値を、依頼材質の密度で再計算する)
    volume_mm3 = meta["metrics"]["volume_mm3"]
    density = field_values.get("密度_kgm3")
    density_note = None
    if density is None:
        density = meta["metrics"]["density_kgm3"]
        if meta["metrics"].get("density_is_default_water"):
            warnings.append(
                "重量: 3Dモデルの材質が未設定のためSWは水密度(1000kg/m3)で質量を返している。"
                "fields['密度_kgm3']が指定されなかったのでそのまま使用した(要確認)。")
    else:
        density_note = "fields['密度_kgm3']=%.1f で上書き計算" % density
    mass_kg = volume_mm3 * density * 1e-9
    weight_spec = field_specs["重量_値"]
    weight_text = to_zenkaku("%.2f" % mass_kg)
    added_entities["重量_値"] = _add_field_mtext(doc, msp, "重量_値", weight_spec,
                                                "\\T1.000000;" + weight_text)

    # 6) 保存
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    doc.saveas(out_path)

    # ------------------------------------------------------------------
    # 検証情報
    # ------------------------------------------------------------------
    remaining, frame_summary = subtract_frame(doc, template_path=frame_template_path)

    zone_overlaps = []
    for k, bb in view_out_bbox.items():
        if _rect_overlap(bb, TITLE_BLOCK_RECT):
            zone_overlaps.append((k, "title_block"))
        if _rect_overlap(bb, NOTE_ZONE_RECT):
            zone_overlaps.append((k, "note_zone"))
    pairs = list(VIEW_KEYS)
    view_pair_overlaps = []
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            if _rect_overlap(view_out_bbox[pairs[i]], view_out_bbox[pairs[j]]):
                view_pair_overlaps.append((pairs[i], pairs[j]))

    field_anchor_check = {}
    for name, ent in added_entities.items():
        spec = field_specs[name]
        ex, ey = spec["anchor"]
        ax, ay = ent.dxf.insert.x, ent.dxf.insert.y
        dx, dy = ax - ex, ay - ey
        field_anchor_check[name] = {
            "expected": [ex, ey], "actual": [round(ax, 4), round(ay, 4)],
            "diff_mm": [round(dx, 4), round(dy, 4)],
            "within_0_5mm": abs(dx) <= 0.5 and abs(dy) <= 0.5,
        }

    result = {
        "out_path": out_path,
        "scale": scale,
        "frame_entity_count_loaded": frame_entity_count,
        "frame_check": frame_summary,  # frame_matchedが113なら合格
        "view_entity_counts": {k: len(v) for k, v in per_view.items()},
        "unclassified_entity_count": len(unclassified),
        "view_out_bbox_mm": view_out_bbox,
        "zone_overlaps": zone_overlaps,
        "view_pair_overlaps": view_pair_overlaps,
        "field_anchor_check": field_anchor_check,
        "linetypes_added": lt_added,
        "linetypes_missing": lt_missing,
        "weight": {
            "volume_mm3": volume_mm3,
            "density_kgm3_used": density,
            "mass_kg": mass_kg,
            "note": density_note,
        },
        "field_values": {
            "品名": part_name, "図番": drawing_no, "装置名": equipment,
            "尺度": _scale_text(scale), "製図者": drafter, "製図日付": draw_date,
            "連番": serial, "重量": weight_text,
        },
        "warnings": warnings,
    }
    return result


def _scale_text(scale):
    if abs(scale - 1.0) < 1e-9:
        ratio = "1:1"
    elif scale < 1.0:
        r = 1.0 / scale
        r_s = str(int(round(r))) if abs(r - round(r)) < 1e-6 else ("%.3g" % r)
        ratio = "1:%s" % r_s
    else:
        s_s = str(int(round(scale))) if abs(scale - round(scale)) < 1e-6 else ("%.3g" % scale)
        ratio = "%s:1" % s_s
    return to_zenkaku(ratio)


_MTEXT_ATTACH_ALIGN = {
    1: ("left", "top"), 2: ("center", "top"), 3: ("right", "top"),
    4: ("left", "center"), 5: ("center", "center"), 6: ("right", "center"),
    7: ("left", "bottom"), 8: ("center", "bottom"), 9: ("right", "bottom"),
}
_LINETYPE_STYLE = {
    "HIDDEN": dict(color="#c81e1e", lw=0.7, ls=(0, (5, 3))),
    "PHANTOM": dict(color="#1e6fc8", lw=0.7, ls=(0, (8, 3, 2, 3))),
    "CENTER": dict(color="#1e9e5a", lw=0.6, ls=(0, (10, 3, 2, 3))),
}
_DEFAULT_LINE_STYLE = dict(color="#e6e6e6", lw=0.7, ls="-")
_ANNOT_STYLE = dict(color="#9a6ac8", lw=0.6, ls="-")


def _clean_mtext(raw):
    u"""MTEXTの書式コード(\\Txxx; \\Wxxx; 等)と波括弧グループを取り除き、\\Pを改行にする。"""
    s = raw.replace("\\P", "\n")
    s = re.sub(r"\\[A-Za-z][^;]*;", "", s)
    s = s.replace("{", "").replace("}", "")
    return s


def render_png(dxf_path, out_png, title=None, figsize=(16, 11.6), dpi=170):
    u"""合成後DXFをPNG化する(目視ゲート用)。

    ezdxf.addons.drawing の汎用Frontend/MatplotlibBackendは、図枠のtxt.shxスタイルの
    日本語グリフをtofu(□)にしてしまう(独自フォント解決系がmatplotlibのrcParamsを見ないため。
    実測確認済み)。調査/render_phase2_dxf.py と同じ「手描き(LINE/CIRCLE/ARC/LWPOLYLINE/INSERT
    はプリミティブ描画、MTEXT/TEXTはax.textでMeiryo描画)」方式を用いる
    (CLAUDE.md知見: matplotlibの日本語はMS Gothic全滅・Meiryo/Yu Gothicを使う)。
    """
    import math
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["font.family"] = "Meiryo"
    matplotlib.rcParams["axes.unicode_minus"] = False
    import matplotlib.pyplot as plt

    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()

    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax = fig.add_axes([0.03, 0.02, 0.94, 0.93])
    ax.set_aspect("equal")

    def style_of(e):
        lt = e.dxf.linetype if e.dxf.hasattr("linetype") else "BYLAYER"
        return _LINETYPE_STYLE.get(lt, _DEFAULT_LINE_STYLE)

    def draw(e, annot=False):
        st = _ANNOT_STYLE if annot else style_of(e)
        t = e.dxftype()
        if t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            ax.plot([s.x, en.x], [s.y, en.y], **st)
        elif t == "CIRCLE":
            c, r = e.dxf.center, e.dxf.radius
            a = [i * math.pi / 60 for i in range(121)]
            ax.plot([c.x + r * math.cos(v) for v in a],
                     [c.y + r * math.sin(v) for v in a], **st)
        elif t == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
            if a1 <= a0:
                a1 += 2 * math.pi
            a = [a0 + (a1 - a0) * i / 64 for i in range(65)]
            ax.plot([c.x + r * math.cos(v) for v in a],
                     [c.y + r * math.sin(v) for v in a], **st)
        elif t == "LWPOLYLINE":
            pts = list(e.get_points("xy"))
            if e.closed and pts:
                pts = pts + [pts[0]]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], **st)
        elif t in ("ELLIPSE", "SPLINE"):
            pts = list(e.flattening(0.05))
            ax.plot([p[0] for p in pts], [p[1] for p in pts], **st)
        elif t == "INSERT":
            is_annot = str(e.dxf.name).upper().startswith("SW_CENTERMARKSYMBOL")
            for ve in e.virtual_entities():
                draw(ve, annot=is_annot)
        elif t == "MTEXT":
            p = e.dxf.insert
            ha, va = _MTEXT_ATTACH_ALIGN.get(e.dxf.attachment_point, ("left", "bottom"))
            ax.text(p.x, p.y, _clean_mtext(e.text), fontsize=e.dxf.char_height * 2.6,
                     ha=ha, va=va, color="#f2f2f2", linespacing=1.3)
        elif t == "TEXT":
            p = e.dxf.insert
            h = e.dxf.height if e.dxf.hasattr("height") else 2.5
            ax.text(p.x, p.y, e.dxf.text, fontsize=h * 2.6, ha="left", va="bottom",
                     color="#cfe8ff")

    for e in msp:
        draw(e)

    ax.set_xlim(FRAME_X0 - 5, FRAME_X1 + 5)
    ax.set_ylim(FRAME_Y0 - 5, FRAME_Y1 + 5)
    ax.set_axis_off()
    if title:
        fig.suptitle(title, fontsize=11, y=0.995)
    fig.savefig(out_png, facecolor="#1b1f27")
    plt.close(fig)
    return out_png


def _main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    views_dxf = "調査/phase2_out_15015-P3-013_ホルダー.dxf"
    meta_json = "調査/phase2_meta_15015-P3-013_ホルダー.json"
    out_dxf = "生成図面/TEST-001_ホルダー.dxf"
    out_png = "生成図面/TEST-001_ホルダー.png"

    fields = {
        "品名": "ホルダー",
        "図番": "テスト-001",
        "装置名": "テスト装置",
        "材質": "S45C",
        "材質形状": "マル80",
        "個数": 1,
        "密度_kgm3": 7850.0,  # S45Cの密度でテスト用に重量を再計算(3Dモデル自体は材質未設定)
        "製図者": "AI",
    }

    result = compose(views_dxf, meta_json, fields, scale=1.0, out_path=out_dxf)

    print("== compose result ==")
    print(json.dumps(
        {k: v for k, v in result.items() if k not in ("view_out_bbox_mm",)},
        ensure_ascii=False, indent=2, default=str))
    print("view_out_bbox_mm:")
    for k, v in result["view_out_bbox_mm"].items():
        print(" ", k, ["%.3f" % x for x in v])

    render_png(out_dxf, out_png, title="TEST-001 ホルダー (A3, 1:1, 第三角法)")
    print("saved", out_png)


if __name__ == "__main__":
    _main()
