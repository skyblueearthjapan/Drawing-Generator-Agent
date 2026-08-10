# -*- coding: utf-8 -*-
u"""教師STEP棚卸し × バケットA から検証対象候補を並べる。"""
import json, io, sys, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

d = json.load(open(os.path.join(ROOT, u"調査/教師STEP棚卸し.json"), encoding="utf-8"))
b = json.load(open(os.path.join(ROOT, u"調査/bucket_A_files.json"), encoding="utf-8"))

bset = set()
for k, v in b.items():
    for p in v:
        bset.add(os.path.basename(p))

rows = []
for m in d["matched"]:
    step = m["step"]
    parts = step.replace("/", "\\").split("\\")
    axis = parts[1]
    sp = os.path.join(ROOT, u"荏原トライ調整用", step)
    ssz = os.path.getsize(sp) if os.path.exists(sp) else -1
    for dx in m["dxf"]:
        dp = os.path.join(ROOT, u"荏原トライ調整用", u"DXF", u"部品表用DXFデータ", axis, dx)
        dsz = os.path.getsize(dp) if os.path.exists(dp) else -1
        rows.append((dx in bset, dsz, ssz, m[u"図番キー"], dx, axis, step))

rows.sort(key=lambda r: (not r[0], r[1]))
for r in rows[:40]:
    print(("A" if r[0] else "-"), "dxf=%8d" % r[1], "step=%9d" % r[2], r[3], r[4])
print("--- bucketA:", sum(1 for r in rows if r[0]), "/", len(rows))
print("--- non-bucketA smallest ---")
for r in [x for x in rows if not x[0]][:12]:
    print("-", "dxf=%8d" % r[1], "step=%9d" % r[2], r[3], r[4])
