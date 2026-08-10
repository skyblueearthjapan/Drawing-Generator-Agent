# -*- coding: utf-8 -*-
"""
「読みやすさの様式」分析(観点1・5・6の定量化)。

観点1: 寸法の配置分散(径・長さ・位置寸法がビューの上下左右にどう散るか)
観点5: 寸法密度(1ビューあたりの寸法本数の分布)
観点6: 同一寸法線への直列連記・累進/並列の使い分け

engine/ 配下は変更しない(読み取りimportのみ)。frame_extract.subtract_frame / 既存の
調査/analyze_style_corpus.py のクラスタリング手法を踏襲・拡張する。
"""
import sys
import os
import io
import json
import math
from collections import defaultdict, Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ezdxf
from engine.frame_extract import subtract_frame, load_frame_signatures
from analyze_style_corpus import entity_repr_points  # noqa: E402

BASE = os.path.join(os.path.dirname(__file__), "..")
FRAME_TEMPLATE = os.path.join(BASE, "図枠", "frame_template.dxf")

GEOM_TYPES = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "SPLINE", "ELLIPSE", "POLYLINE"}
GAP_VIEW = 45.0     # ビュークラスタの近傍閾値(mm)。既存分析(dimension_style_analysis.md §5.1)と同じ
MIN_ISLAND = 4       # このノード数未満のクラスタはノイズとして無視


# ---------------- クラスタリング(entity付き。既存cluster_viewsを拡張) ----------------
def cluster_entities(remaining_entities):
    """remaining_entities中のGEOM_TYPESエンティティを空間クラスタ化し、
    各クラスタの bbox と所属エンティティを返す。

    戻り値: list of {"bbox":(x0,y0,x1,y1), "entities":[...], "n_points":int}
    """
    pts = []          # (x,y)
    owner = []         # 対応するエンティティのindex(entities内)
    ents = [e for e in remaining_entities if e.dxftype() in GEOM_TYPES]
    for i, e in enumerate(ents):
        for p in entity_repr_points(e):
            pts.append(p)
            owner.append(i)

    if not pts:
        return []

    cell = GAP_VIEW
    grid = defaultdict(list)
    for i, (x, y) in enumerate(pts):
        grid[(int(x // cell), int(y // cell))].append(i)

    parent = list(range(len(pts)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    gap2 = cell * cell
    for (cx, cy), idxs in grid.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(grid.get((cx + dx, cy + dy), []))
        for i in idxs:
            xi, yi = pts[i]
            for j in neigh:
                if j <= i:
                    continue
                xj, yj = pts[j]
                if (xi - xj) ** 2 + (yi - yj) ** 2 <= gap2:
                    union(i, j)

    clusters = defaultdict(list)  # root -> [point idx]
    for i in range(len(pts)):
        clusters[find(i)].append(i)

    out = []
    for root, idxs in clusters.items():
        if len(idxs) < MIN_ISLAND:
            continue
        xs = [pts[i][0] for i in idxs]
        ys = [pts[i][1] for i in idxs]
        ent_idx = sorted(set(owner[i] for i in idxs))
        out.append({
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
            "entities": [ents[i] for i in ent_idx],
            "n_points": len(idxs),
        })
    # 大きい順(=ビューらしい順)にソート
    out.sort(key=lambda c: -(c["bbox"][2] - c["bbox"][0]) * (c["bbox"][3] - c["bbox"][1]))
    return out


def bbox_center(bb):
    return ((bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0)


def nearest_cluster(pt, clusters):
    if not clusters:
        return None
    px, py = pt
    best, bd = None, None
    for ci, c in enumerate(clusters):
        x0, y0, x1, y1 = c["bbox"]
        dx = max(x0 - px, 0.0, px - x1)
        dy = max(y0 - py, 0.0, py - y1)
        d = math.hypot(dx, dy)
        if bd is None or d < bd:
            bd, best = d, ci
    return best


def classify_side(anchor, bbox):
    """anchor(寸法文字/ラベル位置)がビューbboxの中心に対しどちら側(top/bottom/left/right)かを
    正規化距離(bboxの半径基準)の支配軸で判定する。"""
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    hw = max((x1 - x0) / 2.0, 1e-6)
    hh = max((y1 - y0) / 2.0, 1e-6)
    dx = (anchor[0] - cx) / hw
    dy = (anchor[1] - cy) / hh
    if abs(dy) >= abs(dx):
        return "top" if dy > 0 else "bottom"
    else:
        return "right" if dx > 0 else "left"


def dim_kind(e, doc):
    dimtype_base = e.dimtype & 0x0F
    if dimtype_base == 3:
        return "diameter_native"
    if dimtype_base == 4:
        return "radius"
    if dimtype_base == 2:
        return "angular"
    # linear/rotated: dimpost を見て直径(線形+%%c)かどうか判定
    try:
        ds = doc.dimstyles.get(e.dxf.dimstyle)
        post = (ds.dxf.get("dimpost") or "")
    except Exception:
        post = ""
    if "%%c" in post or "Ø" in post:
        return "diameter_linear"
    if post.strip().upper().startswith("R"):
        return "radius_linear"
    return "length"


def dim_direction(e):
    """線形寸法の主軸(horizontal/vertical/oblique)。defpoint2-defpoint3の方向。"""
    try:
        p2 = e.dxf.defpoint2
        p3 = e.dxf.defpoint3
    except Exception:
        return None
    dx, dy = (p3.x - p2.x), (p3.y - p2.y)
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None
    ax, ay = abs(dx) / L, abs(dy) / L
    if ax >= 0.9:
        return "horizontal"
    if ay >= 0.9:
        return "vertical"
    return "oblique"


# ---------------- 観点6: 直列連記(chain) / 累進・並列(baseline stack) ----------------
def analyze_chaining(dims_in_view):
    """view内の線形寸法を horizontal/vertical に分け、
    (a) 同一寸法線上で端点が連続する「直列(チェーン)」の本数
    (b) 起点を共有し寸法線レベルが異なる「並列(基準積み)」の本数
    (c) どちらでもない「孤立」寸法の本数
    を数える。"""
    TOL_LEVEL = 3.0   # 同一寸法線とみなすy(またはx)許容差 mm
    TOL_TOUCH = 3.0   # 端点が連続しているとみなす許容差 mm
    TOL_DATUM = 3.0   # 起点共有とみなす許容差 mm

    result = {"horizontal": {"chain_dims": 0, "chain_groups": 0, "baseline_dims": 0,
                              "baseline_groups": 0, "isolated": 0, "total": 0},
              "vertical": {"chain_dims": 0, "chain_groups": 0, "baseline_dims": 0,
                           "baseline_groups": 0, "isolated": 0, "total": 0}}

    for direction in ("horizontal", "vertical"):
        items = []
        for d in dims_in_view:
            if d["direction"] != direction:
                continue
            p2, p3, dp = d["p2"], d["p3"], d["defpoint"]
            if direction == "horizontal":
                level = dp[1]
                a, b = sorted([p2[0], p3[0]])
                datum = p2[0]
            else:
                level = dp[0]
                a, b = sorted([p2[1], p3[1]])
                datum = p2[1]
            items.append({"level": level, "a": a, "b": b, "datum": datum, "id": d["id"]})

        result[direction]["total"] = len(items)
        if len(items) < 2:
            result[direction]["isolated"] = len(items)
            continue

        # レベル(寸法線の高さ/横位置)でグルーピング
        items_sorted = sorted(items, key=lambda x: x["level"])
        level_groups = []
        cur = [items_sorted[0]]
        for it in items_sorted[1:]:
            if abs(it["level"] - cur[-1]["level"]) <= TOL_LEVEL:
                cur.append(it)
            else:
                level_groups.append(cur)
                cur = [it]
        level_groups.append(cur)

        chained_ids = set()
        for grp in level_groups:
            grp_sorted = sorted(grp, key=lambda x: x["a"])
            chain_run = [grp_sorted[0]]
            for it in grp_sorted[1:]:
                if abs(it["a"] - chain_run[-1]["b"]) <= TOL_TOUCH:
                    chain_run.append(it)
                else:
                    if len(chain_run) >= 2:
                        result[direction]["chain_groups"] += 1
                        for x in chain_run:
                            chained_ids.add(x["id"])
                    chain_run = [it]
            if len(chain_run) >= 2:
                result[direction]["chain_groups"] += 1
                for x in chain_run:
                    chained_ids.add(x["id"])
        result[direction]["chain_dims"] = len(chained_ids)

        # 基準(datum)共有×レベル違い = 並列/累進スタック
        remaining = [it for it in items if it["id"] not in chained_ids]
        datum_sorted = sorted(remaining, key=lambda x: x["datum"])
        used = set()
        baseline_ids = set()
        for i, it in enumerate(datum_sorted):
            if it["id"] in used:
                continue
            group = [it]
            for jt in datum_sorted[i + 1:]:
                if jt["id"] in used:
                    continue
                if abs(jt["datum"] - it["datum"]) <= TOL_DATUM and jt["level"] != it["level"]:
                    group.append(jt)
            if len(group) >= 2:
                result[direction]["baseline_groups"] += 1
                for x in group:
                    used.add(x["id"])
                    baseline_ids.add(x["id"])
        result[direction]["baseline_dims"] = len(baseline_ids)
        result[direction]["isolated"] = len(items) - len(chained_ids) - len(baseline_ids)

    return result


def analyze_file(path, frame_sigs, tag=""):
    doc = ezdxf.readfile(path)
    remaining, fsummary = subtract_frame(doc, frame_signatures=frame_sigs)
    clusters = cluster_entities(remaining)

    dim_records = []
    did = 0
    for e in remaining:
        if e.dxftype() != "DIMENSION":
            continue
        try:
            tm = e.dxf.text_midpoint
            anchor = (tm.x, tm.y)
        except Exception:
            dp = e.dxf.defpoint
            anchor = (dp.x, dp.y)
        ci = nearest_cluster(anchor, clusters)
        if ci is None:
            continue
        kind = dim_kind(e, doc)
        direction = dim_direction(e)
        rec = {"id": did, "kind": kind, "view": ci, "anchor": anchor,
               "side": classify_side(anchor, clusters[ci]["bbox"]),
               "direction": direction}
        try:
            p2 = e.dxf.defpoint2
            p3 = e.dxf.defpoint3
            dp = e.dxf.defpoint
            rec["p2"] = (p2.x, p2.y)
            rec["p3"] = (p3.x, p3.y)
            rec["defpoint"] = (dp.x, dp.y)
        except Exception:
            rec["p2"] = rec["p3"] = rec["defpoint"] = None
        dim_records.append(rec)
        did += 1

    # 観点1: kind x side 集計(全体)
    kind_side = Counter((d["kind"], d["side"]) for d in dim_records)

    # 観点5: ビュー毎の寸法数
    per_view_counts = Counter(d["view"] for d in dim_records)
    view_dim_counts = [per_view_counts.get(i, 0) for i in range(len(clusters))]

    # 観点6: ビュー毎に直列/並列判定
    chaining_per_view = []
    for vi in range(len(clusters)):
        dv = [d for d in dim_records if d["view"] == vi and d["direction"] in ("horizontal", "vertical")
              and d["p2"] is not None]
        chaining_per_view.append(analyze_chaining(dv))

    return {
        "path": path, "tag": tag,
        "frame_summary": fsummary,
        "n_views": len(clusters),
        "view_bboxes": [c["bbox"] for c in clusters],
        "n_dims": len(dim_records),
        "kind_side_counts": {f"{k}|{s}": v for (k, s), v in kind_side.items()},
        "view_dim_counts": view_dim_counts,
        "chaining_per_view": chaining_per_view,
        "dim_records": [{k: v for k, v in d.items() if k not in ("p2", "p3", "defpoint")}
                         for d in dim_records],
    }


CORPUS = [
    ("human", "1-27", r"荏原トライ調整用/DXF/部品表用DXFデータ/1.走行軸/25154-1-27_走行フレーム踏板.dxf"),
    ("human", "1-03", r"荏原トライ調整用/DXF/部品表用DXFデータ/1.走行軸/25154-1-03_ジャッキプレート.dxf"),
    ("human", "1-08", r"荏原トライ調整用/DXF/部品表用DXFデータ/1.走行軸/25154-1-08_シャフト.dxf"),
    ("human", "1-09", r"荏原トライ調整用/DXF/部品表用DXFデータ/1.走行軸/25154-1-09_エンドプレート.dxf"),
    ("human", "1-18", r"荏原トライ調整用/DXF/部品表用DXFデータ/1.走行軸/25154-1-18_クッション.dxf"),
    ("human", "2-16", r"荏原トライ調整用/DXF/部品表用DXFデータ/2.ターン軸/25154-2-16_指針.dxf"),
    ("human", "2-06", r"荏原トライ調整用/DXF/部品表用DXFデータ/2.ターン軸/25154-2-06_モータフランジ.dxf"),
    ("human", "2-11", r"荏原トライ調整用/DXF/部品表用DXFデータ/2.ターン軸/25154-2-11_LSブラケット.dxf"),
    ("human", "3-02", r"荏原トライ調整用/DXF/部品表用DXFデータ/3.昇降軸/25154-3-02_モータブラケット.dxf"),
    ("human", "3-04", r"荏原トライ調整用/DXF/部品表用DXFデータ/3.昇降軸/25154-3-04_ベアリングケース.dxf"),
    ("human", "3-05", r"荏原トライ調整用/DXF/部品表用DXFデータ/3.昇降軸/25154-3-05_ベアリングカラー.dxf"),
    ("human", "3-13", r"荏原トライ調整用/DXF/部品表用DXFデータ/3.昇降軸/25154-3-13_クッション.dxf"),
    ("human", "4-05", r"荏原トライ調整用/DXF/部品表用DXFデータ/4.前後軸/25154-4-05_駆動ユニットブラケット.dxf"),
    ("human", "4-07", r"荏原トライ調整用/DXF/部品表用DXFデータ/4.前後軸/25154-4-07_減速機ブラケット.dxf"),
    ("human", "4-13", r"荏原トライ調整用/DXF/部品表用DXFデータ/4.前後軸/25154-4-13_押しボルト座.dxf"),
    ("human", "5-05", r"荏原トライ調整用/DXF/部品表用DXFデータ/5.ひねり軸/25154-5-05_減速機フランジ.dxf"),
    ("human", "5-07", r"荏原トライ調整用/DXF/部品表用DXFデータ/5.ひねり軸/25154-5-07_エンドプレート.dxf"),
    ("human", "5-08", r"荏原トライ調整用/DXF/部品表用DXFデータ/5.ひねり軸/25154-5-08_メカストッパー受け.dxf"),
    ("human", "6-02", r"荏原トライ調整用/DXF/部品表用DXFデータ/6.傾動軸/25154-6-02_傾動面板.dxf"),
    ("human", "6-10", r"荏原トライ調整用/DXF/部品表用DXFデータ/6.傾動軸/25154-6-10_押しボルト座.dxf"),
    ("human", "7-05", r"荏原トライ調整用/DXF/部品表用DXFデータ/7.回転軸/25154-7-05_ホルダー.dxf"),
    ("human", "7-06", r"荏原トライ調整用/DXF/部品表用DXFデータ/7.回転軸/25154-7-06_LSブラケット.dxf"),
]

GENERATED = [
    ("generated", "1-27", r"data/納品箱/BLIND2-25154-1-27/25154-1-27_走行フレーム踏板.dxf"),
    ("generated", "2-16", r"data/納品箱/BLIND2-25154-2-16/25154-2-16_指針.dxf"),
    ("generated", "3-02", r"data/納品箱/BLIND2-25154-3-02/25154-3-02_モータブラケット.dxf"),
    ("generated", "4-05", r"data/納品箱/BLIND2-25154-4-05/25154-4-05_駆動ユニットブラケット.dxf"),
    ("generated", "5-05", r"data/納品箱/BLIND2-25154-5-05/25154-5-05_減速機フランジ.dxf"),
]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _, frame_sigs = load_frame_signatures(FRAME_TEMPLATE)

    all_results = {"human": [], "generated": [], "errors": []}
    for group, tag, rel in CORPUS + GENERATED:
        full = os.path.join(BASE, rel)
        try:
            r = analyze_file(full, frame_sigs, tag=tag)
            all_results[group].append(r)
            print(f"OK {group}/{tag}: views={r['n_views']} dims={r['n_dims']}")
        except Exception as ex:
            all_results["errors"].append({"path": full, "tag": tag, "error": str(ex)})
            print(f"ERR {group}/{tag}: {ex}")

    out_path = os.path.join(BASE, "調査", "style_pattern_raw.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=1)
    print("DONE ->", out_path)


if __name__ == "__main__":
    main()
