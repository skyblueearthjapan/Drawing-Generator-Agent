# -*- coding: utf-8 -*-
u"""(1) ModelName=title でビューが作れるか (2) QuitDoc で未保存インポート部品が閉じられるか。"""
import os
import sys
import io
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import draw_pipeline as dp  # noqa: E402
import sw_docs  # noqa: E402

STEP = os.path.join(ROOT, u"荏原トライ調整用", u"教師STEP", u"1.走行軸", u"1-18.STEP")

sw, mod = dp.connect()
pre = sw_docs.titles(sw)
print(u"pre:", pre)
assert not pre, u"他のドキュメントが開いている: %r" % pre

part_title = None
dwg_title = None
try:
    isd = sw.GetImportFileData(STEP)
    ret = sw.LoadFile4(STEP, "r", isd, 0)
    doc, err = ret if isinstance(ret, tuple) else (ret, None)
    doc = mod.IModelDoc2(doc._oleobj_)
    part_title = dp.prop(doc, "GetTitle")
    print(u"part title=%r" % part_title)

    dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
    dwg_title = dwgdoc.title
    print(u"dwg title=%r" % dwg_title)

    for label, name in ((u"title", part_title), (u"steppath", STEP), (u"empty", "")):
        try:
            v = dwg.CreateDrawViewFromModelView3(name, dp.VIEW_FRONT, 0.4, 0.3, 0.0)
            print(u"  ModelName=%s(%r) -> %r" % (label, name, v is not None))
            if v is not None:
                v = mod.IView(v._oleobj_)
                print(u"     name=%r outline=%r" % (dp.prop(v, "GetName2"), dp._view_outline_mm(v)))
        except Exception as e:
            print(u"  ModelName=%s -> 例外 %s" % (label, e))
except Exception:
    traceback.print_exc()
finally:
    print(u"docs before close:", sw_docs.docs(sw))
    if dwg_title:
        sw.CloseDoc(dwg_title)
        print(u"  CloseDoc(dwg) ->", sw_docs.docs(sw))
    if part_title:
        sw.QuitDoc(part_title)
        print(u"  QuitDoc(part) ->", sw_docs.docs(sw))
