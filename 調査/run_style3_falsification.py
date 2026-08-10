# -*- coding: utf-8 -*-
u"""様式第3弾(上品さの3層構造)の**反証テスト**。

対象は 調査/図面品質メモ_指針レビュー_2026-08-11.md の27論点のうちエンジン実装分:
  層1 値の語彙 : 呼び値翻訳(論点8・15)
  層2 置き場所 : 円形ビューの径は1本まで(論点14) / 注記は対象の近く・引出線長(論点21)
  層3 線の振る舞い: 補助線の溶け込み(論点7)

原則(CLAUDE.md):「検出できない検査は無いのと同じ」。よって各検査について
**壊したら検出できること**と**壊していないのに検出しないこと**の両方を確かめる。

ケース一覧:
  N0 基準       : 呼び値と一致する注記(φ8/ザグリφ11) -> 翻訳は恒等・未確定0・合格
  N1 呼びへ丸め  : drill=7.04 -> 注記が φ7 になる(差0.04 ≦ 窓0.05)
  N2 自明でない  : 90°皿もみ φ13.44 -> **丸めず**13.44 のまま・呼び値未確定1件・様式警告
  N3 範囲外     : drill=219.1 -> 呼び値で語る範囲外として未確定(質問票へ)
  N4 翻訳の偽装  : nominal_diameter を「7.04 -> 8.0」と嘘をつく実装へ差し替え
                  -> **ゲート①不合格**(検算は表にも関数にも依存しないため)
  N5 表の偽装   : 呼び値表を (8.0,) に差し替え -> 7.04 は窓外なので**丸めない**
                  (嘘の丸めをせずフェイルセーフに倒れる)
  C1 コリニア検出: 素のTEST-002 -> 補助線が輪郭と同一直線上の寸法を検出する
  C2 検出器の退行: find_collinear_contours を常に空を返す実装へ差し替え -> 検出0(退行が見える)
  C3 輪郭を隠す  : contour_segments を空にする -> 検出0(=検出器が本当に輪郭を見ている証明)
  C4 dimexo実効 : extension_gap_avoid=true -> **実DXFに描かれた**すき間が 1.0 -> 3.0 へ変わり
                  dimexo と 0.05mm 以内で一致する(dimexoが実図面で機能していることの実測)
  C5 補助線長警告: 素で長い補助線(>30mm)を警告する / しきい値を300mmにすると警告0
  V1 円形径2本  : 円形ビューに直径を2本 -> 様式警告1件(論点14)
  V2 円形径1本  : 素(1本)-> 警告0件
  L1 引出線長超 : 注記を遠くへ置き引出線長>50mm -> 様式警告(論点21)
  L2 引出線長内 : 素(約33.6mm)-> 警告なし
  A1 自動配置   : auto_place=true -> 注記が対象の近傍に移り、引出線が短く・衝突0
  A2 配置不能   : 図枠を極小にして置き場所を無くす -> 失敗の様式警告+計画値へフォールバック

実行: python 調査/run_style3_falsification.py [--json 調査/style3_falsification.json]
"""
import argparse
import copy
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import compose_drawing  # noqa: E402
from engine import dim_engine  # noqa: E402
from engine import nominal_size  # noqa: E402

WORK = os.path.join(ROOT, u"調査", u"style3_falsification")
BASE_PLAN = os.path.join(ROOT, u"調査", u"plan_TEST-002_ホルダー.json")
VIEWS_DXF = os.path.join(ROOT, u"調査", u"phase2_out_15015-P3-013_ホルダー.dxf")
META_JSON = os.path.join(ROOT, u"調査", u"phase2_meta_15015-P3-013_ホルダー.json")

FIELDS = {u"品名": u"ホルダー", u"図番": u"テスト-002", u"装置名": u"テスト装置",
          u"材質": "S45C", u"材質形状": u"マル80", u"個数": 1,
          u"密度_kgm3": 7850.0, u"製図者": "AI"}


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def _note(plan, nid="N_bolt_holes"):
    for n in plan["hole_notes"]:
        if n["id"] == nid:
            return n
    raise KeyError(nid)


def run_case(name, plan, note=u""):
    u"""1計画を走らせて (report or None, error) を返す。"""
    os.makedirs(WORK, exist_ok=True)
    plan = copy.deepcopy(plan)
    stem = u"case_%s" % name
    out_dxf = os.path.join(WORK, stem + u".dxf")
    plan["source"] = dict(plan["source"])
    plan["source"]["base_dxf"] = os.path.relpath(out_dxf, ROOT).replace(os.sep, "/")
    plan_path = os.path.join(WORK, stem + u".json")
    with io.open(plan_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(plan, ensure_ascii=False, indent=2))
    scale, use_views, reserves = dim_engine.plan_layout(plan)
    compose_drawing.compose(VIEWS_DXF, META_JSON, FIELDS, scale=scale, out_path=out_dxf,
                            views=use_views, view_reserves=reserves)
    try:
        rep = dim_engine.apply_plan(plan_path, out_dxf)
        return rep, None
    except dim_engine.DimensionGateError as e:
        return None, str(e)


def has_warn(rep, key):
    return any(key in w for w in (rep or {}).get("style_warnings", []))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, u"調査",
                                                   u"style3_falsification.json"))
    args = ap.parse_args(argv[1:])
    base = _read(BASE_PLAN)
    results = []

    def rec(cid, desc, ok, detail):
        results.append({"case": cid, "desc": desc, "ok": bool(ok), "detail": detail})
        print(u"  %-22s %s  %s" % (cid, u"OK" if ok else u"** NG **", detail))

    # ================= 層1: 呼び値翻訳 =================
    print(u"\n--- 層1: 呼び値翻訳(論点8・15) ---")
    rep, err = run_case("N0_base", base)
    nom = (rep["hole_notes"][0].get("nominal") if rep else None)
    rec("N0_base", u"呼び値と一致する注記",
        rep is not None and rep["gate1_ok"] and not rep["nominal"]["pending"]
        and all(r["resolved"] and r["delta_mm"] == 0.0 for r in nom),
        u"gate1=%s 未確定=%s 翻訳=%s" % (
            rep and rep["gate1_ok"], rep and len(rep["nominal"]["pending"]),
            [(r["field"], r["measured"], r["nominal"]) for r in (nom or [])]))

    p = copy.deepcopy(base)
    _note(p)["spec"]["drill"] = 7.04
    _note(p)["anchor_check"]["diameter"] = 11.0     # 実在円は変えない(注記の径だけの話)
    rep, err = run_case("N1_round", p)
    pat = rep["hole_notes"][0]["pattern"] if rep else ""
    nom = rep["hole_notes"][0]["nominal"] if rep else []
    got = [r for r in nom if r["field"] == "drill"]
    rec("N1_round", u"φ7.04 -> 呼びφ7",
        rep is not None and got and got[0]["nominal"] == 7.0
        and u"７" in pat and u"７．０４" not in pat and not rep["nominal"]["pending"],
        u"注記=%s 翻訳=%s" % (pat, [(r["measured"], r["nominal"], r["delta_mm"])
                                   for r in got]))

    p = copy.deepcopy(base)
    _note(p)["spec"]["countersink"] = {"angle": 90, "dia": 13.44}
    rep, err = run_case("N2_ambiguous", p)
    pat = rep["hole_notes"][0]["pattern"] if rep else ""
    pend = rep["nominal"]["pending"] if rep else []
    rec("N2_ambiguous", u"φ13.44 は丸めず呼び値未確定",
        rep is not None and u"１３．４４" in pat and len(pend) == 1
        and pend[0]["field"] == "countersink.dia"
        and has_warn(rep, u"呼び値未確定"),
        u"注記=%s 未確定=%s 警告=%s" % (pat, [(x["field"], x["measured"]) for x in pend],
                                     has_warn(rep, u"呼び値未確定")))

    p = copy.deepcopy(base)
    _note(p)["spec"]["counterbore"]["dia"] = 219.1
    rep, err = run_case("N3_out_of_domain", p)
    pat = rep["hole_notes"][0]["pattern"] if rep else ""
    pend = rep["nominal"]["pending"] if rep else []
    rec("N3_out_of_domain", u"φ219.1 は呼びの範囲外 -> 未確定",
        rep is not None and len(pend) == 1 and u"範囲外" in pend[0]["reason"]
        and u"２１９．１" in pat,
        u"注記=%s 理由=%s" % (pat, pend and pend[0]["reason"]))

    # N4: 翻訳関数を「嘘をつく実装」に差し替える
    p = copy.deepcopy(base)
    _note(p)["spec"]["drill"] = 7.04
    orig_fn = nominal_size.nominal_diameter

    def _liar(measured, tol=nominal_size.NOMINAL_TOL_MM, table=None,
              domain_max=nominal_size.NOMINAL_DOMAIN_MAX_MM):
        r = orig_fn(measured, tol=tol, table=table, domain_max=domain_max)
        if abs(float(measured) - 7.04) < 1e-9:
            r = dict(r, nominal=8.0, resolved=True, delta_mm=0.0,
                     reason=u"(偽装)人間図面に合わせてφ8にした")
        return r
    nominal_size.nominal_diameter = _liar
    try:
        rep, err = run_case("N4_forged_translation", p)
    finally:
        nominal_size.nominal_diameter = orig_fn
    rec("N4_forged_translation", u"嘘の丸め(7.04->8.0)はゲート①で落ちる",
        rep is None and err is not None and u"呼び値翻訳が許容窓を超えている" in err,
        u"gate=%s msg=%s" % (u"不合格" if rep is None else u"合格",
                             (err or u"")[:110].replace("\n", " ")))

    # N5: 呼び値表そのものを偽装する
    p = copy.deepcopy(base)
    _note(p)["spec"]["drill"] = 7.04
    orig_tbl = nominal_size.NOMINAL_TABLE
    nominal_size.NOMINAL_TABLE = (8.0,)
    try:
        rep, err = run_case("N5_forged_table", p)
    finally:
        nominal_size.NOMINAL_TABLE = orig_tbl
    pat = rep["hole_notes"][0]["pattern"] if rep else ""
    pend = rep["nominal"]["pending"] if rep else []
    # 偽装表 (8.0,) では 7.04 も 11.0 も窓外になる = **どちらも丸めない**のが正しい挙動
    rec("N5_forged_table", u"表を偽装しても嘘の丸めはせず未確定に倒れる",
        rep is not None and u"７．０４" in pat
        and [x["measured"] for x in pend] == [7.04, 11.0],
        u"注記=%s 未確定=%s" % (pat, [(x["field"], x["measured"]) for x in pend]))

    # ================= 層3: 補助線の溶け込み =================
    print(u"\n--- 層3: 補助線の溶け込み(論点7) ---")
    rep0, _ = run_case("C1_collinear_base", base)
    n_col0 = rep0["extension_lines"]["collinear_count"]
    rec("C1_collinear_base", u"素のTEST-002で溶け込みを検出する", n_col0 > 0,
        u"検出=%d本 / 線形寸法%d本" % (n_col0, len(rep0["extension_lines"]["reports"])))

    orig_find = dim_engine.find_collinear_contours
    dim_engine.find_collinear_contours = lambda *a, **k: []
    try:
        rep, _ = run_case("C2_detector_regression", base)
    finally:
        dim_engine.find_collinear_contours = orig_find
    rec("C2_detector_regression", u"検出器を無効化した退行は数値に出る",
        rep["extension_lines"]["collinear_count"] == 0 and n_col0 > 0,
        u"退行実装=%d本(素は%d本)" % (rep["extension_lines"]["collinear_count"], n_col0))

    orig_seg = dim_engine.contour_segments
    dim_engine.contour_segments = lambda ents: []
    try:
        rep, _ = run_case("C3_no_contour", base)
    finally:
        dim_engine.contour_segments = orig_seg
    rec("C3_no_contour", u"輪郭を隠すと検出0(輪郭を本当に見ている)",
        rep["extension_lines"]["collinear_count"] == 0,
        u"輪郭なし=%d本(素は%d本)" % (rep["extension_lines"]["collinear_count"], n_col0))

    gaps0 = sorted({g for e in rep0["extension_lines"]["reports"]
                    for g in (e.get("drawn_gap_mm") or []) if g is not None})
    p = copy.deepcopy(base)
    p.setdefault("defaults", {})["extension_gap_avoid"] = True
    rep, _ = run_case("C4_dimexo_effective", p)
    gaps1 = sorted({g for e in rep["extension_lines"]["reports"]
                    for g in (e.get("drawn_gap_mm") or []) if g is not None})
    ok4 = (gaps0 == [1.0] and dim_engine.EXT_GAP_AVOID_MM in gaps1
           and rep["extension_lines"]["gap_mismatch_count"] == 0
           and rep0["extension_lines"]["gap_mismatch_count"] == 0)
    rec("C4_dimexo_effective", u"実DXFに描かれたすき間はdimexoと一致する",
        ok4, u"素のすき間=%s / 回避ON=%s(不一致 %d件)"
        % (gaps0, gaps1, rep["extension_lines"]["gap_mismatch_count"]))

    long0 = rep0["extension_lines"]["long_count"]
    orig_len = dim_engine.EXT_LEN_WARN_MM
    dim_engine.EXT_LEN_WARN_MM = 300.0
    try:
        rep, _ = run_case("C5_len_threshold", base)
    finally:
        dim_engine.EXT_LEN_WARN_MM = orig_len
    rec("C5_len_threshold", u"補助線長のしきい値が効いている",
        long0 > 0 and rep["extension_lines"]["long_count"] == 0
        and has_warn(rep0, u"補助線が長") and not has_warn(rep, u"補助線が長"),
        u"しきい値%.0fmm=%d本 / 300mm=%d本(最大%.1fmm)"
        % (orig_len, long0, rep["extension_lines"]["long_count"],
           rep0["extension_lines"]["ext_len_max_mm"]))

    # ================= 層2: 円形ビューの径は1本まで =================
    print(u"\n--- 層2: 円形ビューの径は1本まで(論点14) ---")
    rec("V2_circular_one", u"円形ビューの径1本なら警告0",
        not rep0["circular_view_diameter_over"]
        and not has_warn(rep0, u"円形ビューの径寸法"),
        u"超過=%s" % rep0["circular_view_diameter_over"])

    p = copy.deepcopy(base)
    extra = None
    for d in p["dimensions"]:
        if d["id"] == "D75_outer":
            extra = copy.deepcopy(d)
    extra["id"] = "D33_on_circular"
    extra["value_expected"] = 33.0
    extra["measure"] = dict(extra["measure"])
    extra["measure"]["diameter"] = 33.0
    extra["measure"]["leader_angle"] = 135.0
    p["dimensions"].append(extra)
    rep, err = run_case("V1_circular_two", p)
    over = rep["circular_view_diameter_over"] if rep else []
    rec("V1_circular_two", u"円形ビューに径2本 -> 様式警告",
        rep is not None and len(over) == 1 and over[0]["count"] == 2
        and has_warn(rep, u"円形ビューの径寸法は1本まで"),
        u"超過=%s 警告=%s" % (over, rep and has_warn(rep, u"円形ビューの径寸法は1本まで")))

    # ================= 層2: 注記は対象の近くに =================
    print(u"\n--- 層2: 注記は対象の近く・引出線長(論点21) ---")
    len0 = rep0["hole_notes"][0]["leader_len_mm"]
    rec("L2_leader_ok", u"引出線が目安内なら警告なし",
        len0 <= dim_engine.LEADER_LEN_MAX_MM and not has_warn(rep0, u"引出線が長すぎる"),
        u"引出線長=%.1fmm(上限%.0f)" % (len0, dim_engine.LEADER_LEN_MAX_MM))

    p = copy.deepcopy(base)
    n = _note(p)
    n["leader"] = dict(n["leader"])
    n["leader"]["points"] = [n["leader"]["points"][0], [330.0, 200.0], [345.0, 200.0]]
    n["text_insert"] = [346.0, 200.0]
    rep, err = run_case("L1_leader_long", p)
    rec("L1_leader_long", u"引出線が長すぎると様式警告",
        rep is not None and rep["hole_notes"][0]["leader_len_mm"]
        > dim_engine.LEADER_LEN_MAX_MM and has_warn(rep, u"引出線が長すぎる"),
        u"引出線長=%.1fmm 警告=%s" % (rep["hole_notes"][0]["leader_len_mm"],
                                    has_warn(rep, u"引出線が長すぎる")))

    p = copy.deepcopy(base)
    _note(p)["auto_place"] = True
    rep, err = run_case("A1_auto_place", p)
    nr = rep["hole_notes"][0] if rep else {}
    tb = rep["layout"]["text_boxes"].get("N_bolt_holes") if rep else None
    hit = [k for k, vb in (rep["view_bbox"].items() if rep else [])
           if tb and dim_engine._rect_overlap(tb, vb)]
    rec("A1_auto_place", u"自動配置で対象の近傍に置かれ衝突0",
        rep is not None and nr.get("auto_placed")
        and nr["leader_len_mm"] < len0 and not hit
        and not [c for c in rep["layout"]["collisions"] if "N_bolt_holes" in c],
        u"自動=%s 引出線長 %.1f -> %.1fmm ビュー衝突=%s 文字枠衝突=%s"
        % (nr.get("auto_placed"), len0, nr.get("leader_len_mm", -1), hit,
           [c for c in rep["layout"]["collisions"] if "N_bolt_holes" in c]))

    p = copy.deepcopy(base)
    _note(p)["auto_place"] = True
    orig_rect = dim_engine.FRAME_RECT
    dim_engine.FRAME_RECT = (0.0, 0.0, 1.0, 1.0)     # 置き場所を物理的に無くす
    try:
        rep, err = run_case("A2_auto_place_fail", p)
    finally:
        dim_engine.FRAME_RECT = orig_rect
    nr = rep["hole_notes"][0] if rep else {}
    rec("A2_auto_place_fail", u"置き場所が無ければ警告して計画値へ戻る",
        rep is not None and not nr.get("auto_placed")
        and has_warn(rep, u"自動配置に失敗")
        and nr["text_insert"] == [299.0, 120.0],
        u"自動=%s 警告=%s 位置=%s" % (nr.get("auto_placed"),
                                     has_warn(rep, u"自動配置に失敗"),
                                     nr.get("text_insert")))

    n_ok = sum(1 for r in results if r["ok"])
    print(u"\n===== 様式第3弾 反証テスト %d/%d 合格 =====" % (n_ok, len(results)))
    with io.open(args.json, "w", encoding="utf-8") as f:
        f.write(json.dumps({"summary": {"total": len(results), "ok": n_ok},
                            "results": results}, ensure_ascii=False, indent=1))
    print(u"saved %s" % args.json)
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
