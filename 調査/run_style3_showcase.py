# -*- coding: utf-8 -*-
u"""様式第3弾のショーケース6枚を再生成し、全ゲート結果と様式警告を1つのJSONへまとめる。

対象: BLIND-25154-6-02(傾動面板)+ BLIND2の5枚(1-27 / 2-16 / 3-02 / 4-05 / 5-05)。
SW投影は不要(`--skip-sw` で依頼箱の views.dxf / meta.json を再利用する)。

実行:
    python 調査/run_style3_showcase.py --out-dir 調査/style3/after      # 納品箱を触らない
    python 調査/run_style3_showcase.py --deliver                        # 納品箱へも反映
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INBOX = os.path.join(ROOT, u"data", u"依頼箱")
DELIVERY = os.path.join(ROOT, u"data", u"納品箱")

CASES = [
    (u"BLIND-25154-6-02", u"25154-6-02", u"傾動面板", u"6-02.STEP"),
    (u"BLIND2-25154-1-27", u"25154-1-27", u"走行フレーム踏板", u"1-27.STEP"),
    (u"BLIND2-25154-2-16", u"25154-2-16", u"指針", u"2-16.STEP"),
    (u"BLIND2-25154-3-02", u"25154-3-02", u"モータブラケット", u"3-02.STEP"),
    (u"BLIND2-25154-4-05", u"25154-4-05", u"駆動ユニットブラケット", u"4-05.STEP"),
    (u"BLIND2-25154-5-05", u"25154-5-05", u"減速機フランジ", u"5-05.STEP"),
]


def run_one(req_id, zuban, name, model_name, out_root):
    rd = os.path.join(INBOX, req_id)
    model = os.path.join(rd, model_name)
    out_dir = os.path.join(out_root, req_id)
    cmd = [sys.executable, os.path.join(ROOT, u"engine", u"generate_drawing.py"),
           "--model", model,
           "--plan", os.path.join(rd, "plan.json"),
           "--request", os.path.join(rd, u"依頼.json"),
           "--out-dir", out_dir, "--zuban", zuban, "--skip-sw",
           "--views-dxf", os.path.join(rd, "views.dxf"),
           "--meta-json", os.path.join(rd, "meta.json"),
           "--no-ledger"]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True)
    stem = u"%s_%s" % (zuban, name)
    rj = os.path.join(out_dir, stem + u"_result.json")
    if not os.path.exists(rj):
        return {"request_id": req_id, "error": (p.stderr or p.stdout).decode(
            "utf-8", "replace")[-2500:], "rc": p.returncode}
    with io.open(rj, encoding="utf-8") as f:
        res = json.load(f)
    s = res.get("summary") or res
    steps = s.get("steps") or {}
    de = steps.get("dim_engine") or {}
    return {
        "request_id": req_id, "zuban": zuban, "name": name, "rc": p.returncode,
        "ok": s.get("overall_ok"),
        "gates": {"gate1": s.get("gate1_ok"), "gate2": s.get("gate2_ok"),
                  "verify": s.get("verify_ok"), "layout": s.get("layout_ok"),
                  "centerline": (steps.get("centerline") or {}).get(
                      "gate3_circle_centerline_ok")},
        "gate2_unspecified": (steps.get("gate2") or {}).get("unspecified_count"),
        "style_warnings": de.get("style_warnings") or [],
        "n_style_warnings": len(de.get("style_warnings") or []),
        "extension_lines": de.get("extension_lines") or {},
        "circular_view_diameter_over": de.get("circular_view_diameter_over") or [],
        "nominal": de.get("nominal") or {},
        "leaders": de.get("hole_note_leader_len_mm") or [],
        "final_dxf": s.get("final_dxf"),
    }


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(ROOT, u"調査", u"style3", u"after"))
    ap.add_argument("--out", default=os.path.join(ROOT, u"調査", u"style3_showcase.json"))
    ap.add_argument("--deliver", action="store_true",
                    help=u"合格したものを data/納品箱 へ反映する")
    ap.add_argument("--only", default=None, help=u"依頼IDの部分一致で絞る")
    args = ap.parse_args(argv[1:])
    os.makedirs(args.out_dir, exist_ok=True)
    rows = []
    for req_id, zuban, name, model in CASES:
        if args.only and args.only not in req_id:
            continue
        r = run_one(req_id, zuban, name, model, args.out_dir)
        rows.append(r)
        if "error" in r:
            print(u"%-20s ERROR rc=%s\n%s" % (req_id, r["rc"], r["error"][-800:]))
            continue
        print(u"%-20s 総合=%s ゲート%s 未指定=%s 様式警告=%d 補助線[溶%s/長%s/最長%s] 呼び値未確定=%d"
              % (req_id, r["ok"], r["gates"], r["gate2_unspecified"],
                 r["n_style_warnings"],
                 r["extension_lines"].get("collinear_count"),
                 r["extension_lines"].get("long_count"),
                 r["extension_lines"].get("ext_len_max_mm"),
                 len((r["nominal"].get("pending") or []))))
        for w in r["style_warnings"]:
            print(u"      - %s" % w)
        if args.deliver and r.get("ok") and r.get("final_dxf"):
            dst = os.path.join(DELIVERY, req_id)
            os.makedirs(dst, exist_ok=True)
            src_dir = os.path.dirname(r["final_dxf"])
            for fn in os.listdir(src_dir):
                if fn.startswith(u"%s_%s" % (zuban, name)):
                    shutil.copyfile(os.path.join(src_dir, fn), os.path.join(dst, fn))
            print(u"      -> 納品箱へ反映 %s" % dst)
    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"cases": rows}, ensure_ascii=False, indent=1))
    print(u"saved %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
