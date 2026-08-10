# -*- coding: utf-8 -*-
"""
図枠テンプレート(図枠/frame_template.dxf)の全INSERTについて、展開後ジオメトリ(virtual_entities)の
「ページ上の位置(bbox)」をキーに、バケットA実ファイル(80枚)の対応する位置のINSERT内容と
突き合わせ、多数派の中身と一致するかを判定する。

背景:
  - frame_template.dxf の5個のINSERT(BLOCK002/004/005/006/007)は、いずれも挿入点(0,0)・
    スケール1・回転0で、位置差分は「ブロック定義側のローカル座標」にエンコードされている。
    したがって INSERT の挿入点/スケール/回転だけでは複数ブロックを区別できない
    (全て同一の変換パラメータを持つため)。
  - 一方、ブロック名(BLOCK00x)はファイルローカルな自動採番で、同名でも中身が別物になりうる
    (GMM0xxスタイル名と同じ罠)。名前でも区別できない。
  - そこで本監査では、virtual_entities() 展開後のワールド座標bboxを「ページ上のどこに
    描画される要素か」を表す位置キーとして使い、バケットA全体で同じ位置に現れる内容を
    多数決して、テンプレートの現状と比較する。

出力: 調査/frame_block_audit.md (判定表), 調査/frame_block_audit.json(機械可読), 標準出力にサマリ
"""
import sys
import io
import json
from collections import Counter, defaultdict

import ezdxf

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

TOL = 6
BBOX_TOL = 1  # bboxキーの丸め桁(位置バケット判定用。0.1mm単位）


def r(v):
    return round(float(v), TOL)


def pt(p):
    return (r(p[0]), r(p[1]))


def geom_sig(e):
    """スタイル解決を伴わない、幾何+内容のみの比較用シグネチャ。レイヤは無視(既知の罠のため)。"""
    t = e.dxftype()
    try:
        color = e.dxf.color
    except Exception:
        color = 256
    try:
        lt = e.dxf.linetype
    except Exception:
        lt = "BYLAYER"

    if t == "LINE":
        return (t, color, lt, pt(e.dxf.start), pt(e.dxf.end))
    elif t == "LWPOLYLINE":
        pts = tuple((r(p[0]), r(p[1])) for p in e.get_points())
        return (t, color, lt, e.closed, pts)
    elif t == "CIRCLE":
        return (t, color, lt, pt(e.dxf.center), r(e.dxf.radius))
    elif t == "ARC":
        return (t, color, lt, pt(e.dxf.center), r(e.dxf.radius),
                r(e.dxf.start_angle), r(e.dxf.end_angle))
    elif t == "POINT":
        return (t, color, lt, pt(e.dxf.location))
    elif t == "TEXT":
        h = r(e.dxf.height) if e.dxf.hasattr("height") else None
        return (t, color, pt(e.dxf.insert), e.dxf.text, h, r(getattr(e.dxf, "rotation", 0.0)))
    elif t == "MTEXT":
        h = r(e.dxf.char_height) if e.dxf.hasattr("char_height") else None
        return (t, color, pt(e.dxf.insert), e.text, h, r(getattr(e.dxf, "rotation", 0.0)))
    elif t == "INSERT":
        return (t, color, e.dxf.name, pt(e.dxf.insert),
                r(getattr(e.dxf, "xscale", 1.0)), r(getattr(e.dxf, "yscale", 1.0)),
                r(getattr(e.dxf, "rotation", 0.0)))
    return (t,)


def bbox_of(entities):
    xs, ys = [], []
    for e in entities:
        t = e.dxftype()
        try:
            if t == "LINE":
                xs += [e.dxf.start.x, e.dxf.end.x]
                ys += [e.dxf.start.y, e.dxf.end.y]
            elif t in ("ARC", "CIRCLE"):
                c = e.dxf.center
                rad = e.dxf.radius
                xs += [c.x - rad, c.x + rad]
                ys += [c.y - rad, c.y + rad]
            elif t == "LWPOLYLINE":
                for p in e.get_points():
                    xs.append(p[0])
                    ys.append(p[1])
            elif t in ("TEXT", "MTEXT"):
                xs.append(e.dxf.insert.x)
                ys.append(e.dxf.insert.y)
            elif t == "POINT":
                xs.append(e.dxf.location.x)
                ys.append(e.dxf.location.y)
        except Exception:
            pass
    if not xs:
        return None
    return (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))


def bbox_key(bbox):
    """位置バケットキー: bbox min角を丸めたもの(サイズも含めて識別)。"""
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    return (round(x0, BBOX_TOL), round(y0, BBOX_TOL), round(x1 - x0, BBOX_TOL), round(y1 - y0, BBOX_TOL))


def content_fingerprint(entities):
    sigs = []
    for e in entities:
        s = geom_sig(e)
        if s is not None:
            sigs.append(s)
    return frozenset(sigs)


def load_bucket_a_paths():
    d = json.load(open("調査/bucket_A_files.json", encoding="utf-8"))
    paths = []
    for cat, files in d.items():
        for f in files:
            paths.append(f)
    return paths


def identity_transform_inserts(msp):
    for e in msp:
        if e.dxftype() != "INSERT":
            continue
        ins = e.dxf.insert
        if (round(ins.x, TOL), round(ins.y, TOL)) != (0.0, 0.0):
            continue
        if round(getattr(e.dxf, "xscale", 1.0), TOL) != 1.0:
            continue
        if round(getattr(e.dxf, "yscale", 1.0), TOL) != 1.0:
            continue
        if round(getattr(e.dxf, "rotation", 0.0), TOL) != 0.0:
            continue
        yield e


def main():
    template_path = "図枠/frame_template.dxf"
    doc_t = ezdxf.readfile(template_path)
    msp_t = doc_t.modelspace()

    template_blocks = []  # list of dict: name, entity, vents, bbox, key, fp
    for e in msp_t:
        if e.dxftype() == "INSERT":
            vents = list(e.virtual_entities())
            bbox = bbox_of(vents)
            template_blocks.append({
                "name": e.dxf.name,
                "n_ents": len(vents),
                "bbox": bbox,
                "key": bbox_key(bbox),
                "fp": content_fingerprint(vents),
            })

    print(f"テンプレートINSERT数: {len(template_blocks)}")
    for tb in template_blocks:
        print(f"  block={tb['name']} n_ents={tb['n_ents']} bbox={tb['bbox']} key={tb['key']}")

    paths = load_bucket_a_paths()
    print(f"\nバケットA実ファイル数: {len(paths)}")

    # position_key -> Counter(fingerprint -> count)
    pos_fp_counter = defaultdict(Counter)
    # position_key -> fp -> (sample_file, sample_block_name, n_ents, bbox)
    pos_fp_sample = defaultdict(dict)
    # 全ファイルでのINSERT(恒等変換)位置キー一覧(テンプレ側にしか無いキー検出用)
    all_pos_keys_seen = set()

    template_keys = set(tb["key"] for tb in template_blocks)

    files_ok = 0
    files_err = []
    ABSENT = frozenset()  # 「この位置にINSERTが存在しない」ことを表す特別なフィンガープリント
    for p in paths:
        try:
            d = ezdxf.readfile(p)
        except Exception as ex:
            files_err.append((p, str(ex)))
            continue
        files_ok += 1
        m = d.modelspace()
        found_keys_this_file = set()
        for e in identity_transform_inserts(m):
            try:
                vents = list(e.virtual_entities())
            except Exception:
                continue
            bbox = bbox_of(vents)
            k = bbox_key(bbox)
            if k is None:
                continue
            all_pos_keys_seen.add(k)
            fp = content_fingerprint(vents)
            if k in template_keys:
                found_keys_this_file.add(k)
                pos_fp_counter[k][fp] += 1
                if fp not in pos_fp_sample[k]:
                    pos_fp_sample[k][fp] = (p, e.dxf.name, len(vents), bbox)
        # テンプレートに対応するキーがこのファイルに存在しない場合は「不在」に1票
        for k in template_keys:
            if k not in found_keys_this_file:
                pos_fp_counter[k][ABSENT] += 1
                if ABSENT not in pos_fp_sample[k]:
                    pos_fp_sample[k][ABSENT] = (p, "(なし/不在)", 0, None)

    print(f"読み込み成功: {files_ok} / エラー: {len(files_err)}")
    for p, err in files_err:
        print(f"  ERROR {p}: {err}")

    # 判定
    report_lines = []
    report_lines.append("# 図枠テンプレート INSERT 監査結果")
    report_lines.append("")
    report_lines.append(f"- テンプレート: {template_path}")
    report_lines.append(f"- 突き合わせバケットA実ファイル数: {files_ok} (読み込みエラー {len(files_err)} 件)")
    report_lines.append(f"- 位置キー丸め桁: 小数第{BBOX_TOL}位 (bbox min角, 幅, 高さ)")
    report_lines.append("")
    report_lines.append("## 判定表 (テンプレートの5 INSERT)")
    report_lines.append("")
    report_lines.append("| template block | n_ents | bbox (x0,y0,x1,y1) | 枠内(0,0)-(420,297)? | 判定 | バケットAでの一致票 | バケットAでの内容の異なり数 | 多数派サンプル |")
    report_lines.append("|---|---|---|---|---|---|---|---|")

    FRAME_X0, FRAME_Y0, FRAME_X1, FRAME_Y1 = 0.0, 0.0, 420.0, 297.0

    def in_frame(bbox):
        if bbox is None:
            return None
        x0, y0, x1, y1 = bbox
        return (x0 >= FRAME_X0 - 0.5 and y0 >= FRAME_Y0 - 0.5 and
                x1 <= FRAME_X1 + 0.5 and y1 <= FRAME_Y1 + 0.5)

    mismatches = []
    verdicts = []
    for tb in template_blocks:
        k = tb["key"]
        counter = pos_fp_counter.get(k, Counter())
        total_votes = sum(counter.values())
        inframe = in_frame(tb["bbox"])

        if total_votes == 0:
            verdict = "❌位置キー該当なし(バケットAのどのファイルにもこの位置にINSERTなし)"
            majority_desc = "-"
            match = False
        else:
            majority_fp, majority_count = counter.most_common(1)[0]
            match = (tb["fp"] == majority_fp)
            maj_file, maj_block_name, maj_n_ents, maj_bbox = pos_fp_sample[k][majority_fp]
            majority_desc = f"{maj_file} (block={maj_block_name}, n_ents={maj_n_ents}, {majority_count}/{total_votes}票)"
            verdict = "OK(多数派と一致)" if match else "❌不一致(多数派と内容が異なる)"

        verdicts.append({
            "template_block": tb["name"],
            "n_ents": tb["n_ents"],
            "bbox": tb["bbox"],
            "in_frame": inframe,
            "match": match,
            "total_votes": total_votes,
            "n_distinct_contents": len(counter),
            "majority_desc": majority_desc,
        })

        if not match:
            mm = {
                "template_block": tb["name"],
                "n_ents": tb["n_ents"],
                "bbox": tb["bbox"],
                "in_frame": inframe,
                "total_votes": total_votes,
                "n_distinct_contents": len(counter),
            }
            if total_votes > 0:
                majority_fp, majority_count = counter.most_common(1)[0]
                maj_file, maj_block_name, maj_n_ents, maj_bbox = pos_fp_sample[k][majority_fp]
                mm.update({
                    "majority_file": maj_file,
                    "majority_block_name": maj_block_name,
                    "majority_n_ents": maj_n_ents,
                    "majority_bbox": maj_bbox,
                    "majority_votes": majority_count,
                })
            mismatches.append(mm)

        report_lines.append(
            f"| {tb['name']} | {tb['n_ents']} | {tb['bbox']} | {inframe} | {verdict} | "
            f"{counter.most_common(1)[0][1] if counter else 0}/{total_votes} | {len(counter)} | {majority_desc} |"
        )

    report_lines.append("")
    report_lines.append("## 不一致・要対応ブロック詳細")
    report_lines.append("")
    if not mismatches:
        report_lines.append("(不一致なし)")
    for m in mismatches:
        report_lines.append(f"### template_block={m['template_block']}")
        report_lines.append("")
        report_lines.append(f"- テンプレート現状: {m['n_ents']} エンティティ, bbox={m['bbox']}, 枠内={m['in_frame']}")
        if m["total_votes"] == 0:
            report_lines.append("- バケットA 80枚中、この位置にINSERTを持つファイルは0件。")
            report_lines.append("- 判断: この位置にはバケットAのどのファイルにも対応する図枠要素が無い。"
                                 "テンプレート構築時の元ファイル(25154-1-07)固有の残骸である可能性が高い。")
        else:
            report_lines.append(f"- 多数派: {m['majority_n_ents']} エンティティ, bbox={m['majority_bbox']}, "
                                 f"{m['majority_votes']}/{m['total_votes']} 票, 内容の異なり数={m['n_distinct_contents']}")
            report_lines.append(f"- 多数派サンプル: {m['majority_file']} (block={m['majority_block_name']})")
        report_lines.append("")

    # バケットAに存在するが、テンプレートに全く対応スロットがない位置キー(参考情報)
    template_keys = set(tb["key"] for tb in template_blocks)
    extra_keys = all_pos_keys_seen - template_keys
    report_lines.append("## 参考: バケットAにのみ存在する恒等変換INSERT位置(テンプレート未収録)")
    report_lines.append("")
    report_lines.append(f"該当位置キー数: {len(extra_keys)} (これらは可変フィールド/刻印等の可能性があり、本タスクの対象外)")

    report_lines.append("")
    report_lines.append("## 読み込みエラー")
    report_lines.append("")
    if not files_err:
        report_lines.append("(エラーなし)")
    else:
        for p, err in files_err:
            report_lines.append(f"- {p}: {err}")

    with open("調査/frame_block_audit.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))

    print("\n=== 判定サマリ ===")
    for v in verdicts:
        print(v)

    print("\n保存: 調査/frame_block_audit.md")

    out = {
        "verdicts": verdicts,
        "mismatches": mismatches,
    }
    with open("調査/frame_block_audit.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()
