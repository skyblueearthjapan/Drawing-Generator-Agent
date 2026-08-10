# -*- coding: utf-8 -*-
u"""生成図面の**様式指標**(kind×side分布 / 寸法線の本数 / 直列連記 / 密度)を実DXFから測る。

❗`調査/style_pattern_analysis.py` は人間図面にも使える汎用性のためにビューを空間クラスタで
推定しており、**隣り合うビューを1つに融合してしまう**(レポート§0.3の既知の限界)。
その結果「正面図の左に置いた径」と「右側面図側に置いた径」が同じクラスタの左右で潰れ、
作法6(径の左右振り分け)の効果が数値に出ない。

本スクリプトは生成図面専用で、**エンジンが実際に使ったビュー領域**
(`dim_engine.build_view_transforms` + 生成レポートの `view_bbox`)を正として、
DIMENSION の寸法線位置(defpoint)から side を決める。したがってクラスタ推定の誤差が無い。
併せて **計画の placement.side と実DXFの位置が一致しているか** も検算する
(一致しなければ「計画どおりに配置されていない」ことになるので必ず報告する)。

実行:
    python 調査/measure_style_placement.py [--json 調査/style/placement_after.json]
"""
import argparse
import glob
import io
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import ezdxf                                    # noqa: E402
from engine import dim_engine                   # noqa: E402

CASES = [
    (u"1-27", u"BLIND2-25154-1-27"),
    (u"2-16", u"BLIND2-25154-2-16"),
    (u"3-02", u"BLIND2-25154-3-02"),
    (u"4-05", u"BLIND2-25154-4-05"),
    (u"5-05", u"BLIND2-25154-5-05"),
]
SIDES = ("above", "below", "left", "right")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def measure(request_id):
    rd = os.path.join(ROOT, u"data", u"依頼箱", request_id)
    plan_path = os.path.join(rd, "plan.json")
    res = glob.glob(os.path.join(rd, u"生成", u"*_result.json"))
    if not res:
        return None
    plan = _read(plan_path)
    result = _read(res[0])
    view_bbox = result["dim"]["view_bbox"]
    dxf = glob.glob(os.path.join(ROOT, u"data", u"納品箱", request_id, u"*.dxf"))[0]
    doc = ezdxf.readfile(dxf)
    order = [d["id"] for d in plan["dimensions"]]
    by_id = {d["id"]: d for d in plan["dimensions"]}
    scale = float(plan.get("source", {}).get("scale", 1.0))

    rows, mismatch = [], []
    lines = {}          # (view, side, 寸法線座標) -> [id]
    for e in doc.modelspace():
        if e.dxftype() != "DIMENSION":
            continue
        m = re.sub(r"\D", "", str(e.dxf.dimstyle))
        i = int(m) - 1 if m else -1
        if not (0 <= i < len(order)):
            continue
        did = order[i]
        item = by_id[did]
        kind = item["kind"]
        base = (e.dxf.dimtype & 7)
        view = item["view"]
        bb = view_bbox[view]
        side = None
        coord = None
        if base == 0:                       # 線形(rotated)
            ang = float(e.dxf.get("angle", 0.0)) % 180.0
            p = e.dxf.defpoint
            if abs(ang) < 1e-6:             # 水平寸法 -> 寸法線は上か下
                coord = p.y
                side = "above" if p.y > (bb[1] + bb[3]) / 2.0 else "below"
            elif abs(ang - 90.0) < 1e-6:    # 垂直寸法 -> 左か右
                coord = p.x
                side = "right" if p.x > (bb[0] + bb[2]) / 2.0 else "left"
        rows.append({"id": did, "kind": kind, "view": view, "dimtype_base": base,
                     "side_measured": side, "side_planned": (item.get("placement") or {}).get("side"),
                     "line_coord": None if coord is None else round(coord, 4),
                     "chain_group": (item.get("placement") or {}).get("chain_group"),
                     "purpose": item.get("purpose")})
        if side is not None:
            planned = (item.get("placement") or {}).get("side")
            if planned is not None and planned != side:
                mismatch.append({"id": did, "planned": planned, "measured": side})
            lines.setdefault((view, side, round(coord, 3)), []).append(did)

    # kind の集計は「径(diameter*) / 半径 / 角度 / 長さ」で人間コーパス統計と同じ粒度にする
    def kind_class(r):
        if r["dimtype_base"] == 3 or r["kind"].startswith("diameter"):
            return "diameter"
        if r["dimtype_base"] == 4 or r["kind"] == "radius":
            return "radius"
        if r["dimtype_base"] == 2 or r["kind"] == "angle":
            return "angular"
        return "length"

    dist = {}
    for r in rows:
        k = kind_class(r)
        d = dist.setdefault(k, {s: 0 for s in SIDES})
        d["_inside"] = d.get("_inside", 0)
        if r["side_measured"] is None:
            d["_inside"] += 1
        else:
            d[r["side_measured"]] += 1

    per_view = {}
    for r in rows:
        per_view[r["view"]] = per_view.get(r["view"], 0) + 1
    # 「寸法線の本数」= 同一ビュー・同一辺・同一座標を1本と数える
    n_lines = len(lines)
    chains = [{"key": "%s/%s@%s" % k, "ids": v} for k, v in sorted(lines.items()) if len(v) > 1]
    # 作法7の検算: 同じ「ビュー×辺」に径寸法と長さ寸法が同居していないか
    band = {}
    for r in rows:
        if r["side_measured"] is None:
            continue
        k = kind_class(r)
        band.setdefault((r["view"], r["side_measured"]), set()).add(
            "diameter" if k in ("diameter", "radius") else k)
    mixed_bands = ["%s/%s" % k for k, v in sorted(band.items())
                   if "diameter" in v and "length" in v]

    return {"request": request_id, "dxf": os.path.relpath(dxf, ROOT).replace(os.sep, "/"),
            "mixed_bands": mixed_bands,
            "n_dims": len(rows), "per_view_counts": per_view,
            "n_dimension_lines": n_lines, "shared_lines": chains,
            "kind_side": dist, "side_mismatch": mismatch, "rows": rows,
            "scale": scale}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, u"調査", u"style",
                                                   u"placement_after.json"))
    args = ap.parse_args(argv[1:])
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    out, agg = [], {}
    for tag, rid in CASES:
        r = measure(rid)
        if r is None:
            print(u"skip %s" % rid)
            continue
        r["tag"] = tag
        out.append(r)
        for k, d in r["kind_side"].items():
            a = agg.setdefault(k, {s: 0 for s in SIDES})
            a["_inside"] = a.get("_inside", 0)
            for s in list(SIDES) + ["_inside"]:
                a[s] = a.get(s, 0) + d.get(s, 0)

    print(u"=== 生成5枚の様式指標(エンジンのビュー領域を正として実DXFから測定)===")
    for r in out:
        ks = " / ".join(
            u"%s:%s" % (k, "+".join(u"%s%d" % (s, v) for s, v in sorted(d.items()) if v))
            for k, d in sorted(r["kind_side"].items()))
        print(u"  %-6s 寸法%2d本 / 寸法線%2d本 / 1本に2本以上=%d組 / ビュー毎=%s"
              % (r["tag"], r["n_dims"], r["n_dimension_lines"], len(r["shared_lines"]),
                 r["per_view_counts"]))
        print(u"           %s" % ks)
        if r["side_mismatch"]:
            print(u"           ❗計画と実配置の不一致: %s" % r["side_mismatch"])
    print(u"\n-- 合計(kind x side。_inside=ビュー内に描かれ辺を持たない寸法) --")
    for k, d in sorted(agg.items()):
        tot = sum(v for s, v in d.items())
        sided = sum(d[s] for s in SIDES)
        print(u"  %-9s N=%2d  " % (k, tot)
              + " ".join(u"%s %d(%s)" % (s, d[s],
                                         (u"%d%%" % round(100.0 * d[s] / sided)) if sided else "-")
                         for s in SIDES)
              + (u"  ビュー内 %d" % d.get("_inside", 0)))
    mism = sum(len(r["side_mismatch"]) for r in out)
    mixed = [b for r in out for b in r["mixed_bands"]]
    print(u"\n作法7の検算: 径寸法と長さ寸法が同居する『ビュー×辺』= %d件 %s"
          % (len(mixed), mixed))
    print(u"作法8の検算: 1本の寸法線に2本以上を連記した組 = %d組(%d本の寸法線を節約)"
          % (sum(len(r["shared_lines"]) for r in out),
             sum(len(c["ids"]) - 1 for r in out for c in r["shared_lines"])))
    print(u"計画 placement.side と実配置の不一致: %d件" % mism)
    with io.open(args.json, "w", encoding="utf-8") as f:
        f.write(json.dumps({"cases": out, "aggregate": agg}, ensure_ascii=False, indent=2))
    print(u"saved %s" % args.json)
    return 1 if mism else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
