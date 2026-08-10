# -*- coding: utf-8 -*-
u"""計画の `space:"view"` 座標を、**レイアウト変更ぶんだけ平行移動**して救済する。

背景(CLAUDE.md既知の罠):
> ❗計画JSONの `space:"view"`(図面絶対座標)はレイアウトが変わると壊れる。
> 尺度・ビュー集合・予約帯のどれを変えても図面座標が動くため snap/anchor_check が落ちる。

様式第3弾の改訂では `placement.side` / `level` を付け替えたので**予約帯が変わり**、
ビューの中心座標が動いた。ただし尺度もビュー集合も変えていないので、
**同一ビュー内の図面座標の変化は「ビュー中心の平行移動」だけ**である
(`compose_drawing._layout_targets` の `targets` の差分)。よって厳密に救済できる。

使い方(改訂前の計画を退避してあることが前提):
    python 調査/style3_shift_view_space.py --before 調査/style3/plans_before/BLIND2-25154-2-16.json \
                                           --after  data/依頼箱/BLIND2-25154-2-16/plan.json --apply
    python 調査/style3_shift_view_space.py --all --apply
"""
import argparse
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from engine import compose_drawing  # noqa: E402
from engine import dim_engine  # noqa: E402

BEFORE_DIR = os.path.join(ROOT, u"調査", u"style3", u"plans_before")
INBOX = os.path.join(ROOT, u"data", u"依頼箱")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(p, d):
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2))
        f.write(u"\n")


def targets_of(plan):
    scale, use_views, reserves = dim_engine.plan_layout(plan)
    meta = _read(os.path.join(ROOT, plan["source"]["meta_json"]))
    geoms = {k: meta["views"][k]["geom_mm"] for k in use_views}
    t, _c, _s, _i = compose_drawing._layout_targets(geoms, scale, views=use_views,
                                                    reserves=reserves)
    return t


def shift_plan(before, after):
    t0 = targets_of(before)
    t1 = targets_of(after)
    delta = {k: (t1[k][0] - t0[k][0], t1[k][1] - t0[k][1]) for k in t1 if k in t0}
    n = [0]

    def mv(view, pt):
        d = delta.get(view)
        if d is None or (abs(d[0]) < 1e-12 and abs(d[1]) < 1e-12):
            return pt
        n[0] += 1
        return [pt[0] + d[0], pt[1] + d[1]] + list(pt[2:])

    for item in after.get("dimensions", []):
        v = item["view"]
        m = item.get("measure") or {}
        if m.get("space", "view") == "view":
            for k in ("p1", "p2", "base", "vertex"):
                if k in m:
                    m[k] = mv(v, m[k])
        cc = item.get("cross_check")
        if cc and cc.get("space", "view") == "view":
            cc["center"] = mv(cc.get("view", v), cc["center"])
    for note in after.get("hole_notes", []):
        v = note["view"]
        ld = note.get("leader") or {}
        if ld and ld.get("space", "view") == "view":
            ld["points"] = [mv(v, p) for p in ld["points"]]
        if note.get("text_space", "view") == "view" and "text_insert" in note:
            note["text_insert"] = mv(v, note["text_insert"])
        ac = note.get("anchor_check")
        if ac and ac.get("space", "view") == "view":
            ac["center"] = mv(ac.get("view", v), ac["center"])
    return after, delta, n[0]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default=None)
    ap.add_argument("--after", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv[1:])
    pairs = []
    if args.all:
        for fn in sorted(os.listdir(BEFORE_DIR)):
            if not fn.endswith(".json"):
                continue
            req = fn[:-5]
            pairs.append((os.path.join(BEFORE_DIR, fn),
                          os.path.join(INBOX, req, "plan.json")))
    else:
        pairs.append((os.path.join(ROOT, args.before), os.path.join(ROOT, args.after)))
    for b, a in pairs:
        before, after = _read(b), _read(a)
        after2, delta, n = shift_plan(before, after)
        print(u"%-24s ビュー移動=%s  平行移動した座標=%d件"
              % (os.path.basename(os.path.dirname(a)),
                 {k: (round(v[0], 3), round(v[1], 3)) for k, v in delta.items()}, n))
        if args.apply and n:
            _write(a, after2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
