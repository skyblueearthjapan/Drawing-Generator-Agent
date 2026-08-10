# -*- coding: utf-8 -*-
u"""指定クラスタの線分を全部吐く。"""
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
ap.add_argument("idx", type=int)
ap.add_argument("--frame", action="store_true")
ap.add_argument("--gap", type=float, default=12.0)
args = ap.parse_args()

path = args.dxf if os.path.isabs(args.dxf) else os.path.join(ROOT, args.dxf)
doc = ezdxf.readfile(path)
ents = None
if args.frame:
    ents, _ = frame_extract.subtract_frame(
        doc, template_path=os.path.join(ROOT, u"図枠", u"frame_template.dxf"))
pairs = gl.collect(doc, ents)
clusters = gl.cluster_views(pairs, gap=args.gap)
c = clusters[args.idx]
print(u"bbox=%r size=%r" % ([round(v, 3) for v in c["bbox"]], [round(v, 3) for v in c["size"]]))
for e, k in c["entities"]:
    t = e.dxftype()
    if t == "LINE":
        print(u"  LINE %-7s (%9.3f,%9.3f)-(%9.3f,%9.3f) len=%.3f" % (
            k, e.dxf.start.x, e.dxf.start.y, e.dxf.end.x, e.dxf.end.y,
            ((e.dxf.end.x - e.dxf.start.x) ** 2 + (e.dxf.end.y - e.dxf.start.y) ** 2) ** 0.5))
    elif t == "ARC":
        print(u"  ARC  %-7s c=(%9.3f,%9.3f) r=%.4f %.1f..%.1f" % (
            k, e.dxf.center.x, e.dxf.center.y, e.dxf.radius,
            e.dxf.start_angle, e.dxf.end_angle))
    elif t == "CIRCLE":
        print(u"  CIRC %-7s c=(%9.3f,%9.3f) r=%.4f" % (
            k, e.dxf.center.x, e.dxf.center.y, e.dxf.radius))
    else:
        print(u"  %-5s %-7s" % (t, k), [round(v, 3) for v in gl._pts_of(e)[0]] if gl._pts_of(e) else "")
