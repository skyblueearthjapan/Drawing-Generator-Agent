# -*- coding: utf-8 -*-
u"""寸法記入エンジン(恒久モジュール・フェーズ3-B)。

合成済みDXF(engine/compose_drawing.py の出力)+ 作図計画JSON(engine/plan_schema.md)
+ 図枠/dimstyle_spec.json  →  寸法・穴注記入りの部品図DXF。

    apply_plan(plan_path, out_dxf_path) -> 検証レポートdict

設計方針(調査/dimension_style_analysis.md §8 / 図枠/dimstyle_spec.json / 裁定Q1-Q5 準拠):
  1. 直径寸法は**文脈で使い分ける**(2026-08-09 ユーザー確定・裁定Q1の更新):
       円形ビュー上の外径 → ネイティブDIAMETER型(dimtype=3。円を斜めに貫く寸法線+両矢印)
       断面・側面(輪郭)ビュー → 線形(rotated)DIMENSION + dimpost='%%c<>'
     計画側は kind='diameter' + context='circular_view'/'profile_view' と書き、
     どちらの実装を使うかは defaults.diameter_style(DIAMETER_STYLE_DEFAULT)で決める
     = **裁定が変わってもこの1箇所の差し替えで戻せる**。半径はRADIUS型+'R<>'
  2. 1寸法=1専用DIMSTYLE(XDATAオーバーライドは使わない。コーパス実測0件)
  3. _OPEN30 矢印は ARROWS.create_block() で明示生成(ezdxfの罠)
  4. dimdsep=46(明示しないとカンマ区切りになるezdxfの罠)
  5. 寸法線オフセットは輪郭から16mm、2段目以降 +8mm 刻み
  6. 穴注記は**φ(%%c)表記・全角**が既定(2026-08-10 ユーザー裁定。2026-08-09のキリ既定を更新)。
     人間図面の `２－%%c８　ザグリ%%c１１深さ７\\PＰＣＤ６０` を踏襲する。
     キリ表記へ戻す場合は defaults.hole_note(HOLE_NOTE_DEFAULT)を差し替える(1定数/計画1行)
  7. 公差ゼロ側は描画後に text を「0」へ整形(裁定の追記。dimtzinでは再現不可)
  8. **ゲート①内蔵**: 各寸法について
       (a) 測定点が実ジオメトリの特徴点に一致するか(snap検証)
       (b) defpointから再計算した実測値 == 計画のvalue_expected
       (c) 実測値 == DXFに描かれた寸法文字の数値
       (d) (任意)別ビューの実在円との突き合わせ(cross_check)
     を検証し、0.01mmを超えるずれがあれば **保存せずに例外で停止する**。

CLI:
    python engine/dim_engine.py <plan.json> <out.dxf> [--png <out.png>]
"""
import io
import json
import math
import os
import re
import sys

import ezdxf
from ezdxf.bbox import extents as bbox_extents
from ezdxf.render.arrows import ARROWS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import compose_drawing  # noqa: E402
from engine import nominal_size  # noqa: E402
from engine.frame_extract import subtract_frame  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIMSTYLE_SPEC_DEFAULT = os.path.join(ROOT, u"図枠", u"dimstyle_spec.json")

VIEW_KEYS = compose_drawing.VIEW_KEYS

# 図枠バケツAの禁止領域(compose_drawing と同じ定数)
FRAME_RECT = (compose_drawing.FRAME_X0, compose_drawing.FRAME_Y0,
              compose_drawing.FRAME_X1, compose_drawing.FRAME_Y1)
TITLE_BLOCK_RECT = compose_drawing.TITLE_BLOCK_RECT
NOTE_ZONE_RECT = compose_drawing.NOTE_ZONE_RECT

# ビュー分類の許容マージン(mm)。中心マークINSERT等が輪郭からわずかにはみ出すため
CLASSIFY_MARGIN_MM = 8.0

ATTACH = compose_drawing.ATTACH

# ---------------------------------------------------------------------------
# ❗未確定だった流儀の既定値(2026-08-09 ユーザー確定)。
#    **裁定が変わったらここだけ差し替えれば全計画に反映される**ように分離してある。
#    計画JSONの defaults.diameter_style / defaults.hole_note で個別に上書きできる。
# ---------------------------------------------------------------------------
# 直径寸法の実装方式: 円形ビュー(円が見えるビュー)の外径はネイティブDIAMETER型、
# 断面・側面(輪郭)ビューは線形+dimpost='%%c<>'
DIAMETER_STYLE_DEFAULT = {
    "circular_view": "native",   # native | linear
    "profile_view": "linear",    # native | linear
}
# 穴注記の書式: **φ(%%c)表記・全角が既定**(2026-08-10 ユーザー裁定。2026-08-09 の
# 「キリ表記が既定」を更新)。制御コード(%%c/\P/\A1;)は半角のまま。
#   例: `２－%%c８　ザグリ%%c１１深さ７\PＰＣＤ６０`
# 根拠: 盲検10部品の人間図面に **キリ表記は1件も無く**(調査/blind_test_report.md §6.2)、
#       AUTO-001 の人間図面も `%%c５．５ザグリ%%c９．５深さ１２` だった。
# ❗キリ表記は**オプションとして温存**する(計画JSONの defaults.hole_note で
#   {"notation":"kiri"} と書けば従来どおり。切替はこの1定数 or 計画1行で済む)。
HOLE_NOTE_DEFAULT = {
    "notation": "phi",    # phi(=%%c8) | kiri(=8キリ)
    "width": "zenkaku",   # zenkaku | hankaku
    # 穴仕様とザグリ仕様の区切り(全角化されて `　` になる)。詰めたい場合は "" にする
    "separator": " ",
}


class DimensionGateError(Exception):
    u"""ゲート①(寸法値照合)不合格。不合格品は保存しない。"""


# ---------------------------------------------------------------------------
# dimstyle_spec.json の読み込み
# ---------------------------------------------------------------------------
def load_dimstyle_spec(path=DIMSTYLE_SPEC_DEFAULT):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def base_dimvars(spec):
    u"""dimstyle_spec.json の dimstyle_base/arrow から、ezdxfへ設定するDIMSTYLE変数dictを作る。"""
    b = spec["dimstyle_base"]
    v = {
        "dimtxt": b["dimtxt"]["value"],
        "dimasz": b["dimasz"]["value"],
        "dimexo": b["dimexo"]["value"],
        "dimexe": b["dimexe"]["value"],
        "dimgap": b["dimgap"]["value"],
        "dimdec": b["dimdec"]["value"],
        "dimtad": b["dimtad"]["value"],
        "dimclrd": b["dimclrd"]["value"],
        "dimclre": b["dimclre"]["value"],
        "dimclrt": b["dimclrt"]["value"],
        "dimdli": b["dimdli"]["value"],
        "dimscale": b["dimscale"]["value"],
        "dimlfac": b["dimlfac"]["value"],
        "dimsah": b["dimsah"]["value"],
        "dimjust": b["dimjust"]["value"],
        "dimcen": b["dimcen"]["value"],
        # ❗dimfit(群コード287)はR13/R14の旧変数。ezdxfはAC1015保存時にこれを書き出さず
        #   dimatfit(289)+dimtmove(279)へ自動変換する(実測確認済み)。
        #   一方 ACROVA GMM の実ファイルは dimfit を持ち dimatfit を持たない。
        #   AutoCADの公式等価変換 DIMFIT=3 → DIMATFIT=3 / DIMTMOVE=0 を明示設定し、
        #   読み戻し検証も dimatfit/dimtmove で行う(dimfitで検証すると必ずNoneで落ちる)。
        "dimatfit": b["dimfit"]["value"],
        "dimtmove": 0,
        # ezdxf既定はカンマ(',')。実在図面はピリオド表記のため必ず46('.')を明示する
        "dimdsep": 46,
        # コーパス実測 dimzin=8(末尾ゼロ抑制。『40.00』でなく『40』表記)。
        # ezdxfの既定も8だが、依存せず明示する
        "dimzin": 8,
        "dimblk1": spec["arrow"]["dimblk1"]["value"],
        "dimblk2": spec["arrow"]["dimblk2"]["value"],
    }
    return v


# ---------------------------------------------------------------------------
# 座標変換(モデル3D → 最終A3図面)
# ---------------------------------------------------------------------------
def plan_layout(plan):
    u"""計画JSONから「レイアウトを決める3点セット」を取り出す(compose と完全に同じ値を使う)。

    Returns: (scale, views, reserves)
      scale   : source.scale(既定1.0)。図面幾何の尺度。**寸法値は常にモデル実寸**
      views   : layout.views(既定 None = 従来4ビュー)
      reserves: dimensions[].placement から算出した寸法予約帯(ビュー間隔の根拠)
    """
    scale = float(plan.get("source", {}).get("scale", 1.0))
    layout = plan.get("layout") or {}
    views = compose_drawing.resolve_views(layout.get("views"))
    if layout.get("dim_reserve", True):
        reserves = compose_drawing.plan_view_reserves(
            plan, band_mm=float(layout.get("dim_band_mm", compose_drawing.DIM_BAND_MM)))
    else:
        reserves = {}
    return scale, views, reserves


def build_view_transforms(meta_json_path, scale, views=None, reserves=None):
    u"""phase2 meta + compose の再レイアウト計算から、各ビューの
    「モデル3D座標 → 最終A3図面座標」変換と、ビュー幾何外接矩形を復元する。

    ❗compose と同じ引数(scale/views/reserves)を渡さないとレイアウトがずれる。
    計画JSONからは `plan_layout(plan)` で3点セットを取り出すこと。

    Returns: dict view_key -> {
        "model_to_sheet": callable((x,y,z)) -> (sx, sy)   # phase2シート座標
        "sheet_to_draw":  callable((sx,sy)) -> (dx, dy)   # 最終図面座標
        "model_to_draw":  callable((x,y,z)) -> (dx, dy)   # 尺度込み(scale倍された図面座標)
        "region":         (x0,y0,x1,y1)  # ビュー幾何の外接矩形(最終図面座標)
    }
    """
    with io.open(meta_json_path, encoding="utf-8") as f:
        meta = json.load(f)
    use_views = compose_drawing.resolve_views(views)
    geoms = {k: meta["views"][k]["geom_mm"] for k in use_views}
    targets, centers, sizes, _info = compose_drawing._layout_targets(
        geoms, scale, views=use_views, reserves=reserves)

    out = {}
    for k in use_views:
        arr = meta["views"][k]["model_to_view"]
        r = arr[0:9]
        tx, ty = arr[9], arr[10]
        s = arr[12]
        ocx, ocy = centers[k]
        ncx, ncy = targets[k]

        def make_m2s(r=r, tx=tx, ty=ty, s=s):
            def f(p):
                x, y, z = p[0], p[1], p[2] if len(p) > 2 else 0.0
                sx = s * (r[0] * x + r[3] * y + r[6] * z) + tx * 1000.0
                sy = s * (r[1] * x + r[4] * y + r[7] * z) + ty * 1000.0
                return (sx, sy)
            return f

        def make_s2d(ocx=ocx, ocy=ocy, ncx=ncx, ncy=ncy, scale=scale):
            def f(p):
                return ((p[0] - ocx) * scale + ncx, (p[1] - ocy) * scale + ncy)
            return f

        m2s = make_m2s()
        s2d = make_s2d()
        w, h = sizes[k]
        out[k] = {
            "model_to_sheet": m2s,
            "sheet_to_draw": s2d,
            "model_to_draw": (lambda m2s=m2s, s2d=s2d: (lambda p: s2d(m2s(p))))(),
            "region": (ncx - w / 2.0, ncy - h / 2.0, ncx + w / 2.0, ncy + h / 2.0),
        }
    return out


# ---------------------------------------------------------------------------
# ビュー分類・特徴点抽出(ゲート①(a)の材料)
# ---------------------------------------------------------------------------
def _is_centermark(e):
    return (e.dxftype() == "INSERT"
            and str(e.dxf.name).upper().startswith("SW_CENTERMARKSYMBOL"))


#: 中心線の線種。**中心線は注記であって幾何ではない**ので、ビュー幾何の分類から除外する。
#: これを除外しないと engine/centerline_gen.py が足した中心線が
#:   - ゲート①の snap/circle_check の候補点になる
#:   - ゲート②の位置ノード・円フィーチャー(PCD参照円=径寸法が要る円)になる
#: という二重の実害が出る(中心線を足した図面を retry/regate で再検査する経路が実在する)。
CENTERLINE_LINETYPES = frozenset((
    "DASHDOT", "DASHDOT2", "DASHDOTX2", "CENTER", "CENTER2", "CENTERX2"))


def is_centerline(e):
    u"""エンティティが中心線(一点鎖線)か。線種のみで判定する(レイヤ名は当てにならない)。"""
    return str(e.dxf.get("linetype", "BYLAYER")).upper() in CENTERLINE_LINETYPES


def classify_view_geometry(entities, regions, margin=CLASSIFY_MARGIN_MM):
    u"""部品コンテンツ(図枠を差し引いた残り)の幾何エンティティをビュー別に分類する。
    中心マークINSERT・中心線(DASHDOT/CENTER系)は輪郭・特徴点の対象外
    (CLAUDE.md知見: 中心マークはbbox計算時は除外。中心線は注記であって幾何ではない)。
    **図枠を先に subtract_frame() で差し引いておくこと**(図枠外枠の中心がisoビュー領域に
    入ってしまい、ビュー輪郭が図枠全体に化ける実害を確認済み)。"""
    per_view = {k: [] for k in regions}
    for e in entities:
        t = e.dxftype()
        if is_centerline(e):
            continue
        if t in ("MTEXT", "TEXT", "DIMENSION", "LEADER", "POINT"):
            continue
        if _is_centermark(e):
            continue
        bb = bbox_extents([e], fast=True)
        if bb is None or not bb.has_data:
            continue
        cx = (bb.extmin.x + bb.extmax.x) / 2.0
        cy = (bb.extmin.y + bb.extmax.y) / 2.0
        for k in regions:
            x0, y0, x1, y1 = regions[k]
            if x0 - margin <= cx <= x1 + margin and y0 - margin <= cy <= y1 + margin:
                per_view[k].append(e)
                break
    return per_view


def feature_points(entities):
    u"""実ジオメトリの特徴点(線分端点・円/円弧の中心と四分点・ポリライン頂点)を列挙する。"""
    pts = []
    for e in entities:
        t = e.dxftype()
        if t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            pts.append((s.x, s.y))
            pts.append((en.x, en.y))
        elif t == "CIRCLE":
            c, r = e.dxf.center, e.dxf.radius
            pts.append((c.x, c.y))
            for a in (0.0, 90.0, 180.0, 270.0):
                pts.append((c.x + r * math.cos(math.radians(a)),
                            c.y + r * math.sin(math.radians(a))))
        elif t == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            a0, a1 = e.dxf.start_angle, e.dxf.end_angle
            pts.append((c.x, c.y))
            for a in (a0, a1, 0.0, 90.0, 180.0, 270.0):
                span0, span1 = a0 % 360.0, a1 % 360.0
                aa = a % 360.0
                inside = (span0 <= aa <= span1) if span0 <= span1 else (aa >= span0 or aa <= span1)
                if a in (a0, a1) or inside:
                    pts.append((c.x + r * math.cos(math.radians(a)),
                                c.y + r * math.sin(math.radians(a))))
        elif t == "LWPOLYLINE":
            for p in e.get_points("xy"):
                pts.append((p[0], p[1]))
        elif t in ("ELLIPSE", "SPLINE"):
            for p in e.flattening(0.02):
                pts.append((p[0], p[1]))
    return pts


def nearest_feature_distance(pt, pts):
    best = float("inf")
    for q in pts:
        d = math.hypot(pt[0] - q[0], pt[1] - q[1])
        if d < best:
            best = d
    return best


def find_circle(entities, center, diameter, tol):
    u"""指定中心・直径のCIRCLE/ARCを実ジオメトリから探す(cross_check用)。"""
    for e in entities:
        if e.dxftype() not in ("CIRCLE", "ARC"):
            continue
        c, r = e.dxf.center, e.dxf.radius
        if (math.hypot(c.x - center[0], c.y - center[1]) <= tol
                and abs(2.0 * r - diameter) <= tol):
            return e
    return None


# ---------------------------------------------------------------------------
# DIMSTYLE / STYLE 生成
# ---------------------------------------------------------------------------
def ensure_text_style(doc, spec):
    ts = spec["text_style"]
    return compose_drawing._ensure_style(doc, ts["font"], ts["width_factor"],
                                         ts.get("oblique", 0.0))


def ensure_arrow_blocks(doc, names=("_OPEN30",)):
    u"""_OPEN30等の標準矢印ブロックはezdxf.new(setup=True)でも未登録のことがある。明示生成する。"""
    created = []
    for n in names:
        key = n.lstrip("_").lower()  # "_OPEN30" -> "open30"
        if n in doc.blocks:
            continue
        arrow_name = {"open30": ARROWS.open_30, "dot": ARROWS.dot,
                      "closedfilled": ARROWS.closed_filled}.get(key)
        if arrow_name is None:
            continue
        ARROWS.create_block(doc.blocks, arrow_name)
        created.append(n)
    return created


def _new_dimstyle(doc, name, dimvars):
    ds = doc.dimstyles.add(name)
    for k, v in dimvars.items():
        ds.set_dxf_attrib(k, v)
    return ds


# ---------------------------------------------------------------------------
# 公差テキスト整形(裁定: ゼロ側は「0」表記)
# ---------------------------------------------------------------------------
_TOL_STACK_RE = re.compile(r"\\S([^\^]*)\^ ([^;]*);")


def _zero_side_to_0(s):
    body = s.strip()
    sign = ""
    if body[:1] in ("+", "-"):
        sign, body = body[0], body[1:]
    try:
        if abs(float(body)) < 1e-12:
            lead = s[:len(s) - len(s.lstrip())]
            return lead + sign + "0"
    except ValueError:
        pass
    return s


_TOL_HFAC_RE = re.compile(r"\\H([0-9.]+)x;")


def fix_tolerance_height_factor(doc, dim, dimtfac):
    u"""公差スタックの高さ係数表記を dimtfac の実値へ揃える。

    ❗ezdxfは `\\H{:.2f}x;` で丸めるため dimtfac=0.625 が **`\\H0.62x;`** と書かれる。
    GMM実ファイル(人間図面 GMM008)は `\\H0.625x;`。見た目の完全一致を優先し描画後に整形する。
    """
    geom = dim.dxf.get("geometry", None)
    if not geom or geom not in doc.blocks:
        return False
    want = ("%g" % float(dimtfac))
    changed = False
    for e in doc.blocks.get(geom):
        if e.dxftype() != "MTEXT":
            continue
        new = _TOL_HFAC_RE.sub(
            lambda m: ("\\H%sx;" % want) if abs(float(m.group(1)) - float(dimtfac)) <= 0.01
            else m.group(0), e.text)
        if new != e.text:
            e.text = new
            changed = True
    return changed


def fix_zero_tolerance_text(doc, dim):
    u"""描画済みDIMENSIONの寸法文字MTEXT内で、公差のゼロ側を『0.000』→『0』へ整形する。
    (裁定: dimtzinでは再現できないためtext側で整形する)"""
    geom = dim.dxf.get("geometry", None)
    if not geom or geom not in doc.blocks:
        return False
    changed = False
    for e in doc.blocks.get(geom):
        if e.dxftype() != "MTEXT":
            continue
        raw = e.text
        new = _TOL_STACK_RE.sub(
            lambda m: "\\S%s^ %s;" % (_zero_side_to_0(m.group(1)), _zero_side_to_0(m.group(2))),
            raw)
        if new != raw:
            e.text = new
            changed = True
    return changed


# ---------------------------------------------------------------------------
# ネイティブDIAMETER型のφ重複除去
# ---------------------------------------------------------------------------
# ❗ezdxfのDiameterDimensionレンダラーは PREFIX='Ø'(DXF出力時に'%%c')を**必ず**前置する
#   (render/dim_radius.py RadiusMeasurement.format_text)。DIMSTYLEに dimpost='%%c<>' を
#   設定するとアノニマスブロック内の描画文字が '%%c%%c75' と二重φになる。
#   dimpost はコーパス流儀(GMM006 実測)に合わせて '%%c<>' のまま保持し、
#   **キャッシュ描画側の重複だけを描画後に除去する**(DIMENSION.text は '<>' のままなので
#   CAD側で再生成されても dimpost により正しく '%%c75' になる)。
_DUP_PHI_RE = re.compile(r"(%%[cC])\s*(%%[cC])")


def fix_duplicate_diameter_prefix(doc, dim):
    u"""ネイティブDIAMETER寸法の描画文字にできる '%%c%%c' の重複を1つへ潰す。"""
    geom = dim.dxf.get("geometry", None)
    if not geom or geom not in doc.blocks:
        return False
    changed = False
    for e in doc.blocks.get(geom):
        if e.dxftype() == "MTEXT":
            new = _DUP_PHI_RE.sub(r"\1", e.text)
            if new != e.text:
                e.text = new
                changed = True
        elif e.dxftype() == "TEXT":
            new = _DUP_PHI_RE.sub(r"\1", e.dxf.text)
            if new != e.dxf.text:
                e.dxf.text = new
                changed = True
    return changed


# ---------------------------------------------------------------------------
# 穴注記パターン生成(裁定2026-08-09: キリ表記・全角が既定)
# ---------------------------------------------------------------------------
_PROTECT_RE = re.compile(r"(%%[cCpPdD]|\\P|\\[A-Za-z][^;]*;)")


def _zenkaku_keep_codes(s):
    u"""DXF制御コード(%%c, \\P, \\A1; 等)を保護したまま、半角英数記号・空白を全角へ変換する。"""
    out = []
    pos = 0
    for m in _PROTECT_RE.finditer(s):
        out.append(compose_drawing.to_zenkaku(s[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(compose_drawing.to_zenkaku(s[pos:]))
    return "".join(out)


def _fmt_num(v):
    return ("%g" % v)


def build_hole_note_pattern(spec, style=None):
    u"""構造化された穴仕様から注記パターン文字列を組み立てる。

    spec 例:
      {"count":2, "drill":8, "counterbore":{"dia":11,"depth":7}, "placement":"PCD60"}
        -> φ/全角(既定): '\\A1;２－%%c８　ザグリ%%c１１深さ７\\PＰＣＤ６０'
        -> キリ/全角   : '\\A1;２－８キリ　ザグリ%%c１１深さ７\\PＰＣＤ６０'
        -> φ/半角     : '\\A1;2-%%c8 ザグリ%%c11深さ7\\PPCD60'
      {"thread":"M10", "depth":20}
        -> '\\A1;Ｍ１０深さ２０'

    style: {"notation": "phi"|"kiri", "width": "zenkaku"|"hankaku", "separator": " "}
           (既定 HOLE_NOTE_DEFAULT)
    """
    st = dict(HOLE_NOTE_DEFAULT)
    st.update(style or {})
    lines = []
    head = ""
    cnt = spec.get("count")
    if cnt:
        head += "%d-" % int(cnt)
    if spec.get("thread"):
        head += str(spec["thread"])
        if spec.get("depth") is not None:
            head += u"深さ%s" % _fmt_num(spec["depth"])
    elif spec.get("drill") is not None:
        if st["notation"] == "kiri":
            head += u"%sキリ" % _fmt_num(spec["drill"])
        else:
            head += "%%%%c%s" % _fmt_num(spec["drill"])
        if spec.get("depth") is not None:
            head += u"深さ%s" % _fmt_num(spec["depth"])
    cb = spec.get("counterbore")
    if cb:
        sep = st.get("separator", HOLE_NOTE_DEFAULT["separator"])
        head += u"%sザグリ%%%%c%s" % (sep, _fmt_num(cb["dia"]))
        if cb.get("depth") is not None:
            head += u"深さ%s" % _fmt_num(cb["depth"])
    # 皿もみ(countersink)。例 {"angle":90,"dia":13.44} -> `９０°皿もみ%%c１３．４４`
    cs = spec.get("countersink")
    if cs:
        sep = st.get("separator", HOLE_NOTE_DEFAULT["separator"])
        head += sep
        if cs.get("angle") is not None:
            head += u"%s°" % _fmt_num(cs["angle"])
        head += u"皿もみ%%%%c%s" % _fmt_num(cs["dia"])
    lines.append(head)
    if spec.get("placement"):
        lines.append(str(spec["placement"]))
    for extra in spec.get("extra_lines", []):
        lines.append(str(extra))
    body = "\\P".join(lines)
    if st["width"] == "zenkaku":
        body = _zenkaku_keep_codes(body)
    return "\\A1;" + body


# ---------------------------------------------------------------------------
# 寸法文字の読み取り(ゲート①(c))
# ---------------------------------------------------------------------------
_MTEXT_CODE_RE = re.compile(r"\\[A-Za-z][^;]*;")


def dim_text_of(doc, dim):
    geom = dim.dxf.get("geometry", None)
    if not geom or geom not in doc.blocks:
        return None
    for e in doc.blocks.get(geom):
        if e.dxftype() == "MTEXT":
            return e.text
        if e.dxftype() == "TEXT":
            return e.dxf.text
    return None


def parse_dim_text_value(raw):
    u"""寸法文字から数値部分を取り出す(dimpost接頭辞・公差スタック・書式コードを除去)。

    ❗この関数は**括弧も剥がす**ので、`(ＰＣＤ３３３)` のような参考寸法ラベルからも 333 を返す。
    照合側は `effective_text_override()` / `is_reference_text()` で
    **参考寸法を先に除外してから**呼ぶこと(盲検で誤不合格4件を出した欠陥)。
    """
    if raw is None:
        return None
    s = re.sub(r"\{[^{}]*\}", "", raw)          # 公差スタック {\H0.62x;\S...;}
    s = _MTEXT_CODE_RE.sub("", s)                # \A1; \H0.62x; 等
    s = s.replace("%%c", "").replace("%%C", "").replace("%%p", "")
    s = s.replace("(", "").replace(")", "")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


# ---------------------------------------------------------------------------
# 寸法文字オーバーライド(参考寸法ラベル・注記文字列)の扱い
# ---------------------------------------------------------------------------
# 全角ASCII -> 半角(照合用の正規化。注記/参考値は全角で書かれる)
_TXT_ZEN2HAN = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
_TXT_ZEN2HAN[0x3000] = 0x20


def strip_mtext_codes(raw):
    u"""MTEXT書式コード・公差スタック・空白を落として比較用の素の文字列にする(全角は半角へ)。"""
    if raw is None:
        return ""
    s = re.sub(r"\{[^{}]*\}", "", raw)
    s = _MTEXT_CODE_RE.sub("", s)
    s = s.translate(_TXT_ZEN2HAN)
    return re.sub(r"\s+", "", s)


def effective_text_override(item):
    u"""計画の1寸法から、実際に DIMENSION.text へ入る override 文字列を解決する。

    apply_plan と**同じ規則**(明示の text_override / 対称公差の自動生成)を公開して、
    独立検証が同じ結論に到達できるようにする。無ければ None。
    """
    t = item.get("text_override")
    if t:
        return t
    tol = item.get("tolerance")
    if tol and tol.get("mode") == "symmetric":
        return "\\A1;%s%%%%p%s" % (_fmt_num(item["value_expected"]), _fmt_num(tol["value"]))
    return None


def is_reference_text(raw):
    u"""`(ＰＣＤ３３３)` のように**括弧で囲まれた参考値/注記文字列**か。

    plan_schema.md §2.5 が明示的に認めた用途(PCD・OD等のラベル)。
    参考寸法は「その位置に実測値を書かない」ことが目的なので、
    **寸法文字の数値と実測値の照合対象にしてはいけない**。
    """
    s = strip_mtext_codes(raw)
    return len(s) >= 2 and s.startswith("(") and s.endswith(")")


def text_override_applied(drawn_text, override):
    u"""計画が指定した override が、実DXFの描画文字にそのまま入っているか(独立検証用)。

    数値照合を外す代わりに**『計画どおりの文字が描かれているか』は必ず検査する**ので、
    参考寸法を除外しても検証の穴にはならない。
    """
    return strip_mtext_codes(drawn_text) == strip_mtext_codes(override)


# ---------------------------------------------------------------------------
# 実測値の再計算(defpointから)
# ---------------------------------------------------------------------------
def measure_from_defpoints(dim):
    d = dim.dxf
    base = d.dimtype & 7
    if base in (0, 1):
        p2, p3 = d.defpoint2, d.defpoint3
        if base == 0:
            a = math.radians(d.get("angle", 0.0))
            return abs((p3.x - p2.x) * math.cos(a) + (p3.y - p2.y) * math.sin(a))
        return math.hypot(p3.x - p2.x, p3.y - p2.y)
    if base == 4:   # radius
        return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y)
    if base == 3:   # diameter
        return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y)
    if base == 2:   # angular
        return None
    return None


def measure_angle_deg(dim):
    u"""ANGULAR DIMENSION(dimtype=2)の **実際に描かれる寸法円弧の角度[度]** を
    defpoint から独立に再計算する。

    ❗角度は長さではないので `measure_model_value` の尺度換算(1/scale)を通してはいけない。

    ❗❗**2026-08-11 修正(重大)**: 旧実装は「defpoint3-defpoint2 から defpoint-defpoint4 へ
    CCW」で測っていたが、**ezdxf が描く円弧はその逆回り**である。
    `ezdxf.render.dim_curved.AngularDimension` は
      start_angle = angle(defpoint - defpoint4) / end_angle = angle(defpoint3 - defpoint2)
    としてその間を **CCW** に描く(実験 `調査/run_style_falsification.py` の probe と
    ezdxf 1.4.4 のソースで確認)。旧実装は常に **360-描画角** を返していたため、
    「20度と書いてあるのに 340度の優角(reflex)が描かれている」図面がゲート①を通り抜けた
    (25154-3-09 の A30R/A30L が実害。30度の表示に対し実際は約310度の円弧)。
    本関数は **描かれた円弧そのもの** を返すように改めた。
    `apply_plan` 側も `add_angular_dim_2l` へ渡す line1/line2 を入れ替えたので、
    **計画JSONの意味(p1 から p2 へ CCW に測る)は従来どおりで、値も従来と同じ**になる。

    Returns: 角度[度] 0〜360 / 角度寸法でなければ None
    """
    d = dim.dxf
    if (d.dimtype & 7) != 2:
        return None
    try:
        v1, q1 = d.defpoint2, d.defpoint3      # ezdxf の end_angle 側
        v2, q2 = d.defpoint4, d.defpoint       # ezdxf の start_angle 側
    except AttributeError:
        return None
    a_end = math.atan2(q1.y - v1.y, q1.x - v1.x)
    a_start = math.atan2(q2.y - v2.y, q2.x - v2.x)
    return math.degrees((a_end - a_start) % (2.0 * math.pi))


def measure_angle_arc(doc, dim):
    u"""角度寸法の **描画実体(アノニマスブロック内のARC)** から円弧の実寸を読む。

    ❗defpoint からの再計算(`measure_angle_deg`)だけでは
    「ezdxf がどちら回りに描いたか」を取り違えた欠陥を検出できなかった
    (3-09 の実害。上の関数の注記参照)。**描かれた線そのもの**を測って突き合わせる。

    Returns: {"span_deg", "mid_deg", "radius", "center"} / 見つからなければ None
    """
    geom = dim.dxf.get("geometry", None)
    if not geom or geom not in doc.blocks:
        return None
    best = None
    for e in doc.blocks.get(geom):
        if e.dxftype() != "ARC":
            continue
        span = (e.dxf.end_angle - e.dxf.start_angle) % 360.0
        if best is None or span > best[0]:
            best = (span, e)
    if best is None:
        return None
    span, e = best
    return {"span_deg": span,
            "mid_deg": (e.dxf.start_angle + span / 2.0) % 360.0,
            "radius": e.dxf.radius,
            "center": (e.dxf.center.x, e.dxf.center.y)}


def check_angle_arc(doc, dim, measured_deg):
    u"""描かれた寸法円弧が **測った角のセクタの中** にあるかを検査する(ゲート①の一部)。

    寸法円弧は矢印の逃げぶん測定角より少し長く描かれるので長さでは判定できない。
    代わりに **円弧の中点が、始辺から測定角までの範囲に入っているか** を見る。
    優角(reflex)側に描かれた瞬間、中点はセクタの外へ出るので確実に落ちる。
    """
    arc = measure_angle_arc(doc, dim)
    if arc is None or measured_deg is None:
        return {"ok": arc is not None, "reason": u"円弧を読めない" if arc is None else None}
    d = dim.dxf
    start_ray = math.degrees(math.atan2(d.defpoint.y - d.defpoint4.y,
                                        d.defpoint.x - d.defpoint4.x))
    off = (arc["mid_deg"] - start_ray) % 360.0
    mid_ok = -1e-6 <= off <= measured_deg + 1e-6
    # 円弧の長さそのものも見る。矢印の逃げぶんだけ伸縮するので、その分だけ許容する
    # (ezdxf: start_offset=矢印長/半径, end_offset=2*dimasz/半径 → 合計 4*dimasz/半径 が上界)
    try:
        dimasz = float(doc.dimstyles.get(str(d.dimstyle)).dxf.get("dimasz", 2.5))
    except Exception:
        dimasz = 2.5
    allow = (math.degrees(4.0 * dimasz / arc["radius"]) if arc["radius"] > 1e-9 else 360.0)
    span_ok = abs(arc["span_deg"] - measured_deg) <= allow + 1e-6
    return {"ok": bool(mid_ok and span_ok), "mid_ok": bool(mid_ok), "span_ok": bool(span_ok),
            "arc_span_deg": round(arc["span_deg"], 6),
            "arc_mid_offset_deg": round(off, 6), "measured_deg": round(measured_deg, 6),
            "arc_span_allow_deg": round(allow, 6),
            "arc_radius": round(arc["radius"], 6)}


# 角度寸法の許容差[度]。円周等分/群配置の検算(PCD_ANGLE_TOL_DEG)と同じ厳しさに揃える。
ANGLE_TOL_DEG_DEFAULT = 0.05

# 角度の寸法文字から数値を取り出す(全角正規化後。`３０°`/`30度`/`30.5 deg` を許す)
_ANGLE_TEXT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:°|度|deg)?")


def parse_angle_text_value(raw):
    u"""角度寸法の `text_override` から数値[度]を取り出す(ゲート①の文字照合用)。

    ❗`kind:"angle"` は text_override 必須(plan_schema §8ルール3)なので、
    **何もしないと「描いた文字と実測角が食い違っていても誰も気付かない」穴**になる。
    そこで文字から数値を復元して実測角と突き合わせる(復元できなければ None=照合しない)。
    """
    s = strip_mtext_codes(raw)          # 全角→半角の正規化まで含む
    s = s.replace("(", "").replace(")", "").strip()
    m = _ANGLE_TEXT_RE.search(s)
    return float(m.group(1)) if m else None


def measure_model_value(dim, scale=1.0):
    u"""defpointから再計算した図面上の実測(draw mm)を**モデル実寸(mm)**へ戻す。

    ❗尺度1:2の図面でも**寸法値はモデル実寸を表示する**(自社流儀。DIMSTYLEの
    dimlfac=1/scale で実現している)。したがってゲート①の照合(期待値・寸法文字・
    実在円)は**すべてモデル実寸空間**で行う。図面座標のまま比較すると scale≠1 で全滅する。
    """
    v = measure_from_defpoints(dim)
    return None if v is None else v / float(scale)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
_SIDE_ANGLE = {"above": 0.0, "below": 0.0, "left": 90.0, "right": 90.0}
_DIRECTION_ANGLE = {"horizontal": 0.0, "vertical": 90.0}


def resolve_direction(measure, side):
    d = measure.get("direction")
    if d is None:
        return _SIDE_ANGLE[side]
    if isinstance(d, (int, float)):
        return float(d)
    return _DIRECTION_ANGLE[d]


def is_oblique_direction(angle):
    u"""測定方向が図面の軸(0度/90度)に平行でないか。"""
    a = float(angle) % 180.0
    return min(abs(a - 0.0), abs(a - 90.0), abs(a - 180.0)) > 1e-6


def oblique_base_point(side, p1, p2, angle, offset):
    u"""斜め線形寸法(紙面内で回転した正多角形の対角など)の寸法線の位置。

    軸平行寸法はビュー外周から offset だけ外へ逃がすが、斜め寸法には外周に対する
    「上下左右」が定義できない。そこで**測定点の中点から測定方向の法線向きへ offset**
    だけ離す(向きの符号だけを placement.side から決める)。
    結果としてフィーチャーの真横に置かれるので、ビュー外周の寸法予約帯には算入しない
    (`compose_drawing.plan_view_reserves` 側も同じ判定で除外している。判定式を
    片方だけ変えるとレイアウトと作図がずれるので、必ず両方同時に直すこと)。
    """
    a = math.radians(float(angle))
    nx, ny = -math.sin(a), math.cos(a)     # 測定方向の左手法線
    if side in ("below", "left"):
        nx, ny = -nx, -ny
    mx = (p1[0] + p2[0]) / 2.0
    my = (p1[1] + p2[1]) / 2.0
    return (mx + nx * offset, my + ny * offset)


def _proj(p, ux, uy):
    return p[0] * ux + p[1] * uy


def check_chain_alignment(plan, dims_by_id, scale=1.0, tol_mm=0.01):
    u"""**直列連記(`placement.chain_group`)が本当に1本の寸法線に並んでいるか**を、
    保存済み/生成済みの DIMENSION エンティティ**だけ**から独立に検証する。

    人間コーパスが多用する「端点が接する寸法を同一寸法線へ連記する」配置
    (`調査/drawing_style_analysis.md`§6。生成図面は0%だった)をエンジン機能にした以上、
    **壊れたときに検出できなければ意味が無い**。判定はすべて実DXFの defpoint から行い、
    計画の自己申告(placement)はグループの**メンバーシップにしか**使わない。

    dims_by_id: 計画の寸法ID -> ezdxf の DIMENSION エンティティ
    Returns: [{"view","group","ids","offset_coord","segments","ok","errors",
               "member_errors":{id:[msg]}}]

    検査項目(1つでも破れたらそのグループは不合格):
      (1) メンバーが2本以上ある
      (2) 全メンバーが線形寸法(dimtype base 0)で、測定方向(angle)が一致する
      (3) 寸法線の**法線方向の位置が全メンバーで一致**する(=同一直線上にある)
      (4) 測定区間が互いに重ならない(連記した寸法どうしが重なって描かれない)
      (5) 隣り合う区間が端点を共有する(=直列。飛び地は「同じ線に置いただけ」で直列ではない)
    """
    groups = {}
    for item in plan.get("dimensions", []):
        k = compose_drawing.chain_key(item)
        if k is None:
            continue
        groups.setdefault(k, []).append(item)

    out = []
    for (view, gname), items in sorted(groups.items()):
        rep = {"view": view, "group": gname, "ids": [i["id"] for i in items],
               "offset_coord": None, "segments": [], "errors": [], "member_errors": {}}

        def err(msg, ids=None):
            rep["errors"].append(msg)
            for i in (ids if ids is not None else rep["ids"]):
                rep["member_errors"].setdefault(i, []).append(msg)

        if len(items) < 2:
            err(u"直列連記グループ '%s'(%s)のメンバーが%d本しかない(2本以上必要)"
                % (gname, view, len(items)))
            rep["ok"] = False
            out.append(rep)
            continue

        recs, missing = [], []
        for it in items:
            ent = dims_by_id.get(it["id"])
            if ent is None:
                missing.append(it["id"])
                continue
            d = ent.dxf
            if (d.dimtype & 7) != 0:
                err(u"%s: 直列連記は線形寸法(rotated)だけが対象(dimtype base=%d)"
                    % (it["id"], d.dimtype & 7), [it["id"]])
                continue
            a = float(d.get("angle", 0.0))
            ux, uy = math.cos(math.radians(a)), math.sin(math.radians(a))
            nx, ny = -uy, ux
            recs.append({
                "id": it["id"], "angle": a % 180.0,
                "line_coord": _proj((d.defpoint.x, d.defpoint.y), nx, ny) / float(scale),
                "s0": min(_proj((d.defpoint2.x, d.defpoint2.y), ux, uy),
                          _proj((d.defpoint3.x, d.defpoint3.y), ux, uy)) / float(scale),
                "s1": max(_proj((d.defpoint2.x, d.defpoint2.y), ux, uy),
                          _proj((d.defpoint3.x, d.defpoint3.y), ux, uy)) / float(scale),
                "side": (it.get("placement") or {}).get("side"),
            })
        if missing:
            err(u"直列連記グループ '%s' のメンバー %s が図面に存在しない" % (gname, missing), missing)

        if len(recs) >= 2:
            a0 = recs[0]["angle"]
            for r in recs[1:]:
                if abs(r["angle"] - a0) > 1e-6:
                    err(u"%s: 測定方向が %.4f度 で、グループ先頭の %.4f度 と違う"
                        u"(直列連記は同一方向の寸法だけ)" % (r["id"], r["angle"], a0), [r["id"]])
            sides = {r["side"] for r in recs}
            if len(sides) > 1:
                err(u"直列連記グループ '%s' の placement.side が混在している: %s"
                    % (gname, sorted(str(s) for s in sides)))
            c0 = recs[0]["line_coord"]
            rep["offset_coord"] = round(c0, 6)
            for r in recs[1:]:
                dev = abs(r["line_coord"] - c0)
                if dev > tol_mm:
                    err(u"%s: 寸法線が同一直線上に無い(先頭とのずれ %.4fmm > %.4fmm)"
                        u"= 直列連記が崩れている" % (r["id"], dev, tol_mm), [r["id"]])
            ordered = sorted(recs, key=lambda r: r["s0"])
            rep["segments"] = [{"id": r["id"], "s0": round(r["s0"], 6), "s1": round(r["s1"], 6),
                                "line_coord": round(r["line_coord"], 6)} for r in ordered]
            for a_, b_ in zip(ordered, ordered[1:]):
                gap = b_["s0"] - a_["s1"]
                if gap < -tol_mm:
                    err(u"%s と %s の測定区間が %.4fmm 重なっている(同一寸法線上で重なる連記は不正)"
                        % (a_["id"], b_["id"], -gap), [a_["id"], b_["id"]])
                elif gap > tol_mm:
                    err(u"%s と %s が端点を共有していない(隙間 %.4fmm)"
                        u"= 直列(chain)ではなく飛び地の並記になっている"
                        % (a_["id"], b_["id"], gap), [a_["id"], b_["id"]])
        rep["ok"] = not rep["errors"]
        out.append(rep)
    return out


def base_point(region, side, p1, p2, offset):
    x0, y0, x1, y1 = region
    mx = (p1[0] + p2[0]) / 2.0
    my = (p1[1] + p2[1]) / 2.0
    if side == "above":
        return (mx, y1 + offset)
    if side == "below":
        return (mx, y0 - offset)
    if side == "left":
        return (x0 - offset, my)
    if side == "right":
        return (x1 + offset, my)
    raise ValueError("unknown placement.side: %s" % side)


# ---------------------------------------------------------------------------
# 様式第3弾・層3「線の振る舞い」: 補助線が輪郭線と溶ける問題(論点7)
#
# 今泉さん指摘:「線の一番先端から取ると、寸法線と図解の線がすべて寸法線のように見える」。
# 生成図面は寸法補助線が**輪郭線と同一直線上に連続して延びる**ため、
# モノの形の線(図解)と注釈の線(寸法)の区別が視覚的に消える。
#
# 対策は2段:
#   (a) 検出 —— 補助線の延長軸上に、同一直線の輪郭線分が近接して存在するかを機械判定する
#       (`find_collinear_contours`)。合わせて**実DXFに描かれた補助線のすき間**を測って
#       dimexo が実際に効いているかを検証する(`measure_extension_gaps`)。
#       CLAUDE.md の教訓「値が合っていることは正しく描かれていることの証明ではない。
#       作図系の検証は必ず描画実体を測る」に従う。
#   (b) 自動回避 —— 検出された寸法だけ **dimexo を広げて**すき間を視認できる量にする
#       (1寸法=1専用DIMSTYLE方式なので他の寸法に副作用が無い)。
#       補助線の経路そのものに輪郭が重なる(overlap)ケースは広げても解けないので様式警告。
# ---------------------------------------------------------------------------
#: 補助線と輪郭線を「平行」とみなす角度許容[度]
COLLINEAR_ANGLE_TOL_DEG = 0.5
#: 補助線と輪郭線を「同一直線上」とみなす垂直距離[mm]
COLLINEAR_OFFSET_TOL_MM = 0.2
#: 同一直線上の輪郭がこの距離以内まで迫っていたら「溶けている」と判定[mm]
COLLINEAR_GAP_MAX_MM = 2.5
#: 自動回避で使う dimexo。**既定では使わない**(下記の実測結果を参照)
EXT_GAP_AVOID_MM = 3.0
#: 描かれたすき間と dimexo の一致許容[mm](dimexoが実DXFで機能しているかの検証)
EXT_GAP_VERIFY_TOL_MM = 0.05

# ---------------------------------------------------------------------------
# ❗❗論点7の実測結果(調査/measure_extension_collinear.py・人間5枚 vs 生成5枚)
#
#   仮説A「生成の補助線は輪郭線と同一直線上に来るから溶ける」は **反証された**。
#     用紙倍率で正規化して同じ判定器で測ると
#     **人間 59/88 = 67.1% / 生成 46/63 = 73.0%** で、ほぼ同率。
#     人間も日常的に「輪郭の端点から同一直線上に」補助線を出している。
#   仮説B「すき間(dimexo)が足りない」も **反証された**。
#     紙面上の dimexo はコーパス927/927 = 100% が 1.0mm。生成も 1.0mm で同値
#     (人間ファイルのDXF単位 4.0/3.0/1.5 は用紙倍率であって紙面量ではない)。
#     しかも実DXFの描画実体を測ると付け根のすき間は dimexo と 0.0000mm 一致していた
#     (= dimexo は正しく機能している)。
#   ★実際に効いていた差は **補助線の長さ**だった:
#       人間  中央 17.2mm / p75 23.1 / p90 29.1 / 最大 63.7
#       生成  中央 39.8mm / p75 68.3 / p90 114.2 / **最大 211.4**
#     A3の紙に211mmの緑の直線が輪郭の延長として走れば、それは「図解の線」に見える。
#     今泉さん指摘「線の一番先端から取ると全部が寸法線に見える」の実体はこれである。
#   原因は構造的: 寸法線は**ビュー外接矩形**から offset だけ外に置かれるので、
#     `補助線長 = (測定点からその辺の輪郭までの距離) + offset`。
#     測定点がその辺に載っていれば offset(=16mm・人間の中央値17mmと一致)で済むが、
#     **反対側の辺に寸法を置くと部品を縦断する長さになる**。
#   → よってエンジンの仕事は「検出して警告する」ことであり、直すのは**計画の側**
#     (寸法を測定点に最も近い辺へ置く。plan_prompt 作法12)。
#     dimexoを勝手に広げる自動回避は**自社流儀(紙面1.0mm)から外れる**ので既定OFFにした。
# ---------------------------------------------------------------------------
#: 補助線の長さの様式しきい値[mm](人間コーパス5枚の p90 = 29.1mm を丸めた値)
EXT_LEN_WARN_MM = 30.0


def contour_segments(entities):
    u"""ビューの実ジオメトリを線分列 [(a, b, entity), ...] へ平坦化する(図面座標mm)。"""
    segs = []
    for e in entities:
        t = e.dxftype()
        try:
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                segs.append(((a.x, a.y), (b.x, b.y), e))
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points("xy")]
                if e.closed and len(pts) > 2:
                    pts = pts + [pts[0]]
                for a, b in zip(pts, pts[1:]):
                    segs.append((a, b, e))
            elif t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                for a, b in zip(pts, pts[1:]):
                    segs.append((a, b, e))
        except Exception:
            continue
    return segs


def extension_line_geometry(p1, p2, base, angle_deg, dimexo=1.0, dimexe=2.0):
    u"""線形寸法の**補助線2本**の幾何(図面座標mm)。

    Returns: [{"origin":(x,y), "u":(ux,uy), "length":L, "t_start":dimexo, "t_end":L+dimexe}, ...]
      origin = 測定点 / u = 測定点から寸法線へ向かう単位ベクトル /
      t は origin を0とした u 方向の座標。描かれる補助線は [t_start, t_end]。
    """
    a = math.radians(float(angle_deg))
    ux, uy = math.cos(a), math.sin(a)          # 寸法線の方向
    nx, ny = -uy, ux                            # それに直交する方向(補助線の軸)
    out = []
    for p in (p1, p2):
        d = (base[0] - p[0]) * nx + (base[1] - p[1]) * ny
        s = 1.0 if d >= 0 else -1.0
        out.append({"origin": (float(p[0]), float(p[1])),
                    "u": (nx * s, ny * s), "length": abs(d),
                    "t_start": float(dimexo), "t_end": abs(d) + float(dimexe)})
    return out


def find_collinear_contours(ext, segs, angle_tol_deg=COLLINEAR_ANGLE_TOL_DEG,
                            offset_tol=COLLINEAR_OFFSET_TOL_MM,
                            gap_max=COLLINEAR_GAP_MAX_MM):
    u"""補助線 `ext` と**同一直線上で近接する**輪郭線分を列挙する(=溶け込みの検出)。

    典型例(論点7): 板厚3.2を板の端から取ると、補助線が板の長辺の延長になり
    「輪郭線がそこで終わる」ことが見て分からなくなる。
    """
    ox, oy = ext["origin"]
    ux, uy = ext["u"]
    nx, ny = -uy, ux
    sin_tol = math.sin(math.radians(angle_tol_deg))
    hits = []
    for a, b, e in segs:
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg_len = math.hypot(dx, dy)
        if seg_len < 1e-9:
            continue
        vx, vy = dx / seg_len, dy / seg_len
        if abs(vx * uy - vy * ux) > sin_tol:          # 平行でない
            continue
        if abs((a[0] - ox) * nx + (a[1] - oy) * ny) > offset_tol:   # 同一直線でない
            continue
        t0 = (a[0] - ox) * ux + (a[1] - oy) * uy
        t1 = (b[0] - ox) * ux + (b[1] - oy) * uy
        if t0 > t1:
            t0, t1 = t1, t0
        e0, e1 = ext["t_start"], ext["t_end"]
        if t1 < e0:
            gap = e0 - t1
        elif t0 > e1:
            gap = t0 - e1
        else:
            gap = 0.0
        if gap > gap_max:
            continue
        hits.append({"gap_mm": round(gap, 4), "overlap": gap <= 1e-9,
                     "seg": [[round(a[0], 3), round(a[1], 3)],
                             [round(b[0], 3), round(b[1], 3)]],
                     "handle": str(e.dxf.get("handle", ""))})
    return hits


def measure_extension_gaps(dim, ext_geoms, angle_tol_deg=COLLINEAR_ANGLE_TOL_DEG,
                           offset_tol=COLLINEAR_OFFSET_TOL_MM):
    u"""**実DXFに描かれた**補助線の付け根のすき間[mm]を測る(dimexoの実効検証)。

    DIMENSIONを virtual_entities() で展開し、補助線軸と同一直線上のLINEを拾って
    「測定点から最も近い端点までの距離」を返す。計画値ではなく描画実体を測る。
    """
    try:
        prims = [p for p in dim.virtual_entities() if p.dxftype() == "LINE"]
    except Exception:
        return [None for _ in ext_geoms]
    sin_tol = math.sin(math.radians(angle_tol_deg))
    out = []
    for ext in ext_geoms:
        ox, oy = ext["origin"]
        ux, uy = ext["u"]
        nx, ny = -uy, ux
        best = None
        for p in prims:
            a = (p.dxf.start.x, p.dxf.start.y)
            b = (p.dxf.end.x, p.dxf.end.y)
            dx, dy = b[0] - a[0], b[1] - a[1]
            seg_len = math.hypot(dx, dy)
            if seg_len < 1e-9:
                continue
            vx, vy = dx / seg_len, dy / seg_len
            if abs(vx * uy - vy * ux) > sin_tol:
                continue
            if abs((a[0] - ox) * nx + (a[1] - oy) * ny) > offset_tol:
                continue
            t0 = (a[0] - ox) * ux + (a[1] - oy) * uy
            t1 = (b[0] - ox) * ux + (b[1] - oy) * uy
            lo = min(t0, t1)
            if lo < -0.5:        # 反対向きに伸びる線(別の補助線・寸法線本体)は対象外
                continue
            if best is None or lo < best:
                best = lo
        out.append(None if best is None else round(best, 4))
    return out


# ---------------------------------------------------------------------------
# 様式第3弾・層2「置き場所」: 注記は対象の近くに(論点21)
#
# 生成図面は穴注記を**右端に固定**していたため引出線が図を斜めに長く横断・交差していた。
# 人間は注記を対象フィーチャーの近くに置く(位置は固定でなく対象追従)。
# ---------------------------------------------------------------------------
#: 引出線の長さの上限目安[mm]。人間図面の実測はおおむね10〜50mm(論点21)
LEADER_LEN_MAX_MM = 50.0
#: 自動配置で試す「対象円の外周から注記の折れ点まで」の距離[mm](近い順)
NOTE_PLACE_DISTS = (10.0, 13.0, 16.0, 20.0, 25.0, 30.0, 36.0)
#: 自動配置で試す方向(単位ベクトル化して使う)
NOTE_PLACE_DIRS = ((1.0, 1.0), (-1.0, 1.0), (1.0, -1.0), (-1.0, -1.0),
                   (1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0))
#: 折れ点から文字までの水平な着地線[mm]
NOTE_LANDING_MM = 4.0


def note_text_box(insert, size_wh, attachment="bottom-left"):
    u"""注記MTEXTの外接矩形(attachment を考慮)。"""
    w, h = size_wh
    x, y = insert[0], insert[1]
    a = str(attachment)
    if a.endswith("-center"):
        x0, x1 = x - w / 2.0, x + w / 2.0
    elif a.endswith("-right"):
        x0, x1 = x - w, x
    else:
        x0, x1 = x, x + w
    if a.startswith("top"):
        y0, y1 = y - h, y
    elif a.startswith("middle"):
        y0, y1 = y - h / 2.0, y + h / 2.0
    else:
        y0, y1 = y, y + h
    return (x0, y0, x1, y1)


def auto_place_hole_note(center, radius, size_wh, obstacles, frame_rect,
                         view_center=None, dists=NOTE_PLACE_DISTS,
                         landing=NOTE_LANDING_MM):
    u"""対象フィーチャーの近傍で、他要素と衝突しない注記位置を探す。

    Args:
        center:    対象円の中心(図面座標mm)
        radius:    対象円の半径(図面座標mm)。引出線の始点は円周上に置く
                   (`anchor_check` が「始点が円周上にあること」を検証する)
        size_wh:   注記の文字枠(幅, 高さ)mm
        obstacles: 避ける矩形のリスト(ビュー輪郭・既配置の文字枠・表題欄・左上ノート)
        frame_rect:この矩形の内側に文字枠が収まること
        view_center: ビュー中心。**外向き**の方向を優先するために使う

    Returns: {"leader_points", "insert", "attachment", "box", "leader_len_mm"} or None
    """
    cx, cy = float(center[0]), float(center[1])
    dirs = list(NOTE_PLACE_DIRS)
    if view_center is not None:
        ox = cx - float(view_center[0])
        oy = cy - float(view_center[1])
        n = math.hypot(ox, oy)
        if n > 1e-9:
            ox, oy = ox / n, oy / n
            dirs.sort(key=lambda d: -(d[0] * ox + d[1] * oy) /
                      math.hypot(d[0], d[1]))
    for dist in dists:
        for dx, dy in dirs:
            n = math.hypot(dx, dy)
            ux, uy = dx / n, dy / n
            start = (cx + radius * ux, cy + radius * uy)
            elbow = (cx + (radius + dist) * ux, cy + (radius + dist) * uy)
            sgn = 1.0 if ux >= 0 else -1.0
            land = (elbow[0] + sgn * landing, elbow[1])
            attach = "bottom-left" if sgn > 0 else "bottom-right"
            box = note_text_box(land, size_wh, attach)
            if not _rect_inside(box, frame_rect):
                continue
            if any(_rect_overlap(box, ob) for ob in obstacles):
                continue
            pts = [start, elbow, land]
            ln = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                     for a, b in zip(pts, pts[1:]))
            return {"leader_points": pts, "insert": land, "attachment": attach,
                    "box": box, "leader_len_mm": round(ln, 4),
                    "dist_mm": dist}
    return None


def polyline_length(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def _median(vals):
    a = sorted(vals)
    if not a:
        return None
    n = len(a)
    return round(a[n // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2.0, 3)


# ---------------------------------------------------------------------------
# 様式第3弾・層2「置き場所」: 円形ビューの径寸法は1本まで(論点14)
#
# 生成はφ200/φ190/φ219.1の3本を円形ビューに対角線で入れ、大円の中がX字交差で騒がしい。
# 人間は径を断面ビューに積み、円形ビューはPCD・穴・長穴・角度など**位置情報専用**にする。
# 「円形ビュー外径=ネイティブDIAMETER」裁定の運用は**1本まで**(2本目以降は断面ビューへ)。
# ---------------------------------------------------------------------------
CIRCULAR_VIEW_DIA_MAX = 1


def check_circular_view_diameters(plan, resolved_kinds, limit=CIRCULAR_VIEW_DIA_MAX):
    u"""円形ビューに置かれたネイティブ直径寸法が limit 本を超えていないか。

    Returns: [{"view":..., "ids":[...], "count":n}, ...](超過したビューだけ)
    """
    per_view = {}
    for item in plan.get("dimensions", []):
        if resolved_kinds.get(item["id"]) != "diameter_native":
            continue
        per_view.setdefault(item["view"], []).append(item["id"])
    return [{"view": v, "ids": ids, "count": len(ids)}
            for v, ids in sorted(per_view.items()) if len(ids) > limit]


# ---------------------------------------------------------------------------
# レイアウト(テキスト矩形と衝突検出。初歩的なもの・フェーズ4で改善)
# ---------------------------------------------------------------------------
def _text_box(doc, dim, width_factor=0.75):
    geom = dim.dxf.get("geometry", None)
    if not geom or geom not in doc.blocks:
        return None
    for e in doc.blocks.get(geom):
        if e.dxftype() != "MTEXT":
            continue
        raw = _MTEXT_CODE_RE.sub("", re.sub(r"\{[^{}]*\}", "", e.text))
        raw = raw.replace("%%c", "f").replace("%%p", "f")
        n = max(1, len(raw))
        h = e.dxf.char_height
        w = n * h * width_factor
        rot = e.dxf.get("rotation", 0.0) % 180.0
        if abs(rot - 90.0) < 1.0:
            w, h = h, w
        p = e.dxf.insert
        return (p.x - w / 2.0, p.y - h / 2.0, p.x + w / 2.0, p.y + h / 2.0)
    return None


def _disp_len(s):
    u"""表示幅の概算(全角=2・半角=1)。注記の外接矩形推定に使う。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(ch) in ("F", "W", "A") else 1 for ch in s)


def _rect_overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _rect_inside(a, b):
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _entity_box(entities):
    u"""エンティティ群のワールド座標外接矩形。空なら None。"""
    ents = [e for e in entities if e is not None]
    if not ents:
        return None
    bb = bbox_extents(ents, fast=False)
    if bb is None or not bb.has_data:
        return None
    return (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y)


def dim_geometry_box(dim):
    u"""DIMENSION の**描画実体全体**(寸法線・矢印・補助線・文字)の外接矩形。

    ❗`_text_box` は寸法**文字**しか見ていない。盲検 25154-5-08 では寸法文字は表題欄の外なのに
    補助線と寸法線が表題欄に食い込んでいて、`layout.collisions` に何も出なかった
    (調査/blind_test_report.md §7)。衝突判定は必ず展開実体(virtual_entities)で行う。
    """
    try:
        return _entity_box(list(dim.virtual_entities()))
    except Exception:
        return None


def dim_primitive_boxes(dim):
    u"""DIMENSION を展開した**1プリミティブごと**の外接矩形リスト。

    ❗寸法全体の1つのbboxで図枠要素と当てると、細い補助線しか通っていない広い空白まで
    「重なった」ことになり偽陽性を量産する(2ビューをまたぐ長い寸法で顕著)。
    線1本ずつのbboxで見れば、軸平行の線・矢印・文字については実交差とほぼ一致する。
    """
    out = []
    try:
        prims = list(dim.virtual_entities())
    except Exception:
        return out
    for p in prims:
        b = _entity_box([p])
        if b is not None:
            out.append(b)
    return out


# 図枠エンティティのうち「ページ全体を囲む枠」(外形LWPOLYLINE等)はbbox交差が無意味なので
# 個別照合から外し、FRAME_RECT への内包判定(outside_frame)に委ねる
FRAME_CONTAINER_W_MM = 200.0
FRAME_CONTAINER_H_MM = 100.0


def frame_entity_boxes(entities):
    u"""図枠エンティティの外接矩形リスト(ページ全体を囲む容器エンティティは除外)。"""
    out = []
    for e in entities:
        b = _entity_box([e])
        if b is None:
            continue
        if (b[2] - b[0]) > FRAME_CONTAINER_W_MM and (b[3] - b[1]) > FRAME_CONTAINER_H_MM:
            continue
        out.append((b, e.dxftype(), str(e.dxf.get("handle", ""))))
    return out


def frame_zone_collisions(frame_boxes, boxes):
    u"""実体矩形が図枠要素に重なるものを列挙する。

    boxes: id -> プリミティブ矩形のリスト [(x0,y0,x1,y1), ...]
           (1要素だけのリストを渡せば従来どおり全体bboxでの判定になる)
    """
    out = []
    for did in sorted(boxes):
        prims = boxes[did]
        if not prims:
            continue
        union = (min(p[0] for p in prims), min(p[1] for p in prims),
                 max(p[2] for p in prims), max(p[3] for p in prims))
        hits = []
        if any(_rect_overlap(p, TITLE_BLOCK_RECT) for p in prims):
            hits.append("title_block")
        if any(_rect_overlap(p, NOTE_ZONE_RECT) for p in prims):
            hits.append("note_zone")
        if not _rect_inside(union, FRAME_RECT):
            hits.append("outside_frame")
        fe = sorted({t for (b, t, _h) in frame_boxes
                     if any(_rect_overlap(p, b) for p in prims)})
        if fe:
            hits.append("frame_entity:%s" % ",".join(fe))
        for h in hits:
            out.append({"id": did, "zone": h, "box": [round(v, 4) for v in union]})
    return out


def check_frame_collisions(doc, id_of_dim=None, template_path=None):
    u"""**保存済みDXFを読み直して**、寸法・引出線・注記が図枠/表題欄に重なっていないか検査する。

    dim_engine の自己申告を信用せず独立に判定するための入口(独立検証から呼ぶ)。
    id_of_dim: DIMENSION -> レポート用ID を返す関数(既定は DIMSTYLE名)。
    """
    tmpl = template_path or os.path.join(ROOT, u"図枠", u"frame_template.dxf")
    msp = doc.modelspace()
    all_ents = list(msp)
    part_entities, _summary = subtract_frame(doc, template_path=tmpl)
    part_ids = {id(e) for e in part_entities}
    frame_boxes = frame_entity_boxes([e for e in all_ents if id(e) not in part_ids])

    boxes = {}
    for e in part_entities:
        t = e.dxftype()
        if t == "DIMENSION":
            b = dim_primitive_boxes(e)
            key = id_of_dim(e) if id_of_dim else str(e.dxf.dimstyle)
        elif t == "LEADER":
            # 引出線は折れ線なので、区間ごとの矩形で見る(全体bboxだと空白を含む)
            vs = [(p[0], p[1]) for p in e.vertices]
            b = [(min(a[0], c[0]), min(a[1], c[1]), max(a[0], c[0]), max(a[1], c[1]))
                 for a, c in zip(vs, vs[1:])]
            key = "LEADER:%s" % e.dxf.handle
        elif t == "MTEXT" and ("%%c" in e.text or u"キリ" in e.text or u"ザグリ" in e.text
                               or u"深さ" in e.text):
            bb = _entity_box([e])
            b = [bb] if bb else []
            key = "NOTE:%s" % e.dxf.handle
        else:
            continue
        if b:
            boxes[key] = b
    return frame_zone_collisions(frame_boxes, boxes)


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def apply_plan(plan_path, out_dxf_path, dimstyle_spec_path=DIMSTYLE_SPEC_DEFAULT,
               base_dxf_override=None):
    u"""作図計画JSONを実行し、寸法・注記入りDXFを出力する。

    ゲート①(寸法値照合)に1本でも不合格があれば DimensionGateError を送出し、
    **DXFは保存しない**(不合格品を納品しない原則)。
    """
    with io.open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    spec = load_dimstyle_spec(dimstyle_spec_path)

    defaults = plan.get("defaults", {})
    # ❗寸法線オフセット(first_offset_mm / stack_step_mm / offset_mm / chain_group)の解釈は
    #   compose_drawing.resolve_dim_offsets に一本化した(レイアウトと実配置の一致を構造的に担保)
    snap_tol = float(defaults.get("snap_tol_mm", 0.01))
    gate_tol = float(defaults.get("gate_tol_mm", 0.01))
    # 角度寸法(kind='angle')の許容差[度]。長さのmm許容差とは別物なので専用キーで持つ
    angle_tol = float(defaults.get("angle_tol_deg", ANGLE_TOL_DEG_DEFAULT))
    diameter_style = dict(DIAMETER_STYLE_DEFAULT)
    diameter_style.update(defaults.get("diameter_style", {}))
    hole_note_style = dict(HOLE_NOTE_DEFAULT)
    hole_note_style.update(defaults.get("hole_note", {}))
    # 層1: 呼び値翻訳(注記の径値のみ)。既定ON。丸め窓は nominal_size 側の定数
    nominal_on = bool(defaults.get("nominal_translation", True))
    # ❗許容窓は**計画から上書きさせない**。計画側で窓を広げられると
    #   「翻訳も検算も同じ広い窓で通る」抜け穴になり、嘘の丸めが素通りする
    nominal_tol = nominal_size.NOMINAL_TOL_MM
    nominal_pending = []      # 呼び値未確定(解釈レポート/質問票へ誘導する)

    src = plan["source"]
    base_dxf = base_dxf_override or os.path.join(ROOT, src["base_dxf"])
    meta_json = os.path.join(ROOT, src["meta_json"])
    scale, use_views, reserves = plan_layout(plan)

    warnings = []

    # --- 1) 土台DXFとビュー座標系 ---------------------------------------
    doc = ezdxf.readfile(base_dxf)
    doc.encoding = "cp932"
    msp = doc.modelspace()

    tf = build_view_transforms(meta_json, scale, views=use_views, reserves=reserves)
    regions = {k: tf[k]["region"] for k in tf}
    msp_before = list(msp)
    part_entities, frame_summary = subtract_frame(
        doc, template_path=os.path.join(ROOT, u"図枠", u"frame_template.dxf"))
    per_view = classify_view_geometry(part_entities, regions)
    # 図枠側の実エンティティ(表題欄の罫線・左上ノートの丸など)。寸法との衝突判定に使う
    _part_ids = {id(e) for e in part_entities}
    frame_entities = [e for e in msp_before if id(e) not in _part_ids]
    frame_boxes = frame_entity_boxes(frame_entities)
    feats = {k: feature_points(per_view[k]) for k in per_view}

    # ビュー実輪郭(分類済み実ジオメトリの外接矩形)。寸法線オフセットの基準にする
    view_bbox = {}
    for k in per_view:
        if not per_view[k]:
            view_bbox[k] = regions[k]
            continue
        bb = bbox_extents(per_view[k], fast=True)
        view_bbox[k] = (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y)

    # --- 2) スタイル準備 -------------------------------------------------
    text_style = ensure_text_style(doc, spec)
    arrows_created = ensure_arrow_blocks(
        doc, tuple({spec["arrow"]["dimblk1"]["value"], spec["arrow"]["dimblk2"]["value"]}))
    dimvars_base = base_dimvars(spec)
    dimvars_base["dimtxsty"] = text_style
    # ❗尺度対応の要: 作図は scale 倍だが**寸法値はモデル実寸を表示する**(自社流儀。
    #   人間図面も1:2図面に実寸を記入する)。DIMSTYLEの dimlfac(寸法測定値の倍率)を
    #   1/scale にすることで、CADが再計測しても表示値がモデル実寸のまま保たれる。
    #   ezdxf も描画時に measurement * dimlfac を文字にする(render/dim_base.py 実測)。
    dimvars_base["dimlfac"] = 1.0 / scale
    dim_layer = spec["color_layer"]["dimension_entity_layer"]["value"]
    leader_layer = spec["color_layer"]["leader_entity_layer"]["value"]
    for lname in {dim_layer, leader_layer}:
        if lname not in doc.layers:
            doc.layers.add(lname)

    # --- 3) 測定点の解決 -------------------------------------------------
    def to_draw(view, space, p):
        if space == "model":
            return tf[view]["model_to_draw"](p)
        return (float(p[0]), float(p[1]))

    # --- 4) 寸法生成 -----------------------------------------------------
    gate_rows = []
    dimstyle_records = {}
    text_boxes = {}
    geom_boxes = {}      # 寸法・引出線の**描画実体**の外接矩形(レポート表示用)
    prim_boxes = {}      # 同・プリミティブ単位の矩形リスト(図枠衝突判定用)
    counter = [0]

    def next_style_name():
        counter[0] += 1
        name = "GEN%03d" % counter[0]
        while name in doc.dimstyles:
            counter[0] += 1
            name = "GEN%03d" % counter[0]
        return name

    resolved_kinds = {}
    # ❗寸法線オフセットの決定は compose と**同じ1実装**から取る(直列連記の正規化を含む)。
    #   ここで独自計算するとレイアウト(予約帯)と実配置がずれる
    dim_offsets = compose_drawing.resolve_dim_offsets(plan)
    dims_by_id = {}
    # 層3: 補助線の溶け込み(論点7)。ビューごとの輪郭線分を1回だけ作って使い回す
    # ❗既定OFF: 紙面dimexoは人間コーパス927/927=100%が1.0mm。広げると流儀から外れる
    #   (上の実測ブロック参照)。実験・特例用にオプションとしてだけ残す。
    ext_avoid = bool(defaults.get("extension_gap_avoid", False))
    view_segs = {k: contour_segments(per_view[k]) for k in per_view}
    ext_reports = {}
    style_warnings = []
    for item in plan.get("dimensions", []):
        did = item["id"]
        # kind='diameter' は文脈(context)と defaults.diameter_style から実装方式を解決する。
        # 明示の diameter_native / diameter_linear はそのまま使う(強制指定)。
        kind = item["kind"]
        if kind == "diameter":
            ctx = item.get("context", "profile_view")
            if ctx not in diameter_style:
                raise ValueError("%s: 未知のcontext '%s'(circular_view|profile_view)" % (did, ctx))
            kind = "diameter_native" if diameter_style[ctx] == "native" else "diameter_linear"
        resolved_kinds[did] = kind
        view = item["view"]
        if view not in tf:
            raise ValueError("%s: 未知のview '%s'" % (did, view))
        meas = item["measure"]
        space = meas.get("space", "view")
        placement = item.get("placement", {})
        side = placement.get("side", "below")
        offset = dim_offsets[did]

        # 専用DIMSTYLE(1寸法=1スタイル)
        dv = dict(dimvars_base)
        if "dimdec" in item:
            dv["dimdec"] = int(item["dimdec"])
        if kind in ("diameter_linear", "diameter_native"):
            dv["dimpost"] = "%%c<>"
        elif kind == "radius":
            dv["dimpost"] = item.get("dimpost", "R<>")
        elif kind == "linear":
            if item.get("dimpost"):
                dv["dimpost"] = item["dimpost"]
        tol = item.get("tolerance")
        text_override = item.get("text_override")
        if tol and tol.get("mode") == "limit":
            dv["dimtol"] = 1
            dv["dimtp"] = float(tol.get("upper", 0.0))
            dv["dimtm"] = abs(float(tol.get("lower", 0.0)))
            dv["dimtfac"] = spec["tolerance"]["native_limit_tolerance"]["params"]["dimtfac"]
            dv["dimtdec"] = int(tol.get("dec", 3))
            dv["dimtolj"] = spec["tolerance"]["native_limit_tolerance"]["params"]["dimtolj"]
        elif tol and tol.get("mode") == "symmetric":
            if text_override is None:
                text_override = "\\A1;%s%%%%p%s" % (
                    _fmt_num(item["value_expected"]), _fmt_num(tol["value"]))
        # ---- 層3: 補助線の溶け込み検出と自動回避(論点7) ----------------
        # ❗DIMSTYLEを作る**前**に判定する。1寸法=1専用DIMSTYLE方式なので、
        #   この寸法の dimexo だけを広げても他の寸法に副作用が無い。
        lin_pre = None
        if kind in ("linear", "diameter_linear"):
            _p1 = to_draw(view, space, meas["p1"])
            _p2 = to_draw(view, space, meas["p2"])
            _angle = resolve_direction(meas, side)
            if is_oblique_direction(_angle):
                # 斜め線形寸法(紙面内で回転した正多角形の対角/二面幅。ゲート②E4)。
                # side は「中点からどちら側へ寸法線を逃がすか」の符号としてだけ使う
                _base = oblique_base_point(side, _p1, _p2, _angle, offset)
            else:
                if abs((_angle % 180.0) - (_SIDE_ANGLE[side] % 180.0)) > 1e-6:
                    raise ValueError(
                        "%s: placement.side='%s' と measure.direction が矛盾しています"
                        % (did, side))
                _base = base_point(view_bbox[view], side, _p1, _p2, offset)
            lin_pre = (_p1, _p2, _angle, _base)
            _exo = float(dv.get("dimexo", 1.0))
            _exe = float(dv.get("dimexe", 2.0))
            _segs = view_segs.get(view, [])
            _geoms = extension_line_geometry(_p1, _p2, _base, _angle, _exo, _exe)
            _hits = [find_collinear_contours(g, _segs) for g in _geoms]
            erep = {"id": did, "view": view, "dimexo_planned": _exo,
                    "collinear_before": sum(len(h) for h in _hits), "avoided": False}
            if sum(len(h) for h in _hits) and ext_avoid:
                _exo2 = max(_exo, EXT_GAP_AVOID_MM)
                _g2 = extension_line_geometry(_p1, _p2, _base, _angle, _exo2, _exe)
                _h2 = [find_collinear_contours(g, _segs) for g in _g2]
                if sum(len(h) for h in _h2) < sum(len(h) for h in _hits):
                    dv["dimexo"] = _exo2
                    erep["avoided"] = True
                    _geoms, _hits = _g2, _h2
            erep["dimexo_used"] = float(dv.get("dimexo", 1.0))
            erep["collinear"] = [h for h in _hits]
            erep["collinear_count"] = sum(len(h) for h in _hits)
            # ★論点7の真因(実測): 補助線の**長さ**。人間 中央17.2/p90 29.1mm に対し
            #   生成は中央39.8/最大211.4mm だった。長さ = 測定点からその辺の輪郭までの距離 + offset
            erep["ext_len_mm"] = [round(g["t_end"] - g["t_start"], 3) for g in _geoms]
            erep["ext_len_max_mm"] = round(max(erep["ext_len_mm"]), 3)
            erep["side"] = side
            erep["_ext_geoms"] = _geoms
            ext_reports[did] = erep

        style_name = next_style_name()
        _new_dimstyle(doc, style_name, dv)

        attribs = {"layer": dim_layer}

        if kind in ("linear", "diameter_linear"):
            p1, p2, angle, base = lin_pre
            dim = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle,
                                     dimstyle=style_name, dxfattribs=attribs)
            if text_override:
                dim.dimension.dxf.text = text_override
            dim.render()
            ent = dim.dimension
            meas_pts = [p1, p2]
        elif kind == "diameter_native":
            # 円形ビューの外径。円を斜めに貫く寸法線+両端矢印(dimtype=3)。
            # 測定点は「円の中心」+「実在円の半径」で、任意角の円周点は特徴点にならないため
            # snapは中心のみ。代わりに **実在円の存在確認を必須**にする(下の circle_check)。
            center = to_draw(view, space, meas["p1"])
            # measure.diameter / value_expected は**モデル実寸**。作図半径は scale 倍する
            if "diameter" in meas:
                radius = float(meas["diameter"]) / 2.0 * scale
            elif "p2" in meas:
                edge = to_draw(view, space, meas["p2"])
                radius = math.hypot(edge[0] - center[0], edge[1] - center[1])
            else:
                radius = float(item["value_expected"]) / 2.0 * scale
            dim = msp.add_diameter_dim(center=center, radius=radius,
                                       angle=float(meas.get("leader_angle", 45.0)),
                                       dimstyle=style_name, dxfattribs=attribs)
            if text_override:
                dim.dimension.dxf.text = text_override
            dim.render()
            ent = dim.dimension
            fix_duplicate_diameter_prefix(doc, ent)
            meas_pts = [center]
        elif kind == "radius":
            center = to_draw(view, space, meas["p1"])
            if "radius" in meas:
                radius = float(meas["radius"]) * scale   # measure.radius はモデル実寸
                edge = None
            else:
                edge = to_draw(view, space, meas["p2"])
                radius = math.hypot(edge[0] - center[0], edge[1] - center[1])
            dim = msp.add_radius_dim(center=center, radius=radius,
                                     angle=float(meas.get("leader_angle", 45.0)),
                                     dimstyle=style_name, dxfattribs=attribs)
            if text_override:
                dim.dimension.dxf.text = text_override
            dim.render()
            ent = dim.dimension
            meas_pts = [center] + ([edge] if edge else [])
        elif kind == "angle":
            v = to_draw(view, space, meas["vertex"])
            p1 = to_draw(view, space, meas["p1"])
            p2 = to_draw(view, space, meas["p2"])
            if not text_override:
                raise ValueError("%s: kind='angle' は text_override 必須(§8ルール3)" % did)
            # ❗line1/line2 は**入れ替えて**渡す。ezdxf は
            #   start_angle = line2 の向き / end_angle = line1 の向き としてCCWに円弧を描く
            #   (dim_curved.AngularDimension)。計画JSONの意味「p1 から p2 へCCW」を
            #   描画と一致させるには line1=(v,p2) / line2=(v,p1) が正しい。
            #   旧実装(line1=(v,p1))は**常に優角(360-θ)の円弧**を描いていた(3-09の実害)。
            dim = msp.add_angular_dim_2l(
                base=to_draw(view, space, meas["base"]),
                line1=(v, p2), line2=(v, p1),
                dimstyle=style_name, dxfattribs=attribs)
            dim.dimension.dxf.text = text_override
            dim.render()
            ent = dim.dimension
            meas_pts = [v, p1, p2]
        else:
            raise ValueError("%s: 未知のkind '%s'" % (did, kind))

        if tol and tol.get("mode") == "limit":
            fix_zero_tolerance_text(doc, ent)
            fix_tolerance_height_factor(doc, ent, dv["dimtfac"])

        # ---- ゲート①(照合は**モデル実寸空間**。尺度はここで戻す) ----
        snaps = [round(nearest_feature_distance(p, feats[view]), 6) for p in meas_pts]
        is_angle = (kind == "angle")
        if is_angle:
            # ❗角度は長さではないので尺度換算をしてはいけない。許容差も[度]で持つ
            measured = measure_angle_deg(ent)
            cmp_tol, unit = angle_tol, u"度"
        else:
            measured = measure_model_value(ent, scale)
            cmp_tol, unit = gate_tol, u"mm"
        expected = float(item["value_expected"])
        raw_text = dim_text_of(doc, ent)
        shown = parse_dim_text_value(raw_text) if not text_override else None
        if is_angle and text_override:
            # ❗角度は text_override 必須(§8ルール3)。何もしないと「描いた角度文字が
            #   実測角と食い違っていても誰も気付かない」穴になるので、文字から数値を
            #   復元して実測角と突き合わせる
            shown = parse_angle_text_value(raw_text)

        row = {
            "id": did, "kind": kind, "plan_kind": item["kind"], "view": view,
            "expected": expected,
            "measured": None if measured is None else round(measured, 6),
            "diff_mm": None if measured is None else round(abs(measured - expected), 6),
            "snap_max_mm": round(max(snaps), 6) if snaps else None,
            "text": raw_text,
            "text_value": shown,
            "text_diff_mm": (None if (shown is None or measured is None)
                             else round(abs(shown - measured), 6)),
            "style": style_name,
            "ok": True, "errors": [],
        }
        if is_angle:
            row["unit"] = "deg"        # diff_mm/text_diff_mm は**度**で読むこと
            # ❗描かれた円弧そのものを検査する(優角側に描かれる欠陥の再発防止)
            row["arc_check"] = check_angle_arc(doc, ent, measured)
            if not row["arc_check"]["ok"]:
                row["errors"].append(
                    u"角度寸法の円弧が測定セクタの外に描かれている(優角側): %s"
                    % json.dumps(row["arc_check"], ensure_ascii=False))
        if row["snap_max_mm"] is not None and row["snap_max_mm"] > snap_tol:
            row["errors"].append(
                u"測定点が実ジオメトリ特徴点に一致しない(最大%.4fmm > %.4fmm)"
                % (row["snap_max_mm"], snap_tol))
        if measured is None:
            row["errors"].append(u"実測値を再計算できない(kind=%s)" % kind)
        elif row["diff_mm"] > cmp_tol:
            row["errors"].append(u"実測 %.4f vs 期待 %.4f (差 %.4f%s > %.4f%s)"
                                 % (measured, expected, row["diff_mm"], unit, cmp_tol, unit))
        if row["text_diff_mm"] is not None and row["text_diff_mm"] > cmp_tol:
            row["errors"].append(u"寸法文字 %.4f が実測 %.4f と不一致"
                                 % (shown, measured))

        if kind == "diameter_native":
            # ネイティブDIAMETER型は「そのビューに指定中心・指定径の実在円がある」ことを必須検証する
            # (任意角の円周点はsnap検証できないため、これがゲート①(a)の代替になる)
            # 実在円の探索は図面座標(=モデル実寸×scale)で行い、比較はモデル実寸へ戻す
            ent_o = find_circle(per_view[view], meas_pts[0], expected * scale, snap_tol)
            row["circle_check"] = {"ok": ent_o is not None,
                                   "center": [round(v, 4) for v in meas_pts[0]],
                                   "diameter": expected}
            if ent_o is None:
                row["errors"].append(
                    u"ネイティブDIAMETER: %s ビューに中心%s 直径%.4f(図面上φ%.4f)の実在円が無い"
                    % (view, [round(v, 4) for v in meas_pts[0]], expected, expected * scale))
            elif abs(ent_o.dxf.radius * 2.0 / scale - (measured or 0.0)) > gate_tol:
                row["errors"].append(
                    u"ネイティブDIAMETER: 実在円φ%.4f と実測 %.4f が不一致"
                    % (ent_o.dxf.radius * 2.0 / scale, measured))

        cc = item.get("cross_check")
        if cc:
            ccv = cc["view"]
            ent_c = find_circle(per_view[ccv], to_draw(ccv, cc.get("space", "view"), cc["center"]),
                                float(cc["diameter"]) * scale, snap_tol)
            if ent_c is None:
                row["errors"].append(
                    u"cross_check: %s ビューに中心%s 直径%.4f の円が実在しない"
                    % (ccv, cc["center"], float(cc["diameter"])))
                row["cross_check"] = {"ok": False}
            else:
                real_d = ent_c.dxf.radius * 2.0 / scale
                d = abs(real_d - (measured if measured is not None else float("nan")))
                row["cross_check"] = {"ok": d <= gate_tol, "view": ccv,
                                      "found_diameter": round(real_d, 6),
                                      "diff_vs_measured_mm": round(d, 6)}
                if d > gate_tol:
                    row["errors"].append(
                        u"cross_check: 実在円φ%.4f と実測 %.4f が不一致" % (real_d, measured))

        # ❗描かれた補助線のすき間を**実体から**測る(dimexoが実DXFで効いているかの検証)。
        #   CLAUDE.md の教訓「値が合っていることは正しく描かれていることの証明ではない」。
        er = ext_reports.get(did)
        if er is not None:
            er["drawn_gap_mm"] = measure_extension_gaps(ent, er.pop("_ext_geoms"))
            bad = [g for g in er["drawn_gap_mm"]
                   if g is not None and abs(g - er["dimexo_used"]) > EXT_GAP_VERIFY_TOL_MM]
            er["gap_matches_dimexo"] = not bad

        row["ok"] = not row["errors"]
        gate_rows.append(row)
        dims_by_id[did] = ent
        dimstyle_records[did] = style_name
        tb = _text_box(doc, ent, spec["text_style"]["width_factor"])
        if tb:
            text_boxes[did] = [round(v, 4) for v in tb]
        pbs = dim_primitive_boxes(ent)
        if pbs:
            prim_boxes[did] = pbs
            geom_boxes[did] = [round(v, 4) for v in (
                min(p[0] for p in pbs), min(p[1] for p in pbs),
                max(p[2] for p in pbs), max(p[3] for p in pbs))]

    # --- 4.4) 層3: 補助線の溶け込みの集計(論点7) ---------------------------
    ext_unresolved = [er for er in ext_reports.values() if er.get("collinear_count")]
    for did_, er in sorted(ext_reports.items()):
        if er.get("gap_matches_dimexo") is False:
            style_warnings.append(
                u"❗補助線の付け根のすき間が dimexo=%.2f と一致しない(%s・実測%s)。"
                u"dimexoが実DXFで効いていない疑い"
                % (er["dimexo_used"], did_, er["drawn_gap_mm"]))
    # ★実測で「溶ける」の主因と分かったのは長さ(人間 p90=29.1mm)。
    #   同一直線上に来ること自体は人間も67%やっており、単独では欠陥ではない。
    ext_long = sorted([er for er in ext_reports.values()
                       if er.get("ext_len_max_mm", 0.0) > EXT_LEN_WARN_MM],
                      key=lambda e: -e["ext_len_max_mm"])
    ext_long_collinear = [er for er in ext_long if er.get("collinear_count")]
    if ext_long_collinear:
        style_warnings.append(
            u"❗補助線が長すぎて輪郭線と溶ける(論点7・主因) %d本"
            u"(人間コーパスの補助線長 p90=%.0fmm 超 かつ 輪郭と同一直線上): %s。"
            u"**寸法を測定点に最も近い辺へ置く**(placement.side)ことで短くなる"
            % (len(ext_long_collinear), EXT_LEN_WARN_MM,
               [(er["id"], er["side"], er["ext_len_max_mm"])
                for er in ext_long_collinear]))
    ext_long_only = [er for er in ext_long if not er.get("collinear_count")]
    if ext_long_only:
        style_warnings.append(
            u"補助線が長い(p90=%.0fmm超・輪郭とは重ならない) %d本: %s"
            % (EXT_LEN_WARN_MM, len(ext_long_only),
               [(er["id"], er["side"], er["ext_len_max_mm"]) for er in ext_long_only]))

    # --- 4.6) 層2: 円形ビューの径寸法は1本まで(論点14) ----------------------
    circ_over = check_circular_view_diameters(plan, resolved_kinds)
    for c in circ_over:
        style_warnings.append(
            u"❗円形ビューの径寸法は1本まで(論点14): %s に %d本 %s。"
            u"2本目以降は断面ビューへ移し、円形ビューはPCD・穴・角度など位置情報専用にすること"
            % (c["view"], c["count"], c["ids"]))

    # --- 4.7) 層1: 径寸法の値の点検(呼び値でない径 -> 質問票へ) -------------
    #   ❗**寸法値は書き換えない**(モデル実測がゲート①の正)。列挙するだけ。
    nominal_review = []
    for item in plan.get("dimensions", []):
        if resolved_kinds.get(item["id"]) not in ("diameter_native", "diameter_linear"):
            continue
        v = float(item["value_expected"])
        if not nominal_size.is_nominal_like(v):
            nominal_review.append({"id": item["id"], "view": item["view"], "value": v})
    if nominal_review:
        style_warnings.append(
            u"❗呼び値でない径寸法 %d件(論点15/26。**値は実測のまま正**。"
            u"インロー/インチ系の疑いがあるので意図を質問票で確認する): %s"
            % (len(nominal_review), [(r["id"], r["value"]) for r in nominal_review]))

    # --- 4.5) 直列連記(chain_group)の整列検証 -------------------------------
    # ❗連記が崩れた(同一グループの寸法線がずれた・重なった・飛び地になった)図面は
    #   「正しいが読みにくい」どころか**誤読される**。ゲート①の一部として不合格にする。
    chain_reports = check_chain_alignment(plan, dims_by_id, scale=scale, tol_mm=gate_tol)
    _row_by_id = {r["id"]: r for r in gate_rows}
    for crep in chain_reports:
        for mid, msgs in crep["member_errors"].items():
            r = _row_by_id.get(mid)
            if r is None:
                continue
            r["errors"].extend(msgs)
            r["ok"] = False
        if crep["errors"] and not crep["member_errors"]:
            warnings.append(u"❗直列連記 '%s'(%s): %s"
                            % (crep["group"], crep["view"], "; ".join(crep["errors"])))

    # --- 5) 穴注記(LEADER + MTEXT) --------------------------------------
    note_rows = []
    if plan.get("hole_notes"):
        ldr_style = "GENLDR"
        if ldr_style not in doc.dimstyles:
            lv = dict(dimvars_base)
            lv.pop("dimpost", None)
            _new_dimstyle(doc, ldr_style, lv)
    for note in plan.get("hole_notes", []):
        view = note["view"]
        # ---- 層1: 実測径 -> 呼び値の翻訳(論点8・§15)。**注記の径値だけ**が対象 ----
        #   丸め先が自明でない値は丸めず「呼び値未確定」として質問票へ回す。
        nom_recs = []
        note_spec = note.get("spec")
        if note_spec is not None:
            note_spec, nom_recs = nominal_size.translate_hole_spec(
                note_spec, tol=nominal_tol, enabled=nominal_on)
        # pattern が明示されていればそれを使う(強制指定)。無ければ spec から既定書式で組み立てる
        pattern = note.get("pattern")
        if pattern is None:
            if note_spec is None:
                raise ValueError("%s: hole_note は pattern か spec のどちらかが必要" % note["id"])
            style_over = dict(hole_note_style)
            style_over.update(note.get("style", {}))
            pattern = build_hole_note_pattern(note_spec, style_over)
        note = dict(note, pattern=pattern)

        # ---- 注記の文字枠の大きさ(配置の決定に先に必要) ----
        h = dimvars_base["dimtxt"]
        lines_ = note["pattern"].split("\\P")
        w = max(_disp_len(_MTEXT_CODE_RE.sub("", l).replace("%%c", "f")) for l in lines_) * h * \
            spec["text_style"]["width_factor"]
        size_wh = (w, len(lines_) * h * 1.3)
        attachment = note.get("attachment", "bottom-left")

        # ---- 層2: 注記は対象の近くに(論点21)。auto_place=true で engine が場所を探す ----
        #   従来は計画が注記を紙面右端に固定しており、引出線が図を斜めに長く横断していた。
        auto_rep = None
        space = note.get("leader", {}).get("space", "view")
        if note.get("auto_place"):
            ac0 = note.get("anchor_check")
            if ac0:
                # 対象円が分かっている場合: 引出線の始点を円周上に置く
                #(anchor_check が「始点が円周上にあること」を独立に検証する)
                acx = to_draw(ac0["view"], ac0.get("space", "view"), ac0["center"])
                acr = float(ac0["diameter"]) / 2.0 * scale
                ac_view = ac0["view"]
            else:
                # anchor_check が無い計画でも使えるようにする: **計画が書いた引出線の始点**を
                # 対象点(半径0)として扱い、折れ点から先だけを engine が置き直す。
                # 始点は動かさないので、指し先の正しさは計画の責任のまま変わらない。
                acx = to_draw(view, space, note["leader"]["points"][0])
                acr = 0.0
                ac_view = view
            ac0 = {"view": ac_view}
            obstacles = ([tuple(view_bbox[k]) for k in view_bbox]
                         + [tuple(bx) for bx in text_boxes.values()]
                         + [TITLE_BLOCK_RECT, NOTE_ZONE_RECT])
            vb = view_bbox[ac0["view"]]
            auto_rep = auto_place_hole_note(
                acx, acr, size_wh, obstacles, FRAME_RECT,
                view_center=((vb[0] + vb[2]) / 2.0, (vb[1] + vb[3]) / 2.0))
            if auto_rep is None:
                style_warnings.append(
                    u"❗注記 %s の自動配置に失敗(対象の近傍に衝突しない場所が無い)。"
                    u"計画の text_insert を使う" % note["id"])
            else:
                pts = list(auto_rep["leader_points"])
                ins = auto_rep["insert"]
                attachment = auto_rep["attachment"]
        if auto_rep is None:
            pts = [to_draw(view, space, p) for p in note["leader"]["points"]]
            ins = to_draw(view, note.get("text_space", "view"), note["text_insert"])

        leader = msp.add_leader(pts, dimstyle=ldr_style,
                                dxfattribs={"layer": leader_layer})
        msp.add_mtext(note["pattern"], dxfattribs={
            "style": text_style,
            "char_height": dimvars_base["dimtxt"],
            "attachment_point": ATTACH.get(attachment, 7),
            "insert": (ins[0], ins[1], 0.0),
            "layer": leader_layer,
        })
        nrow = {"id": note["id"], "view": view, "pattern": note["pattern"],
                "leader_points": [[round(p[0], 4), round(p[1], 4)] for p in pts],
                "text_insert": [round(ins[0], 4), round(ins[1], 4)],
                "attachment": attachment, "auto_placed": bool(auto_rep),
                "ok": True, "errors": []}
        if "\u03c6" in note["pattern"] or "\u03a6" in note["pattern"]:
            nrow["errors"].append(u"φのUnicode文字は禁止(%%cを使うこと)")

        # ---- 呼び値翻訳の**独立検算**(呼び値表にも翻訳関数にも依存しない) ----
        #   ❗表を偽装しても(φ7.04 -> φ8 のような嘘の丸め)ここで落ちる。
        #     翻訳の正しさを保証するのは表ではなく「実測値との差」の検算である。
        if nom_recs:
            nrow["nominal"] = [{k: r[k] for k in ("field", "measured", "nominal",
                                                  "resolved", "delta_mm", "reason")}
                               for r in nom_recs]
            for r in nom_recs:
                if not r["resolved"]:
                    nominal_pending.append(
                        {"id": note["id"], "view": view, "field": r["field"],
                         "measured": r["measured"], "reason": r["reason"]})
                    continue
                d = abs(float(r["nominal"]) - float(r["measured"]))
                if d > nominal_tol + 1e-9:
                    nrow["errors"].append(
                        u"呼び値翻訳が許容窓を超えている(%s: 実測φ%.4g -> 呼びφ%.4g・"
                        u"差%.4fmm > %.4fmm)"
                        % (r["field"], r["measured"], r["nominal"], d, nominal_tol))
                elif float(r["nominal"]) not in nominal_size.NOMINAL_TABLE:
                    nrow["errors"].append(
                        u"呼び値翻訳の結果が呼び値表に無い(%s: φ%.4g)"
                        % (r["field"], r["nominal"]))

        # ---- 層2: 引出線の長距離横断(論点21) ----
        _llen = polyline_length(pts)
        nrow["leader_len_mm"] = round(_llen, 4)
        if _llen > LEADER_LEN_MAX_MM:
            style_warnings.append(
                u"❗引出線が長すぎる(論点21) %s: %.1fmm > 目安上限%.0fmm。"
                u"注記を対象フィーチャーの近くへ寄せること(auto_place=true)"
                % (note["id"], _llen, LEADER_LEN_MAX_MM))
        ac = note.get("anchor_check")
        if ac:
            c = to_draw(ac["view"], ac.get("space", "view"), ac["center"])
            r = float(ac["diameter"]) / 2.0 * scale   # anchor_check.diameter はモデル実寸
            d = abs(math.hypot(pts[0][0] - c[0], pts[0][1] - c[1]) - r)
            nrow["anchor_check"] = {"ok": d <= snap_tol, "dist_err_mm": round(d, 6)}
            if find_circle(per_view[ac["view"]], c, float(ac["diameter"]) * scale,
                           snap_tol) is None:
                nrow["errors"].append(u"anchor_check: 指定の円が実在しない")
            if d > snap_tol:
                nrow["errors"].append(u"anchor_check: 引出線始点が円周上にない(%.4fmm)" % d)
        nrow["ok"] = not nrow["errors"]
        note_rows.append(nrow)
        # ❗文字枠は attachment を考慮する(bottom-right の注記を bottom-left として
        #   扱っていた従来実装は、注記の衝突判定を左右反転した位置で見ていた)
        text_boxes[note["id"]] = [round(v, 4) for v in
                                  note_text_box(ins, size_wh, attachment)]
        nrow["leader_handle"] = leader.dxf.handle
        # 引出線は区間ごと・注記は文字枠を1つの矩形として扱う(空白を巻き込まないため)
        tbn = text_boxes[note["id"]]
        segs = [(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
                for a, b in zip(pts, pts[1:])]
        prim_boxes[note["id"]] = segs + [tuple(tbn)]
        pbs = prim_boxes[note["id"]]
        geom_boxes[note["id"]] = [round(min(p[0] for p in pbs), 4),
                                  round(min(p[1] for p in pbs), 4),
                                  round(max(p[2] for p in pbs), 4),
                                  round(max(p[3] for p in pbs), 4)]

    # --- 6) 自由注記 ------------------------------------------------------
    for n in plan.get("notes", []):
        h = float(n.get("height", 3.5))
        ins = n["insert"]
        msp.add_mtext(n["text"], dxfattribs={
            "style": text_style,
            "char_height": h,
            "attachment_point": ATTACH.get(n.get("attachment", "top-left"), 1),
            "insert": (ins[0], ins[1], 0.0),
            "layer": "0",
        })
        lines = n["text"].split("\\P")
        w = max(len(_MTEXT_CODE_RE.sub("", l)) for l in lines) * h * 0.9
        top = ins[1] if n.get("attachment", "top-left").startswith("top") else \
            ins[1] + len(lines) * h * 1.3
        text_boxes[n["id"]] = [round(ins[0], 4), round(top - len(lines) * h * 1.3, 4),
                               round(ins[0] + w, 4), round(top, 4)]

    # --- 7) レイアウト衝突チェック ------------------------------------------
    # (7-1) 文字枠どうし・文字枠とビュー(従来からの初歩的チェック。互換のため残す)
    collisions = []
    keys = list(text_boxes)
    for i in range(len(keys)):
        a = text_boxes[keys[i]]
        for k in view_bbox:
            if _rect_overlap(a, view_bbox[k]):
                collisions.append([keys[i], "view:%s" % k])
        if _rect_overlap(a, TITLE_BLOCK_RECT):
            collisions.append([keys[i], "title_block"])
        if _rect_overlap(a, NOTE_ZONE_RECT):
            collisions.append([keys[i], "note_zone"])
        if not _rect_inside(a, FRAME_RECT):
            collisions.append([keys[i], "outside_frame"])
        for j in range(i + 1, len(keys)):
            if _rect_overlap(a, text_boxes[keys[j]]):
                collisions.append([keys[i], keys[j]])

    # (7-2) ❗図枠・表題欄との衝突(2026-08-10 追加)。
    #   判定対象は**寸法の描画実体全体**(virtual_entities展開の外接矩形)であって文字枠ではない。
    #   盲検 25154-5-08 で「下側の寸法が表題欄に重なる」実害が出たのに警告が出なかったのは、
    #   (a)文字枠しか見ていない (b)図枠エンティティを見ていない、の2点が原因だった。
    frame_collisions = frame_zone_collisions(frame_boxes, prim_boxes)
    if collisions:
        warnings.append(u"レイアウト衝突(要目視確認) %d件: %s" % (len(collisions), collisions))
    if frame_collisions:
        warnings.append(
            u"❗図枠・表題欄との衝突 %d件(寸法が図枠要素に重なっている): %s"
            % (len(frame_collisions),
               [(c["id"], c["zone"]) for c in frame_collisions]))

    # 層1: 呼び値未確定(丸めずに実測のまま残した注記の径)を質問票へ誘導する
    if nominal_pending:
        style_warnings.append(
            u"❗呼び値未確定 %d件(論点8/15。丸め先が自明でないため実測のまま作図した。"
            u"解釈レポート/質問票で意図を確認すること): %s"
            % (len(nominal_pending),
               [(p["id"], p["field"], p["measured"]) for p in nominal_pending]))
    # 様式警告は**生成を止めない**(ゲート①②とは別の品質軸。読み手への配慮の層)
    warnings.extend(style_warnings)

    # --- 8) スタイル読み戻し検証 -------------------------------------------
    style_check = _check_dimstyles(doc, dimstyle_records, dimvars_base, spec)

    gate_ok = all(r["ok"] for r in gate_rows) and all(r["ok"] for r in note_rows)

    report = {
        "out_path": out_dxf_path,
        "base_dxf": base_dxf,
        "gate1": gate_rows,
        "gate1_ok": gate_ok,
        "chains": chain_reports,
        "chains_ok": all(c["ok"] for c in chain_reports),
        "dim_offsets": {k: round(v, 4) for k, v in dim_offsets.items()},
        "hole_notes": note_rows,
        "dimstyles": dimstyle_records,
        "style_check": style_check,
        "arrow_blocks_created": arrows_created,
        "frame_check": frame_summary,
        "view_bbox": {k: [round(v, 4) for v in view_bbox[k]] for k in view_bbox},
        "layout": {"text_boxes": text_boxes, "collisions": collisions,
                   "geom_boxes": geom_boxes, "frame_collisions": frame_collisions,
                   "frame_ok": not frame_collisions},
        "resolved_kinds": resolved_kinds,
        "scale": scale,
        "dimlfac": dimvars_base["dimlfac"],
        "views": list(use_views),
        "view_reserves": reserves,
        "defaults_applied": {"diameter_style": diameter_style, "hole_note": hole_note_style,
                             "nominal_translation": nominal_on,
                             "nominal_tol_mm": nominal_tol,
                             "extension_gap_avoid": ext_avoid},
        # --- 様式第3弾(上品さの3層構造)の計測値 ---
        "style_warnings": style_warnings,
        "nominal": {"pending": nominal_pending, "review_dimensions": nominal_review},
        "extension_lines": {
            "reports": [ext_reports[k] for k in sorted(ext_reports)],
            "collinear_count": sum(1 for e in ext_reports.values()
                                   if e.get("collinear_count")),
            "long_count": len(ext_long),
            "long_and_collinear_count": len(ext_long_collinear),
            "ext_len_max_mm": (max(e.get("ext_len_max_mm", 0.0)
                                   for e in ext_reports.values())
                               if ext_reports else None),
            "ext_len_median_mm": _median([v for e in ext_reports.values()
                                          for v in e.get("ext_len_mm", [])]),
            "avoided_count": sum(1 for e in ext_reports.values() if e.get("avoided")),
            "gap_mismatch_count": sum(1 for e in ext_reports.values()
                                      if e.get("gap_matches_dimexo") is False)},
        "circular_view_diameter_over": circ_over,
        "warnings": warnings,
    }

    if not gate_ok:
        bad = [r for r in gate_rows + note_rows if not r["ok"]]
        raise DimensionGateError(
            u"ゲート①不合格 %d件(DXFは保存していない):\n%s"
            % (len(bad), json.dumps(bad, ensure_ascii=False, indent=2)))

    out_dir = os.path.dirname(out_dxf_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    doc.saveas(out_dxf_path)
    return report


def _check_dimstyles(doc, records, dimvars_base, spec):
    u"""生成したDIMSTYLEの実効値がdimstyle_spec.jsonと一致するか読み戻して検証する。"""
    mismatches = []
    checked = {}
    watch = [k for k in dimvars_base if k not in ("dimpost",)]
    for did, name in records.items():
        ds = doc.dimstyles.get(name)
        eff = {}
        for k in watch:
            got = ds.dxf.get(k, None)
            want = dimvars_base[k]
            eff[k] = got
            if isinstance(want, float):
                bad = got is None or abs(float(got) - want) > 1e-9
            else:
                bad = str(got) != str(want)
            if bad:
                mismatches.append({"dim": did, "style": name, "var": k,
                                   "expected": want, "actual": got})
        eff["dimpost"] = ds.dxf.get("dimpost", "")
        eff["dimtol"] = ds.dxf.get("dimtol", 0)
        checked[did] = eff
    return {"ok": not mismatches, "mismatches": mismatches, "effective": checked}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if len(argv) < 3:
        print(__doc__)
        return 2
    plan_path, out_dxf = argv[1], argv[2]
    png = None
    if "--png" in argv:
        png = argv[argv.index("--png") + 1]
    rep = apply_plan(plan_path, out_dxf)
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    if png:
        compose_drawing.render_png(out_dxf, png)
        print("saved", png)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
