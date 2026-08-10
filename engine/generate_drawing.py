# -*- coding: utf-8 -*-
u"""1コマンド部品図生成ランナー(フェーズ5骨格・恒久モジュール)。

    3Dモデル + 作図計画JSON + 依頼情報JSON  --(このCLI)-->  検証済み部品図DXF

処理チェーン(既存の実証済みモジュールをそのまま束ねるだけ。中身のロジックは持たない):
  1. SW投影      : engine/draw_pipeline.py (--skip-sw で 調査/phase2_out_*.dxf 等を再利用)
  2. 重量算出    : 依頼JSONの材質 -> 密度表 -> 体積(SW計測)×密度
  3. compose     : engine/compose_drawing.py (図枠+ビュー+表題欄)
  4. dim_engine  : engine/dim_engine.py (寸法・注記。ゲート①内蔵)
  5. ゲート②     : engine/gate2_completeness.py (寸法完全性)
  6. 独立検証    : 調査/verify_generated.py のA(ゲート①独立再計算)/B(DIMSTYLE)/
                    C(図枠保持)/B2(注記書式)を汎用化して再実行(自己申告を信用しない)
  7. PNG化 + 台帳追記(不合格は 生成図面/不合格/ へ隔離)

安全規約(CLAUDE.md): SW COM操作は engine/draw_pipeline.py の関数をそのまま呼ぶだけで、
OpenDoc6=None時のActiveDocフォールバック禁止・タイトル照合・自分が開いたものだけCloseDoc・
上書き保存禁止は draw_pipeline.py 側の実装にすでに担保されている(ここでは変更しない)。

CLI:
    python engine/generate_drawing.py --model <SLDPRT/STEPパス> --plan <計画JSON>
        --request <依頼JSON> --out-dir 生成図面 [--zuban テスト-004] [--skip-sw]
        [--views-dxf <path>] [--meta-json <path>]
"""
import argparse
import datetime
import io
import json
import os
import re
import shutil
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))

import ezdxf  # noqa: E402

from engine import compose_drawing  # noqa: E402
from engine import dim_engine  # noqa: E402
from engine import gate2_completeness  # noqa: E402
from engine.frame_extract import subtract_frame  # noqa: E402
import draw_pipeline as dp  # noqa: E402

FRAME_TEMPLATE = os.path.join(ROOT, u"図枠", u"frame_template.dxf")

# ---------------------------------------------------------------------------
# 材質 -> 密度表(kg/m^3・近似値)。SWは材質未設定モデルで密度1000(水)を返すため、
# 依頼JSONの材質からここで重量算出用の密度を解決する(CLAUDE.md知見)。
# ---------------------------------------------------------------------------
DENSITY_TABLE_KGM3 = {
    "S45C": 7850.0, "S50C": 7850.0, "S35C": 7850.0, "S25C": 7850.0,
    "SS400": 7850.0, "SS41": 7850.0,
    "SCM440": 7850.0, "SUJ2": 7850.0,
    "SUS304": 7930.0, "SUS303": 7930.0, "SUS316": 8000.0, "SUS316L": 8000.0,
    "A5052": 2680.0, "A2017": 2790.0, "A6061": 2700.0, "A1050": 2700.0,
    "BC6": 8800.0, "BC3": 8700.0, "BC2": 8900.0,
    "FC200": 7300.0, "FC250": 7300.0,
    "C3604": 8500.0,  # 快削黄銅棒
}

_ZEN2HAN_ALL = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
_ZEN2HAN_ALL[0x3000] = 0x20


def to_hankaku(s):
    u"""全角英数記号を半角へ(compose_drawing.to_zenkakuの逆)。材質記号の正規化に使う。"""
    return (s or "").translate(_ZEN2HAN_ALL)


def normalize_material_code(s):
    u"""密度表の照合キーへ正規化: 全角->半角、空白除去、大文字化。先頭トークンのみ使う
    (例 'Ｓ４５Ｃ　マル８０' -> 'S45C')。"""
    h = to_hankaku(s).strip()
    token = re.split(r"[\s　]+", h)[0] if h else ""
    return token.upper()


def resolve_density(material_text, explicit_density=None):
    u"""依頼JSONの材質テキストから密度(kg/m^3)を解決する。
    Returns (density_or_None, note)"""
    if explicit_density:
        return float(explicit_density), u"依頼JSON明示指定 密度_kgm3=%.1f" % float(explicit_density)
    if not material_text:
        return None, u"依頼JSONに材質が無い"
    code = normalize_material_code(material_text)
    d = DENSITY_TABLE_KGM3.get(code)
    if d is None:
        return None, u"材質記号 '%s'(正規化後 '%s') が密度表に無い" % (material_text, code)
    return d, u"材質記号 '%s' -> 密度%.1fkg/m^3" % (code, d)


# ---------------------------------------------------------------------------
# 1) SW投影(draw_pipeline流用。安全規約は draw_pipeline.py 側で担保済み)
# ---------------------------------------------------------------------------
def run_sw_projection(model_path, out_views_dxf, out_meta_json):
    u"""3Dモデルを読み取り専用で開き、4ビューDXF+metaを生成する(調査/run_phase2_pipeline.py と同じ手順)。

    STEPパスが来ても open_part_readonly は拡張子を問わず OpenDoc6 に渡すだけなので、
    SW2023がSTEPを開ける限りそのまま通る(CLAUDE.md: SW2023はSTEPを開ける)。
    """
    meta = {"part_path": model_path}
    part = None
    dwgdoc = None
    sw = None
    try:
        sw, mod = dp.connect()
        meta["sw_progid"] = dp.sw_compat.detected_progid()

        # ❗GetFirstDocument+GetNext は1件目で切れる(実測)。スナップショットは GetDocuments 経由
        pre_docs = dp.list_open_docs(sw)
        meta["pre_open_titles"] = [t for t, _ in pre_docs]

        part, vh = dp.open_model_readonly(sw, mod, model_path)
        meta["version_history"] = vh
        m = dp.part_metrics(mod, part)
        meta["metrics"] = m

        dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
        meta["sheet"] = dp.sheet_info(mod, sheet)

        # ❗インポート部品は GetPathName が空 → ModelName は title を使う(part.model_name)
        views = dp.insert_standard_views(mod, dwg, part.model_name, m["size_mm"])
        meta["views"] = views
        meta["views_on_sheet"] = dp.list_views(mod, dwg)

        dwgdoc.doc.ForceRebuild3(False)
        dwgdoc.doc.ViewZoomtofit2()

        res = dp.export_dxf(sw, dwgdoc, out_views_dxf)
        meta["export"] = {"path": res["path"], "bytes": res["bytes"], "warnings": res["warnings"]}
        meta["ok"] = True
    except Exception:
        meta["ok"] = False
        meta["error"] = traceback.format_exc()
    finally:
        for od, label in ((dwgdoc, u"図面"), (part, u"部品")):
            if od is None:
                continue
            try:
                od.close()
            except Exception as e:
                meta.setdefault("close_errors", []).append("%s: %s" % (label, e))
        if sw is not None:
            meta["post_open_titles"] = [t for t, _ in dp.list_open_docs(sw)]
            leaked = [t for t in meta["post_open_titles"]
                      if t not in meta.get("pre_open_titles", [])]
            if leaked:
                meta["leaked_docs"] = leaked

    out_dir = os.path.dirname(out_meta_json)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    with io.open(out_meta_json, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    if not meta.get("ok"):
        raise RuntimeError(u"SW投影に失敗: %s" % meta.get("error"))
    return meta


# ---------------------------------------------------------------------------
# 6) 独立検証(調査/verify_generated.py の A/B/C/B2 を汎用化。D=人間図面比較は
#    2部品専用のハードコード比較のためここでは対象外。呼び出し側が別途行う)
# ---------------------------------------------------------------------------
def _resolve_kind(item, defaults):
    kind = item["kind"]
    if kind != "diameter":
        return kind
    style = dict(dim_engine.DIAMETER_STYLE_DEFAULT)
    style.update(defaults.get("diameter_style", {}))
    return ("diameter_native" if style[item.get("context", "profile_view")] == "native"
            else "diameter_linear")


def independent_verify(dxf_path, plan_path):
    u"""dim_engine.apply_plan の自己申告を信用せず、保存済みDXFを新規に読み直して検証する。"""
    with io.open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    defaults = plan.get("defaults", {})
    spec = dim_engine.load_dimstyle_spec()
    want = dim_engine.base_dimvars(spec)

    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    dims = [e for e in msp if e.dxftype() == "DIMENSION"]

    result = {"dxf": dxf_path, "plan": plan_path}

    # ---- C. ファイル属性・図枠保持 ----
    _, fsum = subtract_frame(doc, template_path=FRAME_TEMPLATE)
    ok_c = (doc.dxfversion == "AC1015" and fsum["frame_matched"] == 113
            and "_OPEN30" in doc.blocks)
    result["file_attrs_ok"] = ok_c
    result["frame"] = fsum

    # ---- A. ゲート①独立再計算 ----
    order = [d["id"] for d in plan["dimensions"]]
    by_id = {d["id"]: d for d in plan["dimensions"]}
    rows = []
    gate_ok = True
    for dim in sorted(dims, key=lambda e: e.dxf.dimstyle):
        style = dim.dxf.dimstyle
        m_idx = re.sub(r"\D", "", style)
        idx = int(m_idx) - 1 if m_idx else -1
        did = order[idx] if 0 <= idx < len(order) else "?"
        item = by_id.get(did)
        if item is None:
            continue
        kind = _resolve_kind(item, defaults)
        exp = float(item["value_expected"])
        m = dim_engine.measure_from_defpoints(dim)
        t = dim_engine.dim_text_of(doc, dim)
        tv = dim_engine.parse_dim_text_value(t)
        diff = None if m is None else abs(m - exp)
        tdiff = None if (tv is None or m is None) else abs(tv - m)
        base = dim.dxf.dimtype & 7
        kind_ok = ((kind == "diameter_native" and base == 3)
                   or (kind in ("linear", "diameter_linear") and base == 0)
                   or (kind == "radius" and base == 4)
                   or (kind == "angle" and base == 2))
        ok = (diff is not None and diff <= 0.01) and (tdiff is None or tdiff <= 0.01) and kind_ok
        gate_ok = gate_ok and ok
        rows.append({"id": did, "style": style, "kind": kind, "dimtype": dim.dxf.dimtype,
                     "dimtype_base": base, "kind_impl_ok": kind_ok,
                     "expected": exp, "measured": None if m is None else round(m, 6),
                     "diff_mm": None if diff is None else round(diff, 6),
                     "text": t, "text_value": tv,
                     "text_diff_mm": None if tdiff is None else round(tdiff, 6), "ok": ok})
    result["gate1"] = rows
    result["gate1_ok"] = gate_ok
    result["gate1_max_diff_mm"] = (max((r["diff_mm"] for r in rows if r["diff_mm"] is not None),
                                        default=0.0))

    # ---- B. DIMSTYLE実効値 ----
    gen_styles = [s for s in doc.dimstyles if re.fullmatch(r"GEN\d+", s.dxf.name)]
    mism = []
    for st in gen_styles:
        for k, v in want.items():
            got = st.dxf.get(k, None)
            bad = (abs(float(got) - v) > 1e-9) if isinstance(v, float) else (str(got) != str(v))
            if bad:
                mism.append({"style": st.dxf.name, "var": k, "expected": v, "actual": got})
    font_ok = False
    if gen_styles:
        txtsty = doc.styles.get(gen_styles[0].dxf.dimtxsty)
        ts = spec["text_style"]
        font_ok = (str(txtsty.dxf.font).lower() == ts["font"].lower()
                   and abs(txtsty.dxf.width - ts["width_factor"]) < 1e-6
                   and abs(txtsty.dxf.oblique - ts.get("oblique", 0.0)) < 1e-6)
    xdata_count = sum(1 for d in dims if d.xdata is not None)
    posts = {}
    for dim in dims:
        m_idx = re.sub(r"\D", "", dim.dxf.dimstyle)
        idx = int(m_idx) - 1 if m_idx else -1
        if 0 <= idx < len(order):
            posts[order[idx]] = doc.dimstyles.get(dim.dxf.dimstyle).dxf.get("dimpost", "")
    post_ok = all((posts.get(i["id"], "") == "%%c<>")
                  == _resolve_kind(i, defaults).startswith("diameter")
                  for i in plan["dimensions"])
    style_ok = (not mism) and font_ok and xdata_count == 0 and post_ok \
        and len(gen_styles) == len(dims) and all(r["kind_impl_ok"] for r in rows)
    result["style_ok"] = style_ok
    result["style_mismatches"] = mism

    # ---- B2. 注記の書式 ----
    note_ok = True
    notes_found = []
    for e in msp:
        if e.dxftype() != "MTEXT":
            continue
        t = e.text
        if u"φ" in t or u"Φ" in t:
            note_ok = False
        if "%%c" in t or u"キリ" in t or u"ザグリ" in t or u"深さ" in t:
            notes_found.append(t)
    result["notes"] = notes_found
    result["note_ok"] = note_ok

    result["ok"] = all([ok_c, gate_ok, style_ok, note_ok])
    return result


# ---------------------------------------------------------------------------
# 台帳追記
# ---------------------------------------------------------------------------
LEDGER_PATH = os.path.join(ROOT, u"台帳.md")


def _md_cell(s):
    u"""Markdown表の1セルへ安全化(改行/パイプはテーブルを壊すので1行に潰す)。"""
    s = re.sub(r"\s+", " ", str(s)).strip().replace("|", "\\|")
    if len(s) > 200:
        s = s[:200] + u"…"
    return s


def append_ledger(row):
    header_needed = not os.path.exists(LEDGER_PATH)
    line = ("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |\n" % tuple(
        _md_cell(row[k]) for k in ("date", "zuban", "part_name", "model", "out_dxf",
                                    "gate1", "gate2", "gate3", "gate4", "status", "note")))
    with io.open(LEDGER_PATH, "a", encoding="utf-8") as f:
        if header_needed:
            f.write(u"# 処理台帳 — Drawing-Generator-Agent\n\n"
                    u"生成した部品図の記録。1件1行。検証ゲートを通ったものだけ「納品」になる。\n\n"
                    u"| 日付 | 図番 | 品名 | 入力3D | 出力DXF | ゲート①寸法値 | ゲート②完全性 |"
                    u" ゲート③目視 | ゲート④人間図比較 | 状態 | 備考 |\n"
                    u"|---|---|---|---|---|---|---|---|---|---|---|\n")
        f.write(line)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------
def main(argv):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=u"1コマンド部品図生成ランナー")
    ap.add_argument("--model", required=True, help=u"SLDPRT/STEPパス")
    ap.add_argument("--plan", required=True, help=u"作図計画JSON")
    ap.add_argument("--request", required=True, help=u"依頼情報JSON")
    ap.add_argument("--out-dir", required=True, help=u"納品先(生成図面)")
    ap.add_argument("--zuban", default=None, help=u"図番(未指定なら依頼JSONの図番)")
    ap.add_argument("--skip-sw", action="store_true", help=u"SW投影を省略し既存DXF/metaを再利用")
    ap.add_argument("--views-dxf", default=None, help=u"(既定=調査/phase2_out_<モデル名>.dxf)")
    ap.add_argument("--meta-json", default=None, help=u"(既定=調査/phase2_meta_<モデル名>.json)")
    args = ap.parse_args(argv[1:])

    model_path = os.path.abspath(args.model)
    stem = os.path.splitext(os.path.basename(model_path))[0]
    out_dir = os.path.abspath(args.out_dir)
    reject_dir = os.path.join(out_dir, u"不合格")

    views_dxf = args.views_dxf or os.path.join(ROOT, u"調査", u"phase2_out_%s.dxf" % stem)
    meta_json = args.meta_json or os.path.join(ROOT, u"調査", u"phase2_meta_%s.json" % stem)

    with io.open(args.request, encoding="utf-8") as f:
        request = json.load(f)

    zuban = args.zuban or request.get(u"図番")
    if not zuban:
        raise ValueError(u"図番が未指定です(--zuban か 依頼JSONの'図番')")
    default_part_name = stem.rsplit("_", 1)[-1] if "_" in stem else stem
    part_name = request.get(u"品名") or default_part_name

    summary = {"model": model_path, "plan": os.path.abspath(args.plan),
               "request": os.path.abspath(args.request), "zuban": zuban,
               "part_name": part_name, "skip_sw": bool(args.skip_sw), "steps": {}}

    # ---- 1) SW投影(または既存流用) ----
    if args.skip_sw:
        if not (os.path.exists(views_dxf) and os.path.exists(meta_json)):
            raise IOError(u"--skip-sw だが再利用対象が無い: %s / %s" % (views_dxf, meta_json))
        summary["steps"]["sw_projection"] = {"skipped": True,
                                             "views_dxf": views_dxf, "meta_json": meta_json}
    else:
        proj_meta = run_sw_projection(model_path, views_dxf, meta_json)
        summary["steps"]["sw_projection"] = {"skipped": False,
                                             "views_dxf": views_dxf, "meta_json": meta_json,
                                             "mass_kg": proj_meta["metrics"]["mass_kg"],
                                             "density_is_default_water":
                                                 proj_meta["metrics"]["density_is_default_water"]}

    # ---- 2) 重量算出(材質->密度) ----
    material = request.get(u"材質", "")
    material_shape = request.get(u"材質形状", "")
    density, density_note = resolve_density(material, request.get(u"密度_kgm3"))
    summary["steps"]["density"] = {"material": material, "resolved_kgm3": density,
                                   "note": density_note}

    # ---- 3) compose(図枠+ビュー+表題欄) ----
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    safe_zuban = re.sub(r'[\\/:*?"<>|]', "_", zuban)
    out_stem = u"%s_%s" % (safe_zuban, part_name)
    out_dxf = os.path.join(out_dir, out_stem + ".dxf")
    out_png = os.path.join(out_dir, out_stem + ".png")

    fields = {
        u"品名": part_name,
        u"図番": zuban,
        u"装置名": request.get(u"装置名", ""),
        u"材質": material,
        u"材質形状": material_shape,
        u"個数": request.get(u"個数", 1),
        u"製図者": request.get(u"製図者", "AI"),
    }
    if request.get(u"連番"):
        fields[u"連番"] = request[u"連番"]
    if density is not None:
        fields[u"密度_kgm3"] = density

    scale = float(request.get(u"尺度", 1.0))
    comp = compose_drawing.compose(views_dxf, meta_json, fields, scale=scale, out_path=out_dxf)
    summary["steps"]["compose"] = {
        "frame_check": comp["frame_check"], "zone_overlaps": comp["zone_overlaps"],
        "view_pair_overlaps": comp["view_pair_overlaps"], "weight": comp["weight"],
        "warnings": comp["warnings"]}

    # ---- 4) dim_engine(寸法・注記。ゲート①内蔵) ----
    gate1_ok = False
    dim_report = None
    dim_error = None
    try:
        dim_report = dim_engine.apply_plan(args.plan, out_dxf, base_dxf_override=out_dxf)
        gate1_ok = dim_report["gate1_ok"]
        summary["steps"]["dim_engine"] = {
            "gate1_ok": gate1_ok, "warnings": dim_report["warnings"],
            "style_check_ok": dim_report["style_check"]["ok"],
            "layout_collisions": dim_report["layout"]["collisions"],
            "resolved_kinds": dim_report["resolved_kinds"]}
    except dim_engine.DimensionGateError as e:
        dim_error = str(e)
        summary["steps"]["dim_engine"] = {"gate1_ok": False, "error": dim_error}

    # ---- 5) ゲート②(寸法完全性) ----
    gate2_ok = False
    gate2_report = None
    if gate1_ok:
        gate2_report = gate2_completeness.check_completeness(out_dxf, args.plan)
        gate2_ok = gate2_report["ok"]
        summary["steps"]["gate2"] = {"ok": gate2_ok,
                                     "unspecified_count": len(gate2_report["unspecified"]),
                                     "redundant_count": len(gate2_report["redundant_dimensions"])}
    else:
        summary["steps"]["gate2"] = {"ok": False, "skipped_reason": u"ゲート①不合格のため未実施"}

    # ---- 6) 独立検証 ----
    verify_ok = False
    verify_report = None
    if gate1_ok:
        verify_report = independent_verify(out_dxf, args.plan)
        verify_ok = verify_report["ok"]
        summary["steps"]["independent_verify"] = {
            "ok": verify_ok, "file_attrs_ok": verify_report["file_attrs_ok"],
            "gate1_ok": verify_report["gate1_ok"],
            "gate1_max_diff_mm": verify_report["gate1_max_diff_mm"],
            "style_ok": verify_report["style_ok"], "note_ok": verify_report["note_ok"]}
    else:
        summary["steps"]["independent_verify"] = {"ok": False,
                                                   "skipped_reason": u"ゲート①不合格のため未実施"}

    overall_ok = gate1_ok and gate2_ok and verify_ok

    # ---- 7) PNG化(現存する最終dxfに対して実施。失敗しても処理は継続) ----
    png_path = None
    if os.path.exists(out_dxf):
        try:
            title = u"%s %s(%s)" % (zuban, part_name,
                                    u"合格" if overall_ok else u"不合格")
            compose_drawing.render_png(out_dxf, out_png, title=title)
            png_path = out_png
        except Exception:
            summary["steps"]["png_error"] = traceback.format_exc()

    # ---- 8) 不合格の隔離 ----
    final_dxf = out_dxf
    final_png = png_path
    if not overall_ok:
        if not os.path.isdir(reject_dir):
            os.makedirs(reject_dir, exist_ok=True)
        rej_dxf = os.path.join(reject_dir, out_stem + ".dxf")
        if os.path.exists(out_dxf):
            shutil.move(out_dxf, rej_dxf)
            final_dxf = rej_dxf
        if png_path and os.path.exists(png_path):
            rej_png = os.path.join(reject_dir, out_stem + ".png")
            shutil.move(png_path, rej_png)
            final_png = rej_png

    summary["final_dxf"] = final_dxf
    summary["final_png"] = final_png
    summary["gate1_ok"] = gate1_ok
    summary["gate2_ok"] = gate2_ok
    summary["verify_ok"] = verify_ok
    summary["overall_ok"] = overall_ok
    summary["status"] = u"検証通過" if overall_ok else u"不合格"
    if dim_error:
        summary["dim_error"] = dim_error

    result_json = os.path.join(out_dir, out_stem + u"_result.json")
    with io.open(result_json, "w", encoding="utf-8") as f:
        f.write(json.dumps({"summary": summary, "compose": comp, "dim": dim_report,
                            "gate2": gate2_report, "independent_verify": verify_report},
                           ensure_ascii=False, indent=2, default=str))
    summary["result_json"] = result_json

    # ---- 9) 台帳追記 ----
    gate1_text = (u"合格(最大差%.6fmm)" % verify_report["gate1_max_diff_mm"]
                  if verify_report else (u"不合格: %s" % dim_error if dim_error else u"未実施"))
    gate2_text = (u"合格(未指定0件)" if gate2_ok
                  else (u"不合格(未指定%d件)" % len(gate2_report["unspecified"])
                        if gate2_report else u"未実施"))
    gate3_text = u"PNG生成済み(要目視)" if final_png else u"PNG未生成"
    gate4_text = u"未実施(バッチ生成のためスキップ)"
    def _relslash(p):
        return os.path.relpath(p, ROOT).replace(os.sep, "/")

    append_ledger({
        "date": datetime.date.today().isoformat(),
        "zuban": zuban, "part_name": part_name, "model": os.path.basename(model_path),
        "out_dxf": ("`%s`" % _relslash(final_dxf)) if final_dxf else "-",
        "gate1": gate1_text, "gate2": gate2_text, "gate3": gate3_text, "gate4": gate4_text,
        "status": summary["status"],
        "note": (u"generate_drawing.py自動生成。結果=`%s`" % _relslash(result_json)),
    })

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
