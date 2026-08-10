# -*- coding: utf-8 -*-
u"""教師STEPをSW2023で開けるか実証する(読み取り専用・保存しない)。

検証項目
  1. OpenDoc6(swDocPART) で .STEP が開けるか / 戻り値・エラーコード
  2. 開けた場合のドキュメントタイトル(タイトル照合の基準を決めるため)
  3. 単位が mm で入るか(bbox・体積を既知形状と突き合わせ)
  4. ダメなら IImportStepData + LoadFile4 を試す

安全規約: ActiveDocフォールバック禁止 / 自分が開いたものだけCloseDoc / 保存しない
"""
import os
import sys
import io
import json
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import draw_pipeline as dp  # noqa: E402

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


def main():
    print(u"STEP:", STEP, os.path.exists(STEP))
    sw, mod = dp.connect()
    print(u"progid:", dp.sw_compat.detected_progid())
    pre = titles(sw)
    print(u"pre_titles:", pre)

    # --- 単位系のシステムオプション確認(STEPインポート単位) ---
    # swUserPreferenceIntegerValue_e: swStepImportUnits? まずは値を眺めるだけ
    opened = None
    try:
        # 方式A: OpenDoc6 with swDocPART
        err = 0
        warn = 0
        ret = sw.OpenDoc6(STEP, dp.swDocPART,
                          dp.swOpenDocOptions_Silent | dp.swOpenDocOptions_ReadOnly,
                          "", err, warn)
        print(u"OpenDoc6 raw ret type:", type(ret))
        if isinstance(ret, tuple):
            print(u"  tuple:", [type(x) for x in ret], ret[1:])
            doc = ret[0]
        else:
            doc = ret
        print(u"  doc is None?", doc is None)
        if doc is None:
            print(u"方式A失敗 → 方式B(IImportStepData)へ")
        else:
            doc = mod.IModelDoc2(doc._oleobj_)
            title = dp.prop(doc, "GetTitle")
            path = dp.prop(doc, "GetPathName")
            dtype = dp.prop(doc, "GetType")
            print(u"  title=%r pathname=%r type=%r" % (title, path, dtype))
            opened = dp.OpenedDoc(sw, doc, title, True, STEP)
            # 単位
            ext = doc.Extension
            print(u"  LinearUnits(pref 43?) skip")
            part = mod.IPartDoc(doc._oleobj_)
            bodies = part.GetBodies2(0, True) or []
            print(u"  bodies:", len(bodies))
            box = None
            for b in bodies:
                ib = mod.IBody2(b._oleobj_)
                bb = [v * 1000.0 for v in dp.prop(ib, "GetBodyBox")]
                box = bb if box is None else (
                    [min(box[i], bb[i]) for i in range(3)] +
                    [max(box[3 + i], bb[3 + i]) for i in range(3)])
            print(u"  bbox_mm:", box)
            if box:
                print(u"  size_mm:", [round(box[3 + i] - box[i], 4) for i in range(3)])
            mp = ext.CreateMassProperty()
            mp.UseSystemUnits = True
            print(u"  volume_mm3=%.3f  com_mm=%r density=%r" % (
                mp.Volume * 1e9, [c * 1000 for c in list(mp.CenterOfMass)], mp.Density))
            print(u"  model_view_names:", list(dp.prop(doc, "GetModelViewNames") or []))
            try:
                print(u"  material:", part.GetMaterialPropertyName2("", ""))
            except Exception as e:
                print(u"  material err:", e)
    except Exception:
        traceback.print_exc()
    finally:
        if opened is not None:
            cur = dp.prop(opened.doc, "GetTitle")
            print(u"close: current title=%r expected=%r" % (cur, opened.title))
            if cur == opened.title:
                sw.CloseDoc(cur)
                print(u"  closed")
            else:
                print(u"  ❗タイトル不一致のため閉じない")
        print(u"post_titles:", titles(sw))


if __name__ == "__main__":
    main()
