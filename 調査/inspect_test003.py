# -*- coding: utf-8 -*-
u"""TEST-003(端子棒)の材料調査。

  A. 人間の正解図面 15015-P3-012_013.dxf の **012(端子棒)側**(x<210)の
     DIMENSION / MTEXT / LEADER / 幾何を読み取り専用でダンプ
  B. phase2投影DXF(調査/phase2_out_15015-P3-012_端子棒.dxf)のビュー別実ジオメトリを
     モデル座標へ逆変換して特徴を列挙(段付き軸の各段の径・長さ、面取り、二面取り幅)

実行: python 調査/inspect_test003.py
"""
import io
import json
import math
import os
import sys
from collections import Counter

import ezdxf
from ezdxf.bbox import extents as bbox_extents

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import compose_drawing  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUMAN = (u"C:\\Users\\imaizumi.LINEWORKS-NET\\Documents\\3D CAD Operator Agent\\"
         u"DXFデータ 部品表用\\POS(回転)\\15015-P3-012_013.dxf")
X_MAX_012 = 210.0

VIEWS_DXF = os.path.join(ROOT, u"調査", u"phase2_out_15015-P3-012_端子棒.dxf")
META = os.path.join(ROOT, u"調査", u"phase2_meta_15015-P3-012_端子棒.json")


def dim_measure(e):
    dt = e.dxf.dimtype & 7
    d = e.dxf
    try:
        if dt == 0:
            ang = math.radians(d.get("angle", 0.0))
            return abs((d.defpoint3.x - d.defpoint2.x) * math.cos(ang)
                       + (d.defpoint3.y - d.defpoint2.y) * math.sin(ang))
        if dt == 1:
            return math.hypot(d.defpoint3.x - d.defpoint2.x, d.defpoint3.y - d.defpoint2.y)
        if dt == 4:
            return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y)
        if dt == 3:
            return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y) * 2
    except Exception:
        return None
    return None


def dump_human():
    print(u"===== A. 人間図面 012(端子棒)側 x<%.0f =====" % X_MAX_012)
    doc = ezdxf.readfile(HUMAN)
    msp = doc.modelspace()

    print(u"\n--- DIMENSIONS ---")
    for e in msp:
        if e.dxftype() != "DIMENSION":
            continue
        if e.dxf.defpoint.x >= X_MAX_012:
            continue
        d = e.dxf
        st = doc.dimstyles.get(d.dimstyle)
        print(u"dimtype=%-3d base=%d style=%-8s text=%-20r post=%-10r "
              u"dimtol=%s dimtp=%s dimtm=%s dimtdec=%s dimtfac=%s meas=%s "
              u"ang=%.1f dp=(%.2f,%.2f) dp2=(%.2f,%.2f) dp3=(%.2f,%.2f) dp4=(%.2f,%.2f) "
              u"txt=(%.2f,%.2f)"
              % (d.dimtype, d.dimtype & 7, d.dimstyle, d.get("text", None),
                 st.dxf.get("dimpost", ""), st.dxf.get("dimtol", 0), st.dxf.get("dimtp", 0.0),
                 st.dxf.get("dimtm", 0.0), st.dxf.get("dimtdec", None),
                 st.dxf.get("dimtfac", None),
                 ("%.4f" % dim_measure(e)) if dim_measure(e) is not None else "None",
                 d.get("angle", 0.0),
                 d.defpoint.x, d.defpoint.y,
                 d.get("defpoint2", d.defpoint).x, d.get("defpoint2", d.defpoint).y,
                 d.get("defpoint3", d.defpoint).x, d.get("defpoint3", d.defpoint).y,
                 d.get("defpoint4", d.defpoint).x, d.get("defpoint4", d.defpoint).y,
                 d.text_midpoint.x, d.text_midpoint.y))
        # 寸法ブロック内のMTEXT(実描画文字)
        g = d.get("geometry", None)
        if g and g in doc.blocks:
            for b in doc.blocks.get(g):
                if b.dxftype() in ("MTEXT", "TEXT"):
                    t = b.text if b.dxftype() == "MTEXT" else b.dxf.text
                    print(u"        draw_text=%r" % t)

    print(u"\n--- MTEXT/TEXT (x<%.0f) ---" % X_MAX_012)
    rows = []
    for e in msp:
        if e.dxftype() == "MTEXT":
            p = e.dxf.insert
            if p.x < X_MAX_012:
                rows.append((p.x, p.y, "MTEXT", e.text))
        elif e.dxftype() == "TEXT":
            p = e.dxf.insert
            if p.x < X_MAX_012:
                rows.append((p.x, p.y, "TEXT", e.dxf.text))
    for x, y, t, s in sorted(rows, key=lambda r: (-r[1], r[0])):
        print(u"%-5s (%8.2f,%8.2f) %r" % (t, x, y, s))

    print(u"\n--- LEADERS (x<%.0f) ---" % X_MAX_012)
    for e in msp:
        if e.dxftype() != "LEADER":
            continue
        pts = [(p[0], p[1]) for p in e.vertices]
        if pts and pts[0][0] < X_MAX_012:
            print(u"LEADER style=%s pts=%s" % (e.dxf.dimstyle, ["(%.2f,%.2f)" % p for p in pts]))

    print(u"\n--- CIRCLES (x<%.0f) ---" % X_MAX_012)
    for e in msp:
        if e.dxftype() != "CIRCLE":
            continue
        c = e.dxf.center
        if c.x < X_MAX_012:
            print(u"CIRCLE %-10s c=(%8.2f,%8.2f) r=%8.3f d=%8.3f"
                  % (e.dxf.linetype, c.x, c.y, e.dxf.radius, e.dxf.radius * 2))

    print(u"\n--- ARCS (x<%.0f) ---" % X_MAX_012)
    for e in msp:
        if e.dxftype() != "ARC":
            continue
        c = e.dxf.center
        if c.x < X_MAX_012:
            print(u"ARC %-10s c=(%8.2f,%8.2f) r=%8.3f %.1f..%.1f"
                  % (e.dxf.linetype, c.x, c.y, e.dxf.radius, e.dxf.start_angle, e.dxf.end_angle))

    print(u"\n--- LINES (x<%.0f, len>=1) ---" % X_MAX_012)
    lines = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        s, en = e.dxf.start, e.dxf.end
        if max(s.x, en.x) >= X_MAX_012:
            continue
        L = math.hypot(en.x - s.x, en.y - s.y)
        lines.append((s.x, s.y, en.x, en.y, L, e.dxf.linetype))
    for s in sorted(lines, key=lambda r: (round(r[0], 2), round(r[1], 2))):
        print(u"LINE %-10s (%8.2f,%8.2f)-(%8.2f,%8.2f) len=%8.3f"
              % (s[5], s[0], s[1], s[2], s[3], s[4]))


def dump_views():
    print(u"\n\n===== B. phase2投影DXF ビュー別実ジオメトリ(モデル座標へ逆変換) =====")
    with io.open(META, encoding="utf-8") as f:
        meta = json.load(f)
    doc = ezdxf.readfile(VIEWS_DXF)
    msp = doc.modelspace()
    outlines = {k: meta["views"][k]["outline_mm"] for k in compose_drawing.VIEW_KEYS}

    per = {k: [] for k in outlines}
    for e in msp:
        if e.dxftype() in ("MTEXT", "TEXT", "DIMENSION", "POINT"):
            continue
        bb = bbox_extents([e], fast=True)
        if bb is None or not bb.has_data:
            continue
        cx = (bb.extmin.x + bb.extmax.x) / 2.0
        cy = (bb.extmin.y + bb.extmax.y) / 2.0
        for k, o in outlines.items():
            if o[0] <= cx <= o[2] and o[1] <= cy <= o[3]:
                per[k].append(e)
                break

    for k in ("front", "top", "right"):
        arr = meta["views"][k]["model_to_view"]
        r = arr[0:9]
        tx, ty, s = arr[9], arr[10], arr[12]
        print(u"\n--- view=%s (%s) 要素数=%d ---" % (k, meta["views"][k]["view_name"], len(per[k])))
        print(u"    model_to_view r=%s t=(%.6f,%.6f) s=%.3f" % ([round(v, 4) for v in r], tx, ty, s))
        print(u"    types: %s" % Counter(e.dxftype() for e in per[k]).most_common())
        # sheet -> どのモデル軸に対応するか判定用に、逆行列で sheet -> model 近似は難しいので
        # sheet座標のまま + 原点(モデル0,0,0)のsheet位置を基準に相対値を出す
        ox = s * 0.0 + tx * 1000.0
        oy = s * 0.0 + ty * 1000.0
        print(u"    モデル原点のsheet座標=(%.4f, %.4f)" % (ox, oy))
        rows = []
        for e in per[k]:
            t = e.dxftype()
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                rows.append((t, e.dxf.linetype,
                             (a.x - ox, a.y - oy), (b.x - ox, b.y - oy),
                             math.hypot(b.x - a.x, b.y - a.y)))
            elif t in ("CIRCLE", "ARC"):
                c = e.dxf.center
                rows.append((t, e.dxf.linetype, (c.x - ox, c.y - oy),
                             ("r=%.4f d=%.4f" % (e.dxf.radius, e.dxf.radius * 2)),
                             (e.dxf.start_angle, e.dxf.end_angle) if t == "ARC" else None))
            elif t == "INSERT":
                p = e.dxf.insert
                rows.append((t, e.dxf.name, (p.x - ox, p.y - oy), None, None))
            else:
                bb = bbox_extents([e], fast=True)
                rows.append((t, e.dxf.get("linetype", ""),
                             (bb.extmin.x - ox, bb.extmin.y - oy),
                             (bb.extmax.x - ox, bb.extmax.y - oy), None))
        for row in sorted(rows, key=lambda r: (r[0], round(r[2][0], 3), round(r[2][1], 3))):
            if row[0] == "LINE":
                print(u"    LINE %-10s (%9.4f,%9.4f)-(%9.4f,%9.4f) len=%9.4f"
                      % (row[1], row[2][0], row[2][1], row[3][0], row[3][1], row[4]))
            elif row[0] in ("CIRCLE", "ARC"):
                print(u"    %-6s %-10s c=(%9.4f,%9.4f) %s %s"
                      % (row[0], row[1], row[2][0], row[2][1], row[3], row[4] or ""))
            else:
                print(u"    %-6s %-28s at=(%9.4f,%9.4f) %s"
                      % (row[0], row[1], row[2][0], row[2][1], row[3]))


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    dump_human()
    dump_views()
    return 0


if __name__ == "__main__":
    sys.exit(main())
