# -*- coding: utf-8 -*-
u"""穴注記の「呼び値」翻訳層(様式第3弾・層1「値の語彙」)。

背景(調査/図面品質メモ_指針レビュー_2026-08-11.md §8・§15・ユーザー裁定):
- 生成図面の穴注記は **モデル実測値そのまま**(`６－%%c７．０４　９０°皿もみ%%c１３．４４`)で、
  φ7.04 のドリルは存在しない。人間は呼び値(`６－%%c８`)で書く。
- ただしユーザー裁定は「**事実に基づかないものを表現するのは求めすぎ**」。
  よって **丸め先が自明な値だけを呼び値へ翻訳し、自明でない値は丸めずに
  『呼び値未確定』として解釈レポート/質問票へ誘導する**。

このモジュールの守備範囲(意図的に狭くしてある):
- **注記の径値だけ**を翻訳する(`hole_notes[].spec.drill` / `.counterbore.dia`)。
  **寸法値(dimensions[].value_expected)は一切触らない** —— それはゲート①の正であり、
  モデル実測でなければならない。ゲート②の検算も翻訳前の実測で行われる
  (`anchor_check.diameter` は実測のまま計画に残す運用)。
- 用途を「呼び」に限定するため、**φ100を超える径は翻訳しない**(呼びで語らない加工径。
  例: 減速機フランジのφ219.1 = インロー/インチ系疑い → 質問票行き。§15)。

呼び値表(`NOMINAL_TABLE`)の作り:
- φ1.0〜13.0 : 0.1刻み(JIS B 0101 ストレートシャンクドリルの常用呼び)
- φ13.5〜50.0: 0.5刻み(大径ドリル・リーマ・ザグリの常用呼び)
- φ51〜100   : 1.0刻み(整数呼び)

許容窓は **±0.05mm(窓幅0.1mm)**。この幅は
- φ7.04 → φ7.0(差0.04)を通し、
- φ13.44(М6皿もみの幾何値)は最寄りφ13.5との差0.06で**通さない**(=呼び値未確定)
という、レビューで実際に問題になった2値を分離する最小の窓として選んだ。
STEPジオメトリの取り込み誤差は0.009mm以下(CLAUDE.md)なので実測側のブレは無視できる。

❗**この翻訳の正しさを保証するのは表ではなく検算**である。
`dim_engine.apply_plan` は翻訳結果を受け取ったあと、
**表にもこの関数にも依存せず**「|呼び値 − 実測値| ≦ 許容窓」を自前で確かめる。
表を偽装しても(φ7.04→φ8 のような嘘の丸め)そこで落ちる。
"""
from __future__ import division

import copy


#: 呼び値の許容窓(片側)。窓幅は 2×0.05 = 0.1mm
NOMINAL_TOL_MM = 0.05

#: 呼び値で語る径の上限。これを超える径は「呼び」ではなく加工径なので翻訳しない
NOMINAL_DOMAIN_MAX_MM = 100.0


def _grid(lo, hi, step):
    n0 = int(round(lo / step))
    n1 = int(round(hi / step))
    return [round(i * step, 3) for i in range(n0, n1 + 1)]


#: JIS常用の呼び径表(mm)。**中身を書き換えても検算(dim_engine側)は騙せない**
NOMINAL_TABLE = tuple(sorted(set(
    _grid(1.0, 13.0, 0.1) + _grid(13.5, 50.0, 0.5) + _grid(51.0, 100.0, 1.0))))


def nominal_diameter(measured, tol=NOMINAL_TOL_MM, table=None,
                     domain_max=NOMINAL_DOMAIN_MAX_MM):
    u"""モデル実測径 -> 呼び径。丸め先が自明でなければ **丸めずに未確定を返す**。

    Returns: {
        "measured": float, "nominal": float|None, "resolved": bool,
        "delta_mm": float|None, "nearest": float|None, "reason": unicode
    }
    """
    tbl = NOMINAL_TABLE if table is None else tuple(sorted(float(t) for t in table))
    try:
        v = float(measured)
    except (TypeError, ValueError):
        return {"measured": measured, "nominal": None, "resolved": False,
                "delta_mm": None, "nearest": None, "reason": u"数値でない"}
    if v <= 0.0:
        return {"measured": v, "nominal": None, "resolved": False,
                "delta_mm": None, "nearest": None, "reason": u"径が正でない"}
    if v > domain_max:
        return {"measured": v, "nominal": None, "resolved": False,
                "delta_mm": None, "nearest": None,
                "reason": (u"呼び値で語る範囲外(φ%.4g > φ%.4g)。加工径として"
                           u"実測のまま残し、意図を質問票で確認する" % (v, domain_max))}
    if not tbl:
        return {"measured": v, "nominal": None, "resolved": False,
                "delta_mm": None, "nearest": None, "reason": u"呼び値表が空"}
    nearest = min(tbl, key=lambda t: abs(t - v))
    d = abs(nearest - v)
    if d <= tol + 1e-9:
        return {"measured": v, "nominal": nearest, "resolved": True,
                "delta_mm": round(d, 4), "nearest": nearest,
                "reason": (u"実測が呼び値と一致" if d <= 1e-9
                           else u"呼び値へ丸め(差%.3fmm ≦ 許容窓%.3fmm)" % (d, tol))}
    return {"measured": v, "nominal": None, "resolved": False,
            "delta_mm": round(d, 4), "nearest": nearest,
            "reason": (u"許容窓±%.3fmm内に呼び値が無い(最寄りφ%.4g・差%.3fmm)。"
                       u"丸め先が自明でないため実測のまま残す" % (tol, nearest, d))}


#: 翻訳対象の spec フィールド(注記の**径値だけ**)。深さ・個数・ねじ呼びは対象外
SPEC_DIAMETER_FIELDS = (("drill",), ("counterbore", "dia"), ("countersink", "dia"))


def translate_hole_spec(spec, tol=NOMINAL_TOL_MM, table=None, enabled=True):
    u"""穴注記 spec の径値を呼び値へ翻訳する。

    Returns: (new_spec, records)
      records: [{"field": "drill", ...nominal_diameter()の戻り...}, ...]
      未確定(resolved=False)のフィールドは **値を変更しない**。
    """
    out = copy.deepcopy(spec)
    records = []
    for path in SPEC_DIAMETER_FIELDS:
        node = out
        ok = True
        for k in path[:-1]:
            node = node.get(k) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                ok = False
                break
        if not ok or not isinstance(node, dict):
            continue
        leaf = path[-1]
        if node.get(leaf) is None:
            continue
        rec = nominal_diameter(node[leaf], tol=tol, table=table)
        rec["field"] = ".".join(path)
        records.append(rec)
        if enabled and rec["resolved"]:
            node[leaf] = rec["nominal"]
    return out, records


def is_nominal_like(value, tol=0.005):
    u"""「呼び値らしい値」か(整数 or 0.5刻み)。**寸法値の点検専用**(丸めには使わない)。

    径寸法(dimensions)の値は絶対に書き換えないが、φ219.1・φ119.6 のように
    呼び値でない径は「その径の意図は?」を質問票に回す材料になる(§15・§26)。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return abs(v * 2.0 - round(v * 2.0)) <= tol * 2.0
