# -*- coding: utf-8 -*-
u"""ゲート② 寸法完全性チェック v1(恒久モジュール・フェーズ4)。

問い: **「その図面だけで部品が一意に作れるか」**。
加工者が電卓を叩かず・図面の外の情報を使わずに作れることを、決定論的に検査する。

    check_completeness(dxf_path, plan_path) -> レポートdict

―― v1の判定モデル ――――――――――――――――――――――――――――――――――
1. **フィーチャー棚卸し**: 生成DXFの**実ジオメトリだけ**から特徴を列挙する
   (計画JSONは座標系の復元にしか使わない = 計画の自己申告を信用しない)。
     - 円/円弧 → 直径ごとの `circle` 特徴(中心のモデル座標つき)
     - 軸に垂直な直線エッジ → その軸上の **位置ノード**(X/Y/Z のモデル軸座標)
     - 斜線・スプライン・非軸平行ビュー → **判定対象外**として明示的にリスト化
2. **カバレッジ判定**: 各特徴が下記のどれかで決まるか。
     (a) 寸法で直接指定    : DIMENSION の実測値が直径と一致 / ノード対を結ぶ
     (b) 穴注記でカバー    : `2-8キリ`『%%c11』『M10深さ20』等(ねじは下穴径表で解決)
     (c) 他寸法から算術導出: 位置ノードのグラフ連結(=和・差で到達できる)
     (d) 対称性から導出    : 対称軸まわりの径寸法が両側の位置を決める
     (e) 幾何導出(限定)  : 円筒×平面の交線 y=√((D/2)²-(W/2)²)(二面取りの見え掛かり)
3. どれにも該当しない特徴 = **未指定寸法**(ゲート②不合格理由)
4. どの特徴にも対応しない寸法 = **宙に浮いた寸法**、
   位置チェーンに閉路を作る寸法 = **過剰(冗長)寸法** として警告

―― v1で判定できないもの(黙って無視せず必ず列挙する) ――――――――――――――
   面取り/テーパ(斜線)・面取りに接する位置ノード・面取り由来の円弧径 /
   スプライン・楕円(交差曲線) / 等角投影ビュー / ねじのピッチ・等級 /
   表面性状記号・幾何公差・溶接記号(未実装)

CLI:
    python engine/gate2_completeness.py <plan.json> <generated.dxf> [--json out.json]
    python engine/gate2_completeness.py <plan.json> <generated.dxf> --drop <寸法ID>
        --drop は反証テスト用。その寸法を無かったことにして判定する。
"""
import io
import json
import math
import os
import re
import sys

import ezdxf
from ezdxf.bbox import extents as bbox_extents

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import dim_engine  # noqa: E402
from engine.frame_extract import subtract_frame  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AXES = ("X", "Y", "Z")

# 位置ノードのクラスタリング許容差 / 値一致の許容差(mm)
NODE_TOL = 0.01
VALUE_TOL = 0.01
# 直線を「軸に垂直」と見なす許容差(mm)
ORTHO_TOL = 1e-6
# 斜線を「45度面取り」と見なす許容比
CHAMFER_RATIO_TOL = 0.02

# JIS B 0205 メートル並目ねじの下穴径(呼び -> ドリル径mm)。
# 穴注記『M10深さ20』が図中のφ8.5円をカバーすると判定するために使う。
TAP_DRILL = {
    3: 2.5, 4: 3.3, 5: 4.2, 6: 5.0, 8: 6.8, 10: 8.5, 12: 10.3, 14: 12.0,
    16: 14.0, 18: 15.5, 20: 17.5, 22: 19.5, 24: 21.0, 27: 24.0, 30: 26.5,
}

# 全角ASCII(Ｕ+FF01..FF5E)→半角、全角空白→半角空白、全角マイナス→ハイフン。
# ❗キリ表記の注記は全角(2026-08-09裁定)なので、注記解釈の前に必ず正規化すること
# (『ＰＣＤ６０』が半角前提の正規表現に一切引っかからない実害を確認済み)
_ZEN2HAN = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
_ZEN2HAN[0x3000] = 0x20      # 全角空白
_ZEN2HAN[0x2212] = 0x2D      # 全角マイナス(−)
_ZEN2HAN[0x30FC] = 0x2D      # 長音符(ー)を注記中のハイフン代用として吸収


# ---------------------------------------------------------------------------
# ビューの軸マッピング(モデル軸 -> 図面軸)
# ---------------------------------------------------------------------------
def view_axis_map(model_to_draw):
    u"""ビューの `model_to_draw` から、どのモデル軸が図面のx/yに対応するかを解析する。

    Returns: dict or None(軸平行でないビュー=等角投影などは None)
        {"origin": (ox,oy), "x": ("X", coef), "y": ("Y", coef), "normal": "Z"}
    """
    o = model_to_draw((0.0, 0.0, 0.0))
    d = {}
    for i, a in enumerate(AXES):
        p = [0.0, 0.0, 0.0]
        p[i] = 1.0
        q = model_to_draw(tuple(p))
        d[a] = (q[0] - o[0], q[1] - o[1])
    ax_x = ax_y = normal = None
    for a in AXES:
        dx, dy = d[a]
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            normal = a if normal is None else normal
        elif abs(dy) < 1e-9 and abs(dx) > 1e-9:
            if ax_x is not None:
                return None
            ax_x = (a, dx)
        elif abs(dx) < 1e-9 and abs(dy) > 1e-9:
            if ax_y is not None:
                return None
            ax_y = (a, dy)
        else:
            return None
    if ax_x is None or ax_y is None or normal is None:
        return None
    return {"origin": o, "x": ax_x, "y": ax_y, "normal": normal}


def to_model_coords(amap, p):
    u"""図面座標 -> このビューで見えている2つのモデル軸の座標。"""
    o = amap["origin"]
    ax, cx = amap["x"]
    ay, cy = amap["y"]
    return {ax: (p[0] - o[0]) / cx, ay: (p[1] - o[1]) / cy}


# ---------------------------------------------------------------------------
# 位置ノード集合(1軸ぶん)
# ---------------------------------------------------------------------------
class AxisNodes(object):
    def __init__(self, axis, tol=NODE_TOL):
        self.axis = axis
        self.tol = tol
        self.values = []      # 代表座標
        self.sources = []     # 由来(ビュー・エンティティ種別)
        self.tainted = []     # 面取り等で判定対象外にすべきか

    def add(self, v, source, tainted=False):
        # 座標変換の丸め残差(2.6e-11 や -0.0)がそのままレポートに出るのを防ぐ
        v = round(v, 6) + 0.0
        i = self.index(v)
        if i is None:
            self.values.append(v)
            self.sources.append([source])
            self.tainted.append(tainted)
            return len(self.values) - 1
        if source not in self.sources[i]:
            self.sources[i].append(source)
        if tainted:
            self.tainted[i] = True
        return i

    def index(self, v):
        for i, x in enumerate(self.values):
            if abs(x - v) <= self.tol:
                return i
        return None

    def __len__(self):
        return len(self.values)


class UnionFind(object):
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[rb] = ra
        return True


# ---------------------------------------------------------------------------
# 穴注記の解釈
# ---------------------------------------------------------------------------
_NOTE_RE_COUNT_KIRI = re.compile(r"(\d+)\s*[-−]\s*(\d+(?:\.\d+)?)\s*キリ")
_NOTE_RE_KIRI = re.compile(r"(\d+(?:\.\d+)?)\s*キリ")
_NOTE_RE_PHI = re.compile(r"%%[cC](\d+(?:\.\d+)?)")
_NOTE_RE_TAP = re.compile(r"M(\d+(?:\.\d+)?)")
_NOTE_RE_DEPTH = re.compile(u"深さ\\s*(\\d+(?:\\.\\d+)?)")
_NOTE_RE_PCD = re.compile(r"PCD\s*(\d+(?:\.\d+)?)")


def parse_hole_note(raw):
    u"""穴注記MTEXTを解釈して、カバーする直径・深さ・PCDを取り出す。全角は半角へ正規化する。"""
    s = raw.translate(_ZEN2HAN)
    s = re.sub(r"\\P", " ", s)
    s = re.sub(r"\\[A-Za-z][^;]*;", "", s)
    s = s.replace(u"　", " ")
    dias, taps, depths, pcds = [], [], [], []
    for m in _NOTE_RE_COUNT_KIRI.finditer(s):
        dias.append(float(m.group(2)))
    for m in _NOTE_RE_KIRI.finditer(s):
        dias.append(float(m.group(1)))
    for m in _NOTE_RE_PHI.finditer(s):
        dias.append(float(m.group(1)))
    for m in _NOTE_RE_TAP.finditer(s):
        nominal = float(m.group(1))
        taps.append(nominal)
        drill = TAP_DRILL.get(int(nominal))
        if drill:
            dias.append(drill)
    for m in _NOTE_RE_DEPTH.finditer(s):
        depths.append(float(m.group(1)))
    for m in _NOTE_RE_PCD.finditer(s):
        pcds.append(float(m.group(1)))
    return {"raw": raw, "normalized": s, "diameters": sorted(set(dias)),
            "taps": sorted(set(taps)), "depths": sorted(set(depths)),
            "pcds": sorted(set(pcds))}


# ---------------------------------------------------------------------------
# 本体
# ---------------------------------------------------------------------------
def check_completeness(dxf_path, plan_path, drop_dim_ids=(), verbose=False):
    with io.open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)
    src = plan["source"]
    meta_json = os.path.join(ROOT, src["meta_json"])
    # レイアウト(尺度・使用ビュー・寸法予約帯)は計画から compose と同じ値を取り出す
    scale, use_views, reserves = dim_engine.plan_layout(plan)
    tf = dim_engine.build_view_transforms(meta_json, scale, views=use_views, reserves=reserves)
    regions = {k: tf[k]["region"] for k in tf}

    doc = ezdxf.readfile(dxf_path)
    part_entities, _ = subtract_frame(
        doc, template_path=os.path.join(ROOT, u"図枠", u"frame_template.dxf"))
    per_view = dim_engine.classify_view_geometry(part_entities, regions)

    # --- ビューの軸マッピング -------------------------------------------
    amaps, skipped_views = {}, []
    for k in tf:
        am = view_axis_map(tf[k]["model_to_draw"])
        if am is None:
            skipped_views.append(k)
        else:
            amaps[k] = am

    out_of_scope = []
    for k in skipped_views:
        out_of_scope.append({"class": "view_not_axis_aligned", "view": k,
                             "reason": u"軸平行でない投影(等角投影等)。v1は寸法棚卸しの対象外"})

    # --- 1) フィーチャー棚卸し -------------------------------------------
    nodes = {a: AxisNodes(a) for a in AXES}
    circles = []      # {"view","center":{axis:val},"diameter","entity"}
    obliques = []     # 斜線(面取り/テーパ)
    for k, am in amaps.items():
        ax, ay = am["x"][0], am["y"][0]
        for e in per_view[k]:
            t = e.dxftype()
            if t == "LINE":
                a = to_model_coords(am, (e.dxf.start.x, e.dxf.start.y))
                b = to_model_coords(am, (e.dxf.end.x, e.dxf.end.y))
                dx, dy = b[ax] - a[ax], b[ay] - a[ay]
                if abs(dx) <= ORTHO_TOL and abs(dy) > ORTHO_TOL:
                    nodes[ax].add(a[ax], "%s:LINE" % k)
                elif abs(dy) <= ORTHO_TOL and abs(dx) > ORTHO_TOL:
                    nodes[ay].add(a[ay], "%s:LINE" % k)
                elif abs(dx) > ORTHO_TOL and abs(dy) > ORTHO_TOL:
                    leg = min(abs(dx), abs(dy))
                    is45 = abs(abs(dx) - abs(dy)) <= CHAMFER_RATIO_TOL * max(abs(dx), abs(dy))
                    obliques.append({"view": k, "axes": [ax, ay],
                                     "d": [round(dx, 4), round(dy, 4)],
                                     "kind": "chamfer45" if is45 else "taper",
                                     "leg": round(leg, 4),
                                     "pairs": {ax: (a[ax], b[ax]), ay: (a[ay], b[ay])}})
                    # 斜線の端点も位置ノードとして登録する(黙って消さない)。
                    # 「面取り由来だから判定対象外」の判断は**カバレッジ判定の最後**に行う
                    # (ここで一律に落とすと、面取りが接する重要な位置=端面や外径まで
                    #  判定対象外になってしまい取りこぼす)
                    nodes[ax].add(a[ax], "%s:OBLIQUE" % k)
                    nodes[ax].add(b[ax], "%s:OBLIQUE" % k)
                    nodes[ay].add(a[ay], "%s:OBLIQUE" % k)
                    nodes[ay].add(b[ay], "%s:OBLIQUE" % k)
            elif t in ("CIRCLE", "ARC"):
                c = to_model_coords(am, (e.dxf.center.x, e.dxf.center.y))
                # 直径も**モデル実寸**へ戻す(位置は to_model_coords が既に戻している)。
                # 尺度1:2の図面では図面上の実体径はモデル径の半分になる
                circles.append({"view": k, "center": c, "axes": [ax, ay],
                                "diameter": round(e.dxf.radius * 2.0 / scale, 4)})
                nodes[ax].add(c[ax], "%s:CIRCLE_CENTER" % k)
                nodes[ay].add(c[ay], "%s:CIRCLE_CENTER" % k)
            elif t in ("SPLINE", "ELLIPSE"):
                out_of_scope.append({"class": "curve", "view": k, "type": t,
                                     "reason": u"交差曲線(円筒×平面等)。v1は寸法対象としない"})
            elif t == "LWPOLYLINE":
                pts = list(e.get_points("xy"))
                for i in range(len(pts) - 1):
                    a = to_model_coords(am, pts[i])
                    b = to_model_coords(am, pts[i + 1])
                    if abs(b[ax] - a[ax]) <= ORTHO_TOL:
                        nodes[ax].add(a[ax], "%s:PLINE" % k)
                    elif abs(b[ay] - a[ay]) <= ORTHO_TOL:
                        nodes[ay].add(a[ay], "%s:PLINE" % k)

    # 同一ビュー・同一中心・同一直径の円弧群は1つの円として扱う(円は4分割されて出る)
    uniq = {}
    for c in circles:
        key = (c["view"], c["diameter"],
               round(c["center"][c["axes"][0]], 4), round(c["center"][c["axes"][1]], 4))
        uniq.setdefault(key, c)
    circles = list(uniq.values())

    # 同一直径の円は1特徴にまとめる
    circle_groups = {}
    for c in circles:
        key = (c["diameter"],)
        circle_groups.setdefault(key, []).append(c)

    # --- 2) 図面上の寸法・注記を読む(自己申告でなく実DXFから) --------------
    msp = doc.modelspace()
    plan_ids = [d["id"] for d in plan["dimensions"]]
    dims = []
    for e in msp:
        if e.dxftype() != "DIMENSION":
            continue
        style = e.dxf.dimstyle
        m = re.fullmatch(r"GEN(\d+)", str(style))
        did = plan_ids[int(m.group(1)) - 1] if (m and int(m.group(1)) <= len(plan_ids)) else style
        if did in drop_dim_ids:
            continue
        base = e.dxf.dimtype & 7
        val = dim_engine.measure_model_value(e, scale)   # モデル実寸(尺度を戻した値)
        rec = {"id": did, "style": str(style), "dimtype_base": base,
               "value": None if val is None else round(val, 6), "view": None,
               "axis": None, "coords": None, "role": None}
        # ビュー判定(defpointがどのビュー領域に入るか)
        probe = e.dxf.defpoint2 if base in (0, 1) else e.dxf.defpoint
        for k, r in regions.items():
            if r[0] - 1e-6 <= probe.x <= r[2] + 1e-6 and r[1] - 1e-6 <= probe.y <= r[3] + 1e-6:
                rec["view"] = k
                break
        am = amaps.get(rec["view"])
        if base == 0 and am is not None:
            ang = float(e.dxf.get("angle", 0.0)) % 180.0
            p2 = to_model_coords(am, (e.dxf.defpoint2.x, e.dxf.defpoint2.y))
            p3 = to_model_coords(am, (e.dxf.defpoint3.x, e.dxf.defpoint3.y))
            if abs(ang) < 1e-6:
                axis = am["x"][0]
            elif abs(ang - 90.0) < 1e-6:
                axis = am["y"][0]
            else:
                axis = None
                out_of_scope.append({"class": "oblique_dimension", "id": did, "angle": ang,
                                     "reason": u"軸平行でない方向の線形寸法。v1は棚卸し対象外"})
            if axis:
                rec["axis"] = axis
                rec["coords"] = [p2[axis], p3[axis]]
                rec["role"] = "position_pair"
        elif base in (3, 4) and am is not None:
            rec["role"] = "diameter"
            rec["value"] = round((val if base == 3 else val * 2.0), 6)
        dims.append(rec)

    notes = []
    for e in msp:
        if e.dxftype() != "MTEXT":
            continue
        t = e.text
        if not ("%%c" in t or u"キリ" in t or u"ザグリ" in t or u"深さ" in t
                or re.search(u"[ＭM][０-９0-9]", t)):
            continue
        if u"注記" in t:
            continue
        notes.append(parse_hole_note(t))

    note_dias = sorted({d for n in notes for d in n["diameters"]})
    note_depths = sorted({d for n in notes for d in n["depths"]})
    note_taps = sorted({d for n in notes for d in n["taps"]})

    # 直径として通用する寸法値(径寸法 or dimpostが%%c<>の線形寸法)
    dim_dias = []
    for r in dims:
        if r["role"] == "diameter":
            dim_dias.append(r["value"])
        elif r["role"] == "position_pair":
            st = doc.dimstyles.get(r["style"]) if r["style"] in doc.dimstyles else None
            if st is not None and st.dxf.get("dimpost", "") == "%%c<>":
                dim_dias.append(round(abs(r["coords"][1] - r["coords"][0]), 6))
    covered_dias = sorted(set(dim_dias) | set(note_dias))

    # --- 3) 円(直径)のカバレッジ ---------------------------------------
    def dia_covered(d):
        for x in covered_dias:
            if abs(x - d) <= VALUE_TOL:
                return True
        return False

    chamfer_legs = sorted({o["leg"] for o in obliques if o["kind"] == "chamfer45"})

    circle_report, unspecified = [], []
    for key, group in sorted(circle_groups.items()):
        d = key[0]
        row = {"diameter": d, "count": len(group),
               "views": sorted({g["view"] for g in group})}
        if dia_covered(d):
            row["covered_by"] = "dimension_or_note"
            row["ok"] = True
        else:
            derived = None
            for d0 in covered_dias:
                for leg in chamfer_legs:
                    if abs((d0 - 2.0 * leg) - d) <= VALUE_TOL:
                        derived = u"面取りC%g由来(φ%g-2×%g)" % (leg, d0, leg)
                        break
                if derived:
                    break
            if derived:
                row["covered_by"] = "chamfer_derived"
                row["note"] = derived
                row["ok"] = True
                out_of_scope.append({"class": "chamfer_derived_circle", "diameter": d,
                                     "reason": derived + u" / 面取り自体はv1の判定対象外"})
            else:
                row["covered_by"] = None
                row["ok"] = False
                unspecified.append({"feature": "circle", "diameter": d,
                                    "views": row["views"],
                                    "reason": u"直径φ%g を指定する寸法も穴注記も無い" % d})
        circle_report.append(row)

    # --- 4) 位置ノードのカバレッジ ----------------------------------------
    axis_report = {}
    redundant, floating = [], []
    used_dim_ids = set()

    for a in AXES:
        an = nodes[a]
        n = len(an)
        rep = {"axis": a, "node_count": n,
               "nodes": [round(v, 4) for v in sorted(an.values)], "mode": None,
               "edges": [], "uncovered": [], "out_of_scope_nodes": []}
        if n == 0:
            axis_report[a] = rep
            continue

        lo, hi = min(an.values), max(an.values)
        c0 = (lo + hi) / 2.0
        symmetric = all(an.index(2.0 * c0 - v) is not None for v in an.values)
        rep["mode"] = "symmetric" if symmetric else "chain"
        rep["symmetry_center"] = round(c0, 4) if symmetric else None

        idx_c0 = an.index(c0) if symmetric else None
        if symmetric and idx_c0 is None:
            idx_c0 = an.add(c0, "virtual:symmetry_axis")
            rep["nodes"] = [round(v, 4) for v in sorted(an.values)]
            n = len(an)
        uf = UnionFind(len(an))

        def add_edge(i, j, label, allow_cycle_report=True):
            if i is None or j is None or i == j:
                return
            merged = uf.union(i, j)
            if merged or allow_cycle_report:
                rep["edges"].append({"from": round(an.values[i], 4),
                                     "to": round(an.values[j], 4), "by": label,
                                     "new_link": merged})
            if not merged and allow_cycle_report and not symmetric:
                redundant.append({"axis": a, "by": label,
                                  "from": round(an.values[i], 4),
                                  "to": round(an.values[j], 4),
                                  "reason": u"位置チェーンに閉路を作る(他の寸法の和・差で導出可)"})

        # (a)(c) 寸法による直接指定・チェーン
        for r in dims:
            if r["role"] != "position_pair" or r["axis"] != a:
                continue
            i = an.index(r["coords"][0])
            j = an.index(r["coords"][1])
            if i is None or j is None:
                floating.append({"id": r["id"], "axis": a,
                                 "coords": [round(c, 4) for c in r["coords"]],
                                 "reason": u"寸法の測定点が実ジオメトリの位置ノードに一致しない"})
                continue
            used_dim_ids.add(r["id"])
            if symmetric and abs((r["coords"][0] + r["coords"][1]) / 2.0 - c0) <= NODE_TOL:
                # (d) 対称性: 径・幅寸法は対称軸から両側の位置を決める
                add_edge(idx_c0, i, u"%s(対称・径/幅%.4g)" % (r["id"], abs(r["value"])))
                add_edge(idx_c0, j, u"%s(対称・径/幅%.4g)" % (r["id"], abs(r["value"])))
            else:
                add_edge(i, j, u"%s(%.4g)" % (r["id"], abs(r["value"])))

        # (b) 穴注記・径寸法がカバーする円 -> 中心から半径ぶんの位置を決める
        for c in circles:
            if a not in c["axes"] or not dia_covered(c["diameter"]):
                continue
            ic = an.index(c["center"][a])
            r_ = c["diameter"] / 2.0
            for s in (+1, -1):
                ie = an.index(c["center"][a] + s * r_)
                if ie is not None and ic is not None:
                    add_edge(ic, ie, u"円φ%g(%s)" % (c["diameter"], c["view"]),
                             allow_cycle_report=False)
            if symmetric and ic is not None and abs(c["center"][a] - c0) <= NODE_TOL:
                add_edge(idx_c0, ic, u"円φ%g中心" % c["diameter"], allow_cycle_report=False)

        # (b) 穴注記の「深さ」がカバーする位置(距離が一致するノード対を結ぶ)
        for dep in note_depths:
            cand = []
            for i in range(len(an.values)):
                for j in range(len(an.values)):
                    if i >= j:
                        continue
                    if abs(abs(an.values[i] - an.values[j]) - dep) <= NODE_TOL:
                        cand.append((i, j))
            for i, j in cand:
                if uf.find(i) != uf.find(j):
                    add_edge(i, j, u"注記深さ%g" % dep, allow_cycle_report=False)

        # 到達判定
        if symmetric:
            root = uf.find(idx_c0)
        else:
            counts = {}
            for i in range(len(an.values)):
                counts[uf.find(i)] = counts.get(uf.find(i), 0) + 1
            root = max(counts, key=lambda r_: counts[r_]) if counts else None
        # (e) 幾何導出: 円筒×平面の交線(二面取りの見え掛かり) y=√((D/2)²-(W/2)²)
        widths = _covered_widths(dims)
        for i, v in enumerate(an.values):
            if root is not None and uf.find(i) == root:
                continue
            derived = _chord_derivation(abs(v - c0) if symmetric else None,
                                        covered_dias, widths)
            if derived:
                rep["edges"].append({"from": round(c0, 4), "to": round(v, 4),
                                     "by": derived, "new_link": True})
                uf.union(root, i)

        # 未到達ノードの最終仕分け。「面取りの反対側が到達済み」なら面取り由来 = 判定対象外
        for i, v in enumerate(an.values):
            if root is not None and uf.find(i) == root:
                continue
            ch = _chamfer_origin(v, a, obliques, an, uf, root)
            if ch:
                rep["out_of_scope_nodes"].append({"value": round(v, 4), "reason": ch})
                out_of_scope.append({"class": "chamfer_node", "axis": a,
                                     "value": round(v, 4), "reason": ch})
                continue
            rep["uncovered"].append({"value": round(v, 4), "sources": an.sources[i]})
            unspecified.append({"feature": "position", "axis": a, "value": round(v, 4),
                                "sources": an.sources[i],
                                "reason": u"%s軸の位置 %.4g を決める寸法が無い"
                                          u"(他寸法の和・差でも到達できない)" % (a, v)})
        axis_report[a] = rep

    for r in dims:
        if r["role"] == "position_pair" and r["id"] not in used_dim_ids \
                and not any(f["id"] == r["id"] for f in floating):
            floating.append({"id": r["id"], "axis": r["axis"],
                             "reason": u"どのビュー・軸の特徴にも結び付かない"})

    # 値が重複する寸法(情報)
    dup = []
    seen = {}
    for r in dims:
        v = r["value"]
        if v is None:
            continue
        seen.setdefault(round(v, 4), []).append(r["id"])
    for v, ids in sorted(seen.items()):
        if len(ids) > 1:
            dup.append({"value": v, "ids": ids})

    out_of_scope.append({"class": "not_implemented",
                         "reason": u"表面性状記号・幾何公差・溶接記号・ねじのピッチ/等級は"
                                   u"v1の判定対象外(生成側も未実装)"})
    if dup:
        out_of_scope.append(
            {"class": "same_value_features",
             "reason": u"v1の台帳は特徴を**値と座標**で持つため、同じ値の別フィーチャー"
                       u"(左右のφ25等)を区別しない。片方の寸法を消しても検出できない"
                       u"(該当: %s)" % [d["value"] for d in dup]})

    ok = not unspecified
    report = {
        "gate": "gate2_completeness_v1",
        "dxf": dxf_path,
        "plan": plan_path,
        "dropped_dimensions": list(drop_dim_ids),
        "ok": ok,
        "unspecified": unspecified,
        "floating_dimensions": floating,
        "redundant_dimensions": redundant,
        "duplicate_value_dimensions": dup,
        "circles": circle_report,
        "axes": axis_report,
        "dimensions_read": [{"id": r["id"], "role": r["role"], "axis": r["axis"],
                             "value": r["value"], "view": r["view"]} for r in dims],
        "hole_notes": notes,
        "note_diameters": note_dias,
        "note_taps": note_taps,
        "note_depths": note_depths,
        "covered_diameters": covered_dias,
        "chamfers": obliques,
        "out_of_scope": out_of_scope,
    }
    return report


def _covered_widths(dims):
    u"""図面上で指定済みの『幅』(位置対の距離)を集める。二面取り幅19等。"""
    out = []
    for r in dims:
        if r["role"] == "position_pair" and r["value"]:
            out.append(round(abs(r["value"]), 6))
    return sorted(set(out))


def _chamfer_origin(v, axis, obliques, an, uf, root):
    u"""位置 v が「到達済みの位置から面取り/テーパ1本ぶん離れた点」なら、その説明文を返す。

    面取りは v1 の判定対象外(裁定)なので、面取りでしか到達できない位置は
    **未指定寸法にはせず「判定対象外」として明示列挙する**。
    面取りが接するだけの重要な位置(端面・外径)は他経路で到達済みなのでここには来ない。
    """
    for o in obliques:
        if axis not in o.get("pairs", {}):
            continue
        p, q = o["pairs"][axis]
        for near, far in ((p, q), (q, p)):
            if abs(near - v) > NODE_TOL:
                continue
            j = an.index(far)
            if j is None or root is None or uf.find(j) != root:
                continue
            return (u"面取り/テーパ(%s・%s)の端点。到達済みの %g から %g ずれた位置で、"
                    u"面取り寸法はv1の判定対象外"
                    % (o["view"], o["kind"], round(far, 4) + 0.0, round(abs(near - far), 4)))
    return None


def _chord_derivation(half_dist, diameters, widths):
    u"""円筒(直径D)を平面(二面幅W)で切った交線の見え掛かり位置 √((D/2)²-(W/2)²) と一致するか。"""
    if half_dist is None:
        return None
    for d in diameters:
        for w in widths:
            if w >= d:
                continue
            h = math.sqrt(max(0.0, (d / 2.0) ** 2 - (w / 2.0) ** 2))
            if abs(h - half_dist) <= VALUE_TOL and h > 1e-6:
                return u"幾何導出: 円筒φ%g×二面幅%g の交線 √((%g/2)²-(%g/2)²)=%.4f" % (
                    d, w, d, w, h)
    return None


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------
def print_report(rep):
    print(u"===== ゲート② 寸法完全性 v1: %s =====" % os.path.basename(rep["dxf"]))
    if rep["dropped_dimensions"]:
        print(u"  [反証テスト] 除外した寸法: %s" % rep["dropped_dimensions"])
    print(u"判定: %s" % (u"合格(この図面だけで形状が一意に決まる)" if rep["ok"]
                        else u"不合格(未指定寸法 %d件)" % len(rep["unspecified"])))

    print(u"\n-- 円(直径)特徴 %d種 --" % len(rep["circles"]))
    for c in rep["circles"]:
        print(u"   φ%-8g x%-2d %-18s -> %s%s"
              % (c["diameter"], c["count"], ",".join(c["views"]),
                 u"OK(%s)" % c["covered_by"] if c["ok"] else u"** 未指定 **",
                 (u" %s" % c["note"]) if c.get("note") else ""))

    for a in AXES:
        r = rep["axes"].get(a)
        if not r or not r["node_count"]:
            continue
        print(u"\n-- %s軸の位置ノード %d個 (mode=%s%s) --"
              % (a, r["node_count"], r["mode"],
                 u", 対称中心=%g" % r["symmetry_center"] if r.get("symmetry_center") is not None
                 else ""))
        print(u"   ノード: %s" % r["nodes"])
        for e in r["edges"]:
            print(u"     %8.3f <-> %8.3f  by %s%s"
                  % (e["from"], e["to"], e["by"], "" if e["new_link"] else u"  (冗長)"))
        for u_ in r["out_of_scope_nodes"]:
            print(u"     [判定対象外] %g : %s" % (u_["value"], u_["reason"]))
        for u_ in r["uncovered"]:
            print(u"     ** 未指定 ** %g (由来 %s)" % (u_["value"], u_["sources"]))

    if rep["unspecified"]:
        print(u"\n-- ゲート②不合格理由(未指定寸法) --")
        for u_ in rep["unspecified"]:
            print(u"   %s" % u_["reason"])
    if rep["redundant_dimensions"]:
        print(u"\n-- 過剰(冗長)寸法の警告 --")
        for r in rep["redundant_dimensions"]:
            print(u"   %s軸 %s: %s" % (r["axis"], r["by"], r["reason"]))
    if rep["floating_dimensions"]:
        print(u"\n-- 宙に浮いた寸法の警告 --")
        for r in rep["floating_dimensions"]:
            print(u"   %s: %s" % (r["id"], r["reason"]))
    if rep["duplicate_value_dimensions"]:
        print(u"\n-- 同値寸法(情報) --")
        for r in rep["duplicate_value_dimensions"]:
            print(u"   %g: %s" % (r["value"], r["ids"]))

    print(u"\n-- 穴注記の解釈 --")
    for n in rep["hole_notes"]:
        print(u"   %r -> φ%s / タップM%s / 深さ%s / PCD%s"
              % (n["raw"], n["diameters"], n["taps"], n["depths"], n["pcds"]))

    print(u"\n-- 判定対象外(v1で判定できない特徴。黙って無視していないことの明示) --")
    seen = set()
    for o in rep["out_of_scope"]:
        key = (o["class"], o.get("view"), o.get("value"), o.get("diameter"), o.get("type"))
        if key in seen:
            continue
        seen.add(key)
        detail = ", ".join("%s=%s" % (k, o[k]) for k in
                           ("view", "type", "axis", "value", "diameter", "id") if k in o)
        print(u"   [%s] %s%s" % (o["class"], detail + " : " if detail else "", o["reason"]))
    if rep["chamfers"]:
        print(u"   面取り/テーパ実測: %s"
              % [(c["view"], c["kind"], c["leg"]) for c in rep["chamfers"]])


def _main(argv):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if len(argv) < 3:
        print(__doc__)
        return 2
    plan_path, dxf_path = argv[1], argv[2]
    drops = []
    if "--drop" in argv:
        i = argv.index("--drop")
        drops = argv[i + 1].split(",")
    rep = check_completeness(dxf_path, plan_path, drop_dim_ids=drops)
    print_report(rep)
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        with io.open(out, "w", encoding="utf-8") as f:
            f.write(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
        print(u"\nsaved %s" % out)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
