# -*- coding: utf-8 -*-
"""
図枠/fields.json を生成する。
- 位置は18サンプル(バケツA)から実測した値をそのまま使う(基準ファイル=SAMPLES[0]上のエンティティ)
- sample_values は、より母数の多い「バケツA全80ファイル」(調査/bucket_A_files.json)から
  実際に登場した値をできるだけ多様に採取する
"""
import json
import re
import ezdxf

import sys
sys.path.insert(0, "調査")
from sample_files import sample_paths, SAMPLES

REF_PATH = sample_paths()[0]  # 25154-1-07_走行ギア.dxf


def style_info(doc, style_name):
    try:
        s = doc.styles.get(style_name)
        return {"font": s.dxf.font, "width_factor": round(s.dxf.width, 6),
                "oblique": round(s.dxf.oblique, 6)}
    except Exception:
        return {"font": None, "width_factor": None, "oblique": None}


def mtext_prefix(raw):
    """先頭の書式コード列(\Txxxx; \Wxxxx; 等)を抽出。中身({...}含む)は除く"""
    m = re.match(r"^((?:\\[A-Za-z][^;]*;)+)", raw)
    return m.group(1) if m else ""


ATTACH = {
    1: "top-left", 2: "top-center", 3: "top-right",
    4: "middle-left", 5: "middle-center", 6: "middle-right",
    7: "bottom-left", 8: "bottom-center", 9: "bottom-right",
}

doc = ezdxf.readfile(REF_PATH)
msp = doc.modelspace()


def find_mtext_near(x, y, tol=0.6):
    for e in msp:
        if e.dxftype() == "MTEXT":
            ins = e.dxf.insert
            if abs(ins.x - x) < tol and abs(ins.y - y) < tol:
                return e
    return None


def find_text_near(x, y, tol=0.6):
    for e in msp:
        if e.dxftype() == "TEXT":
            ins = e.dxf.insert
            if abs(ins.x - x) < tol and abs(ins.y - y) < tol:
                return e
    return None


def collect_wide_samples(anchor_xy, tol, max_n=6, is_mtext=True):
    """bucket_A全80ファイルからそのアンカー位置の実測値を収集(多様なサンプルを狙う)"""
    with open("調査/bucket_A_files.json", encoding="utf-8") as f:
        bucket = json.load(f)
    files = []
    for v in bucket.values():
        files.extend(v)
    vals = []
    seen = set()
    for p in files:
        try:
            d = ezdxf.readfile(p)
        except Exception:
            continue
        m = d.modelspace()
        for e in m:
            if is_mtext and e.dxftype() != "MTEXT":
                continue
            if not is_mtext and e.dxftype() != "TEXT":
                continue
            ins = e.dxf.insert
            if abs(ins.x - anchor_xy[0]) < tol and abs(ins.y - anchor_xy[1]) < tol:
                txt = e.text if is_mtext else e.dxf.text
                content = re.sub(r"^(?:\\[A-Za-z][^;]*;)+", "", txt)
                content = content.replace("{", "").replace("}", "")
                if content and content not in seen and not re.fullmatch(r"[＊\s]*", content):
                    seen.add(content)
                    vals.append(content)
                if len(vals) >= max_n:
                    return vals
    return vals


fields = []

# --- MTEXT系フィールド定義: (field名, 説明, x, y, tol) ---
mtext_defs = [
    ("品名_値", "品名(部品名)。3Dモデル名を投入する想定", 224.477293, 22.509923, 0.6),
    ("装置名_値", "装置名(プロジェクト/号機名)。本データセットでは全サンプル同一値'ラインマン200'", 224.477293, 14.877069, 0.6),
    ("図番_値", "図番(工番-軸番号-連番)。例: 25154-1-07", 273.604669, 20.497682, 0.6),
    ("尺度_値", "尺度(縮尺表記)。例: 1:2, 1:1, 1/1.5 等表記ゆれあり", 176.415271, 22.445859, 0.6),
    ("品番_番号", "右下ミニ表(締結部材表)の番号列。図番末尾-連番の形式", 328.825335, 24.376167, 0.6),
    ("品番_仕様", "右下ミニ表の仕様/規格列(ボルト規格など)", 334.181749, 24.365188, 0.6),
    ("品番_個数", "右下ミニ表の個数列", 394.778814, 24.456062, 0.6),
    ("REV_日付_2", "変更履歴(REV)テーブル2行目の日付。未使用時は'＊＊.＊＊.＊＊'のプレースホルダ", 207.705397, 14.722393, 0.6),
    ("REV_日付_3", "変更履歴(REV)テーブル3行目の日付", 207.705397, 22.58556, 0.6),
    ("製図者_氏名", "製図欄の氏名。1e-6厳密一致では全18ファイル中1枚が0.047ずれるため'ほぼ固定'扱い(要確認)", 191.878, 22.586, 0.6),
]

for name, desc, x, y, tol in mtext_defs:
    e = find_mtext_near(x, y, tol)
    if e is None:
        continue
    raw = e.text
    prefix = mtext_prefix(raw)
    content = re.sub(r"^(?:\\[A-Za-z][^;]*;)+", "", raw).replace("{", "").replace("}", "")
    samples = collect_wide_samples((x, y), tol, max_n=6, is_mtext=True)
    if content not in samples and content and not re.fullmatch(r"[＊\s]*", content):
        samples = [content] + samples
    fields.append({
        "field": name,
        "description": desc,
        "entity_type": "MTEXT",
        "anchor": [round(e.dxf.insert.x, 3), round(e.dxf.insert.y, 3)],
        "height": round(e.dxf.char_height, 4),
        "style": e.dxf.style,
        "style_resolved": style_info(doc, e.dxf.style),
        "align": ATTACH.get(e.dxf.attachment_point, str(e.dxf.attachment_point)),
        "rotation": e.dxf.rotation,
        "mtext_prefix": prefix,
        "sample_values": samples[:5],
    })

# --- 重量(値) : ラベル'重量'(220.593,11.804)に対し、品名/装置名ラベルとの相対オフセットから位置を推定 ---
# 品名ラベル(220.236,27.409)→品名値(224.477,22.510): dx=+4.241,dy=-4.899
# 装置名ラベル(220.638,20.213)→装置名値(224.477,14.877): dx=+3.839,dy=-5.336
# 重量ラベル(220.593,11.804)に同様のオフセット(dx≈+4.0,dy≈-5.1)を適用(実測値が見つからないための推定)
weight_label = find_text_near(220.593, 11.804, 0.6)
if weight_label is not None:
    lx, ly = weight_label.dxf.insert.x, weight_label.dxf.insert.y
    fields.append({
        "field": "重量_値",
        "description": ("重量(kg)。バケツA(80ファイル)実測ではDXF上に数値記入例が見つからず「未記入運用」"
                         "の可能性が高い。位置は品名/装置名ラベル→値のオフセット(dx≈+4.0,dy≈-5.1)から推定。"
                         "SW質量特性から自動算出しこの位置に新規記入する想定(要ディレクター確認)"),
        "entity_type": "MTEXT(推定/未実測)",
        "anchor": [round(lx + 4.0, 3), round(ly - 5.1, 3)],
        "height": 4.2124107142857,
        "style": "GMM008",
        "style_resolved": style_info(doc, "GMM008"),
        "align": "bottom-left",
        "rotation": 0.0,
        "mtext_prefix": "\\T1.000000;",
        "sample_values": [],
    })

# --- 連番マーク(丸囲み数字, 部品図の左上コーナー) ---
circle_texts = collect_wide_samples((16.2, 275.0), 3.5, max_n=8, is_mtext=True)
fields.append({
    "field": "連番マーク_値",
    "description": ("シート左上の丸(半径10、中心(19.324,278.844)固定)内に入る2桁の連番(図番末尾と一致)。"
                     "文字がCENTER系のためテキスト幅で挿入基準点のxが微妙に動く(13.15〜19.32付近)。"
                     "アンカーは丸の中心を基準にセンター配置とするのが安全"),
    "entity_type": "MTEXT",
    "anchor": [19.324, 275.03],
    "height": 7.0,
    "style": "GMM017系(ファイルにより異なる)",
    "style_resolved": style_info(doc, "GMM017"),
    "align": "bottom-center(推定・ファイルによりleftの例あり)",
    "rotation": 0.0,
    "mtext_prefix": "\\T1.000000;",
    "sample_values": circle_texts[:6],
})

# --- 材質+個数 ノート(自製部品) / メーカー+品名+型式+個数 ノート(購入部品) ---
mat_texts = collect_wide_samples((33.595, 280.001), 0.6, max_n=6, is_mtext=True)
fields.append({
    "field": "材質個数ノート_自製品",
    "description": ("シート左上、連番マーク右の自由記述ノート。自製部品は2行 { 材質  ○○○ }\\P{ 個数  ○ } 形式。"
                     "位置はほぼ固定(280.0前後)だが、内容行数がテンプレートにより変わるため厳密一致からは除外された。"
                     "'材質'はこの帳票では専用セルを持たず、このノート内の自由テキストとして記入される"),
    "entity_type": "MTEXT",
    "anchor": [33.595, 280.001],
    "height": 7.0,
    "style": "GMM016系(ファイルにより異なる)",
    "style_resolved": style_info(doc, "GMM016"),
    "align": "bottom-left",
    "rotation": 0.0,
    "mtext_prefix": "\\T1.000000;",
    "sample_values": mat_texts[:5],
})

purchased_texts = collect_wide_samples((33.595, 287.001), 0.6, max_n=6, is_mtext=True)
fields.append({
    "field": "材質個数ノート_購入品",
    "description": ("同じ左上ノート枠の購入品バリエーション。4行 メーカー/品名/型式/個数 形式。"
                     "自製品ノートと排他(同じ枠を使い回すため、片方が使われる)"),
    "entity_type": "MTEXT",
    "anchor": [33.595, 287.001],
    "height": 7.0,
    "style": "GMM018系(ファイルにより異なる)",
    "style_resolved": style_info(doc, "GMM018"),
    "align": "bottom-left",
    "rotation": 0.0,
    "mtext_prefix": "\\W0.428571;\\T1.000000;",
    "sample_values": purchased_texts[:4],
})

# --- 用紙サイズ (このバケツ内では固定 'Ａ３' = frame_template.dxf に共通エンティティとして含まれる) ---
fields.append({
    "field": "用紙サイズ",
    "description": ("この図枠テンプレート(バケツA: 表題欄が(220.24,27.41)に来る変種)では"
                     "'Ａ３'固定でMTEXTとして図枠内に直接含まれる(frame_template.dxf内の共通エンティティ)。"
                     "他の用紙サイズは図枠全体のスケール/レイアウトが異なる別バリアントとして存在する"
                     "(frame_analysis.md の「用紙サイズ・尺度によるバケツ分割」参照)"),
    "entity_type": "MTEXT(frame_template.dxfに固定値として同梱済み)",
    "anchor": [176.415271, 14.583],
    "height": 8.4248214285714,
    "style": "GMM006系",
    "style_resolved": style_info(doc, "GMM006"),
    "align": "bottom-center",
    "rotation": 0.0,
    "mtext_prefix": "\\T1.000000;",
    "sample_values": ["Ａ３"],
})

with open("図枠/fields.json", "w", encoding="utf-8") as f:
    json.dump({"frame_variant": "bucket_A (品名ラベル基準座標 220.24,27.41 / 用紙410x287 / 尺度Ａ３)",
               "n_fields": len(fields), "fields": fields}, f, ensure_ascii=False, indent=2)

print("wrote 図枠/fields.json  n_fields=", len(fields))
