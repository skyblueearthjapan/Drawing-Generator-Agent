# -*- coding: utf-8 -*-
u"""AIオペレータが選んだ正面ビューで標準4面図を投影し、views.dxf + meta.json を作る。

    python 調査/phase5_ai_operator/project_chosen.py <STEP> <views.dxf> <meta.json> <SWビュー名> <回転角>

「SW標準ビュー名 + 紙面内回転角」から提示フレーム (ex, ey, ez) を復元し、
`view_orient.view_plan()` で 正面/平面/右側面 の割り当てを作って投影する
(= run_batch.py と同じ経路。ただし向きはルールでなく**AIオペレータの選択**)。

meta.json は engine/generate_drawing.py の run_sw_projection と同じ構造で書くので、
`generate_drawing.py --skip-sw --views-dxf ... --meta-json ...` にそのまま渡せる。

安全規約(CLAUDE.md): 読み取り専用 / QuitDoc / 自分が開いたものだけ閉じる / pre-post記録。
"""
import io
import json
import os
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import draw_pipeline as dp   # noqa: E402
import view_orient as vo     # noqa: E402


def frame_from_candidate(sw_view, rot_deg):
    u"""(SW標準ビュー名, 紙面内回転角[度・CCW]) -> 提示フレーム {ex, ey, ez}。

    SWビュー V の画像を θ だけCCWに回すと、V で紙面角 φ に居たモデル方向は φ+θ へ移る。
    よって回転後に紙面右(角0)へ来るのは、回転前に角 -θ に居た方向:
        θ=0 -> ux / θ=90 -> -vy / θ=180 -> -ux / θ=270 -> +vy
    (`view_orient._match_view` の逆写像。deg = -φ の関係と整合する)
    """
    rot = int(rot_deg) % 360
    for name, ux, vy, ot in vo.SW_STANDARD_VIEWS:
        if name != sw_view:
            continue
        ex = {0: ux, 90: vo._neg(vy), 180: vo._neg(ux), 270: vy}[rot]
        ez = ot
        ey = vo._cross(ez, ex)
        xi = [i for i in range(3) if ex[i]][0]
        zi = [i for i in range(3) if ez[i]][0]
        return {"ex": tuple(ex), "ey": tuple(ey), "ez": tuple(ez),
                "ex_axis": (u"-" if ex[xi] < 0 else u"+") + vo.AXIS_NAME[xi],
                "ez_axis": (u"-" if ez[zi] < 0 else u"+") + vo.AXIS_NAME[zi],
                "rule": u"[AIオペレータ目視選択] %s %+d度" % (sw_view, rot)}
    raise ValueError(u"未知のSW標準ビュー名: %r" % sw_view)


def main():
    step = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(ROOT, sys.argv[1])
    out_dxf = sys.argv[2] if os.path.isabs(sys.argv[2]) else os.path.join(ROOT, sys.argv[2])
    out_meta = sys.argv[3] if os.path.isabs(sys.argv[3]) else os.path.join(ROOT, sys.argv[3])
    sw_view, rot = sys.argv[4], int(sys.argv[5])

    frame = frame_from_candidate(sw_view, rot)
    plan = vo.view_plan(frame)
    print(u"フレーム: ex=%s ez=%s" % (frame["ex_axis"], frame["ez_axis"]))
    for k in ("front", "top", "right"):
        print(u"  %-6s %s %+d度" % (k, plan[k]["sw_view"], plan[k]["rotation_deg"]))

    sw, mod = dp.connect()
    meta = {"part_path": step, "chosen_front": {"sw_view": sw_view, "rotation_deg": rot},
            "frame": frame,
            "view_plan": {k: {"sw_view": v["sw_view"], "rotation_deg": v["rotation_deg"]}
                          for k, v in plan.items()}}
    part = dwgdoc = None
    try:
        meta["pre_open_titles"] = [t for t, _ in dp.list_open_docs(sw)]
        part, vh = dp.open_model_readonly(sw, mod, step)
        meta["version_history"] = vh
        m = dp.part_metrics(mod, part)
        meta["metrics"] = m
        dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
        meta["sheet"] = dp.sheet_info(mod, sheet)
        # ❗STEP由来B-repは円筒が半割り → 接線エッジは非表示が既定(CLAUDE.md)
        meta["views"] = dp.insert_standard_views(
            mod, dwg, part.model_name, m["size_mm"],
            tangent=dp.swTangentEdgesHidden, plan=plan)
        meta["views_on_sheet"] = dp.list_views(mod, dwg)
        dwgdoc.doc.ForceRebuild3(False)
        dwgdoc.doc.ViewZoomtofit2()
        res = dp.export_dxf(sw, dwgdoc, out_dxf)
        meta["export"] = {"path": res["path"], "bytes": res["bytes"],
                          "warnings": res["warnings"]}
        meta["ok"] = True
        for k in ("front", "top", "right", "iso"):
            g = meta["views"][k]["geom_size_mm"]
            print(u"  %-6s %s%+d度  %.2f x %.2f mm" % (
                k, meta["views"][k]["view_name"], meta["views"][k]["angle_deg"], g[0], g[1]))
    except Exception:
        meta["ok"] = False
        meta["error"] = traceback.format_exc()
        print(meta["error"])
    finally:
        for od, lab in ((dwgdoc, u"図面"), (part, u"部品")):
            if od is None:
                continue
            try:
                od.close()
            except Exception as e:
                meta.setdefault("close_errors", []).append(u"%s: %s" % (lab, e))
        meta["post_open_titles"] = [t for t, _ in dp.list_open_docs(sw)]
        print(u"post:", meta["post_open_titles"])
    d = os.path.dirname(out_meta)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with io.open(out_meta, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    print(u"保存:", out_meta)
    return 0 if meta.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
