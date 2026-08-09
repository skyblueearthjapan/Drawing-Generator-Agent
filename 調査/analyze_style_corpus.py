# -*- coding: utf-8 -*-
"""
フェーズ3-A: バケットA(80枚)の寸法記入流儀を機械抽出・統計化する。
出力: 調査/style_corpus_raw.json (全生データ、報告書生成の入力)

観点1〜7(調査/フェーズ3設計メモ_寸法記入エンジン.md)を一括で走査する。
"""
import sys
import io
import os
import re
import json
import math
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ezdxf
from engine.frame_extract import subtract_frame, load_frame_signatures

BASE = os.path.join(os.path.dirname(__file__), "..")
FRAME_TEMPLATE = os.path.join(BASE, "図枠", "frame_template.dxf")
BUCKET_A = os.path.join(BASE, "調査", "bucket_A_files.json")
OUT = os.path.join(BASE, "調査", "style_corpus_raw.json")

FIELD_ANCHORS_TOL = 3.0  # mm。fields.json記載の可変フィールド座標に一致するMTEXTは表題欄値として除外
with open(os.path.join(BASE, "図枠", "fields.json"), encoding="utf-8") as f:
    FIELDS = json.load(f)["fields"]
FIELD_ANCHORS = [tuple(f["anchor"]) for f in FIELDS]

TITLE_BLOCK_Y_MAX = 31.0  # 表題欄本体(下部)の上端。これ未満は図枠差し引きで既に除去されているはずだが保険
NOTE_STRIP_Y_MIN = 262.0  # シート左上の可変ノート/連番マーク帯。ここは表題欄類として別扱い

GEOM_TYPES = {"LINE", "ARC", "CIRCLE", "LWPOLYLINE", "SPLINE", "ELLIPSE", "POLYLINE"}


def is_field_anchor(x, y):
    for (fx, fy) in FIELD_ANCHORS:
        if abs(x - fx) <= FIELD_ANCHORS_TOL and abs(y - fy) <= FIELD_ANCHORS_TOL:
            return True
    return False


def mtext_plain(text):
    """MTEXTの書式コード(\\A1; \\T..; \\P \\W..; %%c %%p 等)をおおまかに除去した素のテキスト"""
    t = text
    t = re.sub(r"\\[AWT][^;]*;", "", t)
    t = t.replace("\\P", " ")
    t = re.sub(r"\{|\}", "", t)
    t = re.sub(r"\\U\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), t)
    return t


# ---------- 穴・タップ注記パターン ----------
HOLE_PATTERNS = [
    ("count_phi", re.compile(r"\d+[-－]\s*(?:%%c|φ|Φ)\s*\d+(?:\.\d+)?")),
    ("phi_only", re.compile(r"(?:%%c|φ|Φ)\s*\d+(?:\.\d+)?")),
    ("metric_thread", re.compile(r"[MＭmｍ]\d+(?:[×xX]\d+(?:\.\d+)?)?")),
    ("count_metric_thread", re.compile(r"\d+[-－]\s*[MＭmｍ]\d+")),
    ("pcd", re.compile(r"PCD\s*\d+(?:\.\d+)?", re.IGNORECASE)),
    ("through_zaguri", re.compile(r"通しザグリ")),
    ("fukazaguri", re.compile(r"深ザグリ")),
    ("zaguri", re.compile(r"ザグリ")),
    ("kiri", re.compile(r"キリ")),
    ("through_hole", re.compile(r"通し")),
    ("depth", re.compile(r"深さ\s*\d+(?:\.\d+)?")),
    ("od", re.compile(r"OD\s*\d+(?:\.\d+)?", re.IGNORECASE)),
    ("pilot_hole", re.compile(r"下穴")),
    ("tapped_depth_T", re.compile(r"\d+[TＴ]\b")),
]

TOLERANCE_PATTERNS = [
    ("plus_minus_unicode", re.compile(r"[±]\s*\d+(?:\.\d+)?")),
    ("plus_minus_pctcode", re.compile(r"%%p\s*\d+(?:\.\d+)?")),
    ("plus_minus_asym_text", re.compile(r"[+＋]\d+(?:\.\d+)?\s*[-－]\d+(?:\.\d+)?")),
    ("iso_fit", re.compile(r"(?<![%A-Za-z0-9])[HhGgJjKkMmNnPpRrSsTtUuXx][0-9]{1,2}(?![0-9])")),
]

NOTE_KEYWORDS = ["塗装", "バリ取", "面取", "熱処理", "メッキ", "めっき", "焼き入れ", "研磨",
                  "公差", "指定なき", "指示なき", "注記", "全周", "溶接"]


def classify_hole_text(plain):
    hits = []
    for name, pat in HOLE_PATTERNS:
        ms = pat.findall(plain)
        for m in ms:
            hits.append((name, m))
    return hits


def classify_tolerance_text(plain):
    hits = []
    for name, pat in TOLERANCE_PATTERNS:
        ms = pat.findall(plain)
        for m in ms:
            hits.append((name, m))
    return hits


# ---------- ビュークラスタリング(空間) ----------
def cluster_views(geom_points, gap=18.0):
    """geom_points: [(x,y), ...] 代表点群。グリッドハッシュ+Union-Findで近傍クラスタ化してクラスタ数を返す。"""
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
    cells = list(grid.keys())
    for (cx, cy) in cells:
        idxs = grid[(cx, cy)]
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

    roots = set(find(i) for i in range(len(geom_points)))
    # クラスタごとの点数(ノイズ除去用)
    counts = Counter(find(i) for i in range(len(geom_points)))
    sizes = sorted(counts.values(), reverse=True)
    return len(roots), sizes


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


def bbox_of_points(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def dist_point_to_bbox(px, py, bbox):
    x0, y0, x1, y1 = bbox
    dx = max(x0 - px, 0, px - x1)
    dy = max(y0 - py, 0, py - y1)
    return math.hypot(dx, dy)


def leader_angle_deg(v0, v1):
    dx = v1[0] - v0[0]
    dy = v1[1] - v0[1]
    return math.degrees(math.atan2(dy, dx))


def analyze_file(path, frame_sigs):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    remaining, summary = subtract_frame(doc, frame_signatures=frame_sigs)

    result = {
        "path": path,
        "frame_summary": summary,
        "dimensions": [],
        "leaders": [],
        "hole_note_hits": [],
        "tolerance_hits": [],
        "notes_candidates": [],
        "agm_blocks": [],
        "centerline_lines": 0,
        "centerline_circles": 0,
        "geom_entity_count": 0,
        "view_cluster_sizes": [],
        "n_view_clusters": 0,
    }

    # --- レイヤ/線種の解決ヘルパ ---
    def linetype_of(e):
        lt = e.dxf.linetype if e.dxf.hasattr("linetype") else "BYLAYER"
        if lt == "BYLAYER":
            try:
                lt = doc.layers.get(e.dxf.layer).dxf.linetype
            except Exception:
                pass
        return lt

    # --- DIMENSION ---
    geom_points_for_dist = []  # 幾何(remaining中のGEOM_TYPES)の代表点 (dist計算用)
    for e in msp:
        if e.dxftype() in GEOM_TYPES:
            geom_points_for_dist.extend(entity_repr_points(e))

    view_geom_points = []
    for e in remaining:
        t = e.dxftype()
        if t in GEOM_TYPES:
            result["geom_entity_count"] += 1
            view_geom_points.extend(entity_repr_points(e))
            if t == "LINE" and linetype_of(e).upper().startswith("DASHDOT"):
                result["centerline_lines"] += 1
            if t == "CIRCLE" and linetype_of(e).upper().startswith("DASHDOT"):
                result["centerline_circles"] += 1
        elif t == "DIMENSION":
            dimtype_base = e.dimtype & 0x0F
            text_override = e.dxf.text if e.dxf.hasattr("text") else None
            try:
                measurement = e.get_measurement()
                if hasattr(measurement, "x"):
                    measurement = None
                else:
                    measurement = float(measurement)
            except Exception:
                measurement = None
            ds_name = e.dxf.dimstyle
            ds_params = {}
            try:
                ds = doc.dimstyles.get(ds_name)
                for k in ("dimtxt", "dimasz", "dimexo", "dimexe", "dimgap", "dimdec",
                          "dimtad", "dimclrd", "dimclre", "dimclrt", "dimblk1", "dimblk2",
                          "dimpost", "dimapost", "dimdli", "dimscale", "dimlfac",
                          "dimfit", "dimupt", "dimsah", "dimtxsty", "dimjust", "dimcen",
                          "dimtol", "dimlim", "dimtp", "dimtm", "dimtfac", "dimtdec", "dimtolj"):
                    ds_params[k] = ds.dxf.get(k)
            except Exception:
                pass
            has_xdata = e.has_xdata("ACAD") if hasattr(e, "has_xdata") else False
            # 寸法線オフセット(1段目): defpoint2-defpoint3(引出線起点=ジオメトリ上の2点)を
            # 結ぶ直線から defpoint(寸法線位置)までの垂直距離。線形/回転寸法(type0/1)のみ有効。
            dline_offset = None
            try:
                if dimtype_base in (0, 1) and e.dxf.hasattr("defpoint2") and e.dxf.hasattr("defpoint3"):
                    dp = e.dxf.defpoint
                    p2 = e.dxf.defpoint2
                    p3 = e.dxf.defpoint3
                    dx, dy = (p3.x - p2.x), (p3.y - p2.y)
                    seg_len = math.hypot(dx, dy)
                    if seg_len > 1e-9:
                        ux, uy = dx / seg_len, dy / seg_len
                        vx, vy = dp.x - p2.x, dp.y - p2.y
                        proj = vx * ux + vy * uy
                        perp_x, perp_y = vx - proj * ux, vy - proj * uy
                        dline_offset = math.hypot(perp_x, perp_y)
            except Exception:
                dline_offset = None

            result["dimensions"].append({
                "dimtype_raw": e.dimtype,
                "dimtype_base": dimtype_base,
                "text_override": text_override,
                "measurement": measurement,
                "dimstyle": ds_name,
                "ds_params": ds_params,
                "has_xdata": has_xdata,
                "dline_offset_from_geom": dline_offset,
            })
            if text_override:
                plain = mtext_plain(text_override)
                result["hole_note_hits"].extend([(n, m, "DIM") for n, m in classify_hole_text(plain)])
                result["tolerance_hits"].extend([(n, m, "DIM") for n, m in classify_tolerance_text(plain)])
            # DIMSTYLEネイティブの寸法許容差機構(dimtol=1: dimtp(上側)/dimtm(下側)を自動付記)
            if ds_params.get("dimtol") == 1:
                tp = ds_params.get("dimtp") or 0.0
                tm = ds_params.get("dimtm") or 0.0
                sign = "plus_only" if tp and not tm else ("minus_only" if tm and not tp else "both")
                result["tolerance_hits"].append(
                    ("native_dimtol", f"+{tp}/-{tm}({sign})", "DIMSTYLE"))
        elif t == "LEADER":
            verts = list(e.vertices)
            ang = None
            if len(verts) >= 2:
                ang = leader_angle_deg((verts[0][0], verts[0][1]), (verts[-1][0], verts[-1][1]))
            result["leaders"].append({
                "n_vertices": len(verts),
                "start": [verts[0][0], verts[0][1]] if verts else None,
                "end": [verts[-1][0], verts[-1][1]] if verts else None,
                "angle_deg": ang,
                "dimstyle": e.dxf.dimstyle if e.dxf.hasattr("dimstyle") else None,
            })
        elif t == "INSERT":
            name = e.dxf.name
            if name.startswith("AGM"):
                p = e.dxf.insert
                result["agm_blocks"].append({"name": name, "insert": [p.x, p.y]})
        elif t in ("MTEXT", "TEXT"):
            if t == "MTEXT":
                raw = e.text
                p = e.dxf.insert
            else:
                raw = e.dxf.text
                p = e.dxf.insert
            if is_field_anchor(p.x, p.y):
                continue  # 表題欄可変フィールドの値なので部品内容から除外
            plain = mtext_plain(raw) if t == "MTEXT" else raw
            hole_hits = classify_hole_text(plain)
            tol_hits = classify_tolerance_text(plain)
            if hole_hits:
                result["hole_note_hits"].extend([(n, m, t) for n, m in hole_hits])
            if tol_hits:
                result["tolerance_hits"].extend([(n, m, t) for n, m in tol_hits])
            if any(k in plain for k in NOTE_KEYWORDS):
                result["notes_candidates"].append({"text": plain, "pos": [p.x, p.y], "src": t})

    n_clusters, sizes = cluster_views(view_geom_points, gap=18.0)
    result["n_view_clusters"] = n_clusters
    result["view_cluster_sizes"] = sizes[:20]

    # gap=45mm: 小穴等のノイズ島を吸収し、真の「ビュー」に近い粒度になることを
    # サンプル10ファイルでの閾値比較(調査/_tmp_view_gap_test.py)で確認済み。
    # n_big45(>=4点で構成される島の数)を「ビュー数」の近似推定値として採用する。
    n_clusters45, sizes45 = cluster_views(view_geom_points, gap=45.0)
    result["n_view_clusters_gap45"] = n_clusters45
    result["n_view_islands_big_gap45"] = sum(1 for s in sizes45 if s >= 4)
    result["view_cluster_sizes_gap45"] = sizes45[:20]

    return result


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    with open(BUCKET_A, encoding="utf-8") as f:
        bucket = json.load(f)

    _, frame_sigs = load_frame_signatures(FRAME_TEMPLATE)

    all_results = []
    errors = []
    n = 0
    for axis, paths in bucket.items():
        for rel in paths:
            n += 1
            full = os.path.join(BASE, rel)
            try:
                r = analyze_file(full, frame_sigs)
                r["axis"] = axis
                all_results.append(r)
                print(f"[{n}] OK  {axis} / {os.path.basename(full)}  dims={len(r['dimensions'])} "
                      f"leaders={len(r['leaders'])} clusters={r['n_view_clusters']}")
            except Exception as ex:
                errors.append({"path": full, "error": str(ex)})
                print(f"[{n}] ERR {axis} / {os.path.basename(full)}: {ex}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"n_files": n, "n_ok": len(all_results), "errors": errors, "results": all_results},
                   f, ensure_ascii=False, indent=1)
    print(f"\nDONE: {len(all_results)}/{n} ok, {len(errors)} errors -> {OUT}")


if __name__ == "__main__":
    main()
