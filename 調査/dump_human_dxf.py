# -*- coding: utf-8 -*-
u"""人間の部品図DXFを読み取り専用でダンプ(図枠差し引き後の中身を把握する)。"""
import os
import sys
import io
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import ezdxf  # noqa: E402
import frame_extract  # noqa: E402

path = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(ROOT, sys.argv[1])
doc = ezdxf.readfile(path)
msp = doc.modelspace()
print(u"file:", os.path.basename(path))
print(u"dxfversion=%s enc=%s ents=%d" % (doc.dxfversion, doc.encoding, len(msp)))
print(u"types:", Counter(e.dxftype() for e in msp).most_common())

rem, summary = frame_extract.subtract_frame(
    doc, template_path=os.path.join(ROOT, u"図枠", u"frame_template.dxf"))
print(u"subtract_frame:", summary)
print(u"remaining types:", Counter(e.dxftype() for e in rem).most_common())


def lt(e):
    l = e.dxf.linetype if e.dxf.hasattr("linetype") else "BYLAYER"
    if l == "BYLAYER":
        try:
            l = doc.layers.get(e.dxf.layer).dxf.linetype
        except Exception:
            l = "CONTINUOUS"
    return l


print(u"\n-- linetype x type (remaining) --")
print(Counter((e.dxftype(), lt(e)) for e in rem).most_common())

print(u"\n-- CIRCLE (remaining) --")
for e in rem:
    if e.dxftype() == "CIRCLE":
        print(u"  c=(%.3f,%.3f) r=%.4f d=%.4f lt=%s" % (
            e.dxf.center.x, e.dxf.center.y, e.dxf.radius, e.dxf.radius * 2, lt(e)))

print(u"\n-- ARC (remaining) --")
for e in rem:
    if e.dxftype() == "ARC":
        print(u"  c=(%.3f,%.3f) r=%.4f  %.1f..%.1f lt=%s" % (
            e.dxf.center.x, e.dxf.center.y, e.dxf.radius,
            e.dxf.start_angle, e.dxf.end_angle, lt(e)))

print(u"\n-- INSERT (remaining) --")
print(Counter(e.dxf.name for e in rem if e.dxftype() == "INSERT").most_common())

print(u"\n-- TEXT/MTEXT (remaining) --")
for e in rem:
    if e.dxftype() in ("TEXT", "MTEXT"):
        t = e.dxf.text if e.dxftype() == "TEXT" else e.text
        p = e.dxf.insert
        print(u"  (%8.3f,%8.3f) %r" % (p.x, p.y, t))

print(u"\n-- DIMENSION (remaining) --")
for e in rem:
    if e.dxftype() == "DIMENSION":
        d = e.dxf
        print(u"  dimtype=%d text=%r mid=(%.2f,%.2f) dp=(%.2f,%.2f)" % (
            d.dimtype, d.get("text", None), e.dxf.text_midpoint.x, e.dxf.text_midpoint.y,
            d.defpoint.x, d.defpoint.y))
