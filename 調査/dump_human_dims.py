# -*- coding: utf-8 -*-
u"""人間図面の寸法値(実測値+文字)を一覧する。"""
import os
import sys
import io
import math

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import ezdxf  # noqa: E402

path = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(ROOT, sys.argv[1])
doc = ezdxf.readfile(path)


def measure(e):
    dt = e.dxf.dimtype & 7
    d = e.dxf
    try:
        if dt == 0:
            ang = math.radians(d.get("angle", 0.0))
            ux, uy = math.cos(ang), math.sin(ang)
            p2, p3 = d.defpoint2, d.defpoint3
            return abs((p3.x - p2.x) * ux + (p3.y - p2.y) * uy)
        if dt == 1:
            p2, p3 = d.defpoint2, d.defpoint3
            return math.hypot(p3.x - p2.x, p3.y - p2.y)
        if dt == 4:
            return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y)
        if dt == 3:
            return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y) * 2
    except Exception:
        return None
    return None


for e in doc.modelspace():
    if e.dxftype() != "DIMENSION":
        continue
    d = e.dxf
    m = measure(e)
    try:
        st = doc.dimstyles.get(d.dimstyle)
        post = st.dxf.get("dimpost", "")
    except Exception:
        post = "?"
    txt = None
    try:
        blk = doc.blocks.get(d.geometry)
        for b in blk:
            if b.dxftype() == "MTEXT":
                txt = b.text
    except Exception:
        pass
    print(u"dimtype=%-3d base=%d post=%-8r meas=%s txt=%r mid=(%.2f,%.2f)" % (
        d.dimtype, d.dimtype & 7, post, ("%.4f" % m) if m is not None else "None",
        txt, e.dxf.text_midpoint.x, e.dxf.text_midpoint.y))
