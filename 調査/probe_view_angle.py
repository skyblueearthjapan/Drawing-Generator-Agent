# -*- coding: utf-8 -*-
u"""IView.Angle の単位・符号を実測する(フェーズ4・正面ビュー選定ルールの前提確認)。

問い:
  1. Angle は radian か degree か
  2. 正 = 反時計回り(CCW)か
  3. Angle を変えると ModelToViewTransform も追随するか(= 追随するなら検証に使える)
  4. 標準6ビュー(*正面/*背面/*左側面/*右側面/*平面/*底面)の paper_x/paper_y/out がモデル軸で何か
"""
import os
import sys
import io
import json
import math
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import draw_pipeline as dp  # noqa: E402

VIEWS = [u"*正面", u"*背面", u"*左側面", u"*右側面", u"*平面", u"*底面"]


def axes_of(v):
    u"""ModelToViewTransform から (paper_x, paper_y, out) をモデル軸ベクトルで返す。"""
    a = list(dp.prop(v, "ModelToViewTransform").ArrayData)
    r = a[0:9]
    return ([round(r[0], 4), round(r[3], 4), round(r[6], 4)],
            [round(r[1], 4), round(r[4], 4), round(r[7], 4)],
            [round(r[2], 4), round(r[5], 4), round(r[8], 4)])


def main():
    step = os.path.join(ROOT, u"荏原トライ調整用", u"教師STEP", u"3.昇降軸", u"3-05.STEP")
    out = {"step": step}
    sw, mod = dp.connect()
    print("pre:", dp.list_open_docs(sw))
    part = dwgdoc = None
    try:
        part, _ = dp.open_model_readonly(sw, mod, step)
        print("opened:", part.title)
        dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)

        # --- 標準6ビューの軸マップ ---
        maps = {}
        for i, name in enumerate(VIEWS):
            v = dwg.CreateDrawViewFromModelView3(part.model_name, name,
                                                 0.1 + 0.09 * i, 0.45, 0.0)
            if v is None:
                maps[name] = "None"
                continue
            v = mod.IView(v._oleobj_)
            dp._set_view_scale(v, 1.0)
            px, py, ot = axes_of(v)
            maps[name] = {"paper_x": px, "paper_y": py, "out": ot,
                          "angle_initial": dp.prop(v, "Angle")}
            print(u"%-8s paper_x=%r paper_y=%r out=%r angle0=%r"
                  % (name, px, py, ot, maps[name]["angle_initial"]))
        out["standard_view_axes"] = maps

        # --- Angle の単位・符号 ---
        v = dwg.CreateDrawViewFromModelView3(part.model_name, u"*正面", 0.6, 0.2, 0.0)
        v = mod.IView(v._oleobj_)
        dp._set_view_scale(v, 1.0)
        trials = {}
        for label, val in ((u"pi/2", math.pi / 2), (u"-pi/2", -math.pi / 2),
                           (u"pi", math.pi), (u"90", 90.0), (u"0", 0.0)):
            try:
                v.Angle = val
                got = dp.prop(v, "Angle")
                px, py, ot = axes_of(v)
                ol = dp._view_outline_mm(v)
                trials[label] = {"set": val, "readback": got, "paper_x": px,
                                 "paper_y": py, "out": ot,
                                 "outline_wh": [round(ol[2] - ol[0], 3),
                                                round(ol[3] - ol[1], 3)]}
                print(u"Angle=%-6s → readback=%r paper_x=%r paper_y=%r out=%r wh=%r"
                      % (label, got, px, py, ot, trials[label]["outline_wh"]))
            except Exception as exc:
                trials[label] = {"error": str(exc)}
                print(u"Angle=%s → 例外 %s" % (label, exc))
        out["angle_trials"] = trials
        out["ok"] = True
    except Exception:
        out["ok"] = False
        out["error"] = traceback.format_exc()
        print(out["error"])
    finally:
        for od in (dwgdoc, part):
            if od is not None:
                try:
                    od.close()
                except Exception as e:
                    print("close err:", e)
        print("post:", dp.list_open_docs(sw))

    p = os.path.join(ROOT, u"調査", u"step_check", u"probe_view_angle.json")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(u"保存:", p)


if __name__ == "__main__":
    main()
