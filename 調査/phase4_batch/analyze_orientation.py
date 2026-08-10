# -*- coding: utf-8 -*-
u"""向きルールの外し方を「人間が実際に選んだ紙面法線・紙面水平軸」に翻訳して傾向を見る。

照合結果は「人間のfrontビュー ← SWの<front|top|right>ビューを<D4変換>」という形なので、
  ・SWの front/top/right の紙面手前ベクトルは提示フレームの ez / ey / ex そのもの
  ・そこへ D4変換(紙面内回転)を掛けると人間の紙面水平軸が判る
から、**人間がモデルのどの軸を紙面法線・紙面水平に選んだか**を逆算できる。

    python 調査/phase4_batch/analyze_orientation.py
"""
import os
import sys
import io
import json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import view_orient as vo  # noqa: E402

#: D4変換名 → 紙面内回転角(CCW度)。鏡像は本来出てはいけないので None
ROT_OF = {u"恒等": 0, u"回転90": 90, u"回転180": 180, u"回転270": 270,
          u"鏡像(左右反転)": None, u"鏡像+回転90": None,
          u"鏡像(上下反転)": None, u"鏡像+回転270": None}


def axis_name(v):
    for i in range(3):
        if abs(v[i]) > 0.5:
            return (u"-" if v[i] < 0 else u"+") + vo.AXIS_NAME[i], i
    return "?", None


def rank_of(size, i):
    u"""bbox 3辺のうち i 番目が小さい方から何番目か(0=最小)。"""
    order = sorted(range(3), key=lambda k: size[k])
    return order.index(i)


RANK_NAME = {0: u"最小", 1: u"中間", 2: u"最大"}


def main():
    with io.open(os.path.join(HERE, "targets.json"), encoding="utf-8") as f:
        targets = json.load(f)["targets"]
    rows = []
    for t in targets:
        p = os.path.join(HERE, t["key"], "result.json")
        if not os.path.exists(p):
            continue
        with io.open(p, encoding="utf-8") as f:
            rows.append(json.load(f))

    hdr = u"%-7s %-20s %-11s %-22s %-14s %-14s %s" % (
        u"図番", u"品名", u"クラス", u"bbox(X,Y,Z)", u"我々の法線", u"人間の法線", u"判定")
    print(hdr)
    print(u"-" * len(hdr))
    stats = {}
    for r in rows:
        v = r.get("verdict") or {}
        o = v.get("orientation") or {}
        size = r.get("size_mm") or [0, 0, 0]
        fr = r.get("frame") or {}
        if not fr:
            continue
        ex = fr["ex"]
        ey = fr["ey"]
        ez = fr["ez"]
        our_n, our_i = axis_name(ez)
        our_x, _ = axis_name(ex)
        line = u"%-7s %-20s %-11s %-22s %-14s " % (
            r["key"], (r["name"] or "")[:20], r.get("shape_class"),
            u"%.1f,%.1f,%.1f" % tuple(size),
            u"%s(%s)" % (our_n, RANK_NAME[rank_of(size, our_i)]))

        sw_view = o.get("matched_sw_view")
        tr = o.get("matched_transform")
        if sw_view not in ("front", "top", "right"):
            print(line + u"%-14s %s" % (u"-", v.get("verdict")))
            continue
        # 人間の紙面法線 = そのSWビューの out ベクトル
        out_vec = {"front": ez, "top": ey, "right": ex}[sw_view]
        # 人間の紙面水平 = そのSWビューの paper_x を D4変換で回したもの
        px_vec = {"front": ex, "top": ex, "right": [-c for c in ez]}[sw_view]
        py_vec = {"front": ey, "top": [-c for c in ez], "right": ey}[sw_view]
        # 画像をθだけCCWに回すと、紙面+xに来るのは「元の紙面角 -θ」に居たベクトル
        rot = ROT_OF.get(tr)
        if rot == 90:
            px_vec = [-c for c in py_vec]
        elif rot == 180:
            px_vec = [-c for c in px_vec]
        elif rot == 270:
            px_vec = py_vec
        hum_n, hum_i = axis_name(out_vec)
        hum_x, _ = axis_name(px_vec)
        key = (r.get("shape_class"), RANK_NAME[rank_of(size, our_i)],
               RANK_NAME[rank_of(size, hum_i)])
        stats[key] = stats.get(key, 0) + 1
        print(line + u"%-14s %-5s 水平: 我々%s/人間%s  照合=SW-%s %s%s" % (
            u"%s(%s)" % (hum_n, RANK_NAME[rank_of(size, hum_i)]),
            o.get("verdict"), our_x, hum_x, sw_view, tr,
            u"" if o.get("correct") else u"  ← 外した(%s)" % (
                u"法線ちがい" if our_i != hum_i else u"紙面内回転のみ")))

    print(u"\n-- (形状クラス, 我々の法線の辺順位, 人間の法線の辺順位) の分布 --")
    for k in sorted(stats, key=lambda z: -stats[z]):
        print(u"  %-12s 我々=%-4s 人間=%-4s : %d件%s" % (
            k[0], k[1], k[2], stats[k], u"  ★一致" if k[1] == k[2] else u""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
