# -*- coding: utf-8 -*-
u"""BLIND2納品5枚を「中心線OFF」と「中心線ON(既定)」の2条件で再生成し、
差分が中心線の追加だけであることと、中心線ゲートの結果を機械確認する。

OFF条件は計画JSONの `defaults.centerline.enabled=false` を使う
(= 抑制オプションが本当に効くことの実証も兼ねる)。

出力:
  調査/centerline/blind2_off/*.dxf   中心線OFF(= 改修前と同じ図面)
  調査/centerline/blind2/*.dxf       中心線ON(既定)
  調査/centerline_blind2_report.json

実行:
    python 調査/run_centerline_blind2.py
"""
import io
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

PY = sys.executable
INBOX = os.path.join(ROOT, "data", u"依頼箱")
WORK = os.path.join(ROOT, u"調査", u"centerline")
OUT_ON = os.path.join(WORK, "blind2")
OUT_OFF = os.path.join(WORK, "blind2_off")
PLAN_DIR = os.path.join(WORK, "plans_off")

REQUESTS = ["BLIND2-25154-1-27", "BLIND2-25154-2-16", "BLIND2-25154-3-02",
            "BLIND2-25154-4-05", "BLIND2-25154-5-05"]

MODEL_EXT = (".STEP", ".step", ".stp", ".SLDPRT", ".sldprt")


def find_model(rd):
    for f in sorted(os.listdir(rd)):
        if f.endswith(MODEL_EXT):
            return os.path.join(rd, f)
    raise IOError(u"3Dモデルが無い: %s" % rd)


def make_off_plan(rd, req_id):
    src = os.path.join(rd, "plan.json")
    with io.open(src, encoding="utf-8") as f:
        plan = json.load(f)
    plan.setdefault("defaults", {})["centerline"] = {"enabled": False}
    dst = os.path.join(PLAN_DIR, req_id + "_off.json")
    with io.open(dst, "w", encoding="utf-8") as f:
        f.write(json.dumps(plan, ensure_ascii=False, indent=1))
    return dst


def gen(req_id, plan_path, out_dir):
    rd = os.path.join(INBOX, req_id)
    with io.open(os.path.join(rd, u"依頼.json"), encoding="utf-8") as f:
        request = json.load(f)
    zuban = request.get(u"図番", req_id)
    cmd = [PY, os.path.join(ROOT, "engine", "generate_drawing.py"),
           "--model", find_model(rd), "--plan", plan_path,
           "--request", os.path.join(rd, u"依頼.json"),
           "--out-dir", out_dir, "--zuban", zuban, "--skip-sw", "--no-ledger",
           "--views-dxf", os.path.join(rd, "views.dxf"),
           "--meta-json", os.path.join(rd, "meta.json")]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "replace")
    try:
        summary = json.loads(out[out.index("{"):])
    except Exception:
        return {"rc": p.returncode, "error": out[-2000:]}
    return {"rc": p.returncode, "summary": summary}


def main():
    for d in (OUT_ON, OUT_OFF, PLAN_DIR):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from verify_centerline_only_diff import compare  # noqa: E402
    recs = []
    for req_id in REQUESTS:
        rd = os.path.join(INBOX, req_id)
        off_plan = make_off_plan(rd, req_id)
        r_off = gen(req_id, off_plan, OUT_OFF)
        r_on = gen(req_id, os.path.join(rd, "plan.json"), OUT_ON)
        rec = {"request": req_id, "off": {}, "on": {}}
        for tag, r in (("off", r_off), ("on", r_on)):
            s = (r.get("summary") or {})
            rec[tag] = {"rc": r["rc"], "overall_ok": s.get("overall_ok"),
                        "gate1_ok": s.get("gate1_ok"), "gate2_ok": s.get("gate2_ok"),
                        "verify_ok": s.get("verify_ok"), "layout_ok": s.get("layout_ok"),
                        "centerline": (s.get("steps") or {}).get("centerline"),
                        "dxf": s.get("final_dxf"), "png": s.get("final_png"),
                        "error": r.get("error")}
        a, b = rec["off"].get("dxf"), rec["on"].get("dxf")
        if a and b and os.path.exists(a) and os.path.exists(b):
            rec["diff"] = compare(a, b)
        recs.append(rec)

    out = os.path.join(ROOT, u"調査", "centerline_blind2_report.json")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(recs, ensure_ascii=False, indent=1))

    print(u"%-22s %-8s %-8s %-24s %s" % (u"依頼", u"OFF合格", u"ON合格", u"中心線(本/被覆)", u"差分=中心線のみ"))
    for r in recs:
        cl = r["on"].get("centerline") or {}
        cov = (u"%d本 %d/%d" % (cl.get("added", 0),
                                cl.get("n_features", 0) - cl.get("n_missing", 0),
                                cl.get("n_features", 0))) if cl else "-"
        d = r.get("diff") or {}
        print(u"%-22s %-8s %-8s %-24s %s (中心線以外 %s->%s)"
              % (r["request"], r["off"].get("overall_ok"), r["on"].get("overall_ok"),
                 cov, u"○" if d.get("ok") else u"×",
                 (d.get("non_centerline_entities") or ["?", "?"])[0],
                 (d.get("non_centerline_entities") or ["?", "?"])[1]))
    print(u"\n出力:", out)
    ok = all(r["on"].get("overall_ok") and (r.get("diff") or {}).get("ok") for r in recs)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
