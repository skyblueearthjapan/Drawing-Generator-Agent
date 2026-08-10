# -*- coding: utf-8 -*-
u"""人間図面と生成図面を**同一レンダラー**で左右に並べたPNGを作る(目視ゲート用)。

CLAUDE.md知見: 「手描きレンダラーの文字幅は近似なので、生成図面の見た目を評価する前に
**必ず人間の実図面を同じレンダラーで描いて比較する**」。中心線の見え方も同じ扱いにする。

中心線(DASHDOT/CENTER系)は赤で強調して描く。両側とも同じ規則で描くので、
「人間はどこに中心線を引き、生成はどこに引いたか」が直接見比べられる。

実行:
    python 調査/compare_centerline_png.py            # BLIND2の5枚
    python 調査/compare_centerline_png.py <human.dxf> <gen.dxf> <out.png>
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import ezdxf                                     # noqa: E402
import matplotlib                                # noqa: E402
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = "Meiryo"
matplotlib.rcParams["axes.unicode_minus"] = False
import matplotlib.pyplot as plt                  # noqa: E402

from analyze_centerline_style import detect_frame, CENTER_LT  # noqa: E402
from engine import compose_drawing as cd         # noqa: E402

HIDDEN_LT = {"HIDDEN", "HIDDEN2", "HIDDENX2", "DASHED", "DASHED2", "DASHEDX2"}
PH_LT = {"PHANTOM", "PHANTOM2", "PHANTOMX2", "DIVIDE", "DIVIDE2", "DIVIDEX2"}

S_CENTER = dict(color="#ff4b45", lw=1.1, ls=(0, (9, 3, 1, 3)), zorder=5)
S_HIDDEN = dict(color="#e0a03c", lw=0.6, ls=(0, (5, 3)), zorder=3)
S_PHANT = dict(color="#4f8fd0", lw=0.6, ls=(0, (8, 3, 2, 3)), zorder=3)
S_SOLID = dict(color="#e6e6e6", lw=0.7, ls="-", zorder=4)
S_DIM = dict(color="#3fd07a", lw=0.6, ls="-", zorder=2)

PAIRS = [
    (u"25154-1-27 走行フレーム踏板",
     u"荏原トライ調整用/DXF/部品表用DXFデータ/1.走行軸/25154-1-27_走行フレーム踏板.dxf",
     u"調査/centerline/blind2/25154-1-27_走行フレーム踏板.dxf"),
    (u"25154-2-16 指針",
     u"荏原トライ調整用/DXF/部品表用DXFデータ/2.ターン軸/25154-2-16_指針.dxf",
     u"調査/centerline/blind2/25154-2-16_指針.dxf"),
    (u"25154-3-02 モータブラケット",
     u"荏原トライ調整用/DXF/部品表用DXFデータ/3.昇降軸/25154-3-02_モータブラケット.dxf",
     u"調査/centerline/blind2/25154-3-02_モータブラケット.dxf"),
    (u"25154-4-05 駆動ユニットブラケット",
     u"荏原トライ調整用/DXF/部品表用DXFデータ/4.前後軸/25154-4-05_駆動ユニットブラケット.dxf",
     u"調査/centerline/blind2/25154-4-05_駆動ユニットブラケット.dxf"),
    (u"25154-5-05 減速機フランジ",
     u"荏原トライ調整用/DXF/部品表用DXFデータ/5.ひねり軸/25154-5-05_減速機フランジ.dxf",
     u"調査/centerline/blind2/25154-5-05_減速機フランジ.dxf"),
]


def _lt(doc, e):
    lt = e.dxf.get("linetype", "BYLAYER")
    if lt == "BYLAYER":
        try:
            lt = doc.layers.get(e.dxf.layer).dxf.linetype
        except Exception:
            lt = "CONTINUOUS"
    return str(lt).upper()


def _style(doc, e):
    lt = _lt(doc, e)
    if lt in CENTER_LT:
        return S_CENTER
    if lt in HIDDEN_LT:
        return S_HIDDEN
    if lt in PH_LT:
        return S_PHANT
    return S_SOLID


def draw(ax, doc, e, st=None, depth=0):
    t = e.dxftype()
    s = st or _style(doc, e)
    try:
        if t == "LINE":
            ax.plot([e.dxf.start.x, e.dxf.end.x], [e.dxf.start.y, e.dxf.end.y], **s)
        elif t == "CIRCLE":
            c, r = e.dxf.center, e.dxf.radius
            a = [i * math.pi / 60 for i in range(121)]
            ax.plot([c.x + r * math.cos(v) for v in a], [c.y + r * math.sin(v) for v in a], **s)
        elif t == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
            if a1 <= a0:
                a1 += 2 * math.pi
            a = [a0 + (a1 - a0) * i / 64 for i in range(65)]
            ax.plot([c.x + r * math.cos(v) for v in a], [c.y + r * math.sin(v) for v in a], **s)
        elif t in ("LWPOLYLINE", "POLYLINE"):
            pts = ([(p[0], p[1]) for p in e.get_points("xy")] if t == "LWPOLYLINE"
                   else [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices])
            if getattr(e, "closed", False) and pts:
                pts = pts + [pts[0]]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], **s)
        elif t in ("SPLINE", "ELLIPSE"):
            pts = list(e.flattening(0.1))
            ax.plot([p[0] for p in pts], [p[1] for p in pts], **s)
        elif t == "INSERT" and depth < 4:
            for ve in e.virtual_entities():
                draw(ax, doc, ve, s, depth + 1)
        elif t == "DIMENSION" and depth < 4:
            for ve in e.virtual_entities():
                draw(ax, doc, ve, S_DIM, depth + 1)
        elif t == "LEADER":
            pts = [(p[0], p[1]) for p in e.vertices]
            ax.plot([p[0] for p in pts], [p[1] for p in pts], **S_DIM)
    except Exception:
        pass


def render(ax, path, title):
    doc = ezdxf.readfile(path)
    for e in doc.modelspace():
        draw(ax, doc, e)
    fr = detect_frame(doc)
    if fr:
        m, (x0, y0, x1, y1) = fr
        pad = 6.0 * m
    else:
        x0, y0, x1, y1 = (cd.FRAME_X0, cd.FRAME_Y0, cd.FRAME_X1, cd.FRAME_Y1)
        pad = 6.0
    ax.set_xlim(x0 - pad, x1 + pad)
    ax.set_ylim(y0 - pad, y1 + pad)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(title, fontsize=11, color="#dddddd")


def compare_png(human, gen, out_png, label=""):
    fig, axes = plt.subplots(1, 2, figsize=(26, 9.2), dpi=130)
    fig.patch.set_facecolor("#12151b")
    render(axes[0], human, u"人間図面  " + label)
    render(axes[1], gen, u"生成図面(中心線エンジン)  " + label)
    fig.suptitle(u"赤=中心線(一点鎖線) / 白=実線 / 橙=かくれ線 / 緑=寸法    " + label,
                 color="#eeeeee", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_png, facecolor="#12151b")
    plt.close(fig)
    return out_png


def main(argv):
    if len(argv) >= 4:
        print(compare_png(argv[1], argv[2], argv[3]))
        return 0
    out_dir = os.path.join(ROOT, u"調査", u"centerline", u"compare")
    os.makedirs(out_dir, exist_ok=True)
    for label, h, g in PAIRS:
        hp, gp = os.path.join(ROOT, h), os.path.join(ROOT, g)
        if not (os.path.exists(hp) and os.path.exists(gp)):
            print(u"skip(入力なし):", label)
            continue
        out = os.path.join(out_dir, label.split()[0] + u"_比較.png")
        compare_png(hp, gp, out, label)
        print(u"wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
