# -*- coding: utf-8 -*-
u"""出力DXFの目視確認用PNG。線種で色分けし、ビュー領域枠と実測寸法を重ねて描く。

usage: python 調査/render_phase2_dxf.py <dxf> <meta.json> <out.png>
"""
import sys, io, os, json, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import ezdxf
import matplotlib
matplotlib.use("Agg")
# ❗"MS Gothic"(msgothic.ttc)は matplotlib 3.11 で1文字も描画されない。Meiryo/Yu Gothic は出る
matplotlib.rcParams["font.family"] = "Meiryo"
matplotlib.rcParams["axes.unicode_minus"] = False
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

dxf_path, meta_path, out_png = sys.argv[1], sys.argv[2], sys.argv[3]
meta = json.load(io.open(meta_path, encoding="utf-8"))
doc = ezdxf.readfile(dxf_path)
msp = doc.modelspace()

STYLE = {"Continuous": dict(color="#111111", lw=1.2, ls="-"),
         "HIDDEN":     dict(color="#c81e1e", lw=0.9, ls=(0, (5, 3))),
         "PHANTOM":    dict(color="#1e6fc8", lw=0.9, ls=(0, (8, 3, 2, 3))),
         "CENTER":     dict(color="#1e9e5a", lw=0.9, ls=(0, (10, 3, 2, 3)))}
ANNOT = dict(color="#9a6ac8", lw=0.8, ls="-")

fig, ax = plt.subplots(figsize=(16, 11), dpi=110)


def style_of(e, annot=False):
    if annot:
        return ANNOT
    lt = e.dxf.get("linetype", "Continuous")
    return STYLE.get(lt, dict(color="#888888", lw=0.8, ls=":"))


def draw(e, annot=False):
    st = style_of(e, annot)
    t = e.dxftype()
    if t == "LINE":
        s, en = e.dxf.start, e.dxf.end
        ax.plot([s[0], en[0]], [s[1], en[1]], **st)
    elif t == "CIRCLE":
        c, r = e.dxf.center, e.dxf.radius
        a = [i * math.pi / 90 for i in range(181)]
        ax.plot([c[0] + r * math.cos(t_) for t_ in a],
                [c[1] + r * math.sin(t_) for t_ in a], **st)
    elif t == "ARC":
        c, r = e.dxf.center, e.dxf.radius
        a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
        if a1 < a0:
            a1 += 2 * math.pi
        a = [a0 + (a1 - a0) * i / 64 for i in range(65)]
        ax.plot([c[0] + r * math.cos(t_) for t_ in a],
                [c[1] + r * math.sin(t_) for t_ in a], **st)
    elif t == "LWPOLYLINE":
        pts = list(e.get_points("xy"))
        if e.closed:
            pts = pts + [pts[0]]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], **st)
    elif t == "POLYLINE":
        pts = [v.dxf.location for v in e.vertices]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], **st)
    elif t in ("ELLIPSE", "SPLINE"):
        pts = list(e.flattening(0.02))
        ax.plot([p[0] for p in pts], [p[1] for p in pts], **st)
    elif t == "INSERT":
        an = str(e.dxf.name).upper().startswith("SW_CENTERMARKSYMBOL")
        for ve in e.virtual_entities():
            draw(ve, annot=an)


for e in msp:
    draw(e)

# --- ビュー領域枠 ---
for k in ("front", "top", "right", "iso"):
    v = meta["views"][k]
    x0, y0, x1, y1 = v["outline_mm"]
    ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False,
                           edgecolor="#3aa0ff", lw=0.8, ls="--"))
    ax.text(x0, y1 + 3, u"%s (%s)  scale=%.3g" % (k, v["view_name"], v["scale_decimal"]),
            fontsize=9, color="#1a6fbf")

# --- 用紙枠 ---
sw_, sh_ = meta["sheet"]["sheet_wh_mm"]
ax.add_patch(Rectangle((0, 0), sw_, sh_, fill=False, edgecolor="#666666", lw=1.0))

m = meta["metrics"]
ax.set_title(u"%s   mass=%.4f kg  vol=%.1f mm^3  bbox=%.1f x %.1f x %.1f mm  (A1, 1:1, 第三角法)"
             % (os.path.splitext(os.path.basename(meta["part_path"]))[0],
                m["mass_kg"], m["volume_mm3"], *m["size_mm"]),
             fontsize=11)
ax.set_aspect("equal")
ax.set_xlim(-10, sw_ + 10)
ax.set_ylim(-10, sh_ + 10)
ax.grid(True, lw=0.3, alpha=0.3)

handles = [plt.Line2D([], [], label=u"CONTINUOUS(実線)", **STYLE["Continuous"]),
           plt.Line2D([], [], label=u"HIDDEN(隠れ線)", **STYLE["HIDDEN"]),
           plt.Line2D([], [], label=u"SW_CENTERMARKSYMBOL(中心マーク/INSERT)", **ANNOT)]
ax.legend(handles=handles, loc="upper left", fontsize=9)

fig.savefig(out_png, facecolor="white", bbox_inches="tight")
print("saved", out_png)
