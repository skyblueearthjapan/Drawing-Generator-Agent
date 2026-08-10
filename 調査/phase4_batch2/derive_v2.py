# -*- coding: utf-8 -*-
u"""v2 の投影を**最小限**にするための前処理(SolidWorks不要)。

`presentation_frame_v2` は仕様上
  - 旋盤物でない、または主軸がX  → **恒等**(= `--baseline` の対照実験と完全に同じフレーム)
  - 旋盤物で主軸Y                → ex=+Y, ez=+Z(= v1 の lathe 規則と完全に同じフレーム)
  - 旋盤物で主軸Z                → ex=-Z, ez=+X(**v1でも対照でも出していない新しい向き**)
なので、**投影が要るのは最後のケースだけ**。残りは既存の投影結果をそのまま v2 の結果として使える。

このスクリプトは各部品の v2 フレームを meta.json の分類結果から再計算し、
一致する既存条件の成果物を `_v2/<図番>/` へ複製する(複製したものには `v2_source` を記す)。
その後 `run_batch.py --rule=v2` を回せば、未処理として残った部品だけが投影される。

    python 調査/phase4_batch2/derive_v2.py [--dir=調査/phase4_batch2]
"""
import os
import sys
import io
import json
import shutil

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "engine"))
import view_orient as vo                    # noqa: E402

COPY_FILES = ("views.dxf", "meta.json", "compare.json", "result.json")


def frame_key(fr):
    u"""フレームを比較可能なタプルに(ex/ez だけで一意に決まる。ey は従属)。"""
    return (tuple(fr["ex"]), tuple(fr["ez"]))


def main():
    work = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--dir=")), None)
    work = HERE if not work else (work if os.path.isabs(work) else os.path.join(ROOT, work))
    with io.open(os.path.join(work, "targets.json"), encoding="utf-8") as f:
        targets = json.load(f)["targets"]
    v2_dir = os.path.join(work, "_v2")
    identity = frame_key({"ex": (1, 0, 0), "ez": (0, 0, 1)})

    need, reused = [], []
    for t in targets:
        key = t["key"]
        v1_meta_p = os.path.join(work, key, "meta.json")
        if not os.path.exists(v1_meta_p):
            print(u"  [%s] v1 未処理 → スキップ" % key)
            continue
        with io.open(v1_meta_p, encoding="utf-8") as f:
            meta = json.load(f)
        cls = (meta.get("view_orient") or {}).get("classification") or {}
        if not cls:
            print(u"  [%s] 分類が無い(投影失敗)→ 投影しなおし" % key)
            need.append(key)
            continue
        ma = cls.get("main_axis")
        ev = {"shape_class": cls.get("shape_class"),
              "_main_axis_idx": (list(vo.AXIS_NAME).index(ma) if ma else None)}
        fr2 = vo.presentation_frame_v2(ev, cls.get("size_mm") or [1, 1, 1])
        k2 = frame_key(fr2)
        v1_frame = frame_key((meta.get("view_orient") or {}).get("frame"))

        if k2 == identity:
            src, why = os.path.join(work, "_baseline", key), u"恒等 = 対照実験と同一"
        elif k2 == v1_frame:
            src, why = os.path.join(work, key), u"v1 と同一フレーム(ex=%s ez=%s)" % (
                fr2["ex_axis"], fr2["ez_axis"])
        else:
            need.append(key)
            print(u"  [%s] %-20s → **投影が要る** (%s / ex=%s ez=%s)"
                  % (key, t["name"][:20], cls.get("shape_class"),
                     fr2["ex_axis"], fr2["ez_axis"]))
            continue

        dst = os.path.join(v2_dir, key)
        if not os.path.isdir(dst):
            os.makedirs(dst)
        if not os.path.exists(os.path.join(src, "result.json")):
            print(u"  [%s] 流用元が未処理: %s" % (key, src))
            need.append(key)
            continue
        for fn in COPY_FILES:
            p = os.path.join(src, fn)
            if os.path.exists(p):
                shutil.copyfile(p, os.path.join(dst, fn))
        # 流用したことを成果物に明記する(あとから見て取り違えないように)
        rp = os.path.join(dst, "result.json")
        with io.open(rp, encoding="utf-8") as f:
            res = json.load(f)
        res["rule_version"] = "v2"
        res["v2_source"] = {"dir": os.path.relpath(src, ROOT), "reason": why}
        with io.open(rp, "w", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        reused.append((key, why))

    print(u"\n流用 %d 点 / 要投影 %d 点 %r" % (len(reused), len(need), need))
    n_base = sum(1 for _, w in reused if u"対照" in w)
    print(u"  うち 対照流用 %d 点 / v1流用 %d 点" % (n_base, len(reused) - n_base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
