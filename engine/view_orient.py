# -*- coding: utf-8 -*-
u"""正面ビュー選定ルール(フェーズ4本体・step_import_report.md §7-1 の申し送りの実装)。

**なぜ要るか**: STEPの座標系は部品ごとにバラバラで(3-05だけ主軸がY・原点も端/中心が混在)、
「STEPの正面 = 人間の正面」は成立しない。一方で補正に必要なのは **90度単位の回転だけ**で
鏡像は不要であることが実証済み(1-25走行LSドグで恒等が唯一解・det=+1)。
そこで **形状(面情報)から決定論的に向きを決める**。

決め方(3段階)
  1. `face_survey`  : SWの面情報を採る(ISurface.CylinderParams の軸/半径/面積・平面の法線/面積)
  2. `classify`     : 形状クラス判定 旋盤物 / 板物 / 穴軸物 / その他
  3. `presentation_frame` → `view_plan`
                    : 「モデルのどの向きを正面にするか + 紙面内回転」を決め、
                      標準4ビューそれぞれに使う **SWビュー名 + 紙面内回転角(90度単位)** に落とす

------------------------------------------------------------------ 実測(このモジュールの前提)
**SW標準ビューの軸マップ**(`調査/probe_view_angle.py` で ModelToViewTransform から実測。
第三角法・SW2023):

    ビュー      paper_x   paper_y   out(紙面手前)
    *正面        +X        +Y        +Z
    *背面        -X        +Y        -Z
    *左側面      +Z        +Y        -X
    *右側面      -Z        +Y        +X
    *平面        +X        -Z        +Y
    *底面        +X        +Z        -Y

**❗`IView.Angle` は radian**(90 を代入すると 90rad=2.0354rad に折り返す)。
**正 = 画像が反時計回り(CCW)**: `*正面` に +pi/2 を入れると paper_x が +X→-Y に変わる
(= モデル+X が紙面+yへ移動)。代入後は必ず読み戻すこと。
"""
import math

# ------------------------------------------------------------------ 定数(閾値)
#: 円筒軸がモデル主軸に一致しているとみなす許容(|cos| >= これ)
AXIS_COS_TOL = 0.999
#: 主軸に直交する2辺が「等しい(=断面が丸い可能性)」とみなす相対差
ROUND_SECTION_TOL = 0.03
#: 外径円筒が断面を占めているとみなす比(最大円筒径 / 直交方向の最大辺)
OUTER_CYL_RATIO = 0.95
#: 最小辺/中間辺 がこれ未満なら板物
PLATE_RATIO = 0.5
#: 円筒面の面積比がこれ未満なら「主軸なし」とみなす
CYL_AREA_FRAC_MIN = 0.10
#: 同軸グループを同一とみなす軸線の位置許容(mm)
COAXIAL_TOL_MM = 0.05
#: 直径が別物とみなす差(mm)
DIAM_DISTINCT_MM = 0.2

SURF_PLANE = 4001
SURF_CYLINDER = 4002
SURF_CONE = 4003
SURF_SPHERE = 4004
SURF_TORUS = 4005

AXIS_NAME = ("X", "Y", "Z")

#: SW標準ビュー: name -> (paper_x, paper_y, out) をモデル軸ベクトルで(実測値・上のdocstring参照)
SW_STANDARD_VIEWS = (
    (u"*正面",   (1, 0, 0),  (0, 1, 0),  (0, 0, 1)),
    (u"*背面",   (-1, 0, 0), (0, 1, 0),  (0, 0, -1)),
    (u"*左側面", (0, 0, 1),  (0, 1, 0),  (-1, 0, 0)),
    (u"*右側面", (0, 0, -1), (0, 1, 0),  (1, 0, 0)),
    (u"*平面",   (1, 0, 0),  (0, 0, -1), (0, 1, 0)),
    (u"*底面",   (1, 0, 0),  (0, 0, 1),  (0, -1, 0)),
)


# ------------------------------------------------------------------ ベクトル小物
def _neg(v):
    return (-v[0], -v[1], -v[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit_axis(i, sign=1):
    v = [0, 0, 0]
    v[i] = sign
    return tuple(v)


def _snap_axis(vec):
    u"""ベクトルを主軸に丸める。→ (軸index, 符号) / 主軸に乗っていなければ None。"""
    n = math.sqrt(sum(c * c for c in vec)) or 1.0
    u = [c / n for c in vec]
    for i in range(3):
        if abs(u[i]) >= AXIS_COS_TOL:
            return i, (1 if u[i] > 0 else -1)
    return None


# ------------------------------------------------------------------ 1. 面情報の採取
def face_survey(mod, opened, prop):
    u"""SWで開いた部品から面情報を採る。

    `prop` は `draw_pipeline.prop`(gen_py でプロパティ/メソッドが揺れるのを吸収する)を渡す。
    戻り値の単位は mm / mm^2。
    """
    ip = mod.IPartDoc(opened.doc._oleobj_)
    bodies = ip.GetBodies2(0, True) or []
    cylinders = []
    planes = []
    counts = {}
    total_area = 0.0
    for b in bodies:
        ib = mod.IBody2(b._oleobj_)
        for f in list(prop(ib, "GetFaces") or []):
            iface = mod.IFace2(f._oleobj_)
            surf = mod.ISurface(prop(iface, "GetSurface")._oleobj_)
            st = prop(surf, "Identity")
            area = float(prop(iface, "GetArea")) * 1e6
            total_area += area
            counts[st] = counts.get(st, 0) + 1
            if st == SURF_CYLINDER:
                par = list(prop(surf, "CylinderParams"))
                # [ox, oy, oz, ax, ay, az, radius] (m)
                cylinders.append({
                    "d_mm": par[6] * 2000.0,
                    "axis": [par[3], par[4], par[5]],
                    "origin_mm": [par[0] * 1000.0, par[1] * 1000.0, par[2] * 1000.0],
                    "area_mm2": area,
                })
            elif st == SURF_PLANE:
                par = list(prop(surf, "PlaneParams"))
                # [nx, ny, nz, px, py, pz]
                planes.append({
                    "normal": [par[0], par[1], par[2]],
                    "point_mm": [par[3] * 1000.0, par[4] * 1000.0, par[5] * 1000.0],
                    "area_mm2": area,
                })
    return {"cylinders": cylinders, "planes": planes,
            "surface_counts": {str(k): v for k, v in counts.items()},
            "total_area_mm2": total_area, "body_count": len(bodies)}


# ------------------------------------------------------------------ 2. 形状クラス判定
def classify(survey, size_mm, bbox_mm=None):
    u"""形状クラスを決める。戻り値の `shape_class` は lathe / plate / holed_block / other。

    判定の順序に意味がある(実測 5部品で確認):
      1. **旋盤物(lathe)**: 主軸まわりの断面が丸く(外径円筒が断面を占める)、かつ
         **同軸に2つ以上の異なる直径がある(段付き)** か **軸方向に長い**。
         → 25154-3-05 ベアリングカラー(φ40/φ38/φ30 が同軸)がこれ。人間は**輪郭を正面**にする
      2. **板物(plate)**: 最小辺/中間辺 < 0.5。→ 1-09/1-25/1-12。人間は**広い面を正面**にする
         ❗3-05 も 4.75/40=0.12 で板物条件を満たしてしまう。だから旋盤物を先に見る
      3. **穴軸物(holed_block)**: 板でも旋盤物でもないが支配的な円筒軸(穴)がある。
         → 1-18 クッション(25x25x27 の角物に Z軸の段付き穴)。人間は**穴軸を紙面法線**にする
      4. その他: 最小辺方向を紙面法線にする(板物の一般化)
    """
    size = list(size_mm)
    total_area = survey.get("total_area_mm2") or 1.0

    # --- 円筒面を主軸ごとに集計 ---
    axis_area = [0.0, 0.0, 0.0]
    on_axis = {0: [], 1: [], 2: []}
    off_axis_area = 0.0
    for c in survey["cylinders"]:
        sn = _snap_axis(c["axis"])
        if sn is None:
            off_axis_area += c["area_mm2"]
            continue
        i, _sign = sn
        axis_area[i] += c["area_mm2"]
        on_axis[i].append(c)

    main_axis = max(range(3), key=lambda i: axis_area[i]) if max(axis_area) > 0 else None
    cyl_area_frac = (axis_area[main_axis] / total_area) if main_axis is not None else 0.0

    ev = {"axis_area_mm2": [round(a, 3) for a in axis_area],
          "off_axis_cyl_area_mm2": round(off_axis_area, 3),
          "cyl_area_frac": round(cyl_area_frac, 4),
          "size_mm": [round(v, 4) for v in size]}

    # --- 主軸上の同軸グループ(段付き判定) ---
    n_coax_d = 0
    max_d_main = 0.0
    coax_diams = []
    if main_axis is not None:
        perp = [i for i in range(3) if i != main_axis]
        groups = {}
        for c in on_axis[main_axis]:
            key = tuple(round(c["origin_mm"][i] / COAXIAL_TOL_MM) for i in perp)
            groups.setdefault(key, []).append(c)
        if groups:
            best = max(groups.values(), key=lambda g: sum(x["area_mm2"] for x in g))
            ds = sorted({round(c["d_mm"], 3) for c in best}, reverse=True)
            merged = []
            for d in ds:
                if not merged or abs(merged[-1] - d) > DIAM_DISTINCT_MM:
                    merged.append(d)
            coax_diams = merged
            n_coax_d = len(merged)
            max_d_main = merged[0] if merged else 0.0
        ev["main_axis"] = AXIS_NAME[main_axis]
        ev["coaxial_diameters_mm"] = coax_diams
        ev["perp_extents_mm"] = [round(size[perp[0]], 4), round(size[perp[1]], 4)]
        ev["along_extent_mm"] = round(size[main_axis], 4)

        p0, p1 = size[perp[0]], size[perp[1]]
        pmax = max(p0, p1) or 1.0
        round_section = (abs(p0 - p1) / pmax <= ROUND_SECTION_TOL and
                         max_d_main >= OUTER_CYL_RATIO * pmax)
        elongated = size[main_axis] >= pmax
        ev["round_section"] = bool(round_section)
        ev["elongated"] = bool(elongated)
    else:
        ev["main_axis"] = None
        round_section = elongated = False

    order = sorted(range(3), key=lambda i: size[i])       # 小さい順
    ev["extent_order_small_to_large"] = [AXIS_NAME[i] for i in order]
    plate_ratio = size[order[0]] / (size[order[1]] or 1.0)
    ev["plate_ratio"] = round(plate_ratio, 4)

    if (main_axis is not None and cyl_area_frac >= CYL_AREA_FRAC_MIN and
            round_section and (n_coax_d >= 2 or elongated)):
        cls = "lathe"
        reason = u"断面が丸く(外径φ%.2f/直交辺%r)、同軸径%d種・軸方向長%s" % (
            max_d_main, ev["perp_extents_mm"], n_coax_d,
            u"長い" if elongated else u"短い")
    elif plate_ratio < PLATE_RATIO:
        cls = "plate"
        reason = u"最小辺%.3f/中間辺%.3f=%.3f < %.2f" % (
            size[order[0]], size[order[1]], plate_ratio, PLATE_RATIO)
    elif main_axis is not None and cyl_area_frac >= CYL_AREA_FRAC_MIN:
        cls = "holed_block"
        reason = u"板でも旋盤物でもないが %s軸の円筒面が支配的(面積比%.3f)" % (
            AXIS_NAME[main_axis], cyl_area_frac)
    else:
        cls = "other"
        reason = u"支配的な軸も薄さも無い(円筒面積比%.3f)" % cyl_area_frac

    ev["shape_class"] = cls
    ev["reason"] = reason
    ev["_main_axis_idx"] = main_axis
    ev["_extent_order"] = order
    return ev


# ------------------------------------------------------------------ 3. 提示フレーム
#: 既定のルール版。"v1" = 形状主導(30部品バッチで実測済み・正解率44%)
#:                 "v2" = モデル座標系主導+旋盤物だけ上書き(**未検証の仮説**)
RULE_VERSION_DEFAULT = "v1"


def presentation_frame_v2(cls_ev, size_mm):
    u"""【未検証・次バッチで測る仮説】モデル座標系を既定にし、旋盤物だけ上書きする。

    根拠(2026-08-10・30部品バッチの実測):
      - **v1(形状主導)の向き正解率 44.0%(11/25) に対し、「STEPの座標系をそのまま使う」
        対照実験が 60.0%(15/25)** と上回った。教師モデルは元々 SolidWorks で
        「正面(XY)にスケッチして Z に押し出す」流儀で作られており、STEPはその座標系を
        保っているため、**モデルのZ軸が図面の紙面法線である確率が高い**(実測 17/25=68%)。
      - v1 が対照に勝った2件は **旋盤物(3-05)** と 板物の紙面内回転(4-08)。
        負けた6件はすべて「最小辺=板厚を紙面法線にする」判断が曲げ物ブラケットで裏目に出た例。
      - 旋盤物で主軸がZの2件(7-04端子棒/7-05ホルダー)は、人間が
        **紙面法線=+X・紙面水平=-Z**(= SWの `*右側面` を回転0で使うのと同一)を選んでいた。

    したがって v2 は:
      1. 既定 = 恒等(SWの `*正面` をそのまま。紙面内回転も0)
      2. 旋盤物**だけ**、主軸が紙面水平になるよう最小限に上書きする
         主軸X → 恒等 / 主軸Y → ex=+Y, ez=+Z / 主軸Z → ex=-Z, ez=+X(=`*右側面`回転0)
    この見積りでの期待正解率は 18/25=72% だが、**同じデータで作った仮説なので
    次の98部品バッチで独立に検証すること**(自己申告を信用しない)。
    """
    cls = cls_ev["shape_class"]
    main = cls_ev.get("_main_axis_idx")
    if cls == "lathe" and main is not None and main != 0:
        if main == 1:                       # 主軸Y: 紙面水平へ倒す(`*正面`を270度)
            ex, ez = _unit_axis(1, 1), _unit_axis(2, 1)
            rule = u"[v2] 旋盤物: 主軸Yを紙面水平(*正面を270度)"
        else:                               # 主軸Z: `*右側面` 回転0 が人間の選択と一致
            ex, ez = _unit_axis(2, -1), _unit_axis(0, 1)
            rule = u"[v2] 旋盤物: 主軸Zを紙面水平(*右側面を回転0)"
    else:
        ex, ez = _unit_axis(0, 1), _unit_axis(2, 1)
        rule = u"[v2] モデル座標系をそのまま(SWの*正面・回転0)"
    ey = _cross(ez, ex)
    xi = [i for i in range(3) if ex[i]][0]
    zi = [i for i in range(3) if ez[i]][0]
    return {"ex": ex, "ey": ey, "ez": ez,
            "ex_axis": (u"-" if ex[xi] < 0 else u"+") + AXIS_NAME[xi],
            "ez_axis": (u"-" if ez[zi] < 0 else u"+") + AXIS_NAME[zi],
            "rule": rule}


def presentation_frame(cls_ev, size_mm):
    u"""「正面ビューをどう置くか」を右手系フレーム (ex, ey, ez) で返す。

    ex = 紙面右 / ey = 紙面上 / ez = 紙面手前(=正面ビューの視線の逆向き)。
    ey = ez x ex(必ず右手系 = 鏡像しない。鏡像不要は実証済み)。

    ルール
      lathe       : **主軸を紙面水平**(ex = 主軸)。正面=輪郭図 / 右側面=円が見える図
      plate       : **板厚を紙面法線**(ez = 最小辺方向)。ex = 残り2軸のうち長い方
      holed_block : **穴軸を紙面法線**(ez = 主軸)。ex = 残り2軸のうち長い方
      other       : 最小辺方向を紙面法線(板物の一般化)

    符号は常に**モデル軸の正**を採る(ex/ez とも +)。鏡像は作らないので ey は従属。
    """
    size = list(size_mm)
    cls = cls_ev["shape_class"]
    main = cls_ev.get("_main_axis_idx")
    order = cls_ev.get("_extent_order") or sorted(range(3), key=lambda i: size[i])

    def longer_perp(nz):
        perp = [i for i in range(3) if i != nz]
        perp.sort(key=lambda i: (-size[i], i))      # 長い方優先・同点は X<Y<Z
        return perp[0]

    if cls == "lathe":
        xi = main
        # 主軸に直交する軸のうち Z→Y→X の順で紙面法線に採る(モデル座標に素直な向きを優先)
        zi = next(i for i in (2, 1, 0) if i != xi)
    elif cls == "holed_block":
        zi = main
        xi = longer_perp(zi)
    else:                                            # plate / other
        zi = order[0]
        xi = longer_perp(zi)

    if cls == "lathe":
        rule = u"主軸%sを紙面水平(正面=輪郭図 / 右側面=円が見える図)" % AXIS_NAME[xi]
    elif cls == "holed_block":
        rule = u"穴軸%sを紙面法線(正面=穴が円に見える面)" % AXIS_NAME[zi]
    else:
        rule = u"最小辺%s(板厚)を紙面法線(正面=広い面)" % AXIS_NAME[zi]

    ex = _unit_axis(xi, 1)
    ez = _unit_axis(zi, 1)
    ey = _cross(ez, ex)
    return {"ex": ex, "ey": ey, "ez": ez,
            "ex_axis": AXIS_NAME[xi], "ez_axis": AXIS_NAME[zi], "rule": rule}


# ------------------------------------------------------------------ 4. ビュー割り
def _match_view(out_axis, paper_x):
    u"""(紙面手前ベクトル, 紙面右ベクトル) → (SWビュー名, 紙面内回転角[度・CCW])。

    SW標準ビュー V の画像を θ だけ CCW に回すと、V で紙面角 φ に居たモデル方向は φ+θ へ移る。
    欲しい paper_x を紙面角 0 に持ってきたいので **θ = -φ**。
    """
    for name, ux, vy, ot in SW_STANDARD_VIEWS:
        if ot != tuple(out_axis):
            continue
        px = tuple(paper_x)
        for phi, vec in ((0, ux), (90, vy), (180, _neg(ux)), (270, _neg(vy))):
            if vec == px:
                return name, (-phi) % 360
        raise ValueError(u"paper_x %r がビュー %s の軸に乗らない" % (paper_x, name))
    raise ValueError(u"out=%r に一致するSW標準ビューが無い" % (out_axis,))


def view_plan(frame):
    u"""標準4ビューに使う **SWビュー名 + 紙面内回転角(90度単位)** のマッピングを返す。

    第三角法(SW標準ビューの軸マップと同じ並び):
      正面   : 手前=ez  紙面右=ex   紙面上=ey
      平面   : 手前=ey  紙面右=ex   紙面上=-ez   (正面の上に置く)
      右側面 : 手前=ex  紙面右=-ez  紙面上=ey    (正面の右に置く)
      等角投影: SWの `*等角投影` は固定方向で、提示フレームに追従させられない。
                回転0で出し、**向き判定の対象外**として扱う(照合も等角は見ない)
    """
    ex, ey, ez = frame["ex"], frame["ey"], frame["ez"]
    plan = {}
    for key, out, px in (("front", ez, ex),
                         ("top", ey, ex),
                         ("right", ex, _neg(ez))):
        name, deg = _match_view(out, px)
        plan[key] = {"sw_view": name, "rotation_deg": deg,
                     "rotation_rad": math.radians(deg),
                     "out_axis": list(out), "paper_x_axis": list(px)}
    plan["iso"] = {"sw_view": u"*等角投影", "rotation_deg": 0, "rotation_rad": 0.0,
                   "out_axis": None, "paper_x_axis": None,
                   "note": u"SWの等角投影は固定方向。提示フレームに追従しないため向き判定対象外"}
    return plan


def plan_for_part(mod, opened, prop, size_mm, rule=None):
    u"""面情報の採取 → 分類 → フレーム → ビュー割り を一気通貫で行う。

    `rule="v1"`(既定・実測済み)/ `"v2"`(未検証の改良案。`presentation_frame_v2` 参照)。
    """
    rule = rule or RULE_VERSION_DEFAULT
    survey = face_survey(mod, opened, prop)
    ev = classify(survey, size_mm)
    frame = (presentation_frame_v2 if rule == "v2" else presentation_frame)(ev, size_mm)
    plan = view_plan(frame)
    ev_public = {k: v for k, v in ev.items() if not k.startswith("_")}
    return {"survey_summary": {
                "n_cylinders": len(survey["cylinders"]),
                "n_planes": len(survey["planes"]),
                "surface_counts": survey["surface_counts"],
                "total_area_mm2": round(survey["total_area_mm2"], 3),
                "body_count": survey["body_count"]},
            "cylinders": survey["cylinders"],
            "rule_version": rule,
            "classification": ev_public,
            "frame": frame,
            "view_plan": plan}
