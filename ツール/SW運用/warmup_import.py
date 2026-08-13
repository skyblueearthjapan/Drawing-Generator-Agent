# -*- coding: utf-8 -*-
"""ワクチン接種: SW新インスタンスの「最初のSTEP取り込み」を小部品で済ませる。

仮説(2026-08-12夜): 各SWインスタンスの初回インポートだけが五分五分で永久ハングし、
一度成功したインスタンスは以後安定する。よって本物のジョブを流す前に、
小さい 1-18.STEP を短タイムアウト付きで取り込んで「安定インスタンス」を確認する。
成功: exit 0 / 失敗・例外: exit 1(呼び出し側が timeout で囲んでハングも检出する)
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = Path(r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

STEP = ROOT / "ツール" / "sample" / "1-18.STEP"

from engine import draw_pipeline as dp  # noqa: E402

sw, mod = dp.connect()
t0 = time.time()
part, info = dp.open_model_readonly(sw, mod, str(STEP))
dt = time.time() - t0
print(f"接種インポート {dt:.1f}s {info}", flush=True)
try:
    part.close()
except Exception as exc:  # noqa: BLE001
    print(f"close で例外(続行): {exc}", flush=True)
left = dp.list_open_docs(sw)
print(f"残り文書: {left}", flush=True)
sys.exit(0 if not left else 1)
