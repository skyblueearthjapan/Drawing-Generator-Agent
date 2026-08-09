# -*- coding: utf-8 -*-
u"""TEST-002(ホルダー・寸法入り)の生成と検証(フェーズ3-B)。

  1. compose_drawing.compose() で図枠+4ビュー+表題欄の土台を作る(図番=テスト-002)
  2. dim_engine.apply_plan() で 調査/plan_TEST-002_ホルダー.json の寸法・注記を足す
     (ゲート①内蔵。不合格なら例外で停止し、DXFは保存されない)
  3. PNG化(compose_drawing.render_png。DIMENSION/LEADER対応済み)
  4. 検証結果を 調査/phase3b_verify_TEST-002.json へ保存し、要点を標準出力へ

実行: python 調査/run_dim_engine_test002.py
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

    views_dxf = u"調査/phase2_out_15015-P3-013_ホルダー.dxf"
    meta_json = u"調査/phase2_meta_15015-P3-013_ホルダー.json"
    plan_json = u"調査/plan_TEST-002_ホルダー.json"
    out_dxf = u"生成図面/TEST-002_ホルダー.dxf"
    out_png = u"生成図面/TEST-002_ホルダー.png"
    verify_json = u"調査/phase3b_verify_TEST-002.json"

    fields = {
        "品名": "ホルダー",
        "図番": "テスト-002",
        "装置名": "テスト装置",
        "材質": "S45C",
        "材質形状": "マル80",
        "個数": 1,
        "密度_kgm3": 7850.0,
        "製図者": "AI",
    }

    # 1) 土台(寸法なし)。plan.source.base_dxf と同じパスへ書き、そこへ寸法を足して上書きする
    comp = compose_drawing.compose(views_dxf, meta_json, fields, scale=1.0, out_path=out_dxf)
    print(u"[1] compose: frame_check=%s / zone_overlaps=%s / view_pair_overlaps=%s"
          % (comp["frame_check"], comp["zone_overlaps"], comp["view_pair_overlaps"]))

    # 2) 寸法記入(ゲート①内蔵)
    rep = dim_engine.apply_plan(plan_json, out_dxf)

    print(u"\n[2] ゲート①(寸法値照合) gate1_ok=%s" % rep["gate1_ok"])
    print(u"%-24s %-16s %-6s %10s %10s %10s %10s  %s"
          % ("id", "kind", "view", "expected", "measured", "diff_mm", "snap_mm", "text"))
    for r in rep["gate1"]:
        print(u"%-24s %-16s %-6s %10.4f %10.4f %10.4f %10.4f  %s"
              % (r["id"], r["kind"], r["view"], r["expected"], r["measured"],
                 r["diff_mm"], r["snap_max_mm"], r["text"]))
        if r.get("cross_check"):
            cc = r["cross_check"]
            print(u"%28s cross_check(%s): 実在円φ%.4f / 実測との差 %.4fmm -> %s"
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
    for w in rep["warnings"]:
        print(u"  WARN: %s" % w)

    # 3) PNG
    compose_drawing.render_png(out_dxf, out_png,
                               title=u"TEST-002 ホルダー (A3, 1:1, 第三角法・寸法入り)")
    print(u"\nsaved %s" % out_png)

    with io.open(verify_json, "w", encoding="utf-8") as f:
        f.write(json.dumps({"compose": comp, "dim": rep}, ensure_ascii=False,
                           indent=2, default=str))
    print(u"saved %s" % verify_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
