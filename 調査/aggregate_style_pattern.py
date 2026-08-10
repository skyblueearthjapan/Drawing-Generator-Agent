# -*- coding: utf-8 -*-
"""style_pattern_raw.json を集計し、観点1(配置分散)・5(密度)・6(直列/並列)の
統計をテキストで出力する。"""
import json
import os
from collections import Counter, defaultdict

BASE = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(BASE, "調査", "style_pattern_raw.json")

KIND_GROUP = {
    "diameter_native": "diameter",
    "diameter_linear": "diameter",
    "radius": "radius",
    "radius_linear": "radius",
    "angular": "angular",
    "length": "length",
}


def side_dist_for_group(files):
    """kind_group -> side -> count"""
    agg = defaultdict(Counter)
    for f in files:
        for k, v in f["kind_side_counts"].items():
            kind, side = k.split("|")
            g = KIND_GROUP.get(kind, kind)
            agg[g][side] += v
    return agg


def pct_table(agg):
    lines = []
    for g in ("diameter", "radius", "length", "angular"):
        c = agg.get(g)
        if not c:
            continue
        total = sum(c.values())
        parts = ", ".join(f"{s}={c.get(s,0)}({c.get(s,0)/total*100:.0f}%)"
                           for s in ("top", "bottom", "left", "right"))
        lines.append(f"  {g}: total={total}  {parts}")
    return "\n".join(lines)


def density_stats(files):
    counts = []
    for f in files:
        for c in f["view_dim_counts"]:
            if c > 0:
                counts.append(c)
    if not counts:
        return "  (no views)"
    counts.sort()
    n = len(counts)
    med = counts[n // 2] if n % 2 else (counts[n // 2 - 1] + counts[n // 2]) / 2
    return (f"  n_views={n}  min={min(counts)} median={med} mean={sum(counts)/n:.1f} "
            f"max={max(counts)}  all={counts}")


def chaining_stats(files):
    tot = {"horizontal": Counter(), "vertical": Counter()}
    for f in files:
        for view_ch in f["chaining_per_view"]:
            for d in ("horizontal", "vertical"):
                for k in ("chain_dims", "chain_groups", "baseline_dims", "baseline_groups",
                          "isolated", "total"):
                    tot[d][k] += view_ch[d][k]
    lines = []
    for d in ("horizontal", "vertical"):
        t = tot[d]
        total = t["total"]
        if total == 0:
            lines.append(f"  {d}: total=0")
            continue
        lines.append(f"  {d}: total={total}  chain={t['chain_dims']}({t['chain_dims']/total*100:.0f}%,"
                      f"{t['chain_groups']}groups)  baseline={t['baseline_dims']}({t['baseline_dims']/total*100:.0f}%,"
                      f"{t['baseline_groups']}groups)  isolated={t['isolated']}({t['isolated']/total*100:.0f}%)")
    return "\n".join(lines)


#: frame_matched が低い(非バケットA・大判/複数子部品併記)ファイルはビュークラスタリングが
#: 図枠残骸で汚染されるため、コーパス統計(観点1/5/6)からは除外する。
#: (1-27/1-03/2-06/3-02/4-05/6-02/5-05の7枚。詳細は調査/drawing_style_analysis.md §0)
NON_BUCKET_A_TAGS = {"1-27", "1-03", "2-06", "3-02", "4-05", "6-02", "5-05"}


def main():
    with open(RAW, encoding="utf-8") as f:
        d = json.load(f)

    out = []
    for group in ("human", "generated"):
        files = d[group]
        if group == "human":
            excluded = [f for f in files if f["tag"] in NON_BUCKET_A_TAGS]
            files = [f for f in files if f["tag"] not in NON_BUCKET_A_TAGS]
            out.append(f"(除外: 非バケットA {len(excluded)}枚 = {[f['tag'] for f in excluded]})")
        out.append(f"===== {group} (n_files={len(files)}) =====")
        out.append("-- 観点1: kind x side 分布 --")
        agg = side_dist_for_group(files)
        out.append(pct_table(agg))
        out.append("-- 観点5: view毎の寸法数 --")
        out.append(density_stats(files))
        out.append("-- 観点6: 直列(chain) / 並列(baseline) / 孤立 --")
        out.append(chaining_stats(files))
        out.append("")

    # per-file detail for generated (small n, useful for report)
    out.append("===== generated 個別ファイル =====")
    for f in d["generated"]:
        out.append(f"-- {f['tag']} ({f['path']}) views={f['n_views']} dims={f['n_dims']} --")
        agg = side_dist_for_group([f])
        out.append(pct_table(agg))

    out.append("")
    out.append("===== human 個別ファイル(BLIND2対応分のみ) =====")
    for f in d["human"]:
        if f["tag"] in ("1-27", "2-16", "3-02", "4-05", "5-05"):
            out.append(f"-- {f['tag']} ({f['path']}) views={f['n_views']} dims={f['n_dims']} --")
            agg = side_dist_for_group([f])
            out.append(pct_table(agg))

    text = "\n".join(out)
    print(text)
    with open(os.path.join(BASE, "調査", "style_pattern_summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main()
