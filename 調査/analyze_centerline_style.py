# -*- coding: utf-8 -*-
u"""人間図面(荏原トライ調整用/DXF/部品表用DXFデータ)の**中心線の流儀**を機械採取する。

読み取り専用。荏原フォルダには一切書き込まない。

採取する分類:
  (a) hole_cross     穴ごとのクロス中心線(円形ビュー。円の中心を通る水平/垂直の短い線)
  (b) view_axis      ビュー全体を貫く軸中心線(輪郭からのはみ出し量)
  (c) pcd_circle     PCD参照円(DASHDOT系の CIRCLE/ARC で、3個以上の穴中心が載る)
  (d) profile_axis   旋盤断面・輪郭ビューの軸中心線(= view_axis のうち円が無いビューのもの)
  (e) hole_edge_mark 輪郭ビューで穴の投影位置に引かれた短い中心線(円を持たない側面図)

出力: 調査/centerline_style_stats.json(生データ) + 標準出力サマリ
"""
import os
import sys
import glob
import json
import math
import statistics
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ezdxf
import geom_lib as gl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DXF_DIR = os.path.join(ROOT, u"荏原トライ調整用", u"DXF", u"部品表用DXFデータ")

#: 一点鎖線(中心線)として扱う線種。二点鎖線(DIVIDE=想像線)は別集計する
CENTER_LT = {"DASHDOT", "DASHDOT2", "DASHDOTX2", "CENTER", "CENTER2", "CENTERX2"}
PHANTOM2_LT = {"DIVIDE", "DIVIDE2", "DIVIDEX2"}

#: バケットA図枠の内寸(mm)。用紙倍率の判定に使う
FRAME_W, FRAME_H = 410.0, 287.0
MAG_CANDIDATES = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0)

ANG_TOL = 1e-3          # 水平/垂直判定(DXF単位)
ON_LINE_TOL = 0.15      # 「線が円中心を通る」判定の直交方向許容(DXF単位)


# ---------------------------------------------------------------------------
def eff_color(doc, e):
    c = e.dxf.get("color", 256)
    if c == 256:
        try:
            c = doc.layers.get(e.dxf.layer).dxf.color
        except Exception:
            c = -1
    return c


def detect_frame(doc):
    u"""図枠外形(410x287 の矩形)を探して (用紙倍率, (x0,y0,x1,y1)) を返す。無ければ None。"""
    hs, vs = [], []
    for e in doc.modelspace():
        if e.dxftype() == "LINE":
            s, t = e.dxf.start, e.dxf.end
            if abs(s.y - t.y) < 0.05 and abs(s.x - t.x) > 100:
                hs.append((min(s.x, t.x), max(s.x, t.x), s.y))
            elif abs(s.x - t.x) < 0.05 and abs(s.y - t.y) > 100:
                vs.append((min(s.y, t.y), max(s.y, t.y), s.x))
        elif e.dxftype() == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
            if e.closed:
                pts = pts + pts[:1]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                if abs(y0 - y1) < 0.05 and abs(x0 - x1) > 100:
                    hs.append((min(x0, x1), max(x0, x1), y0))
                elif abs(x0 - x1) < 0.05 and abs(y0 - y1) > 100:
                    vs.append((min(y0, y1), max(y0, y1), x0))
    if not hs or not vs:
        return None
    best = None
    for m in MAG_CANDIDATES:
        w, h = FRAME_W * m, FRAME_H * m
        tol = 3.0 * m
        okh = [x for x in hs if abs((x[1] - x[0]) - w) < tol]
        okv = [x for x in vs if abs((x[1] - x[0]) - h) < tol]
        if okh and okv:
            sc = len(okh) + len(okv)
            if best is None or sc > best[0]:
                x0 = min(a for a, _b, _y in okh)
                x1 = max(b for _a, b, _y in okh)
                y0 = min(a for a, _b, _x in okv)
                y1 = max(b for _a, b, _x in okv)
                best = (sc, m, (x0, y0, x1, y1))
    return (best[1], best[2]) if best else None


def circles_from(pairs, kinds=("solid",)):
    u"""(cx, cy, r, 種別) のリスト。ARC群の合成円も拾う(geom_lib.circles_of を利用)。"""
    return gl.circles_of(pairs, kinds=kinds)


def seg_of(e):
    s, t = e.dxf.start, e.dxf.end
    return (s.x, s.y, t.x, t.y)


# ---------------------------------------------------------------------------
def _bbox_of_entity(e):
    pts = gl._pts_of(e)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def analyze_file(path):
    doc = ezdxf.readfile(path)
    fr = detect_frame(doc)
    if not fr:
        return None
    mag, (fx0, fy0, fx1, fy1) = fr
    msp = list(doc.modelspace())
    pairs_all = gl.collect(doc, msp)
    # ❗図枠を含めたままクラスタリングすると全部が1クラスタに融合し、
    #   「ビュー貫通軸」と「短い中心線」の区別が壊れる(実測: span が図枠幅になり誤分類)。
    #   → 作図エリア(図枠内・表題欄帯より上)へクリップし、図枠外形線そのものを落とす。
    ax0, ay0 = fx0 + 1.0 * mag, fy0 + 26.0 * mag
    ax1, ay1 = fx1 - 1.0 * mag, fy1 - 1.0 * mag
    pairs = []
    for e, kind in pairs_all:
        b = _bbox_of_entity(e)
        if b is None:
            continue
        if b[0] < ax0 or b[1] < ay0 or b[2] > ax1 or b[3] > ay1:
            continue
        # 図枠外形と同寸の矩形辺(=図枠そのもの)は落とす
        if (b[2] - b[0]) > 0.85 * (fx1 - fx0) or (b[3] - b[1]) > 0.85 * (fy1 - fy0):
            if kind == "solid":
                continue
        pairs.append((e, kind))

    # 中心線エンティティ(展開後)
    center_lines, center_circles = [], []
    other_center = Counter()
    lt_layer_color = Counter()
    for e, kind in pairs:
        lt = gl.linetype_of(doc, e)
        if lt not in CENTER_LT:
            continue
        lt_layer_color[(lt, str(e.dxf.get("layer", "?")), eff_color(doc, e))] += 1
        t = e.dxftype()
        if t == "LINE":
            center_lines.append(e)
        elif t in ("CIRCLE", "ARC"):
            center_circles.append(e)
        else:
            other_center[t] += 1

    # ビュークラスタ(実線ベース)。❗gap は紙面mmなので用紙倍率でスケールする
    clusters = gl.cluster_views(pairs, gap=8.0 * mag, kinds=("solid", "hidden", "tangent"))
    clusters = [c for c in clusters
                if c["n"] >= 6
                and (c["bbox"][2] - c["bbox"][0]) > 10.0 * mag
                and (c["bbox"][3] - c["bbox"][1]) > 10.0 * mag]
    solid_circles_raw = circles_from(pairs, kinds=("solid",))

    def in_part_view(cx, cy):
        for c in clusters:
            b = c["bbox"]
            if b[0] - 0.5 <= cx <= b[2] + 0.5 and b[1] - 0.5 <= cy <= b[3] + 0.5:
                return c
        return None

    # 風船(部品番号の丸)・部品表の円は部品ビューに属さないので除外
    solid_circles = [c for c in solid_circles_raw if in_part_view(c[0], c[1])]

    rec = {
        "file": os.path.basename(path),
        "axis": os.path.basename(os.path.dirname(path)),
        "paper_mag": mag,
        "n_center_lines": len(center_lines),
        "n_center_circles": len(center_circles),
        "center_other_types": dict(other_center),
        "lt_layer_color": [[list(k), v] for k, v in lt_layer_color.most_common()],
        "n_divide": sum(1 for e, _ in pairs if gl.linetype_of(doc, e) in PHANTOM2_LT),
        "hole_cross": [],       # (a)
        "view_axis": [],        # (b)/(d)
        "pcd_circle": [],       # (c)
        "hole_edge_mark": [],   # (e)
        "circle_coverage": [],  # 円ごとに中心線が付いているか
    }

    # ---- (c) PCD参照円: 中心線種の CIRCLE/ARC。半径上に穴中心が3個以上あるか検算
    hole_c = [(cx, cy, r) for (cx, cy, r, _k) in solid_circles]
    for e in center_circles:
        c = e.dxf.center
        r = e.dxf.radius
        on = [1 for (hx, hy, hr) in hole_c
              if abs(math.hypot(hx - c.x, hy - c.y) - r) < 0.35 * mag and hr < r * 0.9]
        rec["pcd_circle"].append({
            "r_dxf": r, "r_mm": r / mag, "d_mm": 2 * r / mag,
            "type": e.dxftype(), "holes_on": len(on),
            "verified_pcd": len(on) >= 3,
        })

    # ---- 中心線の直線を分類
    used = [False] * len(center_lines)
    for i, e in enumerate(center_lines):
        x0, y0, x1, y1 = seg_of(e)
        dx, dy = x1 - x0, y1 - y0
        L = math.hypot(dx, dy)
        if L < 1e-6:
            continue
        horiz = abs(dy) < ANG_TOL * max(1.0, L)
        vert = abs(dx) < ANG_TOL * max(1.0, L)
        if not (horiz or vert):
            rec.setdefault("oblique", []).append(round(L / mag, 3))
            continue
        # この線が中心を通る実線円
        hits = []
        for (cx, cy, r, _k) in solid_circles:
            if horiz and abs(cy - y0) < ON_LINE_TOL * mag:
                a, b = min(x0, x1), max(x0, x1)
                if a - 0.01 <= cx <= b + 0.01:
                    hits.append((cx, cy, r, abs(cx - a), abs(b - cx)))
            elif vert and abs(cx - x0) < ON_LINE_TOL * mag:
                a, b = min(y0, y1), max(y0, y1)
                if a - 0.01 <= cy <= b + 0.01:
                    hits.append((cx, cy, r, abs(cy - a), abs(b - cy)))
        # 所属ビュー
        vw = None
        mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        for c in clusters:
            bx0, by0, bx1, by1 = c["bbox"]
            if bx0 - 2 * mag <= mx <= bx1 + 2 * mag and by0 - 2 * mag <= my <= by1 + 2 * mag:
                if vw is None or (bx1 - bx0) * (by1 - by0) < (vw[2] - vw[0]) * (vw[3] - vw[1]):
                    vw = (bx0, by0, bx1, by1)
        span = None
        if vw:
            span = (vw[2] - vw[0]) if horiz else (vw[3] - vw[1])

        # ❗同心円(ザグリ・ボス)が複数あると hits が増える。中心が一致していれば
        #   「1つの穴フィーチャー」として最大半径で延長量を測る
        same_center = (len(hits) >= 1 and
                       all(abs(h[0] - hits[0][0]) < 0.05 * mag and
                           abs(h[1] - hits[0][1]) < 0.05 * mag for h in hits))
        if same_center:
            cx, cy = hits[0][0], hits[0][1]
            r = max(h[2] for h in hits)
            e1, e2 = hits[0][3], hits[0][4]
            ext1, ext2 = (e1 - r) / mag, (e2 - r) / mag
            # 穴専用クロス = 線長が円径 + 小さな延長 に収まる
            if max(ext1, ext2) < max(6.0, r / mag) and min(ext1, ext2) > -0.5:
                used[i] = True
                rec["hole_cross"].append({
                    "d_mm": 2 * r / mag, "ext1_mm": ext1, "ext2_mm": ext2,
                    "ext1_dxf": e1 - r, "ext2_dxf": e2 - r, "d_dxf": 2 * r,
                    "dir": "h" if horiz else "v",
                })
                continue
        if span and L >= 0.95 * span:
            if horiz:
                o1, o2 = (vw[0] - min(x0, x1)) / mag, (max(x0, x1) - vw[2]) / mag
            else:
                o1, o2 = (vw[1] - min(y0, y1)) / mag, (max(y0, y1) - vw[3]) / mag
            if max(o1, o2) > 40.0:      # ビューをまたぐ長い基準線は別枠(統計を汚す)
                rec.setdefault("long_line", []).append(round(L / mag, 2))
                continue
            used[i] = True
            rec["view_axis"].append({
                "len_mm": L / mag, "span_mm": span / mag,
                "over1_mm": o1, "over2_mm": o2,
                "dir": "h" if horiz else "v",
                "n_circles_on": len(hits),
                "view_has_circles": any(
                    vw[0] - 1 <= cx <= vw[2] + 1 and vw[1] - 1 <= cy <= vw[3] + 1
                    for (cx, cy, _r, _k) in solid_circles),
            })
            continue
        # 残り = 輪郭ビューの穴位置マーク等
        rec["hole_edge_mark"].append({"len_mm": L / mag, "dir": "h" if horiz else "v",
                                      "span_ratio": (L / span) if span else None})

    # ---- 円ごとの中心線カバレッジ(ゲート③の素材)
    # 各ビューが「円形ビュー(円が2個以上ある)」かどうかも記録する
    view_ncirc = Counter()
    for (cx, cy, r, _k) in solid_circles:
        c = in_part_view(cx, cy)
        if c:
            view_ncirc[id(c)] += 1
    # ❗同心円(穴+ザグリ)は「1つの円形フィーチャー位置」として数える。
    #   円単位で数えると同じ中心を二重計上してカバレッジ統計が歪む。
    seen_centers = []
    for (cx, cy, r, _k) in sorted(solid_circles, key=lambda c: -c[2]):
        if any(abs(cx - sx) < 0.05 * mag and abs(cy - sy) < 0.05 * mag
               for sx, sy in seen_centers):
            continue
        seen_centers.append((cx, cy))
        cl = in_part_view(cx, cy)
        hh = vv = False
        for e in center_lines:
            x0, y0, x1, y1 = seg_of(e)
            if abs(y1 - y0) < ANG_TOL * max(1.0, abs(x1 - x0)):
                if abs(y0 - cy) < ON_LINE_TOL * mag and min(x0, x1) - 0.01 <= cx <= max(x0, x1) + 0.01:
                    hh = True
            elif abs(x1 - x0) < ANG_TOL * max(1.0, abs(y1 - y0)):
                if abs(x0 - cx) < ON_LINE_TOL * mag and min(y0, y1) - 0.01 <= cy <= max(y0, y1) + 0.01:
                    vv = True
        rec["circle_coverage"].append({"d_mm": 2 * r / mag, "h": hh, "v": vv,
                                       "view_ncirc": view_ncirc.get(id(cl), 0) if cl else 0})
    return rec


# ---------------------------------------------------------------------------
def summarize(recs):
    def stats(vals, name):
        if not vals:
            return f"{name}: n=0"
        v = sorted(vals)
        return (f"{name}: n={len(v)} min={v[0]:.2f} p10={v[int(.1*len(v))]:.2f} "
                f"p25={v[int(.25*len(v))]:.2f} med={statistics.median(v):.2f} "
                f"p75={v[int(.75*len(v))]:.2f} p90={v[int(.9*len(v))]:.2f} max={v[-1]:.2f}")

    print(f"\n===== 解析ファイル数: {len(recs)} =====")
    print("用紙倍率分布:", Counter(r["paper_mag"] for r in recs).most_common())

    lc = Counter()
    for r in recs:
        for k, v in r["lt_layer_color"]:
            lc[tuple(k)] += v
    print("\n--- 中心線の (線種, レイヤ, 色) 上位 ---")
    for k, v in lc.most_common(15):
        print(f"  {k}  {v}")
    ltc = Counter()
    colc = Counter()
    for k, v in lc.items():
        ltc[k[0]] += v
        colc[k[2]] += v
    print("  線種計:", ltc.most_common())
    print("  色計  :", colc.most_common())

    print("\n--- (a) 穴クロス: 作図空間(DXF単位)での延長量 × 円径 ---")
    bb = defaultdict(list)
    for r in recs:
        for hc in r["hole_cross"]:
            d = hc.get("d_dxf", 0.0)
            b = ("D<6" if d < 6 else "6-12" if d < 12 else "12-25" if d < 25 else
                 "25-60" if d < 60 else "D>=60")
            bb[b] += [hc.get("ext1_dxf", 0.0), hc.get("ext2_dxf", 0.0)]
    for b in ("D<6", "6-12", "12-25", "25-60", "D>=60"):
        if bb[b]:
            print("  " + stats(bb[b], f"ext_dxf[{b}]") +
                  "  最頻=" + str(Counter(round(v, 1) for v in bb[b]).most_common(3)))

    ext = [e for r in recs for hc in r["hole_cross"] for e in (hc["ext1_mm"], hc["ext2_mm"])]
    print("\n--- (a) 穴クロス中心線: 円の外への延長量[紙面mm] ---")
    print("  " + stats(ext, "ext"))
    byd = defaultdict(list)
    for r in recs:
        for hc in r["hole_cross"]:
            d = hc["d_mm"]
            b = ("d<6" if d < 6 else "6-12" if d < 12 else "12-25" if d < 25 else
                 "25-60" if d < 60 else "d>=60")
            byd[b] += [hc["ext1_mm"], hc["ext2_mm"]]
    for b in ("d<6", "6-12", "12-25", "25-60", "d>=60"):
        if byd[b]:
            print("  " + stats(byd[b], f"ext[{b}]"))
    # 延長量 / 半径 の比
    ratio = [max(hc["ext1_mm"], hc["ext2_mm"]) / (hc["d_mm"] / 2)
             for r in recs for hc in r["hole_cross"] if hc["d_mm"] > 0.5]
    print("  " + stats(ratio, "ext/半径"))
    extd = [e for r in recs for hc in r["hole_cross"]
            for e in (hc.get("ext1_dxf", 0.0), hc.get("ext2_dxf", 0.0))]
    print("  " + stats(extd, "ext[DXF単位=作図空間]"))
    print("  ext[紙面mm]の最頻値:", Counter(round(v, 1) for v in ext).most_common(8))
    print("  ext[DXF単位]の最頻値:", Counter(round(v, 1) for v in extd).most_common(8))

    print("\n--- (a) 用紙倍率別の延長量[紙面mm](我々の出力は用紙倍率1なので mag=1 行が直接の目標) ---")
    for m in (1.0, 1.5, 2.0, 3.0, 4.0):
        vals = [e for r in recs if r["paper_mag"] == m
                for hc in r["hole_cross"] for e in (hc["ext1_mm"], hc["ext2_mm"])]
        if vals:
            print(f"  mag={m}: " + stats(vals, "ext") +
                  "  最頻=" + str(Counter(round(v, 1) for v in vals).most_common(3)))
    print("--- (b) 用紙倍率別のはみ出し量[紙面mm] ---")
    for m in (1.0, 1.5, 2.0, 3.0, 4.0):
        vals = [o for r in recs if r["paper_mag"] == m
                for va in r["view_axis"] for o in (va["over1_mm"], va["over2_mm"])]
        if vals:
            print(f"  mag={m}: " + stats(vals, "over") +
                  "  最頻=" + str(Counter(round(v, 1) for v in vals).most_common(3)))

    ov = [o for r in recs for va in r["view_axis"] for o in (va["over1_mm"], va["over2_mm"])]
    print("\n--- (b)(d) ビュー貫通軸中心線: 輪郭からのはみ出し量[紙面mm] ---")
    print("  " + stats(ov, "over"))
    ovc = [o for r in recs for va in r["view_axis"] if va["view_has_circles"]
           for o in (va["over1_mm"], va["over2_mm"])]
    ovp = [o for r in recs for va in r["view_axis"] if not va["view_has_circles"]
           for o in (va["over1_mm"], va["over2_mm"])]
    print("  " + stats(ovc, "over[円形ビュー]"))
    print("  " + stats(ovp, "over[輪郭ビュー]"))
    print("  向き:", Counter(va["dir"] for r in recs for va in r["view_axis"]).most_common())

    pcd = [p for r in recs for p in r["pcd_circle"]]
    print(f"\n--- (c) PCD参照円: n={len(pcd)} 検算成立(穴3個以上が載る)="
          f"{sum(1 for p in pcd if p['verified_pcd'])}")
    print("  " + stats([p["d_mm"] for p in pcd], "PCD径"))
    print("  型:", Counter(p["type"] for p in pcd).most_common())

    print("\n--- (e) その他の短い中心線(輪郭ビューの穴位置マーク等) ---")
    marks = [m for r in recs for m in r["hole_edge_mark"]]
    print("  " + stats([m["len_mm"] for m in marks], "len"))

    print("\n--- 円へのカバレッジ(ゲート③の素材) ---")
    cov = defaultdict(lambda: [0, 0, 0])   # bucket -> [n, has_any, has_both]
    for r in recs:
        for c in r["circle_coverage"]:
            d = c["d_mm"]
            b = ("d<3" if d < 3 else "3-6" if d < 6 else "6-12" if d < 12 else
                 "12-25" if d < 25 else "25-60" if d < 60 else "d>=60")
            cov[b][0] += 1
            if c["h"] or c["v"]:
                cov[b][1] += 1
            if c["h"] and c["v"]:
                cov[b][2] += 1
    tot = [0, 0, 0]
    for b in ("d<3", "3-6", "6-12", "12-25", "25-60", "d>=60"):
        n, a, bo = cov[b]
        tot = [tot[0] + n, tot[1] + a, tot[2] + bo]
        if n:
            print(f"  {b:7s} n={n:5d}  中心線あり={a/n*100:5.1f}%  縦横両方={bo/n*100:5.1f}%")
    if tot[0]:
        print(f"  {'合計':7s} n={tot[0]:5d}  中心線あり={tot[1]/tot[0]*100:5.1f}%  "
              f"縦横両方={tot[2]/tot[0]*100:5.1f}%")

    per = [(len(r["hole_cross"]) + len(r["view_axis"]) + len(r["pcd_circle"])
            + len(r["hole_edge_mark"])) for r in recs]
    print("\n--- 1図面あたりの中心線エンティティ数 ---")
    print("  " + stats([float(x) for x in per], "n"))
    print("  中心線ゼロの図面:", sum(1 for r in recs if r["n_center_lines"] == 0), "/", len(recs))


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    files = sorted(glob.glob(os.path.join(DXF_DIR, "*", "*.dxf")))
    # 組立図は部品図の流儀と別物(風船・部品表が主体)なので除外する
    files = [p for p in files if u"組立" not in os.path.basename(p)]
    if limit:
        files = files[:limit]
    recs, skipped = [], []
    for i, p in enumerate(files):
        try:
            r = analyze_file(p)
        except Exception as ex:
            skipped.append((os.path.basename(p), f"ERR {type(ex).__name__}: {ex}"))
            continue
        if r is None:
            skipped.append((os.path.basename(p), "用紙倍率不明(図枠外形なし)"))
            continue
        recs.append(r)
        if (i + 1) % 20 == 0:
            print(f"  ... {i+1}/{len(files)}", flush=True)
    out = os.path.join(ROOT, u"調査", "centerline_style_stats.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"records": recs, "skipped": skipped}, f, ensure_ascii=False, indent=1)
    print(f"\nwrote {out}  (解析{len(recs)} / スキップ{len(skipped)})")
    for s in skipped[:15]:
        print("  skip:", s)
    summarize(recs)


if __name__ == "__main__":
    main()
