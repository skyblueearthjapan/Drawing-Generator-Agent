# -*- coding: utf-8 -*-
u"""既存5図面の回帰スナップショット(修正前後で数値が一致することの機械確認)。

  TEST-002 / TEST-003 / テスト-004 / AUTO-001 / AUTO-002 を**従来設定(4ビュー・1:1)**で
  再生成し、ゲート①の全行・ゲート②の判定・図枠保持・ビューbbox・レイアウト衝突を
  1つのJSONへ正規化して保存する。修正前に --out baseline.json、修正後に --out after.json を
  取って diff すれば「全ゲート数値が一致」を機械的に示せる。

  台帳は汚さない(generate_drawing.py は --no-ledger で呼ぶ)。

実行:
    python 調査/run_regression_all.py --out <snapshot.json> [--skip-run]
"""
import argparse
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

SW_MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(ROOT)),
                            u"3D CAD Operator Agent", u"生成3D")
STEP_DIR = os.path.join(ROOT, u"荏原トライ調整用", u"教師STEP")

SCRIPT_CASES = [
    (u"TEST-002", os.path.join(ROOT, u"調査", u"run_dim_engine_test002.py"),
     os.path.join(ROOT, u"調査", u"phase3b_verify_TEST-002.json")),
    (u"TEST-003", os.path.join(ROOT, u"調査", u"run_dim_engine_test003.py"),
     os.path.join(ROOT, u"調査", u"phase4_verify_TEST-003.json")),
]

GEN_CASES = [
    {
        "id": u"テスト-004",
        "model": os.path.join(SW_MODEL_DIR, u"15015-P3-013_ホルダー.SLDPRT"),
        "plan": os.path.join(ROOT, u"調査", u"plan_TEST-002_ホルダー.json"),
        "request": os.path.join(ROOT, u"調査", u"request_TEST-004_ホルダー.json"),
        "zuban": u"テスト-004",
        "views_dxf": os.path.join(ROOT, u"調査", u"phase2_out_15015-P3-013_ホルダー.dxf"),
        "meta_json": os.path.join(ROOT, u"調査", u"phase2_meta_15015-P3-013_ホルダー.json"),
        "stem": u"テスト-004_ホルダー",
    },
    {
        "id": u"AUTO-001",
        "model": os.path.join(STEP_DIR, u"1.走行軸", u"1-18.STEP"),
        "plan": os.path.join(ROOT, u"調査", u"phase5_ai_operator",
                             u"plan_AUTO-001_クッション.json"),
        "request": os.path.join(ROOT, u"調査", u"phase5_ai_operator", u"request_AUTO-001.json"),
        "zuban": u"AUTO-001",
        "views_dxf": os.path.join(ROOT, u"調査", u"phase5_ai_operator", u"views_1-18.dxf"),
        "meta_json": os.path.join(ROOT, u"調査", u"phase5_ai_operator", u"meta_1-18.json"),
        "stem": u"AUTO-001_クッション",
    },
    {
        "id": u"AUTO-002",
        "model": os.path.join(STEP_DIR, u"3.昇降軸", u"3-05.STEP"),
        "plan": os.path.join(ROOT, u"調査", u"phase5_ai_operator",
                             u"plan_AUTO-002_ベアリングカラー.json"),
        "request": os.path.join(ROOT, u"調査", u"phase5_ai_operator", u"request_AUTO-002.json"),
        "zuban": u"AUTO-002",
        "views_dxf": os.path.join(ROOT, u"調査", u"phase5_ai_operator", u"views_3-05.dxf"),
        "meta_json": os.path.join(ROOT, u"調査", u"phase5_ai_operator", u"meta_3-05.json"),
        "stem": u"AUTO-002_ベアリングカラー",
    },
]


def run(cmd):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "replace")
    return p.returncode, out


def _read(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _gate1_rows(rows):
    return [{k: r.get(k) for k in ("id", "kind", "view", "expected", "measured",
                                   "diff_mm", "snap_max_mm", "text", "ok")} for r in rows]


def snapshot(gen_out_dir):
    snap = {}

    for tid, script, artifact in SCRIPT_CASES:
        rc, out = run([PY, script])
        d = _read(artifact)
        rep, comp = d["dim"], d["compose"]
        snap[tid] = {
            "rc": rc,
            "gate1_ok": rep["gate1_ok"],
            "gate1": _gate1_rows(rep["gate1"]),
            "hole_notes": [{k: n.get(k) for k in ("id", "view", "pattern", "ok",
                                                  "anchor_check", "leader_points",
                                                  "text_insert")}
                           for n in rep["hole_notes"]],
            "frame_matched": rep["frame_check"]["frame_matched"],
            "view_bbox": rep["view_bbox"],
            "collisions": rep["layout"]["collisions"],
            "style_check_ok": rep["style_check"]["ok"],
            "compose_view_bbox": {k: [round(x, 6) for x in v]
                                  for k, v in comp["view_out_bbox_mm"].items()},
            "compose_zone_overlaps": comp["zone_overlaps"],
            "compose_view_pair_overlaps": comp["view_pair_overlaps"],
            "compose_scale_text": comp["field_values"][u"尺度"],
        }

    for c in GEN_CASES:
        rc, out = run([PY, os.path.join(ROOT, "engine", "generate_drawing.py"),
                       "--model", c["model"], "--plan", c["plan"],
                       "--request", c["request"], "--out-dir", gen_out_dir,
                       "--zuban", c["zuban"], "--skip-sw", "--no-ledger",
                       "--views-dxf", c["views_dxf"], "--meta-json", c["meta_json"]])
        result_json = os.path.join(gen_out_dir, c["stem"] + u"_result.json")
        if not os.path.exists(result_json):
            snap[c["id"]] = {"rc": rc, "error": out[-3000:]}
            continue
        d = _read(result_json)
        s, comp, rep = d["summary"], d["compose"], d["dim"]
        snap[c["id"]] = {
            "rc": rc,
            "overall_ok": s["overall_ok"],
            "gate1_ok": s["gate1_ok"], "gate2_ok": s["gate2_ok"],
            "verify_ok": s["verify_ok"],
            "gate1": _gate1_rows(rep["gate1"]) if rep else None,
            "gate2": {"ok": d["gate2"]["ok"],
                      "unspecified": d["gate2"]["unspecified"],
                      "redundant": d["gate2"]["redundant_dimensions"]} if d["gate2"] else None,
            "independent_gate1_max_diff_mm": d["independent_verify"]["gate1_max_diff_mm"],
            "independent_ok": d["independent_verify"]["ok"],
            "frame_matched": comp["frame_check"]["frame_matched"],
            "compose_view_bbox": {k: [round(x, 6) for x in v]
                                  for k, v in comp["view_out_bbox_mm"].items()},
            "compose_zone_overlaps": comp["zone_overlaps"],
            "compose_view_pair_overlaps": comp["view_pair_overlaps"],
            "compose_scale_text": comp["field_values"][u"尺度"],
            "collisions": rep["layout"]["collisions"] if rep else None,
        }

    # ---- ゲート②の反証テスト一式 ----
    rc, out = run([PY, os.path.join(ROOT, u"調査", u"run_gate2.py")])
    g2 = _read(os.path.join(ROOT, u"調査", u"gate2_falsification.json"))
    snap["gate2_falsification"] = {"rc": rc, "summary": {
        k: {"base_ok": v["base_ok"], "detected": v["detected"], "total": v["total"],
            "base_unspecified": v["base_unspecified"],
            "base_redundant": v["base_redundant"],
            "multi_drop": [{"dropped": m["dropped"], "ok": m["ok"]} for m in v["multi_drop"]]}
        for k, v in g2.items()}}

    # ---- 独立検証(verify_generated.py) ----
    rc, out = run([PY, os.path.join(ROOT, u"調査", u"verify_generated.py"), "all"])
    ind = {"rc": rc}
    for tid in ("TEST-002", "TEST-003"):
        v = _read(os.path.join(ROOT, u"調査", u"verify_independent_%s.json" % tid))
        ind[tid] = {"ok": v["ok"], "gate1_ok": v["gate1_ok"], "style_ok": v["style_ok"],
                    "note_ok": v["note_ok"], "human_set_ok": v["human_set_ok"],
                    "frame_matched": v["frame"]["frame_matched"],
                    "gate1": [{k: r.get(k) for k in ("id", "expected", "measured",
                                                     "diff_mm", "text", "ok")}
                              for r in v["gate1"]]}
    snap["verify_generated"] = ind
    return snap


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--gen-out-dir", default=os.path.join(ROOT, u"生成図面"),
                    help=u"generate_drawing.py の納品先(既定=生成図面)")
    args = ap.parse_args(argv[1:])
    snap = snapshot(args.gen_out_dir)
    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    print("saved %s" % args.out)
    for k, v in snap.items():
        if isinstance(v, dict) and "rc" in v:
            print("  %-20s rc=%s ok=%s" % (k, v["rc"], v.get("overall_ok", v.get("gate1_ok"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
