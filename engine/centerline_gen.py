# -*- coding: utf-8 -*-
u"""中心線エンジン(恒久モジュール)。

生成図面に**中心線(一点鎖線)**を自動生成して追加する。
「生成図面には中心線が無く図面として見にくい」(ユーザー最重要フィードバック)への回答。

流儀の根拠は `調査/centerline_style_analysis.md`(人間図面144枚の機械採取)。要点:
  - 線種は **DASHDOT 100%(2290/2290)**、色は **3(緑)98.6%**。レイヤ名はファイルローカルで無意味
  - 用紙倍率1(=我々の出力形式)の83枚では
      穴クロスの円外への延長量 = 中央値 **5.00mm**(最頻5.0・p25 4.50・p75 7.00)
      ビュー貫通軸の輪郭外へのはみ出し = 中央値 **4.56mm**(p25 3.33・p75 7.67)
    → 既定は両方 **5.0mm**(第1段の寸法線オフセット16mmの内側なので寸法と干渉しない)

生成する分類(調査で採取した(a)〜(e)):
  (a) hole_cross    : 円が見えるビューの穴ごとのクロス中心線
  (b) view_axis     : ビュー全体を貫く軸中心線(対称軸・主円の軸)
  (c) pcd_circle    : PCD参照円(同一径・同心・3個以上の穴群)
  (d) (b)のうち円を持たない輪郭/断面ビューのもの(旋盤物の軸中心線)
  (e) profile_mark  : 輪郭ビューに現れる穴の軸線(隠れ線の壁の範囲+延長)

公開API:
  add_centerlines(dxf_path, plan_path, out_path=None, options=None) -> report
  check_centerlines(dxf_path, plan_path, options=None) -> report   # ゲート③の機械化第一歩

❗中心線は**注記であって幾何ではない**。ゲート①②(dim_engine / gate2_completeness)は
  `dim_engine.classify_view_geometry` で中心線種を除外するので、中心線を足しても
  寸法照合・寸法完全性の判定は一切変わらない(回帰試験で差分0件を確認すること)。
"""
import io
import json
import math
import os
import sys

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import dim_engine                      # noqa: E402
from engine import compose_drawing                 # noqa: E402
from engine.frame_extract import subtract_frame    # noqa: E402
from engine.gate2_completeness import (view_axis_map, to_model_coords,  # noqa: E402
                                       parse_hole_note, PCD_MIN_COUNT,
                                       PCD_ANGLE_TOL_DEG, VALUE_TOL)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRAME_TEMPLATE = os.path.join(ROOT, u"図枠", u"frame_template.dxf")

# ---------------------------------------------------------------------------
# 流儀パラメータ(調査/centerline_style_analysis.md の実測から決めた既定値)
# ---------------------------------------------------------------------------
STYLE_DEFAULT = {
    # 線種・色: 人間図面の実測(DASHDOT 100% / 色3 98.6%)。
    # ❗DASHDOT は 図枠/frame_template.dxf に人間図面と**同一パターン**で既に定義済み
    #   (pattern total=1.0 / dash .5 / gap -.25 / dot 0 / gap -.25、$LTSCALE=8.4)。
    #   人間図面(用紙倍率1.5)の $LTSCALE=12.6 = 8.4×1.5 と一致する=見た目が同じになる
    "linetype": "DASHDOT",
    "color": 3,
    "layer": "0",
    # 穴クロスが円の外へ出る量[mm](用紙倍率1の人間図面83枚の中央値5.00・最頻5.0)
    "hole_ext_mm": 5.0,
    # ビュー貫通軸が輪郭の外へ出る量[mm](同 中央値4.56・p25 3.33・p75 7.67)
    "axis_ext_mm": 5.0,
    # 輪郭ビューの穴軸マークが穴の壁の外へ出る量[mm]
    "mark_ext_mm": 5.0,
    # これ未満の径の円には中心線を付けない[mm](人間図面でも極小円は省略される)
    "min_diameter_mm": 1.0,
    # PCD参照円を描く最小個数(gate2_completeness.PCD_MIN_COUNT と同じ)
    "pcd_min_count": PCD_MIN_COUNT,
    "pcd": True,
    "profile_marks": True,
}

#: 「主円(ビューの顔になる円)」判定: 径がビュー短辺のこの割合以上
DOMINANT_CIRCLE_RATIO = 0.40
#: 対称判定でセル一致率がこの値以上なら「対称」とみなす
SYMMETRY_MIN_IOU = 0.90
#: 幾何一致の許容(mm)
GEOM_TOL = 0.05
#: 中心線の重複判定(mm)
DUP_TOL = 0.05


class CenterlineError(Exception):
    pass


# ---------------------------------------------------------------------------
# 幾何ユーティリティ
# ---------------------------------------------------------------------------
def _arc_sweep(a0, a1):
    d = (a1 - a0) % 360.0
    return 360.0 if abs(d) < 1e-9 else d


def collect_circles(entities):
    u"""ビューの実ジオメトリから**本物の円**だけを拾う。

    CIRCLE はそのまま。ARC は「同一中心・同一半径で合計350度以上」の群だけを円とみなす
    (隅Rやフィレットの円弧を穴と誤認して中心線だらけにしないため)。

    Returns: [(cx, cy, r)]
    """
    out = []
    arc_groups = {}
    for e in entities:
        t = e.dxftype()
        if t == "CIRCLE":
            c = e.dxf.center
            out.append((c.x, c.y, e.dxf.radius))
        elif t == "ARC":
            c = e.dxf.center
            key = (round(c.x, 3), round(c.y, 3), round(e.dxf.radius, 3))
            arc_groups.setdefault(key, 0.0)
            arc_groups[key] += _arc_sweep(e.dxf.start_angle, e.dxf.end_angle)
    for (cx, cy, r), sweep in arc_groups.items():
        if sweep >= 350.0:
            out.append((cx, cy, r))
    return out


def group_concentric(circles, tol=GEOM_TOL):
    u"""同心円(穴+ザグリ+ボス)を1つの「円形フィーチャー位置」へまとめ、最大半径を代表にする。

    Returns: [(cx, cy, r_max, r_min, n)]
    """
    groups = []
    for cx, cy, r in sorted(circles, key=lambda c: -c[2]):
        for g in groups:
            if abs(cx - g[0]) <= tol and abs(cy - g[1]) <= tol:
                g[3] = min(g[3], r)
                g[4] += 1
                break
        else:
            groups.append([cx, cy, r, r, 1])
    return [tuple(g) for g in groups]


def _sample_points(entities, step=1.0):
    u"""対称判定用に実ジオメトリを等間隔サンプリングする。"""
    pts = []
    for e in entities:
        t = e.dxftype()
        try:
            if t == "LINE":
                s, en = e.dxf.start, e.dxf.end
                n = max(1, int(math.hypot(en.x - s.x, en.y - s.y) / step))
                for i in range(n + 1):
                    pts.append((s.x + (en.x - s.x) * i / n, s.y + (en.y - s.y) * i / n))
            elif t in ("CIRCLE", "ARC"):
                c, r = e.dxf.center, e.dxf.radius
                if t == "CIRCLE":
                    a0, sweep = 0.0, 360.0
                else:
                    a0 = e.dxf.start_angle
                    sweep = _arc_sweep(a0, e.dxf.end_angle)
                n = max(4, int(math.radians(sweep) * max(r, 0.1) / step))
                for i in range(n + 1):
                    a = math.radians(a0 + sweep * i / n)
                    pts.append((c.x + r * math.cos(a), c.y + r * math.sin(a)))
            elif t == "LWPOLYLINE":
                v = list(e.get_points("xy"))
                if e.closed and v:
                    v = v + [v[0]]
                for p, q in zip(v, v[1:]):
                    n = max(1, int(math.hypot(q[0] - p[0], q[1] - p[1]) / step))
                    for i in range(n + 1):
                        pts.append((p[0] + (q[0] - p[0]) * i / n,
                                    p[1] + (q[1] - p[1]) * i / n))
            elif t in ("SPLINE", "ELLIPSE"):
                pts.extend([(p[0], p[1]) for p in e.flattening(0.2)])
        except Exception:
            continue
    return pts


def is_mirror_symmetric(points, axis, pos, cell=0.6, min_iou=SYMMETRY_MIN_IOU):
    u"""点群が直線(axis='v' なら x=pos / 'h' なら y=pos)について鏡像対称か。

    セル量子化した集合と、その鏡像集合の一致率(IoU)で判定する。
    """
    if len(points) < 20:
        return False, 0.0
    a = set()
    b = set()
    for x, y in points:
        a.add((round(x / cell), round(y / cell)))
        if axis == "v":
            b.add((round((2 * pos - x) / cell), round(y / cell)))
        else:
            b.add((round(x / cell), round((2 * pos - y) / cell)))
    inter = len(a & b)
    union = len(a | b)
    iou = inter / float(union) if union else 0.0
    return iou >= min_iou, iou


# ---------------------------------------------------------------------------
# ビュー情報の復元
# ---------------------------------------------------------------------------
def load_views(dxf_path, plan_path, doc=None):
    u"""生成DXFと計画JSONから、ビューごとの (実ジオメトリ, 領域, 軸マップ) を復元する。

    ❗中心線そのものは `dim_engine.classify_view_geometry` が線種で除外するので、
      何度実行しても「実ジオメトリだけ」を見る(=冪等)。
    """
    with io.open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    meta_json = os.path.join(ROOT, plan["source"]["meta_json"])
    scale, use_views, reserves = dim_engine.plan_layout(plan)
    tf = dim_engine.build_view_transforms(meta_json, scale, views=use_views, reserves=reserves)
    regions = {k: tf[k]["region"] for k in tf}

    if doc is None:
        doc = ezdxf.readfile(dxf_path)
    part_entities, _ = subtract_frame(doc, template_path=FRAME_TEMPLATE)
    per_view = dim_engine.classify_view_geometry(part_entities, regions)

    views = {}
    for k in tf:
        views[k] = {
            "entities": per_view.get(k, []),
            "region": regions[k],
            "amap": view_axis_map(tf[k]["model_to_draw"]),
            "model_to_draw": tf[k]["model_to_draw"],
        }
    return doc, plan, scale, views


# ---------------------------------------------------------------------------
# 中心線の設計(純関数: DXFを触らずに「描くべき中心線」を決める)
# ---------------------------------------------------------------------------
def collect_hole_notes(doc):
    u"""図面のMTEXTから穴注記の構造化情報(個数・径・PCD)を読む。

    ❗**計画JSONの自己申告ではなく、実際に描かれている注記**を読む(ゲート②と同じ思想)。
    """
    out = []
    for e in doc.modelspace():
        if e.dxftype() != "MTEXT":
            continue
        try:
            n = parse_hole_note(e.text)
        except Exception:
            continue
        if n and n.get("pcds"):
            out.append(n)
    return out


def plan_centerlines(views, scale=1.0, style=None, notes=None):
    u"""ビュー情報から、描くべき中心線の一覧を作る。

    Args:
        views : load_views() の戻り値
        scale : 図面の尺度(図面座標 = モデル実寸 × scale)
        style : STYLE_DEFAULT の上書き
        notes : collect_hole_notes() の結果(PCD参照円の根拠。None なら PCD は描かない)

    Returns: {"lines": [...], "circles": [...], "features": [...], "notes": [...]}
      lines   : {"view","cls","dir","p1","p2"}  cls in (hole_cross, view_axis, profile_mark)
      circles : {"view","cls","center","r","n_holes"}  cls == pcd_circle
      features: 円形フィーチャー台帳(ゲート③の照合対象)
    """
    st = dict(STYLE_DEFAULT)
    st.update(style or {})
    hole_ext = float(st["hole_ext_mm"])
    axis_ext = float(st["axis_ext_mm"])
    mark_ext = float(st["mark_ext_mm"])
    min_d = float(st["min_diameter_mm"]) * scale     # 図面座標での最小径

    lines, circles, features, notes_out = [], [], [], []

    # --- 1) 各ビューの円形フィーチャーを棚卸し -------------------------------
    per_view_feats = {}
    for k, v in views.items():
        ents = v["entities"]
        feats = [f for f in group_concentric(collect_circles(ents)) if 2 * f[2] >= min_d]
        per_view_feats[k] = feats
        for (cx, cy, rmax, rmin, n) in feats:
            features.append({"view": k, "center": [round(cx, 4), round(cy, 4)],
                             "d_draw": round(2 * rmax, 4),
                             "d_model": round(2 * rmax / scale, 4), "n_circles": n})

    # --- 2) ビュー貫通軸(b)(d) ---------------------------------------------
    axes_by_view = {k: [] for k in views}      # (dir, pos)
    for k, v in views.items():
        ents = v["entities"]
        if not ents:
            continue
        x0, y0, x1, y1 = v["region"]
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            continue
        cands = {"v": [], "h": []}
        # (i) 主円(ビューの顔になる円)の中心を通る軸
        feats = per_view_feats[k]
        if feats:
            big = max(feats, key=lambda f: f[2])
            if 2 * big[2] >= DOMINANT_CIRCLE_RATIO * min(w, h):
                cands["v"].append((big[0], "dominant_circle"))
                cands["h"].append((big[1], "dominant_circle"))
        # (ii) 幾何の鏡像対称軸
        pts = _sample_points(ents, step=max(0.5, 0.5 * scale))
        for d, pos in (("v", (x0 + x1) / 2.0), ("h", (y0 + y1) / 2.0)):
            ok, iou = is_mirror_symmetric(pts, d, pos)
            if ok:
                cands[d].append((pos, "symmetry(iou=%.3f)" % iou))
        for d in ("v", "h"):
            for pos, why in cands[d]:
                if any(abs(pos - p) <= DUP_TOL for dd, p in axes_by_view[k] if dd == d):
                    continue
                axes_by_view[k].append((d, pos))
                if d == "v":
                    p1, p2 = (pos, y0 - axis_ext), (pos, y1 + axis_ext)
                else:
                    p1, p2 = (x0 - axis_ext, pos), (x1 + axis_ext, pos)
                lines.append({"view": k, "cls": "view_axis", "dir": d,
                              "p1": p1, "p2": p2, "why": why})

    # --- 3) 穴ごとのクロス中心線(a) ----------------------------------------
    # ❗**一直線に並ぶ穴群は1本の中心線でつなぐ**(人間図面の流儀。25154-1-27 の実測:
    #   3個並んだ穴の列は板を横断する1本の一点鎖線で描かれており、穴ごとの短い十字ではない)。
    #   穴ごとに切ると見た目が細切れになり「図面として見にくい」という元の指摘に戻ってしまう。
    for k, v in views.items():
        runs = {}      # (dir, pos) -> [(along, r), ...]
        for (cx, cy, rmax, rmin, n) in per_view_feats[k]:
            for d in ("v", "h"):
                pos = cx if d == "v" else cy
                # 既にビュー貫通軸が同じ位置を通っているならクロスは描かない
                # (人間図面も主円の十字はビュー貫通軸そのもの)
                if any(dd == d and abs(pos - p) <= DUP_TOL for dd, p in axes_by_view[k]):
                    continue
                key = None
                for kk in runs:
                    if kk[0] == d and abs(kk[1] - pos) <= DUP_TOL:
                        key = kk
                        break
                runs.setdefault(key or (d, pos), []).append(
                    ((cy if d == "v" else cx), rmax))
        for (d, pos), members in runs.items():
            lo = min(a - r for a, r in members) - hole_ext
            hi = max(a + r for a, r in members) + hole_ext
            if d == "v":
                p1, p2 = (pos, lo), (pos, hi)
            else:
                p1, p2 = (lo, pos), (hi, pos)
            lines.append({"view": k, "cls": "hole_cross", "dir": d,
                          "p1": p1, "p2": p2, "n_holes": len(members)})

    # --- 4) PCD参照円(c) ----------------------------------------------------
    # ❗**幾何だけで「同心・等距離の穴が3個以上」を条件にしてはいけない**(実測の実害):
    #   25154-1-27 走行フレーム踏板(長方形配置の6穴)で、四隅の4穴が中心から等距離
    #   だったため板全体を覆う巨大な偽PCD円が描かれた(人間図面にはPCD円は無い)。
    #   → 人間の流儀は「図面が**ＰＣＤと書いている**ときにその参照円を描く」。
    #     注記のPCD値を採り、gate2_completeness.find_pcd_groups と**同じ3点検算**
    #     (個数一致 / 中心円径がPCD値と一致 / 円周等分)を実ジオメトリで通ったものだけ描く。
    if st["pcd"] and notes:
        tol = max(VALUE_TOL, 1e-6) * max(scale, 1e-9) * 5.0
        for k, v in views.items():
            feats = per_view_feats[k]
            for n in notes:
                for pcd in n["pcds"]:
                    for dia in n["diameters"]:
                        r_draw = pcd / 2.0 * scale
                        d_draw = dia * scale
                        members = [f for f in feats
                                   if min(abs(2 * f[2] - d_draw), abs(2 * f[3] - d_draw)) <= tol]
                        if len(members) < int(st["pcd_min_count"]):
                            continue
                        if n["count"] is not None and n["count"] != len(members):
                            continue
                        cx = sum(f[0] for f in members) / len(members)
                        cy = sum(f[1] for f in members) / len(members)
                        rs = [math.hypot(f[0] - cx, f[1] - cy) for f in members]
                        if max(abs(r - r_draw) for r in rs) > tol:
                            continue
                        angs = sorted(math.degrees(math.atan2(f[1] - cy, f[0] - cx)) % 360.0
                                      for f in members)
                        step = 360.0 / len(angs)
                        diffs = [(angs[(i + 1) % len(angs)] - angs[i]) % 360.0
                                 for i in range(len(angs))]
                        if max(abs(d - step) for d in diffs) > PCD_ANGLE_TOL_DEG:
                            notes_out.append({"view": k, "cls": "pcd_rejected",
                                              "reason": u"円周等分でない", "pcd": pcd,
                                              "diffs": [round(d, 3) for d in diffs]})
                            continue
                        r_avg = sum(rs) / len(rs)
                        if any(ci["view"] == k and abs(ci["r"] - r_avg) <= DUP_TOL
                               for ci in circles):
                            continue
                        circles.append({"view": k, "cls": "pcd_circle",
                                        "center": (cx, cy), "r": r_avg,
                                        "pcd_d_draw": round(2 * r_avg, 4),
                                        "pcd_d_model": round(2 * r_avg / scale, 4),
                                        "hole_d_model": dia, "n_holes": len(members),
                                        "note": n["raw"]})

    # --- 5) 輪郭ビューの穴軸マーク(e) --------------------------------------
    if st["profile_marks"]:
        for k, v in views.items():
            amap = v["amap"]
            if amap is None:
                continue
            for k2, v2 in views.items():
                if k2 == k:
                    continue
                amap2 = v2["amap"]
                if amap2 is None:
                    continue
                # k で円に見える穴の軸 = k の法線軸。k2 で面内軸ならマークを引く
                az = amap["normal"]
                if amap2["x"][0] == az:
                    mdir = "h"        # 穴軸が k2 の図面x方向 -> 水平マーク
                    perp_axis = amap2["y"][0]
                elif amap2["y"][0] == az:
                    mdir = "v"
                    perp_axis = amap2["x"][0]
                else:
                    continue
                for (cx, cy, rmax, rmin, n) in per_view_feats[k]:
                    mc = to_model_coords(amap, (cx, cy))
                    if perp_axis not in mc:
                        continue
                    # k2 の図面座標での「マーク線の位置」
                    if mdir == "h":
                        pos = amap2["origin"][1] + mc[perp_axis] * amap2["y"][1]
                    else:
                        pos = amap2["origin"][0] + mc[perp_axis] * amap2["x"][1]
                    if any(dd == mdir and abs(pos - p) <= DUP_TOL
                           for dd, p in axes_by_view[k2]):
                        continue
                    span = _wall_span(v2["entities"], mdir, pos, rmax)
                    if span is None:
                        notes_out.append({"view": k2, "cls": "profile_mark_skipped",
                                      "reason": u"穴の壁(隠れ線)が見つからない",
                                      "pos": round(pos, 4)})
                        continue
                    a, b = span[0] - mark_ext, span[1] + mark_ext
                    if mdir == "h":
                        p1, p2 = (a, pos), (b, pos)
                    else:
                        p1, p2 = (pos, a), (pos, b)
                    if _dup_line(lines, k2, mdir, pos, a, b):
                        continue
                    lines.append({"view": k2, "cls": "profile_mark", "dir": mdir,
                                  "p1": p1, "p2": p2, "from_view": k,
                                  "d_draw": round(2 * rmax, 4)})

    return {"lines": lines, "circles": circles, "features": features, "notes": notes_out,
            "style": st}


def _dup_line(lines, view, d, pos, a, b):
    for ln in lines:
        if ln["view"] != view or ln["dir"] != d:
            continue
        p = ln["p1"][1] if d == "h" else ln["p1"][0]
        if abs(p - pos) > DUP_TOL:
            continue
        q1 = min(ln["p1"][0], ln["p2"][0]) if d == "h" else min(ln["p1"][1], ln["p2"][1])
        q2 = max(ln["p1"][0], ln["p2"][0]) if d == "h" else max(ln["p1"][1], ln["p2"][1])
        if q1 - DUP_TOL <= a and b <= q2 + DUP_TOL:
            return True
    return False


def _wall_span(entities, mdir, pos, r, tol=0.2):
    u"""輪郭ビューで「穴の壁」(軸から±r にある軸平行線分)の範囲を求める。

    見つからなければ None(**でっち上げない**。人間図面も見えない穴には線を引かない)。
    """
    lo = hi = None
    for e in entities:
        if e.dxftype() != "LINE":
            continue
        s, t = e.dxf.start, e.dxf.end
        if mdir == "h":
            if abs(s.y - t.y) > 1e-6:
                continue
            off = abs(s.y - pos)
            a, b = min(s.x, t.x), max(s.x, t.x)
        else:
            if abs(s.x - t.x) > 1e-6:
                continue
            off = abs(s.x - pos)
            a, b = min(s.y, t.y), max(s.y, t.y)
        if abs(off - r) > tol:
            continue
        lo = a if lo is None else min(lo, a)
        hi = b if hi is None else max(hi, b)
    if lo is None or hi is None or hi - lo <= 1e-6:
        return None
    return (lo, hi)


# ---------------------------------------------------------------------------
# DXFへの書き込み
# ---------------------------------------------------------------------------
def ensure_linetype(doc, name="DASHDOT"):
    u"""中心線の線種が定義されていることを保証する(図枠テンプレートには既に有る)。"""
    if name in doc.linetypes:
        return False
    # 人間図面(ACROVA GMM出力)と同一パターン: __ . __ . (total 1.0)
    doc.linetypes.add(name, pattern=[1.0, 0.5, -0.25, 0.0, -0.25],
                      description="__ . __ . __ . __ . __ . __ . __ . __ . __ . __")
    return True


def add_centerlines(dxf_path, plan_path, out_path=None, options=None):
    u"""生成済みDXFへ中心線を追加して保存する。

    Args:
        dxf_path : 入力DXF(compose+dim_engine 済みの部品図)
        plan_path: 作図計画JSON(ビュー変換の復元に使う)
        out_path : 出力先(None なら dxf_path を上書き)
        options  : STYLE_DEFAULT の上書き + {"enabled": bool}

    Returns: report dict
    """
    opts = dict(options or {})
    if opts.pop("enabled", True) is False:
        return {"enabled": False, "added": 0, "lines": [], "circles": []}

    doc, plan, scale, views = load_views(dxf_path, plan_path)
    design = plan_centerlines(views, scale=scale, style=opts,
                              notes=collect_hole_notes(doc))
    st = design["style"]

    lt_added = ensure_linetype(doc, st["linetype"])
    msp = doc.modelspace()
    attribs = {"linetype": st["linetype"], "color": int(st["color"]),
               "layer": str(st["layer"])}
    for ln in design["lines"]:
        msp.add_line(ln["p1"], ln["p2"], dxfattribs=dict(attribs))
    for ci in design["circles"]:
        msp.add_circle(ci["center"], ci["r"], dxfattribs=dict(attribs))

    out = out_path or dxf_path
    out_dir = os.path.dirname(out)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    doc.saveas(out)

    counts = {}
    for ln in design["lines"]:
        counts[ln["cls"]] = counts.get(ln["cls"], 0) + 1
    for ci in design["circles"]:
        counts[ci["cls"]] = counts.get(ci["cls"], 0) + 1
    return {
        "enabled": True,
        "out_path": out,
        "added": len(design["lines"]) + len(design["circles"]),
        "counts": counts,
        "linetype_added": lt_added,
        "style": {k: st[k] for k in ("linetype", "color", "hole_ext_mm", "axis_ext_mm")},
        "features": design["features"],
        "lines": [{"view": l["view"], "cls": l["cls"], "dir": l["dir"],
                   "p1": [round(l["p1"][0], 4), round(l["p1"][1], 4)],
                   "p2": [round(l["p2"][0], 4), round(l["p2"][1], 4)]}
                  for l in design["lines"]],
        "circles": design["circles"],
        "notes": design["notes"],
    }


# ---------------------------------------------------------------------------
# ゲート③の機械化第一歩: 「円形フィーチャーに中心線が付いているか」
# ---------------------------------------------------------------------------
def _covering_lines(doc, scale):
    u"""DXF中の中心線(実体)を (dir, pos, lo, hi) の一覧で返す。"""
    out = []
    for e in doc.modelspace():
        if e.dxftype() != "LINE":
            continue
        lt = str(e.dxf.get("linetype", "BYLAYER")).upper()
        if lt not in dim_engine.CENTERLINE_LINETYPES:
            continue
        s, t = e.dxf.start, e.dxf.end
        if abs(s.y - t.y) <= 1e-6 and abs(s.x - t.x) > 1e-6:
            out.append(("h", s.y, min(s.x, t.x), max(s.x, t.x)))
        elif abs(s.x - t.x) <= 1e-6 and abs(s.y - t.y) > 1e-6:
            out.append(("v", s.x, min(s.y, t.y), max(s.y, t.y)))
    return out


def _center_circles(doc):
    out = []
    for e in doc.modelspace():
        if e.dxftype() != "CIRCLE":
            continue
        if str(e.dxf.get("linetype", "BYLAYER")).upper() not in dim_engine.CENTERLINE_LINETYPES:
            continue
        c = e.dxf.center
        out.append((c.x, c.y, e.dxf.radius))
    return out


def check_centerlines(dxf_path, plan_path, options=None):
    u"""ゲート③(機械化第一歩): **円が見えるビューの円形フィーチャーに中心線が付いているか**。

    判定は生成DXFの実体だけで行う(計画の自己申告も add_centerlines の戻り値も信用しない)。
    合格条件:
      (1) 被覆: 各円形フィーチャー(同心円は1件)の中心を、水平・垂直の中心線が**両方**通り、
          円の外へ `min_ext_mm` 以上出ている(中心マーク程度の短い線を合格にしない)
      (2) 完全性: 実ジオメトリから決まる中心線の設計(plan_centerlines)が
          **1本残らず実体として存在する**。輪郭ビューの穴軸マーク(e)やPCD参照円のように
          「円形フィーチャーの被覆」では見えない中心線の欠落もここで捕まえる
      (3) 図枠: 中心線が図枠外/表題欄/左上ノートに侵入していない
    """
    opts = dict(STYLE_DEFAULT)
    opts.update(options or {})
    min_ext = float(opts.get("min_ext_mm", 0.5))

    doc, plan, scale, views = load_views(dxf_path, plan_path)
    cls = _covering_lines(doc, scale)
    min_d = float(opts["min_diameter_mm"]) * scale

    missing, checked = [], []
    for k, v in views.items():
        feats = [f for f in group_concentric(collect_circles(v["entities"]))
                 if 2 * f[2] >= min_d]
        for (cx, cy, rmax, rmin, n) in feats:
            got = {}
            for d, pos, lo, hi in cls:
                if d == "h":
                    if abs(pos - cy) <= DUP_TOL and lo <= cx <= hi:
                        got["h"] = max(got.get("h", -1e9),
                                       min(cx - lo, hi - cx) - rmax)
                else:
                    if abs(pos - cx) <= DUP_TOL and lo <= cy <= hi:
                        got["v"] = max(got.get("v", -1e9),
                                       min(cy - lo, hi - cy) - rmax)
            rec = {"view": k, "center": [round(cx, 3), round(cy, 3)],
                   "d_model": round(2 * rmax / scale, 3),
                   "h": round(got["h"], 3) if "h" in got else None,
                   "v": round(got["v"], 3) if "v" in got else None}
            checked.append(rec)
            lack = [d for d in ("h", "v")
                    if d not in got or got[d] < min_ext * scale - 1e-6]
            if lack:
                missing.append(dict(rec, lack=lack))

    # (2) 設計した中心線が1本残らず実体として存在するか
    #     ❗被覆判定だけでは輪郭ビューの穴軸マーク(e)やPCD参照円(c)の欠落を検出できない
    #        (反証テストで実測: 任意の1本消しが2/10ケースで素通りした)
    design = plan_centerlines(views, scale=scale, style=opts,
                              notes=collect_hole_notes(doc))
    ccircles = _center_circles(doc)
    not_realized = []
    for ln in design["lines"]:
        d = ln["dir"]
        pos = ln["p1"][1] if d == "h" else ln["p1"][0]
        a = min(ln["p1"][0], ln["p2"][0]) if d == "h" else min(ln["p1"][1], ln["p2"][1])
        b = max(ln["p1"][0], ln["p2"][0]) if d == "h" else max(ln["p1"][1], ln["p2"][1])
        found = any(ad == d and abs(ap - pos) <= DUP_TOL
                    and alo <= a + DUP_TOL and b - DUP_TOL <= ahi
                    for ad, ap, alo, ahi in cls)
        if not found:
            not_realized.append({"view": ln["view"], "cls": ln["cls"], "dir": d,
                                 "p1": [round(v, 3) for v in ln["p1"]],
                                 "p2": [round(v, 3) for v in ln["p2"]]})
    for ci in design["circles"]:
        found = any(abs(cx - ci["center"][0]) <= DUP_TOL
                    and abs(cy - ci["center"][1]) <= DUP_TOL
                    and abs(r - ci["r"]) <= DUP_TOL for cx, cy, r in ccircles)
        if not found:
            not_realized.append({"view": ci["view"], "cls": ci["cls"],
                                 "center": [round(v, 3) for v in ci["center"]],
                                 "r": round(ci["r"], 3)})

    # (3) 中心線が図枠ゾーンへ侵入していないか(寸法と同じ判定ゾーンを使う)
    zone_hits = []
    for d, pos, lo, hi in cls:
        box = (lo, pos, hi, pos) if d == "h" else (pos, lo, pos, hi)
        zone = _zone_of(box)
        if zone:
            zone_hits.append({"dir": d, "box": [round(x, 3) for x in box], "zone": zone})

    n = len(checked)
    n_design = len(design["lines"]) + len(design["circles"])
    return {
        "ok": not missing and not not_realized and not zone_hits,
        "n_features": n,
        "n_missing": len(missing),
        "coverage": (1.0 - len(missing) / float(n)) if n else 1.0,
        "n_centerline_entities": len(cls) + len(ccircles),
        "n_designed": n_design,
        "n_not_realized": len(not_realized),
        "missing": missing,
        "not_realized": not_realized,
        "zone_hits": zone_hits,
        "checked": checked,
    }


def _zone_of(box):
    u"""中心線の実体矩形が図枠の禁止ゾーンに掛かっていればゾーン名を返す。"""
    x0, y0, x1, y1 = min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])
    fx0, fy0, fx1, fy1 = (compose_drawing.FRAME_X0, compose_drawing.FRAME_Y0,
                          compose_drawing.FRAME_X1, compose_drawing.FRAME_Y1)
    if x0 < fx0 or y0 < fy0 or x1 > fx1 or y1 > fy1:
        return "outside_frame"
    for name, rect in (("title_block", compose_drawing.TITLE_BLOCK_RECT),
                       ("note_zone", compose_drawing.NOTE_ZONE_RECT)):
        rx0, ry0, rx1, ry1 = rect
        if not (x1 <= rx0 or rx1 <= x0 or y1 <= ry0 or ry1 <= y0):
            return name
    return None


# ---------------------------------------------------------------------------
def _main(argv):
    import argparse
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=u"中心線の生成/検査")
    ap.add_argument("--dxf", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args(argv[1:])
    if not a.check_only:
        rep = add_centerlines(a.dxf, a.plan, out_path=a.out)
        print(json.dumps({k: rep[k] for k in ("added", "counts", "style", "notes")},
                         ensure_ascii=False, indent=2))
    chk = check_centerlines(a.out or a.dxf, a.plan)
    print(json.dumps({k: chk[k] for k in
                      ("ok", "n_features", "n_missing", "coverage",
                       "n_centerline_entities", "missing", "zone_hits")},
                     ensure_ascii=False, indent=2))
    return 0 if chk["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
