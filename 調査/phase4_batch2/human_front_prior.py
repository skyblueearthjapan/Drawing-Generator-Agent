# -*- coding: utf-8 -*-
u"""人間が選んだ正面ビューを**モデル座標系の候補**として数え上げる。

対照条件(`_baseline` = STEPの座標系そのままで投影)の照合結果には
「人間のfront役ビューは、SWの front/top/right のどれを、どう変換したものか」が入っている。
対照は**モデル座標系そのもの**なので、これはそのまま
「人間の正面 = モデル座標系のどの向きか」の分布になる = **候補集合の事前分布**。

    python 調査/phase4_batch2/human_front_prior.py [--dirs=...]

これが要る理由: 決定論ルールの top-1 が 55% 前後で頭打ちになったので、
**候補を複数投影してAIオペレータが選ぶ**運用に切り替えるしかない。
そのとき「候補が何個で、どう並べれば上位で当たるか」を数字で決める必要がある。
"""
import os
import sys
import io
import json
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from compare_conditions import collect, CONDS         # noqa: E402


def main():
    dirs = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--dirs=")), None)
    dirs = (dirs.split(",") if dirs else [u"調査/phase4_batch", u"調査/phase4_batch2"])
    rows = []
    for d in dirs:
        rows += collect(d if os.path.isabs(d) else os.path.join(ROOT, d))
    good = [r for r in rows if r["reliable"]]

    c = Counter()
    unresolved = []
    for r in good:
        o = r[u"対照_o"] or {}          # 対照 = モデル座標系そのまま
        v, t = o.get("matched_sw_view"), o.get("matched_transform")
        if not v:
            unresolved.append(r["key"])
            continue
        # 同点(対称)に恒等が含まれるなら、人間の選択は恒等と区別できない → 恒等として数える
        if u"恒等" in (o.get("ties") or []) and t != u"恒等":
            t = u"恒等"
        c[(v, t)] += 1

    n = sum(c.values())
    print(u"判定できた %d 点 / 内訳が取れた %d 点  ※対照(モデル座標系そのまま)基準" % (len(good), n))
    print(u"\n%-8s %-16s %5s %7s %s" % (u"SWビュー", u"紙面内変換", u"件数", u"割合", u"累積"))
    acc = 0
    order = []
    for (v, t), k in c.most_common():
        acc += k
        order.append((v, t, k, acc))
        print(u"%-8s %-16s %5d %6.1f%% %6.1f%%" % (v, t, k, 100.0 * k / n, 100.0 * acc / n))
    if unresolved:
        print(u"\n内訳不明: %r" % unresolved)

    print(u"\n===== 候補を上位N個まで投影したときの当たる率 =====")
    for topn in range(1, min(9, len(order)) + 1):
        print(u"  上位%2d候補: %5.1f%% (%d/%d)"
              % (topn, 100.0 * order[topn - 1][3] / n, order[topn - 1][3], n))

    print(u"\n===== 3条件(v1/対照/v2)のどれかが当たる率 =====")
    hit = sum(1 for r in good if any(r[l] == u"○" for l, _ in CONDS))
    print(u"  %d/%d = %.1f%%" % (hit, len(good), 100.0 * hit / len(good)))
    miss = [r["key"] for r in good if not any(r[l] == u"○" for l, _ in CONDS)]
    print(u"  3条件すべて外した %d 点: %r" % (len(miss), miss))
    return 0


if __name__ == "__main__":
    sys.exit(main())
