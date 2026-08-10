# -*- coding: utf-8 -*-
u"""view_orient の純ロジック回帰テスト(SW不要)。

step_import_report.md §3-2 で人間図面と照合済みの5部品の面情報を手で写して、
**ルールが人間の選んだ向きを再現するか**を確かめる。期待値は同レポートの実測値。
"""
import os
import sys
import io
import math

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import view_orient as vo  # noqa: E402


def cyl(d, axis, origin, area):
    return {"d_mm": d, "axis": list(axis), "origin_mm": list(origin), "area_mm2": area}


CASES = [
    # (ラベル, size_mm, cylinders, total_area, 期待class, 期待front(SWビュー,回転度))
    (u"1-18 クッション", [25.0, 25.0, 27.0],
     [cyl(9.5, (0, 0, 1), (0, 0, 15), math.pi * 9.5 * 12),
      cyl(5.5, (0, 0, 1), (0, 0, 0), math.pi * 5.5 * 15)],
     4500.0, "holed_block", (u"*正面", 0)),
    (u"1-09 エンドプレート", [48.0, 48.0, 6.0],
     [cyl(48.0, (0, 0, 1), (0, 0, 0), math.pi * 48 * 6),
      cyl(6.6, (0, 0, 1), (17, 0, 0), math.pi * 6.6 * 6),
      cyl(6.6, (0, 0, 1), (-17, 0, 0), math.pi * 6.6 * 6)],
     4663.0, "plate", (u"*正面", 0)),
    (u"3-05 ベアリングカラー", [40.0, 4.75, 40.0],
     [cyl(40.0, (0, -1, 0), (0, 0, 0), math.pi * 40 * 4.75 / 2) for _ in (0, 1)] +
     [cyl(38.0, (0, -1, 0), (0, 0, 0), math.pi * 38 * 2.0 / 2) for _ in (0, 1)] +
     [cyl(30.0, (0, -1, 0), (0, 0, 0), math.pi * 30 * 4.75 / 2) for _ in (0, 1)],
     2500.0, "lathe", (u"*正面", 270)),
    (u"1-25 走行LSドグ", [100.0, 45.0, 12.0],
     [cyl(9.0, (0, 0, 1), (20, 10, 0), math.pi * 9 * 12),
      cyl(9.0, (0, 0, 1), (-20, 10, 0), math.pi * 9 * 12)],
     12800.0, "plate", (u"*正面", 0)),
    (u"1-12 リニアガイドカバー", [1015.0, 50.0, 3.2],
     [cyl(8.0, (0, 0, 1), (x, 0, 0), math.pi * 8 * 3.2) for x in (-300, -100, 100, 300)],
     110000.0, "plate", (u"*正面", 0)),
    # 追加: 単純な丸棒(段無し・軸方向に長い)= 旋盤物として軸を紙面水平にできるか。
    # 主軸が Z なので紙面法線に Z は使えず、正面には `*平面` を90度回して使う(軸が紙面水平になる)
    (u"[合成] 丸棒 φ20x200(Z軸)", [20.0, 20.0, 200.0],
     [cyl(20.0, (0, 0, 1), (0, 0, 0), math.pi * 20 * 200)],
     13200.0, "lathe", (u"*平面", 90)),
]


def main():
    ng = 0
    for label, size, cyls, total, want_cls, want_front in CASES:
        survey = {"cylinders": cyls, "planes": [], "surface_counts": {},
                  "total_area_mm2": total, "body_count": 1}
        ev = vo.classify(survey, size)
        frame = vo.presentation_frame(ev, size)
        plan = vo.view_plan(frame)
        got_front = (plan["front"]["sw_view"], plan["front"]["rotation_deg"])
        ok = (ev["shape_class"] == want_cls and got_front == want_front)
        ng += 0 if ok else 1
        print(u"%s %-24s class=%-11s(期待%-11s) front=%s/%3d度(期待%s/%d度) ex=%s ez=%s" % (
            u"OK " if ok else u"NG!", label, ev["shape_class"], want_cls,
            got_front[0], got_front[1], want_front[0], want_front[1],
            frame["ex_axis"], frame["ez_axis"]))
        print(u"       %s" % ev["reason"])
        print(u"       top=%s/%d度 right=%s/%d度" % (
            plan["top"]["sw_view"], plan["top"]["rotation_deg"],
            plan["right"]["sw_view"], plan["right"]["rotation_deg"]))
    print(u"\n不一致 %d 件" % ng)
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
