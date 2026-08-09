# -*- coding: utf-8 -*-
"""ブロック再帰展開 + 幾何のみ抽出/描画。
usage:
  python geo.py <dxf> dump  [xmin ymin xmax ymax]
  python geo.py <dxf> draw <out.png> xmin ymin xmax ymax [W] [nodash]
"""
import sys, io, math
import ezdxf

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

path = sys.argv[1]
mode = sys.argv[2]
doc = ezdxf.readfile(path)
msp = doc.modelspace()

GEO = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE", "POINT"}
SKIP_BLOCK_PREFIX = ("*D", "AGM_JISB0031", "AGM_JISB0021", "AGM_JISB0022")  # 溶接記号/仕上/バルーン


def ltype(e):
    l = e.dxf.linetype if e.dxf.hasattr("linetype") else "BYLAYER"
    if l == "BYLAYER":
        try:
            l = doc.layers.get(e.dxf.layer).dxf.linetype
        except Exception:
            l = "CONTINUOUS"
    return l


def color(e):
    c = e.dxf.color if e.dxf.hasattr("color") else 256
    if c == 256:
        try:
            c = doc.layers.get(e.dxf.layer).dxf.color
        except Exception:
            c = 7
    return c


def collect(container, depth=0, out=None, src="msp"):
    if out is None:
        out = []
    for e in container:
        t = e.dxftype()
        if t == "INSERT":
            name = e.dxf.name
            if name.startswith(SKIP_BLOCK_PREFIX):
                continue
            try:
                collect(e.virtual_entities(), depth + 1, out, name)
            except Exception:
                pass
        elif t in GEO:
            out.append((e, src))
    return out


ents = collect(msp)


def segs(e):
    """→ [(x0,y0,x1,y1)...] 直線近似 (円/円弧は分割)"""
    t = e.dxftype()
    r = []
    if t == "LINE":
        s, q = e.dxf.start, e.dxf.end
        r.append((s.x, s.y, q.x, q.y))
    elif t == "LWPOLYLINE":
        pts = [(p[0], p[1]) for p in e.get_points()]
        n = len(pts)
        rng = range(n) if e.closed else range(n - 1)
        for i in rng:
            a, b = pts[i], pts[(i + 1) % n]
            r.append((a[0], a[1], b[0], b[1]))
    elif t == "POLYLINE":
        pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        for i in range(len(pts) - 1):
            r.append((*pts[i], *pts[i + 1]))
    elif t == "CIRCLE":
        c, rad = e.dxf.center, e.dxf.radius
        p = [(c.x + rad * math.cos(a * math.pi / 32), c.y + rad * math.sin(a * math.pi / 32)) for a in range(65)]
        for i in range(64):
            r.append((*p[i], *p[i + 1]))
    elif t == "ARC":
        c, rad = e.dxf.center, e.dxf.radius
        a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
        if a1 <= a0:
            a1 += 2 * math.pi
        n = 48
        p = [(c.x + rad * math.cos(a0 + (a1 - a0) * i / n), c.y + rad * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]
        for i in range(n):
            r.append((*p[i], *p[i + 1]))
    elif t in ("ELLIPSE", "SPLINE"):
        try:
            p = [(v.x, v.y) for v in e.flattening(0.5)]
            for i in range(len(p) - 1):
                r.append((*p[i], *p[i + 1]))
        except Exception:
            pass
    return r


if mode == "dump":
    box = list(map(float, sys.argv[3:7])) if len(sys.argv) >= 7 else None
    rows = []
    for e, src in ents:
        t = e.dxftype()
        lt, cl = ltype(e), color(e)
        if t == "CIRCLE":
            c = e.dxf.center
            if box and not (box[0] <= c.x <= box[2] and box[1] <= c.y <= box[3]):
                continue
            rows.append((f"CIR  c=({c.x:8.2f},{c.y:8.2f}) d={e.dxf.radius*2:8.3f}", lt, cl, src))
        elif t == "ARC":
            c = e.dxf.center
            if box and not (box[0] <= c.x <= box[2] and box[1] <= c.y <= box[3]):
                continue
            rows.append((f"ARC  c=({c.x:8.2f},{c.y:8.2f}) r={e.dxf.radius:8.3f} {e.dxf.start_angle:6.1f}..{e.dxf.end_angle:6.1f}", lt, cl, src))
        else:
            ss = segs(e)
            if not ss:
                continue
            xs = [v for s in ss for v in (s[0], s[2])]
            ys = [v for s in ss for v in (s[1], s[3])]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            if box and not (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
                continue
            if t == "LINE":
                s = ss[0]
                L = math.hypot(s[2] - s[0], s[3] - s[1])
                if L < 1.0:
                    continue
                rows.append((f"LIN  ({s[0]:8.2f},{s[1]:8.2f})-({s[2]:8.2f},{s[3]:8.2f}) L={L:7.2f}", lt, cl, src))
            else:
                pts = [(round(s[0], 2), round(s[1], 2)) for s in ss]
                pts.append((round(ss[-1][2], 2), round(ss[-1][3], 2)))
                rows.append((f"{t[:3]} n={len(pts)} bbox=({min(xs):.2f},{min(ys):.2f})-({max(xs):.2f},{max(ys):.2f}) {pts[:14]}", lt, cl, src))
    rows.sort()
    for txt, lt, cl, src in rows:
        print(f"{txt} [{lt} c{cl}] <{src}>")
    print(f"-- {len(rows)} entities --")

else:
    out = sys.argv[3]
    x0, y0, x1, y1 = map(float, sys.argv[4:8])
    W = float(sys.argv[8]) if len(sys.argv) > 8 else 14.0
    only_cont = len(sys.argv) > 9 and sys.argv[9] == "nodash"
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    matplotlib.rcParams["font.family"] = ["MS Gothic", "Yu Gothic", "DejaVu Sans"]
    asp = (y1 - y0) / (x1 - x0)
    fig = plt.figure(figsize=(W, W * asp), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_aspect("equal")
    STYLE = {"CONTINUOUS": ("-", "#000000", 1.1), "HIDDEN": ((0, (6, 3)), "#0066cc", 0.9),
             "DASHDOT": ((0, (12, 4, 2, 4)), "#cc0000", 0.7), "DIVIDE": ((0, (12, 3, 2, 3, 2, 3)), "#aa00aa", 0.7)}
    groups = {}
    for e, src in ents:
        lt = ltype(e)
        key = lt if lt in STYLE else "CONTINUOUS"
        if only_cont and key != "CONTINUOUS":
            continue
        groups.setdefault(key, []).extend(segs(e))
    for key, ss in groups.items():
        st, col, lw = STYLE[key]
        ax.add_collection(LineCollection([[(s[0], s[1]), (s[2], s[3])] for s in ss],
                                         linestyles=st, colors=col, linewidths=lw))
    # ラベル: 主要テキストを薄く重ねる
    for e in msp:
        if e.dxftype() in ("TEXT", "MTEXT"):
            p = e.dxf.insert
            if x0 <= p.x <= x1 and y0 <= p.y <= y1:
                s = (e.text if e.dxftype() == "MTEXT" else e.dxf.text)
                s = s.replace("\\P", " ").replace("\\A1;", "")
                if "\\T" in s:
                    s = s.split(";")[-1]
                s = "".join(ch for ch in s if not 0xD800 <= ord(ch) <= 0xDFFF)  # 壊れたサロゲートでFT2Fontが落ちる
                ax.text(p.x, p.y, s[:28], fontsize=6, color="#008800")
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_axis_off()
    fig.savefig(out, facecolor="white")
    print("saved", out)
