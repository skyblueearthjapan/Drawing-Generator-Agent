# -*- coding: utf-8 -*-
u"""TEST-003(端子棒・寸法入り)の生成と検証(フェーズ4先行)。

  1. compose_drawing.compose() で図枠+4ビュー+表題欄の土台を作る(図番=テスト-003)
  2. dim_engine.apply_plan() で 調査/plan_TEST-003_端子棒.json の寸法・注記を足す
     (ゲート①内蔵。不合格なら例外で停止し、DXFは保存されない)
  3. PNG化(compose_drawing.render_png)
  4. 検証結果を 調査/phase4_verify_TEST-003.json へ保存し、要点を標準出力へ

実行: python 調査/run_dim_engine_test003.py
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import compose_drawing  # noqa: E402
from engine import dim_engine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    os.chdir(ROOT)

    views_dxf = u"調査/phase2_out_15015-P3-012_端子棒.dxf"
    meta_json = u"調査/phase2_meta_15015-P3-012_端子棒.json"
    plan_json = u"調査/plan_TEST-003_端子棒.json"
    out_dxf = u"生成図面/TEST-003_端子棒.dxf"
    out_png = u"生成図面/TEST-003_端子棒.png"
    verify_json = u"調査/phase4_verify_TEST-003.json"

    fields = {
        "品名": "端子棒",
        "図番": "テスト-003",
        "装置名": "テスト装置",
        "材質": "BC6",
        "材質形状": "マル32",
        "個数": 1,
        # BC6(青銅鋳物)の密度。phase2 metaのmass_kgはSW材質未設定で密度1000kg/m3のため上書きする
        "密度_kgm3": 8800.0,
        "製図者": "AI",
    }

    comp = compose_drawing.compose(views_dxf, meta_json, fields, scale=1.0, out_path=out_dxf)
    print(u"[1] compose: frame_check=%s / zone_overlaps=%s / view_pair_overlaps=%s"
          % (comp["frame_check"], comp["zone_overlaps"], comp["view_pair_overlaps"]))

    rep = dim_engine.apply_plan(plan_json, out_dxf)

    print(u"\n[2] ゲート①(寸法値照合) gate1_ok=%s" % rep["gate1_ok"])
    print(u"%-20s %-16s %-6s %10s %10s %10s %10s  %s"
          % ("id", "kind", "view", "expected", "measured", "diff_mm", "snap_mm", "text"))
    for r in rep["gate1"]:
        print(u"%-20s %-16s %-6s %10.4f %10.4f %10.6f %10.6f  %s"
              % (r["id"], r["kind"], r["view"], r["expected"], r["measured"],
                 r["diff_mm"], r["snap_max_mm"], r["text"]))
        if r.get("circle_check"):
            print(u"%24s circle_check: 中心%s φ%.4f -> %s"
                  % ("", r["circle_check"]["center"], r["circle_check"]["diameter"],
                     "OK" if r["circle_check"]["ok"] else "NG"))
        if r.get("cross_check"):
            cc = r["cross_check"]
            print(u"%24s cross_check(%s): 実在円φ%.4f / 実測との差 %.4fmm -> %s"
                  % ("", cc.get("view"), cc.get("found_diameter", float("nan")),
                     cc.get("diff_vs_measured_mm", float("nan")), "OK" if cc["ok"] else "NG"))

    print(u"\n[3] 穴注記")
    for n in rep["hole_notes"]:
        print(u"  %s view=%s pattern=%r anchor=%s ok=%s"
              % (n["id"], n["view"], n["pattern"], n.get("anchor_check"), n["ok"]))

    print(u"\n[4] DIMSTYLE読み戻し検証: ok=%s mismatches=%d"
          % (rep["style_check"]["ok"], len(rep["style_check"]["mismatches"])))
    for m in rep["style_check"]["mismatches"]:
        print(u"    %s" % m)

    print(u"\n[5] レイアウト衝突: %d件" % len(rep["layout"]["collisions"]))
    for c in rep["layout"]["collisions"]:
        print(u"    %s" % c)

    print(u"\n[6] 図枠保持: %s / 生成矢印ブロック: %s"
          % (rep["frame_check"], rep["arrow_blocks_created"]))
    print(u"[7] 解決した実装方式: %s" % json.dumps(rep["resolved_kinds"], ensure_ascii=False))
    print(u"    適用した既定: %s" % json.dumps(rep["defaults_applied"], ensure_ascii=False))
    for w in rep["warnings"]:
        print(u"  WARN: %s" % w)

    compose_drawing.render_png(out_dxf, out_png,
                               title=u"TEST-003 端子棒 (A3, 1:1, 第三角法・寸法入り)")
    print(u"\nsaved %s" % out_png)

    with io.open(verify_json, "w", encoding="utf-8") as f:
        f.write(json.dumps({"compose": comp, "dim": rep}, ensure_ascii=False,
                           indent=2, default=str))
    print(u"saved %s" % verify_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
