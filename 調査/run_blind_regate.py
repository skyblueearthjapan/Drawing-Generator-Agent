# -*- coding: utf-8 -*-
u"""盲検10部品の**再判定**ハーネス(改善サイクルA用)。

盲検テスト(調査/blind_test_report.md)で生成済みのDXFはそのまま使い、
**ゲート②と独立検証だけを現在のエンジンで掛け直す**。
SolidWorksもDXF再生成も不要(生成物 data/依頼箱/BLIND-*/生成/**.dxf が現存する)。

    python 調査/run_blind_regate.py [--out 調査/blind_regate_<tag>.json]

出力: 図番ごとに gate2.ok / 未指定件数 / 未指定の分類 / independent_verify.ok を並べ、
      修正前後の差分を取れるJSONを保存する。
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import gate2_completeness  # noqa: E402
from engine import generate_drawing  # noqa: E402

BOX = os.path.join(ROOT, u"data", u"依頼箱")


def find_cases():
    out = []
    for name in sorted(os.listdir(BOX)):
        if not name.startswith("BLIND-"):
            continue
        d = os.path.join(BOX, name)
        plan = os.path.join(d, "plan.json")
        gen = os.path.join(d, u"生成")
        if not (os.path.exists(plan) and os.path.isdir(gen)):
            continue
        dxf = None
        for f in sorted(os.listdir(gen)):
            if f.lower().endswith(".dxf"):
                dxf = os.path.join(gen, f)
                break
        if dxf is None:
            rej = os.path.join(gen, u"不合格")
            if os.path.isdir(rej):
                for f in sorted(os.listdir(rej)):
                    if f.lower().endswith(".dxf"):
                        dxf = os.path.join(rej, f)
                        break
        if dxf is None:
            continue
        out.append({"id": name, "zuban": name.replace("BLIND-", ""),
                    "plan": plan, "dxf": dxf})
    return out


def classify_unspecified(rep):
    u"""未指定の内訳を分類サマリにする(工房の『不合格理由が要約されない』対策も兼ねる)。"""
    agg = {"circle": 0, "polygon": 0, "position_X": 0, "position_Y": 0, "position_Z": 0}
    for u_ in rep["unspecified"]:
        if u_["feature"] == "position":
            agg["position_%s" % u_["axis"]] += 1
        else:
            agg[u_["feature"]] = agg.get(u_["feature"], 0) + 1
    return agg


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, u"調査", "blind_regate.json"))
    ap.add_argument("--only", default=None, help=u"図番の部分一致で絞る(例 6-02)")
    args = ap.parse_args(argv[1:])

    rows = []
    for c in find_cases():
        if args.only and args.only not in c["id"]:
            continue
        row = {"id": c["id"], "dxf": os.path.relpath(c["dxf"], ROOT)}
        try:
            rep = gate2_completeness.check_completeness(c["dxf"], c["plan"])
            row["gate2_ok"] = rep["ok"]
            row["unspecified"] = len(rep["unspecified"])
            row["unspecified_kinds"] = classify_unspecified(rep)
            row["unspecified_detail"] = [u_["reason"] for u_ in rep["unspecified"]][:40]
            row["redundant"] = len(rep["redundant_dimensions"])
            row["floating"] = len(rep["floating_dimensions"])
            row["pcd_groups"] = rep.get("pcd_groups", [])
            row["polygons"] = rep.get("polygons", [])
        except Exception as e:
            row["gate2_ok"] = None
            row["gate2_error"] = "%s: %s" % (type(e).__name__, e)
        try:
            v = generate_drawing.independent_verify(c["dxf"], c["plan"])
            row["verify_ok"] = v["ok"]
            row["verify_detail"] = {"file_attrs_ok": v["file_attrs_ok"],
                                    "gate1_ok": v["gate1_ok"],
                                    "gate1_max_diff_mm": v["gate1_max_diff_mm"],
                                    "style_ok": v["style_ok"], "note_ok": v["note_ok"]}
            row["verify_ng_dims"] = [{"id": r["id"], "measured": r["measured"],
                                      "text": r["text"], "text_value": r["text_value"],
                                      "text_role": r.get("text_role"),
                                      "text_diff_mm": r["text_diff_mm"]}
                                     for r in v["gate1"] if not r["ok"]]
            row["layout_ok"] = v["layout_ok"]
            row["frame_collisions"] = v["frame_collisions"]
        except Exception as e:
            row["verify_ok"] = None
            row["verify_error"] = "%s: %s" % (type(e).__name__, e)
        row["overall_ok"] = bool(row.get("gate2_ok") and row.get("verify_ok")
                                 and row.get("layout_ok"))
        rows.append(row)
        print(u"%-22s gate2=%-5s 未指定=%-4s verify=%-5s layout=%-5s 総合=%-5s %s"
              % (c["id"], row.get("gate2_ok"), row.get("unspecified"),
                 row.get("verify_ok"), row.get("layout_ok"), row["overall_ok"],
                 row.get("gate2_error", "") or row.get("verify_error", "")))

    g2 = sum(1 for r in rows if r.get("gate2_ok"))
    vf = sum(1 for r in rows if r.get("verify_ok"))
    ly = sum(1 for r in rows if r.get("layout_ok"))
    both = sum(1 for r in rows if r["overall_ok"])
    print(u"\n== 集計: ゲート② %d/%d / 独立検証 %d/%d / レイアウト %d/%d / 総合 %d/%d =="
          % (g2, len(rows), vf, len(rows), ly, len(rows), both, len(rows)))
    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"rows": rows,
                            "summary": {"gate2_pass": g2, "verify_pass": vf,
                                        "layout_pass": ly,
                                        "both_pass": both, "total": len(rows)}},
                           ensure_ascii=False, indent=2, default=str))
    print(u"saved %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
