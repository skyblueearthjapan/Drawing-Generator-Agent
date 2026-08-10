# -*- coding: utf-8 -*-
"""新規盲検2の対象選定: 過去に一切使っていない製作部品から10点(ディレクター実行)"""
import sys, os, json, glob, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent"
inv = json.load(open(os.path.join(BASE, "調査", "教師STEP棚卸し.json"), encoding="utf-8"))

used = set()
# フェーズ4バッチ1・2(図番フォルダ名)
for d in ("phase4_batch", "phase4_batch2"):
    p = os.path.join(BASE, "調査", d)
    if os.path.isdir(p):
        for sub in os.listdir(p):
            m = re.match(r"^(\d+-\d+)", sub)
            if m and os.path.isdir(os.path.join(p, sub)):
                used.add(m.group(1))
# バッチ1の _v2 サブフォルダなども拾う
for sub in glob.glob(os.path.join(BASE, "調査", "phase4_batch", "_v2", "*")):
    m = re.match(r"^(\d+-\d+)", os.path.basename(sub))
    if m:
        used.add(m.group(1))
# STEP実証5点
used |= {"1-18", "1-09", "3-05", "1-25", "1-12"}
# 盲検1(依頼箱)
for d in glob.glob(os.path.join(BASE, "data", "依頼箱", "BLIND-25154-*")):
    m = re.search(r"BLIND-25154-(\d+-\d+)", d)
    if m:
        used.add(m.group(1))

# ギア・ねじ・ラック類の除外(名前ベース。半角カナ正規化込み)
NG = ("ギア", "ｷﾞｱ", "ギヤ", "ラック", "ﾗｯｸ", "ねじ", "ネジ", "ボールネジ", "ウォーム", "ｳｫｰﾑ", "スプライン")

cands = []
for m in inv["matched"]:
    key = m["図番キー"]
    if key in used:
        continue
    name = " ".join(m["dxf"])
    if any(k in name or k in m["step"] for k in NG):
        continue
    cands.append((key, m["step"], m["dxf"][0]))

# 軸バランス+複雑度層化(図面サイズを代理指標に小/中/大を混ぜる)で10点選定
from collections import defaultdict
DXF_ROOT = os.path.join(BASE, "荏原トライ調整用", "DXF", "部品表用DXFデータ")
def dxf_size(dxf_name):
    hits = glob.glob(os.path.join(DXF_ROOT, "**", dxf_name), recursive=True)
    return os.path.getsize(hits[0]) if hits else 0

by_axis = defaultdict(list)
for key, step, dxf in cands:
    by_axis[key.split("-")[0]].append((key, step, dxf, dxf_size(dxf)))
for a in by_axis:
    by_axis[a].sort(key=lambda t: t[3])  # サイズ昇順

picked = []
axes = sorted(by_axis)
# 1周目: 各軸の中央値(中複雑度)を1点ずつ
for a in axes:
    lst = by_axis[a]
    if lst:
        picked.append(lst.pop(len(lst) // 2))
# 2周目以降: 小さい方と大きい方を交互に(分布を広げる)
i = 0
while len(picked) < 10 and any(by_axis[a] for a in axes):
    a = axes[i % len(axes)]
    lst = by_axis[a]
    if lst:
        picked.append(lst.pop(0) if (len(picked) % 2 == 0) else lst.pop(-1))
    i += 1
picked = [(k, s, d) for k, s, d, _ in picked]

print(f"未使用候補: {len(cands)}点 / 選定: {len(picked)}点")
for key, step, dxf in picked:
    print(f"  {key:8s} {step}")
out = [{"図番キー": k, "step": s, "dxf": d} for k, s, d in picked]
json.dump(out, open(os.path.join(BASE, "調査", "blind2_targets.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("saved: 調査/blind2_targets.json")
