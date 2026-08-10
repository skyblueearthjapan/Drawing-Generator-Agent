# -*- coding: utf-8 -*-
u"""様式第3弾(上品さの3層構造)の効果を「人間|改訂前|改訂後」の3面で並べるPNG。

`調査/compare_centerline_png.py` と**同じレンダラー**(手描き方式)を使う。
CLAUDE.md知見「生成図面の見た目を評価する前に必ず人間の実図面を同じレンダラーで描く」に従う。

「改訂前」は様式第2弾+中心線エンジンまでの納品物を `調査/style3/before/` へ凍結したもの
(❗納品箱は再生成で上書きされるので、比較の基準に使ってはいけない)。

実行:
    python 調査/compare_style3_png.py            # 6枚 -> 調査/style3/compare/
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
BEFORE_DIR = os.path.join(ROOT, u"調査", u"style3", u"before")
AFTER_DIR = os.path.join(ROOT, u"data", u"納品箱")

CASES = [
    (u"25154-6-02", u"傾動面板", u"6.傾動軸", u"BLIND-25154-6-02"),
    (u"25154-1-27", u"走行フレーム踏板", u"1.走行軸", u"BLIND2-25154-1-27"),
    (u"25154-2-16", u"指針", u"2.ターン軸", u"BLIND2-25154-2-16"),
    (u"25154-3-02", u"モータブラケット", u"3.昇降軸", u"BLIND2-25154-3-02"),
    (u"25154-4-05", u"駆動ユニットブラケット", u"4.前後軸", u"BLIND2-25154-4-05"),
    (u"25154-5-05", u"減速機フランジ", u"5.ひねり軸", u"BLIND2-25154-5-05"),
]


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(ROOT, u"調査", u"style3", u"compare"))
    args = ap.parse_args(argv[1:])
    os.makedirs(args.out_dir, exist_ok=True)
    for zuban, name, axis, req in CASES:
        stem = u"%s_%s" % (zuban, name)
        panels = [
            (os.path.join(HUMAN_DIR, axis, stem + u".dxf"), u"人間図面"),
            (os.path.join(BEFORE_DIR, stem + u".dxf"), u"生成(改訂前・様式第2弾)"),
            (os.path.join(AFTER_DIR, req, stem + u".dxf"), u"生成(改訂後・様式第3弾)"),
        ]
        missing = [p for p, _t in panels if not os.path.exists(p)]
        if missing:
            print(u"skip(入力なし): %s %s" % (stem, missing))
            continue
        fig, axes = plt.subplots(1, 3, figsize=(13.0 * 3, 9.2), dpi=110)
        fig.patch.set_facecolor("#12151b")
        for ax, (path, title) in zip(axes, panels):
            render(ax, path, u"%s  %s %s" % (title, zuban, name))
        fig.suptitle(u"赤=中心線 / 白=実線 / 橙=かくれ線 / 緑=寸法    %s %s" % (zuban, name),
                     color="#eeeeee", fontsize=13)
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        out = os.path.join(args.out_dir, zuban + u"_比較.png")
        fig.savefig(out, facecolor="#12151b")
        plt.close(fig)
        print(u"wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
