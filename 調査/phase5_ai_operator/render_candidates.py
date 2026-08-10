# -*- coding: utf-8 -*-
u"""候補投影DXF群を1枚のコンタクトシートPNGにする(AIオペレータが目視で正面を選ぶ材料)。

    python 調査/phase5_ai_operator/render_candidates.py <候補ディレクトリ> <出力PNG> [タイトル]

CLAUDE.md: matplotlib の日本語は Meiryo / Yu Gothic(MS Gothic は tofu)。
実寸スケールを候補間で揃える(大きさの錯覚で選定を誤らないため)。
"""
import io
import json
import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, u"調査"))

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import ezdxf  # noqa: E402
import geom_lib as gl  # noqa: E402

matplotlib.rcParams["font.family"] = ["Meiryo", "Yu Gothic", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

STYLE = {"solid": ("#111111", "-", 1.4),
         "hidden": ("#c05010", (0, (5, 3)), 1.0),
         "tangent": ("#1f77b4", (0, (1, 2)), 0.8),
         "center": ("#c04040", (0, (8, 2, 1, 2)), 0.7)}


def draw(ax, pairs, kinds=("solid", "hidden", "tangent")):
    for e, k in pairs:
        if k not in kinds:
            continue
        col, ls, lw = STYLE[k]
        if e.dxftype() == "CIRCLE":
            c, r = e.dxf.center, e.dxf.radius
            n = 180
            ax.plot([c.x + r * math.cos(2 * math.pi * i / n) for i in range(n + 1)],
                    [c.y + r * math.sin(2 * math.pi * i / n) for i in range(n + 1)],
                    color=col, linestyle=ls, linewidth=lw)
            continue
        p = gl._pts_of(e)
        if len(p) < 2:
            continue
        ax.plot([q[0] for q in p], [q[1] for q in p],
                color=col, linestyle=ls, linewidth=lw)


def main():
    d = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(ROOT, sys.argv[1])
    out_png = sys.argv[2] if os.path.isabs(sys.argv[2]) else os.path.join(ROOT, sys.argv[2])
    title = sys.argv[3] if len(sys.argv) > 3 else os.path.basename(d)
    with io.open(os.path.join(d, "candidates_meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    cands = [c for c in meta["candidates"] if c.get("ok")]
    n = len(cands)
    cols = 3
    rows = int(math.ceil(n / float(cols)))

    # 全候補で同一の実寸スケールにする(mm/inch を固定)
    half = 0.0
    for c in cands:
        w, h = c["geom_size_mm"]
        half = max(half, w / 2.0, h / 2.0)
    half = half * 1.28 + 4.0

    fig, axes = plt.subplots(rows, cols, figsize=(4.6 * cols, 4.6 * rows))
    axes = axes.ravel() if n > 1 else [axes]
    for ax in axes:
        ax.axis("off")
    for ax, c in zip(axes, cands):
        doc = ezdxf.readfile(c["dxf"])
        pairs = gl.collect(doc)
        g = c["geom_mm"]
        cx, cy = (g[0] + g[2]) / 2.0, (g[1] + g[3]) / 2.0
        draw(ax, pairs)
        ax.set_xlim(cx - half, cx + half)
        ax.set_ylim(cy - half, cy + half)
        ax.set_aspect("equal")
        ax.axis("on")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(u"%s  %s %+d度\n%.1f × %.1f mm  %s" % (
            c["id"], c["sw_view"], c.get("rot", 0),
            c["geom_size_mm"][0], c["geom_size_mm"][1], c.get("note", "")),
            fontsize=11)
    fig.suptitle(title, fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out_png, dpi=110)
    print(u"保存:", out_png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
