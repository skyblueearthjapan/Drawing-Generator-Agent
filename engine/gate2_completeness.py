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

# JIS B 0203 管用テーパねじ / JIS B 0202 管用平行ねじ の基準山形(基準径の位置での値)。
#   呼び -> (ピッチ p, おねじ外径 d, おねじ有効径 d2, おねじ谷径 d1)  単位mm
# ❗テーパねじ(R/Rc/PT)は 1/16 のテーパを持つので、図面に現れる輪郭径は軸方向位置に
#   よって連続的に変わる。したがって**単一の値ではなく範囲**で照合する
#   (`pipe_thread_envelope`)。範囲の外側の径はこの注記では説明できない = 不合格に落ちる。
PIPE_THREAD = {
    "1/16": (0.9071, 7.723, 7.142, 6.561),
    "1/8": (0.9071, 9.728, 9.147, 8.566),
    "1/4": (1.3368, 13.157, 12.301, 11.445),
    "3/8": (1.3368, 16.662, 15.806, 14.950),
    "1/2": (1.8143, 20.955, 19.793, 18.631),
    "3/4": (1.8143, 26.441, 25.279, 24.117),
    "1": (2.3091, 33.249, 31.770, 30.291),
    "1-1/4": (2.3091, 41.910, 40.431, 38.952),
    "1-1/2": (2.3091, 47.803, 46.324, 44.845),
    "2": (2.3091, 59.614, 58.135, 56.656),
}


def pipe_thread_envelope(size):
    u"""管用ねじの呼びから、図面の輪郭径が取り得る範囲 [下限, 上限] を返す。

    テーパねじの輪郭(谷径・山径・口元の面取り)は基準径の位置から前後にずれるが、
    **ねじ山1ピッチぶんの余裕**を見れば収まる(1/16テーパでは有効ねじ長の前後でも
    径差はピッチ程度)。呼びが実径と桁違い(Rc1/4 と書いて実体がφ9等)なら
    この範囲から外れて採用されない = 反証が効く。
    """
    p, d, _d2, d1 = PIPE_THREAD[size]
    return (round(d1 - p, 4), round(d + p, 4))


def pipe_thread_designation_ok(size, radii):
    u"""呼びが実測の同心輪郭半径群と**呼び検算不等式**で整合するか。

    ❗範囲照合(`pipe_thread_envelope`)だけでは**隣の呼びの範囲と重なって通ってしまう**
    (Rc1/16 と Rc1/8、Rc1/8 と Rc1/4 は範囲が重なる)。そこで同心群の径そのものに
    不等式を課して呼びを1つに絞る:

      小径 d1 ≦ 2·r_max   かつ   2·r_second ≦ 大径 d

    意味: (a)群の最大径は少なくとも「おねじ谷径 d1」に達している(=山径側まで見えている)
          (b)2番目に大きい径(下穴/谷径)は「おねじ外径 d」を超えない。
    盲検 25154-3-04 の実測半径群 3.4062/3.95/4.3612/4.4674/5.0669 では
    **Rc1/8 だけが通り、Rc1/16・Rc1/4 は落ちる**(反証テストで確認)。
    """
    p, d, _d2, d1 = PIPE_THREAD[size]
    rs = sorted(set(round(float(r), 4) for r in radii), reverse=True)
    if len(rs) < 2:
        return False
    return (d1 <= 2.0 * rs[0] + VALUE_TOL) and (2.0 * rs[1] <= d + VALUE_TOL)


# 管用ねじの記号(JIS B 0203 R/Rc、旧JIS PT/PS、JIS B 0202 G/PF)。
# Rp(テーパおねじにはまる平行めねじ)も同じ基準径を使う。
PIPE_THREAD_SYMBOLS = ("Rc", "Rp", "PT", "PS", "PF", "G", "R")

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
# 管用ねじ(Rc1/8・PT1/4・G1/2 等)。呼びは PIPE_THREAD の表にある値だけ採用する
# (`R10` のような半径表記を誤って拾わないための実質的なフィルタ)
_NOTE_RE_PIPE = re.compile(r"(Rc|Rp|PT|PS|PF|G|R)\s*(\d(?:-\d/\d{1,2})?(?:/\d{1,2})?)")
# ❗記号 `R` `G` に整数が続くだけの表記(`Ｒ２`=隅アール)は半径指示と区別できない。
#   分数呼び(1/8・1-1/4 等)を要求して誤検出を防ぐ。Rc/Rp/PT/PS/PF は曖昧さが無いので整数も可
_PIPE_NEEDS_FRACTION = ("R", "G")
# 個数は注記の先頭 `12-` `4-` に出る(『１２－%%c５通し ＰＣＤ１２９』→ 12個)
_NOTE_RE_COUNT = re.compile(r"^\s*(\d+)\s*[-−]")
# 円周等分配置の明示語(自社流儀。無くてもPCD+実ジオメトリの等配検算で代替する)
_NOTE_EQ_WORDS = (u"円周等分", u"等配", u"等分配置")


def parse_hole_note(raw):
    u"""穴注記MTEXTを解釈して、カバーする直径・個数・深さ・PCDを取り出す。全角は半角へ正規化する。

    ❗ねじ注記(`Ｍ６`)は **下穴径(JIS B 0205)と呼び径の両方**をカバー直径として返す。
    荏原の設計者は3Dに下穴でなく**呼び径ちょうどの穴**を立てる(盲検10部品で6/6一致・
    調査/blind_test_report.md §6.1)ため、下穴径だけではモデル側のφ6穴を取りこぼす。
    """
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
        dias.append(nominal)             # 呼び径ちょうどの穴(自社の3Dモデルの流儀)
        drill = TAP_DRILL.get(int(nominal))
        if drill:
            dias.append(drill)           # 下穴径(JIS B 0205)
    for m in _NOTE_RE_DEPTH.finditer(s):
        depths.append(float(m.group(1)))
    for m in _NOTE_RE_PCD.finditer(s):
        pcds.append(float(m.group(1)))
    pipes = []
    for m in _NOTE_RE_PIPE.finditer(s):
        size = m.group(2)
        if size not in PIPE_THREAD:
            continue
        if m.group(1) in _PIPE_NEEDS_FRACTION and "/" not in size:
            continue
        p, d, d2, d1 = PIPE_THREAD[size]
        lo, hi = pipe_thread_envelope(size)
        pipes.append({"designation": "%s%s" % (m.group(1), size), "symbol": m.group(1),
                      "size": size, "pitch": p, "major": d, "pitch_dia": d2, "minor": d1,
                      "env": [lo, hi]})
    mc = _NOTE_RE_COUNT.match(s)
    return {"raw": raw, "normalized": s, "diameters": sorted(set(dias)),
            "taps": sorted(set(taps)), "depths": sorted(set(depths)),
            "pcds": sorted(set(pcds)),
            "pipe_threads": pipes,
            "count": int(mc.group(1)) if mc else None,
            "equal_spacing_declared": any(w in raw or w in s for w in _NOTE_EQ_WORDS)}


# ---------------------------------------------------------------------------
# PCD穴群(円周等分)の同定と検算
# ---------------------------------------------------------------------------
# PCD半径・等配角の許容差。注記のPCDは丸め値・実ジオメトリは厳密値なので、
# 「注記が実ジオメトリと違う」を確実に落とすため VALUE_TOL(0.01mm)を使う。
PCD_ANGLE_TOL_DEG = 0.05
# PCD注記を「位置カバレッジの根拠」として採用する最小の穴数。
# ❗2個(=180度対向)は数学的にはPCDで決まるが、**採用すると検出力が落ちる**:
#   TEST-002(2-8キリ ザグリ%%c11深さ7 / PCD60)で `P60_hole_pitch` を消しても
#   合格のままになり、反証テストの検出が 4/7 -> 3/7 に低下した(2026-08-10 実測)。
#   blind_test_report §6.1 も「同径の穴が3個以上等配」を確度の条件に挙げているので、
#   **2個は従来どおり寸法で指定させる**(安全側)。
PCD_MIN_COUNT = 3


def find_pcd_groups(circles, notes):
    u"""穴注記の構造化情報(個数・径・PCD)を **実ジオメトリで検算した上で** 穴群として同定する。

    判定モデル(b)の拡張。円周等分に並ぶ穴は中心が `R·cos15°` のような非丸め座標を生むため、
    v1の「軸方向の寸法チェーン」では 1本のPCD寸法で1組(2個)しか固定できなかった
    (12穴でX/Y合わせて6本の無意味な弦寸法が要る)。人間はPCD1本で済ませているので、
    **注記のPCD値が実ジオメトリと一致することを検算できた場合に限り**、その円周上の穴中心を
    「PCD注記で位置が決まっている」とみなす。

    ❗検算(=反証テストが効く条件)は3つ全部を満たすこと:
      1. 同径の穴が PCD_MIN_COUNT 個以上あり、注記の個数と一致する(個数が書いてあれば)
      2. 全ての穴中心が共通中心から等距離で、その直径が **注記のPCD値と 0.01mm 以内で一致**
      3. 円周方向に等分(隣り合う角度差が 360/n と 0.05度以内で一致)
    注記のPCDが実ジオメトリと違えば 2 で落ち、穴が等配でなければ 3 で落ちる。

    Returns: list of dict(ok/reason/view/diameter/pcd/count/center/axes/hole_centers)
    """
    groups = []
    for n in notes:
        if not n["pcds"]:
            continue
        for pcd in n["pcds"]:
            for dia in n["diameters"]:
                byview = {}
                for c in circles:
                    if abs(c["diameter"] - dia) <= VALUE_TOL:
                        byview.setdefault(c["view"], []).append(c)
                for view in sorted(byview):
                    group = byview[view]
                    ax, ay = group[0]["axes"]
                    g = {"note": n["raw"], "view": view, "axes": [ax, ay],
                         "diameter": dia, "pcd": pcd, "count_in_note": n["count"],
                         "count_found": len(group), "ok": False, "reason": None}
                    if len(group) < PCD_MIN_COUNT:
                        g["reason"] = (u"φ%g の穴が %d 個(採用の最小 %d 個未満)。"
                                       u"2個以下は寸法で指定させる(安全側)"
                                       % (dia, len(group), PCD_MIN_COUNT))
                        groups.append(g)
                        continue
                    if n["count"] is not None and n["count"] != len(group):
                        g["reason"] = u"注記の個数 %d と実ジオメトリのφ%g穴 %d 個が一致しない" % (
                            n["count"], dia, len(group))
                        groups.append(g)
                        continue
                    cx = sum(c["center"][ax] for c in group) / len(group)
                    cy = sum(c["center"][ay] for c in group) / len(group)
                    rs = [math.hypot(c["center"][ax] - cx, c["center"][ay] - cy) for c in group]
                    g["center"] = {ax: round(cx, 6) + 0.0, ay: round(cy, 6) + 0.0}
                    g["pcd_measured"] = round(2.0 * (sum(rs) / len(rs)), 4)
                    if max(abs(2.0 * r - pcd) for r in rs) > VALUE_TOL:
                        g["reason"] = (u"注記のＰＣＤ%g と実ジオメトリの穴中心円 φ%.4f が"
                                       u"一致しない(許容%.2fmm)"
                                       % (pcd, g["pcd_measured"], VALUE_TOL))
                        groups.append(g)
                        continue
                    angs = sorted(math.degrees(math.atan2(c["center"][ay] - cy,
                                                          c["center"][ax] - cx)) % 360.0
                                  for c in group)
                    step = 360.0 / len(angs)
                    diffs = [(angs[(i + 1) % len(angs)] - angs[i]) % 360.0
                             for i in range(len(angs))]
                    if max(abs(d - step) for d in diffs) > PCD_ANGLE_TOL_DEG:
                        g["reason"] = (u"φ%g の穴 %d 個がＰＣＤ%g 上で円周等分になっていない"
                                       u"(隣接角 %s)"
                                       % (dia, len(group), pcd, [round(d, 3) for d in diffs]))
                        groups.append(g)
                        continue
                    g["ok"] = True
                    g["angles_deg"] = [round(a, 4) for a in angs]
                    g["hole_centers"] = [c["center"] for c in group]
                    g["reason"] = (u"ＰＣＤ%g・φ%g×%d個・円周等分を実ジオメトリで検算(実測φ%.4f)"
                                   % (pcd, dia, len(group), g["pcd_measured"]))
                    groups.append(g)
    return groups


# ---------------------------------------------------------------------------
# 正多角形(六角座・角形)の同定
# ---------------------------------------------------------------------------
POLY_MIN_R = 0.5      # これより小さい多角形は拾わない(mm)
POLY_MAX_N = 12
POLY_ANGLE_TOL_DEG = 0.2


def find_regular_polygons(segments):
    u"""ビュー内の線分から**閉じた正多角形**(六角座・角形)を1つの特徴として取り出す。

    v1には多角形の概念が無く、六角座(二面幅24のM16座)の6本の斜線の端点が全て
    「未指定の位置ノード」として並んでしまっていた(盲検 25154-1-04)。
    多角形は **二面幅(または対角)1本**で決まるので、1特徴として扱う。

    segments: [((u1,v1),(u2,v2)), ...](そのビューの2軸のモデル座標)
    Returns: [{"n","center":(cu,cv),"circum_r","across_flats","vertices":[(u,v)...]}]
    """
    def q(p):
        return (round(p[0], 3) + 0.0, round(p[1], 3) + 0.0)

    adj = {}
    for a, b in segments:
        ka, kb = q(a), q(b)
        if ka == kb:
            continue
        adj.setdefault(ka, set()).add(kb)
        adj.setdefault(kb, set()).add(ka)

    out, seen = [], set()
    for start in sorted(adj):
        if start in seen or len(adj[start]) != 2:
            continue
        cycle, prev, cur, closed = [start], None, start, False
        while True:
            if len(adj[cur]) != 2:
                break
            nbrs = [x for x in adj[cur] if x != prev]
            if not nbrs:
                break
            nxt = nbrs[0]
            if nxt == start:
                closed = True
                break
            if nxt in cycle or len(cycle) > POLY_MAX_N:
                break
            cycle.append(nxt)
            prev, cur = cur, nxt
        if not closed or not (3 <= len(cycle) <= POLY_MAX_N):
            continue
        for v in cycle:
            seen.add(v)
        n = len(cycle)
        cu = sum(p[0] for p in cycle) / n
        cv = sum(p[1] for p in cycle) / n
        rs = [math.hypot(p[0] - cu, p[1] - cv) for p in cycle]
        r = sum(rs) / n
        if r < POLY_MIN_R or max(abs(x - r) for x in rs) > NODE_TOL:
            continue
        angs = sorted(math.degrees(math.atan2(p[1] - cv, p[0] - cu)) % 360.0 for p in cycle)
        step = 360.0 / n
        diffs = [(angs[(i + 1) % n] - angs[i]) % 360.0 for i in range(n)]
        if max(abs(d - step) for d in diffs) > POLY_ANGLE_TOL_DEG:
            continue
        out.append({"n": n, "center": (round(cu, 6) + 0.0, round(cv, 6) + 0.0),
                    "circum_r": round(r, 6),
                    "across_flats": round(2.0 * r * math.cos(math.pi / n), 6),
                    "across_corners": round(2.0 * r, 6),
                    "vertices": [(round(p[0], 6) + 0.0, round(p[1], 6) + 0.0) for p in cycle]})
    return out


def polygon_covered(poly, widths, diameters, oblique_dims=()):
    u"""正多角形が「二面幅」か「対角」の寸法で決まっているか。決まっていれば説明文を返す。

    多角形は1本の寸法(二面幅 or 対角)で全頂点が決まる。逆に**どちらの寸法も無ければ
    頂点位置は決まらない**ので未指定(=ゲート②不合格)にする。

    ❗**紙面内で任意角に回転した正多角形**(盲検 25154-1-04 の六角座)は、軸平行の
    幅寸法では二面幅にも対角にも一致しない(頂点間の軸平行距離が最大24.756/27.666で
    二面幅24・対角27.712 のどちらとも 0.01mm 以内で一致しない)。そこで
    **斜め線形寸法(`measure.direction` に角度を書いた寸法)** を受け付ける。
    自己申告は信じず、実ジオメトリで検算する:
      - 対角  : 寸法の測定点2つが **その多角形の相対する頂点2つ**と一致すること
      - 二面幅: 寸法の測定点2つが **相対する2辺の中点**であること
    """
    w, dd = poly["across_flats"], poly["across_corners"]
    for x in list(widths) + list(diameters):
        if abs(x - w) <= VALUE_TOL:
            return u"二面幅%.4g" % w
        if abs(x - dd) <= VALUE_TOL:
            return u"対角%.4g" % dd
    for od in oblique_dims:
        hit = _oblique_matches_polygon(poly, od)
        if hit:
            return hit
    return None


def _oblique_matches_polygon(poly, od):
    u"""斜め寸法 od がこの正多角形の対角/二面幅を**実ジオメトリとして**決めているか。

    ❗値が合っているだけでは採用しない(たまたま同じ長さの別寸法を「多角形を決めた」と
    誤認すると、多角形が寸法無しで合格してしまう)。**測定点の実体まで**一致を要求する。
    """
    if list(od.get("axes") or []) != list(poly["axes"]) or od.get("value") is None:
        return None
    w, dd = poly["across_flats"], poly["across_corners"]
    if abs(od["value"] - dd) <= VALUE_TOL and _oblique_hits_opposite_vertices(poly, od):
        return u"対角%.4g(斜め寸法%s・測定点が相対する頂点と一致)" % (dd, od["id"])
    if abs(od["value"] - w) <= VALUE_TOL and _oblique_hits_opposite_flats(poly, od):
        return u"二面幅%.4g(斜め寸法%s・測定点が相対する2辺の中点と一致)" % (w, od["id"])
    return None


def _oblique_endpoints_centered(poly, od):
    u"""測定点が2つあり、その中点が多角形の中心に一致するか(=相対する対を測っている)。"""
    pts = od.get("points") or []
    if len(pts) != 2:
        return False
    cu, cv = poly["center"]
    return (abs((pts[0][0] + pts[1][0]) / 2.0 - cu) <= NODE_TOL
            and abs((pts[0][1] + pts[1][1]) / 2.0 - cv) <= NODE_TOL)


def _oblique_hits_opposite_vertices(poly, od):
    u"""斜め寸法の測定点2つが、この多角形の**相対する頂点**の対と一致するか。

    「方向が対角と平行」「長さが対角と一致」だけでは不十分(中心を通る同じ長さの
    任意の線分が対角を名乗れてしまう)。**実在の頂点座標との一致**まで要求する。
    """
    if not _oblique_endpoints_centered(poly, od):
        return False
    for p in od["points"]:
        if not any(abs(p[0] - q[0]) <= NODE_TOL and abs(p[1] - q[1]) <= NODE_TOL
                   for q in poly["vertices"]):
            return False
    return True


def _oblique_hits_opposite_flats(poly, od):
    u"""斜め寸法の測定点2つが、この多角形の**相対する2辺の中点**と一致するか(二面幅)。

    ❗辺の中点は SW 投影の特徴点ではないので実際には snap できない(だから 1-04 は
    対角で寸法を入れる)。それでも人手計画が二面幅で書いてきた場合に備えて、
    「向きが対辺の法線」ではなく**測定点が実在の辺中点**であることまで要求する。
    向きだけの判定にすると、中心を通る同じ長さの任意の線分が二面幅を名乗れてしまう。
    """
    if poly["n"] % 2:
        return False              # 奇数角形に「対辺」は無い
    if not _oblique_endpoints_centered(poly, od):
        return False
    vs = poly["vertices"]
    mids = [((vs[i][0] + vs[(i + 1) % len(vs)][0]) / 2.0,
             (vs[i][1] + vs[(i + 1) % len(vs)][1]) / 2.0) for i in range(len(vs))]
    for p in od["points"]:
        if not any(abs(p[0] - m[0]) <= NODE_TOL and abs(p[1] - m[1]) <= NODE_TOL
                   for m in mids):
            return False
    return True


# ---------------------------------------------------------------------------
# 管用(テーパ)ねじ穴の同心群を1特徴として同定する(E3)
# ---------------------------------------------------------------------------
# 「同心群」と認めるのに必要な径の種類数。1種類だけ(=単なる対の位置)ではねじ穴の
# 輪郭群と区別できないので採用しない(安全側)。
THREAD_MIN_DIAMETERS = 2


def _profile_clusters(nodes, env):
    u"""ある径範囲に入る**同心の位置ノード対**を軸ごとに集める(輪郭ビューのねじ穴)。

    ねじ穴が円として見えないビュー(側面・断面)では、下穴・谷径・山径・口元の面取りが
    HIDDEN線分の対として現れる。中心(=ねじ軸)は線分の端点でしかなく位置ノードにならない。
    そこで「同じ中心に対して径違いの対が2種類以上ある」ことを同心群の条件にする。
    """
    lo, hi = env
    out = {}
    for a in AXES:
        vals = sorted(nodes[a].values)
        groups = {}
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                d = vals[j] - vals[i]
                if d < lo - VALUE_TOL:
                    continue
                if d > hi + VALUE_TOL:
                    break                      # 昇順なのでこれ以上は必ず範囲外
                c = round((vals[i] + vals[j]) / 2.0, 4) + 0.0
                groups.setdefault(c, []).append((vals[i], vals[j], round(d, 4)))
        for c, prs in sorted(groups.items()):
            dias = sorted({p[2] for p in prs})
            if len(dias) >= THREAD_MIN_DIAMETERS:
                out.setdefault(a, []).append(
                    {"center": c, "diameters": dias,
                     "nodes": sorted({v for p in prs for v in p[:2]})})
    return out


def find_thread_features(circles, nodes, pipe_threads):
    u"""管用ねじ注記(Rc/PT/G…)の呼びで説明できる**同心群**を1つのフィーチャーとして返す。

    ねじ穴の内部径(下穴・谷径・山径)は**ねじの呼びが決めるもので、図面に寸法を入れない**。
    したがって「呼びが書いてあり、その基準山形の範囲に収まる同心群が実在する」なら、
    その群は1特徴としてカバー済みとする(位置は別途チェーンで到達している必要がある)。

    ❗自己申告は信じない。採用条件は2段:
      (1) 呼びから引いた径範囲(`pipe_thread_envelope`)に実ジオメトリが収まること
      (2) **呼び検算不等式**(`pipe_thread_designation_ok`)を満たすこと。
          (1)だけだと隣の呼び(Rc1/16・Rc1/4)も範囲が重なって通ってしまう

    Returns: [{"kind","designation","env","axes","center","diameters","nodes","view"}]
    """
    feats = []
    by_center = {}
    for c in circles:
        ax, ay = c["axes"]
        key = (c["view"], ax, ay, round(c["center"][ax], 4), round(c["center"][ay], 4))
        by_center.setdefault(key, []).append(c)

    for t in pipe_threads:
        lo, hi = t["env"]
        # (a) 同心円群(ねじ穴が円として見えるビュー)
        for key, group in sorted(by_center.items()):
            view, ax, ay, cx, cy = key
            dias = sorted({g["diameter"] for g in group
                           if lo - VALUE_TOL <= g["diameter"] <= hi + VALUE_TOL})
            if len(dias) < THREAD_MIN_DIAMETERS:
                continue
            if not pipe_thread_designation_ok(t["size"], [d / 2.0 for d in dias]):
                continue
            feats.append({"kind": "concentric_circles", "designation": t["designation"],
                          "env": [lo, hi], "view": view, "axes": [ax, ay],
                          "center": {ax: cx, ay: cy}, "diameters": dias,
                          "nodes": {ax: [], ay: []}})
        # (b) 輪郭の同心群(円として見えないビュー)。**2軸で同じ径集合を持つ**ことを
        #     要求して初めて「1本の円筒」と認める(片方の軸だけの偶然一致を排除)
        clusters = _profile_clusters(nodes, (lo, hi))
        axes_present = [a for a in AXES if a in clusters]
        for i, a1 in enumerate(axes_present):
            for a2 in axes_present[i + 1:]:
                for c1 in clusters[a1]:
                    for c2 in clusters[a2]:
                        common = sorted(d for d in c1["diameters"]
                                        if any(abs(d - e) <= VALUE_TOL
                                               for e in c2["diameters"]))
                        if len(common) < THREAD_MIN_DIAMETERS:
                            continue
                        # 呼び検算不等式(範囲照合だけでは隣の呼びも通ってしまうため)
                        if not pipe_thread_designation_ok(t["size"],
                                                          [d / 2.0 for d in common]):
                            continue
                        n1 = [v for v in c1["nodes"]
                              if any(abs(abs(v - c1["center"]) - d / 2.0) <= NODE_TOL
                                     for d in common)]
                        n2 = [v for v in c2["nodes"]
                              if any(abs(abs(v - c2["center"]) - d / 2.0) <= NODE_TOL
                                     for d in common)]
                        feats.append({"kind": "profile_cluster",
                                      "designation": t["designation"], "env": [lo, hi],
                                      "view": None, "axes": [a1, a2],
                                      "center": {a1: c1["center"], a2: c2["center"]},
                                      "diameters": common,
                                      "nodes": {a1: n1, a2: n2}})
    return feats


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
    segments = {k: [] for k in amaps}   # 多角形検出用(ビューの2軸のモデル座標)
    for k, am in amaps.items():
        ax, ay = am["x"][0], am["y"][0]
        for e in per_view[k]:
            t = e.dxftype()
            if t == "LINE":
                a = to_model_coords(am, (e.dxf.start.x, e.dxf.start.y))
                b = to_model_coords(am, (e.dxf.end.x, e.dxf.end.y))
                segments[k].append(((a[ax], a[ay]), (b[ax], b[ay])))
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
                    segments[k].append(((a[ax], a[ay]), (b[ax], b[ay])))
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
                # ❗E4: 斜め方向の線形寸法は「位置チェーン」の辺には使えない(1本で2軸を
                #   同時に動かすため和差の到達判定に載らない)が、**紙面内で回転した
                #   正多角形の二面幅/対角**を決める寸法としては有効。測定点をビューの
                #   2軸のモデル座標で持ち、多角形カバレッジ側で実ジオメトリと突き合わせる
                axis = None
                ax_, ay_ = am["x"][0], am["y"][0]
                rec["role"] = "oblique_width"
                rec["axes"] = [ax_, ay_]
                rec["angle_deg"] = round(ang, 6)
                rec["points"] = [[p2[ax_], p2[ay_]], [p3[ax_], p3[ay_]]]
                out_of_scope.append({
                    "class": "oblique_dimension", "id": did, "angle": ang,
                    "reason": u"軸平行でない方向の線形寸法。位置チェーンの辺には使わない"
                              u"(正多角形の二面幅/対角の照合にのみ使う)"})
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
        if u"注記" in t:
            continue
        if not ("%%c" in t or u"キリ" in t or u"ザグリ" in t or u"深さ" in t
                or re.search(u"[ＭM][０-９0-9]", t)
                # 管用ねじだけの注記(『Ｒｃ１／８』)は上のどれにも当たらない
                or _NOTE_RE_PIPE.search(t.translate(_ZEN2HAN))):
            continue
        notes.append(parse_hole_note(t))

    note_dias = sorted({d for n in notes for d in n["diameters"]})
    note_depths = sorted({d for n in notes for d in n["depths"]})
    note_taps = sorted({d for n in notes for d in n["taps"]})
    pipe_threads = [t for n in notes for t in n.get("pipe_threads", [])]

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

    # --- 2b) 管用(テーパ)ねじ穴の同心群を1特徴化する(E3) --------------------
    # ねじの内部径は**呼びが決める**ので図面に寸法は入らない。呼びの基準山形の範囲に
    # 実ジオメトリが収まり、かつ呼び検算不等式を満たすことを確認した上で1特徴として扱う。
    thread_features = find_thread_features(circles, nodes, pipe_threads)
    thread_dias = sorted({d for f in thread_features for d in f["diameters"]})
    # 輪郭同心群の中心(=ねじ軸)は線分の端点でしかなく位置ノードにならないので**仮想ノード**
    # として登録する(これが寸法で到達できて初めて群がカバーされる)。
    for f in thread_features:
        if f["kind"] != "profile_cluster":
            continue
        for a_ in f["axes"]:
            if nodes[a_].index(f["center"][a_]) is None:
                nodes[a_].add(f["center"][a_], "virtual:thread_axis")

    # --- 3) 円(直径)のカバレッジ ---------------------------------------
    def dia_covered(d):
        for x in covered_dias:
            if abs(x - d) <= VALUE_TOL:
                return True
        return False

    def thread_dia_covered(d):
        return any(abs(x - d) <= VALUE_TOL for x in thread_dias)

    chamfer_legs = sorted({o["leg"] for o in obliques if o["kind"] == "chamfer45"})

    circle_report, unspecified = [], []
    for key, group in sorted(circle_groups.items()):
        d = key[0]
        row = {"diameter": d, "count": len(group),
               "views": sorted({g["view"] for g in group})}
        if dia_covered(d):
            row["covered_by"] = "dimension_or_note"
            row["ok"] = True
        elif thread_dia_covered(d):
            # 管用ねじの同心群。径は呼び(Rc1/8等)が決めるので寸法は要らない
            row["covered_by"] = "taper_thread_note"
            row["ok"] = True
            row["note"] = u"管用ねじ%s の同心群" % ",".join(
                sorted({f["designation"] for f in thread_features
                        if any(abs(x - d) <= VALUE_TOL for x in f["diameters"])}))
        else:
            derived = None
            # ❗面取りは径を減らす側(軸の外周C面取り)だけでなく**増やす側**にも出る
            #   (穴の口元C1で φ72 → φ74。盲検 25154-3-04 で実害)。両方向を見る
            for d0 in covered_dias:
                for leg in chamfer_legs:
                    if abs((d0 - 2.0 * leg) - d) <= VALUE_TOL:
                        derived = u"面取りC%g由来(φ%g-2×%g)" % (leg, d0, leg)
                        break
                    if abs((d0 + 2.0 * leg) - d) <= VALUE_TOL:
                        derived = u"面取りC%g由来(穴口元・φ%g+2×%g)" % (leg, d0, leg)
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

    # --- 3b) PCD穴群・正多角形の同定(位置カバレッジの根拠) -----------------
    # (b)の拡張。**注記の自己申告をそのまま信じるのではなく実ジオメトリで検算する**
    pcd_groups = find_pcd_groups(circles, notes)
    for g in pcd_groups:
        if not g["ok"]:
            out_of_scope.append({"class": "pcd_group_rejected", "view": g["view"],
                                 "diameter": g["diameter"],
                                 "reason": u"ＰＣＤ注記を位置の根拠に採用できない: %s"
                                           % g["reason"]})
    if any(g["ok"] for g in pcd_groups):
        out_of_scope.append(
            {"class": "pcd_phase",
             "reason": u"円周等分穴群の**位相(基準角)**はv1の判定対象外。"
                       u"PCD+等配+個数までを実ジオメトリで検算して位置カバレッジに採用している"})

    # 幅(位置対の距離)は多角形の二面幅照合にも使うので先に集める
    covered_widths = _covered_widths(dims)
    oblique_widths = _covered_oblique_widths(dims)
    polygons = []
    for k in sorted(segments):
        am = amaps[k]
        pax, pay = am["x"][0], am["y"][0]
        for pg in find_regular_polygons(segments[k]):
            pg = dict(pg, view=k, axes=[pax, pay])
            hit = polygon_covered(pg, covered_widths, covered_dias, oblique_widths)
            pg["ok"] = hit is not None
            pg["covered_by"] = hit
            polygons.append(pg)
    # 斜め寸法が実際にどの多角形を決めたか(決めていない斜め寸法は「宙に浮いた寸法」警告)
    used_oblique_ids = set()
    for pg in polygons:
        for od in oblique_widths:
            if _oblique_matches_polygon(pg, od):
                used_oblique_ids.add(od["id"])
    # 未指定の多角形は「1特徴」として1件で報告する(頂点ノードをバラバラに並べない)
    poly_uncovered_nodes = {a: set() for a in AXES}
    for pg in polygons:
        if pg["ok"]:
            continue
        pax, pay = pg["axes"]
        for i, ax_ in enumerate(pg["axes"]):
            for v in {p[i] for p in pg["vertices"]}:
                poly_uncovered_nodes[ax_].add(round(v, 6) + 0.0)
        unspecified.append({"feature": "polygon", "view": pg["view"], "n": pg["n"],
                            "across_flats": pg["across_flats"],
                            "center": list(pg["center"]),
                            "reason": u"%sビューの正%d角形(二面幅%.4g・対角%.4g)を決める寸法が無い"
                                      % (pg["view"], pg["n"], pg["across_flats"],
                                         pg["across_corners"])})

    # --- 4) 位置ノードのカバレッジ ----------------------------------------
    axis_report = {}
    redundant, floating = [], []
    used_dim_ids = set()
    axis_state = {}

    for a in AXES:
        an = nodes[a]
        n = len(an)
        rep = {"axis": a, "node_count": n,
               "nodes": [round(v, 4) for v in sorted(an.values)], "mode": None,
               "edges": [], "uncovered": [], "out_of_scope_nodes": []}
        if n == 0:
            axis_report[a] = rep
            axis_state[a] = None
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

        # (b拡張) PCD穴群: 検算に通った円周等分穴は、PCD中心から各穴中心の位置が決まる。
        #   人間はPCD1本で済ませる流儀なので、弦寸法を何本も要求しない
        #   (中心そのものが図面上で決まっていない場合は連結されないので合格にはならない)
        for g in pcd_groups:
            if not g["ok"] or a not in g["axes"]:
                continue
            ic = an.index(g["center"][a])
            if ic is None:
                continue
            for hc in g["hole_centers"]:
                ih = an.index(hc[a])
                if ih is not None:
                    add_edge(ic, ih,
                             u"ＰＣＤ%g注記(φ%g×%d・円周等分・%s)"
                             % (g["pcd"], g["diameter"], g["count_found"], g["view"]),
                             allow_cycle_report=False)

        # (b拡張) 正多角形: 二面幅(または対角)1本で全頂点の位置が決まる
        for pg in polygons:
            if not pg["ok"] or a not in pg["axes"]:
                continue
            ai = pg["axes"].index(a)
            ic = an.index(pg["center"][ai])
            if ic is None:
                continue
            for v in {p[ai] for p in pg["vertices"]}:
                iv = an.index(v)
                if iv is not None:
                    add_edge(ic, iv, u"正%d角形(%s・%s)" % (pg["n"], pg["covered_by"], pg["view"]),
                             allow_cycle_report=False)

        # (b拡張) 管用(テーパ)ねじ穴の同心群: 呼び1つで内部径が全部決まる(E3)。
        #   ねじ軸(中心)が寸法で到達していることが前提(していなければ群ごと未到達のまま)
        for f in thread_features:
            if a not in f["axes"]:
                continue
            ic = an.index(f["center"][a])
            if ic is None:
                continue
            vals = list(f["nodes"].get(a) or [])
            for d in f["diameters"]:
                for s in (+1, -1):
                    vals.append(f["center"][a] + s * d / 2.0)
            for v in vals:
                iv = an.index(v)
                if iv is not None:
                    add_edge(ic, iv,
                             u"管用ねじ%s注記(呼びが内部径を決める・%s)"
                             % (f["designation"], f["kind"]),
                             allow_cycle_report=False)

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
        axis_state[a] = {"an": an, "uf": uf, "root": root, "rep": rep,
                         "symmetric": symmetric, "c0": c0}

    # --- 4b) 幾何導出(**軸をまたぐ**ので全軸の到達判定が出そろってから行う) --------
    #   (e) 円筒×平面の交線(二面取り・左右対称)   … 従来
    #   (f) 円 × 到達済みの直線(片側の弦)          … E5(b)
    #   (g) 円 × 円 の交点                          … E5(a)
    geo_feats = _circle_features(circles, dia_covered, thread_features)
    _apply_geometric_derivations(axis_state, geo_feats, covered_dias, covered_widths)

    # --- 4c) 未到達ノードの最終仕分け ---------------------------------------
    for a in AXES:
        stt = axis_state.get(a)
        if stt is None:
            continue
        an, uf, root, rep = stt["an"], stt["uf"], stt["root"], stt["rep"]
        # 「面取りの反対側が到達済み」なら面取り由来 = 判定対象外
        for i, v in enumerate(an.values):
            if root is not None and uf.find(i) == root:
                continue
            ch = _chamfer_origin(v, a, obliques, an, uf, root)
            if ch:
                rep["out_of_scope_nodes"].append({"value": round(v, 4), "reason": ch})
                out_of_scope.append({"class": "chamfer_node", "axis": a,
                                     "value": round(v, 4), "reason": ch})
                continue
            if any(abs(v - pv) <= NODE_TOL for pv in poly_uncovered_nodes[a]):
                # 未指定の多角形の頂点。特徴1件として既に unspecified に計上済み
                rep["uncovered"].append({"value": round(v, 4), "sources": an.sources[i],
                                         "grouped_into": "polygon"})
                continue
            rep["uncovered"].append({"value": round(v, 4), "sources": an.sources[i]})
            unspecified.append({"feature": "position", "axis": a, "value": round(v, 4),
                                "sources": an.sources[i],
                                "reason": u"%s軸の位置 %.4g を決める寸法が無い"
                                          u"(他寸法の和・差でも到達できない)" % (a, v)})
        axis_report[a] = rep

    for r in dims:
        if r["role"] == "oblique_width" and r["id"] not in used_oblique_ids:
            floating.append({"id": r["id"], "axis": None,
                             "reason": u"斜め線形寸法だが、どの正多角形の二面幅/対角とも"
                                       u"一致しない(位置チェーンの辺にもならない)"})
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
        "pcd_groups": pcd_groups,
        "polygons": polygons,
        "oblique_width_dimensions": oblique_widths,
        "thread_features": thread_features,
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


def _covered_oblique_widths(dims):
    u"""斜め方向の線形寸法(`measure.direction` に角度を書いた寸法)を測定点つきで集める。

    ❗値だけを `_covered_widths` に混ぜてはいけない。幅の値は `_chord_derivation`
    (円筒×平面の交線)の入力にもなっており、そこへ「どこを測ったか分からない値」を
    足すと幾何導出が偽陽性を出す。斜め寸法は**測定点を実ジオメトリと突き合わせられる
    多角形の照合にだけ**使う。
    """
    out = []
    for r in dims:
        if r["role"] != "oblique_width" or not r.get("value"):
            continue
        out.append({"id": r["id"], "view": r.get("view"), "axes": r.get("axes"),
                    "value": round(abs(r["value"]), 6),
                    "angle_deg": r.get("angle_deg"),
                    "points": r.get("points")})
    return out


def _circle_features(circles, dia_covered, thread_features):
    u"""幾何導出に使える「位置と径が図面から確定している円」を集める。

    実在円(直径が寸法/注記でカバー済み)に加えて、**管用ねじの同心群**(輪郭ビューでしか
    見えない円筒。呼びが径を決める)も同じ形にして扱う。
    """
    out = []
    for c in circles:
        if not dia_covered(c["diameter"]):
            continue
        out.append({"axes": list(c["axes"]), "center": dict(c["center"]),
                    "diameter": c["diameter"],
                    "label": u"円φ%g(%s)" % (c["diameter"], c["view"])})
    for f in thread_features:
        if f["kind"] != "profile_cluster":
            continue
        for d in f["diameters"]:
            out.append({"axes": list(f["axes"]), "center": dict(f["center"]),
                        "diameter": d,
                        "label": u"管用ねじ%s の円筒φ%g" % (f["designation"], d)})
    return out


def _apply_geometric_derivations(axis_state, feats, covered_dias, covered_widths,
                                 max_rounds=6):
    u"""**幾何的に一意に決まる**位置を導出して到達済みへ繰り入れる(判定モデル(e)(f)(g))。

    (e) 円筒φD を幅W の平面2枚で切った交線 √((D/2)²-(W/2)²)(左右対称の二面取り。従来)
    (f) **片側だけの弦**: 位置と径が確定した円を、到達済みの位置 u で切った交点
        h=√((D/2)²-(u-c)²)(盲検 25154-6-02 の φ420 を X=161 で切った Y=±134.8295)
    (g) **円×円の交点**: 位置と径が確定した円2つの交点
        (同 6-02 の φ360インロー円 × φ12穴(中心(73,±164)) の Y=±161.9189/±166.7885)

    どれも「図面から確定した要素だけで**作図的に一意に決まる**点」であり、加工者が
    設計意図を推測する必要がない = ゲート②の趣旨(この図面だけで形状が決まる)に合う。
    逆に、円の中心位置か直径が図面で決まっていなければ導出は起きない(そこで落ちる)。

    ❗軸をまたぐ(円の中心は2軸ぶんの到達が要る)ため、全軸の到達判定が出そろってから
    **不動点になるまで**繰り返す。1軸ずつのループの中でやると、後から到達した軸を
    使う導出を取りこぼす。
    """
    def reached(a, v):
        st = axis_state.get(a)
        if st is None or st["root"] is None:
            return False
        i = st["an"].index(v)
        return i is not None and st["uf"].find(i) == st["root"]

    def derive(a, v, label, src):
        st = axis_state.get(a)
        if st is None or st["root"] is None:
            return False
        i = st["an"].index(v)
        if i is None or st["uf"].find(i) == st["root"]:
            return False
        st["uf"].union(st["root"], i)
        st["rep"]["edges"].append({"from": round(src, 4),
                                   "to": round(st["an"].values[i], 4),
                                   "by": label, "new_link": True})
        return True

    for _round in range(max_rounds):
        changed = False

        # (e) 従来の二面取り導出(対称軸まわり)
        for a in AXES:
            st = axis_state.get(a)
            if st is None or not st["symmetric"]:
                continue
            for v in list(st["an"].values):
                if reached(a, v):
                    continue
                d_ = _chord_derivation(abs(v - st["c0"]), covered_dias, covered_widths)
                if d_ and derive(a, v, d_, st["c0"]):
                    changed = True

        # (f) 円 × 到達済みの直線
        for f in feats:
            ax, ay = f["axes"]
            if axis_state.get(ax) is None or axis_state.get(ay) is None:
                continue
            if not reached(ax, f["center"][ax]) or not reached(ay, f["center"][ay]):
                continue
            r = f["diameter"] / 2.0
            for a1, a2 in ((ax, ay), (ay, ax)):
                for u in list(axis_state[a1]["an"].values):
                    if not reached(a1, u):
                        continue
                    dd = abs(u - f["center"][a1])
                    if dd >= r - 1e-9:
                        continue
                    h = math.sqrt(max(0.0, r * r - dd * dd))
                    if h <= 1e-6:
                        continue
                    lbl = (u"幾何導出: %s を %s=%.4g で切った交点 √((%g/2)²-%.4g²)=%.4f"
                           % (f["label"], a1, u, f["diameter"], dd, h))
                    for s in (+1, -1):
                        if derive(a2, f["center"][a2] + s * h, lbl, f["center"][a2]):
                            changed = True

        # (g) 円 × 円
        for i, f1 in enumerate(feats):
            for f2 in feats[i + 1:]:
                if f1["axes"] != f2["axes"]:
                    continue
                ax, ay = f1["axes"]
                if axis_state.get(ax) is None or axis_state.get(ay) is None:
                    continue
                if not (reached(ax, f1["center"][ax]) and reached(ay, f1["center"][ay])
                        and reached(ax, f2["center"][ax])
                        and reached(ay, f2["center"][ay])):
                    continue
                r1, r2 = f1["diameter"] / 2.0, f2["diameter"] / 2.0
                dx = f2["center"][ax] - f1["center"][ax]
                dy = f2["center"][ay] - f1["center"][ay]
                dist = math.hypot(dx, dy)
                if dist <= 1e-9 or dist > r1 + r2 - 1e-9 or dist < abs(r1 - r2) + 1e-9:
                    continue          # 離れている/内包している = 交点が無い(or 接する)
                aa = (dist * dist + r1 * r1 - r2 * r2) / (2.0 * dist)
                h2 = r1 * r1 - aa * aa
                if h2 <= 1e-12:
                    continue
                h = math.sqrt(h2)
                px = f1["center"][ax] + aa * dx / dist
                py = f1["center"][ay] + aa * dy / dist
                lbl = u"幾何導出: %s と %s の交点" % (f1["label"], f2["label"])
                for s in (+1, -1):
                    if derive(ax, px + s * h * (-dy / dist), lbl, f1["center"][ax]):
                        changed = True
                    if derive(ay, py + s * h * (dx / dist), lbl, f1["center"][ay]):
                        changed = True

        if not changed:
            break


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
        print(u"   %r -> 個数%s / φ%s / タップM%s / 深さ%s / PCD%s"
              % (n["raw"], n.get("count"), n["diameters"], n["taps"], n["depths"], n["pcds"]))

    if rep.get("pcd_groups"):
        print(u"\n-- PCD穴群の検算(注記を位置の根拠に採用してよいか) --")
        for g in rep["pcd_groups"]:
            print(u"   [%s] %s φ%g×%s PCD%g : %s"
                  % (u"採用" if g["ok"] else u"却下", g["view"], g["diameter"],
                     g["count_found"], g["pcd"], g["reason"]))
    if rep.get("polygons"):
        print(u"\n-- 正多角形の検出 --")
        for p in rep["polygons"]:
            print(u"   [%s] %s 正%d角形 二面幅%.4g 対角%.4g 中心%s : %s"
                  % (u"OK" if p["ok"] else u"** 未指定 **", p["view"], p["n"],
                     p["across_flats"], p["across_corners"], p["center"],
                     p["covered_by"] or u"二面幅/対角を指定する寸法が無い"))

    if rep.get("thread_features"):
        print(u"\n-- 管用(テーパ)ねじの同心群 --")
        for f in rep["thread_features"]:
            print(u"   %s %s 軸%s 中心%s 径%s 範囲%s"
                  % (f["designation"], f["kind"], f["axes"],
                     {k: round(v, 4) for k, v in f["center"].items()},
                     f["diameters"], f["env"]))

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
