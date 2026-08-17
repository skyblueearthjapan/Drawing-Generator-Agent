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
# ❗3D Interconnect(STEPを外部参照 "->" で取り込む方式)。ON だと LoadFile4 の
#   COM戻りが永遠に失われる(Z2/SW2021 SP0.0 で 5/5 再現・2026-08-17実測。
#   画面上は取り込み完了・フィーチャーツリーに "1-18.STEP<1> ->" が出るのが症状)。
#   OFF なら 3.3s で正常返答。インポート前に必ず OFF を確認する。
swMultiCAD_Enable3DInterconnect = 691
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

    def __init__(self, sw, doc, title, mine, path=None, imported=False):
        self.sw = sw
        self.doc = doc          # gen_py で包んだ IModelDoc2
        self.title = title
        self.mine = mine
        self.path = path
        #: STEP等からインポートした未保存ドキュメントか
        self.imported = imported

    @property
    def model_name(self):
        u"""`CreateDrawViewFromModelView3` に渡す ModelName。

        ❗インポート部品は `GetPathName()` が空なので**元のSTEPパスは使えない**(実測: None が返る)。
          未保存ドキュメントは **GetTitle()('1-18.SLDPRT')** を渡すと通る。
        """
        return self.title if self.imported else self.path

    def close(self):
        if not self.mine:
            return False
        cur = prop(self.doc, "GetTitle")
        if cur != self.title:
            # 途中で名前が変わった(SaveAs 等)。現タイトルで閉じる前に必ず記録
            self.title = cur
        if self.imported:
            # ❗未保存のインポート部品は CloseDoc(title) では閉じない(黙って居座る・実測)。
            #   QuitDoc = 保存せずに閉じる。教師STEPを一切汚さないためにもこちらが正しい。
            self.sw.QuitDoc(self.title)
        else:
            self.sw.CloseDoc(self.title)
        self.mine = False
        return True


def list_open_docs(sw):
    u"""開いている全ドキュメントの [(title, type)]。type: 1=部品 2=アセンブリ 3=図面。

    ❗`GetFirstDocument`+`GetNext` の連鎖は当機では1件目で切れる(2件開いていても1件しか返らない
      のを実測)。安全確認(pre/post スナップショット)は必ず `GetDocuments()` で取る。
    """
    out = []
    try:
        arr = sw.GetDocuments()
    except Exception:
        arr = None
    for d in list(arr or []):
        try:
            out.append((prop(d, "GetTitle"), prop(d, "GetType")))
        except Exception:
            pass
    return out


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


#: STEP/IGES 等、SolidWorks が「インポート」で読む中間フォーマット
IMPORT_EXTS = (".step", ".stp", ".iges", ".igs", ".x_t", ".x_b", ".sat")


def open_step_readonly(sw, mod, path):
    u"""STEP を新規部品としてインポートして開く(元ファイルは一切変更しない)。

    ❗**`OpenDoc6` では STEP を開けない**(実測 2026-08-10 / SW2023):
        swDocPART(1)          → doc=None, error=2097152 (swFileRequiresRepairError)
        swDocIMPORTED_PART(6) → doc=None, error=1024    (swInvalidFileTypeError)
      **`ISldWorks.LoadFile4(path, "r", IImportStepData, err)` が唯一通る**。
      戻り値は (IModelDoc2, error) のタプルで error=0。

    生成されるのは **未保存の部品ドキュメント**(GetTitle='<stem>.SLDPRT' / GetPathName='')。
    単位は mm で入る(GetBodyBox の m→mm 換算がそのまま実寸になることを実測で確認済み)。
    """
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise IOError(u"STEP が無い: %s" % path)

    # ❗3D Interconnect が ON だと LoadFile4 がハングする(Z2/SW2021 実測)。
    #   OFF へ倒し、戻さない(戻すと同一セッションの後続インポートが再びハングする)。
    interconnect_disabled = False
    try:
        if sw.GetUserPreferenceToggle(swMultiCAD_Enable3DInterconnect):
            sw.SetUserPreferenceToggle(swMultiCAD_Enable3DInterconnect, False)
            interconnect_disabled = True
    except Exception:                    # noqa: BLE001 - 読めない版では従来動作のまま
        pass

    isd = sw.GetImportFileData(path)     # STEP なら IImportStepData が返る(None でも続行可)
    ret = sw.LoadFile4(path, "r", isd, 0)
    doc, err = (ret if isinstance(ret, tuple) else (ret, None))
    if doc is None:
        raise RuntimeError(u"LoadFile4 が None: %s (error=%r)。ActiveDoc で拾ってはいけない"
                           % (path, err))
    doc = mod.IModelDoc2(doc._oleobj_)
    title = prop(doc, "GetTitle")
    stem = os.path.splitext(os.path.basename(path))[0]
    if os.path.splitext(title)[0] != stem:
        # 掴んだのが別物 → 絶対に閉じない(ユーザーのドキュメントの可能性)
        raise RuntimeError(u"タイトル不一致: 期待 '%s.*' / 実際 %r" % (stem, title))
    return OpenedDoc(sw, doc, title, True, path, imported=True), {
        "import_error": err, "interconnect_disabled": interconnect_disabled}


def open_model_readonly(sw, mod, path):
    u"""拡張子で SLDPRT / STEP を振り分けて読み取り専用で開く。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMPORT_EXTS:
        return open_step_readonly(sw, mod, path)
    return open_part_readonly(sw, mod, path)


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


def _set_view_angle(v, deg, tol_deg=1e-6):
    u"""ビューの紙面内回転角(度・CCW)を設定する。

    ❗**`IView.Angle` は radian**(90 を代入すると 90rad が 2.0354rad に折り返る・実測)。
      **正 = 画像が反時計回り**(`*正面` に +pi/2 で paper_x が +X→-Y になるのを実測)。
      代入後は必ず読み戻して検証する(Position と同じクラスの罠を警戒)。
    """
    import math
    rad = math.radians(deg % 360)
    v.Angle = rad
    got = prop(v, "Angle")
    if abs((math.degrees(got) - deg) % 360.0) > tol_deg and \
       abs((math.degrees(got) - deg) % 360.0 - 360.0) > tol_deg:
        raise RuntimeError(u"ビュー回転角を %s度 にできない(実際 %s度)"
                           % (deg, math.degrees(got)))
    return math.degrees(got)


def insert_standard_views(mod, dwg, model_path, size_mm=None, gap_mm=40.0,
                          sheet_wh_mm=(841.0, 594.0), scale=1.0,
                          hidden=True, tangent=swTangentEdgesVisibleAndFonted,
                          plan=None):
    u"""第三角法の 正面/平面/右側面 + 等角投影 を配置する。

    各ビューは `CreateDrawViewFromModelView3` で独立に作る(投影ビューの親子関係は作らない)。
    2パス構成:
      1パス目 = 生成し、尺度を明示設定してから実外形(GetOutline)を測る
      2パス目 = 実外形からグリッド(左下=正面/左上=平面/右下=右側面/右上=等角)を組み、
                Position を設定して重なりゼロで配置する
    `IView.Position` は**投影ジオメトリ外形の中心**(実測)なので、グリッドのセル中心を
    そのまま渡せばよい。

    `plan` に `view_orient.view_plan()` の戻り(front/top/right/iso ごとの
    `sw_view` と `rotation_deg`)を渡すと、**STEPの座標系ではなく形状から決めた向き**で
    投影する。省略時は従来どおり SWの正面/平面/右側面/等角投影をそのまま使う。
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
        angle_deg = 0.0
        if plan and key in plan:
            vname = plan[key]["sw_view"]
            angle_deg = float(plan[key].get("rotation_deg") or 0.0)
        v = dwg.CreateDrawViewFromModelView3(model_path, vname,
                                             sw_w / 2000.0, sw_h / 2000.0, 0.0)
        if v is None:
            raise RuntimeError(u"CreateDrawViewFromModelView3 が None: %s" % vname)
        v = mod.IView(v._oleobj_)
        v.SetDisplayMode3(False, swHIDDEN_GREYED if hidden else swHIDDEN, False, False)
        v.SetDisplayTangentEdges2(tangent)
        _set_view_scale(v, scale)
        # ❗回転は**外形を測る前**に入れる(回すと GetOutline の縦横が入れ替わるため)
        if angle_deg:
            _set_view_angle(v, angle_deg)
        ol = _view_outline_mm(v)
        made[key] = {"view": v, "view_name": vname, "col": col, "row": row,
                     "angle_deg": angle_deg,
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
            "angle_deg": info["angle_deg"],
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
        try:
            ret = opened.doc.Extension.SaveAs3(
                out_path, swSaveAsCurrentVersion, swSaveAsOptions_Silent,
                None, None, 0, 0)
        except Exception as exc:         # noqa: BLE001
            # ❗Z2/SW2021 の gen_py では Extension.SaveAs3 の byref 引数が
            #   DISP_E_TYPEMISMATCH(-2147352571) になる(2026-08-17実測。
            #   VARIANT を渡す変種も early binding に拒否される)。
            #   旧 IModelDoc2.SaveAs3(3引数) は同機で成功する。戻り値は成功でも
            #   0 が返るため信用せず、後段のファイル実在チェックを正とする。
            if getattr(exc, "hresult", None) != -2147352571:
                raise
            opened.doc.SaveAs3(out_path, swSaveAsCurrentVersion,
                               swSaveAsOptions_Silent)
            ret = (os.path.exists(out_path), 0, 0)
    ok, errs, warns = (ret if isinstance(ret, tuple) else (ret, 0, 0))
    if not ok or errs:
        raise RuntimeError(u"SaveAs3(DXF) 失敗 ok=%r errors=%r warnings=%r"
                           % (ok, errs, warns))
    if not os.path.exists(out_path):
        raise RuntimeError(u"DXF が出力されていない: %s" % out_path)
    return {"path": out_path, "warnings": warns,
            "bytes": os.path.getsize(out_path), "prefs_before": before}
