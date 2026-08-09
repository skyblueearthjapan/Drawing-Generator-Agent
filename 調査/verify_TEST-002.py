# -*- coding: utf-8 -*-
u"""【廃止】TEST-002専用の独立検証スクリプト。

2026-08-09 の流儀確定(円形ビューの外径=ネイティブDIAMETER型 / 穴注記=キリ表記・全角)により
本スクリプトの判定条件(kind=='diameter_linear' 前提・穴注記の全角禁止チェック)が
**現行仕様に対して誤検出を出す**ため、部品非依存の `調査/verify_generated.py` へ統合した。

    python 調査/verify_generated.py TEST-002

このファイルは互換のため残してあり、上記へ委譲するだけ。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import verify_generated  # noqa: E402


if __name__ == "__main__":
    sys.exit(verify_generated.main(["verify_generated.py", "TEST-002"]))
