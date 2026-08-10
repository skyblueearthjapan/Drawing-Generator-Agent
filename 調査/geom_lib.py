# -*- coding: utf-8 -*-
u"""照合用の幾何抽出ライブラリ(人間図面 / SW投影DXF の両方に使う)。

方針
  - INSERT は再帰展開する。ただし寸法のアノニマスブロック(*D..)・中心マーク・
    仕上げ/溶接記号ブロックは除外(CLAUDE.md: 中心マークはbbox計算から除外する)
  - 線種で用途を分ける: 実線=外形 / HIDDEN=隠れ線 / DASHDOT・CENTER=中心線(外形bboxから除外)
    / PHANTOM=接線エッジ(SW出力。外形bboxに含めてよい)
  - 円は CIRCLE と「同一中心・同一半径で合計360度になる ARC 群」の両方を拾う
"""
import math
from collections import defaultdict

GEO_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE", "POINT"}

#: 展開しないブロック(寸法の実体・記号類)
SKIP_BLOCK_PREFIX = ("*D", "AGM_JISB0031", "AGM_JISB0021", "AGM_JISB0022",
                     "SW_CENTERMARKSYMBOL", "*U")

CENTERLINE_LT = {"DASHDOT", "CENTER", "CENTER2", "CENTERX2", "DASHDOT2", "DASHDOTX2",
                 "DIVIDE", "DIVIDE2", "DIVIDEX2"}
HIDDEN_LT = {"HIDDEN", "HIDDEN2", "HIDDENX2", "DASHED", "DASHED2", "DASHEDX2"}
PHANTOM_LT = {"PHANTOM", "PHANTOM2", "PHANTOMX2"}


def linetype_of(doc, e):
    lt = e.dxf.linetype if e.dxf.hasattr("linetype") else "BYLAYER"
    if lt == "BYLAYER":
        try:
            lt = doc.layers.get(e.dxf.layer).dxf.linetype
        except Exception:
            lt = "CONTINUOUS"
    return str(lt).upper()


def kind_of(lt):
    if lt in CENTERLINE_LT:
        return "center"
    if lt in HIDDEN_LT:
        return "hidden"
    if lt in PHANTOM_LT:
        return "tangent"
    return "solid"


def _flatten(doc, entities, out, depth=0):
    u"""INSERT を再帰展開し、**LWPOLYLINE/POLYLINE も LINE/ARC へばらす**。

    ❗ポリラインの bulge(円弧)を無視すると外形bboxが狂う。実際 25154-1-18 の円形ビューの
      外形φ25は「2頂点+bulge=1 の LWPOLYLINE」で描かれており、頂点だけ見ると円と判らない。
      LINE/ARC に展開しておけば bbox も「ARC合計360度=円」判定も同じ経路で正しくなる。
    """
    for e in entities:
        t = e.dxftype()
        if t == "INSERT":
            name = str(e.dxf.name)
            if name.startswith(SKIP_BLOCK_PREFIX) or depth > 4:
                continue
            try:
                _flatten(doc, e.virtual_entities(), out, depth + 1)
            except Exception:
                pass
        elif t in ("LWPOLYLINE", "POLYLINE"):
            try:
                sub = list(e.virtual_entities())
            except Exception:
                sub = []
            if sub:
                for s in sub:
                    # 展開後の線種は元エンティティに合わせる(BYLAYER のままだと誤判定する)
                    try:
                        if e.dxf.hasattr("linetype"):
                            s.dxf.linetype = e.dxf.linetype
                        s.dxf.layer = e.dxf.layer
                    except Exception:
                        pass
                out.extend(sub)
            else:
                out.append(e)
        elif t in GEO_TYPES:
            out.append(e)
    return out


def collect(doc, entities=None):
    u"""(entity, kind) のリストを返す。kind は solid/hidden/center/tangent。"""
    src = list(entities) if entities is not None else list(doc.modelspace())
    ents = _flatten(doc, src, [])
    return [(e, kind_of(linetype_of(doc, e))) for e in ents]


def _pts_of(e):
    u"""エンティティを代表点列に落とす(bbox用)。"""
    t = e.dxftype()
    try:
        if t == "LINE":
            return [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        if t == "LWPOLYLINE":
            return [(p[0], p[1]) for p in e.get_points()]
        if t == "POLYLINE":
            return [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        if t == "CIRCLE":
            c, r = e.dxf.center, e.dxf.radius
            return [(c.x - r, c.y - r), (c.x + r, c.y + r), (c.x - r, c.y + r), (c.x + r, c.y - r)]
        if t == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
            if a1 <= a0:
                a1 += 2 * math.pi
            # ❗弦近似の刻みを固定角にすると外形が縮む(r=24 の半円を0.2rad刻みにすると
            #   両側で 0.26mm 小さく測れて「外形不一致」の誤判定になる)。矢高0.005mm基準で刻む。
            tol = 0.005
            dth = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tol / r))) if r > tol else 0.5
            n = max(8, int(math.ceil((a1 - a0) / dth)))
            return [(c.x + r * math.cos(a0 + (a1 - a0) * i / n),
                     c.y + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]
        if t == "ELLIPSE":
            return [(p.x, p.y) for p in e.flattening(0.05)]
        if t == "SPLINE":
            return [(p.x, p.y) for p in e.flattening(0.05)]
        if t == "POINT":
            return [(e.dxf.location.x, e.dxf.location.y)]
    except Exception:
        return []
    return []


def bbox_of(pairs, kinds=("solid", "hidden", "tangent")):
    xs, ys = [], []
    for e, k in pairs:
        if k not in kinds:
            continue
        for x, y in _pts_of(e):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def cluster_views(pairs, gap=12.0, kinds=("solid", "hidden", "tangent")):
    u"""エンティティを空間的にクラスタリングしてビュー候補に分ける(bbox の距離 < gap で連結)。"""
    items = []
    for e, k in pairs:
        if k not in kinds:
            continue
        pts = _pts_of(e)
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        items.append({"e": e, "kind": k,
                      "bb": [min(xs), min(ys), max(xs), max(ys)]})
    n = len(items)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            a, b = items[i]["bb"], items[j]["bb"]
            dx = max(0.0, max(a[0] - b[2], b[0] - a[2]))
            dy = max(0.0, max(a[1] - b[3], b[1] - a[3]))
            if math.hypot(dx, dy) < gap:
                union(i, j)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(items[i])

    out = []
    for g in groups.values():
        xs0 = min(it["bb"][0] for it in g)
        ys0 = min(it["bb"][1] for it in g)
        xs1 = max(it["bb"][2] for it in g)
        ys1 = max(it["bb"][3] for it in g)
        out.append({
            "bbox": [xs0, ys0, xs1, ys1],
            "size": [xs1 - xs0, ys1 - ys0],
            "n": len(g),
            "entities": [(it["e"], it["kind"]) for it in g],
        })
    out.sort(key=lambda c: -(c["size"][0] * c["size"][1]))
    return out


def circles_of(pairs, kinds=("solid", "hidden")):
    u"""円(CIRCLE)と、同心・同半径で360度を構成する ARC 群を (cx, cy, r, kind) で返す。"""
    res = []
    arc_acc = defaultdict(float)
    arc_c = {}
    for e, k in pairs:
        if k not in kinds:
            continue
        t = e.dxftype()
        if t == "CIRCLE":
            res.append((e.dxf.center.x, e.dxf.center.y, e.dxf.radius, k))
        elif t == "ARC":
            c = e.dxf.center
            key = (round(c.x, 3), round(c.y, 3), round(e.dxf.radius, 4), k)
            a0, a1 = e.dxf.start_angle, e.dxf.end_angle
            sweep = (a1 - a0) % 360.0
            arc_acc[key] += sweep
            arc_c[key] = (c.x, c.y, e.dxf.radius, k)
    for key, tot in arc_acc.items():
        if tot > 355.0:
            res.append(arc_c[key])
    return res
