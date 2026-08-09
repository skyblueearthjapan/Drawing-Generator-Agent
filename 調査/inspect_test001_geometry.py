# -*- coding: utf-8 -*-
u"""TEST-001_ホルダー.dxf の各ビューのジオメトリを一覧する(寸法計画立案の材料)。"""
import io
import os
import sys

import ezdxf
from ezdxf.bbox import extents as bbox_extents

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.frame_extract import subtract_frame  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VIEW_BBOX = {
    "front": (141.841, 61.908, 181.841, 136.908),
    "top": (141.841, 168.500, 181.841, 243.500),
    "right": (200.000, 61.908, 275.000, 136.908),
    "iso": (196.841, 151.908, 278.159, 260.092),
}


def classify(cx, cy):
    for k, (x0, y0, x1, y1) in VIEW_BBOX.items():
        if x0 - 2 <= cx <= x1 + 2 and y0 - 2 <= cy <= y1 + 2:
            return k
    return "?"


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    path = os.path.join(ROOT, u"生成図面", u"TEST-001_ホルダー.dxf")
    doc = ezdxf.readfile(path)
    remaining, summary = subtract_frame(doc)
    print("frame summary:", summary)

    rows = {k: [] for k in list(VIEW_BBOX) + ["?"]}
    for e in remaining:
        t = e.dxftype()
        if t in ("MTEXT", "TEXT"):
            continue
        bb = bbox_extents([e], fast=True)
        if bb is None or not bb.has_data:
            continue
        cx = (bb.extmin.x + bb.extmax.x) / 2.0
        cy = (bb.extmin.y + bb.extmax.y) / 2.0
        k = classify(cx, cy)
        lt = e.dxf.linetype if e.dxf.hasattr("linetype") else "BYLAYER"
        if t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            ln = ((s.x - en.x) ** 2 + (s.y - en.y) ** 2) ** 0.5
            rows[k].append("LINE  %-9s (%9.4f,%9.4f)-(%9.4f,%9.4f) len=%8.4f"
                           % (lt, s.x, s.y, en.x, en.y, ln))
        elif t == "CIRCLE":
            c = e.dxf.center
            rows[k].append("CIRCLE%-9s c=(%9.4f,%9.4f) r=%8.4f  d=%8.4f"
                           % (lt, c.x, c.y, e.dxf.radius, e.dxf.radius * 2))
        elif t == "ARC":
            c = e.dxf.center
            rows[k].append("ARC   %-9s c=(%9.4f,%9.4f) r=%8.4f a=%.2f..%.2f"
                           % (lt, c.x, c.y, e.dxf.radius, e.dxf.start_angle, e.dxf.end_angle))
        elif t == "INSERT":
            p = e.dxf.insert
            rows[k].append("INSERT %s ins=(%.4f,%.4f) bbox=(%.3f,%.3f)-(%.3f,%.3f)"
                           % (e.dxf.name, p.x, p.y, bb.extmin.x, bb.extmin.y,
                              bb.extmax.x, bb.extmax.y))
        else:
            rows[k].append("%-6s %-9s bbox=(%.3f,%.3f)-(%.3f,%.3f)"
                           % (t, lt, bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y))

    for k in list(VIEW_BBOX) + ["?"]:
        print("\n=== %s (n=%d) bbox=%s ===" % (k, len(rows[k]), VIEW_BBOX.get(k)))
        for r in sorted(rows[k]):
            print("  " + r)


if __name__ == "__main__":
    main()
