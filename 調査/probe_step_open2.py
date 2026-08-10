# -*- coding: utf-8 -*-
u"""STEPを開くAPIの総当たり実証(読み取り専用運用・保存しない)。

方式:
  A1 OpenDoc6(swDocPART, Silent|ReadOnly)      … probe_step_open.py で 2097152 で失敗済み
  A2 OpenDoc6(swDocPART, Silent)               … ReadOnly が悪さをしていないか
  A3 OpenDoc6(swDocIMPORTED_PART=6, Silent)
  B  LoadFile4(path, "r", ImportStepData, err)
  C  LoadFile2(path, "r")

安全: 開いた分は「pre に無く post にある」タイトルのみ CloseDoc する。
"""
import os
import sys
import io
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import draw_pipeline as dp  # noqa: E402

swDocIMPORTED_PART = 6
swImportNeutralUnits = 580
swImportUnitPreference = 205

STEP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, u"荏原トライ調整用", u"教師STEP", u"1.走行軸", u"1-18.STEP")


def titles(sw):
    out = []
    d = sw.GetFirstDocument()
    while d is not None:
        try:
            out.append(dp.prop(d, "GetTitle"))
        except Exception:
            pass
        d = d.GetNext()
    return out


def report_doc(mod, sw, doc, label):
    doc = mod.IModelDoc2(doc._oleobj_)
    title = dp.prop(doc, "GetTitle")
    print(u"  [%s] title=%r path=%r type=%r" % (
        label, title, dp.prop(doc, "GetPathName"), dp.prop(doc, "GetType")))
    try:
        part = mod.IPartDoc(doc._oleobj_)
        bodies = part.GetBodies2(0, True) or []
        box = None
        for b in bodies:
            ib = mod.IBody2(b._oleobj_)
            bb = [v * 1000.0 for v in dp.prop(ib, "GetBodyBox")]
            box = bb if box is None else (
                [min(box[i], bb[i]) for i in range(3)] +
                [max(box[3 + i], bb[3 + i]) for i in range(3)])
        print(u"  [%s] bodies=%d bbox_mm=%r" % (label, len(bodies), box))
        if box:
            print(u"  [%s] size_mm=%r" % (label, [round(box[3 + i] - box[i], 4) for i in range(3)]))
        mp = doc.Extension.CreateMassProperty()
        mp.UseSystemUnits = True
        print(u"  [%s] vol_mm3=%.3f com_mm=%r" % (
            label, mp.Volume * 1e9, [round(c * 1000, 4) for c in list(mp.CenterOfMass)]))
        print(u"  [%s] model_views=%r" % (label, list(dp.prop(doc, "GetModelViewNames") or [])))
    except Exception:
        traceback.print_exc()
    return title


def cleanup(sw, pre):
    now = titles(sw)
    extra = [t for t in now if t not in pre]
    for t in extra:
        print(u"  cleanup CloseDoc(%r)" % t)
        sw.CloseDoc(t)
    print(u"  after cleanup:", titles(sw))


def main():
    print(u"STEP:", STEP)
    sw, mod = dp.connect()
    pre = titles(sw)
    print(u"pre_titles:", pre)
    print(u"swImportNeutralUnits(580)=", sw.GetUserPreferenceIntegerValue(swImportNeutralUnits))
    print(u"swImportUnitPreference(205)=", sw.GetUserPreferenceIntegerValue(swImportUnitPreference))

    # ---- A2: Silent のみ ----
    print(u"\n== A2 OpenDoc6(swDocPART, Silent) ==")
    try:
        ret = sw.OpenDoc6(STEP, dp.swDocPART, dp.swOpenDocOptions_Silent, "", 0, 0)
        doc, err, warn = ret if isinstance(ret, tuple) else (ret, None, None)
        print(u"  err=%r warn=%r doc=%r" % (err, warn, doc is not None))
        if doc is not None:
            report_doc(mod, sw, doc, "A2")
    except Exception:
        traceback.print_exc()
    cleanup(sw, pre)

    # ---- A3: swDocIMPORTED_PART ----
    print(u"\n== A3 OpenDoc6(swDocIMPORTED_PART=6, Silent) ==")
    try:
        ret = sw.OpenDoc6(STEP, swDocIMPORTED_PART, dp.swOpenDocOptions_Silent, "", 0, 0)
        doc, err, warn = ret if isinstance(ret, tuple) else (ret, None, None)
        print(u"  err=%r warn=%r doc=%r" % (err, warn, doc is not None))
        if doc is not None:
            report_doc(mod, sw, doc, "A3")
    except Exception:
        traceback.print_exc()
    cleanup(sw, pre)

    # ---- B: LoadFile4 ----
    print(u"\n== B LoadFile4(path, 'r', ImportStepData, err) ==")
    try:
        isd = sw.GetImportFileData(STEP)
        print(u"  GetImportFileData ->", isd)
        if isd is not None:
            try:
                isd2 = mod.IImportStepData(isd._oleobj_)
                isd2.MapConfigurationData = False
                print(u"  MapConfigurationData set False")
                isd = isd2
            except Exception as e:
                print(u"  IImportStepData wrap err:", e)
        ret = sw.LoadFile4(STEP, "r", isd, 0)
        doc, err = ret if isinstance(ret, tuple) else (ret, None)
        print(u"  err=%r doc=%r" % (err, doc is not None))
        if doc is not None:
            report_doc(mod, sw, doc, "B")
    except Exception:
        traceback.print_exc()
    cleanup(sw, pre)

    print(u"\nfinal titles:", titles(sw))


if __name__ == "__main__":
    main()
