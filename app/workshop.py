# -*- coding: utf-8 -*-
u"""工房ループ CLI(フェーズ5・依頼受付→処理→納品→台帳)。

    python app/workshop.py scan     依頼箱を走査し、決定論で進められる段を全部進める
    python app/workshop.py status   全依頼の状態一覧を表示

思想: 1依頼 = data/依頼箱/<依頼ID>/ フォルダ。状態機械を status.json に持ち、
再scanのたびに「今の状態から次へ進めるか」だけを判定して1段ずつ進める(冪等・再試行可能)。

状態遷移:
    受付済 -> 計測中 -> 候補提示中 -> 計画待ち -> 生成中 -> 合格
                                          (生成中 -> 不合格 もあり得る)
    (エラー時は状態を変えずに status.json の errors[] に記録して次の依頼へ進む)

このループは **AIの判断(向き選択・寸法計画)を含まない**。「計画待ち」で必ず止まり、
後段のAIオペレータ(Claude)が候補PNG(候補.png)とmeas.jsonを見て
choice.json(向き選択)/plan.json(作図計画)をこのフォルダに置くのを待つ
(`app/prompts/orientation_prompt.md` / `plan_prompt.md` がそのAIへの指示雛形)。

安全規約:
  - engine/*.py・図枠/・生成図面/ は一切書き換えない(読むだけ)。
  - engine/generate_drawing.py はブラックボックスとして CLI 経由でのみ呼ぶ。
  - 調査/phase5_ai_operator/ の各スクリプトも同様に CLI 経由でのみ呼ぶ(中身は改変しない)。
  - 人間図面(荏原トライ調整用/DXF)への参照はこのファイルに一切書かない(汚染遮断)。
"""
import argparse
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # app/ 自身(candidates.py用)

import view_orient  # noqa: E402  (純粋関数のみ使用。SW接続はしない)
import candidates as candidate_sets  # noqa: E402

INBOX_DIR = os.path.join(ROOT, u"data", u"依頼箱")
DELIVERY_DIR = os.path.join(ROOT, u"data", u"納品箱")

MEASURE_SCRIPT = os.path.join(u"調査", u"phase5_ai_operator", u"measure_3d.py")
PROJECT_CAND_SCRIPT = os.path.join(u"調査", u"phase5_ai_operator", u"project_candidates.py")
RENDER_CAND_SCRIPT = os.path.join(u"調査", u"phase5_ai_operator", u"render_candidates.py")
PROJECT_CHOSEN_SCRIPT = os.path.join(u"調査", u"phase5_ai_operator", u"project_chosen.py")
GENERATE_SCRIPT = os.path.join(u"engine", u"generate_drawing.py")

MODEL_EXTS = (".step", ".stp", ".sldprt")
REQUIRED_REQUEST_FIELDS = (u"図番", u"材質", u"個数")

STATES_TERMINAL = (u"合格", u"不合格", u"質問あり")

# ---------------------------------------------------------------------------
# 小物
# ---------------------------------------------------------------------------
def now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _read_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    os.replace(tmp, path)


def run_script(rel_path, args, timeout):
    u"""調査/・engine/ の既存スクリプトをブラックボックスとしてサブプロセス起動する。

    (中身を書き換えない・importして再利用しない。安全規約の「ブラックボックスとして呼ぶ」を
    generate_drawing.py 以外の決定論部品スクリプトにも一貫して適用する)
    """
    script = os.path.join(ROOT, rel_path)
    cmd = [sys.executable, script] + [str(a) for a in args]
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


# ---------------------------------------------------------------------------
# status.json
# ---------------------------------------------------------------------------
def load_status(rd):
    p = os.path.join(rd, "status.json")
    if os.path.exists(p):
        return _read_json(p)
    st = {"request_id": os.path.basename(rd), "state": u"受付済",
          "created_at": now_iso(), "updated_at": now_iso(),
          "history": [], "errors": []}
    save_status(rd, st)
    return st


def save_status(rd, st):
    st["updated_at"] = now_iso()
    _write_json(os.path.join(rd, "status.json"), st)


def set_state(st, new_state, note=None):
    st["history"].append({"at": now_iso(), "from": st["state"], "to": new_state, "note": note})
    st["state"] = new_state


def record_error(st, step, exc):
    st.setdefault("errors", []).append({
        "at": now_iso(), "step": step,
        "message": u"%s: %s" % (type(exc).__name__, exc)})
    st["errors"] = st["errors"][-20:]


# ---------------------------------------------------------------------------
# 依頼.json / モデルファイル
# ---------------------------------------------------------------------------
def _load_request(rd):
    return _read_json(os.path.join(rd, u"依頼.json"))


def validate_request(req):
    u"""材質・個数は必須(CLAUDE.md知見: 3Dからは原理的に決まらない)。図番も無いと後段が動かない。"""
    missing = []
    for k in REQUIRED_REQUEST_FIELDS:
        v = req.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(k)
    if u"個数" not in missing:
        try:
            if float(req[u"個数"]) <= 0:
                missing.append(u"個数(0以下)")
        except (TypeError, ValueError):
            missing.append(u"個数(数値でない)")
    return missing


def find_model_file(rd):
    cands = [f for f in os.listdir(rd)
             if os.path.isfile(os.path.join(rd, f))
             and os.path.splitext(f)[1].lower() in MODEL_EXTS]
    if not cands:
        raise FileNotFoundError(u"3Dモデル(STEP/SLDPRT)がフォルダに無い: %s" % rd)
    if len(cands) > 1:
        raise ValueError(u"3Dモデルが複数ある(1件にすること): %r" % (cands,))
    return os.path.join(rd, cands[0])


# ---------------------------------------------------------------------------
# 1) 計測 + 形状クラス判定(measure_3d相当)
# ---------------------------------------------------------------------------
def _survey_from_faces(faces, body_count):
    u"""measure_3d.py の meas.json['faces'](type_id別の面情報)を
    view_orient.classify() が要求する survey 形状(cylinders/planes)へ変換する。
    view_orient.face_survey は生SWハンドルが要るので、既に採った実測値から作る。"""
    cylinders, planes, counts = [], [], {}
    total_area = 0.0
    for f in faces:
        st = f.get("type_id")
        counts[str(st)] = counts.get(str(st), 0) + 1
        total_area += float(f.get("area_mm2") or 0.0)
        if st == 4002 and "axis" in f and "origin_mm" in f and "d_mm" in f:
            cylinders.append({"d_mm": f["d_mm"], "axis": f["axis"],
                              "origin_mm": f["origin_mm"], "area_mm2": f.get("area_mm2", 0.0)})
        elif st == 4001 and "normal" in f and "point_mm" in f:
            planes.append({"normal": f["normal"], "point_mm": f["point_mm"],
                           "area_mm2": f.get("area_mm2", 0.0)})
    return {"cylinders": cylinders, "planes": planes, "surface_counts": counts,
            "total_area_mm2": total_area or 1.0, "body_count": body_count}


def step_measure_classify(rd, model_path):
    meas_path = os.path.join(rd, "meas.json")
    proc = run_script(MEASURE_SCRIPT, [model_path, meas_path], timeout=180)
    if not os.path.exists(meas_path):
        raise RuntimeError(u"計測スクリプトが meas.json を出力しなかった(rc=%s): %s"
                           % (proc.returncode, (proc.stderr or proc.stdout)[-2000:]))
    meas = _read_json(meas_path)
    if not meas.get("ok"):
        raise RuntimeError(u"SW計測に失敗: %s" % str(meas.get("error", "?"))[:2000])

    survey = _survey_from_faces(meas.get("faces", []), meas["metrics"].get("body_count", 0))
    ev = view_orient.classify(survey, meas["metrics"]["size_mm"], meas["metrics"].get("bbox_mm"))
    ev_public = {k: v for k, v in ev.items() if not k.startswith("_")}
    _write_json(os.path.join(rd, u"分類.json"), ev_public)
    return ev_public


# ---------------------------------------------------------------------------
# 2) 候補6投影+PNG(project_candidates/render_candidates相当)
# ---------------------------------------------------------------------------
def step_candidates(rd, model_path, shape_class, title):
    cand_list = candidate_sets.candidates_for(shape_class)
    cand_input = os.path.join(rd, u"候補設定.json")
    _write_json(cand_input, cand_list)

    cand_dir = os.path.join(rd, u"候補")
    proc = run_script(PROJECT_CAND_SCRIPT, [model_path, cand_dir, cand_input], timeout=300)
    meta_path = os.path.join(cand_dir, "candidates_meta.json")
    if not os.path.exists(meta_path):
        raise RuntimeError(u"候補投影が candidates_meta.json を出力しなかった(rc=%s): %s"
                           % (proc.returncode, (proc.stderr or proc.stdout)[-2000:]))
    meta = _read_json(meta_path)
    ok_list = [c for c in meta.get("candidates", []) if c.get("ok")]
    if not ok_list:
        raise RuntimeError(u"候補投影が全滅した: %s" % str(meta.get("error", "?"))[:2000])

    png_path = os.path.join(rd, u"候補.png")
    proc2 = run_script(RENDER_CAND_SCRIPT, [cand_dir, png_path, title], timeout=120)
    if not os.path.exists(png_path):
        raise RuntimeError(u"候補PNGが生成されなかった(rc=%s): %s"
                           % (proc2.returncode, (proc2.stderr or proc2.stdout)[-2000:]))
    return {"n_ok": len(ok_list), "n_total": len(cand_list), "png": png_path}


# ---------------------------------------------------------------------------
# 3) AIオペレータの choice.json を反映した4面図の作成(project_chosen相当)
#    「計画待ち」に入った時点で choice.json があれば先に materialize しておく
#    (AIオペレータが plan.json を書く際に meta.json の view_plan/model_to_view を参照できるように)。
# ---------------------------------------------------------------------------
def materialize_chosen_views(rd, model_path):
    choice_path = os.path.join(rd, "choice.json")
    views_dxf = os.path.join(rd, "views.dxf")
    meta_json = os.path.join(rd, "meta.json")
    choice = _read_json(choice_path)
    sw_view = choice.get("sw_view")
    rot = choice.get("rotation_deg", choice.get("rot", 0))
    if not sw_view:
        raise ValueError(u"choice.json に sw_view が無い")

    proc = run_script(PROJECT_CHOSEN_SCRIPT,
                      [model_path, views_dxf, meta_json, sw_view, int(rot)], timeout=180)
    if not os.path.exists(meta_json):
        raise RuntimeError(u"project_chosen が meta.json を出力しなかった(rc=%s): %s"
                           % (proc.returncode, (proc.stderr or proc.stdout)[-2000:]))
    meta = _read_json(meta_json)
    if not meta.get("ok"):
        try:
            os.remove(meta_json)   # 失敗時は残さない(次回scanで再試行できるように)
        except OSError:
            pass
        raise RuntimeError(u"選択した向きでの投影に失敗: %s" % str(meta.get("error"))[:2000])
    return meta


# ---------------------------------------------------------------------------
# 4) 生成(generate_drawing.py をブラックボックスで実行)
# ---------------------------------------------------------------------------
def _out_stem(model_path, request, zuban):
    u"""generate_drawing.py 内の出力ファイル名決定ロジックと同一(result.jsonの場所を突き止めるため)。"""
    stem = os.path.splitext(os.path.basename(model_path))[0]
    default_part_name = stem.rsplit("_", 1)[-1] if "_" in stem else stem
    part_name = request.get(u"品名") or default_part_name
    safe_zuban = re.sub(r'[\\/:*?"<>|]', "_", zuban)
    return u"%s_%s" % (safe_zuban, part_name), part_name


def step_generate(rd, request, zuban, model_path, request_json_path):
    plan_path = os.path.join(rd, "plan.json")
    choice_path = os.path.join(rd, "choice.json")
    views_dxf = os.path.join(rd, "views.dxf")
    meta_json = os.path.join(rd, "meta.json")

    if not os.path.exists(meta_json):
        if not os.path.exists(choice_path):
            raise RuntimeError(u"plan.json はあるが choice.json が無い(向き選択が先)")
        materialize_chosen_views(rd, model_path)

    out_dir = os.path.join(rd, u"生成")
    out_stem, _part_name = _out_stem(model_path, request, zuban)
    proc = run_script(GENERATE_SCRIPT,
                      ["--model", model_path, "--plan", plan_path,
                       "--request", request_json_path, "--out-dir", out_dir,
                       "--zuban", zuban, "--skip-sw",
                       "--views-dxf", views_dxf, "--meta-json", meta_json],
                      timeout=240)
    result_json = os.path.join(out_dir, out_stem + u"_result.json")
    if not os.path.exists(result_json):
        raise RuntimeError(u"generate_drawing.py が result.json を出力しなかった(rc=%s): %s"
                           % (proc.returncode, (proc.stderr or proc.stdout)[-3000:]))
    return _read_json(result_json)


def write_interpretation_report(path, request_id, request, summary):
    lines = [
        u"# 解釈レポート %s %s" % (summary.get("zuban", ""), summary.get("part_name", "")),
        u"",
        u"- 依頼ID: %s" % request_id,
        u"- 生成日時: %s" % now_iso(),
        u"- 装置名: %s" % request.get(u"装置名", ""),
        u"- 材質: %s / 材質形状: %s / 個数: %s" % (
            request.get(u"材質", ""), request.get(u"材質形状", ""), request.get(u"個数", "")),
        u"- 入力3D: `%s`" % os.path.basename(summary.get("model", "")),
        u"",
        u"## 検証ゲート結果",
        u"- ゲート①(寸法値照合・実測との突合せ): %s" % (u"合格" if summary.get("gate1_ok") else u"不合格"),
        u"- ゲート②(寸法完全性): %s" % (u"合格" if summary.get("gate2_ok") else u"不合格"),
        u"- 独立検証(DIMSTYLE・図枠・注記書式): %s" % (u"合格" if summary.get("verify_ok") else u"不合格"),
        u"",
        u"## 既知の制限(このループでは未実施)",
        u"- ゲート③(目視照合)は自動化されていない。PNGを人間が確認すること。",
        u"- ゲート④(人間図面との比較)は開発期間限定の照合手段のため本ループ対象外。",
        u"- 本レポートは自動生成の雛形。仮定・矛盾点はAIオペレータが計画作成時(plan.json)に",
        u"  検討した内容に限られる。最終確認は人間が行うこと。",
        u"",
    ]
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(u"\n".join(lines))


def deliver(rd, request_id, request, zuban, result):
    summary = result["summary"]
    dest_dir = os.path.join(DELIVERY_DIR, request_id)
    os.makedirs(dest_dir, exist_ok=True)
    copied = {}
    for label, src in (("dxf", summary.get("final_dxf")),
                       ("png", summary.get("final_png")),
                       ("result_json", summary.get("result_json"))):
        if src and os.path.exists(src):
            dst = os.path.join(dest_dir, os.path.basename(src))
            shutil.copyfile(src, dst)
            copied[label] = dst
    report_path = os.path.join(
        dest_dir, u"解釈レポート_%s_%s.md" % (zuban, summary.get("part_name", "")))
    write_interpretation_report(report_path, request_id, request, summary)
    copied["解釈レポート"] = report_path
    return copied


def write_reject_reason(rd, result):
    summary = result["summary"]
    gate2 = result.get("gate2") or {}
    reasons = {
        "at": now_iso(),
        "gate1_ok": summary.get("gate1_ok"),
        "gate2_ok": summary.get("gate2_ok"),
        "verify_ok": summary.get("verify_ok"),
        "dim_error": summary.get("dim_error"),
        "gate2_unspecified": gate2.get("unspecified"),
        "gate2_redundant_dimensions": gate2.get("redundant_dimensions"),
        "result_json": summary.get("result_json"),
    }
    _write_json(os.path.join(rd, u"不合格理由.json"), reasons)


# ---------------------------------------------------------------------------
# 状態機械: 1回の呼び出しで「1段」だけ進める(進んだら True・進めなければ False)
# ---------------------------------------------------------------------------
def advance_one(rd):
    request_id = os.path.basename(rd)
    st = load_status(rd)
    state = st["state"]
    try:
        # ---- 受付済 -> 計測中 ----
        if state == u"受付済":
            req_path = os.path.join(rd, u"依頼.json")
            if not os.path.exists(req_path):
                raise FileNotFoundError(u"依頼.json が無い")
            request = _read_json(req_path)
            missing = validate_request(request)
            if missing:
                raise ValueError(u"依頼.json の必須項目が不足/不正: %s" % u", ".join(missing))
            find_model_file(rd)  # 存在確認のみ(無ければここで例外)
            set_state(st, u"計測中", note=u"依頼検証OK・計測開始")
            save_status(rd, st)
            return True

        # ---- 計測中 -> 候補提示中 ----
        if state == u"計測中":
            model_path = find_model_file(rd)
            ev = step_measure_classify(rd, model_path)
            set_state(st, u"候補提示中", note=u"形状クラス=%s(%s)" % (
                ev.get("shape_class"), ev.get("reason")))
            save_status(rd, st)
            return True

        # ---- 候補提示中 -> 計画待ち ----
        if state == u"候補提示中":
            request = _load_request(rd)
            model_path = find_model_file(rd)
            ev = _read_json(os.path.join(rd, u"分類.json"))
            zuban = request.get(u"図番", request_id)
            title = u"%s %s" % (zuban, request.get(u"品名", ""))
            info = step_candidates(rd, model_path, ev.get("shape_class"), title)
            set_state(st, u"計画待ち",
                     note=(u"候補%d/%d件成功。AIオペレータの choice.json/plan.json 待ち"
                          % (info["n_ok"], info["n_total"])))
            save_status(rd, st)
            return True

        # ---- 計画待ち: choice.jsonが来たら向き反映図を先に作る/plan.jsonが来たら生成へ ----
        if state == u"計画待ち":
            choice_path = os.path.join(rd, "choice.json")
            plan_path = os.path.join(rd, "plan.json")
            meta_json = os.path.join(rd, "meta.json")
            did_work = False
            if os.path.exists(choice_path) and not os.path.exists(meta_json):
                model_path = find_model_file(rd)
                materialize_chosen_views(rd, model_path)
                st.setdefault("notes", []).append(
                    {"at": now_iso(), "note": u"choice.json を反映した views.dxf/meta.json を生成"})
                did_work = True
            if os.path.exists(plan_path):
                set_state(st, u"生成中", note=u"plan.json 検出・生成開始")
                save_status(rd, st)
                return True
            if did_work:
                save_status(rd, st)
                return True
            return False  # AIオペレータ待ち。エラーではない

        # ---- 生成中 -> 合格/不合格 ----
        if state == u"生成中":
            request = _load_request(rd)
            zuban = request.get(u"図番", request_id)
            model_path = find_model_file(rd)
            req_path = os.path.join(rd, u"依頼.json")
            result = step_generate(rd, request, zuban, model_path, req_path)
            overall_ok = result["summary"]["overall_ok"]
            if overall_ok:
                copied = deliver(rd, request_id, request, zuban, result)
                set_state(st, u"合格", note=u"納品箱へコピー: %s" % copied.get("dxf"))
            else:
                write_reject_reason(rd, result)
                set_state(st, u"不合格", note=u"ゲート不合格(gate1=%s gate2=%s verify=%s)" % (
                    result["summary"].get("gate1_ok"), result["summary"].get("gate2_ok"),
                    result["summary"].get("verify_ok")))
            save_status(rd, st)
            return True

        # ---- 終端状態(合格/不合格/質問あり) ----
        return False

    except Exception as e:  # noqa: BLE001  意図的: 1依頼のエラーで他依頼を巻き込まない
        record_error(st, state, e)
        save_status(rd, st)
        return False


# ---------------------------------------------------------------------------
# CLI: scan / status
# ---------------------------------------------------------------------------
def scan_all():
    if not os.path.isdir(INBOX_DIR):
        print(u"依頼箱が無い: %s" % INBOX_DIR)
        return
    request_ids = sorted(d for d in os.listdir(INBOX_DIR)
                         if os.path.isdir(os.path.join(INBOX_DIR, d)))
    if not request_ids:
        print(u"依頼箱は空です: %s" % INBOX_DIR)
        return
    for rid in request_ids:
        rd = os.path.join(INBOX_DIR, rid)
        print(u"=== %s ===" % rid)
        steps = 0
        while True:
            try:
                advanced = advance_one(rd)
            except Exception:
                # advance_one 自体は例外を握り潰す設計だが、二重の安全網として残す
                print(u"  !! 予期しない例外(この依頼はスキップし、他の依頼へ進む)")
                traceback.print_exc()
                break
            steps += 1
            if not advanced:
                break
            if steps > 20:
                print(u"  !! 状態遷移が20回を超えた(ループの疑いのため打ち切り)")
                break
        st = load_status(rd)
        print(u"  -> state=%s" % st["state"])
        if st.get("errors"):
            print(u"     直近エラー: %s" % st["errors"][-1]["message"][:300])


def cmd_status():
    if not os.path.isdir(INBOX_DIR):
        print(u"依頼箱が無い: %s" % INBOX_DIR)
        return
    rows = []
    for rid in sorted(os.listdir(INBOX_DIR)):
        rd = os.path.join(INBOX_DIR, rid)
        if not os.path.isdir(rd):
            continue
        st = load_status(rd)
        last_err = st["errors"][-1]["message"] if st.get("errors") else ""
        rows.append((rid, st["state"], st.get("updated_at", ""), last_err))
    if not rows:
        print(u"依頼が1件もありません: %s" % INBOX_DIR)
        return
    w = max(len(r[0]) for r in rows)
    print(u"%-*s  %-10s  %-20s  %s" % (w, u"依頼ID", u"状態", u"更新", u"直近エラー"))
    for rid, state, updated, err in rows:
        print(u"%-*s  %-10s  %-20s  %s" % (w, rid, state, updated, err[:80]))


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=u"工房ループ(依頼受付→処理→納品→台帳)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help=u"依頼箱を走査し、決定論で進められる段を全部進める")
    sub.add_parser("status", help=u"全依頼の状態一覧を表示")
    args = ap.parse_args(argv[1:])
    if args.cmd == "scan":
        scan_all()
    elif args.cmd == "status":
        cmd_status()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
