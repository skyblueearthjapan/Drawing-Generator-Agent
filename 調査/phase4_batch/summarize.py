# -*- coding: utf-8 -*-
u"""phase4_batch の result.json を集計して素データを出す(スコアボードの元)。

    python 調査/phase4_batch/summarize.py [--detail]
"""
import os
import sys
import io
import json
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def load(base=None):
    base = base or HERE
    with io.open(os.path.join(HERE, "targets.json"), encoding="utf-8") as f:
        targets = json.load(f)["targets"]
    rows = []
    for t in targets:
        p = os.path.join(base, t["key"], "result.json")
        if not os.path.exists(p):
            continue
        with io.open(p, encoding="utf-8") as f:
            rows.append(json.load(f))
    return rows


def main():
    detail = "--detail" in sys.argv
    base = os.path.join(HERE, "_baseline") if "--baseline" in sys.argv else HERE
    rows = load(base)
    print(u"処理済み %d 点\n" % len(rows))

    hdr = (u"%-7s %-22s %-12s %-11s %-4s %-6s %-8s %-8s %-8s %s"
           % (u"図番", u"品名", u"形状クラス", u"正面ビュー割", u"向き", u"同点",
              u"一致度", u"外形差", u"円径差", u"総合"))
    print(hdr)
    print(u"-" * len(hdr))
    cls_c, orient_c, verdict_c = Counter(), Counter(), Counter()
    for r in rows:
        v = r.get("verdict") or {}
        o = v.get("orientation") or {}
        g = v.get("gate4") or {}
        w = g.get("worst") or {}
        fp = (r.get("view_plan") or {}).get("front") or {}
        cls_c[r.get("shape_class")] += 1
        orient_c[o.get("verdict") or v.get("verdict")] += 1
        verdict_c[v.get("verdict")] += 1
        print(u"%-7s %-22s %-12s %-11s %-4s %-6s %-8s %-8s %-8s %s" % (
            r["key"], (r["name"] or "")[:22], r.get("shape_class"),
            u"%s%+d" % ((fp.get("sw_view") or "?").replace(u"*", ""), fp.get("rotation_deg") or 0),
            o.get("verdict") or u"-",
            (u"%d" % o["n_ties"]) if o.get("n_ties") else u"-",
            (u"%.4f" % w["iou"]) if w.get("iou") is not None else u"-",
            (u"%.3f" % w["size_err"]) if w.get("size_err") is not None else u"-",
            (u"%.3f" % w["circle"]) if w.get("circle") is not None else u"-",
            v.get("verdict")))
    print()
    print(u"形状クラス:", dict(cls_c))
    print(u"向き判定  :", dict(orient_c))
    print(u"総合判定  :", dict(verdict_c))
    ok = sum(1 for r in rows
             if ((r.get("verdict") or {}).get("orientation") or {}).get("correct"))
    judged = sum(1 for r in rows
                 if ((r.get("verdict") or {}).get("orientation") or {}).get("verdict")
                 in (u"正解", u"不正解"))
    print(u"\n向きルール正解率: %d/%d = %.1f%%(判定できた%d点のうち)"
          % (ok, judged, 100.0 * ok / judged if judged else 0, judged))
    print(u"                  %d/%d = %.1f%%(処理した%d点全体)"
          % (ok, len(rows), 100.0 * ok / len(rows) if rows else 0, len(rows)))

    if detail:
        print(u"\n===== 不合格・要調査の内訳 =====")
        for r in rows:
            v = r.get("verdict") or {}
            if v.get("verdict") == u"合格":
                continue
            g = v.get("gate4") or {}
            o = v.get("orientation") or {}
            print(u"\n[%s] %s (%s) 総合=%s" % (r["key"], r["name"], r.get("shape_class"),
                                             v.get("verdict")))
            print(u"   size=%r frame ex=%s ez=%s" % (
                [round(x, 2) for x in (r.get("size_mm") or [])],
                (r.get("frame") or {}).get("ex_axis"), (r.get("frame") or {}).get("ez_axis")))
            if o:
                print(u"   向き: %s / 人間front ← SW-%s %s / 同点%r" % (
                    o.get("verdict"), o.get("matched_sw_view"), o.get("matched_transform"),
                    o.get("ties")))
            if v.get("reason"):
                print(u"   理由: %r" % (v.get("reason"),))
            for f in (g.get("fails") or []):
                print(u"   NG: %s" % f)
            for f in (g.get("warns") or []):
                print(u"   警告: %s" % f)
            for vw in (g.get("views") or []):
                print(u"     人間[%s](%s) ← SW-%s %s det=%+d iou=%.4f covSW=%.4f covH=%.4f "
                      u"外形差=%.3f 円差=%s" % (
                          vw["human_idx"], vw["role"], vw["sw_view"], vw["transform"],
                          vw["det"], vw["iou"], vw["cov_sw"], vw["cov_human"],
                          vw["size_err_mm"], vw["circle_diff_mm"]))
                if vw.get("circle_pairs"):
                    print(u"        円: %r" % (vw["circle_pairs"],))
    return 0


if __name__ == "__main__":
    sys.exit(main())
