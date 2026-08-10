# -*- coding: utf-8 -*-
u"""フェーズ4 量産照合バッチの対象30部品を決める(決定論・再実行しても同じ並び)。

方針(タスク指定)
  - 棚卸し matched(128点)から**軸をまたいでバランスよく**30点
  - **バケットA図面を優先**しつつ、非バケットAも数点混ぜる
  - 人間DXFのエンティティ数を測っておく(照合の重さ=時間予算の見積り)

    python 調査/phase4_batch/pick_targets.py
"""
import os
import sys
import io
import json
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, u"荏原トライ調整用")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

N_TOTAL = 30
N_NON_A = 6          # 非バケットAをこれだけ混ぜる


def load():
    with io.open(os.path.join(ROOT, u"調査", u"教師STEP棚卸し.json"), encoding="utf-8") as f:
        inv = json.load(f)
    with io.open(os.path.join(ROOT, u"調査", u"bucket_A_files.json"), encoding="utf-8") as f:
        bucket = json.load(f)
    a_names = set()
    for _, files in bucket.items():
        for p in files:
            a_names.add(os.path.basename(p))
    return inv, a_names


def main():
    inv, a_names = load()
    rows = []
    for m in inv["matched"]:
        step_rel = m["step"]
        axis = step_rel.split(u"\\")[1]
        dxf_name = m["dxf"][0]
        dxf_rel = os.path.join(u"DXF", u"部品表用DXFデータ", axis, dxf_name)
        step_abs = os.path.join(DATA, step_rel)
        dxf_abs = os.path.join(DATA, dxf_rel)
        if not (os.path.exists(step_abs) and os.path.exists(dxf_abs)):
            continue
        rows.append({
            "key": m[u"図番キー"],
            "axis": axis,
            "step": os.path.relpath(step_abs, ROOT),
            "human_dxf": os.path.relpath(dxf_abs, ROOT),
            "name": os.path.splitext(dxf_name)[0].split("_", 1)[-1],
            "bucket_a": dxf_name in a_names,
            "step_bytes": os.path.getsize(step_abs),
            "dxf_bytes": os.path.getsize(dxf_abs),
        })
    rows.sort(key=lambda r: (r["axis"], r["key"]))

    by_axis = defaultdict(list)
    for r in rows:
        by_axis[r["axis"]].append(r)
    axes = sorted(by_axis)
    print(u"軸別: " + ", ".join(u"%s=%d(A%d)" % (
        a, len(by_axis[a]), sum(1 for r in by_axis[a] if r["bucket_a"])) for a in axes))

    # --- バケットA を軸ラウンドロビンで、小さいSTEPから(重い部品で時間を溶かさない) ---
    # 同じ品名(指針・押しボルト座・クッション等)は軸をまたいで重複するので、
    # 形状の多様性を稼ぐため**未出の品名を優先**する(枯れたら重複も許す)
    picked, seen, names = [], set(), set()

    def take(pool_pred, n_target):
        pools = {a: [r for r in sorted(by_axis[a], key=lambda r: r["step_bytes"])
                     if pool_pred(r) and r["key"] not in seen] for a in axes}
        for pass_no in (0, 1):
            while len(picked) < n_target:
                moved = False
                for a in axes:
                    if len(picked) >= n_target:
                        break
                    pool = pools[a]
                    idx = next((i for i, r in enumerate(pool)
                                if pass_no or r["name"] not in names), None)
                    if idx is None:
                        continue
                    r = pool.pop(idx)
                    picked.append(r)
                    seen.add(r["key"])
                    names.add(r["name"])
                    moved = True
                if not moved:
                    break

    take(lambda r: r["bucket_a"], N_TOTAL - N_NON_A)
    take(lambda r: not r["bucket_a"], N_TOTAL)
    take(lambda r: True, N_TOTAL)

    picked.sort(key=lambda r: (r["axis"], r["key"]))
    for i, r in enumerate(picked):
        r["order"] = i
        print(u"[%2d] %-8s %-12s %-28s %s step=%6dKB dxf=%5dKB" % (
            i, r["key"], r["axis"], r["name"][:28],
            u"A " if r["bucket_a"] else u"非A", r["step_bytes"] // 1024,
            r["dxf_bytes"] // 1024))
    print(u"合計 %d 点 / バケットA %d 点 / 非A %d 点" % (
        len(picked), sum(1 for r in picked if r["bucket_a"]),
        sum(1 for r in picked if not r["bucket_a"])))

    p = os.path.join(OUT_DIR, "targets.json")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"targets": picked}, ensure_ascii=False, indent=2))
    print(u"保存:", p)


if __name__ == "__main__":
    main()
