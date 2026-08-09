# -*- coding: utf-8 -*-
"""
図枠の共通エンティティ抽出(フェーズ1)
18枚(9軸フォルダ×2枚)の部品図から、全ファイルに同一座標(許容誤差1e-6)・同一タイプで
現れるエンティティ(=図枠)を機械的に特定する。

出力: 調査/common_signatures.json (共通シグネチャ一覧)
      調査/field_candidates.json (位置は共通だが内容が変わるフィールド候補)
      標準出力にサマリ
"""
import sys
import io
import json
import ezdxf
from collections import defaultdict, Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, "調査")
from sample_files import sample_paths, SAMPLES

TOL = 6  # round digits (~1e-6 tolerance after round-half)


def r(v):
    return round(float(v), TOL)


def pt(p):
    return (r(p.x), r(p.y))


def style_key(doc, style_name):
    """STYLE名はファイルごとにGMM001,GMM002...と採番され直るため、名前ではなく
    実体(フォント/幅係数/斜体角)で比較する"""
    try:
        s = doc.styles.get(style_name)
        return (s.dxf.font.lower(), round(s.dxf.width, 6), round(s.dxf.oblique, 6))
    except Exception:
        return (style_name,)


def entity_sig(e, doc):
    """位置・幾何を含む完全一致シグネチャ (テキスト内容も含む)"""
    t = e.dxftype()
    layer = e.dxf.layer
    try:
        color = e.dxf.color
    except Exception:
        color = 256
    try:
        lt = e.dxf.linetype
    except Exception:
        lt = "BYLAYER"

    if t == "LINE":
        return (t, layer, color, lt, pt(e.dxf.start), pt(e.dxf.end))
    elif t == "LWPOLYLINE":
        pts = tuple((r(p[0]), r(p[1])) for p in e.get_points())
        return (t, layer, color, lt, e.closed, pts)
    elif t == "CIRCLE":
        return (t, layer, color, lt, pt(e.dxf.center), r(e.dxf.radius))
    elif t == "ARC":
        return (t, layer, color, lt, pt(e.dxf.center), r(e.dxf.radius),
                r(e.dxf.start_angle), r(e.dxf.end_angle))
    elif t == "POINT":
        return (t, layer, color, lt, pt(e.dxf.location))
    elif t == "TEXT":
        h = r(e.dxf.height) if e.dxf.hasattr("height") else None
        return (t, layer, color, pt(e.dxf.insert), e.dxf.text, h, style_key(doc, e.dxf.style),
                r(getattr(e.dxf, "rotation", 0.0)))
    elif t == "MTEXT":
        h = r(e.dxf.char_height) if e.dxf.hasattr("char_height") else None
        return (t, layer, color, pt(e.dxf.insert), e.text, h, style_key(doc, e.dxf.style),
                r(getattr(e.dxf, "rotation", 0.0)))
    elif t == "INSERT":
        return (t, layer, color, e.dxf.name, pt(e.dxf.insert),
                r(getattr(e.dxf, "xscale", 1.0)), r(getattr(e.dxf, "yscale", 1.0)),
                r(getattr(e.dxf, "rotation", 0.0)))
    elif t == "LEADER":
        verts = tuple((r(v[0]), r(v[1])) for v in e.vertices)
        return (t, layer, color, verts)
    elif t == "DIMENSION":
        dp = e.dxf.defpoint
        return (t, layer, color, pt(dp), e.dxf.text if e.dxf.hasattr("text") else "")
    else:
        return None


def anchor_sig(e, doc):
    """内容(text)を除いた位置シグネチャ。可変フィールド候補の位置一致判定に使う"""
    t = e.dxftype()
    if t not in ("TEXT", "MTEXT"):
        return None
    layer = e.dxf.layer
    h = None
    if t == "TEXT" and e.dxf.hasattr("height"):
        h = r(e.dxf.height)
    if t == "MTEXT" and e.dxf.hasattr("char_height"):
        h = r(e.dxf.char_height)
    style = style_key(doc, e.dxf.style)
    return (t, layer, pt(e.dxf.insert), h, style)


def main():
    paths = sample_paths()
    per_file_sigs = []   # list of set(sig)
    per_file_sig2ent = []  # list of dict sig -> entity(text repr) for reporting
    per_file_anchors = []  # list of dict anchor -> text

    for p in paths:
        doc = ezdxf.readfile(p)
        msp = doc.modelspace()
        sigs = set()
        sig2info = {}
        anchors = {}
        for e in msp:
            s = entity_sig(e, doc)
            if s is not None:
                sigs.add(s)
                sig2info[s] = f"{e.dxftype()}"
            a = anchor_sig(e, doc)
            if a is not None:
                txt = e.text if e.dxftype() == "MTEXT" else e.dxf.text
                anchors.setdefault(a, []).append(txt)
        per_file_sigs.append(sigs)
        per_file_sig2ent.append(sig2info)
        per_file_anchors.append(anchors)

    # 1) 完全一致(位置+内容+タイプ)= 共通・固定 図枠エンティティ
    common = set(per_file_sigs[0])
    for s in per_file_sigs[1:]:
        common &= s
    print(f"[完全一致(固定)エンティティ] {len(common)} 個 (全{len(paths)}ファイル共通)")

    type_counts = Counter(s[0] for s in common)
    print("  タイプ別:", dict(type_counts))

    # 2) 位置は全ファイル共通だが内容(text)が変わる = 可変フィールド候補
    anchor_all = defaultdict(list)  # anchor -> list of (file_idx, text)
    for i, anchors in enumerate(per_file_anchors):
        for a, texts in anchors.items():
            for txt in texts:
                anchor_all[a].append((i, txt))

    field_candidates = []
    for a, items in anchor_all.items():
        file_idxs = set(i for i, _ in items)
        if len(file_idxs) < len(paths):
            continue  # 全ファイルに存在しないアンカーは除外
        texts = set(txt for _, txt in items)
        if len(texts) == 1:
            continue  # 内容も同じ→固定ラベル(1)で既にcommonに含まれるはず
        field_candidates.append((a, items))

    print(f"\n[可変フィールド候補(位置一致・内容相違)] {len(field_candidates)} 個")
    for a, items in field_candidates:
        samples = [t for _, t in items][:4]
        print(f"  anchor={a} samples={samples}")

    # 3) 一部ファイルにのみ現れる完全一致(閾値未満)= 部分共通、要確認用に出力
    all_sigs = set()
    for s in per_file_sigs:
        all_sigs |= s
    freq = Counter()
    for s in per_file_sigs:
        for sig in s:
            freq[sig] += 1
    partial = {sig: c for sig, c in freq.items() if sig not in common and c >= len(paths) - 2 and c > 1}
    print(f"\n[準共通(N-2以上で一致・不一致調査用)] {len(partial)} 個")

    # 保存
    def sig_to_jsonable(s):
        return json.dumps(s, ensure_ascii=False, default=str)

    out = {
        "n_files": len(paths),
        "files": SAMPLES,
        "common_count": len(common),
        "common_type_counts": dict(type_counts),
        "common_signatures": [list(map(str, s)) for s in common],
    }
    with open("調査/common_signatures.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    out2 = {
        "field_candidates": [
            {"anchor": [str(x) for x in a], "samples": [{"file": SAMPLES[i], "text": t} for i, t in items]}
            for a, items in field_candidates
        ]
    }
    with open("調査/field_candidates.json", "w", encoding="utf-8") as f:
        json.dump(out2, f, ensure_ascii=False, indent=2)

    print("\n保存: 調査/common_signatures.json, 調査/field_candidates.json")


if __name__ == "__main__":
    main()
