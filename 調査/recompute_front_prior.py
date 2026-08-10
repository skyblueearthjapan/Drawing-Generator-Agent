# -*- coding: utf-8 -*-
u"""正面ビュー候補の事前分布を **60点(フェーズ4の50点 + 盲検10点)** で再計算する。

`調査/phase4_batch2/human_front_prior.py` は第1弾/第2弾の50点だけで分布を出していた。
盲検10点(調査/blind_test_report.md §1)で **上位6候補の被覆率が90%→70%** に落ちたので、
盲検の10点を足して順位を引き直し、`app/candidates.py` の定数の根拠にする。

    python 調査/recompute_front_prior.py [--out 調査/front_prior_60.json]

出力: 全体順位・形状クラス別順位・上位N被覆率・**旋盤物の主軸が紙面でどう見えていたか**。
"""
import argparse
import io
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, u"調査", "phase4_batch2"))
sys.path.insert(0, os.path.join(ROOT, "app"))

from compare_conditions import collect  # noqa: E402
import candidates as candidate_sets     # noqa: E402

# 盲検10点(調査/blind_test_report.md §1 の「人間の正面図」列と §0 の形状クラス列)。
# ○/× でなく **人間が選んだ向きそのもの** を候補集合の観測値として使う。
BLIND = [
    (u"1-04", "other",       "top",   u"回転270"),
    (u"2-06", "lathe",       "top",   u"回転270"),
    (u"3-04", "holed_block", "front", u"恒等"),
    (u"3-09", "lathe",       "top",   u"回転180"),
    (u"4-07", "plate",       "top",   u"恒等"),
    (u"4-12", "plate",       "front", u"恒等"),
    (u"5-04", "lathe",       "left",  u"回転180"),
    (u"5-08", "other",       "right", u"恒等"),
    (u"6-02", "lathe",       "front", u"恒等"),
    (u"7-06", "other",       "front", u"恒等"),
]
BLIND_BOX = os.path.join(ROOT, u"data", u"依頼箱")


def blind_main_axis(zuban):
    u"""盲検依頼の 分類.json(view_orient.classify の出力)から主軸を読む。"""
    p = os.path.join(BLIND_BOX, u"BLIND-25154-%s" % zuban, u"分類.json")
    if not os.path.exists(p):
        return None
    with io.open(p, encoding="utf-8") as f:
        return json.load(f).get("main_axis")

# compare_views の表記 -> (SWビュー名, 紙面内回転deg)
VIEW_NAME = {"front": u"*正面", "right": u"*右側面", "top": u"*平面",
             "left": u"*左側面", "back": u"*背面", "bottom": u"*底面"}
TRANSFORM_ROT = {u"恒等": 0, u"回転90": 90, u"回転180": 180, u"回転270": 270}
# 「鏡像(左右反転)」は *右側面 の鏡像 = *左側面(scoreboard §12 の注記どおり)
MIRROR_LR = {"right": ("left", 0), "left": ("right", 0),
             "front": ("back", 0), "back": ("front", 0)}

# CLAUDE.md「SW標準6ビューの軸マップ(実測)」: (paper_x, paper_y, 紙面手前)
SW_AXIS_MAP = {
    u"*正面":   ("+X", "+Y", "+Z"),
    u"*背面":   ("-X", "+Y", "-Z"),
    u"*左側面": ("+Z", "+Y", "-X"),
    u"*右側面": ("-Z", "+Y", "+X"),
    u"*平面":   ("+X", "-Z", "+Y"),
    u"*底面":   ("+X", "+Z", "-Y"),
}


def axis_role(sw_view, rot, main_axis):
    u"""主軸がそのビュー・回転で紙面上どう見えるか: horizontal / vertical / circular。"""
    px, py, nz = (a[1] for a in SW_AXIS_MAP[sw_view])
    if main_axis == nz:
        return "circular"      # 主軸が紙面法線 = 円が見えるビュー
    role = "horizontal" if main_axis == px else ("vertical" if main_axis == py else None)
    if role is None:
        return "circular"
    if int(rot) % 180 == 90:
        role = "vertical" if role == "horizontal" else "horizontal"
    return role


def normalize(view, transform):
    u"""(compare_views のビュー名, 紙面内変換) -> (SWビュー名, 回転deg) or None。"""
    if transform in TRANSFORM_ROT:
        v = VIEW_NAME.get(view)
        return (v, TRANSFORM_ROT[transform]) if v else None
    if u"左右反転" in (transform or ""):
        m = MIRROR_LR.get(view)
        if m:
            return (VIEW_NAME[m[0]], m[1])
    return None


def observations():
    rows = []
    for d in (u"調査/phase4_batch", u"調査/phase4_batch2"):
        rows += collect(os.path.join(ROOT, d))
    out = []
    for r in rows:
        if not r["reliable"]:
            continue
        o = r.get(u"対照_o") or {}
        v, t = o.get("matched_sw_view"), o.get("matched_transform")
        if not v:
            continue
        if u"恒等" in (o.get("ties") or []) and t != u"恒等":
            t = u"恒等"        # 対称で恒等と区別できない場合は恒等に寄せる(元スクリプトと同じ規則)
        key = normalize(v, t)
        if key is None:
            continue
        out.append({"key": r["key"], "shape_class": r["shape_class"],
                    "main_axis": r.get("main_axis"), "cand": key, "src": "phase4"})
    for k, cls, v, t in BLIND:
        key = normalize(v, t)
        out.append({"key": k, "shape_class": cls, "main_axis": blind_main_axis(k),
                    "cand": key, "src": "blind"})
    return out


def rank(counter):
    n = sum(counter.values())
    acc, out = 0, []
    for cand, k in counter.most_common():
        acc += k
        out.append({"sw_view": cand[0], "rot": cand[1], "count": k,
                    "pct": round(100.0 * k / n, 1), "cum_pct": round(100.0 * acc / n, 1)})
    return out


def coverage(obs, cand_list_for):
    u"""実際の candidates.py が上位N候補で人間の選択を被覆できる率。"""
    res = {}
    maxn = max(len(cand_list_for(c["shape_class"], c.get("main_axis"))) for c in obs)
    for n in range(1, maxn + 1):
        hit = 0
        for c in obs:
            cl = cand_list_for(c["shape_class"], c.get("main_axis"))[:n]
            if any((x["sw_view"], int(x["rot"])) == c["cand"] for x in cl):
                hit += 1
        res[n] = {"hit": hit, "total": len(obs), "pct": round(100.0 * hit / len(obs), 1)}
    return res


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, u"調査", "front_prior_60.json"))
    args = ap.parse_args(argv[1:])

    obs = observations()
    print(u"観測 %d 点(phase4 %d + 盲検 %d)"
          % (len(obs), sum(1 for o in obs if o["src"] == "phase4"),
             sum(1 for o in obs if o["src"] == "blind")))

    overall = rank(Counter(tuple(o["cand"]) for o in obs))
    print(u"\n===== 全体の事前分布(60点) =====")
    print(u"%-10s %-5s %5s %7s %8s" % (u"SWビュー", u"回転", u"件数", u"割合", u"累積"))
    for r in overall:
        print(u"%-10s %-5d %5d %6.1f%% %7.1f%%"
              % (r["sw_view"], r["rot"], r["count"], r["pct"], r["cum_pct"]))

    per_class = {}
    for cls in sorted({o["shape_class"] for o in obs}):
        sub = [o for o in obs if o["shape_class"] == cls]
        per_class[cls] = rank(Counter(tuple(o["cand"]) for o in sub))
        print(u"\n----- %s (%d点) -----" % (cls, len(sub)))
        for r in per_class[cls]:
            print(u"   %-10s %-5d %2d  %5.1f%%  累積 %5.1f%%"
                  % (r["sw_view"], r["rot"], r["count"], r["pct"], r["cum_pct"]))

    lathe = [o for o in obs if o["shape_class"] == "lathe" and o.get("main_axis")]
    roles = Counter(axis_role(o["cand"][0], o["cand"][1], o["main_axis"]) for o in lathe)
    print(u"\n===== 旋盤物: 人間が選んだ正面で主軸は紙面上どう見えていたか(%d点) =====" % len(lathe))
    for k, v in roles.most_common():
        print(u"   %-11s %2d 件 (%.0f%%)" % (k, v, 100.0 * v / len(lathe)))

    cov = coverage(obs, lambda cls, ax: candidate_sets.candidates_for(cls, ax))
    print(u"\n===== 現在の app/candidates.py が上位N候補で被覆する率(60点) =====")
    for n in sorted(cov):
        print(u"   上位%2d候補: %5.1f%% (%d/%d)"
              % (n, cov[n]["pct"], cov[n]["hit"], cov[n]["total"]))

    blind_only = [o for o in obs if o["src"] == "blind"]
    covb = coverage(blind_only, lambda cls, ax: candidate_sets.candidates_for(cls, ax))
    print(u"\n----- うち盲検10点だけ -----")
    for n in sorted(covb):
        print(u"   上位%2d候補: %5.1f%% (%d/%d)"
              % (n, covb[n]["pct"], covb[n]["hit"], covb[n]["total"]))

    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"observations": obs, "overall": overall,
                            "per_class": per_class,
                            "lathe_axis_roles": dict(roles),
                            "coverage_all60": cov, "coverage_blind10": covb},
                           ensure_ascii=False, indent=2, default=str))
    print(u"\nsaved %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
