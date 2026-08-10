# -*- coding: utf-8 -*-
u"""STEP由来のSW投影ビュー vs 人間の部品図 の照合。

照合指標(タスク指定)
  (a) 円の半径集合が一致するか
  (b) ビュー外形(幅×高さ)が一致するか
  (c) ビューの向き・並びが人間図面と同じか
      = 8通りの平面変換(回転4 × 鏡像2)のうちどれで一致するかを機械探索する。
        **行列式-1(鏡像)を必ず候補に入れる**(姉妹プロジェクトの教訓)。
        対称形状では複数の変換が同点になる → その旨を「判定不能(対称)」として明示する。

    python 調査/compare_views.py <label> <人間DXF> [--scale 1.0] [--no-frame] [--gap 8]
"""
import os
import sys
import io
import json
import math
import argparse
import re
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ezdxf  # noqa: E402
import frame_extract  # noqa: E402
import geom_lib as gl  # noqa: E402

CELL = 0.4          # ラスタ格子(mm)
SIZE_TOL = 0.5      # 外形一致とみなす許容差(mm)。0.5mm超は「不一致」として要調査

#: (名前, 2x2行列, 行列式)
D4 = [
    (u"恒等",            (1, 0, 0, 1), 1),
    (u"回転90",          (0, -1, 1, 0), 1),
    (u"回転180",         (-1, 0, 0, -1), 1),
    (u"回転270",         (0, 1, -1, 0), 1),
    (u"鏡像(左右反転)",   (-1, 0, 0, 1), -1),
    (u"鏡像+回転90",      (0, 1, 1, 0), -1),
    (u"鏡像(上下反転)",   (1, 0, 0, -1), -1),
    (u"鏡像+回転270",     (0, -1, -1, 0), -1),
]


def sample_points(entities, kinds=("solid", "hidden", "tangent"), step=0.3):
    u"""エッジを等間隔サンプリングした点列(mm)。"""
    pts = []
    for e, k in entities:
        if k not in kinds:
            continue
        t = e.dxftype()
        if t == "LINE":
            x1, y1 = e.dxf.start.x, e.dxf.start.y
            x2, y2 = e.dxf.end.x, e.dxf.end.y
            n = max(1, int(math.hypot(x2 - x1, y2 - y1) / step))
            for i in range(n + 1):
                pts.append((x1 + (x2 - x1) * i / n, y1 + (y2 - y1) * i / n))
        elif t == "CIRCLE":
            # ❗`_pts_of` は bbox 用に「四隅」を返す。サンプリングにそのまま使うと
            #   菱形のニセ線分が入って一致率が落ちる(実測で front の IoU が 0.89 止まりになった)
            c, r = e.dxf.center, e.dxf.radius
            n = max(24, int(2 * math.pi * r / step))
            for i in range(n):
                a = 2 * math.pi * i / n
                pts.append((c.x + r * math.cos(a), c.y + r * math.sin(a)))
        else:
            p = gl._pts_of(e)
            for i in range(len(p) - 1):
                x1, y1 = p[i]
                x2, y2 = p[i + 1]
                n = max(1, int(math.hypot(x2 - x1, y2 - y1) / step))
                for j in range(n + 1):
                    pts.append((x1 + (x2 - x1) * j / n, y1 + (y2 - y1) * j / n))
    return pts


def norm(pts):
    if not pts:
        return [], (0.0, 0.0)
    x0 = min(p[0] for p in pts)
    y0 = min(p[1] for p in pts)
    x1 = max(p[0] for p in pts)
    y1 = max(p[1] for p in pts)
    return [(p[0] - x0, p[1] - y0) for p in pts], (x1 - x0, y1 - y0)


def apply_t(pts, m):
    a, b, c, d = m
    return [(a * x + b * y, c * x + d * y) for x, y in pts]


def cells(pts):
    return set((int(round(x / CELL)), int(round(y / CELL))) for x, y in pts)


_NB = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]


def _dilate(s):
    out = set()
    for x, y in s:
        for dx, dy in _NB:
            out.add((x + dx, y + dy))
    return out


def iou(a, b):
    u"""被覆率ベースの一致度(0..1)。

    ❗素の格子IoUは**半セルずれで壊れる**: 25mm を CELL=0.4 で切ると 62.5 セルになり、
      90度回転+再正規化で 0.5 セルずれて完全対称な図形でも IoU が 0.76 に落ちた(実測)。
      片側を1セル膨張させた相互被覆率(の小さい方)を使えばずれに強く、
      かつ 0.4mm 級の形状差はちゃんと落ちる。
    """
    if not a or not b:
        return 0.0, 0.0, 0.0
    da, db = _dilate(a), _dilate(b)
    cov_a = len([c for c in a if c in db]) / float(len(a))
    cov_b = len([c for c in b if c in da]) / float(len(b))
    return min(cov_a, cov_b), cov_a, cov_b


def view_record(cluster, scale=1.0):
    ents = cluster["entities"]
    pts = [(x * scale, y * scale) for x, y in sample_points(ents)]
    npts, size = norm(pts)
    circ = gl.circles_of(ents)
    bb = cluster["bbox"]
    circles = sorted([(round(r * 2 * scale, 3),
                       round((cx - bb[0]) * scale, 3),
                       round((cy - bb[1]) * scale, 3)) for cx, cy, r, _ in circ])
    return {"pts": npts, "size": [size[0], size[1]], "circles": circles,
            "bbox": [v * scale for v in bb], "n": cluster["n"],
            "types": dict(Counter("%s/%s" % (e.dxftype(), k) for e, k in ents))}


def best_transform(a, b):
    u"""a(SW側) を D4 変換して b(人間側) に最も合う変換を探す。"""
    cb = cells(b["pts"])
    res = []
    for name, m, det in D4:
        tp, _ = norm(apply_t(a["pts"], m))
        # サイズが違うなら早期に低スコア
        w = max(p[0] for p in tp) if tp else 0.0
        h = max(p[1] for p in tp) if tp else 0.0
        size_err = max(abs(w - b["size"][0]), abs(h - b["size"][1]))
        score, cov_sw, cov_h = iou(cells(tp), cb)
        res.append({"transform": name, "det": det, "iou": round(score, 4),
                    "cov_sw": round(cov_sw, 4), "cov_human": round(cov_h, 4),
                    "size_err_mm": round(size_err, 4),
                    "size": [round(w, 3), round(h, 3)]})
    res.sort(key=lambda r: (-r["iou"], r["size_err_mm"]))
    return res


_BALLOON_RE = re.compile(r"^\d+-\d+$")


def find_balloons(ents):
    u"""風船(部品番号の丸)を特定する。

    ❗**風船の径は図面ごとに違う**(25154-1-18 は φ10、25154-1-25 は φ16)。さらに
      **部品の輪郭の内側に置かれることがある**ので「孤立した円」フィルタでは落ちない。
      実際 1-25 では φ16 の風船を部品の穴と誤認して人間側被覆が 0.87 に落ちた。
      → 「円の中に `NN-N` 形式の文字がある」ことで判定する。
    """
    texts = []
    for e in ents:
        t = e.dxftype()
        if t not in ("TEXT", "MTEXT"):
            continue
        s = e.dxf.text if t == "TEXT" else e.text
        s = re.sub(r"\\[A-Za-z][^;]*;", "", s or "")
        s = zen2han(s).strip()
        if _BALLOON_RE.match(s):
            texts.append((e.dxf.insert.x, e.dxf.insert.y))
    out = set()
    for e in ents:
        if e.dxftype() != "CIRCLE":
            continue
        c, r = e.dxf.center, e.dxf.radius
        for tx, ty in texts:
            if abs(tx - c.x) <= r and abs(ty - c.y) <= r * 1.5:
                out.add(id(e))
                break
    return out


def load_human(path, use_frame=True, gap=8.0):
    doc = ezdxf.readfile(path)
    ents = None
    fsum = None
    if use_frame:
        ents, fsum = frame_extract.subtract_frame(
            doc, template_path=os.path.join(ROOT, u"図枠", u"frame_template.dxf"))
    src = list(ents) if ents is not None else list(doc.modelspace())
    balloons = find_balloons(src)
    ents = [e for e in src if id(e) not in balloons]
    pairs = gl.collect(doc, ents)
    # ❗単一のギャップ幅では割れない/くっつく。実測:
    #   gap=8 だと 3-05 の側面図(4.75x40)に**表面性状記号(▽)**がくっついて 17.36x51.72 になり
    #   ビューとして認識できない。逆に gap を詰めると板物の中の穴が本体から切れる。
    #   → 複数のギャップで候補を作って重複を除き、最終的に照合スコアで選ばせる。
    kept = []
    seen = set()
    for g in sorted({gap, 4.0, 2.0}, reverse=True):
        for c in gl.cluster_views(pairs, gap=g):
            types = Counter(e.dxftype() for e, _ in c["entities"])
            if c["n"] == 1 and types.get("CIRCLE") == 1:
                continue                      # 風船(単独の円)
            key = tuple(round(v, 3) for v in c["bbox"])
            if key in seen:
                continue
            seen.add(key)
            c["gap"] = g
            kept.append(c)
    # --- 用紙倍率(図枠が実寸の何倍で描かれているか)を作図範囲から推定 ---
    xs = []
    for e in doc.modelspace():
        if e.dxftype() == "LINE":
            xs += [e.dxf.start.x, e.dxf.end.x]
    paper_mag = 1.0
    if xs:
        w = max(xs) - min(xs)
        paper_mag = max(1.0, round(w / 410.0))     # バケットA の図枠内寸 410mm が基準
    # 尺度テキスト(バケットAのアンカー(176.415,22.446)を用紙倍率でスケールした位置)
    scale_text = None
    for e in doc.modelspace():
        if e.dxftype() == "MTEXT":
            p = e.dxf.insert
            if (abs(p.x - 176.415 * paper_mag) < 1.5 * paper_mag and
                    abs(p.y - 22.446 * paper_mag) < 1.5 * paper_mag):
                scale_text = e.text
    return doc, kept, fsum, scale_text, paper_mag


def zen2han(s):
    return (s or "").translate({c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)})


def scale_factor_from_text(t):
    u"""'１：２' → 2.0(図面1mm = 実物2mm)。'１：１' → 1.0。読めなければ None。"""
    h = zen2han(t or "").replace(u"：", ":").replace(u"／", "/")
    h = h.split(";")[-1].strip()
    for sep in (":", "/"):
        if sep in h:
            a, b = h.split(sep)[:2]
            try:
                return float(b) / float(a)
            except ValueError:
                return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("label")
    ap.add_argument("human")
    ap.add_argument("--scale", type=float, default=None,
                    help=u"人間図面の1単位が実物何mmか(未指定なら尺度セルから自動)")
    ap.add_argument("--no-frame", action="store_true")
    ap.add_argument("--gap", type=float, default=8.0)
    ap.add_argument("--sw-gap", type=float, default=8.0)
    ap.add_argument("--paper-mag", type=float, default=None)
    args = ap.parse_args()

    sw_dxf = os.path.join(ROOT, u"調査", u"step_check", u"views_%s.dxf" % args.label)
    meta_p = os.path.join(ROOT, u"調査", u"step_check", u"meta_%s.json" % args.label)
    with io.open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)

    human_p = args.human if os.path.isabs(args.human) else os.path.join(ROOT, args.human)
    hdoc, hcl, fsum, stext, pmag = load_human(human_p, not args.no_frame, args.gap)
    if args.paper_mag:
        pmag = args.paper_mag
    # ❗DXF1単位 → 実物mm = (尺度の分母/分子) / 用紙倍率。
    #   バケットA(410x287・倍率1・尺度1:1)では 1.0 だが、25154-1-12 は
    #   **用紙倍率3 × 尺度1:3** なので偶然また 1.0 になる。倍率と尺度は必ず別々に見ること。
    sf = args.scale if args.scale else ((scale_factor_from_text(stext) or 1.0) / pmag)

    out = {"label": args.label, "human_dxf": os.path.basename(human_p),
           "frame_summary": fsum, "scale_text": stext, "scale_factor": sf,
           "paper_magnification": pmag,
           "model_size_mm": meta["metrics"]["size_mm"],
           "model_volume_mm3": meta["metrics"]["volume_mm3"],
           "model_bbox_mm": meta["metrics"]["bbox_mm"]}
    print(u"== %s ==" % args.label)
    print(u"人間図面: %s" % os.path.basename(human_p))
    print(u"図枠差し引き: %r" % (fsum,))
    print(u"用紙倍率=%.3g / 尺度セル=%r → DXF1単位=実物%.4gmm" % (pmag, stext, sf))
    print(u"モデル: size=%r vol=%.3f" % (meta["metrics"]["size_mm"], meta["metrics"]["volume_mm3"]))

    # --- SW側のビューを meta の geom_mm で同定する ---
    sdoc = ezdxf.readfile(sw_dxf)
    spairs = gl.collect(sdoc)
    scl = gl.cluster_views(spairs, gap=args.sw_gap)
    sw_views = {}
    for key in ("front", "top", "right", "iso"):
        g = meta["views"][key]["geom_mm"]
        best, bd = None, 1e9
        for c in scl:
            d = sum(abs(c["bbox"][i] - g[i]) for i in range(4))
            if d < bd:
                best, bd = c, d
        sw_views[key] = {"cluster": best, "match_err": bd}

    print(u"\n-- SW投影ビュー --")
    sw_rec = {}
    for key in ("front", "top", "right"):
        c = sw_views[key]["cluster"]
        r = view_record(c)
        sw_rec[key] = r
        print(u"  %-5s size=%.3fx%.3f 円d=%r %s" % (
            key, r["size"][0], r["size"][1], [c0 for c0, _, _ in r["circles"]], r["types"]))

    print(u"\n-- 人間図面のビュー候補(実物mm換算) --")
    h_rec = []
    sw_sizes = [tuple(round(v, 3) for v in sw_rec[k]["size"]) for k in ("front", "top", "right")]
    for i, c in enumerate(hcl):
        r = view_record(c, sf)
        # SWのどれかのビュー外形(またはその転置)と一致するものだけを部品ビューとみなす
        w, h = r["size"]
        r["is_part_view"] = any(
            (abs(w - a) < SIZE_TOL and abs(h - b) < SIZE_TOL) or
            (abs(w - b) < SIZE_TOL and abs(h - a) < SIZE_TOL) for a, b in sw_sizes)
        h_rec.append(r)
        print(u"  [%d] size=%.3fx%.3f bbox=(%.2f,%.2f) 円d=%r n=%d %s %s" % (
            i, r["size"][0], r["size"][1], c["bbox"][0], c["bbox"][1],
            [c0 for c0, _, _ in r["circles"]], r["n"], r["types"],
            u"◀部品ビュー" if r["is_part_view"] else u""))

    # --- 人間図面のビュー役割(第三角法の配置から推定) ---
    part_idx = [i for i, r in enumerate(h_rec) if r["is_part_view"]]
    roles = {}
    if part_idx:
        def cx(i):
            b = h_rec[i]["bbox"]
            return (b[0] + b[2]) / 2.0

        def cy(i):
            b = h_rec[i]["bbox"]
            return (b[1] + b[3]) / 2.0
        # 正面 = 「左下」(左に他ビューが無く、下にも他ビューが無い)
        base = min(part_idx, key=lambda i: (cx(i), cy(i)))
        roles[base] = "front"
        for i in part_idx:
            if i == base:
                continue
            dx = cx(i) - cx(base)
            dy = cy(i) - cy(base)
            if abs(dy) < max(h_rec[base]["size"][1], h_rec[i]["size"][1]) * 0.6 and dx > 0:
                roles[i] = "right"      # 第三角法: 右にあるのは右側面図
            elif abs(dx) < max(h_rec[base]["size"][0], h_rec[i]["size"][0]) * 0.6 and dy > 0:
                roles[i] = "top"        # 第三角法: 上にあるのは平面図
            else:
                roles[i] = "other"
    out["human_roles"] = {str(i): roles.get(i, "-") for i in part_idx}
    print(u"  人間図面の役割推定(第三角法):",
          {"[%d]" % i: roles.get(i, "-") for i in part_idx})

    print(u"\n-- 照合(SWビュー → 人間ビュー) --")
    results = {}
    for key in ("front", "top", "right"):
        a = sw_rec[key]
        rows = []
        for i, b in enumerate(h_rec):
            if not b.get("is_part_view"):
                continue
            tr = best_transform(a, b)
            rows.append({"human_idx": i, "human_size": [round(v, 3) for v in b["size"]],
                         "best": tr[0], "all": tr})
        if not rows:
            print(u"  %-5s → 一致する人間ビュー無し" % key)
            results[key] = {"error": u"部品ビュー候補が無い"}
            continue
        rows.sort(key=lambda r: -r["best"]["iou"])
        top = rows[0]
        # 同点の変換(対称形状)を列挙
        ties = [t["transform"] for t in top["all"]
                if abs(t["iou"] - top["best"]["iou"]) < 1e-6]
        mirror_needed = (top["best"]["det"] == -1) and all(
            t["det"] == -1 for t in top["all"] if abs(t["iou"] - top["best"]["iou"]) < 1e-6)
        identity_iou = [t["iou"] for t in top["all"] if t["transform"] == u"恒等"][0]
        results[key] = {"best_human_idx": top["human_idx"],
                        "human_role": roles.get(top["human_idx"], "-"),
                        "identity_iou": identity_iou,
                        "identity_is_best": abs(identity_iou - top["best"]["iou"]) < 1e-6,
                        "human_size": top["human_size"],
                        "sw_size": [round(v, 3) for v in a["size"]],
                        "iou": top["best"]["iou"],
                        "cov_sw": top["best"]["cov_sw"],
                        "cov_human": top["best"]["cov_human"],
                        "transform": top["best"]["transform"],
                        "det": top["best"]["det"],
                        "size_err_mm": top["best"]["size_err_mm"],
                        "tie_transforms": ties,
                        "mirror_required": bool(mirror_needed),
                        "sw_circle_d": [c0 for c0, _, _ in a["circles"]],
                        "human_circle_d": ([c0 for c0, _, _ in h_rec[top["human_idx"]]["circles"]]
                                           if h_rec else []),
                        "ranking": [{"human_idx": r["human_idx"], "iou": r["best"]["iou"],
                                     "transform": r["best"]["transform"]} for r in rows[:4]]}
        print(u"  %-5s → 人間[%d](役割=%s) 一致=%.4f (SW被覆%.4f/人間被覆%.4f) 変換=%s(det=%+d) "
              u"外形差=%.3fmm 恒等=%.4f 同点=%r" % (
                  key, top["human_idx"], roles.get(top["human_idx"], "-"),
                  top["best"]["iou"], top["best"]["cov_sw"], top["best"]["cov_human"],
                  top["best"]["transform"], top["best"]["det"],
                  top["best"]["size_err_mm"], identity_iou, ties))
        print(u"        SW円d=%r / 人間円d=%r" % (
            results[key]["sw_circle_d"], results[key]["human_circle_d"]))
        # 役割が同じ人間ビューとの照合(本命)
        same = [i for i in part_idx if roles.get(i) == key]
        if same:
            b = h_rec[same[0]]
            tr = best_transform(a, b)
            tr.sort(key=lambda r: (-r["iou"], r["size_err_mm"]))
            t0 = tr[0]
            idt = [t for t in tr if t["transform"] == u"恒等"][0]
            results[key]["role_match"] = {
                "human_idx": same[0], "best": t0, "identity": idt,
                "ties": [t["transform"] for t in tr if abs(t["iou"] - t0["iou"]) < 1e-6],
                "human_circle_d": [c0 for c0, _, _ in b["circles"]],
                "size_human": [round(v, 3) for v in b["size"]]}
            print(u"        ★役割一致[%d]: 最良=%s(det=%+d) 一致=%.4f / 恒等=%.4f "
                  u"外形差=%.3fmm 同点=%r" % (
                      same[0], t0["transform"], t0["det"], t0["iou"], idt["iou"],
                      t0["size_err_mm"], results[key]["role_match"]["ties"]))
    # --- 逆方向(本命の問い): 人間の各ビューは「モデルのどの向き」から見た図か ---
    print(u"\n-- 逆引き(人間ビュー ← SWのどのビュー方向か) --")
    inv = {}
    for i in part_idx:
        b = h_rec[i]
        cand = []
        for key in ("front", "top", "right"):
            tr = best_transform(sw_rec[key], b)
            tr.sort(key=lambda r: (-r["iou"], r["size_err_mm"]))
            cand.append({"sw_view": key, "best": tr[0],
                         "ties": [t["transform"] for t in tr
                                  if abs(t["iou"] - tr[0]["iou"]) < 1e-6],
                         "identity_iou": [t["iou"] for t in tr
                                          if t["transform"] == u"恒等"][0]})
        # 同点のときは「役割が同じSWビュー」を優先する(対称部品では複数ビューが合同になり、
        # 隠れ線の本数差だけで順位が決まってしまうため)
        cand.sort(key=lambda c: (-c["best"]["iou"], c["best"]["size_err_mm"],
                                 0 if c["sw_view"] == roles.get(i) else 1))
        top = cand[0]
        mirror_only = all(t in (u"鏡像(左右反転)", u"鏡像+回転90", u"鏡像(上下反転)",
                                u"鏡像+回転270") for t in top["ties"])
        inv[str(i)] = {"human_role": roles.get(i, "-"),
                       "human_size": [round(v, 3) for v in b["size"]],
                       "human_circle_d": [c0 for c0, _, _ in b["circles"]],
                       "sw_view": top["sw_view"],
                       "transform": top["best"]["transform"],
                       "det": top["best"]["det"],
                       "iou": top["best"]["iou"],
                       "cov_sw": top["best"]["cov_sw"],
                       "cov_human": top["best"]["cov_human"],
                       "size_err_mm": top["best"]["size_err_mm"],
                       "ties": top["ties"],
                       "mirror_required": bool(mirror_only),
                       "ranking": [{"sw_view": c["sw_view"], "iou": c["best"]["iou"],
                                    "transform": c["best"]["transform"]} for c in cand]}
        print(u"  人間[%d](%s %.1f×%.1f) ← SW-%s を %s(det=%+d) 一致=%.4f "
              u"(SW被覆%.4f/人間被覆%.4f) 外形差=%.3fmm 同点=%r%s" % (
                  i, roles.get(i, "-"), b["size"][0], b["size"][1], top["sw_view"],
                  top["best"]["transform"], top["best"]["det"], top["best"]["iou"],
                  top["best"]["cov_sw"], top["best"]["cov_human"],
                  top["best"]["size_err_mm"], top["ties"],
                  u"  ❗鏡像が必要" if mirror_only else u""))
    out["human_to_sw"] = inv

    out["sw_views"] = {k: {"size": v["size"], "circles": v["circles"]} for k, v in sw_rec.items()}
    out["human_views"] = [{"size": r["size"], "circles": r["circles"], "bbox": r["bbox"]}
                          for r in h_rec]
    out["match"] = results

    op = os.path.join(ROOT, u"調査", u"step_check", u"compare_%s.json" % args.label)
    with io.open(op, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(u"\n保存: %s" % op)


if __name__ == "__main__":
    main()
