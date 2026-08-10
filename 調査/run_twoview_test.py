# -*- coding: utf-8 -*-
u"""2ビュー部品のビュー数選択の実証: AUTO-002(ベアリングカラー)を **front+right の2ビュー**で再生成する。

人間図面はホルダー/端子棒/ベアリングカラーとも2ビュー(正面+右側面)。
計画JSONの `layout.views` で使用ビュー集合を指定でき、compose / dim_engine / gate2 /
generate_drawing を貫通する(既定=指定なし=従来4ビューで後方互換)。

  - 使わないビュー(top/iso)のエンティティは取り込まない
  - 残ったビューは紙面中央へバランス配置される
  - ゲート①②・独立検証がすべて合格すること(engine/generate_drawing.py をCLIで実行して確認)

実行: python 調査/run_twoview_test.py
出力: 調査/twoview_test/
"""
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, u"調査", u"twoview_test")
SRC_PLAN = os.path.join(ROOT, u"調査", u"phase5_ai_operator",
                        u"plan_AUTO-002_ベアリングカラー.json")
REQUEST = os.path.join(ROOT, u"調査", u"phase5_ai_operator", u"request_AUTO-002.json")
MODEL = os.path.join(ROOT, u"荏原トライ調整用", u"教師STEP", u"3.昇降軸", u"3-05.STEP")
VIEWS_DXF = os.path.join(ROOT, u"調査", u"phase5_ai_operator", u"views_3-05.dxf")
META_JSON = os.path.join(ROOT, u"調査", u"phase5_ai_operator", u"meta_3-05.json")


def build_plan(views, tag):
    with io.open(SRC_PLAN, encoding="utf-8") as f:
        plan = json.load(f)
    plan["_note"] = (u"調査/run_twoview_test.py が plan_AUTO-002_ベアリングカラー.json から自動生成"
                     u"(ビュー数選択の実証・使用ビュー=%s)。手で編集しないこと。" % list(views))
    plan["layout"] = {"views": list(views)}
    plan["source"]["base_dxf"] = u"調査/twoview_test/%s_ベアリングカラー.dxf" % tag
    out = os.path.join(OUT_DIR, u"plan_%s.json" % tag)
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(plan, ensure_ascii=False, indent=2))
    return out


def run_generate(plan_path, zuban):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, os.path.join(ROOT, "engine", "generate_drawing.py"),
           "--model", MODEL, "--plan", plan_path, "--request", REQUEST,
           "--out-dir", OUT_DIR, "--zuban", zuban, "--skip-sw", "--no-ledger",
           "--views-dxf", VIEWS_DXF, "--meta-json", META_JSON]
    p = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    out = p.stdout.decode("utf-8", "replace")
    result_json = os.path.join(OUT_DIR, u"%s_ベアリングカラー_result.json" % zuban)
    if not os.path.exists(result_json):
        print(out[-3000:])
        raise RuntimeError(u"result.json が出ていない(rc=%s)" % p.returncode)
    with io.open(result_json, encoding="utf-8") as f:
        return p.returncode, json.load(f)


def report(tag, d):
    s, comp, rep, g2, iv = (d["summary"], d["compose"], d["dim"],
                            d["gate2"], d["independent_verify"])
    print(u"\n===== %s =====" % tag)
    print(u"  使用ビュー: %s / 取り込まなかったビュー: %s"
          % (comp["views"], comp.get("dropped_view_entity_counts")))
    print(u"  レイアウト: gap_x=%.3f gap_y=%.3f group=%s"
          % (comp["layout"]["gap_x_mm"], comp["layout"]["gap_y_mm"],
             [round(v, 2) for v in comp["layout"]["group_wh_mm"]]))
    for k, v in sorted(comp["view_out_bbox_mm"].items()):
        print(u"    %-6s bbox=%s 中心=(%.2f, %.2f)"
              % (k, [round(x, 2) for x in v], (v[0] + v[2]) / 2, (v[1] + v[3]) / 2))
    print(u"  ゲート①=%s(独立検証の最大差 %.6fmm) / ゲート②=%s(未指定%d件・冗長%d件) / 独立検証=%s"
          % (s["gate1_ok"], iv["gate1_max_diff_mm"], s["gate2_ok"],
             len(g2["unspecified"]), len(g2["redundant_dimensions"]), s["verify_ok"]))
    print(u"  図枠=%d/112 / レイアウト衝突=%s / 総合=%s"
          % (comp["frame_check"]["frame_matched"], rep["layout"]["collisions"], s["overall_ok"]))
    print(u"  PNG: %s" % s["final_png"])
    return s["overall_ok"]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    results = {}
    # (a) 2ビュー(front+right) = 人間図面と同じ構成
    p2 = build_plan(["front", "right"], u"AUTO-002-2V")
    rc2, d2 = run_generate(p2, u"AUTO-002-2V")
    ok2 = report(u"2ビュー(front+right)", d2)
    results["two_view"] = d2["summary"]

    # (b) 対照: 同じ計画を従来どおり4ビューで(後方互換の確認)
    p4 = build_plan(["front", "top", "right", "iso"], u"AUTO-002-4V")
    rc4, d4 = run_generate(p4, u"AUTO-002-4V")
    ok4 = report(u"対照: 4ビュー(従来)", d4)
    results["four_view"] = d4["summary"]

    # 2ビューでは top/iso のエンティティが1つも入っていないこと
    dropped = d2["compose"].get("dropped_view_entity_counts") or {}
    drop_ok = set(dropped) == {"top", "iso"} and all(v > 0 for v in dropped.values())
    print(u"\n除外ビューのエンティティ数: %s -> 除外が効いている: %s" % (dropped, drop_ok))

    ok = bool(ok2 and ok4 and drop_ok)
    out = os.path.join(OUT_DIR, u"twoview_test_result.json")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"two_view": d2, "four_view": d4, "dropped": dropped,
                            "ok": ok}, ensure_ascii=False, indent=2, default=str))
    print(u"\nsaved %s" % out)
    print(u"\n===== ビュー数選択 総合: %s =====" % (u"合格" if ok else u"不合格"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
