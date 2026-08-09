# -*- coding: utf-8 -*-
u"""出力DXFの構造解析 + スケール検証。

usage: python 調査/analyze_phase2_dxf.py <dxf> <meta.json>
"""
import sys, io, os, json, math, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import ezdxf
from ezdxf.math import Vec3

dxf_path = sys.argv[1]
meta_path = sys.argv[2]
meta = json.load(io.open(meta_path, encoding="utf-8"))

doc = ezdxf.readfile(dxf_path)
print(u"=== ヘッダ ===")
print("dxfversion :", doc.dxfversion, ezdxf.DXF2000 == doc.dxfversion and "(AC1015)" or "")
print("acad_release:", doc.acad_release)
print("$INSUNITS  :", doc.header.get("$INSUNITS"))
print("$EXTMIN/MAX:", doc.header.get("$EXTMIN"), doc.header.get("$EXTMAX"))
print("$MEASUREMENT:", doc.header.get("$MEASUREMENT"))

print(u"\n=== レイヤ ===")
for lay in doc.layers:
    print("  %-22s color=%-4s linetype=%-14s lw=%s" %
          (lay.dxf.name, lay.dxf.color, lay.dxf.linetype,
           getattr(lay.dxf, "lineweight", None)))

print(u"\n=== 線種(LTYPE) ===")
for lt in doc.linetypes:
    print("  %-20s desc=%r pattern_len=%s" %
          (lt.dxf.name, getattr(lt.dxf, "description", ""),
           getattr(lt.dxf, "length", None)))

print(u"\n=== ブロック ===")
for b in doc.blocks:
    n = len(list(b))
    if not b.name.startswith("*") or n:
        print("  %-28s entities=%d" % (b.name, n))

msp = doc.modelspace()
psp_layouts = [l for l in doc.layouts if l.name.lower() != "model"]
print(u"\n=== レイアウト ===")
for l in doc.layouts:
    print("  %-16s entities=%d" % (l.name, len(list(l))))


def survey(space, label):
    cnt = collections.Counter()
    by_layer = collections.Counter()
    by_ltype = collections.Counter()
    lay_lt = collections.Counter()
    for e in space:
        cnt[e.dxftype()] += 1
        by_layer[e.dxf.layer] += 1
        lt = e.dxf.get("linetype", "BYLAYER")
        by_ltype[lt] += 1
        lay_lt[(e.dxf.layer, lt)] += 1
    print(u"\n=== %s: エンティティ種別 ===" % label)
    for k, v in cnt.most_common():
        print("  %-14s %d" % (k, v))
    print(u"--- レイヤ別 ---")
    for k, v in by_layer.most_common():
        print("  %-24s %d" % (k, v))
    print(u"--- 線種別 ---")
    for k, v in by_ltype.most_common():
        print("  %-24s %d" % (k, v))
    print(u"--- (レイヤ, 線種) ---")
    for (l, lt), v in lay_lt.most_common():
        print("  %-24s %-16s %d" % (l, lt, v))
    return cnt


survey(msp, u"modelspace")
for l in psp_layouts:
    survey(l, u"paperspace[%s]" % l.name)


# ---------------------------------------------------------- ジオメトリ抽出
def entity_points(e):
    t = e.dxftype()
    if t == "LINE":
        return [Vec3(e.dxf.start), Vec3(e.dxf.end)]
    if t == "CIRCLE":
        c, r = Vec3(e.dxf.center), e.dxf.radius
        return [c + Vec3(r, 0), c + Vec3(-r, 0), c + Vec3(0, r), c + Vec3(0, -r)]
    if t == "ARC":
        pts = []
        c, r = Vec3(e.dxf.center), e.dxf.radius
        a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
        if a1 < a0:
            a1 += 2 * math.pi
        n = 24
        for i in range(n + 1):
            a = a0 + (a1 - a0) * i / n
            pts.append(c + Vec3(r * math.cos(a), r * math.sin(a)))
        return pts
    if t in ("LWPOLYLINE",):
        return [Vec3(p[0], p[1]) for p in e.get_points("xy")]
    if t == "POLYLINE":
        return [Vec3(v.dxf.location) for v in e.vertices]
    if t in ("ELLIPSE", "SPLINE"):
        try:
            return [Vec3(p) for p in e.flattening(0.05)]
        except Exception:
            return []
    if t in ("POINT",):
        return [Vec3(e.dxf.location)]
    if t in ("TEXT", "MTEXT"):
        return []
    if t == "INSERT":
        pts = []
        for ve in e.virtual_entities():
            pts.extend(entity_points(ve))
        return pts
    return []


space = msp if len(list(msp)) >= sum(len(list(l)) for l in psp_layouts) else psp_layouts[0]
space_name = "modelspace" if space is msp else space.name
print(u"\n=== ジオメトリ解析対象: %s ===" % space_name)

ANNOT_BLOCK_PREFIX = ("SW_CENTERMARKSYMBOL",)

items = []
for e in space:
    pts = entity_points(e)
    if not pts:
        continue
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    is_annot = (e.dxftype() == "INSERT" and
                str(e.dxf.name).upper().startswith(ANNOT_BLOCK_PREFIX))
    items.append({"e": e, "bbox": (min(xs), min(ys), max(xs), max(ys)),
                  "layer": e.dxf.layer, "annot": is_annot,
                  "ltype": e.dxf.get("linetype", "BYLAYER")})

print(u"ジオメトリを持つエンティティ数:", len(items))
allx = [b for it in items for b in (it["bbox"][0], it["bbox"][2])]
ally = [b for it in items for b in (it["bbox"][1], it["bbox"][3])]
print(u"全体範囲: X %.4f .. %.4f  /  Y %.4f .. %.4f" %
      (min(allx), max(allx), min(ally), max(ally)))

# ---------------------------------------------------------- ビュー領域への割当
views = {k: v for k, v in meta["views"].items() if k != "_layout"}
print(u"\n=== ビュー領域への割当(SWのGetOutlineから作った枠) ===")
assigned = collections.defaultdict(list)
unassigned = []
for it in items:
    x0, y0, x1, y1 = it["bbox"]
    hit = None
    for k, v in views.items():
        ox0, oy0, ox1, oy1 = v["outline_mm"]
        if x0 >= ox0 - 1 and x1 <= ox1 + 1 and y0 >= oy0 - 1 and y1 <= oy1 + 1:
            hit = k
            break
    (assigned[hit] if hit else unassigned).append(it)

for k in ("front", "top", "right", "iso"):
    lst = assigned.get(k, [])
    if not lst:
        print("  %-6s : 0 entities" % k)
        continue
    xs = [b for it in lst for b in (it["bbox"][0], it["bbox"][2])]
    ys = [b for it in lst for b in (it["bbox"][1], it["bbox"][3])]
    lt = collections.Counter(it["ltype"] for it in lst)
    ly = collections.Counter(it["layer"] for it in lst)
    et = collections.Counter(it["e"].dxftype() for it in lst)
    print(u"  %-6s : n=%-4d 実測bbox=[%.4f, %.4f, %.4f, %.4f] size=[%.4f, %.4f]"
          % (k, len(lst), min(xs), min(ys), max(xs), max(ys),
             max(xs) - min(xs), max(ys) - min(ys)))
    print(u"            SW予告geom =[%s] size=[%s]"
          % (", ".join("%.4f" % c for c in views[k]["geom_mm"]),
             ", ".join("%.4f" % c for c in views[k]["geom_size_mm"])))
    print(u"            types=%s layers=%s linetypes=%s"
          % (dict(et), dict(ly), dict(lt)))
print(u"  未割当: %d" % len(unassigned))
for it in unassigned[:20]:
    print("     %-12s layer=%-16s bbox=%s" %
          (it["e"].dxftype(), it["layer"],
           ["%.2f" % c for c in it["bbox"]]))

# ---------------------------------------------------------- スケール検証
print(u"\n=== スケール検証① 投影bbox(中心マーク等の注記INSERTを除外) ===")
sx, sy, sz = meta["metrics"]["size_mm"]
expect = {"front": (sx, sy), "top": (sx, sz), "right": (sz, sy)}
worst = 0.0
for k in ("front", "top", "right"):
    ew, eh = expect[k]
    lst = [it for it in assigned.get(k, []) if not it["annot"]]
    if not lst:
        print("  %-6s : ジオメトリ無し" % k)
        worst = float("inf")
        continue
    xs = [b for it in lst for b in (it["bbox"][0], it["bbox"][2])]
    ys = [b for it in lst for b in (it["bbox"][1], it["bbox"][3])]
    mw, mh = max(xs) - min(xs), max(ys) - min(ys)
    ex = abs(mw - ew) / ew * 100.0
    ey = abs(mh - eh) / eh * 100.0
    worst = max(worst, ex, ey)
    print(u"  %-6s 幅 期待%.4f 実測%.6f 誤差%.6f%%   高さ 期待%.4f 実測%.6f 誤差%.6f%%"
          % (k, ew, mw, ex, eh, mh, ey))
print(u"  最大誤差: %.6f%%  → 判定 %s" % (worst, "OK(<0.1%)" if worst < 0.1 else "NG"))

# --- 検証② ModelToViewTransform による厳密予測との突き合わせ ---
print(u"\n=== スケール検証② ModelToViewTransform から算出した予測位置との差 ===")
bx0, by0, bz0, bx1, by1, bz1 = meta["metrics"]["bbox_mm"]
corners = [(x, y, z) for x in (bx0, bx1) for y in (by0, by1) for z in (bz0, bz1)]
worst2 = 0.0
for k in ("front", "top", "right", "iso"):
    a = meta["views"][k]["model_to_view"]
    R = a[0:9]
    T = [a[9] * 1000.0, a[10] * 1000.0]
    s = a[12]
    px, py = [], []
    for (x, y, z) in corners:
        px.append(s * (R[0] * x + R[3] * y + R[6] * z) + T[0])
        py.append(s * (R[1] * x + R[4] * y + R[7] * z) + T[1])
    pred = (min(px), min(py), max(px), max(py))
    lst = [it for it in assigned.get(k, []) if not it["annot"]]
    xs = [b for it in lst for b in (it["bbox"][0], it["bbox"][2])]
    ys = [b for it in lst for b in (it["bbox"][1], it["bbox"][3])]
    meas = (min(xs), min(ys), max(xs), max(ys))
    d = [abs(meas[i] - pred[i]) for i in range(4)]
    worst2 = max(worst2, max(d))
    print(u"  %-6s s=%.6f  予測bbox=[%s]" % (k, s, ", ".join("%.4f" % c for c in pred)))
    print(u"          実測bbox=[%s]  最大差 %.6f mm"
          % (", ".join("%.4f" % c for c in meas), max(d)))
print(u"  最大差: %.6f mm" % worst2)

# --- 検証③ 円の直径(旋盤物の径寸法) ---
print(u"\n=== 円エンティティの直径一覧 ===")
for k in ("front", "top", "right", "iso"):
    circles = [it for it in assigned.get(k, [])
               if it["e"].dxftype() == "CIRCLE" and not it["annot"]]
    if not circles:
        continue
    ds = sorted(round(it["e"].dxf.radius * 2, 6) for it in circles)
    print(u"  %-6s φ %s" % (k, ds))

# --- 検証④ ビュー領域が重ならず分離しているか ---
print(u"\n=== ビュー領域の分離 ===")
boxes = {}
for k in ("front", "top", "right", "iso"):
    lst = assigned.get(k, [])
    xs = [b for it in lst for b in (it["bbox"][0], it["bbox"][2])]
    ys = [b for it in lst for b in (it["bbox"][1], it["bbox"][3])]
    boxes[k] = (min(xs), min(ys), max(xs), max(ys))
    print(u"  %-6s [%.4f, %.4f, %.4f, %.4f]" % ((k,) + boxes[k]))
ks = list(boxes)
ok_sep = True
for i in range(len(ks)):
    for j in range(i + 1, len(ks)):
        a, b = boxes[ks[i]], boxes[ks[j]]
        ov = not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])
        if ov:
            ok_sep = False
            print(u"  ❗重なり: %s x %s" % (ks[i], ks[j]))
print(u"  分離判定: %s" % ("OK(全ペア非重複)" if ok_sep else "NG"))

# --- 検証⑤ 第三角法の整列 ---
print(u"\n=== 第三角法の整列 ===")
fx = (boxes["front"][0] + boxes["front"][2]) / 2
tx = (boxes["top"][0] + boxes["top"][2]) / 2
fy = (boxes["front"][1] + boxes["front"][3]) / 2
ry = (boxes["right"][1] + boxes["right"][3]) / 2
print(u"  正面/平面 の x中心差 = %.6f mm (0なら鉛直整列)" % abs(fx - tx))
print(u"  正面/右側面 の y中心差 = %.6f mm (0なら水平整列)" % abs(fy - ry))
print(u"  平面は正面の上か: %s / 右側面は正面の右か: %s"
      % (boxes["top"][1] > boxes["front"][3], boxes["right"][0] > boxes["front"][2]))

# ---------------------------------------------------------- 隠れ線の区別
print(u"\n=== 隠れ線の区別 ===")
for k in ("front", "top", "right", "iso"):
    lst = assigned.get(k, [])
    lt = collections.Counter(it["ltype"] for it in lst)
    ly = collections.Counter(it["layer"] for it in lst)
    print("  %-6s linetypes=%s layers=%s" % (k, dict(lt), dict(ly)))
