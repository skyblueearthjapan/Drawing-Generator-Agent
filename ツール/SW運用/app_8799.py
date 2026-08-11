# -*- coding: utf-8 -*-
"""ランダム5部品 統合システムテスト用のアプリ起動。

    python app_8799.py <データルート> <ポート>

安全規約: 本番ポート 8788 / 本番DB(リポジトリの data/)には一切触らない。
db の定数を connect() より前に一時データルートへ差し替えてから uvicorn を起こす
(worker/tests/test_ref_files.py の setup_sandbox と同じ作法)。
"""
import sys
from pathlib import Path

REPO = Path(r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3D CAD Operator Agent")
sys.path.insert(0, str(REPO))

data_root = Path(sys.argv[1]).resolve()
port = int(sys.argv[2])
assert port != 8788, "本番ポートは使わない"

from phase2.app import db  # noqa: E402

db._conn = None
db.DATA_DIR = data_root
db.DB_PATH = data_root / "workshop.db"
db.INBOX = data_root / "依頼箱"
db.OUTBOX = data_root / "納品箱"
db.BOXES = {"依頼箱": db.INBOX, "納品箱": db.OUTBOX}
data_root.mkdir(parents=True, exist_ok=True)

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import uvicorn  # noqa: E402

from phase2.app import server  # noqa: E402

print(f"random5 app: data={data_root} port={port}", flush=True)
uvicorn.run(server.app, host="127.0.0.1", port=port, log_level="warning")
