# -*- coding: utf-8 -*-
u"""バケットA全ファイルで subtract_frame の一致数を レイヤ名あり/なし で比較する。"""
import os
import sys
import io
import json
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import ezdxf  # noqa: E402
import frame_extract as fe  # noqa: E402

TPL = os.path.join(ROOT, u"図枠", u"frame_template.dxf")
b = json.load(io.open(os.path.join(ROOT, u"調査", u"bucket_A_files.json"), encoding="utf-8"))

_, sig_on = fe.load_frame_signatures(TPL, ignore_layer=False)
_, sig_off = fe.load_frame_signatures(TPL, ignore_layer=True)
print(u"テンプレ署名数: レイヤ含む=%d / レイヤ無視=%d" % (len(sig_on), len(sig_off)))

rows = []
layers = Counter()
for axis, files in b.items():
    for rel in files:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        doc = ezdxf.readfile(p)
        _, s1 = fe.subtract_frame(doc, frame_signatures=sig_on, ignore_layer=False)
        _, s2 = fe.subtract_frame(doc, frame_signatures=sig_off, ignore_layer=True)
        for e in doc.modelspace():
            if e.dxftype() == "LINE":
                layers[e.dxf.layer] += 1
        rows.append((os.path.basename(p), s1["frame_matched"], s2["frame_matched"], s1["total"]))

rows.sort(key=lambda r: r[1])
bad_before = [r for r in rows if r[1] < 100]
bad_after = [r for r in rows if r[2] < 100]
print(u"対象ファイル数: %d" % len(rows))
print(u"図枠一致<100件だったファイル: レイヤ含む=%d → レイヤ無視=%d" % (len(bad_before), len(bad_after)))
print(u"一致数の平均: レイヤ含む=%.1f → レイヤ無視=%.1f" % (
    sum(r[1] for r in rows) / len(rows), sum(r[2] for r in rows) / len(rows)))
print(u"\n-- レイヤ含むと失敗していたファイル(上位15) --")
for r in rows[:15]:
    print(u"  %-52s %3d → %3d (総数%3d)" % (r[0], r[1], r[2], r[3]))
print(u"\n-- LINE のレイヤ名分布 --")
print(layers.most_common(12))
if bad_after:
    print(u"\n-- レイヤ無視でもまだ<100のファイル --")
    for r in bad_after:
        print(u"  %-52s %3d (総数%3d)" % (r[0], r[2], r[3]))
