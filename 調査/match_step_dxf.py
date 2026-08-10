# -*- coding: utf-8 -*-
"""教師STEP棚卸し: STEPファイルと部品図DXFを図番で突き合わせて仕分ける。

STEP名の例: 1-03.STEP(製作部品) / SHS30LR388020 Track.STEP(購入品)
DXF名の例:  25154-1-03_ジャッキプレート.dxf(工番25154-軸-連番_品名)
対応規則:   STEP "<軸>-<連番>" ↔ DXF "25154-<軸>-<連番>_*"
"""
import sys, io, os, re, json, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent\荏原トライ調整用"
STEP_ROOT = os.path.join(BASE, "教師STEP")
DXF_ROOT = os.path.join(BASE, "DXF", "部品表用DXFデータ")

# DXF側: 図番 → ファイルパス
dxf_map = {}
for p in glob.glob(os.path.join(DXF_ROOT, "**", "*.dxf"), recursive=True):
    name = os.path.basename(p)
    m = re.match(r"25154-([A-Za-z0-9]+)-([A-Za-z0-9.]+?)_", name)
    if m:
        dxf_map.setdefault(f"{m.group(1)}-{m.group(2)}", []).append(p)

steps = glob.glob(os.path.join(STEP_ROOT, "**", "*.STEP"), recursive=True)
matched, unmatched = [], []
for p in steps:
    base = os.path.splitext(os.path.basename(p))[0]
    # "1-03" / "1-106_HFSFIN5-..." 形式から軸-連番を取る
    m = re.match(r"^([0-9]+)-([0-9]+)", base)
    key = f"{m.group(1)}-{m.group(2)}" if m else None
    # 連番の前ゼロゆれ(03 vs 3)も試す
    hits = []
    if key:
        cands = {key, f"{m.group(1)}-{m.group(2).lstrip('0') or '0'}", f"{m.group(1)}-{m.group(2).zfill(2)}"}
        for k in cands:
            if k in dxf_map:
                hits = dxf_map[k]
                key = k
                break
    rel = p.replace(BASE + os.sep, "")
    if hits:
        matched.append({"step": rel, "図番キー": key, "dxf": [os.path.basename(h) for h in hits]})
    else:
        unmatched.append(rel)

print(f"STEP総数: {len(steps)}")
print(f"部品図とマッチ(製作部品): {len(matched)}")
print(f"マッチなし(購入品/アセンブリ/その他): {len(unmatched)}")
print("\n--- マッチなし一覧 ---")
for u in sorted(unmatched):
    print(" ", u)

out = {"matched": matched, "unmatched": sorted(unmatched),
       "counts": {"steps": len(steps), "matched": len(matched), "unmatched": len(unmatched)}}
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "教師STEP棚卸し.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("\nsaved: 調査/教師STEP棚卸し.json")
