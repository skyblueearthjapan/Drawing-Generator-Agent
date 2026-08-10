# -*- coding: utf-8 -*-
u"""3条件(v1 / 対照=STEP座標そのまま / v2)の向き正解率を並べて集計する。

    python 調査/phase4_batch2/compare_conditions.py [--dirs=調査/phase4_batch,調査/phase4_batch2] [--detail]

**測定器の信頼性フィルタ**(第1弾スコアボード §7-2 の申し送り「役割推定の検算」の実装):
人間図面から**部品ビューが1つも同定できない**、または **front役ビューの一致度(IoU)が
`IOU_MIN` 未満**の部品は、向きの正誤そのものが測れていないので**分母から外す**。
外した部品は「照合不能」として本数と理由を必ず出す — 黙って消さない。
フィルタは**3条件の最良値**で判定する(条件によって分母が変わると比較にならないため)。
"""
import os
import sys
import io
import json
from collections import Counter, defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

CONDS = ((u"v1", ""), (u"対照", "_baseline"), (u"v2", "_v2"))
#: front役ビューの一致度がこれ未満なら「そもそも同定できていない」と見なす
IOU_MIN = 0.60


def _load(path):
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def collect(work):
    u"""1バッチぶんの部品行を作る。"""
    targets = _load(os.path.join(work, "targets.json"))["targets"]
    batch = os.path.basename(work)
    rows = []
    for t in targets:
        base = _load(os.path.join(work, t["key"], "result.json"))
        if base is None:
            continue
        meta = _load(os.path.join(work, t["key"], "meta.json")) or {}
        cl = ((meta.get("view_orient") or {}).get("classification")) or {}
        r = {"batch": batch, "key": t["key"], "name": t["name"], "axis": t["axis"],
             "bucket_a": t["bucket_a"], "shape_class": base.get("shape_class"),
             "size_mm": base.get("size_mm"), "main_axis": cl.get("main_axis"),
             "coax": cl.get("coaxial_diameters_mm") or [],
             "front_plan": (base.get("view_plan") or {}).get("front") or {},
             "gate4": (base.get("verdict") or {}).get("verdict")}
        sz, ma = r["size_mm"] or [1, 1, 1], r["main_axis"]
        if ma and r["shape_class"] == "lathe":
            i = "XYZ".index(ma)
            r["ld"] = sz[i] / (max(v for j, v in enumerate(sz) if j != i) or 1.0)
        n_view, iou = 0, 0.0
        for label, sub in CONDS:
            d = os.path.join(work, sub, t["key"]) if sub else os.path.join(work, t["key"])
            res = _load(os.path.join(d, "result.json"))
            o = ((res or {}).get("verdict") or {}).get("orientation") or {}
            r[label] = {u"正解": u"○", u"不正解": u"×"}.get(o.get("verdict"), u"?")
            r[label + "_o"] = o
            cmpj = _load(os.path.join(d, "compare.json")) or {}
            inv = cmpj.get("human_to_sw") or {}
            n_view = max(n_view, len(inv))
            f = next((v for v in inv.values() if v.get("human_role") == "front"), None)
            if f and f.get("iou"):
                iou = max(iou, f["iou"])
        r["n_human_view"] = n_view
        r["front_iou"] = round(iou, 4)
        r["reliable"] = bool(n_view >= 1 and iou >= IOU_MIN)
        r["unreliable_reason"] = (u"部品ビューを1つも同定できない" if n_view == 0 else
                                  (u"front役の一致度 %.3f < %.2f" % (iou, IOU_MIN)
                                   if iou < IOU_MIN else u""))
        rows.append(r)
    return rows


def rate(rows, label):
    ok = sum(1 for r in rows if r[label] == u"○")
    return ok, len(rows), (100.0 * ok / len(rows) if rows else 0.0)


def block(title, rows):
    print(u"\n===== %s =====" % title)
    good = [r for r in rows if r["reliable"]]
    bad = [r for r in rows if not r["reliable"]]
    print(u"処理 %d 点 / 判定できた %d 点 / 照合不能 %d 点" % (len(rows), len(good), len(bad)))
    for label, _ in CONDS:
        ok, n, p = rate(good, label)
        print(u"  %-4s : %2d/%2d = %5.1f%%" % (label, ok, n, p))
    if not good:
        return
    print(u"  -- 形状クラス別 --")
    by = defaultdict(list)
    for r in good:
        by[r["shape_class"]].append(r)
    for cls in sorted(by):
        g = by[cls]
        print(u"    %-12s %2d点 | %s" % (cls, len(g), u" | ".join(
            u"%s %d/%d" % (l, rate(g, l)[0], len(g)) for l, _ in CONDS)))
    print(u"  -- 図枠別 --")
    for tag, pred in ((u"バケットA", lambda r: r["bucket_a"]),
                      (u"非バケットA", lambda r: not r["bucket_a"])):
        g = [r for r in good]
        g = [r for r in g if pred(r)]
        a = [r for r in rows if pred(r)]
        print(u"    %-10s 判定できた %2d/%2d 点 | %s" % (
            tag, len(g), len(a), u" | ".join(
                u"%s %d/%d" % (l, rate(g, l)[0], len(g)) for l, _ in CONDS)))


def main():
    dirs = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--dirs=")), None)
    dirs = (dirs.split(",") if dirs else [u"調査/phase4_batch", u"調査/phase4_batch2"])
    dirs = [d if os.path.isabs(d) else os.path.join(ROOT, d) for d in dirs]
    detail = "--detail" in sys.argv

    per = [(os.path.basename(d), collect(d)) for d in dirs]
    allrows = [r for _, rs in per for r in rs]

    hdr = u"%-14s %-7s %-20s %-12s %-3s %-6s %-4s %-4s %-4s %s" % (
        u"バッチ", u"図番", u"品名", u"形状クラス", u"図枠", u"front", u"v1", u"対照", u"v2", u"信頼")
    print(hdr)
    print(u"-" * len(hdr))
    for r in allrows:
        print(u"%-14s %-7s %-20s %-12s %-3s %-6s %-4s %-4s %-4s %s" % (
            r["batch"], r["key"], (r["name"] or "")[:20], r["shape_class"],
            u"A" if r["bucket_a"] else u"非A",
            (u"%.3f" % r["front_iou"]) if r["front_iou"] else u"-",
            r[u"v1"], r[u"対照"], r[u"v2"],
            u"" if r["reliable"] else (u"除外: " + r["unreliable_reason"])))

    for name, rs in per:
        block(name, rs)
    block(u"合計(第1弾+第2弾)", allrows)

    print(u"\n===== 条件間の勝ち負け(判定できた点のみ) =====")
    good = [r for r in allrows if r["reliable"]]
    for a, b in ((u"v2", u"対照"), (u"v2", u"v1"), (u"v1", u"対照")):
        win = [r["key"] for r in good if r[a] == u"○" and r[b] == u"×"]
        lose = [r["key"] for r in good if r[a] == u"×" and r[b] == u"○"]
        print(u"  %-4s vs %-4s : 勝ち %d %r / 負け %d %r" % (a, b, len(win), win,
                                                           len(lose), lose))

    print(u"\n===== 旋盤物(v2の上書きが効く唯一のクラス)の全数 =====")
    print(u"%-7s %-18s %-5s %-6s %-24s %-4s %-4s %-4s %s"
          % (u"図番", u"品名", u"主軸", u"L/D", u"同軸径", u"v1", u"対照", u"v2", u"備考"))
    for r in allrows:
        if r["shape_class"] != "lathe":
            continue
        print(u"%-7s %-18s %-5s %-6s %-24s %-4s %-4s %-4s %s" % (
            r["key"], r["name"][:18], r["main_axis"],
            (u"%.2f" % r["ld"]) if r.get("ld") is not None else u"-",
            str([round(d, 1) for d in r["coax"]])[:24],
            r[u"v1"], r[u"対照"], r[u"v2"],
            u"" if r["reliable"] else u"照合不能"))

    print(u"\n===== 照合不能(向きの分母から外した部品) =====")
    c = Counter()
    for r in allrows:
        if r["reliable"]:
            continue
        c[(r["unreliable_reason"], u"A" if r["bucket_a"] else u"非A")] += 1
        print(u"  %-14s %-7s %-20s %-11s %-3s bbox=%r %s" % (
            r["batch"], r["key"], r["name"][:20], r["shape_class"],
            u"A" if r["bucket_a"] else u"非A",
            [round(x, 1) for x in (r["size_mm"] or [])], r["unreliable_reason"]))
    print(u"  内訳: %r" % {u"%s / 図枠%s" % k: v for k, v in c.items()})

    if detail:
        print(u"\n===== v2 が外した部品の内訳 =====")
        for r in good:
            if r[u"v2"] != u"×":
                continue
            o = r[u"v2_o"] or {}
            print(u"  [%s] %-18s %-11s bbox=%r → 人間front ← SW-%s %s / 同点%d件"
                  % (r["key"], r["name"][:18], r["shape_class"],
                     [round(x, 1) for x in (r["size_mm"] or [])],
                     o.get("matched_sw_view"), o.get("matched_transform"),
                     len(o.get("ties") or [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
