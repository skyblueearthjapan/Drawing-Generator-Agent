# -*- coding: utf-8 -*-
u"""生成図面の独立検証(汎用版。調査/verify_TEST-002.py を部品非依存に一般化したもの)。

**dim_engine の自己申告を信用せず、保存済みDXFを新規に読み直して**検証する:
  A. 全DIMENSIONの実測値(defpointから独立再計算)vs 計画期待値 vs 描画テキスト(=ゲート①再現)
  B. 全DIMSTYLEの実効値 vs 図枠/dimstyle_spec.json(1寸法=1スタイル・XDATA不使用・dimpost割当)
  C. 図枠113エンティティの保持 / DXFバージョン・コードページ
  D. 人間の正解図面との寸法対応表(どの寸法を入れるかの正解との突き合わせ)
  E. 公差機構(ネイティブdimtol + ゼロ側「0」整形 + \\H係数整形)のスモークテスト

実行:
    python 調査/verify_generated.py TEST-002
    python 調査/verify_generated.py TEST-003
    python 調査/verify_generated.py all
"""
import io
import json
import math
import os
import re
import sys

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import dim_engine  # noqa: E402
from engine.frame_extract import subtract_frame  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HUMAN_REF = (u"C:\\Users\\imaizumi.LINEWORKS-NET\\Documents\\3D CAD Operator Agent\\"
             u"DXFデータ 部品表用\\POS(回転)\\15015-P3-012_013.dxf")

# 人間の正解図面 15015-P3-012_013.dxf は1枚に2部品。x座標で左右に分かれる
# (左=012 端子棒 / 右=013 ホルダー)。読み取り専用。
TESTS = {
    "TEST-002": {
        "title": u"ホルダー",
        "plan": os.path.join(ROOT, u"調査", u"plan_TEST-002_ホルダー.json"),
        "dxf": os.path.join(ROOT, u"生成図面", u"TEST-002_ホルダー.dxf"),
        "human_x_range": (210.0, 1e9),
        "out": os.path.join(ROOT, u"調査", u"verify_independent_TEST-002.json"),
    },
    "TEST-003": {
        "title": u"端子棒",
        "plan": os.path.join(ROOT, u"調査", u"plan_TEST-003_端子棒.json"),
        "dxf": os.path.join(ROOT, u"生成図面", u"TEST-003_端子棒.dxf"),
        "human_x_range": (-1e9, 210.0),
        "out": os.path.join(ROOT, u"調査", u"verify_independent_TEST-003.json"),
    },
}


def resolve_kind(item, defaults):
    u"""計画の kind を実装方式へ解決する(dim_engine と同じ規則の独立実装)。"""
    kind = item["kind"]
    if kind != "diameter":
        return kind
    style = dict(dim_engine.DIAMETER_STYLE_DEFAULT)
    style.update(defaults.get("diameter_style", {}))
    return "diameter_native" if style[item.get("context", "profile_view")] == "native" \
        else "diameter_linear"


def human_dims(doc, x0, x1):
    u"""人間図面の指定x範囲のDIMENSIONを実測して一覧にする(読み取り専用)。"""
    out = []
    for e in doc.modelspace():
        if e.dxftype() != "DIMENSION":
            continue
        d = e.dxf
        if not (x0 <= d.defpoint.x <= x1):
            continue
        st = doc.dimstyles.get(d.dimstyle)
        post = st.dxf.get("dimpost", "")
        base = d.dimtype & 7
        if base == 3:
            m = math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y)
            kind = "diameter_native"
        elif base == 4:
            m = math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y)
            kind = "radius"
        else:
            a = math.radians(d.get("angle", 0.0))
            m = abs((d.defpoint3.x - d.defpoint2.x) * math.cos(a)
                    + (d.defpoint3.y - d.defpoint2.y) * math.sin(a))
            kind = "diameter_linear" if post == "%%c<>" else "linear"
        draw = None
        g = d.get("geometry", None)
        if g and g in doc.blocks:
            for b in doc.blocks.get(g):
                if b.dxftype() == "MTEXT":
                    draw = b.text
                    break
        out.append({"kind": kind, "value": round(m, 4), "dimpost": post,
                    "style": d.dimstyle, "draw_text": draw,
                    "dimtol": st.dxf.get("dimtol", 0),
                    "dimtp": st.dxf.get("dimtp", 0.0), "dimtm": st.dxf.get("dimtm", 0.0)})
    return out


def human_notes(doc, x0, x1):
    out = []
    for e in doc.modelspace():
        if e.dxftype() != "MTEXT":
            continue
        p = e.dxf.insert
        if not (x0 <= p.x <= x1):
            continue
        t = e.text
        if ("%%c" in t or u"キリ" in t or u"ザグリ" in t
                or re.search(u"[ＭM][０-９0-9]", t)):
            out.append(t)
    return out


def verify(test_id, cfg):
    print(u"\n" + u"=" * 78)
    print(u"===== %s(%s)の独立検証 =====" % (test_id, cfg["title"]))
    print(u"=" * 78)

    with io.open(cfg["plan"], encoding="utf-8") as f:
        plan = json.load(f)
    defaults = plan.get("defaults", {})
    spec = dim_engine.load_dimstyle_spec()
    want = dim_engine.base_dimvars(spec)

    doc = ezdxf.readfile(cfg["dxf"])
    msp = doc.modelspace()
    dims = [e for e in msp if e.dxftype() == "DIMENSION"]
    leaders = [e for e in msp if e.dxftype() == "LEADER"]
    result = {"test": test_id, "dxf": cfg["dxf"]}

    # ---------------- C. ファイル属性・図枠保持 ----------------
    print(u"\n----- C. ファイル属性・図枠保持 -----")
    _, fsum = subtract_frame(doc, template_path=os.path.join(ROOT, u"図枠", u"frame_template.dxf"))
    print(u"dxfversion=%s / encoding=%s / $DWGCODEPAGE=%s"
          % (doc.dxfversion, doc.encoding, doc.header.get("$DWGCODEPAGE")))
    print(u"図枠一致: %d/113 (total=%d, remaining=%d)"
          % (fsum["frame_matched"], fsum["total"], fsum["remaining"]))
    print(u"DIMENSION=%d本 / LEADER=%d本 / _OPEN30ブロック=%s"
          % (len(dims), len(leaders), "_OPEN30" in doc.blocks))
    ok_c = (doc.dxfversion == "AC1015" and fsum["frame_matched"] == 113
            and "_OPEN30" in doc.blocks)
    result["file_attrs_ok"] = ok_c
    result["frame"] = fsum
    print(u"-> %s" % ("OK" if ok_c else "NG"))

    # ---------------- A. ゲート①独立再計算 ----------------
    print(u"\n----- A. ゲート①: 実測値(defpoint独立再計算) vs 計画期待値 vs 描画文字 -----")
    order = [d["id"] for d in plan["dimensions"]]
    by_id = {d["id"]: d for d in plan["dimensions"]}
    rows = []
    gate_ok = True
    print(u"%-20s %-8s %-16s %-7s %10s %10s %10s  %s"
          % ("id", "style", "kind", "dimtype", "expected", "measured", "diff_mm", "text"))
    for dim in sorted(dims, key=lambda e: e.dxf.dimstyle):
        style = dim.dxf.dimstyle
        idx = int(re.sub(r"\D", "", style)) - 1
        did = order[idx] if 0 <= idx < len(order) else "?"
        item = by_id[did]
        kind = resolve_kind(item, defaults)
        exp = float(item["value_expected"])
        m = dim_engine.measure_from_defpoints(dim)
        t = dim_engine.dim_text_of(doc, dim)
        tv = dim_engine.parse_dim_text_value(t)
        diff = abs(m - exp)
        tdiff = abs(tv - m) if tv is not None else None
        # 実装方式の一致(円形ビュー=ネイティブDIAMETER(base=3) / 輪郭ビュー=線形(base=0))
        base = dim.dxf.dimtype & 7
        kind_ok = ((kind == "diameter_native" and base == 3)
                   or (kind in ("linear", "diameter_linear") and base == 0)
                   or (kind == "radius" and base == 4)
                   or (kind == "angle" and base == 2))
        ok = diff <= 0.01 and (tdiff is None or tdiff <= 0.01) and kind_ok
        gate_ok = gate_ok and ok
        rows.append({"id": did, "style": style, "kind": kind, "dimtype": dim.dxf.dimtype,
                     "dimtype_base": base, "kind_impl_ok": kind_ok,
                     "expected": exp, "measured": round(m, 6), "diff_mm": round(diff, 6),
                     "text": t, "text_value": tv,
                     "text_diff_mm": None if tdiff is None else round(tdiff, 6), "ok": ok})
        print(u"%-20s %-8s %-16s %-7d %10.4f %10.4f %10.6f  %s%s"
              % (did, style, kind, dim.dxf.dimtype, exp, m, diff, t,
                 "" if ok else u"   <-- NG"))
    result["gate1"] = rows
    result["gate1_ok"] = gate_ok
    print(u"-> ゲート① %s (許容0.01mm・最大差 %.6fmm・寸法%d本)"
          % ("OK" if gate_ok else "NG",
             max(r["diff_mm"] for r in rows) if rows else 0.0, len(rows)))

    # ---------------- B. DIMSTYLE実効値 ----------------
    print(u"\n----- B. DIMSTYLE実効値 vs 図枠/dimstyle_spec.json -----")
    gen_styles = [s for s in doc.dimstyles if re.fullmatch(r"GEN\d+", s.dxf.name)]
    mism = []
    for st in gen_styles:
        for k, v in want.items():
            got = st.dxf.get(k, None)
            bad = (abs(float(got) - v) > 1e-9) if isinstance(v, float) else (str(got) != str(v))
            if bad:
                mism.append({"style": st.dxf.name, "var": k, "expected": v, "actual": got})
    fit_note = {"dimfit_in_file": gen_styles[0].dxf.get("dimfit", None),
                "dimatfit": gen_styles[0].dxf.get("dimatfit", None),
                "dimtmove": gen_styles[0].dxf.get("dimtmove", None),
                "spec_dimfit": spec["dimstyle_base"]["dimfit"]["value"]}
    txtsty = doc.styles.get(gen_styles[0].dxf.dimtxsty)
    ts = spec["text_style"]
    font_ok = (str(txtsty.dxf.font).lower() == ts["font"].lower()
               and abs(txtsty.dxf.width - ts["width_factor"]) < 1e-6
               and abs(txtsty.dxf.oblique - ts.get("oblique", 0.0)) < 1e-6)
    xdata_count = sum(1 for d in dims if d.xdata is not None)
    posts = {}
    for dim in dims:
        idx = int(re.sub(r"\D", "", dim.dxf.dimstyle)) - 1
        posts[order[idx]] = doc.dimstyles.get(dim.dxf.dimstyle).dxf.get("dimpost", "")
    post_ok = all((posts[i["id"]] == "%%c<>")
                  == resolve_kind(i, defaults).startswith("diameter")
                  for i in plan["dimensions"])
    print(u"生成DIMSTYLE数(GENnnn)=%d(専用スタイル数 == 寸法本数: %s)"
          % (len(gen_styles), len(gen_styles) == len(dims)))
    print(u"dimfit等価変換: %s" % json.dumps(fit_note, ensure_ascii=False))
    print(u"照合変数=%d種 × %d スタイル / 不一致=%d件" % (len(want), len(gen_styles), len(mism)))
    for m in mism:
        print(u"   NG %s" % m)
    print(u"寸法文字スタイル: name=%s font=%s width=%.3f oblique=%.1f -> %s"
          % (txtsty.dxf.name, txtsty.dxf.font, txtsty.dxf.width, txtsty.dxf.oblique,
             "OK" if font_ok else "NG"))
    print(u"XDATA(DSTYLE)オーバーライドを持つDIMENSION: %d本(コーパス実測0件・0であるべき)"
          % xdata_count)
    print(u"dimpost: %s" % json.dumps(posts, ensure_ascii=False))
    style_ok = (not mism) and font_ok and xdata_count == 0 and post_ok \
        and len(gen_styles) == len(dims) and all(r["kind_impl_ok"] for r in rows)
    result.update({"style_ok": style_ok, "style_mismatches": mism,
                   "dimfit_equivalence": fit_note, "dimpost": posts})
    print(u"-> スタイル検証 %s" % ("OK" if style_ok else "NG"))

    # ---------------- B2. 注記の書式 ----------------
    print(u"\n----- B2. 注記の書式(φは%%cのみ・キリ表記/全角は2026-08-09裁定で許容) -----")
    note_ok = True
    notes_found = []
    for e in msp:
        if e.dxftype() != "MTEXT":
            continue
        t = e.text
        if u"\u03c6" in t or u"\u03a6" in t:
            print(u"   NG UnicodeのφがMTEXTに混入: %r" % t)
            note_ok = False
        if "%%c" in t or u"キリ" in t or u"ザグリ" in t or u"深さ" in t:
            notes_found.append(t)
            print(u"   注記 MTEXT: %r insert=(%.3f,%.3f)" % (t, e.dxf.insert.x, e.dxf.insert.y))
    result["notes"] = notes_found
    result["note_ok"] = note_ok
    print(u"-> %s" % ("OK" if note_ok else "NG"))

    # ---------------- D. 人間図面との比較 ----------------
    print(u"\n----- D. 人間の正解図面との寸法対応(『どの寸法を入れるか』の正解) -----")
    hdoc = ezdxf.readfile(HUMAN_REF)
    x0, x1 = cfg["human_x_range"]
    hd = human_dims(hdoc, x0, x1)
    hn = human_notes(hdoc, x0, x1)
    print(u"人間図面の寸法 %d本:" % len(hd))
    for h in sorted(hd, key=lambda r: -r["value"]):
        print(u"   %-16s %8.3f  dimpost=%-8r tol=%s(+%s/-%s) text=%r"
              % (h["kind"], h["value"], h["dimpost"], h["dimtol"], h["dimtp"], h["dimtm"],
                 h["draw_text"]))
    print(u"人間図面の注記 %d件: %s" % (len(hn), hn))

    gvals = sorted(round(r["measured"], 3) for r in rows)
    hvals = sorted(round(h["value"], 3) for h in hd)
    missing = list(hvals)
    for v in gvals:
        if v in missing:
            missing.remove(v)
    extra = list(gvals)
    for v in hvals:
        if v in extra:
            extra.remove(v)
    print(u"\n生成図面の寸法値(昇順): %s" % gvals)
    print(u"人間図面の寸法値(昇順): %s" % hvals)
    print(u"人間にあって生成に無い(不足): %s" % (missing or u"なし"))
    print(u"生成にあって人間に無い(過剰): %s" % (extra or u"なし"))
    result["human_compare"] = {"human": hd, "human_notes": hn,
                               "generated": [{"id": r["id"], "kind": r["kind"],
                                              "value": r["measured"]} for r in rows],
                               "missing": missing, "extra": extra}
    set_ok = not missing and not extra
    result["human_set_ok"] = set_ok
    print(u"-> 寸法集合の一致 %s" % ("OK" if set_ok else "NG(要説明)"))

    # ---------------- E. 公差機構スモークテスト ----------------
    print(u"\n----- E. 公差機構(dimtol + ゼロ側『0』整形 + \\H係数整形) -----")
    tdoc = ezdxf.new("R2000", setup=False)
    tsty = dim_engine.ensure_text_style(tdoc, spec)
    dim_engine.ensure_arrow_blocks(tdoc, ("_OPEN30",))
    tv2 = dim_engine.base_dimvars(spec)
    tv2.update({"dimtxsty": tsty, "dimtol": 1, "dimtp": 0.0, "dimtm": 0.021,
                "dimtfac": 0.625, "dimtdec": 3, "dimtolj": 1})
    dim_engine._new_dimstyle(tdoc, "TOL1", tv2)
    td = tdoc.modelspace().add_linear_dim(base=(0, 20), p1=(0, 0), p2=(30, 0),
                                          angle=0, dimstyle="TOL1")
    td.render()
    raw_before = dim_engine.dim_text_of(tdoc, td.dimension)
    dim_engine.fix_zero_tolerance_text(tdoc, td.dimension)
    dim_engine.fix_tolerance_height_factor(tdoc, td.dimension, 0.625)
    raw_after = dim_engine.dim_text_of(tdoc, td.dimension)
    tol_ok = ("0.000" in raw_before) and ("0.000" not in raw_after) \
        and ("-0.021" in raw_after) and ("\\H0.625x;" in raw_after)
    print(u"整形前: %r" % raw_before)
    print(u"整形後: %r" % raw_after)
    print(u"-> ゼロ側『0』整形+\\H0.625x %s" % ("OK" if tol_ok else "NG"))
    result["tolerance_smoke_ok"] = tol_ok

    # 実戦投入された公差(この図面に実在するもの)
    real_tol = []
    for dim in dims:
        st = doc.dimstyles.get(dim.dxf.dimstyle)
        if st.dxf.get("dimtol", 0):
            idx = int(re.sub(r"\D", "", dim.dxf.dimstyle)) - 1
            real_tol.append({"id": order[idx], "dimtp": st.dxf.get("dimtp"),
                             "dimtm": st.dxf.get("dimtm"), "dimtdec": st.dxf.get("dimtdec"),
                             "text": dim_engine.dim_text_of(doc, dim)})
    result["tolerances_in_drawing"] = real_tol
    if real_tol:
        print(u"この図面の実公差:")
        for t in real_tol:
            print(u"   %s: +%s/-%s dec=%s text=%r"
                  % (t["id"], t["dimtp"], t["dimtm"], t["dimtdec"], t["text"]))
    else:
        print(u"この図面に公差付き寸法は無い")

    result["ok"] = all([ok_c, gate_ok, style_ok, note_ok, tol_ok])
    print(u"\n===== %s 総合: %s =====" % (test_id, u"合格" if result["ok"] else u"不合格"))
    with io.open(cfg["out"], "w", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(u"saved %s" % cfg["out"])
    return result


def main(argv):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ids = list(TESTS) if (len(argv) < 2 or argv[1] == "all") else argv[1:]
    rc = 0
    for t in ids:
        r = verify(t, TESTS[t])
        if not r["ok"]:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
