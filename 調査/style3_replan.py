# -*- coding: utf-8 -*-
u"""様式第3弾の27論点レンズで**作図計画を改訂する**(AIオペレータの作業を機械化したもの)。

手で6枚ぶんの `placement.side` を書き換えると再現できないので、規則を実装して適用する。
適用する規則(いずれも `app/prompts/plan_prompt.md` の作法12〜16に対応):

  R1 作法12: 各線形寸法の `placement.side` を **測定点に最も近い辺**へ寄せる
             (補助線長 =(測定点からその辺の輪郭までの距離)+ offset を最小化する)。
             `placement.chain_group` のメンバーは**グループ単位**で動かす(整列を壊さない)。
             `placement.side_fixed: true` の寸法は動かさない。
  R2 再段付け: 辺ごとに寸法を「測定区間の短い順」に level 1,2,3… へ振り直す
             (人間の流儀: 短い寸法が内側・長い寸法が外側)。連記グループは1段を占有する。
  R3 作法13: 円形ビューの直径が2本以上なら、**外径(最大)以外**を輪郭ビューへ移す。
             移し先は「その径の実在円が別ビューで線分として見える」ことを確認できないため、
             **自動では移さず候補を出すだけ**にして、実際の移動は個別に書く。
  R4 作法14: `anchor_check` を持つ穴注記に `auto_place: true` を付ける。
  R5 作法16: 生の `pattern` で書かれた穴注記のうち、構造化できるものを `spec` へ直す
             (呼び値翻訳を効かせるため)。**自動変換はせず候補の列挙のみ**。

実行:
    python 調査/style3_replan.py --plan data/依頼箱/BLIND2-25154-4-05/plan.json --apply
    python 調査/style3_replan.py --all --dry-run
"""
import argparse
import copy
import io
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import compose_drawing  # noqa: E402
from engine import dim_engine  # noqa: E402

BACKUP_DIR = os.path.join(ROOT, u"調査", u"style3", u"plans_before")

PLANS = [
    u"data/依頼箱/BLIND-25154-6-02/plan.json",
    u"data/依頼箱/BLIND2-25154-1-27/plan.json",
    u"data/依頼箱/BLIND2-25154-2-16/plan.json",
    u"data/依頼箱/BLIND2-25154-3-02/plan.json",
    u"data/依頼箱/BLIND2-25154-4-05/plan.json",
    u"data/依頼箱/BLIND2-25154-5-05/plan.json",
]

SIDE_PAIR = {"above": ("above", "below"), "below": ("above", "below"),
             "left": ("left", "right"), "right": ("left", "right")}


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(p, d):
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2))
        f.write(u"\n")


def view_bboxes(plan, meta_json, scale, use_views, reserves):
    u"""ビュー幾何の外接矩形(図面座標)。apply_plan の view_bbox と同じ値になる region を使う。"""
    tf = dim_engine.build_view_transforms(meta_json, scale, views=use_views,
                                          reserves=reserves)
    return tf, {k: tf[k]["region"] for k in tf}


def ext_len_for_side(bbox, side, p1, p2, offset):
    u"""その辺に寸法線を置いたときの**補助線の最大長**(mm)。"""
    x0, y0, x1, y1 = bbox
    if side == "below":
        return max(p1[1], p2[1]) - y0 + offset
    if side == "above":
        return y1 - min(p1[1], p2[1]) + offset
    if side == "left":
        return max(p1[0], p2[0]) - x0 + offset
    if side == "right":
        return x1 - min(p1[0], p2[0]) + offset
    raise ValueError(side)


def span_of(side, p1, p2):
    return (abs(p1[0] - p2[0]) if side in ("above", "below")
            else abs(p1[1] - p2[1]))


def replan(plan_path, verbose=True):
    plan = _read(plan_path)
    scale, use_views, reserves = dim_engine.plan_layout(plan)
    meta_json = os.path.join(ROOT, plan["source"]["meta_json"])
    tf, bbox = view_bboxes(plan, meta_json, scale, use_views, reserves)
    offsets = compose_drawing.resolve_dim_offsets(plan)

    def to_draw(view, meas):
        sp = meas.get("space", "view")
        f = tf[view]["model_to_draw"]
        return ([f(meas["p1"]), f(meas["p2"])] if sp == "model"
                else [tuple(meas["p1"][:2]), tuple(meas["p2"][:2])])

    # ---- 対象になる線形寸法を集める ----
    targets = {}
    for item in plan.get("dimensions", []):
        pl = item.get("placement") or {}
        side = pl.get("side")
        if side not in SIDE_PAIR or pl.get("side_fixed"):
            continue
        if item["kind"] not in ("linear", "diameter"):
            continue
        if item["kind"] == "diameter" and item.get("context") == "circular_view":
            continue
        meas = item["measure"]
        if "p1" not in meas or "p2" not in meas:
            continue
        ang = dim_engine.resolve_direction(meas, side)
        if dim_engine.is_oblique_direction(ang):
            continue
        p1, p2 = to_draw(item["view"], meas)
        targets[item["id"]] = {"item": item, "p1": p1, "p2": p2, "side": side,
                              "view": item["view"],
                              "group": compose_drawing.chain_key(item)}

    # ---- R1: グループ単位で辺を選び直す ----
    groups = {}
    for did, t in targets.items():
        key = t["group"] or ("#", did)
        groups.setdefault(key, []).append(did)
    moves = []
    for key, ids in sorted(groups.items(), key=lambda kv: str(kv[0])):
        t0 = targets[ids[0]]
        view = t0["view"]
        cand = SIDE_PAIR[t0["side"]]
        best, best_len = None, None
        for s in cand:
            off = max(offsets[i] for i in ids)
            ln = max(ext_len_for_side(bbox[view], s, targets[i]["p1"],
                                      targets[i]["p2"], off) for i in ids)
            if best_len is None or ln < best_len - 1e-6:
                best, best_len = s, ln
        old = t0["side"]
        if best != old:
            for i in ids:
                targets[i]["item"].setdefault("placement", {})["side"] = best
                targets[i]["side"] = best
            moves.append({"group": str(key), "ids": ids,
                          "from": old, "to": best,
                          "ext_len_mm": round(best_len, 2)})

    # ---- R2: 辺ごとに「短い寸法ほど内側」で段を振り直す ----
    by_side = {}
    for did, t in targets.items():
        by_side.setdefault((t["view"], t["side"]), []).append(did)
    levels = []
    for (view, side), ids in sorted(by_side.items()):
        gmap = {}
        for i in ids:
            gmap.setdefault(targets[i]["group"] or ("#", i), []).append(i)
        order = sorted(gmap.items(),
                       key=lambda kv: max(span_of(side, targets[i]["p1"],
                                                  targets[i]["p2"]) for i in kv[1]))
        for lv, (_g, gids) in enumerate(order, start=1):
            for i in gids:
                pl = targets[i]["item"].setdefault("placement", {})
                if pl.get("offset_mm") is not None:
                    continue          # 明示オフセットは尊重する
                if pl.get("level") != lv:
                    levels.append({"id": i, "view": view, "side": side,
                                   "level_from": pl.get("level"), "level_to": lv})
                pl["level"] = lv

    # ---- R4: 穴注記に auto_place ----
    autos = []
    for n in plan.get("hole_notes", []):
        # anchor_check が無い注記でも、引出線の**始点は動かさず**折れ点から先だけを
        # engine が置き直せる(dim_engine.auto_place_hole_note の半径0モード)
        if not n.get("auto_place"):
            n["auto_place"] = True
            autos.append(n["id"])

    # ---- R3/R5: 自動で直さないものを候補として列挙 ----
    circ = {}
    for item in plan.get("dimensions", []):
        if item["kind"] == "diameter" and item.get("context") == "circular_view":
            circ.setdefault(item["view"], []).append(
                (item["id"], float(item["value_expected"])))
    circ_over = {v: sorted(ids, key=lambda x: -x[1]) for v, ids in circ.items()
                 if len(ids) > 1}
    raw_notes = [n["id"] for n in plan.get("hole_notes", [])
                 if n.get("pattern") and not n.get("spec")]

    rep = {"plan": os.path.relpath(plan_path, ROOT), "side_moves": moves,
           "level_changes": levels, "auto_place_added": autos,
           "circular_view_over": circ_over, "raw_pattern_notes": raw_notes}
    if verbose:
        print(u"== %s" % rep["plan"])
        print(u"   辺の付け替え %d件: %s" % (len(moves),
                                          [(m["ids"], m["from"], "->", m["to"]) for m in moves]))
        print(u"   段の振り直し %d件" % len(levels))
        print(u"   auto_place 付与 %d件: %s" % (len(autos), autos))
        if circ_over:
            print(u"   ❗円形ビューの径が複数(手当が要る): %s" % circ_over)
        if raw_notes:
            print(u"   ❗生patternの注記(呼び値翻訳が効かない): %s" % raw_notes)
    return plan, rep


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv[1:])
    paths = ([os.path.join(ROOT, args.plan)] if args.plan
             else [os.path.join(ROOT, p) for p in PLANS])
    os.makedirs(BACKUP_DIR, exist_ok=True)
    reps = []
    for p in paths:
        plan, rep = replan(p)
        reps.append(rep)
        if args.apply:
            bk = os.path.join(BACKUP_DIR, os.path.basename(os.path.dirname(p)) + u".json")
            if not os.path.exists(bk):
                shutil.copyfile(p, bk)
            _write(p, plan)
            print(u"   -> applied(退避 %s)" % os.path.relpath(bk, ROOT))
    out = os.path.join(ROOT, u"調査", u"style3_replan.json")
    with io.open(out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"reports": reps}, ensure_ascii=False, indent=1))
    print(u"saved %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
