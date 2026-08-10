# -*- coding: utf-8 -*-
u"""補助線が輪郭線と溶ける現象(論点7)を **人間図面と生成図面で同じ物差しで測る**。

今泉さん指摘:「線の一番先端から取ると、寸法線と図解の線がすべて寸法線のように見える」。
この指摘に対して「では人間はどうしているのか」を実データで確かめないまま
すき間を広げる等の対処をすると、**自社流儀(dimexo=1.0 が927/927=100%)から外れる**。

測るもの(1図面あたり):
  - 線形寸法(dimtype 0/1)の本数
  - そのうち **補助線が輪郭線と同一直線上で近接している** 本数(= 溶けている本数)と比率
判定器は `engine/dim_engine.find_collinear_contours`(生成側の検出と同一実装)。

実行:
    python 調査/measure_extension_collinear.py                # 人間5枚 vs 生成5枚
    python 調査/measure_extension_collinear.py --json out.json
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ezdxf                                            # noqa: E402
from engine import dim_engine                           # noqa: E402
from engine.frame_extract import subtract_frame         # noqa: E402

HUMAN_DIR = os.path.join(ROOT, u"荏原トライ調整用", u"DXF", u"部品表用DXFデータ")
GEN_DIR = os.path.join(ROOT, u"data", u"納品箱")

CASES = [
    (u"25154-1-27", u"走行フレーム踏板", u"1.走行軸", u"BLIND2-25154-1-27"),
    (u"25154-2-16", u"指針", u"2.ターン軸", u"BLIND2-25154-2-16"),
    (u"25154-3-02", u"モータブラケット", u"3.昇降軸", u"BLIND2-25154-3-02"),
    (u"25154-4-05", u"駆動ユニットブラケット", u"4.前後軸", u"BLIND2-25154-4-05"),
    (u"25154-5-05", u"減速機フランジ", u"5.ひねり軸", u"BLIND2-25154-5-05"),
]


def analyze(path, dimexo=1.0, dimexe=2.0):
    u"""1図面の線形寸法について「補助線が輪郭線と溶けている」本数を数える。"""
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    try:
        part, _fs = subtract_frame(doc, template_path=os.path.join(
            ROOT, u"図枠", u"frame_template.dxf"))
    except Exception:
        part = list(msp)
    geo = [e for e in part
           if e.dxftype() in ("LINE", "LWPOLYLINE", "POLYLINE")
           and not dim_engine.is_centerline(e)]
    segs = dim_engine.contour_segments(geo)
    rows = []
    for d in msp.query("DIMENSION"):
        base = d.dxf.dimtype & 7
        if base not in (0, 1):
            continue
        # 実DXFの DIMSTYLE から dimexo/dimexe を読む(無ければ流儀既定)
        exo, exe = dimexo, dimexe
        try:
            st = doc.dimstyles.get(d.dxf.dimstyle)
            exo = float(st.dxf.get("dimexo", dimexo))
            exe = float(st.dxf.get("dimexe", dimexe))
        except Exception:
            pass
        p1 = (d.dxf.defpoint2.x, d.dxf.defpoint2.y)
        p2 = (d.dxf.defpoint3.x, d.dxf.defpoint3.y)
        bp = (d.dxf.defpoint.x, d.dxf.defpoint.y)
        ang = float(d.dxf.get("angle", 0.0)) if base == 0 else None
        if ang is None:
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            ang = 0.0 if abs(dx) >= abs(dy) else 90.0
        # ❗用紙倍率の正規化。**紙面上の dimexo は 927/927=100% で 1.0mm**(コーパス実測)なので、
        #   DXF単位の dimexo がそのまま「紙面mm -> DXF単位」の倍率になる。
        #   これを掛けずに固定しきい値で測ると、用紙倍率4の図面は自動的に「溶けていない」と
        #   出てしまう(測定器が尺度に汚染される。CLAUDE.md『照合ツールの罠』と同型)。
        unit = exo if exo > 0 else 1.0
        gs = dim_engine.extension_line_geometry(p1, p2, bp, ang, exo, exe)
        hits = [dim_engine.find_collinear_contours(
            g, segs, offset_tol=dim_engine.COLLINEAR_OFFSET_TOL_MM * unit,
            gap_max=dim_engine.COLLINEAR_GAP_MAX_MM * unit) for g in gs]
        n = sum(len(h) for h in hits)
        # 補助線の**描かれる長さ**(紙面mm)。長い補助線ほど「図解の線」に見える
        ext_len = [round((g["t_end"] - g["t_start"]) / unit, 3) for g in gs]
        rows.append({"handle": d.dxf.handle, "dimexo": exo, "paper_unit": unit,
                     "ext_len_paper_mm": ext_len,
                     "collinear": n,
                     "min_gap_paper_mm": (round(min(hh["gap_mm"] for h in hits
                                                    for hh in h) / unit, 3)
                                          if n else None)})
    n_dim = len(rows)
    n_bad = sum(1 for r in rows if r["collinear"])
    lens = sorted(v for r in rows for v in r["ext_len_paper_mm"])
    # 「溶けている」補助線だけの長さ(=見分けが要る場所の長さ)
    lens_bad = sorted(v for r in rows if r["collinear"] for v in r["ext_len_paper_mm"])

    def q(a, p):
        return round(a[min(len(a) - 1, int(len(a) * p))], 2) if a else None
    return {"path": os.path.relpath(path, ROOT), "n_linear_dim": n_dim,
            "n_collinear": n_bad,
            "ratio": (round(n_bad / n_dim, 4) if n_dim else None),
            "dimexo_seen": sorted({r["dimexo"] for r in rows}),
            "ext_len_paper_mm": {"n": len(lens), "p25": q(lens, 0.25),
                                 "median": q(lens, 0.5), "p75": q(lens, 0.75),
                                 "p90": q(lens, 0.90), "max": (lens[-1] if lens else None)},
            "ext_len_collinear_paper_mm": {"n": len(lens_bad), "median": q(lens_bad, 0.5),
                                           "p75": q(lens_bad, 0.75),
                                           "max": (lens_bad[-1] if lens_bad else None)},
            "rows": rows}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, u"調査",
                                                   u"extension_collinear.json"))
    args = ap.parse_args(argv[1:])
    out = {"cases": []}
    for zuban, name, axis, req in CASES:
        stem = u"%s_%s" % (zuban, name)
        human = os.path.join(HUMAN_DIR, axis, stem + u".dxf")
        gen = os.path.join(GEN_DIR, req, stem + u".dxf")
        rec = {"zuban": zuban, "name": name}
        for label, p in (("human", human), ("generated", gen)):
            if os.path.exists(p):
                try:
                    rec[label] = analyze(p)
                except Exception as e:
                    rec[label] = {"error": "%s: %s" % (type(e).__name__, e)}
            else:
                rec[label] = {"missing": p}
        out["cases"].append(rec)
        h = rec.get("human", {})
        g = rec.get("generated", {})
        print(u"%s %s\n   人間 溶け %s/%s (%s) 補助線長 中央%s/p75 %s/最大%s (溶けている分 中央%s)\n"
              u"   生成 溶け %s/%s (%s) 補助線長 中央%s/p75 %s/最大%s (溶けている分 中央%s)"
              % (zuban, name,
                 h.get("n_collinear"), h.get("n_linear_dim"), h.get("ratio"),
                 (h.get("ext_len_paper_mm") or {}).get("median"),
                 (h.get("ext_len_paper_mm") or {}).get("p75"),
                 (h.get("ext_len_paper_mm") or {}).get("max"),
                 (h.get("ext_len_collinear_paper_mm") or {}).get("median"),
                 g.get("n_collinear"), g.get("n_linear_dim"), g.get("ratio"),
                 (g.get("ext_len_paper_mm") or {}).get("median"),
                 (g.get("ext_len_paper_mm") or {}).get("p75"),
                 (g.get("ext_len_paper_mm") or {}).get("max"),
                 (g.get("ext_len_collinear_paper_mm") or {}).get("median")))
    tot = {"human": [0, 0], "generated": [0, 0]}
    alll = {"human": [], "generated": []}
    badl = {"human": [], "generated": []}
    for c in out["cases"]:
        for k in ("human", "generated"):
            r = c.get(k) or {}
            if "n_linear_dim" in r:
                tot[k][0] += r["n_collinear"]
                tot[k][1] += r["n_linear_dim"]
                for row in r["rows"]:
                    alll[k].extend(row["ext_len_paper_mm"])
                    if row["collinear"]:
                        badl[k].extend(row["ext_len_paper_mm"])

    def q(a, p):
        a = sorted(a)
        return round(a[min(len(a) - 1, int(len(a) * p))], 2) if a else None
    out["total"] = {k: {"collinear": v[0], "linear_dims": v[1],
                        "ratio": (round(v[0] / v[1], 4) if v[1] else None),
                        "ext_len_paper_mm": {"n": len(alll[k]), "p25": q(alll[k], .25),
                                             "median": q(alll[k], .5), "p75": q(alll[k], .75),
                                             "p90": q(alll[k], .90),
                                             "max": q(alll[k], 1.0)},
                        "ext_len_collinear_paper_mm": {
                            "n": len(badl[k]), "median": q(badl[k], .5),
                            "p75": q(badl[k], .75), "p90": q(badl[k], .90),
                            "max": q(badl[k], 1.0)}}
                    for k, v in tot.items()}
    print(u"合計: 人間 %s" % json.dumps(out["total"]["human"], ensure_ascii=False))
    print(u"合計: 生成 %s" % json.dumps(out["total"]["generated"], ensure_ascii=False))
    with io.open(args.json, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=1))
    print(u"wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
