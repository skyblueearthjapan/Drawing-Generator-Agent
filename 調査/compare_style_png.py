# -*- coding: utf-8 -*-
u"""様式実装(直列連記・左右振り分け・角度位相)の効果を人間図面と並置して見るPNG。

`調査/compare_centerline_png.py` と**同じレンダラー**(手描き方式)で、
左=人間図面 / 中=様式実装前の生成図面(中心線第1弾の版) / 右=様式実装後の再生成図面
を並べる。CLAUDE.md知見「生成図面の見た目を評価する前に必ず人間の実図面を同じレンダラーで描く」に従う。

色分けは compare_centerline_png と同じ(赤=中心線 / 白=実線 / 橙=かくれ線 / 緑=寸法)。

実行:
    python 調査/compare_style_png.py                 # BLIND2の5枚 -> 調査/style/compare/
    python 調査/compare_style_png.py --two           # 人間 vs 様式実装後 の2面のみ
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import matplotlib                                   # noqa: E402
matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = "Meiryo"
matplotlib.rcParams["axes.unicode_minus"] = False
import matplotlib.pyplot as plt                     # noqa: E402

from compare_centerline_png import render           # noqa: E402

HUMAN_DIR = os.path.join(ROOT, u"荏原トライ調整用", u"DXF", u"部品表用DXFデータ")
# ❗「様式実装前」は中心線第1弾(2026-08-10)時点の生成物を凍結保存したもの。
#   `調査/centerline/blind2/` は反証テストの種として計画変更のたびに作り直されるので、
#   比較の基準にしてはいけない(作り直すと『前』が『後』になってしまう)。
BEFORE_DIR = os.path.join(ROOT, u"調査", u"style", u"before")
AFTER_DIR = os.path.join(ROOT, u"data", u"納品箱")

CASES = [
    (u"25154-1-27", u"走行フレーム踏板", u"1.走行軸"),
    (u"25154-2-16", u"指針", u"2.ターン軸"),
    (u"25154-3-02", u"モータブラケット", u"3.昇降軸"),
    (u"25154-4-05", u"駆動ユニットブラケット", u"4.前後軸"),
    (u"25154-5-05", u"減速機フランジ", u"5.ひねり軸"),
]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--two", action="store_true", help=u"人間 vs 様式実装後 の2面だけ描く")
    args = ap.parse_args(argv[1:])

    out_dir = os.path.join(ROOT, u"調査", u"style", u"compare")
    os.makedirs(out_dir, exist_ok=True)
    for zuban, name, axis in CASES:
        stem = u"%s_%s" % (zuban, name)
        human = os.path.join(HUMAN_DIR, axis, stem + u".dxf")
        before = os.path.join(BEFORE_DIR, stem + u".dxf")
        after = os.path.join(AFTER_DIR, u"BLIND2-" + zuban, stem + u".dxf")
        panels = [(human, u"人間図面"), (after, u"生成(様式実装後)")] if args.two else [
            (human, u"人間図面"), (before, u"生成(様式実装前)"), (after, u"生成(様式実装後)")]
        missing = [p for p, _t in panels if not os.path.exists(p)]
        if missing:
            print(u"skip(入力なし): %s %s" % (stem, missing))
            continue
        n = len(panels)
        fig, axes = plt.subplots(1, n, figsize=(13.0 * n, 9.2), dpi=110)
        fig.patch.set_facecolor("#12151b")
        for ax, (path, title) in zip(axes, panels):
            render(ax, path, u"%s  %s %s" % (title, zuban, name))
        fig.suptitle(u"赤=中心線 / 白=実線 / 橙=かくれ線 / 緑=寸法    %s %s" % (zuban, name),
                     color="#eeeeee", fontsize=13)
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        out = os.path.join(out_dir, zuban + u"_比較.png")
        fig.savefig(out, facecolor="#12151b")
        plt.close(fig)
        print(u"wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
