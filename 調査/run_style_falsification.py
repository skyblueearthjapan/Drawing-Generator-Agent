# -*- coding: utf-8 -*-
u"""様式実装(直列連記 chain_group / 様式寸法 purpose='style')の**反証テスト**。

「直列連記の整列が壊れたら検出できるか」を、壊した計画・壊した実装で実際に確かめる。
検出できない検査は無いのと同じ、という本プロジェクトの原則
(CLAUDE.md「反証テストは1本消しだけでは足りない」)に従う。

ケース:
  S0  基準       : TEST-002 の隣接2寸法(L35/L5)に chain_group を付ける -> 合格・整列OK
  S1  段の正規化 : 同一グループに level 1 と level 3 を混在 -> **両方とも同じ寸法線**へ揃う
                   (=chain_group が offset を正規化している証拠。予約帯も同じ値を見る)
  S2  実装退行   : resolve_dim_offsets を「chain_group を無視する旧実装」へ差し替える
                   -> 同一グループの寸法線がずれる -> **検出されること**
  S3  side混在   : 同一グループで side を below/above に割る -> 検出
  S4  区間の重なり: 同一グループに全長(-40..0)と部分(-40..-5)を入れる -> 検出
  S5  飛び地     : 端点を共有しない2区間を同一グループにする -> 検出
  S6  方向混在   : 水平寸法と垂直寸法を同一グループにする -> 検出
  S7  単独       : メンバー1本だけのグループ -> 検出
  S8  様式寸法   : purpose='style' は gate2 の「宙に浮いた寸法」警告から外れるが、
                   **未指定(完全性)の判定は1件も変わらない**こと
                   (再生成済みの BLIND2-25154-3-02 を使う。無ければ skip)

実行: python 調査/run_style_falsification.py [--json 調査/style_falsification.json]
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
from engine import gate2_completeness  # noqa: E402

WORK = os.path.join(ROOT, u"調査", u"style_falsification")
BASE_PLAN = os.path.join(ROOT, u"調査", u"plan_TEST-002_ホルダー.json")
VIEWS_DXF = os.path.join(ROOT, u"調査", u"phase2_out_15015-P3-013_ホルダー.dxf")
META_JSON = os.path.join(ROOT, u"調査", u"phase2_meta_15015-P3-013_ホルダー.json")

FIELDS = {u"品名": u"ホルダー", u"図番": u"テスト-002", u"装置名": u"テスト装置",
          u"材質": "S45C", u"材質形状": u"マル80", u"個数": 1,
          u"密度_kgm3": 7850.0, u"製図者": "AI"}


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def _by_id(plan, did):
    for d in plan["dimensions"]:
        if d["id"] == did:
            return d
    raise KeyError(did)


def run_case(name, plan, note=u""):
    u"""計画を1本走らせ、(合格したか, 直列連記レポート, エラー文字列) を返す。"""
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
    rec = {"case": name, "note": note, "reserves": reserves,
           "offsets": compose_drawing.resolve_dim_offsets(plan)}
    try:
        rep = dim_engine.apply_plan(plan_path, out_dxf)
        rec.update({"passed": True, "chains": rep["chains"], "chains_ok": rep["chains_ok"],
                    "gate1_ok": rep["gate1_ok"], "error": None,
                    "view_bbox": rep["view_bbox"], "dxf": out_dxf,
                    "styles": rep["dimstyles"]})
    except dim_engine.DimensionGateError as e:
        rec.update({"passed": False, "chains": None, "chains_ok": False,
                    "gate1_ok": False, "error": str(e)[:1500]})
    return rec


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, u"調査", u"style_falsification.json"))
    args = ap.parse_args(argv[1:])

    base = _read(BASE_PLAN)
    results = []

    # ---- S0: 基準(隣接する L35 と L5 を同一寸法線へ連記) ----
    p0 = copy.deepcopy(base)
    _by_id(p0, "L35_counterbore_depth")["placement"] = {"side": "below", "chain_group": "X"}
    _by_id(p0, "L5_bore_land")["placement"] = {"side": "below", "chain_group": "X"}
    results.append(run_case("S0_baseline", p0, u"隣接2寸法を chain_group='X' で連記"))

    # ---- S1: 段(level)が混在していてもグループで正規化される ----
    p1 = copy.deepcopy(base)
    _by_id(p1, "L35_counterbore_depth")["placement"] = {
        "side": "below", "level": 1, "chain_group": "X"}
    _by_id(p1, "L5_bore_land")["placement"] = {"side": "below", "level": 3, "chain_group": "X"}
    r1 = run_case("S1_level_normalized", p1, u"level 1 と 3 を混在させても同一寸法線へ揃うか")
    results.append(r1)

    # ---- S2: 実装退行(chain_group を無視する旧オフセット実装)を注入 ----
    orig = compose_drawing.resolve_dim_offsets

    def legacy_offsets(plan):
        u"""chain_group を見ない旧実装(段ごとに別オフセットへ積む)。"""
        d = (plan.get("defaults") or {})
        fo = float(d.get("first_offset_mm", 16.0))
        ss = float(d.get("stack_step_mm", 8.0))
        out = {}
        for it in plan.get("dimensions", []):
            pl = it.get("placement") or {}
            off = pl.get("offset_mm")
            out[it["id"]] = (fo + (int(pl.get("level", 1)) - 1) * ss
                             if off is None else float(off))
        return out

    compose_drawing.resolve_dim_offsets = legacy_offsets
    try:
        results.append(run_case("S2_engine_regression", p1,
                                u"chain_group を無視する旧実装を注入(同一グループがずれる)"))
    finally:
        compose_drawing.resolve_dim_offsets = orig

    # ---- S3: side 混在 ----
    p3 = copy.deepcopy(p0)
    _by_id(p3, "L5_bore_land")["placement"] = {"side": "above", "chain_group": "X"}
    results.append(run_case("S3_side_mixed", p3, u"同一グループで side を below/above に割る"))

    # ---- S4: 区間の重なり(全長と部分を同じ線へ) ----
    p4 = copy.deepcopy(p0)
    _by_id(p4, "L40_total")["placement"] = {"side": "below", "chain_group": "X"}
    results.append(run_case("S4_overlap", p4, u"全長(-40..0)と部分(-40..-5)を同一寸法線へ"))

    # ---- S5: 飛び地(端点を共有しない2区間) ----
    p5 = copy.deepcopy(base)
    p5["dimensions"] = [d for d in p5["dimensions"]
                        if d["id"] not in ("D33_counterbore", "D26_bore")]
    p5["dimensions"].append({
        "id": "V_lower", "kind": "linear", "view": "front",
        "measure": {"space": "model", "p1": [-40.0, -37.5, 0.0], "p2": [-40.0, -16.5, 0.0],
                    "direction": "vertical"},
        "placement": {"side": "left", "chain_group": "Y"},
        "value_expected": 21.0, "tolerance": None})
    p5["dimensions"].append({
        "id": "V_upper", "kind": "linear", "view": "front",
        "measure": {"space": "model", "p1": [-40.0, 16.5, 0.0], "p2": [-40.0, 37.5, 0.0],
                    "direction": "vertical"},
        "placement": {"side": "left", "chain_group": "Y"},
        "value_expected": 21.0, "tolerance": None})
    results.append(run_case("S5_gap", p5, u"端点を共有しない2区間(間に33mmの隙間)を同一グループへ"))

    # ---- S6: 方向混在(水平寸法と垂直寸法を同じ寸法線に載せようとする) ----
    p6 = copy.deepcopy(base)
    _by_id(p6, "L35_counterbore_depth")["placement"] = {"side": "below", "chain_group": "X"}
    _by_id(p6, "D33_counterbore")["placement"] = {"side": "left", "chain_group": "X"}
    r6 = run_case("S6_direction_mixed", p6, u"水平寸法と垂直寸法を同一グループへ")
    r6["direction_error_reported"] = (u"測定方向" in (r6.get("error") or ""))
    results.append(r6)

    # ---- S7: メンバー1本 ----
    p7 = copy.deepcopy(base)
    _by_id(p7, "L35_counterbore_depth")["placement"] = {"side": "below", "chain_group": "Z"}
    results.append(run_case("S7_single_member", p7, u"メンバー1本だけのグループ"))

    # ---- S9: placement.side の4方向が計画どおり効くか(左右・上下の振り分け) ----
    # ❗様式規則(作法6・7)は「径を左右へ振り分ける」ことが前提なので、side が実際に
    #   ビュー輪郭の指定辺 ± オフセットへ寸法線を出しているかを**実DXFから検算**する。
    p9 = copy.deepcopy(base)
    # ❗TEST-002 の穴注記引出線は `space:"view"`(図面絶対座標)なので、side を変えて
    #   レイアウトが動くと anchor_check が落ちる(CLAUDE.md既知の罠)。side の検算とは
    #   無関係なので注記を外す
    p9["hole_notes"] = []
    _by_id(p9, "D33_counterbore")["placement"] = {"side": "right", "level": 1}
    _by_id(p9, "D26_bore")["placement"] = {"side": "left", "level": 1}
    _by_id(p9, "L40_total")["placement"] = {"side": "above", "level": 1}
    _by_id(p9, "L35_counterbore_depth")["placement"] = {"side": "below", "level": 1}
    _by_id(p9, "L5_bore_land")["placement"] = {"side": "below", "level": 2}
    r9 = run_case("S9_side_dispatch", p9, u"4方向(above/below/left/right)の配置検算")
    if r9["passed"]:
        import ezdxf
        doc = ezdxf.readfile(r9["dxf"])
        want = {"D33_counterbore": ("front", "right", 16.0),
                "D26_bore": ("front", "left", 16.0),
                "L40_total": ("front", "above", 16.0),
                "L35_counterbore_depth": ("front", "below", 16.0),
                "L5_bore_land": ("front", "below", 24.0)}
        style_of = {v: k for k, v in r9["styles"].items()}
        checks = []
        for e in doc.modelspace():
            if e.dxftype() != "DIMENSION":
                continue
            did = style_of.get(str(e.dxf.dimstyle))
            if did not in want:
                continue
            view, side, off = want[did]
            x0, y0, x1, y1 = r9["view_bbox"][view]
            exp = {"left": x0 - off, "right": x1 + off,
                   "below": y0 - off, "above": y1 + off}[side]
            got = e.dxf.defpoint.x if side in ("left", "right") else e.dxf.defpoint.y
            checks.append({"id": did, "side": side, "offset_mm": off,
                           "expected_coord": round(exp, 4), "actual_coord": round(got, 4),
                           # view_bbox はレポートで小数4桁に丸められているため 1e-3 で見る
                           "ok": abs(got - exp) <= 1e-3})
        r9["side_checks"] = sorted(checks, key=lambda c: c["id"])
        r9["side_dispatch_ok"] = len(checks) == len(want) and all(c["ok"] for c in checks)
    else:
        r9["side_dispatch_ok"] = False
    results.append(r9)

    # ---- 判定 ----
    expect = {"S0_baseline": True, "S1_level_normalized": True, "S9_side_dispatch": True,
              "S2_engine_regression": False, "S3_side_mixed": False, "S4_overlap": False,
              "S5_gap": False, "S6_direction_mixed": False, "S7_single_member": False}
    for r in results:
        r["expected_pass"] = expect[r["case"]]
        r["verdict"] = "OK" if r["passed"] == r["expected_pass"] else "NG"

    # S1 の追加判定: 2本の寸法線座標が一致していること(整列の実測値)
    s1 = next(r for r in results if r["case"] == "S1_level_normalized")
    seg = (s1["chains"] or [{}])[0].get("segments") or []
    s1["line_coords"] = [s["line_coord"] for s in seg]
    s1["aligned_exactly"] = len({round(c, 6) for c in s1["line_coords"]}) == 1
    if not s1["aligned_exactly"]:
        s1["verdict"] = "NG"

    # S6 の追加判定: 「測定方向が違う」というメッセージ自体が出ていること
    s6 = next(r for r in results if r["case"] == "S6_direction_mixed")
    if not s6.get("direction_error_reported"):
        s6["verdict"] = "NG"

    # S9 の追加判定: 4方向すべての寸法線位置が輪郭±オフセットに一致すること
    s9 = next(r for r in results if r["case"] == "S9_side_dispatch")
    if not s9.get("side_dispatch_ok"):
        s9["verdict"] = "NG"

    # ---- S8: 様式寸法(purpose='style')が完全性判定を甘くしないこと ----
    s8 = {"case": "S8_style_dimension", "note": u"purpose='style' は警告からのみ外れる"}
    gen_dir = os.path.join(ROOT, u"data", u"依頼箱", u"BLIND2-25154-3-02", u"生成")
    dxf = os.path.join(gen_dir, u"25154-3-02_モータブラケット.dxf")
    plan_p = os.path.join(ROOT, u"data", u"依頼箱", u"BLIND2-25154-3-02", u"plan.json")
    if os.path.exists(dxf) and os.path.exists(plan_p):
        plan = _read(plan_p)
        style_ids = [d["id"] for d in plan["dimensions"] if d.get("purpose") == "style"]
        s8["style_ids"] = style_ids
        if style_ids:
            with_style = gate2_completeness.check_completeness(dxf, plan_p)
            plan_no = copy.deepcopy(plan)
            for d in plan_no["dimensions"]:
                d.pop("purpose", None)
            no_p = os.path.join(WORK, u"plan_3-02_nostyle.json")
            os.makedirs(WORK, exist_ok=True)
            with io.open(no_p, "w", encoding="utf-8") as f:
                f.write(json.dumps(plan_no, ensure_ascii=False, indent=2))
            without = gate2_completeness.check_completeness(dxf, no_p)
            s8["unspecified_with_style"] = len(with_style["unspecified"])
            s8["unspecified_without_style"] = len(without["unspecified"])
            s8["floating_with_style"] = [f["id"] for f in with_style["floating_dimensions"]]
            s8["floating_without_style"] = [f["id"] for f in without["floating_dimensions"]]
            same_gate = (s8["unspecified_with_style"] == s8["unspecified_without_style"]
                         and with_style["ok"] == without["ok"])
            silenced = (set(s8["floating_without_style"]) - set(s8["floating_with_style"])
                        == set(style_ids))
            s8["verdict"] = "OK" if (same_gate and silenced) else "NG"
        else:
            s8["verdict"] = "SKIP"
            s8["reason"] = u"3-02の計画に purpose='style' の寸法が無い"
    else:
        s8["verdict"] = "SKIP"
        s8["reason"] = u"再生成済みの 3-02 DXF が無い(再生成後に再実行すること)"
    results.append(s8)

    with io.open(args.json, "w", encoding="utf-8") as f:
        f.write(json.dumps(results, ensure_ascii=False, indent=2, default=str))

    print(u"=== 様式実装の反証テスト ===")
    for r in results:
        line = u"  %-22s 期待=%s 実際=%s -> %s" % (
            r["case"], r.get("expected_pass"), r.get("passed"), r["verdict"])
        print(line)
        if r.get("chains"):
            for c in r["chains"]:
                if c["errors"]:
                    print(u"        検出: %s" % c["errors"][0])
        elif r.get("error"):
            first = [l for l in r["error"].splitlines() if u"直列連記" in l or u"寸法線" in l
                     or u"測定区間" in l or u"端点" in l or u"測定方向" in l]
            if first:
                print(u"        検出: %s" % first[0].strip().strip('",'))
    ng = [r for r in results if r["verdict"] == "NG"]
    print(u"\n合格 %d / %d(SKIP %d)"
          % (sum(1 for r in results if r["verdict"] == "OK"), len(results),
             sum(1 for r in results if r["verdict"] == "SKIP")))
    print(u"saved %s" % args.json)
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
