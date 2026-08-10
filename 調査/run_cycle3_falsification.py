# -*- coding: utf-8 -*-
u"""改善サイクル3(E1 群配置穴群 / E2 傾斜フィーチャー)の**反証テスト**。

サイクル3で足した規則は、どちらも「今まで落ちていたものを通す」方向の変更なので、
**通してはいけないものが通らないこと**を機械的に試験する。
反証の効かない緩和は「検証ゲート」ではなく「合格印の自動押印機」でしかない。

  E1(ＰＣＤ+群構成+振分角 で円周上の群配置穴群の位置を決める)
    G1  素の判定: 25154-5-04 は合格し、φ9/φ14(ＰＣＤ142)・φ11/φ17.5(ＰＣＤ110)の
        4群が実ジオメトリ検算つきで採用される
    G2  **振分角の偽装**(１２－１８－１２ -> １２－１８－１３): 1度だけずらしても却下
    G3  **群数の偽装**(４個×３群 -> ４個×４群): 総数16 が実数12 と合わず却下
    G4  **群構成の偽装(核心)**(４個×３群 振分角１２－１８ -> ３個×４群 振分角１２－１８):
        **総数12・ＰＣＤ142 は一致したまま**配置だけ別物にする。
        個数と径だけ見る実装ならここを通してしまう
    G5  **ＰＣＤの偽装**(ＰＣＤ１４２ -> ＰＣＤ１４３)
    G6  **順序の偽装(核心)**(振分角１２－１８－１２ -> １８－１２－１２):
        角度の**集合**は同じで並びだけ違う。角度をソートして比べる実装なら通してしまう
    G7  **実ジオメトリの改竄**: φ9穴を1つだけ1度回すと(ＰＣＤ半径は不変)却下

  E2(角度寸法+基準点で傾斜穴の位置を決める)
    H1  素の判定: 25154-3-09 は合格し、傾斜穴2件が
        「φ6・傾斜30度・基準点(X=0,Z=0)」として採用される
    H2  **角度寸法を1本消す**と傾斜穴の採用は1件に減るが、左右対称でZノードを共有する
        3-09 では E5(円×到達済み直線)が残る穴の端点を導出するので**合格のまま**
        (=1本消しでは落ちない。反証は H3 の『2本まとめて消す』で取る)
    H3  **角度寸法を2本とも消す**と、修理パス時点と同じ未指定12件へ戻る
    H4  **角度寸法の偽装(核心)**: 頂点が実在端点でない / 傾斜辺が輪郭線と平行でない /
        基準辺が軸平行でない / 実測角が2辺の成す角と違う —— のどれか1つでも欠ければ採用しない
    H5  **実ジオメトリの改竄**: 傾斜穴の輪郭線2本を1mm平行移動すると
        軸線が基準点(0,0)を通らなくなり却下
    H6  **角度の寸法文字の偽装**(３０%%d -> ３５%%d): ゲート①(独立検証)が落とす
        (kind='angle' は text_override 必須なので、文字と実測角の照合が無いと素通りする)
    H7  角度の実測(`measure_angle_deg`)単体: 既知の角度の ANGULAR DIMENSION を合成して
        defpoint から測り直した値が一致する

実行:
    python 調査/run_cycle3_falsification.py [--out 調査/cycle3_falsification.json]
"""
import argparse
import glob
import io
import json
import math
import os
import shutil
import sys
import tempfile

import ezdxf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import dim_engine                    # noqa: E402
from engine import gate2_completeness as g2      # noqa: E402
from engine import generate_drawing              # noqa: E402


def _case(request_id):
    u"""依頼フォルダの生成DXFと計画を探す(合格=`生成/`・不合格=`生成/不合格/`の両方)。"""
    base = os.path.join(ROOT, u"data", u"依頼箱", request_id)
    cands = (sorted(glob.glob(os.path.join(base, u"生成", "*.dxf")))
             + sorted(glob.glob(os.path.join(base, u"生成", u"不合格", "*.dxf"))))
    if not cands:
        raise SystemExit(u"生成DXFが見つかりません: %s" % base)
    return {"dxf": cands[0], "plan": os.path.join(base, "plan.json")}


CLUSTER_CASE = _case(u"BLIND-25154-5-04")    # E1: 円周上の群配置穴群
INCLINED_CASE = _case(u"BLIND-25154-3-09")   # E2: 30度傾斜のφ6横穴2本


def _tmp(path):
    fd, p = tempfile.mkstemp(suffix=".dxf", prefix="falsify3_")
    os.close(fd)
    shutil.copy2(path, p)
    return p


def tamper_note(src, pairs):
    u"""注記MTEXTの文字だけを差し替える(実ジオメトリはそのまま=注記の偽装)。"""
    p = _tmp(src)
    doc = ezdxf.readfile(p)
    hit = 0
    for e in doc.modelspace():
        if e.dxftype() != "MTEXT":
            continue
        t = e.text
        for old, new in pairs:
            if old in t:
                t = t.replace(old, new)
        if t != e.text:
            e.text = t
            hit += 1
    doc.saveas(p)
    return p, hit


def rotate_one_hole(src, diameter, deg, center, scale=1.0, near=None):
    u"""指定径の穴を**1つだけ**中心まわりに回す(ＰＣＤ半径は変えずに配置角だけ壊す)。

    ❗SW投影の円は CIRCLE ではなく ARC 2〜4本に分割されて出るので、
      同じ中心の弧はまとめて回す(片方だけ回すと『穴が増えた』別の理由で落ちる)。
    """
    p = _tmp(src)
    doc = ezdxf.readfile(p)
    ents = [e for e in doc.modelspace()
            if e.dxftype() in ("CIRCLE", "ARC")
            and abs(e.dxf.radius * 2.0 - diameter * scale) < 1e-6]
    if not ents:
        return p, 0
    if near is None:
        c0 = ents[0].dxf.center
    else:
        c0 = min((e.dxf.center for e in ents),
                 key=lambda c: math.hypot(c.x - near[0], c.y - near[1]))
    a = math.radians(deg)
    hit = 0
    for e in ents:
        c = e.dxf.center
        if math.hypot(c.x - c0.x, c.y - c0.y) >= 1e-6:
            continue
        dx, dy = c.x - center[0], c.y - center[1]
        e.dxf.center = (center[0] + dx * math.cos(a) - dy * math.sin(a),
                        center[1] + dx * math.sin(a) + dy * math.cos(a), c.z)
        hit += 1
    doc.saveas(p)
    return p, hit


def move_lines(src, predicate, dx, dy):
    u"""条件に当たる LINE だけを平行移動する(傾斜穴の輪郭線をずらす)。"""
    p = _tmp(src)
    doc = ezdxf.readfile(p)
    hit = 0
    for e in doc.modelspace():
        if e.dxftype() != "LINE":
            continue
        s, t = e.dxf.start, e.dxf.end
        if not predicate(s, t):
            continue
        e.dxf.start = (s.x + dx, s.y + dy, s.z)
        e.dxf.end = (t.x + dx, t.y + dy, t.z)
        hit += 1
    doc.saveas(p)
    return p, hit


def tamper_dim_text(src, old, new):
    u"""DIMENSION の**描画された寸法文字**を差し替える。

    ❗`DIMENSION.dxf.text` を書き換えても検証側には効かない。
      `dim_engine.dim_text_of` は**アノニマスブロック(*Dn)内のMTEXT**(=紙に出る文字)を読む。
      改竄の反証は「紙に出る文字」を書き換えて行わないと意味が無い。
    """
    p = _tmp(src)
    doc = ezdxf.readfile(p)
    hit = 0
    for e in doc.modelspace():
        if e.dxftype() != "DIMENSION":
            continue
        geom = e.dxf.get("geometry", None)
        if not geom or geom not in doc.blocks:
            continue
        touched = False
        for b in doc.blocks.get(geom):
            if b.dxftype() == "MTEXT" and old in b.text:
                b.text = b.text.replace(old, new)
                touched = True
            elif b.dxftype() == "TEXT" and old in str(b.dxf.text):
                b.dxf.text = str(b.dxf.text).replace(old, new)
                touched = True
        if touched:
            if old in str(e.dxf.get("text", "")):
                e.dxf.text = str(e.dxf.text).replace(old, new)
            hit += 1
    doc.saveas(p)
    return p, hit


def gate2(dxf, plan, drop=()):
    r = g2.check_completeness(dxf, plan, drop_dim_ids=tuple(drop))
    return r["ok"], len(r["unspecified"]), r


def _rot2(v, deg):
    a = math.radians(deg)
    return [v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a)]


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, u"調査", "cycle3_falsification.json"))
    args = ap.parse_args(argv[1:])
    res, tmps = {}, []

    # ================= E1: 円周上の群(クラスタ)配置穴群 =================
    ok, n, rep = gate2(CLUSTER_CASE["dxf"], CLUSTER_CASE["plan"])
    cg = rep["cluster_groups"]
    adopted = [g for g in cg if g["ok"]]
    res["G1_cluster_base"] = {
        "gate2_ok": ok, "unspecified": n,
        "groups": [{"diameter": g["diameter"], "pcd": g["pcd"], "per_group": g["per_group"],
                    "groups": g["groups"], "pitches": g["pitches"], "gap_deg": g.get("gap_deg"),
                    "ok": g["ok"]} for g in cg],
        "expect": u"合格・ＰＣＤ142(φ9/φ14)とＰＣＤ110(φ11/φ17.5)の4群が採用される",
        "pass": (ok is True and n == 0 and len(cg) == 4 and len(adopted) == 4)}
    print(u"G1 5-04 素の判定: gate2=%s 未指定=%d 群配置=%d件採用/%d件 -> %s"
          % (ok, n, len(adopted), len(cg), u"OK" if res["G1_cluster_base"]["pass"] else u"NG"))

    # ---------- G2〜G6: 注記(自己申告)の偽装 ----------
    forgeries = [
        ("G2_pitch_1deg", [(u"振分角１２－１８－１２", u"振分角１２－１８－１３")],
         u"振分角を1度だけずらす(１２－１８－１３)"),
        ("G3_group_count", [(u"４個×３群", u"４個×４群")],
         u"群数を3->4に偽装(総数16 != 実数12)"),
        ("G4_regroup_same_total", [(u"４個×３群", u"３個×４群"),
                                   (u"振分角１２－１８－１２", u"振分角１２－１８")],
         u"総数12・ＰＣＤ142 は一致したまま群構成だけ3個×4群へ偽装"),
        ("G5_pcd", [(u"ＰＣＤ１４２", u"ＰＣＤ１４３")], u"ＰＣＤ値を143に偽装"),
        ("G6_pitch_order", [(u"振分角１２－１８－１２", u"振分角１８－１２－１２")],
         u"角度の集合は同じで並びだけ入れ替え(１８－１２－１２)"),
    ]
    for key, pairs, label in forgeries:
        p, hit = tamper_note(CLUSTER_CASE["dxf"], pairs)
        tmps.append(p)
        okx, nx, repx = gate2(p, CLUSTER_CASE["plan"])
        cgx = repx["cluster_groups"]
        pcd142 = [g for g in cgx if abs(g["pcd"] - 142.0) < 1.5]
        adoptedx = [g for g in cgx if g["ok"]]
        # ＰＣＤ142側(φ9/φ14)が却下され、ゲート②が不合格になること
        good = (hit > 0 and okx is False and nx > 0
                and all(not g["ok"] for g in pcd142) and len(adoptedx) < len(adopted))
        res[key] = {"replaced_notes": hit, "label": label, "gate2_ok": okx, "unspecified": nx,
                    "adopted_groups": len(adoptedx),
                    "reasons": [g["reason"] for g in pcd142][:2],
                    "expect": u"%s -> ＰＣＤ142の群を却下しゲート②不合格" % label,
                    "pass": good}
        print(u"%s %s: gate2=%s 未指定=%d 採用群=%d(素は%d) -> %s"
              % (key.split("_")[0], label, okx, nx, len(adoptedx), len(adopted),
                 u"OK" if good else u"NG"))

    # ---------- G7: 実ジオメトリの改竄(1穴だけ1度回す) ----------
    # 5-04 は 1:1・right ビューの中心は図面座標 (253.25, 161.0)(実測)
    p, hit = rotate_one_hole(CLUSTER_CASE["dxf"], 9.0, 1.0, (253.25, 161.0))
    tmps.append(p)
    ok7, n7, rep7 = gate2(p, CLUSTER_CASE["plan"])
    cg7 = [g for g in rep7["cluster_groups"] if abs(g["diameter"] - 9.0) < 1e-6]
    res["G7_geometry_rotated"] = {
        "moved_arcs": hit, "gate2_ok": ok7, "unspecified": n7,
        "phi9_group_ok": [g["ok"] for g in cg7],
        "reasons": [g["reason"] for g in cg7],
        "expect": u"φ9穴を1つ1度回す(ＰＣＤ半径は不変)と角度列が合わず却下・不合格",
        "pass": (hit > 0 and ok7 is False and n7 > 0 and cg7 and not any(g["ok"] for g in cg7))}
    print(u"G7 φ9穴を1つ1度回す: gate2=%s 未指定=%d φ9群採用=%s -> %s"
          % (ok7, n7, [g["ok"] for g in cg7],
             u"OK" if res["G7_geometry_rotated"]["pass"] else u"NG"))

    # ================= E2: 傾斜フィーチャー =================
    oki, ni, repi = gate2(INCLINED_CASE["dxf"], INCLINED_CASE["plan"])
    feats = repi["inclined_features"]
    ok_feats = [f for f in feats if f["ok"]]
    res["H1_inclined_base"] = {
        "gate2_ok": oki, "unspecified": ni,
        "features": [{"diameter": f["diameter"], "angle": f["angle_from_axis_deg"],
                      "dim_id": f["dim_id"], "anchor": f.get("anchor"), "ok": f["ok"]}
                     for f in feats],
        "expect": u"合格・φ6/傾斜30度の傾斜穴2件が基準点(X=0,Z=0)つきで採用される",
        "pass": (oki is True and ni == 0 and len(ok_feats) == 2
                 and all(abs(f["angle_from_axis_deg"] - 30.0) < 0.05 for f in ok_feats)
                 and all(f.get("anchor") == [0.0, 0.0] for f in ok_feats))}
    print(u"H1 3-09 素の判定: gate2=%s 未指定=%d 傾斜穴=%d件採用 角度=%s 基準点=%s -> %s"
          % (oki, ni, len(ok_feats), [f["angle_from_axis_deg"] for f in ok_feats],
             [f.get("anchor") for f in ok_feats],
             u"OK" if res["H1_inclined_base"]["pass"] else u"NG"))

    # ---------- H2/H3: 角度寸法を消す ----------
    # ❗**1本消しでは落ちない**(実測)。3-09 の傾斜穴2本は左右対称で Z(高さ)ノードを共有する
    #   ため、片方の穴が角度寸法で確定すると、残る穴の端点は
    #   **E5(f) 円×到達済みの直線**(φ76/φ110 を到達済みの Z で切った交点)で幾何的に決まる。
    #   これは正しい導出なので合格のままでよい。E2 が必要条件であることは H3(2本とも消す)で示す。
    #   CLAUDE.md の「反証は1本消しだけでは足りない・冗長な組をまとめて消せ」の実例。
    ok2, n2, rep2 = gate2(INCLINED_CASE["dxf"], INCLINED_CASE["plan"], drop=("A30R",))
    f2 = [f for f in rep2["inclined_features"] if f["ok"]]
    geo_edges2 = [e for a in rep2["axes"].values() for e in a["edges"]
                  if u"幾何導出" in (e.get("by") or u"")]
    res["H2_one_angle_dropped"] = {
        "gate2_ok": ok2, "unspecified": n2, "adopted": len(f2),
        "geometric_derivation_edges": len(geo_edges2),
        "expect": u"角度寸法A30Rを消すと傾斜フィーチャーの採用は1件に減るが、"
                  u"左右対称でZノードを共有するため残る穴の端点は"
                  u"E5(円×到達済み直線)で幾何導出され合格のまま(1本消しでは落ちない)",
        "pass": (ok2 is True and n2 == 0 and len(f2) == 1 and len(geo_edges2) > 0)}
    print(u"H2 角度寸法A30Rを消す: gate2=%s 未指定=%d 採用=%d件 幾何導出=%d本"
          u"(1本消しでは落ちないことの確認) -> %s"
          % (ok2, n2, len(f2), len(geo_edges2),
             u"OK" if res["H2_one_angle_dropped"]["pass"] else u"NG"))

    ok3, n3, rep3 = gate2(INCLINED_CASE["dxf"], INCLINED_CASE["plan"], drop=("A30R", "A30L"))
    f3 = [f for f in rep3["inclined_features"] if f["ok"]]
    res["H3_all_angles_dropped"] = {
        "gate2_ok": ok3, "unspecified": n3, "adopted": len(f3),
        "expect": u"角度寸法を2本とも消すと修理パス時点と同じ未指定12件へ戻る",
        "pass": (ok3 is False and n3 == 12 and len(f3) == 0)}
    print(u"H3 角度寸法を2本とも消す: gate2=%s 未指定=%d(修理パス時点と同じ12件か) 採用=%d件 -> %s"
          % (ok3, n3, len(f3), u"OK" if res["H3_all_angles_dropped"]["pass"] else u"NG"))

    # ---------- H4(核心): 角度寸法そのものの偽装 ----------
    # 実検出した傾斜フィーチャーに**偽の角度寸法**を合成し、_match_angle_dim が
    # 「向きが合っているだけ」「頂点がどこでもよい」では採用しないことを直接示す。
    f0 = ok_feats[0]
    udir = f0["direction"]
    # 実物と同じ条件の角度寸法を組み立て直す(頂点=実在端点・傾斜辺=輪郭方向・基準辺=軸方向)
    vtx = f0["endpoints"][1]          # 輪郭線の端点(実在)
    datum = [1.0, 0.0]
    geo = math.degrees(math.atan2(abs(udir[0] * datum[1] - udir[1] * datum[0]),
                                  udir[0] * datum[0] + udir[1] * datum[1]))

    def ad(vertex, ray_incl, ray_datum, value, i="FAKE"):
        return {"id": i, "role": "angle", "view": f0["view"], "axes": f0["axes"],
                "vertex": list(vertex), "rays": [list(ray_datum), list(ray_incl)],
                "value": value}

    good = g2._match_angle_dim(f0, [tuple(p) for p in f0["endpoints"]], tuple(udir),
                               [ad(vtx, udir, datum, geo, "REAL")])
    bad_vertex = g2._match_angle_dim(
        f0, [tuple(p) for p in f0["endpoints"]], tuple(udir),
        [ad([vtx[0] + 1.0, vtx[1] + 1.0], udir, datum, geo, "OFFVERTEX")])
    bad_incl = g2._match_angle_dim(
        f0, [tuple(p) for p in f0["endpoints"]], tuple(udir),
        [ad(vtx, _rot2(udir, 5.0), datum, geo, "NOTPARALLEL")])
    bad_datum = g2._match_angle_dim(
        f0, [tuple(p) for p in f0["endpoints"]], tuple(udir),
        [ad(vtx, udir, _rot2(datum, 5.0), geo, "TILTEDDATUM")])
    bad_value = g2._match_angle_dim(
        f0, [tuple(p) for p in f0["endpoints"]], tuple(udir),
        [ad(vtx, udir, datum, geo + 5.0, "WRONGVALUE")])
    res["H4_angle_dim_forgery"] = {
        "geometric_angle_deg": round(geo, 6),
        "real": good is not None, "vertex_off_by_1mm": bad_vertex,
        "inclined_ray_tilted_5deg": bad_incl, "datum_ray_tilted_5deg": bad_datum,
        "value_off_by_5deg": bad_value,
        "expect": u"頂点が実在端点・傾斜辺が輪郭と平行・基準辺が軸平行・"
                  u"実測角が2辺の成す角と一致、の全てを満たすものだけ採用",
        "pass": (good is not None and bad_vertex is None and bad_incl is None
                 and bad_datum is None and bad_value is None)}
    print(u"H4 角度寸法の偽装: 正=%s / 頂点1mmずれ=%s / 傾斜辺5度傾け=%s / 基準辺5度傾け=%s"
          u" / 値5度ずれ=%s -> %s"
          % (good is not None, bad_vertex, bad_incl, bad_datum, bad_value,
             u"OK" if res["H4_angle_dim_forgery"]["pass"] else u"NG"))

    # ---------- H5: 実ジオメトリの改竄(輪郭線を平行移動) ----------
    # 3-09(1:1)の front ビューは原点(188.75,161.0)・X=紙面x(+1)・Z=紙面y(-1)。
    # 右側の傾斜穴の輪郭線2本(紙面上で右上がり30度・x>200)だけを1mm動かす。
    def _is_right_hole(s, t):
        d = (t.x - s.x, t.y - s.y)
        if abs(d[0]) < 1e-9 or abs(d[1]) < 1e-9:
            return False
        ang = math.degrees(math.atan2(d[1], d[0])) % 180.0
        return abs(ang - 150.0) < 0.1 and min(s.x, t.x) > 200.0

    p, hit = move_lines(INCLINED_CASE["dxf"], _is_right_hole, 1.0, 0.0)
    tmps.append(p)
    ok5, n5, rep5 = gate2(p, INCLINED_CASE["plan"])
    f5 = [f for f in rep5["inclined_features"] if f["ok"]]
    res["H5_geometry_shifted"] = {
        "moved_lines": hit, "gate2_ok": ok5, "unspecified": n5, "adopted": len(f5),
        "reasons": [f["reason"] for f in rep5["inclined_features"] if not f["ok"]][:2],
        "expect": u"右側の傾斜穴の輪郭線2本を1mm動かすと軸線が基準点(0,0)を通らなくなり却下",
        "pass": (hit == 2 and ok5 is False and n5 > 0 and len(f5) == 1)}
    print(u"H5 傾斜穴の輪郭線2本を1mm動かす: 移動=%d本 gate2=%s 未指定=%d 採用=%d件 -> %s"
          % (hit, ok5, n5, len(f5), u"OK" if res["H5_geometry_shifted"]["pass"] else u"NG"))

    # ---------- H6: 角度の寸法文字の偽装(ゲート①) ----------
    base_iv = generate_drawing.independent_verify(INCLINED_CASE["dxf"], INCLINED_CASE["plan"])
    p, hit = tamper_dim_text(INCLINED_CASE["dxf"], u"３０", u"３５")
    tmps.append(p)
    iv = generate_drawing.independent_verify(p, INCLINED_CASE["plan"])
    ng_rows = [r for r in iv["gate1"] if not r["ok"]]
    res["H6_angle_text_forgery"] = {
        "tampered_dims": hit,
        "base_gate1_ok": base_iv["gate1_ok"], "tampered_gate1_ok": iv["gate1_ok"],
        "ng_rows": [{"id": r["id"], "text": r["text"], "measured": r["measured"],
                     "text_value": r["text_value"]} for r in ng_rows],
        "expect": u"角度の寸法文字を３０->３５に改竄すると、実測角30度と食い違って"
                  u"独立検証のゲート①が不合格になる",
        "pass": (hit == 2 and base_iv["gate1_ok"] is True and iv["gate1_ok"] is False
                 and len(ng_rows) == 2)}
    print(u"H6 角度の寸法文字を３０->３５に改竄: 素のgate1=%s 改竄後=%s NG行=%d -> %s"
          % (base_iv["gate1_ok"], iv["gate1_ok"], len(ng_rows),
             u"OK" if res["H6_angle_text_forgery"]["pass"] else u"NG"))

    # ---------- H7: 角度の実測(measure_angle_deg)+ **描かれた円弧** 単体 ----------
    # ❗2026-08-11: 旧H7は `add_angular_dim_2l(line1=(v,p1), line2=(v,p2))` を直に呼んで
    #   defpoint からの再計算だけを見ていたため、**ezdxf が優角(360-θ)側に円弧を描いている**
    #   ことを見逃していた(25154-3-09 が30度の表示で約310度の円弧を持ったまま納品されていた)。
    #   ここでは dim_engine と**同じ引数順**(line1=(v,p2), line2=(v,p1))で作り、
    #   実測角と**描画実体のARC**の両方が既知角と一致することを要求する。
    doc = ezdxf.new("R2000", setup=True)
    msp = doc.modelspace()
    probe, probe_arc = {}, {}
    for want in (30.0, 137.5, 285.0):
        v, r0 = (10.0, 5.0), 20.0
        p1 = (v[0] + r0, v[1])
        p2 = (v[0] + r0 * math.cos(math.radians(want)),
              v[1] + r0 * math.sin(math.radians(want)))
        d = msp.add_angular_dim_2l(base=(v[0] + 12.0, v[1] + 12.0),
                                   line1=(v, p2), line2=(v, p1))
        d.render()
        probe[want] = round(dim_engine.measure_angle_deg(d.dimension), 6)
        chk = dim_engine.check_angle_arc(doc, d.dimension, probe[want])
        probe_arc[want] = chk
    res["H7_measure_angle_deg"] = {
        "probe": {str(k): v for k, v in probe.items()},
        "arc": {str(k): v for k, v in probe_arc.items()},
        "expect": u"合成した既知角(30/137.5/285度)を defpoint から測り直して一致し、"
                  u"**描かれた円弧もその角のセクタ内**にある",
        "pass": (all(abs(probe[k] - k) <= 1e-6 for k in probe)
                 and all(probe_arc[k]["ok"] for k in probe_arc))}
    print(u"H7 measure_angle_deg + 描画円弧: %s / arc_ok=%s -> %s"
          % (probe, {k: v["ok"] for k, v in probe_arc.items()},
             u"OK" if res["H7_measure_angle_deg"]["pass"] else u"NG"))

    # ---------- H8: 優角(reflex)側に円弧を描く実装退行を検出できるか ----------
    # 旧実装と同じ引数順(line1=(v,p1), line2=(v,p2))で作ると、ezdxf は 360-θ の
    # 優角側に円弧を描く。**この状態を check_angle_arc が落とすこと**が反証の本体。
    doc2 = ezdxf.new("R2000", setup=True)
    msp2 = doc2.modelspace()
    reflex = {}
    for want in (30.0, 137.5):
        v, r0 = (10.0, 5.0), 20.0
        p1 = (v[0] + r0, v[1])
        p2 = (v[0] + r0 * math.cos(math.radians(want)),
              v[1] + r0 * math.sin(math.radians(want)))
        d = msp2.add_angular_dim_2l(base=(v[0] + 12.0, v[1] + 12.0),
                                    line1=(v, p1), line2=(v, p2))   # ❗旧(誤)の引数順
        d.render()
        m = dim_engine.measure_angle_deg(d.dimension)
        reflex[want] = {"measured": round(m, 6),
                        "arc": dim_engine.check_angle_arc(doc2, d.dimension, want)}
    res["H8_reflex_arc_regression"] = {
        "probe": {str(k): v for k, v in reflex.items()},
        "expect": u"旧実装の引数順(=優角側に円弧が出る)を再現すると、"
                  u"(1)実測角が 360-θ になってゲート①の値照合が必ず落ち、"
                  u"(2)『θ度のつもり』で円弧を検査しても不合格になる",
        "pass": all(abs(reflex[k]["measured"] - (360.0 - k)) <= 1e-6
                    and reflex[k]["arc"]["ok"] is False for k in reflex)}
    print(u"H8 優角側に描く実装退行: %s -> %s"
          % ({k: (v["measured"], v["arc"]["ok"]) for k, v in reflex.items()},
             u"OK" if res["H8_reflex_arc_regression"]["pass"] else u"NG"))

    # ---------- 集計 ----------
    total = sum(1 for v in res.values() if "pass" in v)
    passed = sum(1 for v in res.values() if v.get("pass"))
    print(u"\n===== 反証テスト(サイクル3) %d/%d 合格 =====" % (passed, total))
    for k, v in sorted(res.items()):
        if not v.get("pass"):
            print(u"  ** NG ** %s: %s" % (k, v.get("expect")))

    with io.open(args.out, "w", encoding="utf-8") as f:
        f.write(json.dumps({"passed": passed, "total": total, "cases": res},
                           ensure_ascii=False, indent=1))
    print(u"saved %s" % args.out)

    for p in tmps:
        try:
            os.remove(p)
        except OSError:
            pass
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
