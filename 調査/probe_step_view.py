# -*- coding: utf-8 -*-
u"""STEP由来ドキュメント(PathName='')で CreateDrawViewFromModelView3 が通るか実証。

ModelName に何を渡せばよいかを総当たり:
  1. GetPathName()  … '' (未保存)
  2. GetTitle()     … '1-18.SLDPRT'
  3. STEPのフルパス
"""
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

STEP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, u"荏原トライ調整用", u"教師STEP", u"1.走行軸", u"1-18.STEP")


def main():
    sw, mod = dp.connect()
    pre = sw_docs.titles(sw)
    print(u"pre:", pre)
    try:
        isd = sw.GetImportFileData(STEP)
        ret = sw.LoadFile4(STEP, "r", isd, 0)
        doc, err = ret if isinstance(ret, tuple) else (ret, None)
        if doc is None:
            raise RuntimeError(u"LoadFile4 失敗 err=%r" % err)
        doc = mod.IModelDoc2(doc._oleobj_)
        title = dp.prop(doc, "GetTitle")
        pathname = dp.prop(doc, "GetPathName")
        print(u"imported title=%r pathname=%r" % (title, pathname))

        dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
        print(u"drawing title=%r" % dwgdoc.title)
        print(u"sheet:", dp.sheet_info(mod, sheet))

        for label, name in ((u"pathname", pathname), (u"title", title), (u"steppath", STEP)):
            try:
                v = dwg.CreateDrawViewFromModelView3(name, dp.VIEW_FRONT, 0.4, 0.3, 0.0)
                print(u"  ModelName=%s(%r) -> %r" % (label, name, v is not None))
                if v is not None:
                    v = mod.IView(v._oleobj_)
                    print(u"    view name=%r outline=%r scale=%r" % (
                        dp.prop(v, "GetName2"), dp._view_outline_mm(v), dp.prop(v, "ScaleDecimal")))
                    break
            except Exception as e:
                print(u"  ModelName=%s(%r) -> 例外 %s" % (label, name, e))
    except Exception:
        traceback.print_exc()
    finally:
        print(u"cleanup:")
        # 図面は未保存なので CloseDoc(title) で捨てられる(保存ダイアログはSilentで出ない)
        print(sw_docs.close_extras(sw, pre))


if __name__ == "__main__":
    main()
