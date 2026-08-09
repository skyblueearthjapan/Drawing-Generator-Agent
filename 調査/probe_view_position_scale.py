# -*- coding: utf-8 -*-
u"""IView.Position / スケール設定の受け付け方を実測する切り分けスクリプト。"""
import sys, io, os, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import draw_pipeline as dp
import win32com.client, pythoncom

PART = (r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3D CAD Operator Agent"
        r"\生成3D\15015-P3-013_ホルダー.SLDPRT")

sw, mod = dp.connect()
part = dwgdoc = None
try:
    part, _ = dp.open_part_readonly(sw, mod, PART)
    dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
    print("sheet:", dp.sheet_info(mod, sheet))

    v = dwg.CreateDrawViewFromModelView3(part.path, dp.VIEW_FRONT, 0.363, 0.2395, 0.0)
    v = mod.IView(v._oleobj_)
    def show(tag):
        pos = dp.prop(v, "Position")
        ol = dp._view_outline_mm(v)
        print("%-28s Position=%r len=%s outline=%s center=(%.3f,%.3f) "
              "ScaleRatio=%r ScaleDecimal=%r UseSheetScale=%r"
              % (tag, [round(p*1000,3) for p in list(pos)], len(list(pos)),
                 [round(c,3) for c in ol],
                 (ol[0]+ol[2])/2, (ol[1]+ol[3])/2,
                 list(dp.prop(v, "ScaleRatio")), dp.prop(v, "ScaleDecimal"),
                 dp.prop(v, "UseSheetScale")))
    show("created@(363,239.5)")

    # --- スケールを 1:1 にする手を順に試す ---
    for tag, fn in (
        ("UseSheetScale=1", lambda: setattr(v, "UseSheetScale", 1)),
        ("ScaleDecimal=1.0", lambda: setattr(v, "ScaleDecimal", 1.0)),
        ("ScaleRatio=VARIANT[1,1]", lambda: setattr(
            v, "ScaleRatio",
            win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [1.0, 1.0]))),
    ):
        try:
            fn(); show(tag)
        except Exception as e:
            print(tag, "-> 例外", e)

    # --- Position の代入形式を順に試す ---
    for tag, val in (
        ("tuple(0.5,0.3)", (0.5, 0.3)),
        ("list[0.5,0.3]", [0.5, 0.3]),
        ("VARIANT R8[0.5,0.3]",
         win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.5, 0.3])),
        ("VARIANT R8[0.5,0.3,0.0]",
         win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.5, 0.3, 0.0])),
    ):
        try:
            v.Position = val
            show("Pos=" + tag)
        except Exception as e:
            print("Pos=" + tag, "-> 例外", e)

    # --- ModelToViewTransform ---
    t = dp.prop(v, "ModelToViewTransform")
    print("ModelToViewTransform:", [round(x, 6) for x in list(t.ArrayData)])
except Exception:
    traceback.print_exc()
finally:
    for od in (dwgdoc, part):
        if od is not None:
            try:
                od.close()
            except Exception as e:
                print("close err", e)
