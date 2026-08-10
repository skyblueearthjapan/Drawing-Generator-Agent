# -*- coding: utf-8 -*-
u"""STEP投影ビューと人間図面を並置した目視確認用PNGを作る。

    python 調査/render_step_check.py <label> <人間DXF> <出力PNG> [--no-frame]

CLAUDE.md: matplotlib の日本語は Meiryo(MS Gothic は全滅)。
"""
import os
import sys
import io
import json
import math
import argparse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # ❗import時に重ね包みしない(compare_views参照)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

import ezdxf  # noqa: E402
import geom_lib as gl  # noqa: E402
import compare_views as cv  # noqa: E402

matplotlib.rcParams["font.family"] = ["Meiryo", "Yu Gothic", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

STYLE = {"solid": ("#111111", "-", 1.0),
         "hidden": ("#8a8a8a", (0, (4, 2)), 0.7),
         "tangent": ("#1f77b4", (0, (1, 2)), 0.7),
         "center": ("#c04040", (0, (8, 2, 1, 2)), 0.6)}


def draw(ax, pairs, scale=1.0, kinds=("solid", "hidden", "tangent", "center")):
    for e, k in pairs:
        if k not in kinds:
            continue
        col, ls, lw = STYLE[k]
        t = e.dxftype()
        if t == "CIRCLE":
            c, r = e.dxf.center, e.dxf.radius
            n = 128
            xs = [(c.x + r * math.cos(2 * math.pi * i / n)) * scale for i in range(n + 1)]
            ys = [(c.y + r * math.sin(2 * math.pi * i / n)) * scale for i in range(n + 1)]
            ax.plot(xs, ys, color=col, linestyle=ls, linewidth=lw)
            continue
        p = gl._pts_of(e)
        if len(p) < 2:
            continue
        ax.plot([q[0] * scale for q in p], [q[1] * scale for q in p],
                color=col, linestyle=ls, linewidth=lw)


def box(ax, bb, label, color="#d62728"):
    ax.add_patch(Rectangle((bb[0], bb[1]), bb[2] - bb[0], bb[3] - bb[1],
                           fill=False, edgecolor=color, linewidth=0.7, linestyle=":"))
    ax.text(bb[0], bb[1] - (bb[3] - bb[1]) * 0.04 - 1.0, label,
            color=color, fontsize=8, va="top")


def render(label, sw_dxf, meta, cmpj, human_p, out_png, use_frame=True, gap=8.0):
    u"""照合の目視確認PNGを作る(フェーズ4の量産バッチ用に実パス受け取りへ一般化)。"""
    hdoc, hcl, fsum, stext, pmag = cv.load_human(human_p, use_frame, gap)
    sf = cmpj.get("scale_factor") or 1.0

    sdoc = ezdxf.readfile(sw_dxf)
    spairs = gl.collect(sdoc)

    fig = plt.figure(figsize=(16, 11))
    gs = fig.add_gridspec(2, 6, height_ratios=[1.25, 1.0], hspace=0.32, wspace=0.45)
    ax = fig.add_subplot(gs[0, 0:3])
    draw(ax, spairs)
    xs, ys = [], []
    for key in ("front", "top", "right", "iso"):
        g = meta["views"][key]["geom_mm"]
        box(ax, g, u"%s %.1f×%.1f" % (key, g[2] - g[0], g[3] - g[1]))
        if key != "iso":
            xs += [g[0], g[2]]
            ys += [g[1], g[3]]
    mx = max(20.0, (max(xs) - min(xs)) * 0.08)
    ax.set_xlim(min(xs) - mx, max(xs) + mx)
    ax.set_ylim(min(ys) - mx, max(ys) + mx)
    ax.set_title(u"SolidWorks投影(STEP由来・尺度1:1・第三角法)\n%s"
                 % os.path.basename(meta["step"]), fontsize=11)

    ax2 = fig.add_subplot(gs[0, 3:6])
    # 人間図面は「部品ビューと判定されたクラスタ」だけを実物mmで描く
    roles = cmpj.get("human_roles", {})
    drawn = []
    for i, c in enumerate(hcl):
        w = (c["bbox"][2] - c["bbox"][0]) * sf
        h = (c["bbox"][3] - c["bbox"][1]) * sf
        if str(i) not in roles:
            continue
        draw(ax2, c["entities"], scale=sf)
        bb = [v * sf for v in c["bbox"]]
        box(ax2, bb, u"%s %.1f×%.1f" % (roles[str(i)], w, h))
        drawn.append(i)
    if not drawn:      # 役割が取れなければ全部描く
        for c in hcl:
            draw(ax2, c["entities"], scale=sf)
    ax2.set_title(u"人間の部品図(図枠差し引き・実物mm換算)\n%s\n尺度=%s / 用紙倍率=%g / DXF1単位=%.4gmm"
                  % (os.path.basename(human_p),
                     (stext or "").split(";")[-1], pmag, sf), fontsize=10)

    for a in (ax, ax2):
        a.set_aspect("equal")
        a.grid(True, linewidth=0.3, alpha=0.4)
        a.tick_params(labelsize=7)

    # --- 重ね合わせ(照合の一次証拠): 各ビューを bbox 左下原点に正規化して重ねる ---
    inv = cmpj.get("human_to_sw", {})
    scl = gl.cluster_views(spairs, gap=8.0)
    slots = [(1, 0), (1, 2), (1, 4)]
    lines = []
    for hidx, r in sorted(inv.items(), key=lambda kv: int(kv[0])):
        if not slots:
            break
        k = r["sw_view"]
        lines.append(u"人間%s ← SW-%s を %s(det=%+d) 一致=%.3f 外形差=%.3fmm" % (
            r["human_role"], k, r["transform"], r["det"], r["iou"], r["size_err_mm"]))
        g = meta["views"][k]["geom_mm"]
        sc = min(scl, key=lambda c: sum(abs(c["bbox"][i] - g[i]) for i in range(4)))
        hc = hcl[int(hidx)]
        r = {"best": {"transform": r["transform"], "iou": r["iou"], "det": r["det"]},
             "human_idx": hidx, "human_role": r["human_role"]}
        row, col = slots.pop(0)
        axo = fig.add_subplot(gs[row, col:col + 2])
        # SW側(黒): 照合で選ばれた D4 変換を適用してから bbox 左下を原点へ揃える
        mat = [mm for nm, mm, _ in cv.D4 if nm == r["best"]["transform"]][0]
        a_, b_, c_, d_ = mat
        polys = []
        for e, kk in sc["entities"]:
            p = gl._pts_of(e)
            if len(p) < 2:
                continue
            polys.append((kk, [(a_ * q[0] + b_ * q[1], c_ * q[0] + d_ * q[1]) for q in p]))
        if polys:
            mnx = min(q[0] for _, pp in polys for q in pp)
            mny = min(q[1] for _, pp in polys for q in pp)
            for kk, pp in polys:
                axo.plot([q[0] - mnx for q in pp], [q[1] - mny for q in pp],
                         color="#111111", linewidth=1.6,
                         linestyle="-" if kk == "solid" else (0, (4, 2)), alpha=0.8)
        hb = hc["bbox"]
        for e, kk in hc["entities"]:
            if e.dxftype() == "CIRCLE":
                c0, rr = e.dxf.center, e.dxf.radius
                n = 128
                axo.plot([(c0.x + rr * math.cos(2 * math.pi * i / n) - hb[0]) * sf
                          for i in range(n + 1)],
                         [(c0.y + rr * math.sin(2 * math.pi * i / n) - hb[1]) * sf
                          for i in range(n + 1)],
                         color="#e8000b", linewidth=0.9, alpha=0.9)
                continue
            p = gl._pts_of(e)
            if len(p) < 2:
                continue
            axo.plot([(q[0] - hb[0]) * sf for q in p], [(q[1] - hb[1]) * sf for q in p],
                     color="#e8000b", linewidth=0.9, alpha=0.9)
        axo.set_aspect("equal")
        axo.grid(True, linewidth=0.3, alpha=0.4)
        axo.tick_params(labelsize=7)
        axo.set_title(u"重ね 人間%s[%s] ← SW-%s\n黒=SW(%s適用) 赤=人間 / 一致=%.3f det=%+d" % (
            r.get("human_role"), r.get("human_idx"), k, r["best"]["transform"],
            r["best"]["iou"], r["best"]["det"]), fontsize=9)

    if lines:
        fig.text(0.5, 0.012, u"　/　".join(lines), ha="center", fontsize=10)
    fig.suptitle(u"STEP→投影 vs 人間図面 照合: %s" % label, fontsize=13)

    fig.savefig(out_png, dpi=130)
    plt.close(fig)
    print(u"保存:", out_png)
    return out_png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("human")
    ap.add_argument("out")
    ap.add_argument("--no-frame", action="store_true")
    ap.add_argument("--gap", type=float, default=8.0)
    args = ap.parse_args()

    base = os.path.join(ROOT, u"調査", u"step_check")
    meta = json.load(io.open(os.path.join(base, u"meta_%s.json" % args.label), encoding="utf-8"))
    cmp_p = os.path.join(base, u"compare_%s.json" % args.label)
    cmpj = json.load(io.open(cmp_p, encoding="utf-8")) if os.path.exists(cmp_p) else {}
    human_p = args.human if os.path.isabs(args.human) else os.path.join(ROOT, args.human)
    out = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    render(args.label, os.path.join(base, u"views_%s.dxf" % args.label), meta, cmpj,
           human_p, out, use_frame=not args.no_frame, gap=args.gap)


if __name__ == "__main__":
    main()
