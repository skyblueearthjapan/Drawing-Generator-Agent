# -*- coding: utf-8 -*-
u"""Z2(SolidWorks 2021)向け M1-3 検証スパイク。

Z2工房統合手順書_2026-08-11.md の M1-3(検証点)を、このリポジトリの実コード
(`engine/sw_compat.py` / `engine/draw_pipeline.py`)を**そのまま呼ぶ最小構成**で
1点ずつ独立に検証する。1点が失敗しても他の検証点の実行は止めない
(検証点ごとに try/except で隔離。前提となる検証点が失敗していれば「スキップ」として
明示し、無理に実行しない)。

結果は UTF-8 の Markdown ログ(spike結果.md 形式: 検証点ごとの ○/×/スキップ + 実測値)
として stdout とファイルの両方に出す。

    python ツール/z2_spike_sw2021.py [<STEPパス>] [<ログ出力先.md>]

既定STEP: ツール/sample/1-18.STEP(教師STEPの小さい板物・約27KB。荏原の3Dモデルそのもの)
既定ログ: 調査/z2統合_M1_SW2021スパイク.md
         (Z2で再実行して上書きしたくない場合は第2引数で別名を指定すること。
          開発機(SW2023)での実行結果はZ2(SW2021)実行時の比較基線になる)

前提: SolidWorks が**起動済みの対話セッション**であること(GetActiveObjectのみ。
New-Objectはしない。SSHの非対話セッションからはSW COMを掴めない=Z2工房統合手順書§2.5)。

安全規約(CLAUDE.md / Z2工房統合手順書§5・厳守):
  - サンプルSTEP(教師STEPの複製。ツール/sample/配下)以外は一切開かない
  - 開いたドキュメントは必ず QuitDoc/CloseDoc で後始末する。閉じる前後で「開いている
    ドキュメント一覧」に差分が無いことまで確認する(ユーザーの他の作業に影響しない)
  - ビルド中のSolidWorksのビューに割り込まない(工房が暇な時間帯に実行すること)
  - engine/*.py は一切書き換えない(呼ぶだけ)
"""
import datetime
import io
import json
import os
import platform
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "engine"))

import sw_compat            # noqa: E402
import draw_pipeline as dp  # noqa: E402

DEFAULT_STEP = os.path.join(ROOT, u"ツール", u"sample", u"1-18.STEP")
DEFAULT_LOG = os.path.join(ROOT, u"調査", u"z2統合_M1_SW2021スパイク.md")


# ---------------------------------------------------------------------------
# 検証点ランナー: 1点ずつ try/except で隔離実行。前提(requires)が未達成ならスキップ。
# ---------------------------------------------------------------------------
class Checkpoint(object):
    def __init__(self, key, label):
        self.key = key
        self.label = label
        self.ok = False
        self.skipped = False
        self.detail = u""
        self.error = u""
        self.elapsed_s = 0.0


class SpikeRunner(object):
    def __init__(self):
        self.checkpoints = []
        self.ctx = {}

    def run(self, key, label, fn, requires=()):
        cp = Checkpoint(key, label)
        missing = [r for r in requires if self.ctx.get(r) is None]
        if missing:
            cp.skipped = True
            cp.detail = u"前提の検証点が未達成のためスキップ(不足: %s)" % u", ".join(missing)
            self.checkpoints.append(cp)
            print(u"[スキップ] %s: %s" % (label, cp.detail))
            return cp
        t0 = time.time()
        try:
            detail = fn(self.ctx)
            cp.ok = True
            cp.detail = detail or u""
        except Exception:
            cp.ok = False
            cp.error = traceback.format_exc()
        finally:
            cp.elapsed_s = time.time() - t0
        self.checkpoints.append(cp)
        mark = u"○" if cp.ok else u"×"
        print(u"[%s] %s (%.2fs)" % (mark, label, cp.elapsed_s))
        if cp.detail:
            print(u"    %s" % cp.detail)
        if not cp.ok and cp.error:
            print(cp.error.rstrip()[-1500:])
        return cp


# ---------------------------------------------------------------------------
# 検証点1: sw_compat接続(.29自動判別) / gen_module
# ---------------------------------------------------------------------------
def cp_connect(ctx):
    sw = sw_compat.connect_sw()
    mod = sw_compat.gen_module()
    ctx["sw"] = sw
    ctx["mod"] = mod
    progid = sw_compat.detected_progid()
    has_iface = hasattr(mod, "IModelDoc2") and hasattr(mod, "IView")
    if not has_iface:
        raise RuntimeError(u"gen_moduleが返ったが IModelDoc2/IView が無い(makepy不完全の疑い)")
    return u"接続成功。判別ProgID=%s / gen_moduleにIModelDoc2・IViewあり" % progid


def cp_pre_open(ctx):
    titles = dp.list_open_docs(ctx["sw"])
    ctx["pre_open_titles"] = titles
    return u"実行前に開いているドキュメント: %d件 %r" % (len(titles), titles)


# ---------------------------------------------------------------------------
# 検証点2: STEP取り込み(GetImportFileData+LoadFile4 / OpenDoc6不可の再確認)
# ---------------------------------------------------------------------------
def cp_opendoc6_rejected(ctx):
    u"""OpenDoc6(swDocPART)でSTEPを開こうとすると失敗する、という開発機の実測(CLAUDE.md)が
    SW2021でも同じか確認する。予期せず成功したら後始末した上で「不一致」として記録する。"""
    sw = ctx["sw"]
    step_path = ctx["step_path"]
    doc = sw.OpenDoc6(step_path, dp.swDocPART,
                       dp.swOpenDocOptions_Silent | dp.swOpenDocOptions_ReadOnly, "", 0, 0)
    if isinstance(doc, tuple):
        doc = doc[0]
    if doc is None:
        return u"想定どおり失敗(doc=None)。LoadFile4系(検証点3)が必要と再確認"
    # 予期せず開けてしまった場合は後始末してから不一致として報告する
    try:
        d2 = ctx["mod"].IModelDoc2(doc._oleobj_)
        title = dp.prop(d2, "GetTitle")
        sw.QuitDoc(title)
    except Exception:
        pass
    raise RuntimeError(u"❗予期せず OpenDoc6 がSTEPを開けた(開発機の実測と不一致。"
                        u"SW2021の挙動差の可能性があるため要調査)")


def cp_step_import(ctx):
    step_path = ctx["step_path"]
    opened, info = dp.open_step_readonly(ctx["sw"], ctx["mod"], step_path)
    ctx["part"] = opened
    path_name = dp.prop(opened.doc, "GetPathName")
    return (u"LoadFile4成功。title=%r imported=%s GetPathName()=%r(空が想定通り) "
            u"import_error=%r" % (opened.title, opened.imported, path_name, info.get("import_error")))


# ---------------------------------------------------------------------------
# 検証点3: CreateDrawViewFromModelView3(タイトル渡し)
# ---------------------------------------------------------------------------
def cp_new_drawing(ctx):
    dwgdoc, dwg, sheet = dp.new_drawing(ctx["sw"], ctx["mod"])
    ctx["dwgdoc"] = dwgdoc
    ctx["dwg"] = dwg
    ctx["sheet"] = sheet
    info = dp.sheet_info(ctx["mod"], sheet)
    return u"図面ドキュメント作成成功。シート=%r サイズ_mm=%r" % (
        info.get("name"), info.get("sheet_wh_mm"))


def cp_create_view(ctx):
    part = ctx["part"]
    dwg = ctx["dwg"]
    model_name = part.model_name  # ❗インポート部品は GetPathName=''なのでタイトルを渡す
    v = dwg.CreateDrawViewFromModelView3(model_name, dp.VIEW_FRONT, 0.42, 0.30, 0.0)
    if v is None:
        raise RuntimeError(u"CreateDrawViewFromModelView3 が None を返した(model_name=%r)"
                            % model_name)
    v = ctx["mod"].IView(v._oleobj_)
    ctx["view"] = v
    return u"ビュー作成成功(タイトル渡し model_name=%r)。view_name=%s" % (
        model_name, dp.prop(v, "GetName2"))


# ---------------------------------------------------------------------------
# 検証点4: IView.Position VARIANT代入+読み戻し
# ---------------------------------------------------------------------------
def cp_position(ctx):
    v = ctx["view"]
    target = (420.5, 297.3)
    got = dp._set_position_mm(v, target[0], target[1])
    return u"Position=%r mm を VARIANT(VT_ARRAY|VT_R8)で代入 → 読み戻し%r" % (target, got)


# ---------------------------------------------------------------------------
# 検証点5: ScaleDecimal明示
# ---------------------------------------------------------------------------
def cp_scale(ctx):
    v = ctx["view"]
    before = dp.prop(v, "ScaleDecimal")
    got = dp._set_view_scale(v, 1.0)
    return u"ScaleDecimal 明示前=%r → 1.0を代入 → 読み戻し%r(UseSheetScale=%r)" % (
        before, got, dp.prop(v, "UseSheetScale"))


# ---------------------------------------------------------------------------
# 検証点6: ModelToViewTransform.ArrayData の列優先変換
# ---------------------------------------------------------------------------
def cp_transform(ctx):
    v = ctx["view"]
    a = list(dp.prop(v, "ModelToViewTransform").ArrayData)
    if len(a) < 13:
        raise RuntimeError(u"ArrayDataの長さが想定(13以上)に満たない: %d" % len(a))
    R = a[0:9]
    T = [a[9] * 1000.0, a[10] * 1000.0]
    s = a[12]
    # 列優先の検算: モデル原点(0,0,0)を変換すると並進Tそのものに一致するはず
    # (調査/analyze_phase2_dxf.py の実装式そのまま: px=s*(R0*x+R3*y+R6*z)+Tx)
    px0 = s * (R[0] * 0 + R[3] * 0 + R[6] * 0) + T[0]
    py0 = s * (R[1] * 0 + R[4] * 0 + R[7] * 0) + T[1]
    origin_ok = abs(px0 - T[0]) < 1e-9 and abs(py0 - T[1]) < 1e-9
    ctx["model_to_view"] = a
    return (u"ArrayData長=%d s=%.6f T_mm=(%.4f,%.4f) 列優先検算(原点)=%s"
            % (len(a), s, T[0], T[1], u"OK" if origin_ok else u"NG(要調査)"))


# ---------------------------------------------------------------------------
# 検証点7: SetDisplayTangentEdges2
# ---------------------------------------------------------------------------
def cp_tangent(ctx):
    v = ctx["view"]
    v.SetDisplayMode3(False, dp.swHIDDEN_GREYED, False, False)
    # ❗STEP由来B-repは円筒が半割り2面 → STEP入力ではswTangentEdgesHidden(0)が既定(CLAUDE.md)
    v.SetDisplayTangentEdges2(dp.swTangentEdgesHidden)
    return u"SetDisplayTangentEdges2(swTangentEdgesHidden=0) 呼び出し成功(例外なし)"


# ---------------------------------------------------------------------------
# 検証点8: DXF書き出し + ezdxf読み戻し
# ---------------------------------------------------------------------------
def cp_export_dxf(ctx):
    import ezdxf
    ctx["dwgdoc"].doc.ForceRebuild3(False)
    ctx["dwgdoc"].doc.ViewZoomtofit2()
    out_dxf = ctx["out_dxf"]
    res = dp.export_dxf(ctx["sw"], ctx["dwgdoc"], out_dxf)
    doc = ezdxf.readfile(out_dxf)
    msp = doc.modelspace()
    counts = {}
    for e in msp:
        counts[e.dxftype()] = counts.get(e.dxftype(), 0) + 1
    ctx["dxf_counts"] = counts
    return (u"出力=%s (%dバイト) ezdxfバージョン=%s ezdxf読み戻しエンティティ種別=%r"
            % (out_dxf, res["bytes"], doc.dxfversion, counts))


# ---------------------------------------------------------------------------
# 検証点9: QuitDoc/CloseDoc後始末(ユーザーの開いている文書に触れていないか確認)
# ---------------------------------------------------------------------------
def cp_cleanup(ctx):
    u"""前段の失敗にかかわらず必ず試みる(requiresを付けず、ctx.get()で安全に扱う)。"""
    errors = []
    for key, label in (("dwgdoc", u"図面"), ("part", u"部品")):
        od = ctx.get(key)
        if od is None:
            continue
        try:
            closed = od.close()
            if not closed:
                errors.append(u"%s: close()がFalse(mine=Falseで元々自分のものでない?)" % label)
        except Exception as e:  # noqa: BLE001
            errors.append(u"%s: close失敗 %s" % (label, e))
    sw = ctx.get("sw")
    if sw is None:
        if errors:
            raise RuntimeError(u"; ".join(errors))
        return u"sw未接続のため後始末対象なし"
    post_titles = dp.list_open_docs(sw)
    pre_titles = set(t for t, _ in ctx.get("pre_open_titles") or [])
    post_titles_set = set(t for t, _ in post_titles)
    leaked = post_titles_set - pre_titles
    if errors:
        raise RuntimeError(u"; ".join(errors))
    if leaked:
        raise RuntimeError(u"後始末後も差分が残っている(ユーザーの文書に影響した疑い): %r" % leaked)
    return (u"QuitDoc/CloseDoc完了。閉じる前後でドキュメント一覧の差分なし"
            u"(前=%d件 後=%d件)" % (len(pre_titles), len(post_titles)))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
CHECKPOINTS = [
    # (key, label, fn, requires)
    ("connect", u"① sw_compat.connect_sw()が.版を掴む / gen_module()が通る", cp_connect, ()),
    ("pre_open", u"(前準備)実行前に開いているドキュメントを記録", cp_pre_open, ("sw",)),
    ("opendoc6_rejected", u"② STEP取り込み: OpenDoc6不可の再確認", cp_opendoc6_rejected,
     ("sw", "mod")),
    ("step_import", u"② STEP取り込み: GetImportFileData+LoadFile4", cp_step_import,
     ("sw", "mod")),
    ("new_drawing", u"③ 図面ドキュメント新規作成(テンプレート・シート設定)", cp_new_drawing,
     ("sw", "mod")),
    ("create_view", u"③ CreateDrawViewFromModelView3(タイトル渡し)", cp_create_view,
     ("dwg", "part")),
    ("position", u"④ IView.Position VARIANT代入+読み戻し", cp_position, ("view",)),
    ("scale", u"⑤ ScaleDecimal明示代入(2:1へ勝手に化けないか)", cp_scale, ("view",)),
    ("transform", u"⑥ ModelToViewTransform.ArrayData 列優先変換", cp_transform, ("view",)),
    ("tangent", u"⑦ SetDisplayTangentEdges2", cp_tangent, ("view",)),
    ("export_dxf", u"⑧ DXF書き出し(AC1015/mm)+ezdxf読み戻し", cp_export_dxf,
     ("dwgdoc", "view")),
    ("cleanup", u"⑨ QuitDoc/CloseDocでの後始末(ユーザー文書への無影響確認)", cp_cleanup, ()),
]


def _env_summary():
    lines = [
        u"- 実行日時: %s" % datetime.datetime.now().isoformat(timespec="seconds"),
        u"- ホスト: %s" % platform.node(),
        u"- OS: %s" % platform.platform(),
        u"- Python: %s (%s)" % (platform.python_version(), sys.executable),
    ]
    try:
        import win32com.client  # noqa: F401
        lines.append(u"- pywin32: import win32com.client 成功")
    except Exception as e:
        lines.append(u"- pywin32: import失敗 %s" % e)
    try:
        import ezdxf
        lines.append(u"- ezdxf: %s" % ezdxf.__version__)
    except Exception as e:
        lines.append(u"- ezdxf: import失敗 %s" % e)
    return lines


def write_log(path, step_path, runner, ok_count, total):
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    lines = [
        u"# Z2(SW2021)向け M1-3 検証スパイク結果",
        u"",
        u"> `ツール/z2_spike_sw2021.py` の実行結果。Z2工房統合手順書_2026-08-11.md M1-3 の",
        u"> 検証点に対応する。1点の失敗が他の検証を止めない設計(独立try/except)。",
        u"",
        u"## 環境",
        u"",
    ] + _env_summary() + [
        u"- 検証STEP: `%s`" % step_path,
        u"",
        u"## 結果サマリ: %d/%d 件 ○" % (ok_count, total),
        u"",
        u"| # | 検証点 | 結果 | 所要時間 | 実測値/備考 |",
        u"|---|---|---|---|---|",
    ]
    for i, cp in enumerate(runner.checkpoints, 1):
        mark = u"○" if cp.ok else (u"スキップ" if cp.skipped else u"×")
        detail = cp.detail.replace("\n", " ") if cp.ok or cp.skipped else (
            (cp.error or u"").strip().splitlines()[-1] if cp.error else u"")
        lines.append(u"| %d | %s | %s | %.2fs | %s |" % (i, cp.label, mark, cp.elapsed_s, detail))
    lines.append(u"")
    lines.append(u"## 詳細ログ")
    lines.append(u"")
    for cp in runner.checkpoints:
        mark = u"○" if cp.ok else (u"スキップ" if cp.skipped else u"×")
        lines.append(u"### [%s] %s" % (mark, cp.label))
        lines.append(u"")
        if cp.detail:
            lines.append(u"```")
            lines.append(cp.detail)
            lines.append(u"```")
        if cp.error:
            lines.append(u"```")
            lines.append(cp.error.rstrip())
            lines.append(u"```")
        lines.append(u"")
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(u"\n".join(lines))
    print(u"ログを書き出しました: %s" % path)


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    step_path = os.path.abspath(argv[1]) if len(argv) > 1 else DEFAULT_STEP
    out_log = os.path.abspath(argv[2]) if len(argv) > 2 else DEFAULT_LOG
    if not os.path.exists(step_path):
        print(u"検証STEPが見つかりません: %s" % step_path)
        return 2

    import tempfile
    out_dxf = os.path.join(tempfile.gettempdir(), u"z2_spike_出力.dxf")

    runner = SpikeRunner()
    runner.ctx["step_path"] = step_path
    runner.ctx["out_dxf"] = out_dxf

    print(u"=== Z2(SW2021)向け M1-3 検証スパイク開始 ===")
    print(u"検証STEP: %s" % step_path)
    print(u"")

    for key, label, fn, requires in CHECKPOINTS:
        cp = runner.run(key, label, fn, requires=requires)
        runner.ctx[key] = True if cp.ok else None  # requires判定用フラグ

    total = len(runner.checkpoints)
    ok_count = sum(1 for cp in runner.checkpoints if cp.ok)
    print(u"")
    print(u"=== 結果: %d/%d 件 ○ ===" % (ok_count, total))

    write_log(out_log, step_path, runner, ok_count, total)
    return 0 if ok_count == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
