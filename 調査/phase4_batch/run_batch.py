# -*- coding: utf-8 -*-
u"""フェーズ4 量産照合バッチ: STEP → view_orient で向き決定 → 投影 → 人間図面と照合。

    python 調査/phase4_batch/run_batch.py [処理する部品数]      # 既定 3

**チャンク再開設計**: PowerShell 1回の上限が10分なので、1回の実行で数点だけ処理して終了する。
処理済み判定は `調査/phase4_batch/<図番>/result.json` の有無(= 途中で落ちても取りこぼさない)。
SolidWorks が死んでいたら起動して待つ(読み取り専用運用なのでデータ被害は無い)。再起動回数は
`調査/phase4_batch/state.json` に累積する。

安全規約(CLAUDE.md): 教師STEP/DXFは読むだけ・一切保存しない / 自分が開いたものだけ QuitDoc・CloseDoc /
pre/post スナップショットを毎回記録する。
"""
import os
import sys
import io
import json
import time
import subprocess
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # ❗import時に重ね包みしない(compare_views参照)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, u"調査"))

import draw_pipeline as dp            # noqa: E402
import view_orient as vo              # noqa: E402
import compare_views as cv            # noqa: E402

SW_EXE = r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe"
STATE_P = os.path.join(HERE, "state.json")

# --- ゲート④ 合格基準(step_import_report.md §7-5 の提案) ---
TOL_CIRCLE_MM = 0.05      # 円直径差
TOL_SIZE_MM = 0.05        # ビュー外形差
COV_HUMAN_MIN = 1.0       # 人間側被覆(人間が描いた線は全てSW側に在ること)
COV_SW_WARN = 0.85        # SW側被覆(隠れ線の流儀差は警告どまり)


# ------------------------------------------------------------------ 状態
def load_state():
    if os.path.exists(STATE_P):
        with io.open(STATE_P, encoding="utf-8") as f:
            return json.load(f)
    return {"sw_restarts": 0, "runs": []}


def save_state(st):
    with io.open(STATE_P, "w", encoding="utf-8") as f:
        f.write(json.dumps(st, ensure_ascii=False, indent=2))


def ensure_sw(state, wait_s=240):
    u"""SolidWorks に接続する。死んでいたら起動して待つ(再起動回数を state に記録)。"""
    try:
        sw, mod = dp.connect()
        dp.list_open_docs(sw)          # 生きているかを実際に叩いて確かめる
        return sw, mod, False
    except Exception as exc:
        print(u"SW接続失敗(%s)→ 起動する" % exc)
    if not os.path.exists(SW_EXE):
        raise RuntimeError(u"SLDWORKS.exe が無い: %s" % SW_EXE)
    subprocess.Popen([SW_EXE])
    state["sw_restarts"] = state.get("sw_restarts", 0) + 1
    save_state(state)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        time.sleep(10)
        try:
            sw, mod = dp.connect()
            dp.list_open_docs(sw)
            print(u"SW再接続OK(%.0f秒)" % (time.time() - t0))
            return sw, mod, True
        except Exception:
            continue
    raise RuntimeError(u"SolidWorks に再接続できない(%d秒待った)" % wait_s)


# ------------------------------------------------------------------ 判定
#: 人間側の円がSW側の円と「同じ円のつもり」とみなせる上限(これを超えたら対応円なし)
NEAR_CIRCLE_MM = 0.5


def _diam_diff(sw_d, hu_d, tol=TOL_CIRCLE_MM):
    u"""円直径集合の突き合わせ。人間側の各円に最も近いSW側の円との差を採る。

    ❗人間図面に描かれた円が主(=人間側被覆と同じ思想)。SW側にだけある円(隠れ線の穴等)は
      不一致にしない。人間側の円が1つも無い場合は None。
    ❗**ねじ(タップ)の表現円は3Dモデルに実在しない**(M5 なら谷径φ4.134 が図面にだけ在る)。
      対応する円がまるで無い(差 > 0.5mm)ものは「幾何が違う」とは意味が違うので、
      `unmatched` として別に数える(合否は落とすが、傾向分析で切り分けられるようにする)。
    """
    if not hu_d:
        return None, [], []
    pairs = []
    worst = 0.0
    unmatched = []
    for h in hu_d:
        best = min(sw_d, key=lambda s: abs(s - h)) if sw_d else None
        d = abs(best - h) if best is not None else float("inf")
        pairs.append((h, best, (round(d, 4) if best is not None else None)))
        if d > NEAR_CIRCLE_MM:
            unmatched.append(h)
        else:
            worst = max(worst, d)
    return worst, pairs, unmatched


def evaluate(cmpj, plan):
    u"""向きルールの正誤とゲート④の合否を判定する。

    **向きルールの正誤**: 投影は既に view_orient の向きで出しているので、
      人間の front 役ビューが「SWの front を**恒等**で」一致すれば正解。
      対称形状で恒等が同点1位に含まれる場合も正解(原理的に区別できないため)。
    """
    inv = cmpj.get("human_to_sw") or {}
    res = {"gate4": {}, "orientation": {}}

    # --- 人間側の front 役ビュー ---
    front_idx = next((k for k, v in inv.items() if v.get("human_role") == "front"), None)
    if front_idx is None:
        res["orientation"] = {"verdict": u"判定不能", "reason": u"人間図面のfront役ビューが取れない"}
        res["gate4"] = {"verdict": u"判定不能", "reason": u"照合が成立していない"}
        res["verdict"] = u"要調査"
        return res

    f = inv[front_idx]
    ties = f.get("ties") or []
    ident_ok = (f.get("sw_view") == "front" and
                (f.get("transform") == u"恒等" or u"恒等" in ties))
    res["orientation"] = {
        "human_front_idx": front_idx,
        "matched_sw_view": f.get("sw_view"),
        "matched_transform": f.get("transform"),
        "ties": ties,
        "n_ties": len(ties),
        "planned_sw_view": plan["front"]["sw_view"],
        "planned_rotation_deg": plan["front"]["rotation_deg"],
        "correct": bool(ident_ok),
        "ambiguous": bool(ident_ok and len(ties) > 1),
        "verdict": u"正解" if ident_ok else u"不正解",
    }
    if not ident_ok:
        res["orientation"]["miss_kind"] = (
            u"別ビュー方向(%s)" % f.get("sw_view") if f.get("sw_view") != "front"
            else u"紙面内回転(%s)" % f.get("transform"))

    # --- ゲート④(人間の全部品ビューについて) ---
    rows = []
    worst = {"iou": 1.0, "size_err": 0.0, "circle": 0.0, "cov_human": 1.0, "cov_sw": 1.0}
    mirror = False
    unmatched_all = []
    for k, v in sorted(inv.items(), key=lambda kv: int(kv[0])):
        cd, cpairs, unm = _diam_diff(v.get("sw_circle_d") or [], v.get("human_circle_d") or [])
        rows.append({"human_idx": k, "role": v.get("human_role"), "sw_view": v.get("sw_view"),
                     "transform": v.get("transform"), "det": v.get("det"),
                     "iou": v.get("iou"), "cov_sw": v.get("cov_sw"),
                     "cov_human": v.get("cov_human"), "size_err_mm": v.get("size_err_mm"),
                     "circle_diff_mm": cd, "circle_pairs": cpairs,
                     "circle_unmatched_human": unm})
        worst["iou"] = min(worst["iou"], v.get("iou") or 0.0)
        worst["size_err"] = max(worst["size_err"], v.get("size_err_mm") or 0.0)
        worst["cov_human"] = min(worst["cov_human"], v.get("cov_human") or 0.0)
        worst["cov_sw"] = min(worst["cov_sw"], v.get("cov_sw") or 0.0)
        if cd is not None:
            worst["circle"] = max(worst["circle"], cd)
        unmatched_all += [(k, d) for d in unm]
        if v.get("mirror_required"):
            mirror = True

    fails = []
    if mirror:
        fails.append(u"鏡像が最良(det=-1)→即停止")
    if worst["size_err"] > TOL_SIZE_MM:
        fails.append(u"外形差 %.4fmm > %.2f" % (worst["size_err"], TOL_SIZE_MM))
    if worst["circle"] > TOL_CIRCLE_MM:
        fails.append(u"円直径差 %.4fmm > %.2f" % (worst["circle"], TOL_CIRCLE_MM))
    if worst["cov_human"] < COV_HUMAN_MIN - 1e-9:
        fails.append(u"人間側被覆 %.4f < 1.0000" % worst["cov_human"])
    if unmatched_all:
        fails.append(u"SW側に対応円が無い人間側の円 %d 個: %r(ねじ表現の可能性)"
                     % (len(unmatched_all), [round(d, 3) for _, d in unmatched_all[:6]]))
    warns = []
    if worst["cov_sw"] < COV_SW_WARN:
        warns.append(u"SW側被覆 %.4f < %.2f(隠れ線の流儀差の可能性)" % (worst["cov_sw"], COV_SW_WARN))

    res["gate4"] = {"views": rows, "worst": worst, "mirror_required": mirror,
                    "unmatched_human_circles": [round(d, 3) for _, d in unmatched_all],
                    "fails": fails, "warns": warns,
                    "verdict": u"合格" if not fails else u"不合格"}
    res["verdict"] = u"合格" if (not fails and res["orientation"]["correct"]) else (
        u"向き外し" if (not fails) else u"不合格")
    return res


# ------------------------------------------------------------------ 1部品
#: 「何もしない」= STEPの座標系をそのまま使う対照実験用の提示フレーム(恒等)
IDENTITY_FRAME = {"ex": (1, 0, 0), "ey": (0, 1, 0), "ez": (0, 0, 1),
                  "ex_axis": "X", "ez_axis": "Z", "rule": u"[対照] STEPの座標系そのまま"}


def process(sw, mod, tgt, out_dir, tangent=dp.swTangentEdgesHidden, baseline=False,
            rule=None):
    u"""1部品: STEP → 面情報 → view_orient → 投影 → DXF → 照合 → 判定。

    `baseline=True` は**対照実験**: view_orient を使わず SWの標準ビューをそのまま出す
    (= STEPの座標系をそのまま信じる)。ルールが「何もしない」に勝っているかを測るために要る。
    """
    step = os.path.join(ROOT, tgt["step"])
    human = os.path.join(ROOT, tgt["human_dxf"])
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    dxf_p = os.path.join(out_dir, "views.dxf")
    meta_p = os.path.join(out_dir, "meta.json")
    cmp_p = os.path.join(out_dir, "compare.json")

    meta = {"key": tgt["key"], "name": tgt["name"], "axis": tgt["axis"],
            "step": tgt["step"], "human_dxf": tgt["human_dxf"],
            "bucket_a": tgt["bucket_a"], "tangent_mode": tangent,
            "t_start": time.strftime("%Y-%m-%d %H:%M:%S")}
    part = dwgdoc = None
    try:
        meta["pre_open"] = dp.list_open_docs(sw)
        part, info = dp.open_model_readonly(sw, mod, step)
        meta["open_info"] = info
        meta["doc_title"] = part.title
        m = dp.part_metrics(mod, part)
        meta["metrics"] = m
        print(u"  bbox=%r vol=%.1f bodies=%d" % (
            [round(v, 3) for v in m["size_mm"]], m["volume_mm3"], m["body_count"]))

        # --- ★正面ビュー選定 ---
        orient = vo.plan_for_part(mod, part, dp.prop, m["size_mm"], rule=rule)
        if baseline:
            orient["frame"] = IDENTITY_FRAME
            orient["view_plan"] = vo.view_plan(IDENTITY_FRAME)
            orient["baseline"] = True
        meta["view_orient"] = orient
        cls = orient["classification"]
        plan = orient["view_plan"]
        print(u"  形状クラス=%s (%s)" % (cls["shape_class"], cls["reason"]))
        print(u"  向き: ex=%s ez=%s / %s" % (
            orient["frame"]["ex_axis"], orient["frame"]["ez_axis"], orient["frame"]["rule"]))
        print(u"  ビュー割: " + " ".join(
            u"%s=%s%+d度" % (k, plan[k]["sw_view"], plan[k]["rotation_deg"])
            for k in ("front", "top", "right")))

        dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
        meta["sheet"] = dp.sheet_info(mod, sheet)
        views = dp.insert_standard_views(mod, dwg, part.model_name, m["size_mm"],
                                         tangent=tangent, plan=plan)
        meta["views"] = views
        dwgdoc.doc.ForceRebuild3(False)
        dwgdoc.doc.ViewZoomtofit2()
        res = dp.export_dxf(sw, dwgdoc, dxf_p)
        meta["export"] = {"path": res["path"], "bytes": res["bytes"]}
        meta["projection_ok"] = True
    except Exception:
        meta["projection_ok"] = False
        meta["error"] = traceback.format_exc()
        print(meta["error"])
    finally:
        for od, lab in ((dwgdoc, u"図面"), (part, u"部品")):
            if od is None:
                continue
            try:
                od.close()
            except Exception as e:
                meta.setdefault("close_errors", []).append(u"%s: %s" % (lab, e))
        post = dp.list_open_docs(sw)
        meta["post_open"] = post
        pre_t = [t for t, _ in (meta.get("pre_open") or [])]
        meta["leaked_docs"] = [t for t, _ in post if t not in pre_t]

    # --- 照合(SWを触らないのでここから先は落ちてもSWに影響しない) ---
    if meta.get("projection_ok"):
        try:
            cmpj = cv.run_compare(tgt["key"], dxf_p, meta, human,
                                  use_frame=bool(tgt["bucket_a"]), out_path=cmp_p)
            meta["compare_ok"] = True
            meta["verdict"] = evaluate(cmpj, meta["view_orient"]["view_plan"])
        except Exception:
            meta["compare_ok"] = False
            meta["compare_error"] = traceback.format_exc()
            meta["verdict"] = {"verdict": u"照合破綻",
                               "reason": meta["compare_error"].strip().splitlines()[-1]}
            print(meta["compare_error"])
    else:
        meta["compare_ok"] = False
        meta["verdict"] = {"verdict": u"投影失敗",
                           "reason": (meta.get("error") or "").strip().splitlines()[-1:]}

    meta["t_end"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with io.open(meta_p, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    # result.json = 「この部品は処理済み」の印(再開判定に使う)
    with io.open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "key": tgt["key"], "name": tgt["name"], "axis": tgt["axis"],
            "bucket_a": tgt["bucket_a"],
            "shape_class": (meta.get("view_orient") or {}).get(
                "classification", {}).get("shape_class"),
            "reason": (meta.get("view_orient") or {}).get("classification", {}).get("reason"),
            "frame": (meta.get("view_orient") or {}).get("frame"),
            "view_plan": {k: {"sw_view": v["sw_view"], "rotation_deg": v["rotation_deg"]}
                          for k, v in ((meta.get("view_orient") or {})
                                       .get("view_plan", {})).items()},
            "size_mm": (meta.get("metrics") or {}).get("size_mm"),
            "projection_ok": meta.get("projection_ok"),
            "compare_ok": meta.get("compare_ok"),
            "verdict": meta.get("verdict"),
        }, ensure_ascii=False, indent=2, default=str))
    return meta


def recompare(targets):
    u"""保存済みの views.dxf / meta.json だけで照合と判定をやり直す(SolidWorks不要)。

    照合ロジック(役割推定・尺度換算・ゲート④)を直したときに、投影をやり直さずに
    30点ぶんの判定を作り直せる。投影は決定論なので結果は同一になる。
    """
    n = 0
    for t in targets:
        d = os.path.join(HERE, t["key"])
        meta_p = os.path.join(d, "meta.json")
        dxf_p = os.path.join(d, "views.dxf")
        if not (os.path.exists(meta_p) and os.path.exists(dxf_p)):
            continue
        with io.open(meta_p, encoding="utf-8") as f:
            meta = json.load(f)
        if not meta.get("projection_ok"):
            continue
        print(u"\n===== [%s] %s =====" % (t["key"], t["name"]))
        try:
            cmpj = cv.run_compare(t["key"], dxf_p, meta, os.path.join(ROOT, t["human_dxf"]),
                                  use_frame=bool(t["bucket_a"]),
                                  out_path=os.path.join(d, "compare.json"))
            meta["compare_ok"] = True
            meta["verdict"] = evaluate(cmpj, meta["view_orient"]["view_plan"])
        except Exception:
            meta["compare_ok"] = False
            meta["compare_error"] = traceback.format_exc()
            meta["verdict"] = {"verdict": u"照合破綻",
                               "reason": meta["compare_error"].strip().splitlines()[-1]}
            print(meta["compare_error"])
        with io.open(meta_p, "w", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
        rp = os.path.join(d, "result.json")
        with io.open(rp, encoding="utf-8") as f:
            res = json.load(f)
        res["compare_ok"] = meta["compare_ok"]
        res["verdict"] = meta["verdict"]
        with io.open(rp, "w", encoding="utf-8") as f:
            f.write(json.dumps(res, ensure_ascii=False, indent=2, default=str))
        v = (meta.get("verdict") or {}).get("verdict")
        o = (meta.get("verdict") or {}).get("orientation", {}).get("verdict")
        print(u"  → 判定=%s / 向き=%s" % (v, o))
        n += 1
    print(u"\n再照合 %d 点" % n)
    return 0


# ------------------------------------------------------------------ main
def main():
    with io.open(os.path.join(HERE, "targets.json"), encoding="utf-8") as f:
        targets = json.load(f)["targets"]
    if "--recompare" in sys.argv:
        return recompare(targets)
    baseline = "--baseline" in sys.argv
    rule = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--rule=")), None)
    base_dir = HERE
    if baseline:
        base_dir = os.path.join(HERE, "_baseline")
    elif rule and rule != vo.RULE_VERSION_DEFAULT:
        base_dir = os.path.join(HERE, "_" + rule)
    n_want = int(next((a for a in sys.argv[1:] if not a.startswith("-")), 3))
    state = load_state()

    pending = [t for t in targets
               if not os.path.exists(os.path.join(base_dir, t["key"], "result.json"))]
    print(u"対象 %d 点 / 未処理 %d 点 / 今回 %d 点" % (len(targets), len(pending), n_want))
    if not pending:
        print(u"全部処理済み")
        return 0

    sw, mod, restarted = ensure_sw(state)
    if restarted:
        print(u"※SWを再起動した(累計 %d 回)" % state["sw_restarts"])

    done = 0
    for t in pending[:n_want]:
        print(u"\n===== [%s] %s (%s) =====" % (t["key"], t["name"], t["axis"]))
        t0 = time.time()
        try:
            meta = process(sw, mod, t, os.path.join(base_dir, t["key"]),
                           baseline=baseline, rule=rule)
            v = (meta.get("verdict") or {}).get("verdict")
            o = (meta.get("verdict") or {}).get("orientation", {}).get("verdict")
            print(u"  → 判定=%s / 向き=%s (%.1f秒)" % (v, o, time.time() - t0))
        except Exception:
            traceback.print_exc()
            print(u"  → 例外。SWの生死を確認して次のチャンクで再開すること")
            break
        done += 1
    state.setdefault("runs", []).append(
        {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "processed": done})
    save_state(state)
    print(u"\n今回 %d 点処理。残り %d 点" % (done, len(pending) - done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
