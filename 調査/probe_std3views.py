# -*- coding: utf-8 -*-
u"""IDrawingDoc.Create3rdAngleViews2 の挙動確認(標準3面図API)。"""
import sys, io, os, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import draw_pipeline as dp

PART = (r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3D CAD Operator Agent"
        r"\生成3D\15015-P3-013_ホルダー.SLDPRT")

sw, mod = dp.connect()
part = dwgdoc = None
try:
    part, _ = dp.open_part_readonly(sw, mod, PART)
    dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
    ok = dwg.Create3rdAngleViews2(part.path)
    print("Create3rdAngleViews2 ->", ok)
    for v in dp.list_views(mod, dwg):
        print("   ", v)
    # 個々のスケール/親子関係
    sheets = dp.prop(dwg, "GetViews") or []
    for sv in sheets:
        for i, v in enumerate(list(sv)):
            v = mod.IView(v._oleobj_)
            if i == 0:
                continue
            print("  %-12s type=%s scale=%s useSheet=%s useParent=%s pos=%s" % (
                dp.prop(v, "GetName2"), dp.prop(v, "Type"),
                dp.prop(v, "ScaleDecimal"), dp.prop(v, "UseSheetScale"),
                dp.prop(v, "UseParentScale"),
                [round(p * 1000, 3) for p in list(dp.prop(v, "Position"))]))
except Exception:
    traceback.print_exc()
finally:
    for od in (dwgdoc, part):
        if od is not None:
            try:
                od.close()
            except Exception as e:
                print("close err", e)
