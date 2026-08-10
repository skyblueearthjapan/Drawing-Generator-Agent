# -*- coding: utf-8 -*-
u"""中心線ゲート(ゲート③の機械化第一歩)の**反証テスト**。

「中心線を意図的に欠落/劣化させたら必ず不合格になる」ことを機械確認する。
検出できないケースが1件でもあれば、そのゲートは通っても意味がない。

反証ケース(生成済み図面ごとに実施):
  C0 baseline          : 中心線を正しく足した状態      -> 合格でなければならない
  C1 全削除            : 中心線を全部消す              -> 不合格(全フィーチャー未被覆)
  C2 1本消し(水平)     : **穴の中心を通る**水平中心線を1本消す -> 不合格(被覆h欠落)
  C3 1本消し(垂直)     : **穴の中心を通る**垂直中心線を1本消す -> 不合格(被覆v欠落)
  C4 短縮(中心マーク化) : 全中心線を円の内側だけへ縮める -> 不合格(延長不足)
  C5 直線を全消し       : 直線を全部消して円だけ残す     -> 不合格
  C6 図枠侵入          : 中心線を1本、表題欄まで伸ばす   -> 不合格(zone_hits)
  C7 中心線を実線化    : 線種をCONTINUOUSに変える       -> 不合格(中心線として数えない)
  C8 穴軸マーク消し    : **穴を通らない**中心線を1本消す -> 不合格(設計未実現)
                          ❗被覆判定だけでは素通りする穴。完全性判定が捕まえること
  C9 PCD参照円消し     : 中心線のCIRCLEを1つ消す        -> 不合格(設計未実現)

実行:
    python 調査/run_centerline_falsification.py [--out 調査/centerline_falsification.json]
"""
import argparse
import io
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import ezdxf  # noqa: E402
from engine import centerline_gen as cg  # noqa: E402
from engine import dim_engine  # noqa: E402

WORK = os.path.join(ROOT, u"調査", u"centerline", u"falsification")
# ❗反証の出発点は**中心線を1本も持たない図面**でなければならない。
#   納品箱のDXFは既に中心線入りへ更新され得るので、そちらを種にすると
#   「消した1本が今回の設計に含まれない古い中心線」になり、検出されないのは当然になる
#   (実測でこの罠に落ちた)。run_centerline_blind2.py が作る中心線OFFの生成物を使う。
SRC_DIR = os.path.join(ROOT, u"調査", u"centerline", u"blind2_off")
FALLBACK_DIR = os.path.join(ROOT, "data", u"納品箱")

CASES = [
    (u"BLIND2-25154-3-02", u"25154-3-02_モータブラケット.dxf"),
    (u"BLIND2-25154-5-05", u"25154-5-05_減速機フランジ.dxf"),
    (u"BLIND2-25154-4-05", u"25154-4-05_駆動ユニットブラケット.dxf"),
    (u"BLIND2-25154-1-27", u"25154-1-27_走行フレーム踏板.dxf"),
    (u"BLIND2-25154-2-16", u"25154-2-16_指針.dxf"),
]


def source_dxf(req_id, dxf_name):
    p = os.path.join(SRC_DIR, dxf_name)
    if os.path.exists(p):
        return p
    return os.path.join(FALLBACK_DIR, req_id, dxf_name)


def centerline_entities(doc):
    return [e for e in doc.modelspace()
            if e.dxftype() in ("LINE", "CIRCLE") and dim_engine.is_centerline(e)]


def solid_circle_centers(doc):
    u"""中心線でない実ジオメトリの円の中心(=被覆判定の対象になる位置)。

    ❗SW投影の円は**4分割ARC**で出るので CIRCLE だけを見てはいけない
    (実測: 25154-2-16 の modelspace に CIRCLE は図枠の連番マーク1個しか無い)。
    `centerline_gen.collect_circles` を使って ARC 合成円も拾う。
    """
    ents = [e for e in doc.modelspace() if not dim_engine.is_centerline(e)]
    return cg.collect_circles(ents)


def _line_dir(e):
    s, t = e.dxf.start, e.dxf.end
    return "h" if abs(s.y - t.y) < 1e-6 else "v"


def _passes_a_circle(e, centers):
    s, t = e.dxf.start, e.dxf.end
    d = _line_dir(e)
    for cx, cy, _r in centers:
        if d == "h" and abs(s.y - cy) < 0.05 and min(s.x, t.x) <= cx <= max(s.x, t.x):
            return True
        if d == "v" and abs(s.x - cx) < 0.05 and min(s.y, t.y) <= cy <= max(s.y, t.y):
            return True
    return False


def mutate(src, dst, how):
    doc = ezdxf.readfile(src)
    msp = doc.modelspace()
    ents = centerline_entities(doc)
    lines = [e for e in ents if e.dxftype() == "LINE"]
    ccircles = [e for e in ents if e.dxftype() == "CIRCLE"]
    centers = solid_circle_centers(doc)
    n = 0
    if how == "C1_delete_all":
        for e in ents:
            msp.delete_entity(e)
            n += 1
    elif how in ("C2_drop_h", "C3_drop_v"):
        want = "h" if how == "C2_drop_h" else "v"
        for e in lines:
            if _line_dir(e) == want and _passes_a_circle(e, centers):
                msp.delete_entity(e)
                n = 1
                break
    elif how == "C8_drop_mark":
        for e in lines:
            if not _passes_a_circle(e, centers):
                msp.delete_entity(e)
                n = 1
                break
    elif how == "C9_drop_pcd":
        if ccircles:
            msp.delete_entity(ccircles[0])
            n = 1
    elif how == "C4_shrink":
        # 中心線を「中心マーク程度」に縮める(円の外へ出ない)
        for e in lines:
            s, t = e.dxf.start, e.dxf.end
            cx, cy = (s.x + t.x) / 2.0, (s.y + t.y) / 2.0
            e.dxf.start = (cx - (cx - s.x) * 0.05, cy - (cy - s.y) * 0.05)
            e.dxf.end = (cx + (t.x - cx) * 0.05, cy + (t.y - cy) * 0.05)
            n += 1
    elif how == "C5_lines_gone":
        for e in lines:
            msp.delete_entity(e)
            n += 1
    elif how == "C6_frame_intrusion":
        for e in lines:
            s, t = e.dxf.start, e.dxf.end
            if abs(s.x - t.x) < 1e-6:      # 垂直線を表題欄まで下ろす
                e.dxf.start = (s.x, 8.0)
                n = 1
                break
    elif how == "C7_continuous":
        for e in ents:
            e.dxf.linetype = "Continuous"
            n += 1
    else:
        raise ValueError(how)
    doc.saveas(dst)
    return n


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, u"調査",
                                                  u"centerline_falsification.json"))
    a = ap.parse_args(argv[1:])

    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK, exist_ok=True)

    results = []
    for req_id, dxf_name in CASES:
        plan = os.path.join(ROOT, "data", u"依頼箱", req_id, "plan.json")
        src = source_dxf(req_id, dxf_name)
        if not (os.path.exists(plan) and os.path.exists(src)):
            results.append({"case": req_id, "error": u"入力が無い", "plan": plan, "dxf": src})
            continue
        base = os.path.join(WORK, req_id + "_base.dxf")
        shutil.copyfile(src, base)
        gen = cg.add_centerlines(base, plan)
        chk = cg.check_centerlines(base, plan)
        rec = {"case": req_id, "added": gen["added"], "counts": gen["counts"],
               "C0_baseline": {"ok": chk["ok"], "n_features": chk["n_features"],
                               "n_missing": chk["n_missing"],
                               "n_centerline_entities": chk["n_centerline_entities"]},
               "mutations": {}}
        for how in ("C1_delete_all", "C2_drop_h", "C3_drop_v", "C4_shrink",
                    "C5_lines_gone", "C6_frame_intrusion", "C7_continuous",
                    "C8_drop_mark", "C9_drop_pcd"):
            dst = os.path.join(WORK, "%s_%s.dxf" % (req_id, how))
            n = mutate(base, dst, how)
            m = cg.check_centerlines(dst, plan)
            rec["mutations"][how] = {
                "mutated_entities": n, "ok": m["ok"], "n_missing": m["n_missing"],
                "n_not_realized": m["n_not_realized"],
                "n_zone_hits": len(m["zone_hits"]),
                # ❗変異させる対象が無かったケース(例: PCD参照円を持たない図面)は
                #   反証にならないので「該当なし」として集計から外す
                "applicable": n > 0,
                "detected": (not m["ok"]) if n > 0 else None,
            }
        results.append(rec)

    n_base_ok = sum(1 for r in results if r.get("C0_baseline", {}).get("ok"))
    muts = [(r["case"], k, v["detected"]) for r in results
            for k, v in r.get("mutations", {}).items() if v["applicable"]]
    n_skip = sum(1 for r in results for v in r.get("mutations", {}).values()
                 if not v["applicable"])
    n_det = sum(1 for _c, _k, d in muts if d)
    summary = {"cases": len(results), "baseline_ok": n_base_ok,
               "mutations_total": len(muts), "mutations_detected": n_det,
               "not_applicable": n_skip,
               "undetected": [(c, k) for c, k, d in muts if not d]}
    with io.open(a.out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"summary": summary, "results": results},
                           ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in results:
        if "error" in r:
            print(" ERR", r["case"], r["error"])
            continue
        print(" %-22s base_ok=%s feats=%d 中心線%d本 %s"
              % (r["case"], r["C0_baseline"]["ok"], r["C0_baseline"]["n_features"],
                 r["added"], r["counts"]))
        for k, v in r["mutations"].items():
            mark = "-" if not v["applicable"] else ("○" if v["detected"] else "×")
            print("    %-20s 検出=%s (未被覆%d件/設計未実現%d件/図枠侵入%d件)"
                  % (k, mark, v["n_missing"], v["n_not_realized"], v["n_zone_hits"]))
    print("\n出力:", a.out)
    return 0 if (n_base_ok == len(results) and n_det == len(muts)) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
