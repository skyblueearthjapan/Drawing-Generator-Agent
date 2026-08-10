# -*- coding: utf-8 -*-
u"""DXFのビュークラスタを一覧表示する(人間図面/SW投影DXFの両方)。

    python 調査/show_views.py <dxf> [--frame] [--gap 12]
"""
import os
import sys
import io
import argparse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ezdxf  # noqa: E402
import frame_extract  # noqa: E402
import geom_lib as gl  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("dxf")
ap.add_argument("--frame", action="store_true", help=u"図枠を差し引く(人間図面用)")
ap.add_argument("--gap", type=float, default=12.0)
args = ap.parse_args()

path = args.dxf if os.path.isabs(args.dxf) else os.path.join(ROOT, args.dxf)
doc = ezdxf.readfile(path)
ents = None
if args.frame:
    ents, summary = frame_extract.subtract_frame(
        doc, template_path=os.path.join(ROOT, u"図枠", u"frame_template.dxf"))
    print(u"subtract_frame:", summary)

pairs = gl.collect(doc, ents)
from collections import Counter
print(u"flattened:", Counter((e.dxftype(), k) for e, k in pairs).most_common())

clusters = gl.cluster_views(pairs, gap=args.gap)
print(u"clusters: %d" % len(clusters))
for i, c in enumerate(clusters):
    circ = gl.circles_of(c["entities"])
    rs = sorted(set(round(r, 4) for _, _, r, _ in circ))
    print(u"[%d] bbox=(%.3f,%.3f)-(%.3f,%.3f) size=%.3fx%.3f  n=%d" % (
        i, c["bbox"][0], c["bbox"][1], c["bbox"][2], c["bbox"][3],
        c["size"][0], c["size"][1], c["n"]))
    print(u"    types:", Counter((e.dxftype(), k) for e, k in c["entities"]).most_common())
    if circ:
        print(u"    円 d=", [round(r * 2, 4) for r in rs])
        for cx, cy, r, k in sorted(circ, key=lambda z: -z[2]):
            print(u"      c=(%.3f,%.3f) d=%.4f %s" % (cx, cy, r * 2, k))
