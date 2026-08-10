# -*- coding: utf-8 -*-
u"""ビュー間隔と寸法線の干渉(VIEW_GAP_MM=15 < first_offset_mm=16)の修正検証。

反証つき比較(同じ計画・同じ寸法で、レイアウト方式だけを変える):
  ケースA `layout.dim_reserve=false` = 従来の固定間隔15mm
      -> front の右側1段目・上側3段目の寸法が隣のビュー領域へ食い込む(干渉>0)
  ケースB `layout.dim_reserve=true`(新既定) = 計画の寸法段数から予約帯を計算して間隔を決める
      -> 干渉ゼロ

干渉の判定は**生成DXFの実体**で行う(自己申告を信用しない):
  各DIMENSIONを virtual_entities() で展開した外接矩形 vs **自分以外のビューの実ジオメトリ外接矩形**
  の bbox 交差。1件でも交差したら干渉ありとする。

計画は 調査/plan_TEST-002_ホルダー.json 由来(front右側の寸法を人手で詰めた offset_mm=11 を外し、
規定どおり level=1=16mm に戻す/全長を上側3段目へ移す)。穴注記・自由注記は絶対座標指定で
レイアウト変更に追随しないため、この試験では外してある(寸法だけの試験)。

実行: python 調査/run_layout_interference_test.py
出力: 調査/layout_test/
"""
import io
import json
import os
import sys

import ezdxf
from ezdxf.bbox import extents as bbox_extents

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import compose_drawing  # noqa: E402
from engine import dim_engine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, u"調査", u"layout_test")


def build_plan(dim_reserve):
    with io.open(os.path.join(ROOT, u"調査", u"plan_TEST-002_ホルダー.json"),
                 encoding="utf-8") as f:
        plan = json.load(f)
    tag = "B_reserve" if dim_reserve else "A_fixed15"
    plan["_note"] = (u"調査/run_layout_interference_test.py が自動生成(ケース%s)。"
                     u"front右側の寸法を規定の1段目(16mm)に戻し、全長を上側3段目へ移した"
                     u"干渉試験用の計画。穴注記・自由注記は絶対座標のため除外。" % tag)
    plan["part"][u"図番"] = u"レイアウト-%s" % tag
    plan["source"]["base_dxf"] = u"調査/layout_test/レイアウト-%s.dxf" % tag
    plan["layout"] = {"dim_reserve": bool(dim_reserve)}
    plan.pop("hole_notes", None)
    plan.pop("notes", None)
    for d in plan["dimensions"]:
        if d["id"] == "D26_bore":       # 人手で11mmへ詰めていたものを規定の1段目へ戻す
            d["placement"] = {"side": "right", "level": 1}
        if d["id"] == "L40_total":      # 上側3段目(CLAUDE.md『front上側3段目が平面図に迫る』)
            d["placement"] = {"side": "above", "level": 3}
    return plan, tag


def dim_boxes(doc):
    u"""各DIMENSIONの描画実体(アノニマスブロック展開)の外接矩形を返す。"""
    out = []
    for e in doc.modelspace():
        if e.dxftype() != "DIMENSION":
            continue
        ents = list(e.virtual_entities())
        bb = bbox_extents(ents, fast=False) if ents else None
        if bb is None or not bb.has_data:
            continue
        out.append((str(e.dxf.dimstyle),
                    (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y)))
    return out


def _overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def run_case(dim_reserve):
    plan, tag = build_plan(dim_reserve)
    plan_path = os.path.join(OUT_DIR, u"plan_レイアウト-%s.json" % tag)
    with io.open(plan_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(plan, ensure_ascii=False, indent=2))

    out_dxf = os.path.join(OUT_DIR, u"レイアウト-%s.dxf" % tag)
    fields = {u"品名": u"ホルダー", u"図番": u"レイアウト-%s" % tag, u"装置名": u"テスト装置",
              u"材質": "S45C", u"材質形状": u"マル80", u"個数": 1,
              u"密度_kgm3": 7850.0, u"製図者": "AI"}
    scale, use_views, reserves = dim_engine.plan_layout(plan)
    comp = compose_drawing.compose(u"調査/phase2_out_15015-P3-013_ホルダー.dxf",
                                   plan["source"]["meta_json"], fields, scale=scale,
                                   out_path=out_dxf, views=use_views, view_reserves=reserves)
    rep = dim_engine.apply_plan(plan_path, out_dxf, base_dxf_override=out_dxf)

    doc = ezdxf.readfile(out_dxf)
    view_bbox = {k: tuple(v) for k, v in rep["view_bbox"].items()}
    plan_ids = [d["id"] for d in plan["dimensions"]]
    hits = []
    for style, box in dim_boxes(doc):
        idx = int("".join(ch for ch in style if ch.isdigit()) or 0) - 1
        did = plan_ids[idx] if 0 <= idx < len(plan_ids) else style
        own = next((d["view"] for d in plan["dimensions"] if d["id"] == did), None)
        for k, vb in view_bbox.items():
            if k == own:
                continue
            if _overlap(box, vb):
                hits.append({"dim": did, "dim_view": own, "intruded_view": k,
                             "dim_bbox": [round(v, 4) for v in box],
                             "view_bbox": [round(v, 4) for v in vb]})
    png = os.path.join(OUT_DIR, u"レイアウト-%s.png" % tag)
    compose_drawing.render_png(out_dxf, png,
                               title=u"%s (dim_reserve=%s) 干渉%d件"
                                     % (tag, dim_reserve, len(hits)))
    return {"case": tag, "dim_reserve": dim_reserve, "gate1_ok": rep["gate1_ok"],
            "layout": comp["layout"], "view_bbox": rep["view_bbox"],
            "interference_count": len(hits), "interference": hits,
            "collisions": rep["layout"]["collisions"], "dxf": out_dxf, "png": png}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(ROOT)
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)

    res = {}
    for dim_reserve in (False, True):
        r = run_case(dim_reserve)
        res[r["case"]] = r
        print(u"\n===== ケース%s (dim_reserve=%s) =====" % (r["case"], dim_reserve))
        print(u"  ビュー間隔: gap_x=%.4fmm gap_y=%.4fmm / 予約帯=%s"
              % (r["layout"]["gap_x_mm"], r["layout"]["gap_y_mm"],
                 json.dumps(r["layout"]["reserves"], ensure_ascii=False)))
        print(u"  ゲート①=%s / 寸法bboxが他ビュー領域へ食い込んだ件数: %d"
              % (r["gate1_ok"], r["interference_count"]))
        for h in r["interference"]:
            print(u"    %s(%sビューの寸法) -> %sビュー領域へ侵入 dim=%s view=%s"
                  % (h["dim"], h["dim_view"], h["intruded_view"],
                     h["dim_bbox"], h["view_bbox"]))
        print(u"  saved %s" % r["png"])

    ok = (res["A_fixed15"]["interference_count"] > 0
          and res["B_reserve"]["interference_count"] == 0
          and res["A_fixed15"]["gate1_ok"] and res["B_reserve"]["gate1_ok"])
    out_json = os.path.join(OUT_DIR, u"layout_interference_result.json")
    with io.open(out_json, "w", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    print(u"\nsaved %s" % out_json)
    print(u"\n===== 反証つき判定: %s(A=従来固定15mmで干渉%d件 / B=予約帯で干渉%d件) ====="
          % (u"合格" if ok else u"不合格",
             res["A_fixed15"]["interference_count"], res["B_reserve"]["interference_count"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
