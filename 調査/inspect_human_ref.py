# -*- coding: utf-8 -*-
u"""人間の正解図面 15015-P3-012_013.dxf を読み取り専用でダンプする。

図枠バケットが違うため frame_extract は使わず、DIMENSION/LEADER/MTEXT/TEXT/幾何を
そのまま座標付きで一覧し、ホルダー(013)部分の寸法群を人手で読み取る材料にする。
"""
import io
import math
import os
import sys
from collections import Counter

import ezdxf
from ezdxf.bbox import extents as bbox_extents

REF = (u"C:\\Users\\imaizumi.LINEWORKS-NET\\Documents\\3D CAD Operator Agent\\"
       u"DXFデータ 部品表用\\POS(回転)\\15015-P3-012_013.dxf")


def dim_measure(e):
    u"""DIMENSIONの実測値をdefpointから計算(rotated linear/radius/diameter/angular)。"""
    dt = e.dxf.dimtype & 7
    d = e.dxf
    try:
        if dt in (0, 1):
            p2 = d.defpoint2
            p3 = d.defpoint3
            ang = math.radians(d.get("angle", 0.0)) if dt == 0 else None
            if dt == 0:
                ux, uy = math.cos(ang), math.sin(ang)
                return abs((p3.x - p2.x) * ux + (p3.y - p2.y) * uy)
            return math.hypot(p3.x - p2.x, p3.y - p2.y)
        if dt == 4:
            return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y)
        if dt == 3:
            return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y) * 2
    except Exception:
        return None
    return None


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    doc = ezdxf.readfile(REF)
    msp = doc.modelspace()
    print("dxfversion=%s encoding=%s entities=%d" % (doc.dxfversion, doc.encoding, len(msp)))
    print("types:", Counter(e.dxftype() for e in msp).most_common())

    print("\n===== DIMENSIONS =====")
    for e in msp:
        if e.dxftype() != "DIMENSION":
            continue
        d = e.dxf
        dt = d.dimtype
        base = dt & 7
        st = d.dimstyle
        try:
            style = doc.dimstyles.get(st)
            dimpost = style.dxf.get("dimpost", "")
            dimtol = style.dxf.get("dimtol", 0)
            dimtp = style.dxf.get("dimtp", 0.0)
            dimtm = style.dxf.get("dimtm", 0.0)
            dimtxt = style.dxf.get("dimtxt", 0.0)
        except Exception:
            dimpost, dimtol, dimtp, dimtm, dimtxt = "?", "?", "?", "?", "?"
        m = dim_measure(e)
        print("dimtype=%-3d base=%d style=%-8s text=%-16r post=%-10r tol=%s(+%s/-%s) "
              "meas=%s dp=(%.2f,%.2f) dp2=(%.2f,%.2f) dp3=(%.2f,%.2f) dp4=(%.2f,%.2f) "
              "ang=%.2f txtpos=(%.2f,%.2f)"
              % (dt, base, st, d.get("text", None), dimpost, dimtol, dimtp, dimtm,
                 ("%.4f" % m) if m is not None else "None",
                 d.defpoint.x, d.defpoint.y,
                 d.get("defpoint2", d.defpoint).x, d.get("defpoint2", d.defpoint).y,
                 d.get("defpoint3", d.defpoint).x, d.get("defpoint3", d.defpoint).y,
                 d.get("defpoint4", d.defpoint).x, d.get("defpoint4", d.defpoint).y,
                 d.get("angle", 0.0), d.text_midpoint.x, d.text_midpoint.y))

    print("\n===== MTEXT / TEXT =====")
    rows = []
    for e in msp:
        if e.dxftype() == "MTEXT":
            p = e.dxf.insert
            rows.append((p.x, p.y, "MTEXT", e.text))
        elif e.dxftype() == "TEXT":
            p = e.dxf.insert
            rows.append((p.x, p.y, "TEXT", e.dxf.text))
    for x, y, t, s in sorted(rows, key=lambda r: (-r[1], r[0])):
        print("%-5s (%8.2f,%8.2f) %r" % (t, x, y, s))

    print("\n===== LEADERS =====")
    for e in msp:
        if e.dxftype() != "LEADER":
            continue
        pts = [(p[0], p[1]) for p in e.vertices]
        print("LEADER style=%s pts=%s" % (e.dxf.dimstyle, ["(%.2f,%.2f)" % p for p in pts]))

    print("\n===== CIRCLES =====")
    for e in msp:
        if e.dxftype() != "CIRCLE":
            continue
        c = e.dxf.center
        print("CIRCLE %-10s c=(%8.2f,%8.2f) r=%8.3f d=%8.3f"
              % (e.dxf.linetype, c.x, c.y, e.dxf.radius, e.dxf.radius * 2))

    print("\n===== ARCS =====")
    for e in msp:
        if e.dxftype() != "ARC":
            continue
        c = e.dxf.center
        print("ARC %-10s c=(%8.2f,%8.2f) r=%8.3f %.1f..%.1f"
              % (e.dxf.linetype, c.x, c.y, e.dxf.radius, e.dxf.start_angle, e.dxf.end_angle))

    print("\n===== INSERTS =====")
    for e in msp:
        if e.dxftype() != "INSERT":
            continue
        bb = bbox_extents([e], fast=True)
        p = e.dxf.insert
        print("INSERT %-28s ins=(%.2f,%.2f) bbox=%s"
              % (e.dxf.name, p.x, p.y,
                 ("(%.2f,%.2f)-(%.2f,%.2f)" % (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y))
                 if bb and bb.has_data else "?"))

    print("\n===== LINES (sorted) =====")
    lines = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        s, en = e.dxf.start, e.dxf.end
        lines.append((s.x, s.y, en.x, en.y, math.hypot(en.x - s.x, en.y - s.y), e.dxf.linetype))
    for s in sorted(lines, key=lambda r: (round(r[0], 2), round(r[1], 2))):
        print("LINE %-10s (%8.2f,%8.2f)-(%8.2f,%8.2f) len=%8.3f" % (s[5], s[0], s[1], s[2], s[3], s[4]))


if __name__ == "__main__":
    main()
