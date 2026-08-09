# -*- coding: utf-8 -*-
u"""SolidWorks 図面パイプライン(フェーズ2)。

    connect → open_part_readonly → new_drawing → insert_standard_views → export_dxf → close

思想: SolidWorks は「3D→2D投影」と「計測」だけに使い、最終DXFの合成(図枠・寸法)は
ezdxf 側で行う(CLAUDE.md 設計判断)。本モジュールは前半=投影の部分だけを担当する。

❗安全規約(CLAUDE.md 知見・実害あり)
  - OpenDoc6 が None のとき ActiveDoc で拾ってはいけない
  - 開いたドキュメントは必ずタイトルとファイル名を照合してから処理する
  - CloseDoc は「自分が開いた」ことが確認できたものだけ
  - 部品は常に読み取り専用(swOpenDocOptions_ReadOnly)で開く。保存は一切しない
  - システムオプションを触ったら必ず元へ戻す(prefs_override コンテキストマネージャ)
"""
import os
import sys
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sw_compat

# ---- swconst(gen_py の swconstants から拾った実値。決め打ちで持つ) -------------
swDocPART = 1
swDocDRAWING = 3

swOpenDocOptions_Silent = 1
swOpenDocOptions_ReadOnly = 2

swDwgPaperA1size = 10
swDwgPaperA3size = 8
swDwgPapersUserDefined = 12

swDwgTemplateNone = 13
swDwgTemplateCustom = 12

# swDisplayMode_e ※ swHIDDEN = 隠れ線"非表示"(removed) / swHIDDEN_GREYED = 隠れ線"表示"
swWIREFRAME = 0
swHIDDEN_GREYED = 1          # 隠れ線表示(HIDDEN 線種で描かれる)
swHIDDEN = 2                 # 隠れ線除去
swSHADED = 3

# swDisplayTangentEdges_e
swTangentEdgesHidden = 0
swTangentEdgesVisibleAndFonted = 1
swTangentEdgesVisible = 2

# swUserPreferenceIntegerValue_e
swDxfVersion = 0
swDxfOutputFonts = 1
swDxfOutputLineStyles = 135
swDxfMultiSheetOption = 253
# swUserPreferenceToggle_e
swDxfUseSolidworksLayers = 305
swDxfMapping = 8
# swDxfFormat_e
swDxfFormat_R2000 = 3
swDxfFormat_R2013 = 7
# swDxfMultisheet_e
swDxfActiveSheetOnly = 0

swSaveAsCurrentVersion = 0
swSaveAsOptions_Silent = 1

# 標準3面図+等角投影の日本語ビュー名(doc.GetModelViewNames で実在を確認する)
VIEW_FRONT = u"*正面"
VIEW_TOP = u"*平面"
VIEW_RIGHT = u"*右側面"
VIEW_ISO = u"*等角投影"


# ---------------------------------------------------------------- 基本ユーティリティ
def prop(obj, name):
    u"""gen_py でプロパティにもメソッドにもなり得る属性を一様に読む。"""
    v = getattr(obj, name)
    return v() if callable(v) else v


def connect():
    u"""起動済み SolidWorks に接続(版は sw_compat が自動判別)。"""
    return sw_compat.connect_sw(), sw_compat.gen_module()


class OpenedDoc(object):
    u"""自分が開いた/作ったドキュメント。close() はタイトル照合済みのものだけ閉じる。"""

    def __init__(self, sw, doc, title, mine, path=None):
        self.sw = sw
        self.doc = doc          # gen_py で包んだ IModelDoc2
        self.title = title
        self.mine = mine
        self.path = path

    def close(self):
        if not self.mine:
            return False
        cur = prop(self.doc, "GetTitle")
        if cur != self.title:
            # 途中で名前が変わった(SaveAs 等)。現タイトルで閉じる前に必ず記録
            self.title = cur
        self.sw.CloseDoc(self.title)
        self.mine = False
        return True


def open_part_readonly(sw, mod, path):
    u"""SLDPRT を読み取り専用+サイレントで開く。タイトル照合まで済ませて返す。

    ❗OpenDoc6 が None のとき ActiveDoc へフォールバックしない(CLAUDE.md 実害あり)。
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise IOError(u"SLDPRT が無い: %s" % path)

    # 開く前に保存版を確認(未来版なら開かずに落とす)
    try:
        vh = sw.VersionHistory(path)
        if isinstance(vh, tuple):
            vh = list(vh)
    except Exception:
        vh = None

    doc = sw.OpenDoc6(path, swDocPART,
                      swOpenDocOptions_Silent | swOpenDocOptions_ReadOnly,
                      "", 0, 0)
    if isinstance(doc, tuple):          # gen_py では戻り値がタプルになる
        doc = doc[0]
    if doc is None:
        raise RuntimeError(
            u"OpenDoc6 が None: %s (VersionHistory=%r)。"
            u"ActiveDoc で拾ってはいけない" % (path, vh))

    doc = mod.IModelDoc2(doc._oleobj_)
    title = prop(doc, "GetTitle")
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    if title not in (base, stem):
        # 掴んだのが別物 → 絶対に閉じない(ユーザーのドキュメントの可能性)
        raise RuntimeError(u"タイトル不一致: 期待 %r / 実際 %r" % (base, title))
    return OpenedDoc(sw, doc, title, True, path), vh


def part_metrics(mod, opened):
    u"""質量特性とバウンディングボックスを取る。単位は kg / mm。"""
    doc = opened.doc
    mp = doc.Extension.CreateMassProperty()
    mp.UseSystemUnits = True
    com = list(mp.CenterOfMass)
    out = {
        "mass_kg": mp.Mass,
        "volume_mm3": mp.Volume * 1e9,
        "density_kgm3": mp.Density,
        "surface_area_mm2": mp.SurfaceArea * 1e6,
        "com_mm": [c * 1000.0 for c in com],
    }
    part = mod.IPartDoc(doc._oleobj_)
    # 表題欄の「重量」に使うので材質の有無を必ず持ち帰る。
    # ❗材質未設定のモデルは密度 1000 kg/m^3(水)で計算されるため質量は無意味になる
    try:
        out["material"] = part.GetMaterialPropertyName2("", "")
    except Exception as exc:      # noqa: BLE001
        out["material"] = "<取得失敗: %s>" % exc
    out["density_is_default_water"] = abs(out["density_kgm3"] - 1000.0) < 1e-6

    bodies = part.GetBodies2(0, True) or []
    box = None
    for b in bodies:
        ib = mod.IBody2(b._oleobj_)
        bb = prop(ib, "GetBodyBox")
        bb = [v * 1000.0 for v in bb]
        if box is None:
            box = bb
        else:
            box = [min(box[i], bb[i]) for i in range(3)] + \
                  [max(box[3 + i], bb[3 + i]) for i in range(3)]
    out["bbox_mm"] = box
    if box:
        out["size_mm"] = [box[3] - box[0], box[4] - box[1], box[5] - box[2]]
    out["body_count"] = len(bodies)
    out["model_view_names"] = list(prop(doc, "GetModelViewNames") or [])
    return out


# ---------------------------------------------------------------- 図面ドキュメント
def new_drawing(sw, mod, paper=swDwgPaperA1size, scale=(1.0, 1.0),
                width_m=0.841, height_m=0.594, first_angle=False,
                sheet_format=swDwgTemplateNone):
    u"""図面テンプレート(GetUserPreferenceStringValue(10))から新規図面を作る。

    scale=(1,1) 固定・図枠(シートフォーマット)無しが既定。図枠は後工程で ezdxf が入れる。
    first_angle=False で第三角法。
    """
    tmpl = sw.GetUserPreferenceStringValue(10)
    if not tmpl or not os.path.exists(tmpl):
        raise RuntimeError(u"図面テンプレートが見つからない: %r" % tmpl)
    doc = sw.NewDocument(tmpl, paper, width_m, height_m)
    if isinstance(doc, tuple):
        doc = doc[0]
    if doc is None:
        raise RuntimeError(u"NewDocument(図面) が None: %s" % tmpl)
    doc = mod.IModelDoc2(doc._oleobj_)
    title = prop(doc, "GetTitle")
    dwg = mod.IDrawingDoc(doc._oleobj_)

    sheet = dwg.GetCurrentSheet()
    sheet = mod.ISheet(sheet._oleobj_)
    sheet_name = prop(sheet, "GetName")
    ok = dwg.SetupSheet5(sheet_name, paper, sheet_format,
                         scale[0], scale[1], bool(first_angle), "",
                         width_m, height_m, "Default", True)
    if not ok:
        raise RuntimeError(u"SetupSheet5 に失敗")
    return OpenedDoc(sw, doc, title, True), dwg, sheet


def sheet_info(mod, sheet):
    u"""シート諸元。

    ❗`ISheet.GetSize` は byref 出力引数を2つ取るので **引数なしでは呼べない**
      (「種類が一致しません」)。`GetSize(0.0, 0.0)` と呼び、戻り値のタプルで受ける。
    GetProperties2 = [paperSize, templateIn, scale1, scale2, firstAngle, width_m, height_m]
    """
    props = list(prop(sheet, "GetProperties2") or [])
    try:
        size = list(sheet.GetSize(0.0, 0.0))
    except Exception:
        size = []
    if size and size[0] == 0:      # 先頭が戻り値のことがある
        size = size[1:]
    info = {
        "name": prop(sheet, "GetName"),
        "properties2": props,
        "size_raw": size,
    }
    if len(props) >= 7:
        info["paper_size"] = props[0]
        info["template_in"] = props[1]
        info["scale"] = [props[2], props[3]]
        info["first_angle"] = bool(props[4])
        info["sheet_wh_mm"] = [props[5] * 1000.0, props[6] * 1000.0]
    return info


def _view_outline_mm(v):
    o = prop(v, "GetOutline")
    return [c * 1000.0 for c in o]


def _set_position_mm(v, cx_mm, cy_mm, tol_mm=1e-6):
    u"""IView.Position(=投影ジオメトリの外形中心, 単位m)を設定する。

    ❗**Python の tuple / list を代入してはいけない**(実測 2026-08-09)。
      `v.Position = (0.5, 0.3)` は例外を出さずに **Position=[0.0, 500.0]** になる
      (x が捨てられ y に x*1000 が入る)。Position は VT_VARIANT なので
      `VARIANT(VT_ARRAY|VT_R8, [x, y])` で渡すこと。代入後は必ず読み戻して検証する。
    """
    import win32com.client
    import pythoncom
    v.Position = win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8, [cx_mm / 1000.0, cy_mm / 1000.0])
    got = [p * 1000.0 for p in list(prop(v, "Position"))]
    if abs(got[0] - cx_mm) > tol_mm or abs(got[1] - cy_mm) > tol_mm:
        raise RuntimeError(u"Position 設定が反映されていない: 指定(%.4f,%.4f) 実際%r"
                           % (cx_mm, cy_mm, got))
    return got


def _set_view_scale(v, scale=1.0):
    u"""ビュー尺度を明示設定する。

    ❗シート尺度が 1:1 でも `CreateDrawViewFromModelView3` で作ったビューは
      **小物だと勝手に 2:1 になる**(UseSheetScale は True と答えるのに ScaleDecimal=2.0)。
      `ScaleDecimal = 1.0` を代入すると 1:1 になり UseSheetScale は False に落ちる。
    """
    v.ScaleDecimal = float(scale)
    got = prop(v, "ScaleDecimal")
    if abs(got - scale) > 1e-9:
        raise RuntimeError(u"ビュー尺度を %s にできない(実際 %s)" % (scale, got))
    return got


#: GetOutline はジオメトリ外形に一定マージンを足して返す(実測 11.88mm/片側・尺度非依存)
OUTLINE_MARGIN_MM = 11.88


def insert_standard_views(mod, dwg, model_path, size_mm=None, gap_mm=40.0,
                          sheet_wh_mm=(841.0, 594.0), scale=1.0,
                          hidden=True, tangent=swTangentEdgesVisibleAndFonted):
    u"""第三角法の 正面/平面/右側面 + 等角投影 を配置する。

    各ビューは `CreateDrawViewFromModelView3` で独立に作る(投影ビューの親子関係は作らない)。
    2パス構成:
      1パス目 = 生成し、尺度を明示設定してから実外形(GetOutline)を測る
      2パス目 = 実外形からグリッド(左下=正面/左上=平面/右下=右側面/右上=等角)を組み、
                Position を設定して重なりゼロで配置する
    `IView.Position` は**投影ジオメトリ外形の中心**(実測)なので、グリッドのセル中心を
    そのまま渡せばよい。
    """
    sw_w, sw_h = sheet_wh_mm
    targets = [
        ("front", VIEW_FRONT, 0, 0),
        ("top",   VIEW_TOP,   0, 1),
        ("right", VIEW_RIGHT, 1, 0),
        ("iso",   VIEW_ISO,   1, 1),
    ]

    made = {}
    for key, vname, col, row in targets:
        v = dwg.CreateDrawViewFromModelView3(model_path, vname,
                                             sw_w / 2000.0, sw_h / 2000.0, 0.0)
        if v is None:
            raise RuntimeError(u"CreateDrawViewFromModelView3 が None: %s" % vname)
        v = mod.IView(v._oleobj_)
        v.SetDisplayMode3(False, swHIDDEN_GREYED if hidden else swHIDDEN, False, False)
        v.SetDisplayTangentEdges2(tangent)
        _set_view_scale(v, scale)
        ol = _view_outline_mm(v)
        made[key] = {"view": v, "view_name": vname, "col": col, "row": row,
                     "w": ol[2] - ol[0], "h": ol[3] - ol[1]}

    # --- グリッド寸法(第三角法: 平面=上 / 右側面=右) ---
    col_w = [max(i["w"] for i in made.values() if i["col"] == c) for c in (0, 1)]
    row_h = [max(i["h"] for i in made.values() if i["row"] == r) for r in (0, 1)]
    total_w = col_w[0] + gap_mm + col_w[1]
    total_h = row_h[0] + gap_mm + row_h[1]
    x0 = (sw_w - total_w) / 2.0
    y0 = (sw_h - total_h) / 2.0
    col_cx = [x0 + col_w[0] / 2.0, x0 + col_w[0] + gap_mm + col_w[1] / 2.0]
    row_cy = [y0 + row_h[0] / 2.0, y0 + row_h[0] + gap_mm + row_h[1] / 2.0]

    out = {}
    m = OUTLINE_MARGIN_MM
    for key, info in made.items():
        v = info["view"]
        _set_position_mm(v, col_cx[info["col"]], row_cy[info["row"]])
        ol = _view_outline_mm(v)
        out[key] = {
            "name": prop(v, "GetName2"),
            "view_name": info["view_name"],
            "grid": [info["col"], info["row"]],
            "position_mm": [p * 1000.0 for p in list(prop(v, "Position"))],
            "outline_mm": ol,
            "geom_mm": [ol[0] + m, ol[1] + m, ol[2] - m, ol[3] - m],
            "geom_size_mm": [ol[2] - ol[0] - 2 * m, ol[3] - ol[1] - 2 * m],
            "scale_ratio": list(prop(v, "ScaleRatio") or []),
            "scale_decimal": prop(v, "ScaleDecimal"),
            "use_sheet_scale": prop(v, "UseSheetScale"),
            # ArrayData = [r0..r8, tx,ty,tz, scale, 0,0,0] : sheet = s*R*model + T (単位m)
            "model_to_view": list(prop(v, "ModelToViewTransform").ArrayData),
        }
    out["_layout"] = {"col_w": col_w, "row_h": row_h,
                      "col_cx": col_cx, "row_cy": row_cy,
                      "sheet_wh_mm": list(sheet_wh_mm), "gap_mm": gap_mm}
    return out


def list_views(mod, dwg):
    u"""シート上の全ビューを列挙(シート自身の擬似ビューは除く)。"""
    res = []
    sheets = prop(dwg, "GetViews") or []
    for sv in sheets:
        for i, v in enumerate(list(sv)):
            v = mod.IView(v._oleobj_)
            if i == 0:
                continue        # 先頭はシート
            res.append({
                "name": prop(v, "GetName2"),
                "type": prop(v, "Type"),
                "outline_mm": _view_outline_mm(v),
                "position_mm": [p * 1000.0 for p in list(prop(v, "Position"))],
            })
    return res


# ---------------------------------------------------------------- 出力
@contextlib.contextmanager
def prefs_override(sw, ints=(), toggles=()):
    u"""システムオプションを一時変更して必ず元へ戻す。"""
    old_i = [(k, sw.GetUserPreferenceIntegerValue(k)) for k, _ in ints]
    old_t = [(k, sw.GetUserPreferenceToggle(k)) for k, _ in toggles]
    try:
        for k, v in ints:
            sw.SetUserPreferenceIntegerValue(k, v)
        for k, v in toggles:
            sw.SetUserPreferenceToggle(k, v)
        yield {"ints_before": old_i, "toggles_before": old_t}
    finally:
        for k, v in old_i:
            sw.SetUserPreferenceIntegerValue(k, v)
        for k, v in old_t:
            sw.SetUserPreferenceToggle(k, v)


def export_dxf(sw, opened, out_path, dxf_version=swDxfFormat_R2000):
    u"""図面を DXF へエクスポート。SaveAs3 の戻り値は (ok, errors, warnings) のタプル。"""
    out_path = os.path.abspath(out_path)
    d = os.path.dirname(out_path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    if os.path.exists(out_path):
        os.remove(out_path)

    ints = ((swDxfVersion, dxf_version),
            (swDxfMultiSheetOption, swDxfActiveSheetOnly))
    with prefs_override(sw, ints=ints) as before:
        ret = opened.doc.Extension.SaveAs3(
            out_path, swSaveAsCurrentVersion, swSaveAsOptions_Silent,
            None, None, 0, 0)
    ok, errs, warns = (ret if isinstance(ret, tuple) else (ret, 0, 0))
    if not ok or errs:
        raise RuntimeError(u"SaveAs3(DXF) 失敗 ok=%r errors=%r warnings=%r"
                           % (ok, errs, warns))
    if not os.path.exists(out_path):
        raise RuntimeError(u"DXF が出力されていない: %s" % out_path)
    return {"path": out_path, "warnings": warns,
            "bytes": os.path.getsize(out_path), "prefs_before": before}
