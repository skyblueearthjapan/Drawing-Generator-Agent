# -*- coding: utf-8 -*-
u"""寸法記入エンジン(恒久モジュール・フェーズ3-B)。

合成済みDXF(engine/compose_drawing.py の出力)+ 作図計画JSON(engine/plan_schema.md)
+ 図枠/dimstyle_spec.json  →  寸法・穴注記入りの部品図DXF。

    apply_plan(plan_path, out_dxf_path) -> 検証レポートdict

設計方針(調査/dimension_style_analysis.md §8 / 図枠/dimstyle_spec.json / 裁定Q1-Q5 準拠):
  1. 直径寸法は「線形(rotated)DIMENSION + dimpost='%%c<>'」(裁定Q1)。半径はRADIUS型+'R<>'
  2. 1寸法=1専用DIMSTYLE(XDATAオーバーライドは使わない。コーパス実測0件)
  3. _OPEN30 矢印は ARROWS.create_block() で明示生成(ezdxfの罠)
  4. dimdsep=46(明示しないとカンマ区切りになるezdxfの罠)
  5. 寸法線オフセットは輪郭から16mm、2段目以降 +8mm 刻み
  6. 穴注記は %%c 制御コード・半角統一(裁定Q5)
  7. 公差ゼロ側は描画後に text を「0」へ整形(裁定の追記。dimtzinでは再現不可)
  8. **ゲート①内蔵**: 各寸法について
       (a) 測定点が実ジオメトリの特徴点に一致するか(snap検証)
       (b) defpointから再計算した実測値 == 計画のvalue_expected
       (c) 実測値 == DXFに描かれた寸法文字の数値
       (d) (任意)別ビューの実在円との突き合わせ(cross_check)
     を検証し、0.01mmを超えるずれがあれば **保存せずに例外で停止する**。

CLI:
    python engine/dim_engine.py <plan.json> <out.dxf> [--png <out.png>]
"""
import io
import json
import math
import os
import re
import sys

import ezdxf
from ezdxf.bbox import extents as bbox_extents
from ezdxf.render.arrows import ARROWS

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import compose_drawing  # noqa: E402
from engine.frame_extract import subtract_frame  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIMSTYLE_SPEC_DEFAULT = os.path.join(ROOT, u"図枠", u"dimstyle_spec.json")

VIEW_KEYS = compose_drawing.VIEW_KEYS

# 図枠バケツAの禁止領域(compose_drawing と同じ定数)
FRAME_RECT = (compose_drawing.FRAME_X0, compose_drawing.FRAME_Y0,
              compose_drawing.FRAME_X1, compose_drawing.FRAME_Y1)
TITLE_BLOCK_RECT = compose_drawing.TITLE_BLOCK_RECT
NOTE_ZONE_RECT = compose_drawing.NOTE_ZONE_RECT

# ビュー分類の許容マージン(mm)。中心マークINSERT等が輪郭からわずかにはみ出すため
CLASSIFY_MARGIN_MM = 8.0

ATTACH = compose_drawing.ATTACH


class DimensionGateError(Exception):
    u"""ゲート①(寸法値照合)不合格。不合格品は保存しない。"""


# ---------------------------------------------------------------------------
# dimstyle_spec.json の読み込み
# ---------------------------------------------------------------------------
def load_dimstyle_spec(path=DIMSTYLE_SPEC_DEFAULT):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def base_dimvars(spec):
    u"""dimstyle_spec.json の dimstyle_base/arrow から、ezdxfへ設定するDIMSTYLE変数dictを作る。"""
    b = spec["dimstyle_base"]
    v = {
        "dimtxt": b["dimtxt"]["value"],
        "dimasz": b["dimasz"]["value"],
        "dimexo": b["dimexo"]["value"],
        "dimexe": b["dimexe"]["value"],
        "dimgap": b["dimgap"]["value"],
        "dimdec": b["dimdec"]["value"],
        "dimtad": b["dimtad"]["value"],
        "dimclrd": b["dimclrd"]["value"],
        "dimclre": b["dimclre"]["value"],
        "dimclrt": b["dimclrt"]["value"],
        "dimdli": b["dimdli"]["value"],
        "dimscale": b["dimscale"]["value"],
        "dimlfac": b["dimlfac"]["value"],
        "dimsah": b["dimsah"]["value"],
        "dimjust": b["dimjust"]["value"],
        "dimcen": b["dimcen"]["value"],
        # ❗dimfit(群コード287)はR13/R14の旧変数。ezdxfはAC1015保存時にこれを書き出さず
        #   dimatfit(289)+dimtmove(279)へ自動変換する(実測確認済み)。
        #   一方 ACROVA GMM の実ファイルは dimfit を持ち dimatfit を持たない。
        #   AutoCADの公式等価変換 DIMFIT=3 → DIMATFIT=3 / DIMTMOVE=0 を明示設定し、
        #   読み戻し検証も dimatfit/dimtmove で行う(dimfitで検証すると必ずNoneで落ちる)。
        "dimatfit": b["dimfit"]["value"],
        "dimtmove": 0,
        # ezdxf既定はカンマ(',')。実在図面はピリオド表記のため必ず46('.')を明示する
        "dimdsep": 46,
        # コーパス実測 dimzin=8(末尾ゼロ抑制。『40.00』でなく『40』表記)。
        # ezdxfの既定も8だが、依存せず明示する
        "dimzin": 8,
        "dimblk1": spec["arrow"]["dimblk1"]["value"],
        "dimblk2": spec["arrow"]["dimblk2"]["value"],
    }
    return v


# ---------------------------------------------------------------------------
# 座標変換(モデル3D → 最終A3図面)
# ---------------------------------------------------------------------------
def build_view_transforms(meta_json_path, scale):
    u"""phase2 meta + compose の再レイアウト計算から、各ビューの
    「モデル3D座標 → 最終A3図面座標」変換と、ビュー幾何外接矩形を復元する。

    Returns: dict view_key -> {
        "model_to_sheet": callable((x,y,z)) -> (sx, sy)   # phase2シート座標
        "sheet_to_draw":  callable((sx,sy)) -> (dx, dy)   # 最終図面座標
        "model_to_draw":  callable((x,y,z)) -> (dx, dy)
        "region":         (x0,y0,x1,y1)  # ビュー幾何の外接矩形(最終図面座標)
    }
    """
    with io.open(meta_json_path, encoding="utf-8") as f:
        meta = json.load(f)
    geoms = {k: meta["views"][k]["geom_mm"] for k in VIEW_KEYS}
    targets, centers, sizes = compose_drawing._layout_targets(geoms, scale)

    out = {}
    for k in VIEW_KEYS:
        arr = meta["views"][k]["model_to_view"]
        r = arr[0:9]
        tx, ty = arr[9], arr[10]
        s = arr[12]
        ocx, ocy = centers[k]
        ncx, ncy = targets[k]

        def make_m2s(r=r, tx=tx, ty=ty, s=s):
            def f(p):
                x, y, z = p[0], p[1], p[2] if len(p) > 2 else 0.0
                sx = s * (r[0] * x + r[3] * y + r[6] * z) + tx * 1000.0
                sy = s * (r[1] * x + r[4] * y + r[7] * z) + ty * 1000.0
                return (sx, sy)
            return f

        def make_s2d(ocx=ocx, ocy=ocy, ncx=ncx, ncy=ncy, scale=scale):
            def f(p):
                return ((p[0] - ocx) * scale + ncx, (p[1] - ocy) * scale + ncy)
            return f

        m2s = make_m2s()
        s2d = make_s2d()
        w, h = sizes[k]
        out[k] = {
            "model_to_sheet": m2s,
            "sheet_to_draw": s2d,
            "model_to_draw": (lambda m2s=m2s, s2d=s2d: (lambda p: s2d(m2s(p))))(),
            "region": (ncx - w / 2.0, ncy - h / 2.0, ncx + w / 2.0, ncy + h / 2.0),
        }
    return out


# ---------------------------------------------------------------------------
# ビュー分類・特徴点抽出(ゲート①(a)の材料)
# ---------------------------------------------------------------------------
def _is_centermark(e):
    return (e.dxftype() == "INSERT"
            and str(e.dxf.name).upper().startswith("SW_CENTERMARKSYMBOL"))


def classify_view_geometry(entities, regions, margin=CLASSIFY_MARGIN_MM):
    u"""部品コンテンツ(図枠を差し引いた残り)の幾何エンティティをビュー別に分類する。
    中心マークINSERTは輪郭・特徴点の対象外(CLAUDE.md知見: bbox計算時は除外)。
    **図枠を先に subtract_frame() で差し引いておくこと**(図枠外枠の中心がisoビュー領域に
    入ってしまい、ビュー輪郭が図枠全体に化ける実害を確認済み)。"""
    per_view = {k: [] for k in regions}
    for e in entities:
        t = e.dxftype()
        if t in ("MTEXT", "TEXT", "DIMENSION", "LEADER", "POINT"):
            continue
        if _is_centermark(e):
            continue
        bb = bbox_extents([e], fast=True)
        if bb is None or not bb.has_data:
            continue
        cx = (bb.extmin.x + bb.extmax.x) / 2.0
        cy = (bb.extmin.y + bb.extmax.y) / 2.0
        for k in regions:
            x0, y0, x1, y1 = regions[k]
            if x0 - margin <= cx <= x1 + margin and y0 - margin <= cy <= y1 + margin:
                per_view[k].append(e)
                break
    return per_view


def feature_points(entities):
    u"""実ジオメトリの特徴点(線分端点・円/円弧の中心と四分点・ポリライン頂点)を列挙する。"""
    pts = []
    for e in entities:
        t = e.dxftype()
        if t == "LINE":
            s, en = e.dxf.start, e.dxf.end
            pts.append((s.x, s.y))
            pts.append((en.x, en.y))
        elif t == "CIRCLE":
            c, r = e.dxf.center, e.dxf.radius
            pts.append((c.x, c.y))
            for a in (0.0, 90.0, 180.0, 270.0):
                pts.append((c.x + r * math.cos(math.radians(a)),
                            c.y + r * math.sin(math.radians(a))))
        elif t == "ARC":
            c, r = e.dxf.center, e.dxf.radius
            a0, a1 = e.dxf.start_angle, e.dxf.end_angle
            pts.append((c.x, c.y))
            for a in (a0, a1, 0.0, 90.0, 180.0, 270.0):
                span0, span1 = a0 % 360.0, a1 % 360.0
                aa = a % 360.0
                inside = (span0 <= aa <= span1) if span0 <= span1 else (aa >= span0 or aa <= span1)
                if a in (a0, a1) or inside:
                    pts.append((c.x + r * math.cos(math.radians(a)),
                                c.y + r * math.sin(math.radians(a))))
        elif t == "LWPOLYLINE":
            for p in e.get_points("xy"):
                pts.append((p[0], p[1]))
        elif t in ("ELLIPSE", "SPLINE"):
            for p in e.flattening(0.02):
                pts.append((p[0], p[1]))
    return pts


def nearest_feature_distance(pt, pts):
    best = float("inf")
    for q in pts:
        d = math.hypot(pt[0] - q[0], pt[1] - q[1])
        if d < best:
            best = d
    return best


def find_circle(entities, center, diameter, tol):
    u"""指定中心・直径のCIRCLE/ARCを実ジオメトリから探す(cross_check用)。"""
    for e in entities:
        if e.dxftype() not in ("CIRCLE", "ARC"):
            continue
        c, r = e.dxf.center, e.dxf.radius
        if (math.hypot(c.x - center[0], c.y - center[1]) <= tol
                and abs(2.0 * r - diameter) <= tol):
            return e
    return None


# ---------------------------------------------------------------------------
# DIMSTYLE / STYLE 生成
# ---------------------------------------------------------------------------
def ensure_text_style(doc, spec):
    ts = spec["text_style"]
    return compose_drawing._ensure_style(doc, ts["font"], ts["width_factor"],
                                         ts.get("oblique", 0.0))


def ensure_arrow_blocks(doc, names=("_OPEN30",)):
    u"""_OPEN30等の標準矢印ブロックはezdxf.new(setup=True)でも未登録のことがある。明示生成する。"""
    created = []
    for n in names:
        key = n.lstrip("_").lower()  # "_OPEN30" -> "open30"
        if n in doc.blocks:
            continue
        arrow_name = {"open30": ARROWS.open_30, "dot": ARROWS.dot,
                      "closedfilled": ARROWS.closed_filled}.get(key)
        if arrow_name is None:
            continue
        ARROWS.create_block(doc.blocks, arrow_name)
        created.append(n)
    return created


def _new_dimstyle(doc, name, dimvars):
    ds = doc.dimstyles.add(name)
    for k, v in dimvars.items():
        ds.set_dxf_attrib(k, v)
    return ds


# ---------------------------------------------------------------------------
# 公差テキスト整形(裁定: ゼロ側は「0」表記)
# ---------------------------------------------------------------------------
_TOL_STACK_RE = re.compile(r"\\S([^\^]*)\^ ([^;]*);")


def _zero_side_to_0(s):
    body = s.strip()
    sign = ""
    if body[:1] in ("+", "-"):
        sign, body = body[0], body[1:]
    try:
        if abs(float(body)) < 1e-12:
            lead = s[:len(s) - len(s.lstrip())]
            return lead + sign + "0"
    except ValueError:
        pass
    return s


def fix_zero_tolerance_text(doc, dim):
    u"""描画済みDIMENSIONの寸法文字MTEXT内で、公差のゼロ側を『0.000』→『0』へ整形する。
    (裁定: dimtzinでは再現できないためtext側で整形する)"""
    geom = dim.dxf.get("geometry", None)
    if not geom or geom not in doc.blocks:
        return False
    changed = False
    for e in doc.blocks.get(geom):
        if e.dxftype() != "MTEXT":
            continue
        raw = e.text
        new = _TOL_STACK_RE.sub(
            lambda m: "\\S%s^ %s;" % (_zero_side_to_0(m.group(1)), _zero_side_to_0(m.group(2))),
            raw)
        if new != raw:
            e.text = new
            changed = True
    return changed


# ---------------------------------------------------------------------------
# 寸法文字の読み取り(ゲート①(c))
# ---------------------------------------------------------------------------
_MTEXT_CODE_RE = re.compile(r"\\[A-Za-z][^;]*;")


def dim_text_of(doc, dim):
    geom = dim.dxf.get("geometry", None)
    if not geom or geom not in doc.blocks:
        return None
    for e in doc.blocks.get(geom):
        if e.dxftype() == "MTEXT":
            return e.text
        if e.dxftype() == "TEXT":
            return e.dxf.text
    return None


def parse_dim_text_value(raw):
    u"""寸法文字から数値部分を取り出す(dimpost接頭辞・公差スタック・書式コードを除去)。"""
    if raw is None:
        return None
    s = re.sub(r"\{[^{}]*\}", "", raw)          # 公差スタック {\H0.62x;\S...;}
    s = _MTEXT_CODE_RE.sub("", s)                # \A1; \H0.62x; 等
    s = s.replace("%%c", "").replace("%%C", "").replace("%%p", "")
    s = s.replace("(", "").replace(")", "")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


# ---------------------------------------------------------------------------
# 実測値の再計算(defpointから)
# ---------------------------------------------------------------------------
def measure_from_defpoints(dim):
    d = dim.dxf
    base = d.dimtype & 7
    if base in (0, 1):
        p2, p3 = d.defpoint2, d.defpoint3
        if base == 0:
            a = math.radians(d.get("angle", 0.0))
            return abs((p3.x - p2.x) * math.cos(a) + (p3.y - p2.y) * math.sin(a))
        return math.hypot(p3.x - p2.x, p3.y - p2.y)
    if base == 4:   # radius
        return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y)
    if base == 3:   # diameter
        return math.hypot(d.defpoint4.x - d.defpoint.x, d.defpoint4.y - d.defpoint.y)
    if base == 2:   # angular
        return None
    return None


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
_SIDE_ANGLE = {"above": 0.0, "below": 0.0, "left": 90.0, "right": 90.0}
_DIRECTION_ANGLE = {"horizontal": 0.0, "vertical": 90.0}


def resolve_direction(measure, side):
    d = measure.get("direction")
    if d is None:
        return _SIDE_ANGLE[side]
    if isinstance(d, (int, float)):
        return float(d)
    return _DIRECTION_ANGLE[d]


def base_point(region, side, p1, p2, offset):
    x0, y0, x1, y1 = region
    mx = (p1[0] + p2[0]) / 2.0
    my = (p1[1] + p2[1]) / 2.0
    if side == "above":
        return (mx, y1 + offset)
    if side == "below":
        return (mx, y0 - offset)
    if side == "left":
        return (x0 - offset, my)
    if side == "right":
        return (x1 + offset, my)
    raise ValueError("unknown placement.side: %s" % side)


# ---------------------------------------------------------------------------
# レイアウト(テキスト矩形と衝突検出。初歩的なもの・フェーズ4で改善)
# ---------------------------------------------------------------------------
def _text_box(doc, dim, width_factor=0.75):
    geom = dim.dxf.get("geometry", None)
    if not geom or geom not in doc.blocks:
        return None
    for e in doc.blocks.get(geom):
        if e.dxftype() != "MTEXT":
            continue
        raw = _MTEXT_CODE_RE.sub("", re.sub(r"\{[^{}]*\}", "", e.text))
        raw = raw.replace("%%c", "f").replace("%%p", "f")
        n = max(1, len(raw))
        h = e.dxf.char_height
        w = n * h * width_factor
        rot = e.dxf.get("rotation", 0.0) % 180.0
        if abs(rot - 90.0) < 1.0:
            w, h = h, w
        p = e.dxf.insert
        return (p.x - w / 2.0, p.y - h / 2.0, p.x + w / 2.0, p.y + h / 2.0)
    return None


def _rect_overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _rect_inside(a, b):
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def apply_plan(plan_path, out_dxf_path, dimstyle_spec_path=DIMSTYLE_SPEC_DEFAULT,
               base_dxf_override=None):
    u"""作図計画JSONを実行し、寸法・注記入りDXFを出力する。

    ゲート①(寸法値照合)に1本でも不合格があれば DimensionGateError を送出し、
    **DXFは保存しない**(不合格品を納品しない原則)。
    """
    with io.open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    spec = load_dimstyle_spec(dimstyle_spec_path)

    defaults = plan.get("defaults", {})
    first_offset = float(defaults.get("first_offset_mm", 16.0))
    stack_step = float(defaults.get("stack_step_mm", spec["dimstyle_base"]["dimdli"]["value"]))
    snap_tol = float(defaults.get("snap_tol_mm", 0.01))
    gate_tol = float(defaults.get("gate_tol_mm", 0.01))

    src = plan["source"]
    base_dxf = base_dxf_override or os.path.join(ROOT, src["base_dxf"])
    meta_json = os.path.join(ROOT, src["meta_json"])
    scale = float(src.get("scale", 1.0))

    warnings = []

    # --- 1) 土台DXFとビュー座標系 ---------------------------------------
    doc = ezdxf.readfile(base_dxf)
    doc.encoding = "cp932"
    msp = doc.modelspace()

    tf = build_view_transforms(meta_json, scale)
    regions = {k: tf[k]["region"] for k in tf}
    part_entities, frame_summary = subtract_frame(
        doc, template_path=os.path.join(ROOT, u"図枠", u"frame_template.dxf"))
    per_view = classify_view_geometry(part_entities, regions)
    feats = {k: feature_points(per_view[k]) for k in per_view}

    # ビュー実輪郭(分類済み実ジオメトリの外接矩形)。寸法線オフセットの基準にする
    view_bbox = {}
    for k in per_view:
        if not per_view[k]:
            view_bbox[k] = regions[k]
            continue
        bb = bbox_extents(per_view[k], fast=True)
        view_bbox[k] = (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y)

    # --- 2) スタイル準備 -------------------------------------------------
    text_style = ensure_text_style(doc, spec)
    arrows_created = ensure_arrow_blocks(
        doc, tuple({spec["arrow"]["dimblk1"]["value"], spec["arrow"]["dimblk2"]["value"]}))
    dimvars_base = base_dimvars(spec)
    dimvars_base["dimtxsty"] = text_style
    dim_layer = spec["color_layer"]["dimension_entity_layer"]["value"]
    leader_layer = spec["color_layer"]["leader_entity_layer"]["value"]
    for lname in {dim_layer, leader_layer}:
        if lname not in doc.layers:
            doc.layers.add(lname)

    # --- 3) 測定点の解決 -------------------------------------------------
    def to_draw(view, space, p):
        if space == "model":
            return tf[view]["model_to_draw"](p)
        return (float(p[0]), float(p[1]))

    # --- 4) 寸法生成 -----------------------------------------------------
    gate_rows = []
    dimstyle_records = {}
    text_boxes = {}
    counter = [0]

    def next_style_name():
        counter[0] += 1
        name = "GEN%03d" % counter[0]
        while name in doc.dimstyles:
            counter[0] += 1
            name = "GEN%03d" % counter[0]
        return name

    for item in plan.get("dimensions", []):
        did = item["id"]
        kind = item["kind"]
        view = item["view"]
        if view not in tf:
            raise ValueError("%s: 未知のview '%s'" % (did, view))
        meas = item["measure"]
        space = meas.get("space", "view")
        placement = item.get("placement", {})
        side = placement.get("side", "below")
        level = int(placement.get("level", 1))
        offset = placement.get("offset_mm")
        offset = first_offset + (level - 1) * stack_step if offset is None else float(offset)

        # 専用DIMSTYLE(1寸法=1スタイル)
        dv = dict(dimvars_base)
        if "dimdec" in item:
            dv["dimdec"] = int(item["dimdec"])
        if kind == "diameter_linear":
            dv["dimpost"] = "%%c<>"
        elif kind == "radius":
            dv["dimpost"] = item.get("dimpost", "R<>")
        elif kind == "linear":
            if item.get("dimpost"):
                dv["dimpost"] = item["dimpost"]
        tol = item.get("tolerance")
        text_override = item.get("text_override")
        if tol and tol.get("mode") == "limit":
            dv["dimtol"] = 1
            dv["dimtp"] = float(tol.get("upper", 0.0))
            dv["dimtm"] = abs(float(tol.get("lower", 0.0)))
            dv["dimtfac"] = spec["tolerance"]["native_limit_tolerance"]["params"]["dimtfac"]
            dv["dimtdec"] = int(tol.get("dec", 3))
            dv["dimtolj"] = spec["tolerance"]["native_limit_tolerance"]["params"]["dimtolj"]
        elif tol and tol.get("mode") == "symmetric":
            if text_override is None:
                text_override = "\\A1;%s%%%%p%s" % (
                    _fmt_num(item["value_expected"]), _fmt_num(tol["value"]))
        style_name = next_style_name()
        _new_dimstyle(doc, style_name, dv)

        attribs = {"layer": dim_layer}

        if kind in ("linear", "diameter_linear"):
            p1 = to_draw(view, space, meas["p1"])
            p2 = to_draw(view, space, meas["p2"])
            angle = resolve_direction(meas, side)
            if abs((angle % 180.0) - (_SIDE_ANGLE[side] % 180.0)) > 1e-6:
                raise ValueError(
                    "%s: placement.side='%s' と measure.direction が矛盾しています" % (did, side))
            base = base_point(view_bbox[view], side, p1, p2, offset)
            dim = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle,
                                     dimstyle=style_name, dxfattribs=attribs)
            if text_override:
                dim.dimension.dxf.text = text_override
            dim.render()
            ent = dim.dimension
            meas_pts = [p1, p2]
        elif kind == "radius":
            center = to_draw(view, space, meas["p1"])
            if "radius" in meas:
                radius = float(meas["radius"])
                edge = None
            else:
                edge = to_draw(view, space, meas["p2"])
                radius = math.hypot(edge[0] - center[0], edge[1] - center[1])
            dim = msp.add_radius_dim(center=center, radius=radius,
                                     angle=float(meas.get("leader_angle", 45.0)),
                                     dimstyle=style_name, dxfattribs=attribs)
            if text_override:
                dim.dimension.dxf.text = text_override
            dim.render()
            ent = dim.dimension
            meas_pts = [center] + ([edge] if edge else [])
        elif kind == "angle":
            v = to_draw(view, space, meas["vertex"])
            p1 = to_draw(view, space, meas["p1"])
            p2 = to_draw(view, space, meas["p2"])
            if not text_override:
                raise ValueError("%s: kind='angle' は text_override 必須(§8ルール3)" % did)
            dim = msp.add_angular_dim_2l(
                base=to_draw(view, space, meas["base"]),
                line1=(v, p1), line2=(v, p2),
                dimstyle=style_name, dxfattribs=attribs)
            dim.dimension.dxf.text = text_override
            dim.render()
            ent = dim.dimension
            meas_pts = [v, p1, p2]
        else:
            raise ValueError("%s: 未知のkind '%s'" % (did, kind))

        if tol and tol.get("mode") == "limit":
            fix_zero_tolerance_text(doc, ent)

        # ---- ゲート① ----
        snaps = [round(nearest_feature_distance(p, feats[view]), 6) for p in meas_pts]
        measured = measure_from_defpoints(ent)
        expected = float(item["value_expected"])
        raw_text = dim_text_of(doc, ent)
        shown = parse_dim_text_value(raw_text) if not text_override else None

        row = {
            "id": did, "kind": kind, "view": view,
            "expected": expected,
            "measured": None if measured is None else round(measured, 6),
            "diff_mm": None if measured is None else round(abs(measured - expected), 6),
            "snap_max_mm": round(max(snaps), 6) if snaps else None,
            "text": raw_text,
            "text_value": shown,
            "text_diff_mm": (None if (shown is None or measured is None)
                             else round(abs(shown - measured), 6)),
            "style": style_name,
            "ok": True, "errors": [],
        }
        if row["snap_max_mm"] is not None and row["snap_max_mm"] > snap_tol:
            row["errors"].append(
                u"測定点が実ジオメトリ特徴点に一致しない(最大%.4fmm > %.4fmm)"
                % (row["snap_max_mm"], snap_tol))
        if measured is None:
            row["errors"].append(u"実測値を再計算できない(kind=%s)" % kind)
        elif row["diff_mm"] > gate_tol:
            row["errors"].append(u"実測 %.4f vs 期待 %.4f (差 %.4fmm > %.4fmm)"
                                 % (measured, expected, row["diff_mm"], gate_tol))
        if row["text_diff_mm"] is not None and row["text_diff_mm"] > gate_tol:
            row["errors"].append(u"寸法文字 %.4f が実測 %.4f と不一致"
                                 % (shown, measured))

        cc = item.get("cross_check")
        if cc:
            ccv = cc["view"]
            ent_c = find_circle(per_view[ccv], to_draw(ccv, cc.get("space", "view"), cc["center"]),
                                float(cc["diameter"]), snap_tol)
            if ent_c is None:
                row["errors"].append(
                    u"cross_check: %s ビューに中心%s 直径%.4f の円が実在しない"
                    % (ccv, cc["center"], float(cc["diameter"])))
                row["cross_check"] = {"ok": False}
            else:
                real_d = ent_c.dxf.radius * 2.0
                d = abs(real_d - (measured if measured is not None else float("nan")))
                row["cross_check"] = {"ok": d <= gate_tol, "view": ccv,
                                      "found_diameter": round(real_d, 6),
                                      "diff_vs_measured_mm": round(d, 6)}
                if d > gate_tol:
                    row["errors"].append(
                        u"cross_check: 実在円φ%.4f と実測 %.4f が不一致" % (real_d, measured))

        row["ok"] = not row["errors"]
        gate_rows.append(row)
        dimstyle_records[did] = style_name
        tb = _text_box(doc, ent, spec["text_style"]["width_factor"])
        if tb:
            text_boxes[did] = [round(v, 4) for v in tb]

    # --- 5) 穴注記(LEADER + MTEXT) --------------------------------------
    note_rows = []
    if plan.get("hole_notes"):
        ldr_style = "GENLDR"
        if ldr_style not in doc.dimstyles:
            lv = dict(dimvars_base)
            lv.pop("dimpost", None)
            _new_dimstyle(doc, ldr_style, lv)
    for note in plan.get("hole_notes", []):
        view = note["view"]
        space = note.get("leader", {}).get("space", "view")
        pts = [to_draw(view, space, p) for p in note["leader"]["points"]]
        leader = msp.add_leader(pts, dimstyle=ldr_style,
                                dxfattribs={"layer": leader_layer})
        ins = to_draw(view, note.get("text_space", "view"), note["text_insert"])
        msp.add_mtext(note["pattern"], dxfattribs={
            "style": text_style,
            "char_height": dimvars_base["dimtxt"],
            "attachment_point": ATTACH.get(note.get("attachment", "bottom-left"), 7),
            "insert": (ins[0], ins[1], 0.0),
            "layer": leader_layer,
        })
        nrow = {"id": note["id"], "view": view, "pattern": note["pattern"],
                "leader_points": [[round(p[0], 4), round(p[1], 4)] for p in pts],
                "text_insert": [round(ins[0], 4), round(ins[1], 4)],
                "ok": True, "errors": []}
        if "\u03c6" in note["pattern"] or "\u03a6" in note["pattern"]:
            nrow["errors"].append(u"φのUnicode文字は禁止(%%cを使うこと)")
        ac = note.get("anchor_check")
        if ac:
            c = to_draw(ac["view"], ac.get("space", "view"), ac["center"])
            r = float(ac["diameter"]) / 2.0
            d = abs(math.hypot(pts[0][0] - c[0], pts[0][1] - c[1]) - r)
            nrow["anchor_check"] = {"ok": d <= snap_tol, "dist_err_mm": round(d, 6)}
            if find_circle(per_view[ac["view"]], c, float(ac["diameter"]), snap_tol) is None:
                nrow["errors"].append(u"anchor_check: 指定の円が実在しない")
            if d > snap_tol:
                nrow["errors"].append(u"anchor_check: 引出線始点が円周上にない(%.4fmm)" % d)
        nrow["ok"] = not nrow["errors"]
        note_rows.append(nrow)
        h = dimvars_base["dimtxt"]
        lines = note["pattern"].split("\\P")
        w = max(len(_MTEXT_CODE_RE.sub("", l).replace("%%c", "f")) for l in lines) * h * \
            spec["text_style"]["width_factor"]
        text_boxes[note["id"]] = [round(ins[0], 4), round(ins[1], 4),
                                  round(ins[0] + w, 4), round(ins[1] + len(lines) * h * 1.3, 4)]
        nrow["leader_handle"] = leader.dxf.handle

    # --- 6) 自由注記 ------------------------------------------------------
    for n in plan.get("notes", []):
        h = float(n.get("height", 3.5))
        ins = n["insert"]
        msp.add_mtext(n["text"], dxfattribs={
            "style": text_style,
            "char_height": h,
            "attachment_point": ATTACH.get(n.get("attachment", "top-left"), 1),
            "insert": (ins[0], ins[1], 0.0),
            "layer": "0",
        })
        lines = n["text"].split("\\P")
        w = max(len(_MTEXT_CODE_RE.sub("", l)) for l in lines) * h * 0.9
        top = ins[1] if n.get("attachment", "top-left").startswith("top") else \
            ins[1] + len(lines) * h * 1.3
        text_boxes[n["id"]] = [round(ins[0], 4), round(top - len(lines) * h * 1.3, 4),
                               round(ins[0] + w, 4), round(top, 4)]

    # --- 7) レイアウト衝突チェック(初歩的) --------------------------------
    collisions = []
    keys = list(text_boxes)
    for i in range(len(keys)):
        a = text_boxes[keys[i]]
        for k in view_bbox:
            if _rect_overlap(a, view_bbox[k]):
                collisions.append([keys[i], "view:%s" % k])
        if _rect_overlap(a, TITLE_BLOCK_RECT):
            collisions.append([keys[i], "title_block"])
        if _rect_overlap(a, NOTE_ZONE_RECT):
            collisions.append([keys[i], "note_zone"])
        if not _rect_inside(a, FRAME_RECT):
            collisions.append([keys[i], "outside_frame"])
        for j in range(i + 1, len(keys)):
            if _rect_overlap(a, text_boxes[keys[j]]):
                collisions.append([keys[i], keys[j]])
    if collisions:
        warnings.append(u"レイアウト衝突(要目視確認) %d件: %s" % (len(collisions), collisions))

    # --- 8) スタイル読み戻し検証 -------------------------------------------
    style_check = _check_dimstyles(doc, dimstyle_records, dimvars_base, spec)

    gate_ok = all(r["ok"] for r in gate_rows) and all(r["ok"] for r in note_rows)

    report = {
        "out_path": out_dxf_path,
        "base_dxf": base_dxf,
        "gate1": gate_rows,
        "gate1_ok": gate_ok,
        "hole_notes": note_rows,
        "dimstyles": dimstyle_records,
        "style_check": style_check,
        "arrow_blocks_created": arrows_created,
        "frame_check": frame_summary,
        "view_bbox": {k: [round(v, 4) for v in view_bbox[k]] for k in view_bbox},
        "layout": {"text_boxes": text_boxes, "collisions": collisions},
        "warnings": warnings,
    }

    if not gate_ok:
        bad = [r for r in gate_rows + note_rows if not r["ok"]]
        raise DimensionGateError(
            u"ゲート①不合格 %d件(DXFは保存していない):\n%s"
            % (len(bad), json.dumps(bad, ensure_ascii=False, indent=2)))

    out_dir = os.path.dirname(out_dxf_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)
    doc.saveas(out_dxf_path)
    return report


def _fmt_num(v):
    return ("%g" % v)


def _check_dimstyles(doc, records, dimvars_base, spec):
    u"""生成したDIMSTYLEの実効値がdimstyle_spec.jsonと一致するか読み戻して検証する。"""
    mismatches = []
    checked = {}
    watch = [k for k in dimvars_base if k not in ("dimpost",)]
    for did, name in records.items():
        ds = doc.dimstyles.get(name)
        eff = {}
        for k in watch:
            got = ds.dxf.get(k, None)
            want = dimvars_base[k]
            eff[k] = got
            if isinstance(want, float):
                bad = got is None or abs(float(got) - want) > 1e-9
            else:
                bad = str(got) != str(want)
            if bad:
                mismatches.append({"dim": did, "style": name, "var": k,
                                   "expected": want, "actual": got})
        eff["dimpost"] = ds.dxf.get("dimpost", "")
        eff["dimtol"] = ds.dxf.get("dimtol", 0)
        checked[did] = eff
    return {"ok": not mismatches, "mismatches": mismatches, "effective": checked}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if len(argv) < 3:
        print(__doc__)
        return 2
    plan_path, out_dxf = argv[1], argv[2]
    png = None
    if "--png" in argv:
        png = argv[argv.index("--png") + 1]
    rep = apply_plan(plan_path, out_dxf)
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    if png:
        compose_drawing.render_png(out_dxf, png)
        print("saved", png)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
