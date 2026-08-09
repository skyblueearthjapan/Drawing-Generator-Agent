# -*- coding: utf-8 -*-
u"""接線エッジ(タンジェントエッジ)の DXF 表現を切り分ける。

同じ図面に同じ *正面 ビューを3つ置き、SetDisplayTangentEdges2 を 0/1/2 で変えて
DXF に出し、ビュー領域ごとの線種内訳を比べる。
"""
import sys, io, os, collections, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import draw_pipeline as dp

PART = sys.argv[1] if len(sys.argv) > 1 else (
    r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3D CAD Operator Agent"
    r"\生成3D\15015-P3-008_回転指針.SLDPRT")
OUT = os.path.join(ROOT, u"調査", u"probe_tangent.dxf")

MODES = [(dp.swTangentEdgesHidden, u"0:Hidden(非表示)"),
         (dp.swTangentEdgesVisibleAndFonted, u"1:VisibleAndFonted(フォント付)"),
         (dp.swTangentEdgesVisible, u"2:Visible(実線)")]

sw, mod = dp.connect()
part = dwgdoc = None
regions = []
try:
    part, _ = dp.open_part_readonly(sw, mod, PART)
    m = dp.part_metrics(mod, part)
    print(u"part:", part.title, u"size_mm=", [round(v, 3) for v in m["size_mm"]])
    dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)

    for i, (mode, label) in enumerate(MODES):
        v = dwg.CreateDrawViewFromModelView3(part.path, dp.VIEW_ISO, 0.4, 0.3, 0.0)
        v = mod.IView(v._oleobj_)
        v.SetDisplayMode3(False, dp.swHIDDEN_GREYED, False, False)
        v.SetDisplayTangentEdges2(mode)
        dp._set_view_scale(v, 1.0)
        dp._set_position_mm(v, 150.0 + i * 250.0, 300.0)
        ol = dp._view_outline_mm(v)
        regions.append((label, ol))
        print(u"  %-28s outline=%s" % (label, [round(c, 3) for c in ol]))

    dwgdoc.doc.ForceRebuild3(False)
    res = dp.export_dxf(sw, dwgdoc, OUT)
    print("DXF:", res["path"], res["bytes"])
except Exception:
    traceback.print_exc()
finally:
    for od in (dwgdoc, part):
        if od is not None:
            try:
                od.close()
            except Exception as e:
                print("close err", e)

if regions and os.path.exists(OUT):
    import ezdxf
    d = ezdxf.readfile(OUT)
    msp = d.modelspace()
    print(u"\n=== 領域別 (エンティティ種別, 線種) ===")
    for label, ol in regions:
        cnt = collections.Counter()
        for e in msp:
            try:
                b = e.bbox() if hasattr(e, "bbox") else None
            except Exception:
                b = None
            p = None
            if e.dxftype() == "LINE":
                p = e.dxf.start
            elif e.dxftype() in ("CIRCLE", "ARC"):
                p = e.dxf.center
            elif e.dxftype() == "LWPOLYLINE":
                pts = list(e.get_points("xy"))
                p = pts[0] if pts else None
            elif e.dxftype() == "SPLINE":
                cp = list(e.control_points)
                p = cp[0] if cp else None
            elif e.dxftype() == "INSERT":
                p = e.dxf.insert
            if p is None:
                continue
            if ol[0] - 2 <= p[0] <= ol[2] + 2 and ol[1] - 2 <= p[1] <= ol[3] + 2:
                cnt[(e.dxftype(), e.dxf.get("linetype", "BYLAYER"))] += 1
        print(u"  %-28s %s (計%d)" % (label, dict(cnt), sum(cnt.values())))
