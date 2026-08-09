# -*- coding: utf-8 -*-
"""
style_corpus_raw.json (80ファイル生データ) を集計し、
- 調査/style_corpus_stats.json (report生成・dimstyle_spec生成の入力となる統計サマリ)
- 調査/hole_note_patterns.json (穴注記パターン+頻度+実例)
を出力する。
"""
import sys
import io
import os
import json
import statistics
from collections import Counter, defaultdict

BASE = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(BASE, "調査", "style_corpus_raw.json")
OUT_STATS = os.path.join(BASE, "調査", "style_corpus_stats.json")
OUT_HOLE = os.path.join(BASE, "調査", "hole_note_patterns.json")

DIMTYPE_NAME = {0: "linear/rotated(長さ)", 1: "aligned(平行)", 2: "angular(角度)",
                3: "diameter(直径)", 4: "radius(半径)", 5: "angular3p", 6: "ordinate(座標)"}


def mode_or_none(counter):
    if not counter:
        return None
    return counter.most_common(1)[0]


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    with open(RAW, encoding="utf-8") as f:
        raw = json.load(f)
    results = raw["results"]
    n_files = len(results)

    # ---------- 1. DIMENSION 詳細 ----------
    dimtype_counter = Counter()
    text_override_count = 0
    text_blank_count = 0
    ds_param_values = defaultdict(list)  # param -> list of values (numeric only)
    ds_param_counter = defaultdict(Counter)  # param -> Counter(value) for categorical
    dimpost_counter = Counter()
    xdata_count = 0
    dims_per_file = []
    dimtype_by_dimpost = Counter()

    for r in results:
        dims_per_file.append(len(r["dimensions"]))
        for dm in r["dimensions"]:
            dimtype_counter[dm["dimtype_base"]] += 1
            if dm["text_override"]:
                text_override_count += 1
            else:
                text_blank_count += 1
            if dm["has_xdata"]:
                xdata_count += 1
            dp = dm["ds_params"] or {}
            dpost = dp.get("dimpost", "")
            dimpost_counter[dpost] += 1
            for k in ("dimtxt", "dimasz", "dimexo", "dimexe", "dimgap", "dimdec",
                      "dimtad", "dimclrd", "dimclre", "dimclrt", "dimdli", "dimscale",
                      "dimlfac", "dimfit", "dimupt", "dimsah", "dimjust", "dimcen"):
                if k in dp and dp[k] is not None:
                    v = dp[k]
                    ds_param_values[k].append(v)
            for k in ("dimblk1", "dimblk2", "dimtxsty"):
                if k in dp and dp[k] is not None:
                    ds_param_counter[k][dp[k]] += 1

    def numeric_stats(vals):
        if not vals:
            return None
        rounded = [round(v, 4) for v in vals]
        cnt = Counter(rounded)
        return {
            "n": len(vals),
            "min": min(vals), "max": max(vals),
            "mean": sum(vals) / len(vals),
            "median": statistics.median(vals),
            "mode": cnt.most_common(1)[0][0],
            "mode_freq": cnt.most_common(1)[0][1],
            "mode_ratio": cnt.most_common(1)[0][1] / len(vals),
            "top5": cnt.most_common(5),
        }

    dim_param_stats = {k: numeric_stats(v) for k, v in ds_param_values.items()}
    dim_categorical_stats = {k: c.most_common(10) for k, c in ds_param_counter.items()}

    # ---------- 2. 穴・タップ注記 ----------
    hole_hit_counter = Counter()
    hole_examples = defaultdict(list)
    for r in results:
        for name, m, src in r["hole_note_hits"]:
            hole_hit_counter[name] += 1
            if len(hole_examples[name]) < 8 and m not in hole_examples[name]:
                hole_examples[name].append(m)

    # ---------- 3. 公差 ----------
    tol_hit_counter = Counter()
    tol_examples = defaultdict(list)
    for r in results:
        for name, m, src in r["tolerance_hits"]:
            tol_hit_counter[name] += 1
            if len(tol_examples[name]) < 10 and m not in tol_examples[name]:
                tol_examples[name].append(m)

    # ---------- 4. AGM(仕上げ/ねじ記号)ブロック ----------
    agm_counter = Counter()
    files_with_agm = defaultdict(set)
    for r in results:
        for b in r["agm_blocks"]:
            key = b["name"]
            agm_counter[key] += 1
            files_with_agm[key].add(r["path"])
    agm_summary = {k: {"count": v, "n_files": len(files_with_agm[k])} for k, v in agm_counter.items()}

    # ---------- 5. ビュー構成 ----------
    n_clusters_list = [r["n_view_clusters"] for r in results]
    n_islands_big45_list = [r.get("n_view_islands_big_gap45", 0) for r in results]
    centerline_lines = [r["centerline_lines"] for r in results]
    centerline_circles = [r["centerline_circles"] for r in results]
    n_clusters_counter = Counter(n_clusters_list)
    n_islands_big45_counter = Counter(n_islands_big45_list)

    # ---------- 6. 配置ルール ----------
    dline_dists = []
    for r in results:
        for dm in r["dimensions"]:
            d_ = dm.get("dline_offset_from_geom")
            if d_ is not None and d_ >= 0:
                dline_dists.append(d_)
    leader_angles = []
    leader_lengths = []
    for r in results:
        for ld in r["leaders"]:
            if ld["angle_deg"] is not None:
                leader_angles.append(ld["angle_deg"])
            if ld["start"] and ld["end"]:
                dx = ld["end"][0] - ld["start"][0]
                dy = ld["end"][1] - ld["start"][1]
                leader_lengths.append((dx**2 + dy**2) ** 0.5)

    # ---------- 7. 共通注記 ----------
    note_norm_counter = Counter()
    note_examples = {}
    for r in results:
        for nc in r["notes_candidates"]:
            t = nc["text"].strip()
            t_norm = "".join(t.split())  # 空白除去で軽く正規化
            note_norm_counter[t_norm] += 1
            if t_norm not in note_examples:
                note_examples[t_norm] = t

    stats = {
        "n_files": n_files,
        "dims": {
            "total": sum(dims_per_file),
            "per_file": numeric_stats([float(x) for x in dims_per_file]),
            "dimtype_base_counts": {DIMTYPE_NAME.get(k, str(k)): v for k, v in dimtype_counter.most_common()},
            "text_override_count": text_override_count,
            "text_blank_count": text_blank_count,
            "text_override_ratio": text_override_count / (text_override_count + text_blank_count),
            "xdata_override_count": xdata_count,
            "dimpost_top": dimpost_counter.most_common(20),
            "ds_param_stats": dim_param_stats,
            "ds_categorical_stats": dim_categorical_stats,
        },
        "hole_notes": {
            "hit_counts": hole_hit_counter.most_common(),
            "examples": {k: v for k, v in hole_examples.items()},
        },
        "tolerance": {
            "hit_counts": tol_hit_counter.most_common(),
            "examples": {k: v for k, v in tol_examples.items()},
            "n_files_with_any_tolerance": len({r["path"] for r in results if r["tolerance_hits"]}),
        },
        "agm_blocks": agm_summary,
        "views": {
            "note": "n_clusters(gap=18mm)は幾何の空間連結成分数(=真のビュー数より過大: 孤立した穴等も別クラスタ化される)。"
                    "n_view_islands_big_gap45(gap=45mm・4点以上の島のみ)の方がビュー数の近似として妥当性が高いことを"
                    "10ファイルでの閾値比較(調査/_tmp_view_gap_test.py、削除済み・本ファイルに知見のみ残す)で確認した。"
                    "ただし機械的クラスタリングは『同一ビュー内で離れた穴』と『別ビュー』を完全には区別できない限界がある。",
            "n_clusters_gap18_per_file": numeric_stats([float(x) for x in n_clusters_list]),
            "n_clusters_gap18_distribution": sorted(n_clusters_counter.items()),
            "n_view_islands_big_gap45_per_file": numeric_stats([float(x) for x in n_islands_big45_list]),
            "n_view_islands_big_gap45_distribution": sorted(n_islands_big45_counter.items()),
            "centerline_lines_per_file": numeric_stats([float(x) for x in centerline_lines]),
            "centerline_circles_per_file": numeric_stats([float(x) for x in centerline_circles]),
            "n_files_with_centerline_circle": sum(1 for x in centerline_circles if x > 0),
        },
        "placement": {
            "dline_offset_from_geom_mm": numeric_stats(dline_dists),
            "leader_angle_deg": numeric_stats(leader_angles),
            "leader_length": numeric_stats(leader_lengths),
        },
        "notes": {
            "n_candidates_total": sum(len(r["notes_candidates"]) for r in results),
            "recurring_normalized_top30": [
                {"text": note_examples[t], "count": c} for t, c in note_norm_counter.most_common(30)
            ],
        },
    }

    with open(OUT_STATS, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    print("wrote", OUT_STATS)

    # hole_note_patterns.json (専用フォーマット: パターン+正規表現+頻度+実例)
    PATTERN_REGEX_SRC = {
        "count_phi": r"\d+[-－]\s*(?:%%c|φ|Φ)\s*\d+(?:\.\d+)?",
        "phi_only": r"(?:%%c|φ|Φ)\s*\d+(?:\.\d+)?",
        "metric_thread": r"[MＭmｍ]\d+(?:[×xX]\d+(?:\.\d+)?)?",
        "count_metric_thread": r"\d+[-－]\s*[MＭmｍ]\d+",
        "pcd": r"PCD\s*\d+(?:\.\d+)?",
        "through_zaguri": r"通しザグリ",
        "fukazaguri": r"深ザグリ",
        "zaguri": r"ザグリ",
        "kiri": r"キリ",
        "through_hole": r"通し",
        "depth": r"深さ\s*\d+(?:\.\d+)?",
        "od": r"OD\s*\d+(?:\.\d+)?",
        "pilot_hole": r"下穴",
        "tapped_depth_T": r"\d+[TＴ]\b",
    }
    hole_json = {
        "n_files_analyzed": n_files,
        "note": "LEADER+MTEXT/DIMENSIONのオーバーライドテキストから正規表現で機械抽出した穴・タップ注記の書式パターン。頻度は全80枚合計の出現回数(1テキスト内の複数マッチも各カウント)。",
        "patterns": [
            {
                "name": name,
                "regex": PATTERN_REGEX_SRC.get(name, ""),
                "count": hole_hit_counter.get(name, 0),
                "examples": hole_examples.get(name, []),
            }
            for name in sorted(hole_hit_counter, key=lambda k: -hole_hit_counter[k])
        ],
    }
    with open(OUT_HOLE, "w", encoding="utf-8") as f:
        json.dump(hole_json, f, ensure_ascii=False, indent=1)
    print("wrote", OUT_HOLE)


if __name__ == "__main__":
    main()
