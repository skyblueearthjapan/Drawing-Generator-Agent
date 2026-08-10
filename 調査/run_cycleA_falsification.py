# -*- coding: utf-8 -*-
u"""改善サイクルAの**反証テスト**(「直したことで検出力が落ちていない」ことの機械確認)。

直した3点は、どれも「今まで落ちていたものを通す」方向の変更なので、
**通してはいけないものが通らないこと**を明示的に試験する。

  F1. ゲート②のPCD穴群: 注記のPCD値を実ジオメトリと違う値に書き換えると **不合格に戻る**
  F2. ゲート②のPCD穴群: 注記の個数を実ジオメトリと違う値にすると **却下される**
  F3. ゲート②のPCD穴群: 穴を1つ動かして等配でなくすると **却下される**
  F4. ゲート②の多角形: 六角形を**1特徴として検出**し続けている(頂点バラバラに戻っていない)
  F4b. ゲート②の多角形: 二面幅/対角が正しければ合格・0.5mmずれた寸法では **不合格**
  F5. 独立検証: 参考寸法 `(ＰＣＤ１２９)` を計画と違う文字に差し替えると **不合格になる**
  F6. 独立検証: 参考寸法でない寸法の文字を狂わせると **従来どおり不合格になる**
  F7. 図枠衝突: 衝突ゼロの図面の寸法を表題欄へ移すと **検出される**

実行:
    python 調査/run_cycleA_falsification.py [--out 調査/cycleA_falsification.json]
"""
import argparse
import io
import json
import math
import os
import shutil
import sys
import tempfile

import ezdxf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import dim_engine            # noqa: E402
from engine import gate2_completeness    # noqa: E402
from engine import generate_drawing      # noqa: E402

def _case(request_id, dxf_name):
    u"""依頼フォルダの生成DXFを探す。

    ❗合否で置き場所が変わる(合格=`生成/`、不合格=`生成/不合格/`)ので、
    どちらにあってもよいように**両方を探す**。ハードコードすると、部品が納品化した
    (=検証が強化されて合格に変わった)瞬間に反証テストが FileNotFoundError で死ぬ。
    """
    base = os.path.join(ROOT, u"data", u"依頼箱", request_id)
    cands = [os.path.join(base, u"生成", dxf_name),
             os.path.join(base, u"生成", u"不合格", dxf_name)]
    dxf = next((p for p in cands if os.path.exists(p)), cands[0])
    return {"dxf": dxf, "plan": os.path.join(base, u"plan.json")}


PCD_CASE = _case(u"BLIND-25154-4-07", u"25154-4-07_減速機ブラケット.dxf")
POLY_CASE = _case(u"BLIND-25154-1-04", u"25154-1-04_ケーブルベア側ジャッキプレート.dxf")
CLEAN_CASE = _case(u"BLIND-25154-4-12", u"25154-4-12_LSブラケット2.dxf")

ZEN = u"０１２３４５６７８９"


def to_zen(n):
    return u"".join(ZEN[int(c)] for c in str(n))


def _tmp(path, suffix=".dxf"):
    fd, p = tempfile.mkstemp(suffix=suffix, prefix="falsify_")
    os.close(fd)
    shutil.copy2(path, p)
    return p


def tamper_note(src, old, new):
    u"""穴注記MTEXTの文字を差し替えたDXFを作る(実ジオメトリはそのまま)。"""
    p = _tmp(src)
    doc = ezdxf.readfile(p)
    hit = 0
    for e in doc.modelspace():
        if e.dxftype() == "MTEXT" and old in e.text:
            e.text = e.text.replace(old, new)
            hit += 1
    doc.saveas(p)
    return p, hit


def move_one_hole(src, diameter, dx, scale=1.0):
    u"""指定径の穴を**1つだけ**動かして『円周等分でない』状態を作る。

    ❗SW投影の円は CIRCLE ではなく **ARC 2〜4本に分割**されて出る(CLAUDE.md知見)。
    片方の弧だけ動かすと「穴が1個増えた」ことになって別の理由で落ちてしまうので、
    **同じ中心の弧を全部まとめて**動かす。
    """
    p = _tmp(src)
    doc = ezdxf.readfile(p)
    ents = [e for e in doc.modelspace()
            if e.dxftype() in ("CIRCLE", "ARC")
            and abs(e.dxf.radius * 2.0 - diameter * scale) < 1e-6]
    if not ents:
        return p, 0
    c0 = ents[0].dxf.center
    hit = 0
    for e in ents:
        c = e.dxf.center
        if math.hypot(c.x - c0.x, c.y - c0.y) < 1e-6:
            e.dxf.center = (c.x + dx, c.y, c.z)
            hit += 1
    doc.saveas(p)
    return p, hit


def tamper_dim_text(src, style, new_text):
    u"""DIMENSIONの描画文字(アノニマスブロック内MTEXT)を差し替える。"""
    p = _tmp(src)
    doc = ezdxf.readfile(p)
    hit = 0
    for e in doc.modelspace():
        if e.dxftype() != "DIMENSION" or str(e.dxf.dimstyle) != style:
            continue
        g = e.dxf.get("geometry", None)
        if g and g in doc.blocks:
            for b in doc.blocks.get(g):
                if b.dxftype() == "MTEXT":
                    b.text = new_text
                    hit += 1
    doc.saveas(p)
    return p, hit


def move_dim_into_title_block(src, style, dy):
    u"""1本の寸法のアノニマスブロック実体を下へ平行移動して表題欄に載せる。"""
    p = _tmp(src)
    doc = ezdxf.readfile(p)
    hit = 0
    for e in doc.modelspace():
        if e.dxftype() != "DIMENSION" or str(e.dxf.dimstyle) != style:
            continue
        g = e.dxf.get("geometry", None)
        if g and g in doc.blocks:
            blk = doc.blocks.get(g)
            for b in blk:
                try:
                    b.translate(0, dy, 0)
                    hit += 1
                except Exception:
                    pass
    doc.saveas(p)
    return p, hit


def gate2_ok(dxf, plan):
    r = gate2_completeness.check_completeness(dxf, plan)
    return r["ok"], len(r["unspecified"]), r


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, u"調査", "cycleA_falsification.json"))
    args = ap.parse_args(argv[1:])
    res = {}
    tmps = []

    # ---------- F0: 基準(手を加えない状態) ----------
    base_ok, base_n, base_rep = gate2_ok(PCD_CASE["dxf"], PCD_CASE["plan"])
    res["F0_base_4-07"] = {"gate2_ok": base_ok, "unspecified": base_n,
                           "pcd_groups_ok": [g["ok"] for g in base_rep["pcd_groups"]],
                           "expect": "gate2_ok=True",
                           "pass": base_ok is True}
    print(u"F0 4-07 素の判定: gate2=%s 未指定=%d -> %s"
          % (base_ok, base_n, u"OK" if base_ok else u"NG"))

    # ---------- F1: PCD値を偽る ----------
    p, hit = tamper_note(PCD_CASE["dxf"], u"ＰＣＤ%s" % to_zen(129), u"ＰＣＤ%s" % to_zen(130))
    tmps.append(p)
    ok, n, rep = gate2_ok(p, PCD_CASE["plan"])
    res["F1_pcd_value_wrong"] = {
        "replaced_notes": hit, "gate2_ok": ok, "unspecified": n,
        "pcd_reasons": [g["reason"] for g in rep["pcd_groups"]],
        "expect": u"注記PCD130 vs 実ジオメトリPCD129 -> 却下されゲート②不合格",
        "pass": (hit > 0 and ok is False and all(not g["ok"] for g in rep["pcd_groups"]))}
    print(u"F1 PCD値を129->130に偽装: gate2=%s 未指定=%d -> %s"
          % (ok, n, u"OK" if res["F1_pcd_value_wrong"]["pass"] else u"NG"))

    # ---------- F2: 個数を偽る ----------
    p, hit = tamper_note(PCD_CASE["dxf"], u"%s－" % to_zen(12), u"%s－" % to_zen(8))
    tmps.append(p)
    ok, n, rep = gate2_ok(p, PCD_CASE["plan"])
    res["F2_pcd_count_wrong"] = {
        "replaced_notes": hit, "gate2_ok": ok, "unspecified": n,
        "pcd_reasons": [g["reason"] for g in rep["pcd_groups"]],
        "expect": u"注記8個 vs 実ジオメトリ12個 -> 却下されゲート②不合格",
        "pass": (hit > 0 and ok is False and all(not g["ok"] for g in rep["pcd_groups"]))}
    print(u"F2 個数を12->8に偽装: gate2=%s 未指定=%d -> %s"
          % (ok, n, u"OK" if res["F2_pcd_count_wrong"]["pass"] else u"NG"))

    # ---------- F3: 穴を1つ動かして等配を崩す ----------
    p, hit = move_one_hole(PCD_CASE["dxf"], 5.0, 1.0)
    tmps.append(p)
    ok, n, rep = gate2_ok(p, PCD_CASE["plan"])
    res["F3_not_equally_spaced"] = {
        "moved_circles": hit, "gate2_ok": ok, "unspecified": n,
        "pcd_reasons": [g["reason"] for g in rep["pcd_groups"]],
        "expect": u"φ5穴を1つ1mm動かす -> 半径/等配の検算に落ちてゲート②不合格",
        "pass": (hit > 0 and ok is False and all(not g["ok"] for g in rep["pcd_groups"]))}
    print(u"F3 φ5穴を1つ1mmずらす: gate2=%s 未指定=%d -> %s"
          % (ok, n, u"OK" if res["F3_not_equally_spaced"]["pass"] else u"NG"))

    # ---------- F4: 多角形の二面幅 ----------
    _ok, _n, rep0 = gate2_ok(POLY_CASE["dxf"], POLY_CASE["plan"])
    polys = rep0["polygons"]
    # ❗2026-08-10(改善サイクル2/E4)で期待値を更新した。
    #   旧: 「二面幅寸法が無いので未指定1件で報告する」(ok is False)
    #   新: 1-04の計画に**斜め寸法HEXD(相対する頂点間の対角27.712)**が入ったので、
    #       同じ六角形が「1特徴として検出され、かつ対角寸法でカバー済み」になる。
    #   ここで見張るのは **多角形を1特徴として検出し続けているか**(バラバラの頂点ノードに
    #   戻っていないか)。カバレッジ規則自体の反証は F4b と run_cycle2_falsification.py。
    res["F4_polygon"] = {"detected": [{"view": p_["view"], "n": p_["n"],
                                       "across_flats": p_["across_flats"], "ok": p_["ok"],
                                       "covered_by": p_["covered_by"]}
                                      for p_ in polys],
                         "expect": u"六角形(二面幅24)を1特徴として検出し、"
                                   u"斜め寸法HEXD(対角)でカバー済みと判定する。"
                                   u"未指定に多角形は残らない",
                         "pass": (len(polys) == 1 and polys[0]["n"] == 6
                                  and abs(polys[0]["across_flats"] - 24.0) <= 0.01
                                  and polys[0]["ok"] is True
                                  and u"HEXD" in (polys[0]["covered_by"] or u"")
                                  and not any(u_["feature"] == "polygon"
                                              for u_ in rep0["unspecified"]))}
    print(u"F4 六角形の検出と対角カバレッジ: %s -> %s"
          % (res["F4_polygon"]["detected"], u"OK" if res["F4_polygon"]["pass"] else u"NG"))

    # ---------- F4b: 二面幅寸法があれば合格・値が違えば不合格(多角形カバレッジの反証) ----------
    hexa = polys[0] if polys else None
    if hexa:
        w = hexa["across_flats"]
        good = gate2_completeness.polygon_covered(hexa, [w], [])
        near = gate2_completeness.polygon_covered(hexa, [w + 0.5], [])
        corner = gate2_completeness.polygon_covered(hexa, [hexa["across_corners"]], [])
        res["F4b_polygon_rule"] = {
            "across_flats": w, "with_correct_width": good,
            "with_wrong_width(+0.5mm)": near, "with_across_corners": corner,
            "expect": u"正しい二面幅/対角では合格・0.5mmずれた寸法では不合格",
            "pass": (good is not None and corner is not None and near is None)}
        print(u"F4b 二面幅%.4g: 正=%s / +0.5mm=%s / 対角=%s -> %s"
              % (w, good, near, corner,
                 u"OK" if res["F4b_polygon_rule"]["pass"] else u"NG"))

    # ---------- F5: 参考寸法の文字を差し替える(計画と不一致にする) ----------
    with io.open(PCD_CASE["plan"], encoding="utf-8") as f:
        plan = json.load(f)
    order = [d["id"] for d in plan["dimensions"]]
    ref_id = next(d["id"] for d in plan["dimensions"] if d.get("text_override"))
    ref_style = "GEN%03d" % (order.index(ref_id) + 1)
    p, hit = tamper_dim_text(PCD_CASE["dxf"], ref_style, u"\\A1;(ＰＣＤ９９９)")
    tmps.append(p)
    v = generate_drawing.independent_verify(p, PCD_CASE["plan"])
    row = next(r for r in v["gate1"] if r["id"] == ref_id)
    res["F5_reference_text_tampered"] = {
        "style": ref_style, "replaced": hit, "verify_ok": v["ok"],
        "row": {k: row.get(k) for k in ("id", "text", "text_role", "text_override_ok", "ok")},
        "expect": u"参考寸法は数値照合しないが、**計画と違う文字**なら不合格",
        "pass": (hit > 0 and v["ok"] is False and row["ok"] is False
                 and row["text_override_ok"] is False)}
    print(u"F5 参考寸法(ＰＣＤ１２９)を(ＰＣＤ９９９)に改竄: verify=%s -> %s"
          % (v["ok"], u"OK" if res["F5_reference_text_tampered"]["pass"] else u"NG"))

    # ---------- F5b: 素の状態では参考寸法で不合格にならない ----------
    v0 = generate_drawing.independent_verify(PCD_CASE["dxf"], PCD_CASE["plan"])
    row0 = next(r for r in v0["gate1"] if r["id"] == ref_id)
    res["F5b_reference_text_intact"] = {
        "verify_ok": v0["ok"], "text_role": row0["text_role"],
        "text_diff_mm": row0["text_diff_mm"],
        "expect": u"参考寸法(ＰＣＤ１２９)を実測124.6と比較しない -> 合格",
        "pass": (v0["ok"] is True and row0["text_role"] == "reference"
                 and row0["text_diff_mm"] is None)}
    print(u"F5b 素の参考寸法: verify=%s role=%s -> %s"
          % (v0["ok"], row0["text_role"],
             u"OK" if res["F5b_reference_text_intact"]["pass"] else u"NG"))

    # ---------- F6: 参考寸法でない寸法の文字を狂わせる ----------
    plain_id = next(d["id"] for d in plan["dimensions"]
                    if not d.get("text_override") and d["kind"] == "linear")
    plain_style = "GEN%03d" % (order.index(plain_id) + 1)
    p, hit = tamper_dim_text(PCD_CASE["dxf"], plain_style, u"999")
    tmps.append(p)
    v = generate_drawing.independent_verify(p, PCD_CASE["plan"])
    row = next(r for r in v["gate1"] if r["id"] == plain_id)
    res["F6_plain_text_tampered"] = {
        "style": plain_style, "replaced": hit, "verify_ok": v["ok"],
        "row": {k: row.get(k) for k in ("id", "text", "text_role", "text_diff_mm", "ok")},
        "expect": u"通常寸法の文字改竄は従来どおり数値照合で検出",
        "pass": (hit > 0 and v["ok"] is False and row["ok"] is False)}
    print(u"F6 通常寸法(%s)の文字を999に改竄: verify=%s -> %s"
          % (plain_id, v["ok"], u"OK" if res["F6_plain_text_tampered"]["pass"] else u"NG"))

    # ---------- F7: 図枠衝突 ----------
    doc = ezdxf.readfile(CLEAN_CASE["dxf"])
    base_col = dim_engine.check_frame_collisions(doc)
    d2 = ezdxf.readfile(CLEAN_CASE["dxf"])
    style = next(str(e.dxf.dimstyle) for e in d2.modelspace() if e.dxftype() == "DIMENSION")
    p, hit = move_dim_into_title_block(CLEAN_CASE["dxf"], style, -100.0)
    tmps.append(p)
    col = dim_engine.check_frame_collisions(ezdxf.readfile(p))
    res["F7_frame_collision"] = {
        "clean_collisions": base_col, "moved_style": style, "moved_entities": hit,
        "collisions_after": col,
        "expect": u"衝突0の図面(4-12)で0件・寸法を表題欄へ100mm下げると検出",
        "pass": (len(base_col) == 0 and hit > 0 and len(col) > 0
                 and any("title_block" in c["zone"] for c in col))}
    print(u"F7 図枠衝突: 素=%d件 / 表題欄へ移動後=%d件 -> %s"
          % (len(base_col), len(col), u"OK" if res["F7_frame_collision"]["pass"] else u"NG"))

    for t in tmps:
        try:
            os.remove(t)
        except OSError:
            pass

    n_pass = sum(1 for v_ in res.values() if v_["pass"])
    print(u"\n===== 反証テスト %d/%d 合格 =====" % (n_pass, len(res)))
    for k, v_ in res.items():
        if not v_["pass"]:
            print(u"  ** NG ** %s: %s" % (k, v_["expect"]))
    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    print(u"saved %s" % args.out)
    return 0 if n_pass == len(res) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
