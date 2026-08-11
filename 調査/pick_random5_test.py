# -*- coding: utf-8 -*-
"""システムテスト用: 過去未使用の製作部品からランダム5点選定(シード固定で再現可能)"""
import sys, os, json, glob, re, random
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent"
inv = json.load(open(os.path.join(BASE, "調査", "教師STEP棚卸し.json"), encoding="utf-8"))

used = set()
for d in ("phase4_batch", "phase4_batch2"):
    p = os.path.join(BASE, "調査", d)
    if os.path.isdir(p):
        for sub in os.listdir(p):
            m = re.match(r"^(\d+-\d+)", sub)
            if m:
                used.add(m.group(1))
used |= {"1-18", "1-09", "3-05", "1-25", "1-12"}
for d in glob.glob(os.path.join(BASE, "data", "依頼箱", "BLIND*")):
    m = re.search(r"BLIND2?-25154-(\d+-\d+)", d)
    if m:
        used.add(m.group(1))
# blind2ターゲット(未処理含む)も除外
try:
    for t in json.load(open(os.path.join(BASE, "調査", "blind2_targets.json"), encoding="utf-8")):
        used.add(t["図番キー"])
except Exception:
    pass

NG = ("ギア", "ｷﾞｱ", "ギヤ", "ラック", "ﾗｯｸ", "ねじ", "ネジ", "ボールネジ", "ウォーム", "ｳｫｰﾑ", "スプライン")
cands = []
for m in inv["matched"]:
    key = m["図番キー"]
    if key in used:
        continue
    name = " ".join(m["dxf"]) + m["step"]
    if any(k in name for k in NG):
        continue
    cands.append({"図番キー": key, "step": m["step"], "dxf": m["dxf"][0]})

random.seed(20260811)
picked = random.sample(cands, 5)
print(f"未使用候補: {len(cands)}点 → ランダム5点(seed=20260811):")
for p in picked:
    print(f"  {p['図番キー']:8s} {p['step']}")
json.dump(picked, open(os.path.join(BASE, "調査", "random5_targets.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("saved: 調査/random5_targets.json")
