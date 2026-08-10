# -*- coding: utf-8 -*-
u"""工房ループ CLI(フェーズ5・依頼受付→処理→納品→台帳)。

    python app/workshop.py scan               依頼箱を走査し、決定論で進められる段を全部進める
    python app/workshop.py status             全依頼の状態一覧を表示(不合格は理由サマリ付き)
    python app/workshop.py retry <依頼ID>      不合格/質問あり/エラーを再試行可能な状態へ戻す
    python app/workshop.py new <依頼ID> ...    依頼.json雛形+3Dモデル配置(--model/--材質は必須)

思想: 1依頼 = data/依頼箱/<依頼ID>/ フォルダ。状態機械を status.json に持ち、
再scanのたびに「今の状態から次へ進めるか」だけを判定して1段ずつ進める(冪等・再試行可能)。

状態遷移:
    受付済 -> 計測中 -> 候補提示中 -> 計画待ち -> 生成中 -> 合格
                            (計画待ち -> 質問あり: 質問票.mdが置かれた時)
                                          (生成中 -> 不合格 もあり得る)
    (エラー時は状態を変えずに status.json の errors[] に記録して次の依頼へ進む)
    (不合格・質問あり・エラーは `retry` で計画待ちへ巻き戻せる。台帳.mdは依頼IDごとに1行を維持)

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

QUESTION_FILENAME = u"質問票.md"       # 計画待ち中にAIオペレータが置く(向き/計画で判断できない時)
GEN_MARKER_FILENAME = u"生成完了.json"  # 生成成功〜status更新の間のクラッシュ復旧用マーカー
LEDGER_PATH = os.path.join(ROOT, u"台帳.md")

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


def _fmt_num(v):
    u"""不合格理由の代表値を短く表示する(132.0 -> '132', 62.3785 -> '62.38')。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f - round(f)) < 1e-6:
        return str(int(round(f)))
    return "%.2f" % f


def _read_question_excerpt(path, max_len=120):
    u"""質問票.md の先頭の非空行を短く抜粋する(status表示・履歴note用)。"""
    try:
        with io.open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip().lstrip("#").strip()
                if line:
                    return line[:max_len]
    except OSError:
        pass
    return u""


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
                       "--views-dxf", views_dxf, "--meta-json", meta_json,
                       "--no-ledger"],  # 台帳記帳はworkshop側(book_ledger)に一本化し二重記帳を防ぐ
                      timeout=240)
    result_json = os.path.join(out_dir, out_stem + u"_result.json")
    if not os.path.exists(result_json):
        raise RuntimeError(u"generate_drawing.py が result.json を出力しなかった(rc=%s): %s"
                           % (proc.returncode, (proc.stderr or proc.stdout)[-3000:]))
    result = _read_json(result_json)
    # ❗generate_drawing.py はresult_jsonへの自己パスを書き出す"前"にsummaryへ追記するため、
    #   ファイルを読み戻すとsummary["result_json"]が常に欠落する(engine側の既存の癖)。
    #   ここで実際のパスを補完する(write_reject_reason/book_ledger/クラッシュ復旧マーカーが使う)。
    result.setdefault("summary", {})["result_json"] = result_json
    return result


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


def summarize_reject_reason(rd):
    u"""不合格理由.json を人が読める1行サマリへ整形する(種類別件数+代表例。盲検所見§7-2への対応)。

    生JSON羅列(未指定N件がN行並ぶ)をやめ、feature(position/circle等)別・position は軸別に
    グルーピングして件数と代表値だけ見せる。"""
    path = os.path.join(rd, u"不合格理由.json")
    if not os.path.exists(path):
        return u""
    try:
        reasons = _read_json(path)
    except (ValueError, OSError):
        return u"(不合格理由.jsonの読み込みに失敗)"

    parts = []
    if reasons.get("gate1_ok") is False:
        parts.append(u"ゲート①不合格: %s" % str(reasons.get("dim_error") or u"?")[:60])

    unspecified = reasons.get("gate2_unspecified") or []
    if unspecified:
        groups = {}
        for item in unspecified:
            feat = item.get("feature")
            if feat == "position":
                key = u"位置(%s軸)" % item.get("axis", "?")
            elif feat == "circle":
                key = u"直径"
            else:
                key = feat or u"その他"
            groups.setdefault(key, []).append(item)
        segs = []
        for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            rep = items[0]
            if rep.get("feature") == "circle":
                rep_txt = u"φ%s" % _fmt_num(rep.get("diameter"))
            else:
                rep_txt = u"%s=%s" % (rep.get("axis", "?"), _fmt_num(rep.get("value")))
            segs.append(u"%s未指定%d件(例:%s)" % (key, len(items), rep_txt))
        parts.append(u"未指定計%d件[%s]" % (len(unspecified), u"、".join(segs)))
    elif reasons.get("gate2_ok") is False:
        parts.append(u"ゲート②不合格(詳細不明)")

    redundant = reasons.get("gate2_redundant_dimensions") or []
    if redundant:
        parts.append(u"冗長寸法%d件(警告)" % len(redundant))

    if reasons.get("verify_ok") is False:
        parts.append(u"独立検証不合格")

    if not parts:
        parts.append(u"不合格(理由詳細なし)")
    return u" / ".join(parts)


# ---------------------------------------------------------------------------
# 台帳.md — workshop側で1依頼1行に保つ(retry等で同じ依頼を何度生成しても追記でなく更新)
#
# generate_drawing.py は --no-ledger 付きで呼び、engine側の追記(呼ぶたび1行増える)を止め、
# 代わりにこちら側で「依頼ID」をnote列に埋め込んだ行をupsertする。二重台帳記帳の防止はここで担保する。
# ---------------------------------------------------------------------------
LEDGER_HEADER = (
    u"# 処理台帳 — Drawing-Generator-Agent\n\n"
    u"生成した部品図の記録。1件1行(依頼IDごとに最新の生成結果で更新)。"
    u"検証ゲートを通ったものだけ「納品」になる。\n\n"
    u"| 日付 | 図番 | 品名 | 入力3D | 出力DXF | ゲート①寸法値 | ゲート②完全性 |"
    u" ゲート③目視 | ゲート④人間図比較 | 状態 | 備考 |\n"
    u"|---|---|---|---|---|---|---|---|---|---|---|\n"
)


def _ledger_cell(s):
    s = re.sub(r"\s+", " ", str(s)).strip().replace("|", "\\|")
    if len(s) > 200:
        s = s[:200] + u"…"
    return s


def upsert_ledger_row(request_id, row):
    u"""依頼IDをnoteに埋め込んだ行を台帳.mdへ書く。既存行があれば置換、無ければ追記(1依頼1行を維持)。"""
    marker = u"依頼ID=%s" % request_id
    note = u"%s(%s)" % (row.get("note", ""), marker)
    line = u"| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |\n" % (
        _ledger_cell(row["date"]), _ledger_cell(row["zuban"]), _ledger_cell(row["part_name"]),
        _ledger_cell(row["model"]), _ledger_cell(row["out_dxf"]), _ledger_cell(row["gate1"]),
        _ledger_cell(row["gate2"]), _ledger_cell(row["gate3"]), _ledger_cell(row["gate4"]),
        _ledger_cell(row["status"]), _ledger_cell(note))

    if os.path.exists(LEDGER_PATH):
        with io.open(LEDGER_PATH, encoding="utf-8") as f:
            content = f.read()
    else:
        content = LEDGER_HEADER
    lines = content.splitlines(keepends=True)
    replaced = False
    for i, ln in enumerate(lines):
        if marker in ln:
            lines[i] = line
            replaced = True
            break
    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(line)

    tmp = LEDGER_PATH + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(u"".join(lines))
    os.replace(tmp, LEDGER_PATH)


def book_ledger(request_id, request, zuban, result):
    u"""generate_drawing.py の結果から台帳.md向けの行を組み立ててupsertする(engine側の追記は
    --no-ledger で止めてあるので、台帳への記帳経路はここ1本に統一される)。"""
    summary = result.get("summary") or {}
    gate2_report = result.get("gate2")
    verify_report = result.get("independent_verify")

    dim_error = summary.get("dim_error")
    if verify_report and verify_report.get("gate1_max_diff_mm") is not None:
        gate1_text = u"合格(最大差%.6fmm)" % verify_report["gate1_max_diff_mm"]
    elif dim_error:
        gate1_text = u"不合格: %s" % dim_error
    else:
        gate1_text = u"未実施"

    if summary.get("gate2_ok"):
        gate2_text = u"合格(未指定0件)"
    elif gate2_report is not None:
        gate2_text = u"不合格(未指定%d件)" % len(gate2_report.get("unspecified") or [])
    else:
        gate2_text = u"未実施"

    gate3_text = u"PNG生成済み(要目視)" if summary.get("final_png") else u"PNG未生成"
    gate4_text = u"未実施(バッチ生成のためスキップ)"

    def _relslash(p):
        if not p:
            return "-"
        return os.path.relpath(p, ROOT).replace(os.sep, "/")

    upsert_ledger_row(request_id, {
        "date": datetime.date.today().isoformat(),
        "zuban": zuban,
        "part_name": summary.get("part_name", ""),
        "model": os.path.basename(summary.get("model", "")) if summary.get("model") else "",
        "out_dxf": (u"`%s`" % _relslash(summary.get("final_dxf"))) if summary.get("final_dxf") else "-",
        "gate1": gate1_text, "gate2": gate2_text, "gate3": gate3_text, "gate4": gate4_text,
        "status": summary.get("status", ""),
        "note": u"workshop.py経由(generate_drawing.py --no-ledger)。結果=`%s`" % _relslash(
            summary.get("result_json")),
    })


# ---------------------------------------------------------------------------
# 生成完了マーカー — 「生成成功」と「status更新」の間でプロセスが死んだ場合の復旧用
#
# step_generate() が result.json を出力した直後にこのマーカーを書く。次回scanで
# 状態がまだ「生成中」のままなら、まずマーカーを見て「plan.jsonが前回と同じなら」
# generate_drawing.py を再実行せずに前回の result.json から納品/記帳/状態更新だけを
# やり直す(SW呼び出しの再実行=二重納品/二重記帳の危険を避ける)。
# ---------------------------------------------------------------------------
def _gen_marker_path(rd):
    return os.path.join(rd, GEN_MARKER_FILENAME)


def _write_gen_marker(rd, result, plan_path):
    marker = {
        "at": now_iso(),
        "result_json": result.get("summary", {}).get("result_json"),
        "overall_ok": result.get("summary", {}).get("overall_ok"),
        "plan_mtime": os.path.getmtime(plan_path) if os.path.exists(plan_path) else None,
    }
    _write_json(_gen_marker_path(rd), marker)


def _remove_gen_marker(rd):
    p = _gen_marker_path(rd)
    if os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass


def _read_gen_marker_if_fresh(rd, plan_path):
    u"""前回生成が完了済み(マーカーあり)かつ plan.json が未変更なら、その result.json を返す。
    マーカーが無い/古い/参照先が消えている場合は None(=通常どおり再生成が必要)。"""
    mp = _gen_marker_path(rd)
    if not os.path.exists(mp) or not os.path.exists(plan_path):
        return None
    try:
        marker = _read_json(mp)
    except (ValueError, OSError):
        return None
    if marker.get("plan_mtime") != os.path.getmtime(plan_path):
        return None  # plan.jsonが変わっている = 古いマーカー。再生成する
    rj = marker.get("result_json")
    if not rj or not os.path.exists(rj):
        return None
    try:
        result = _read_json(rj)
    except (ValueError, OSError):
        return None
    result.setdefault("summary", {})["result_json"] = rj  # step_generate()と同じ補完(see 上のnote)
    return result


def _cleanup_generation_artifacts(rd):
    u"""retry用: 生成の出力(生成/・不合格理由.json・生成完了.json)を掃除する。
    choice.json/plan.json/meta.json/views.dxf/候補系は残す(向き選択・作図計画は使い回せる。
    plan.jsonを直して再scanする運用を想定)。削除したものの一覧を返す。"""
    removed = []
    gen_dir = os.path.join(rd, u"生成")
    if os.path.isdir(gen_dir):
        shutil.rmtree(gen_dir)
        removed.append(u"生成/")
    for name in (u"不合格理由.json", GEN_MARKER_FILENAME):
        p = os.path.join(rd, name)
        if os.path.exists(p):
            os.remove(p)
            removed.append(name)
    return removed


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
            question_path = os.path.join(rd, QUESTION_FILENAME)
            if os.path.exists(question_path):
                excerpt = _read_question_excerpt(question_path)
                set_state(st, u"質問あり",
                         note=(u"%s を検出・人間の確認待ちへ遷移%s" % (
                             QUESTION_FILENAME, u": %s" % excerpt if excerpt else u"")))
                save_status(rd, st)
                return True

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
            plan_path = os.path.join(rd, "plan.json")

            # クラッシュ復旧: 前回「生成成功」まで進んでいて status 更新だけが未了なら、
            # plan.jsonが変わっていない限り generate_drawing.py を再実行せず前回結果を使い回す
            # (二重にSWを叩く/二重納品/二重記帳を避ける)。
            result = _read_gen_marker_if_fresh(rd, plan_path)
            resumed = result is not None
            if result is None:
                result = step_generate(rd, request, zuban, model_path, req_path)
                _write_gen_marker(rd, result, plan_path)

            overall_ok = result["summary"]["overall_ok"]
            book_ledger(request_id, request, zuban, result)  # 依頼IDでupsert(retryしても1依頼1行)
            resumed_tag = u"(クラッシュ復旧: 前回の生成結果を再利用)" if resumed else u""
            if overall_ok:
                copied = deliver(rd, request_id, request, zuban, result)
                set_state(st, u"合格", note=u"納品箱へコピー: %s%s" % (copied.get("dxf"), resumed_tag))
            else:
                write_reject_reason(rd, result)
                set_state(st, u"不合格", note=u"ゲート不合格(gate1=%s gate2=%s verify=%s)%s" % (
                    result["summary"].get("gate1_ok"), result["summary"].get("gate2_ok"),
                    result["summary"].get("verify_ok"), resumed_tag))
            _remove_gen_marker(rd)
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
        state = st["state"]
        if state == u"不合格":
            note = summarize_reject_reason(rd)
        elif state == u"質問あり":
            note = _read_question_excerpt(os.path.join(rd, QUESTION_FILENAME)) or (
                u"%s 待ち" % QUESTION_FILENAME)
        elif st.get("errors"):
            note = st["errors"][-1]["message"]
        else:
            note = u""
        rows.append((rid, state, st.get("updated_at", ""), note))
    if not rows:
        print(u"依頼が1件もありません: %s" % INBOX_DIR)
        return
    w = max(len(r[0]) for r in rows)
    print(u"%-*s  %-10s  %-20s  %s" % (w, u"依頼ID", u"状態", u"更新", u"備考(不合格理由/質問/エラー)"))
    for rid, state, updated, note in rows:
        print(u"%-*s  %-10s  %-20s  %s" % (w, rid, state, updated, note[:160]))


# ---------------------------------------------------------------------------
# CLI: retry
# ---------------------------------------------------------------------------
def cmd_retry(request_id):
    rd = os.path.join(INBOX_DIR, request_id)
    if not os.path.isdir(rd):
        print(u"依頼フォルダが見つかりません: %s" % rd)
        return 1
    st = load_status(rd)
    state = st["state"]

    if state in (u"不合格", u"質問あり"):
        removed = _cleanup_generation_artifacts(rd)
        if state == u"質問あり":
            qpath = os.path.join(rd, QUESTION_FILENAME)
            if os.path.exists(qpath):
                os.remove(qpath)
                removed.append(QUESTION_FILENAME)
        st["errors"] = []
        set_state(st, u"計画待ち",
                 note=(u"retry: 「%s」から巻き戻し。掃除した生成物: %s。"
                      u"choice.json/plan.jsonを見直して再度 scan してください。"
                      % (state, u"、".join(removed) if removed else u"(無し)")))
        save_status(rd, st)
        print(u"%s を「計画待ち」へ巻き戻しました(掃除: %s)" % (
            request_id, u"、".join(removed) if removed else u"無し"))
        return 0

    if st.get("errors"):
        st["errors"] = []
        set_state(st, state,
                 note=u"retry: エラー履歴をクリア(状態は「%s」のまま・次のscanで再試行)" % state)
        save_status(rd, st)
        print(u"%s のエラー履歴をクリアしました(状態=%s のまま。次の scan で再試行されます)" % (
            request_id, state))
        return 0

    print(u"%s は不合格/質問あり/エラーのいずれでもありません(state=%s)。retryは不要です。" % (
        request_id, state))
    return 1


# ---------------------------------------------------------------------------
# CLI: new(依頼.json雛形+3Dモデル配置。手書きJSONのミス防止)
# ---------------------------------------------------------------------------
def _parse_scale(raw):
    u"""'1:2' -> 0.5 / '0.5' -> 0.5 / '1' -> 1.0 / None -> 1.0"""
    if raw is None:
        return 1.0
    raw = raw.strip()
    if ":" in raw:
        a, b = raw.split(":", 1)
        return float(a) / float(b)
    return float(raw)


def _num_clean(x):
    u"""個数などのJSON出力を 1.0 でなく 1 のように整形する(既存依頼.jsonの流儀に合わせる)。"""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return x
    return int(f) if f == int(f) else f


def cmd_new(args):
    rd = os.path.join(INBOX_DIR, args.request_id)
    if os.path.isdir(rd) and not args.force:
        print(u"依頼フォルダが既に存在します(上書きするなら --force): %s" % rd)
        return 1
    if not os.path.isfile(args.model):
        print(u"3Dモデルが見つかりません: %s" % args.model)
        return 1
    ext = os.path.splitext(args.model)[1].lower()
    if ext not in MODEL_EXTS:
        print(u"3Dモデルの拡張子が対応外です(対応: %s): %s" % (u"/".join(MODEL_EXTS), args.model))
        return 1
    try:
        scale = _parse_scale(args.scale)
        if scale <= 0:
            raise ValueError(u"尺度は正の数にしてください")
    except (ValueError, ZeroDivisionError) as e:
        print(u"尺度の指定が不正です(%r): %s" % (args.scale, e))
        return 1
    try:
        qty = float(args.qty)
        if qty <= 0:
            raise ValueError(u"個数は正の数にしてください")
    except (TypeError, ValueError) as e:
        print(u"個数の指定が不正です(%r): %s" % (args.qty, e))
        return 1
    if not args.material or not args.material.strip():
        print(u"材質が未指定です(--材質 は必須。3Dモデルからは決まらないため)")
        return 1

    os.makedirs(rd, exist_ok=True)
    dest_model = os.path.join(rd, os.path.basename(args.model))
    shutil.copyfile(args.model, dest_model)

    zuban = args.zuban or args.request_id
    request = {
        u"図番": zuban,
        u"品名": args.part_name or u"",
        u"装置名": args.souchi or u"",
        u"材質": args.material,
        u"個数": _num_clean(qty),
        u"尺度": scale,
        u"製図者": args.draftsman or u"AI",
    }
    if args.material_shape:
        request[u"材質形状"] = args.material_shape
    if args.rev:
        request[u"連番"] = args.rev
    if args.note:
        request[u"_note"] = args.note

    req_path = os.path.join(rd, u"依頼.json")
    _write_json(req_path, request)

    print(u"依頼を作成しました: %s" % rd)
    print(u"  モデル: %s" % dest_model)
    print(u"  %s" % req_path)
    print(u"次は `python app/workshop.py scan` を実行してください。")
    return 0


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=u"工房ループ(依頼受付→処理→納品→台帳)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan", help=u"依頼箱を走査し、決定論で進められる段を全部進める")
    sub.add_parser("status", help=u"全依頼の状態一覧を表示(不合格は理由サマリ付き)")

    retry_p = sub.add_parser(
        "retry", help=u"不合格/質問あり/エラーの依頼を安全に再試行可能な状態へ戻す")
    retry_p.add_argument("request_id", help=u"依頼ID(data/依頼箱/<ID>/)")

    new_p = sub.add_parser("new", help=u"新規依頼を作成する(依頼.json雛形+3Dモデル配置)")
    new_p.add_argument("request_id", help=u"依頼ID(data/依頼箱/<ID>/ を作成)")
    new_p.add_argument("--model", required=True, help=u"3Dモデル(STEP/SLDPRT)。依頼フォルダへコピーする")
    new_p.add_argument(u"--図番", dest="zuban", default=None, help=u"省略時は依頼IDを使う")
    new_p.add_argument(u"--品名", dest="part_name", default=None)
    new_p.add_argument(u"--装置名", dest="souchi", default=None)
    new_p.add_argument(u"--材質", dest="material", required=True,
                       help=u"必須(3Dモデルからは決まらないためAI推定させない)")
    new_p.add_argument(u"--材質形状", dest="material_shape", default=None, help=u"例: マル40 / ＰＬ６")
    new_p.add_argument(u"--個数", dest="qty", default=1, help=u"既定1")
    new_p.add_argument(u"--尺度", dest="scale", default=None, help=u"例: 1:2 / 0.5 / 1 (既定1.0)")
    new_p.add_argument(u"--製図者", dest="draftsman", default=u"AI")
    new_p.add_argument(u"--連番", dest="rev", default=None)
    new_p.add_argument("--note", dest="note", default=None, help=u"依頼.jsonの_noteに書く任意メモ")
    new_p.add_argument("--force", action="store_true", help=u"既存の依頼フォルダに上書きする")

    args = ap.parse_args(argv[1:])
    if args.cmd == "scan":
        scan_all()
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "retry":
        return cmd_retry(args.request_id)
    elif args.cmd == "new":
        return cmd_new(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
