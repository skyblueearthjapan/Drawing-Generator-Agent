# -*- coding: utf-8 -*-
u"""尺度対応(scale≠1)の実証: ホルダーを **1:2** で生成し、全ゲートを通す。

自社流儀の確認事項(2026-08-10 ディレクター指示):
  - 作図(ジオメトリ)は 1/2 になる
  - **寸法値は尺度に関係なくモデル実寸**を表示する(DIMSTYLE の dimlfac=1/scale)
  - 表題欄の尺度欄が「１：２」になる
  - ゲート①(dim_engine内蔵)・ゲート②(完全性)・独立検証がすべて合格する

計画は 調査/plan_TEST-002_ホルダー.json から機械的に派生させる(寸法集合は同一)。
引出線・注記だけは絶対座標指定のため、新レイアウトに合わせて再計算して書き込む。
派生計画は 調査/plan_SCALE-002_ホルダー_1to2.json として保存する(再現用)。

実行: python 調査/run_scale_test.py
出力: 調査/scale_test/ (DXF/PNG/結果JSON)
"""
import io
import json
import math
import os
import sys

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import compose_drawing  # noqa: E402
from engine import dim_engine  # noqa: E402
from engine import gate2_completeness  # noqa: E402
from engine import generate_drawing  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, u"調査", u"scale_test")
SCALE = 0.5


def build_plan():
    u"""TEST-002の計画から1:2版を機械的に作る(寸法集合は同一・座標指定はモデル空間のまま)。"""
    with io.open(os.path.join(ROOT, u"調査", u"plan_TEST-002_ホルダー.json"),
                 encoding="utf-8") as f:
        plan = json.load(f)
    plan["_note"] = (u"調査/run_scale_test.py が plan_TEST-002_ホルダー.json から自動生成。"
                     u"尺度1:2の実証用(寸法集合はTEST-002と同一)。手で編集しないこと。")
    plan["part"][u"図番"] = u"スケール-002"
    plan["source"]["scale"] = SCALE
    plan["source"]["base_dxf"] = u"調査/scale_test/スケール-002_ホルダー.dxf"
    return plan


def place_annotations(plan):
    u"""引出線・注記の絶対座標を、1:2の新レイアウトに合わせて再計算する。

    引出線の始点は φ11 ザグリ円の円周上(45度)= モデル座標 (0, 5.5/√2, -30-5.5/√2)。
    そこから元計画と同じ相対量(+16.611,+16.703 → +10,0)で折れ点・文字位置を作る。
    """
    scale, views, reserves = dim_engine.plan_layout(plan)
    meta = os.path.join(ROOT, plan["source"]["meta_json"])
    tf = dim_engine.build_view_transforms(meta, scale, views=views, reserves=reserves)
    r = 5.5 / math.sqrt(2.0)
    start = tf["right"]["model_to_draw"]((0.0, r, -30.0 - r))
    p2 = (start[0] + 16.611, start[1] + 16.703)
    p3 = (p2[0] + 10.0, p2[1])
    note = plan["hole_notes"][0]
    note["leader"] = {"space": "view", "points": [list(start), list(p2), list(p3)]}
    note["text_insert"] = [p3[0] + 1.0, p3[1]]
    # 自由注記は右下の空きへ(1:2でビューが小さくなるぶん外周に余裕がある)
    plan["notes"][0]["insert"] = [292.0, 78.0]
    return plan, tf


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(ROOT)
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    plan = build_plan()
    plan, tf = place_annotations(plan)
    plan_path = os.path.join(ROOT, u"調査", u"plan_SCALE-002_ホルダー_1to2.json")
    with io.open(plan_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(plan, ensure_ascii=False, indent=2))
    print(u"saved %s" % plan_path)

    views_dxf = u"調査/phase2_out_15015-P3-013_ホルダー.dxf"
    meta_json = plan["source"]["meta_json"]
    out_dxf = os.path.join(OUT_DIR, u"スケール-002_ホルダー.dxf")
    out_png = os.path.join(OUT_DIR, u"スケール-002_ホルダー.png")

    fields = {u"品名": u"ホルダー", u"図番": u"スケール-002", u"装置名": u"テスト装置",
              u"材質": "S45C", u"材質形状": u"マル80", u"個数": 1,
              u"密度_kgm3": 7850.0, u"製図者": "AI"}

    scale, use_views, reserves = dim_engine.plan_layout(plan)
    comp = compose_drawing.compose(views_dxf, meta_json, fields, scale=scale,
                                   out_path=out_dxf, views=use_views, view_reserves=reserves)
    print(u"\n[1] compose: scale=%s 尺度欄=%r frame=%s zone_overlaps=%s view_pair_overlaps=%s"
          % (scale, comp["field_values"][u"尺度"], comp["frame_check"],
             comp["zone_overlaps"], comp["view_pair_overlaps"]))
    print(u"    layout: %s" % json.dumps(comp["layout"], ensure_ascii=False))

    rep = dim_engine.apply_plan(plan_path, out_dxf, base_dxf_override=out_dxf)
    print(u"\n[2] ゲート①: gate1_ok=%s (dimlfac=%.6g)" % (rep["gate1_ok"], rep["dimlfac"]))
    print(u"%-24s %10s %10s %10s %10s  %s"
          % ("id", "expected", "measured", "diff_mm", "snap_mm", "text(描画文字)"))
    for r in rep["gate1"]:
        print(u"%-24s %10.4f %10.4f %10.6f %10.6f  %s"
              % (r["id"], r["expected"], r["measured"], r["diff_mm"],
                 r["snap_max_mm"], r["text"]))

    # ---- 独立検証: 図面文字がモデル実寸か / 作図が1/2か ----
    doc = ezdxf.readfile(out_dxf)
    msp = doc.modelspace()
    text_rows = []
    for e in msp:
        if e.dxftype() != "DIMENSION":
            continue
        raw = dim_engine.dim_text_of(doc, e)
        tv = dim_engine.parse_dim_text_value(raw)
        draw_v = dim_engine.measure_from_defpoints(e)          # 図面上の実距離
        model_v = dim_engine.measure_model_value(e, scale)      # モデル実寸
        st = doc.dimstyles.get(e.dxf.dimstyle)
        text_rows.append({"style": e.dxf.dimstyle, "text": raw, "text_value": tv,
                          "draw_mm": round(draw_v, 6), "model_mm": round(model_v, 6),
                          "dimlfac": st.dxf.get("dimlfac", None),
                          "text_is_model_size": abs(tv - model_v) <= 0.01 if tv else None,
                          "draw_is_half": abs(draw_v - model_v * scale) <= 1e-6})
    print(u"\n[3] 寸法文字=モデル実寸 / 作図=1/2 の独立確認")
    for t in text_rows:
        print(u"    %-8s dimlfac=%-6s 図面上%8.4fmm  モデル実寸%8.4fmm  文字%r -> 文字=実寸:%s 作図=1/2:%s"
              % (t["style"], t["dimlfac"], t["draw_mm"], t["model_mm"], t["text"],
                 t["text_is_model_size"], t["draw_is_half"]))
    text_ok = all(t["text_is_model_size"] for t in text_rows if t["text_value"] is not None)
    half_ok = all(t["draw_is_half"] for t in text_rows)

    # 実ジオメトリ(φ75外形円)が図面上 φ37.5 で描かれていること
    circle_ok = False
    for e in msp:
        if e.dxftype() == "CIRCLE" and abs(e.dxf.radius - 75.0 / 2.0 * scale) < 1e-6:
            circle_ok = True
            break
    print(u"    実ジオメトリ φ75外形円が図面上 r=%.4f で存在: %s" % (75.0 / 2 * scale, circle_ok))

    # ---- ゲート② ----
    g2 = gate2_completeness.check_completeness(out_dxf, plan_path)
    print(u"\n[4] ゲート②: ok=%s 未指定=%d件 冗長=%d件"
          % (g2["ok"], len(g2["unspecified"]), len(g2["redundant_dimensions"])))
    for u_ in g2["unspecified"]:
        print(u"    未指定: %s" % u_["reason"])

    # ---- 独立検証(generate_drawing の A/B/C/B2) ----
    iv = generate_drawing.independent_verify(out_dxf, plan_path)
    print(u"\n[5] 独立検証: ok=%s (図枠=%s / ゲート①最大差=%.6fmm / style=%s / note=%s)"
          % (iv["ok"], iv["frame"]["frame_matched"], iv["gate1_max_diff_mm"],
             iv["style_ok"], iv["note_ok"]))

    compose_drawing.render_png(out_dxf, out_png,
                               title=u"スケール-002 ホルダー (A3, 1:2, 寸法はモデル実寸)")
    print(u"\nsaved %s" % out_png)

    ok = all([rep["gate1_ok"], g2["ok"], iv["ok"], text_ok, half_ok, circle_ok,
              comp["field_values"][u"尺度"] == u"１：２",
              not comp["zone_overlaps"], not comp["view_pair_overlaps"],
              not rep["layout"]["collisions"]])
    res = {"scale": scale, "compose": comp, "dim": rep, "gate2_ok": g2["ok"],
           "gate2_unspecified": g2["unspecified"], "gate2_redundant": g2["redundant_dimensions"],
           "independent_verify": iv, "text_rows": text_rows,
           "text_is_model_size_ok": text_ok, "draw_is_half_ok": half_ok,
           "outer_circle_half_ok": circle_ok, "overall_ok": ok}
    out_json = os.path.join(OUT_DIR, u"scale_test_result.json")
    with io.open(out_json, "w", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    print(u"saved %s" % out_json)
    print(u"\n===== 1:2 総合: %s =====" % (u"合格" if ok else u"不合格"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
