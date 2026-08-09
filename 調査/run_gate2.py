# -*- coding: utf-8 -*-
u"""ゲート②(寸法完全性 v1)を TEST-002 / TEST-003 に掛け、反証テストまで通す。

  1. 正常系: 人間図面と同じ寸法集合で **合格** すること
  2. 反証系: 寸法を1本ずつ消して掛け直し、
       - 形状決定に必要な寸法を消したら **不合格** になること
       - 冗長寸法(和・差で導出できるもの)を消しても合格のままであること
     を全寸法について確認し、表にする

実行: python 調査/run_gate2.py
出力: 調査/gate2_TEST-002.json / gate2_TEST-003.json / gate2_falsification.json
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import gate2_completeness as g2  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CASES = [
    ("TEST-002", u"ホルダー",
     os.path.join(ROOT, u"調査", u"plan_TEST-002_ホルダー.json"),
     os.path.join(ROOT, u"生成図面", u"TEST-002_ホルダー.dxf")),
    ("TEST-003", u"端子棒",
     os.path.join(ROOT, u"調査", u"plan_TEST-003_端子棒.json"),
     os.path.join(ROOT, u"生成図面", u"TEST-003_端子棒.dxf")),
]

# 冗長な組(1本消しただけでは冗長性に吸収されるもの)をまとめて消す反証ケース。
# 「冗長だから合格」なのか「検出漏れ」なのかを切り分けるために必ず併走させる。
MULTI_DROPS = {
    "TEST-002": [["L40_total", "L35_counterbore_depth"],
                 ["L40_total", "L5_bore_land"],
                 ["L35_counterbore_depth", "L5_bore_land"]],
    "TEST-003": [["L78_total", "L28_to_step_end"],
                 ["L78_total", "L50_from_step_end"],
                 ["L28_to_step_end", "L50_from_step_end"],
                 ["D25_left", "D25_right"]],
}


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    summary = {}
    for tid, title, plan, dxf in CASES:
        print(u"\n" + u"#" * 78)
        print(u"# %s(%s)" % (tid, title))
        print(u"#" * 78)
        rep = g2.check_completeness(dxf, plan)
        g2.print_report(rep)
        out = os.path.join(ROOT, u"調査", u"gate2_%s.json" % tid)
        with io.open(out, "w", encoding="utf-8") as f:
            f.write(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        print(u"\nsaved %s" % out)

        # ---- 反証テスト -------------------------------------------------
        with io.open(plan, encoding="utf-8") as f:
            ids = [d["id"] for d in json.load(f)["dimensions"]]
        print(u"\n===== 反証テスト(%s): 寸法を1本消して掛け直す =====" % tid)
        print(u"%-24s %-8s %s" % (u"消した寸法", u"判定", u"検出内容"))
        rows = []
        for did in ids:
            r = g2.check_completeness(dxf, plan, drop_dim_ids=[did])
            reasons = [u["reason"] for u in r["unspecified"]]
            verdict = u"合格" if r["ok"] else u"不合格"
            rows.append({"dropped": did, "ok": r["ok"], "unspecified": r["unspecified"],
                         "redundant": r["redundant_dimensions"]})
            print(u"%-24s %-8s %s"
                  % (did, verdict,
                     (u" / ".join(reasons))[:120] if reasons
                     else u"(この寸法は他寸法から導出可能=冗長。元の判定でも冗長警告済み)"))
        caught = sum(1 for r in rows if not r["ok"])
        print(u"-> %d/%d 本で不合格を検出。残り %d 本は冗長寸法(和・差で導出可)"
              % (caught, len(rows), len(rows) - caught))

        print(u"\n===== 反証テスト(%s): 冗長な組をまとめて消す =====" % tid)
        multi = []
        for combo in MULTI_DROPS.get(tid, []):
            r = g2.check_completeness(dxf, plan, drop_dim_ids=combo)
            reasons = [u["reason"] for u in r["unspecified"]]
            multi.append({"dropped": combo, "ok": r["ok"], "unspecified": r["unspecified"]})
            print(u"%-44s %-8s %s"
                  % (u"+".join(combo), u"合格" if r["ok"] else u"不合格",
                     (u" / ".join(reasons))[:110] if reasons else u"(まだ導出可能)"))

        summary[tid] = {"base_ok": rep["ok"], "multi_drop": multi,
                        "base_unspecified": rep["unspecified"],
                        "base_redundant": rep["redundant_dimensions"],
                        "out_of_scope_classes": sorted({o["class"] for o in rep["out_of_scope"]}),
                        "falsification": rows,
                        "detected": caught, "total": len(rows)}

    out = os.path.join(ROOT, u"調査", u"gate2_falsification.json")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print(u"\nsaved %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
