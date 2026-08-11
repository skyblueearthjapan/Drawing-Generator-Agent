# -*- coding: utf-8 -*-
"""ランダム5部品を 8799 の工房へ順次投入する(日本語はインライン引数で渡さない)。"""
import json
import sys
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from targets import targets  # noqa: E402

BASE = "http://127.0.0.1:8799"
OUT = Path(__file__).resolve().parent / "jobs.json"

submitted = []
for t in targets():
    data = {
        "kind": "drawing",
        "requester": "今泉 勇二",
        "note": t["note"],
        "drawing_no": t["drawing_no"],
        "part_name": t["part_name"],
        "machine_name": t["machine_name"],
        "material": t["material"],
        "quantity": t["quantity"],
        "draftsman": t["draftsman"],
    }
    with open(t["step"], "rb") as f:
        r = requests.post(BASE + "/api/jobs", data=data,
                          files={"files": (t["step"].name, f, "application/octet-stream")},
                          timeout=120)
    print(f"{t['drawing_no']:<12} HTTP {r.status_code} {r.text[:200]}")
    r.raise_for_status()
    job_id = r.json()["id"]
    j = requests.get(f"{BASE}/api/jobs/{job_id}", timeout=30).json()
    print(f"   job={job_id} kind={j['kind']} status={j['status']} "
          f"request={json.dumps(j['results']['request'], ensure_ascii=False)}")
    submitted.append({"job_id": job_id, "key": t["key"], "drawing_no": t["drawing_no"],
                      "part_name": t["part_name"], "machine_name": t["machine_name"],
                      "step": str(t["step"])})

OUT.write_text(json.dumps(submitted, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n{len(submitted)} 件を投入しました -> {OUT}")
