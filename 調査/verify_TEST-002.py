# -*- coding: utf-8 -*-
u"""TEST-002_ホルダー.dxf の独立検証(フェーズ3-B・ゲート①/スタイル/人間図面比較)。

**dim_engine の自己申告を信用せず、保存済みDXFを新規に読み直して**検証する:
  A. 全DIMENSIONの実測値(defpointから独立再計算)vs 期待値表 vs 描画テキスト
  B. 全DIMSTYLEの実効値 vs 図枠/dimstyle_spec.json(1寸法=1スタイル・XDATA不使用も確認)
  C. 図枠113エンティティの保持 / DXFバージョン・コードページ
  D. 人間の正解図面(15015-P3-012_013.dxf のホルダー部分)との寸法対応表
  E. 公差機構(ネイティブdimtol + ゼロ側「0」整形)のスモークテスト

実行: python 調査/verify_TEST-002.py
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
OUT_DXF = os.path.join(ROOT, u"生成図面", u"TEST-002_ホルダー.dxf")
PLAN = os.path.join(ROOT, u"調査", u"plan_TEST-002_ホルダー.json")
HUMAN = (u"C:\\Users\\imaizumi.LINEWORKS-NET\\Documents\\3D CAD Operator Agent\\"
         u"DXFデータ 部品表用\\POS(回転)\\15015-P3-012_013.dxf")
HUMAN_HOLDER_X_MIN = 210.0  # 013(ホルダー)は右側の図枠。x>210 のエンティティが該当


def measure(dim):
    return dim_engine.measure_from_defpoints(dim)


def dim_text(doc, dim):
    return dim_engine.dim_text_of(doc, dim)


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    with io.open(PLAN, encoding="utf-8") as f:
        plan = json.load(f)
    spec = dim_engine.load_dimstyle_spec()
    want = dim_engine.base_dimvars(spec)

    doc = ezdxf.readfile(OUT_DXF)
    msp = doc.modelspace()
    dims = [e for e in msp if e.dxftype() == "DIMENSION"]
    leaders = [e for e in msp if e.dxftype() == "LEADER"]

    result = {"ok": True}

    # ---------------- C. ファイル属性・図枠保持 ----------------
    print(u"===== C. ファイル属性・図枠保持 =====")
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
    print(u"-> %s" % ("OK" if ok_c else "NG"))

    # ---------------- A. ゲート①(実測 vs 期待 vs 表示) ----------------
    print(u"\n===== A. ゲート①: 実測値(defpoint独立再計算) vs 計画期待値 vs 描画文字 =====")
    exp_by_style = {}
    for d in plan["dimensions"]:
        exp_by_style[d["id"]] = d
    # DIMSTYLE名 -> 計画id は dim_engine の生成順(GEN001..)で決まる
    order = [d["id"] for d in plan["dimensions"]]
    rows = []
    print(u"%-24s %-8s %-7s %10s %10s %10s  %-10s %s"
          % ("id", "style", "dimtype", "expected", "measured", "diff_mm", "text", "判定"))
    gate_ok = True
    for i, dim in enumerate(sorted(dims, key=lambda e: e.dxf.dimstyle)):
        style = dim.dxf.dimstyle
        idx = int(re.sub(r"\D", "", style)) - 1
        did = order[idx] if 0 <= idx < len(order) else "?"
        exp = float(exp_by_style[did]["value_expected"])
        m = measure(dim)
        t = dim_text(doc, dim)
        tv = dim_engine.parse_dim_text_value(t)
        diff = abs(m - exp)
        tdiff = abs(tv - m) if tv is not None else None
        ok = diff <= 0.01 and (tdiff is None or tdiff <= 0.01)
        gate_ok = gate_ok and ok
        rows.append({"id": did, "style": style, "dimtype": dim.dxf.dimtype,
                     "expected": exp, "measured": round(m, 6), "diff_mm": round(diff, 6),
                     "text": t, "text_value": tv,
                     "text_diff_mm": None if tdiff is None else round(tdiff, 6), "ok": ok})
        print(u"%-24s %-8s %-7d %10.4f %10.4f %10.6f  %-10s %s"
              % (did, style, dim.dxf.dimtype, exp, m, diff, t, "OK" if ok else "NG"))
    result["gate1"] = rows
    result["gate1_ok"] = gate_ok
    print(u"-> ゲート① %s (許容0.01mm・最大差 %.6fmm)"
          % ("OK" if gate_ok else "NG", max(r["diff_mm"] for r in rows) if rows else 0.0))

    # ---------------- B. DIMSTYLE実効値 vs dimstyle_spec.json ----------------
    print(u"\n===== B. DIMSTYLE実効値 vs 図枠/dimstyle_spec.json =====")
    # GENnnn = 寸法専用スタイル(1寸法=1スタイル)、GENLDR = 引出線用の共通スタイル
    gen_styles = [s for s in doc.dimstyles if re.fullmatch(r"GEN\d+", s.dxf.name)]
    mism = []
    for st in gen_styles:
        for k, v in want.items():
            got = st.dxf.get(k, None)
            bad = (abs(float(got) - v) > 1e-9) if isinstance(v, float) else (str(got) != str(v))
            if bad:
                mism.append({"style": st.dxf.name, "var": k, "expected": v, "actual": got})
    # dimfit(287)はR13/R14旧変数でezdxfはAC1015に書き出さない。等価のdimatfit/dimtmoveで検証する
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
    print(u"生成DIMSTYLE数(GENnnn)=%d(専用スタイル数 == 寸法本数: %s)"
          % (len(gen_styles), len(gen_styles) == len(dims)))
    print(u"dimfit等価変換: %s" % json.dumps(fit_note, ensure_ascii=False))
    print(u"照合変数=%d種 × %d スタイル / 不一致=%d件" % (len(want), len(gen_styles), len(mism)))
    for m in mism:
        print(u"   NG %s" % m)
    sample = gen_styles[0]
    print(u"実効値サンプル(%s): %s" % (sample.dxf.name, {k: sample.dxf.get(k) for k in sorted(want)}))
    print(u"寸法文字スタイル: name=%s font=%s width=%.3f oblique=%.1f -> %s"
          % (txtsty.dxf.name, txtsty.dxf.font, txtsty.dxf.width, txtsty.dxf.oblique,
             "OK" if font_ok else "NG"))
    print(u"XDATA(DSTYLE)オーバーライドを持つDIMENSION: %d本(コーパス実測0件・0であるべき)"
          % xdata_count)
    # dimpost の割当確認
    posts = {}
    for i, dim in enumerate(sorted(dims, key=lambda e: e.dxf.dimstyle)):
        st = doc.dimstyles.get(dim.dxf.dimstyle)
        idx = int(re.sub(r"\D", "", dim.dxf.dimstyle)) - 1
        posts[order[idx]] = st.dxf.get("dimpost", "")
    print(u"dimpost: %s" % json.dumps(posts, ensure_ascii=False))
    post_ok = all((posts[d["id"]] == "%%c<>") == (d["kind"] == "diameter_linear")
                  for d in plan["dimensions"])
    style_ok = (not mism) and font_ok and xdata_count == 0 and post_ok \
        and len(gen_styles) == len(dims)
    result["style_ok"] = style_ok
    result["style_mismatches"] = mism
    result["dimfit_equivalence"] = fit_note
    result["dimpost"] = posts
    print(u"-> スタイル検証 %s" % ("OK" if style_ok else "NG"))

    # ---------------- 注記の書式検証 ----------------
    print(u"\n===== B2. 注記(%%c制御コード・半角統一) =====")
    note_ok = True
    for e in msp:
        if e.dxftype() != "MTEXT":
            continue
        t = e.text
        if u"\u03c6" in t or u"\u03a6" in t:
            print(u"   NG Unicodeのφが混入: %r" % t)
            note_ok = False
    hole = [e for e in msp if e.dxftype() == "MTEXT" and "%%c" in e.text]
    for e in hole:
        print(u"   穴注記 MTEXT: %r insert=(%.3f,%.3f)" % (e.text, e.dxf.insert.x, e.dxf.insert.y))
        if re.search(r"[\uFF10-\uFF19\uFF0D]", e.text):
            print(u"   NG 全角数字/全角ハイフンが混入(裁定Q5: 穴注記は半角)")
            note_ok = False
    result["note_ok"] = note_ok
    print(u"-> %s" % ("OK" if note_ok else "NG"))

    # ---------------- D. 人間図面との比較 ----------------
    print(u"\n===== D. 人間の正解図面(15015-P3-012_013.dxf・ホルダー部分)との寸法対応 =====")
    hdoc = ezdxf.readfile(HUMAN)
    hmsp = hdoc.modelspace()
    hdims = []
    for e in hmsp:
        if e.dxftype() != "DIMENSION":
            continue
        if e.dxf.defpoint.x < HUMAN_HOLDER_X_MIN:
            continue
        st = hdoc.dimstyles.get(e.dxf.dimstyle)
        base = e.dxf.dimtype & 7
        if base == 3:
            m = math.hypot(e.dxf.defpoint4.x - e.dxf.defpoint.x,
                           e.dxf.defpoint4.y - e.dxf.defpoint.y)
            kind = "diameter_native"
        elif base == 4:
            m = math.hypot(e.dxf.defpoint4.x - e.dxf.defpoint.x,
                           e.dxf.defpoint4.y - e.dxf.defpoint.y)
            kind = "radius"
        else:
            a = math.radians(e.dxf.get("angle", 0.0))
            m = abs((e.dxf.defpoint3.x - e.dxf.defpoint2.x) * math.cos(a)
                    + (e.dxf.defpoint3.y - e.dxf.defpoint2.y) * math.sin(a))
            kind = "diameter_linear" if st.dxf.get("dimpost", "") == "%%c<>" else "linear"
        hdims.append({"kind": kind, "value": round(m, 4),
                      "dimpost": st.dxf.get("dimpost", ""), "style": e.dxf.dimstyle})
    hnotes = [e.text for e in hmsp
              if e.dxftype() == "MTEXT" and e.dxf.insert.x >= HUMAN_HOLDER_X_MIN
              and ("%%c" in e.text or u"キリ" in e.text or u"ザグリ" in e.text)]
    print(u"人間図面のホルダー寸法 %d本:" % len(hdims))
    for h in sorted(hdims, key=lambda r: -r["value"]):
        print(u"   %-16s %8.3f  dimpost=%r" % (h["kind"], h["value"], h["dimpost"]))
    print(u"人間図面のホルダー穴注記 %d件: %s" % (len(hnotes), hnotes))

    gen = [{"kind": r["id"], "value": r["measured"]} for r in rows]
    gvals = sorted(round(r["measured"], 3) for r in rows)
    hvals = sorted(round(h["value"], 3) for h in hdims)
    print(u"\n生成図面の寸法値(昇順): %s" % gvals)
    print(u"人間図面の寸法値(昇順): %s" % hvals)
    missing = [v for v in hvals if v not in gvals]
    extra = [v for v in gvals if v not in hvals]
    print(u"人間にあって生成に無い(不足): %s" % (missing or u"なし"))
    print(u"生成にあって人間に無い(過剰): %s" % (extra or u"なし"))
    result["human_compare"] = {"human": hdims, "generated": gen,
                               "missing": missing, "extra": extra,
                               "human_notes": hnotes}

    # ---------------- E. 公差機構スモークテスト ----------------
    print(u"\n===== E. 公差機構(dimtol + ゼロ側『0』整形)スモークテスト =====")
    tdoc = ezdxf.new("R2000", setup=False)
    tsty = dim_engine.ensure_text_style(tdoc, spec)
    dim_engine.ensure_arrow_blocks(tdoc, ("_OPEN30",))
    tv = dim_engine.base_dimvars(spec)
    tv.update({"dimtxsty": tsty, "dimtol": 1, "dimtp": 0.0, "dimtm": 0.021,
               "dimtfac": 0.625, "dimtdec": 3, "dimtolj": 1})
    dim_engine._new_dimstyle(tdoc, "TOL1", tv)
    td = tdoc.modelspace().add_linear_dim(base=(0, 20), p1=(0, 0), p2=(30, 0),
                                         angle=0, dimstyle="TOL1")
    td.render()
    raw_before = dim_engine.dim_text_of(tdoc, td.dimension)
    dim_engine.fix_zero_tolerance_text(tdoc, td.dimension)
    raw_after = dim_engine.dim_text_of(tdoc, td.dimension)
    tol_ok = ("0.000" in raw_before) and ("0.000" not in raw_after) and ("-0.021" in raw_after)
    print(u"整形前: %r" % raw_before)
    print(u"整形後: %r" % raw_after)
    print(u"-> ゼロ側『0』整形 %s" % ("OK" if tol_ok else "NG"))
    result["tolerance_smoke_ok"] = tol_ok

    result["ok"] = all([ok_c, gate_ok, style_ok, note_ok, tol_ok])
    print(u"\n===== 総合: %s =====" % ("合格" if result["ok"] else "不合格"))

    out = os.path.join(ROOT, u"調査", u"phase3b_verify_independent.json")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(u"saved %s" % out)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
