# -*- coding: utf-8 -*-
import sys, io, os, json
from collections import defaultdict, Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
import ezdxf
from engine.frame_extract import subtract_frame, load_frame_signatures

GEOM_TYPES = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "SPLINE", "ELLIPSE", "POLYLINE"}


def entity_repr_points(e):
    t = e.dxftype()
    try:
        if t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            return [(s.x, s.y), (en.x, en.y)]
        elif t == "CIRCLE":
            c = e.dxf.center
            return [(c.x, c.y)]
        elif t == "ARC":
            c = e.dxf.center
            return [(c.x, c.y)]
        elif t == "LWPOLYLINE":
            pts = e.get_points()
            return [(p[0], p[1]) for p in pts]
        elif t == "SPLINE":
            cps = list(e.control_points)
            return [(p[0], p[1]) for p in cps[:4]] if cps else []
        elif t == "ELLIPSE":
            c = e.dxf.center
            return [(c.x, c.y)]
        elif t == "POLYLINE":
            return [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
    except Exception:
        return []
    return []


def cluster_views(geom_points, gap=18.0):
    if not geom_points:
        return 0, []
    cell = gap
    grid = defaultdict(list)
    for i, (x, y) in enumerate(geom_points):
        grid[(int(x // cell), int(y // cell))].append(i)
    parent = list(range(len(geom_points)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    gap2 = gap * gap
    for (cx, cy), idxs in grid.items():
        neighbor_idxs = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbor_idxs.extend(grid.get((cx + dx, cy + dy), []))
        for i in idxs:
            xi, yi = geom_points[i]
            for j in neighbor_idxs:
                if j <= i:
                    continue
                xj, yj = geom_points[j]
                if (xi - xj) ** 2 + (yi - yj) ** 2 <= gap2:
                    union(i, j)
    counts = Counter(find(i) for i in range(len(geom_points)))
    sizes = sorted(counts.values(), reverse=True)
    return len(counts), sizes


BASE = "."
_, frame_sigs = load_frame_signatures(os.path.join(BASE, "図枠", "frame_template.dxf"))

with open(os.path.join(BASE, "調査", "bucket_A_files.json"), encoding="utf-8") as f:
    bucket = json.load(f)
sample_paths = []
for axis, paths in bucket.items():
    sample_paths.extend(paths[:2])

for gap in (18, 30, 45, 60, 80, 100):
    print(f"--- gap={gap} ---")
    for rel in sample_paths[:10]:
        full = os.path.join(BASE, rel)
        doc = ezdxf.readfile(full)
        remaining, summary = subtract_frame(doc, frame_signatures=frame_sigs)
        pts = []
        for e in remaining:
            if e.dxftype() in GEOM_TYPES:
                pts.extend(entity_repr_points(e))
        n, sizes = cluster_views(pts, gap=gap)
        # ノイズ除去: サイズ1-2の孤立点(単独円など小穴)を除いた「主要クラスタ」数も見る
        big = [s for s in sizes if s >= 4]
        print(f"  {os.path.basename(full)[:30]:30s} n_clusters={n:4d} n_big(>=4pts)={len(big):3d} top5sizes={sizes[:5]}")
