# -*- coding: utf-8 -*-
"""5部品の進み具合を1行ずつ出す(フォアグラウンドのポーリング用)。

    python watch.py            1回だけ表示
    python watch.py <秒> <回>   その間隔で回数ぶん表示して終わる
"""
import json
import sys
import time
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8799"
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
JOBS = json.loads((HERE / "jobs.json").read_text(encoding="utf-8"))
BY_ID = {j["job_id"]: j for j in JOBS}

DONE = ("delivered", "fuda", "failed", "cancelled")


def dga_state(job_id: str) -> str:
    p = DATA / "納品箱" / job_id / "_work" / "dga" / "data" / "依頼箱" / job_id / "status.json"
    if not p.is_file():
        return "-"
    try:
        return str(json.loads(p.read_text(encoding="utf-8")).get("state") or "-")
    except (OSError, ValueError):
        return "?"


def snap() -> bool:
    jobs = {j["id"]: j for j in requests.get(BASE + "/api/jobs", timeout=30).json()}
    finished = 0
    print(time.strftime("[%H:%M:%S]"))
    for jid in sorted(BY_ID):
        j = jobs.get(jid, {})
        st = j.get("status", "?")
        if st in DONE:
            finished += 1
        meta = BY_ID[jid]
        note = (j.get("stage_note") or "").replace("\n", " ")[:64]
        print(f"  {jid} {meta['drawing_no']:<12}{meta['part_name']:<12} "
              f"{st:<11} DGA={dga_state(jid):<7} {note}")
    return finished >= len(BY_ID)


if __name__ == "__main__":
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 0
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    for i in range(rounds):
        if snap():
            print("== 5件とも終わりました ==")
            break
        if i + 1 < rounds:
            time.sleep(interval)
