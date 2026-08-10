# -*- coding: utf-8 -*-
u"""改善サイクル2(E4/E5/E3)の**反証テスト**。

サイクル2で足した3つの規則は、どれも「今まで落ちていたものを通す」方向の変更なので、
**通してはいけないものが通らないこと**を機械的に試験する。
反証の効かない緩和は「検証ゲート」ではなく「合格印の自動押印機」でしかない。

  E4(紙面内で回転した正多角形を斜め寸法で決める)
    C1  素の判定: 25154-1-04 は合格し、六角形が対角(斜め寸法HEXD)でカバーされる
    C2  **対角寸法を消す**と六角形が未指定に戻りゲート②不合格
    C3  **対角値偽装**: 測定点は実在頂点だが「隣り合う頂点」(=辺長)の斜め寸法は採用しない
    C4  **自己申告偽装(核心)**: 中心対称・長さは対角ぴったり・しかし**頂点でない**点対は
        採用しない。向きと長さだけで判定する実装ならここを通してしまう
    C4b 二面幅も同様: 長さが二面幅ぴったりでも**実在する対辺の中点**でなければ採用しない

  E3(管用テーパねじの呼びで同心群の内部径をカバーする)
    C5  素の判定: 25154-3-04 は合格し、Ｒｃ１／８ の同心群(中心Y=-2,Z=0)が同定される
    C6  **呼び偽装(Ｒｃ１／４)**: 実体φ7.9〜10.13に対し呼びが大きすぎる -> 却下・不合格
    C7  **呼び偽装(Ｒｃ１／１６)**: 呼びが小さすぎる -> 却下・不合格
    C8  **呼び検算不等式**単体: 実測半径群に対し Ｒｃ１／８ だけが真になる
        (範囲照合だけでは隣の呼びも通るため、この不等式が呼びを一意にしている)

  E5(円×直線の片側弦・円×円の交点を幾何導出にする)
    C9  素の判定: 25154-6-02 は合格し、片側弦(√(210²-161²))と円×円の交点が導出される
    C10 **交点ずらし**: φ12の穴を1つ動かすと交点が実ジオメトリに当たらなくなり不合格

実行:
    python 調査/run_cycle2_falsification.py [--out 調査/cycle2_falsification.json]
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

from engine import gate2_completeness as g2   # noqa: E402


def _case(request_id):
    u"""依頼フォルダの生成DXFと計画を探す(合格=`生成/`・不合格=`生成/不合格/`の両方)。"""
    base = os.path.join(ROOT, u"data", u"依頼箱", request_id)
    cands = (sorted(glob.glob(os.path.join(base, u"生成", "*.dxf")))
             + sorted(glob.glob(os.path.join(base, u"生成", u"不合格", "*.dxf"))))
    if not cands:
        raise SystemExit(u"生成DXFが見つかりません: %s" % base)
    return {"dxf": cands[0], "plan": os.path.join(base, "plan.json")}


POLY_CASE = _case(u"BLIND-25154-1-04")     # E4: 回転した正六角形
PIPE_CASE = _case(u"BLIND-25154-3-04")     # E3: Rc1/8 テーパ雌ねじ
GEO_CASE = _case(u"BLIND-25154-6-02")      # E5: 円×直線・円×円


def _tmp(path):
    fd, p = tempfile.mkstemp(suffix=".dxf", prefix="falsify2_")
    os.close(fd)
    shutil.copy2(path, p)
    return p


def tamper_note(src, old, new):
    u"""注記MTEXTの文字だけを差し替える(実ジオメトリはそのまま=呼びの偽装)。"""
    p = _tmp(src)
    doc = ezdxf.readfile(p)
    hit = 0
    for e in doc.modelspace():
        if e.dxftype() == "MTEXT" and old in e.text:
            e.text = e.text.replace(old, new)
            hit += 1
    doc.saveas(p)
    return p, hit


def move_one_hole(src, diameter, dx, scale=1.0, near=None):
    u"""指定径の穴を1つだけ動かす。

    ❗SW投影の円は CIRCLE ではなく **ARC 2〜4本に分割**されて出るので、
    同じ中心の弧をまとめて動かす(片方だけ動かすと「穴が増えた」別の理由で落ちる)。
    ❗同じ径の穴が複数ある場合、`near`(図面座標)で**どの穴か**を必ず指定すること。
      指定しないと「導出に関与していない穴」を動かしてしまい、反証が空振りする
      (6-02 は φ12 が6個あり、円×円の交点に効くのは (73,±164) の2個だけ)。
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
    hit = 0
    for e in ents:
        c = e.dxf.center
        if math.hypot(c.x - c0.x, c.y - c0.y) < 1e-6:
            e.dxf.center = (c.x + dx, c.y, c.z)
            hit += 1
    doc.saveas(p)
    return p, hit


def gate2(dxf, plan, drop=()):
    r = g2.check_completeness(dxf, plan, drop_dim_ids=tuple(drop))
    return r["ok"], len(r["unspecified"]), r


def _rot(p, c, deg):
    a = math.radians(deg)
    dx, dy = p[0] - c[0], p[1] - c[1]
    return (c[0] + dx * math.cos(a) - dy * math.sin(a),
            c[1] + dx * math.sin(a) + dy * math.cos(a))


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, u"調査", "cycle2_falsification.json"))
    args = ap.parse_args(argv[1:])
    res, tmps = {}, []

    # ================= E4: 回転した正多角形の斜め寸法 =================
    ok, n, rep = gate2(POLY_CASE["dxf"], POLY_CASE["plan"])
    polys = rep["polygons"]
    hexa = polys[0] if polys else None
    obl = rep["oblique_width_dimensions"]
    res["C1_poly_base"] = {
        "gate2_ok": ok, "unspecified": n,
        "polygons": [{"n": p["n"], "ok": p["ok"], "covered_by": p["covered_by"]} for p in polys],
        "oblique_dims": obl,
        "expect": u"合格・六角形が斜め寸法(対角)でカバーされる",
        "pass": (ok is True and len(polys) == 1 and polys[0]["ok"] is True
                 and u"対角" in (polys[0]["covered_by"] or u"") and len(obl) == 1)}
    print(u"C1 1-04 素の判定: gate2=%s 未指定=%d 多角形=%s -> %s"
          % (ok, n, polys[0]["covered_by"] if polys else None,
             u"OK" if res["C1_poly_base"]["pass"] else u"NG"))

    # ---------- C2: 対角寸法を消すと未指定へ戻る ----------
    did = obl[0]["id"] if obl else "HEXD"
    ok2, n2, rep2 = gate2(POLY_CASE["dxf"], POLY_CASE["plan"], drop=(did,))
    poly_unspec = [u_ for u_ in rep2["unspecified"] if u_["feature"] == "polygon"]
    res["C2_poly_dim_dropped"] = {
        "dropped": did, "gate2_ok": ok2, "unspecified": n2,
        "polygon_unspecified": len(poly_unspec),
        "expect": u"斜め寸法%s を消すと六角形が未指定に戻り不合格" % did,
        "pass": (ok2 is False and len(poly_unspec) == 1)}
    print(u"C2 対角寸法%sを消す: gate2=%s 未指定=%d(多角形%d件) -> %s"
          % (did, ok2, n2, len(poly_unspec),
             u"OK" if res["C2_poly_dim_dropped"]["pass"] else u"NG"))

    # ---------- C3/C4/C4b: 斜め寸法の「自己申告」を実ジオメトリで潰せているか ----------
    # 実検出した六角形に対して**偽の斜め寸法**を合成し、polygon_covered が
    # 「値が合っているだけ」では採用しないことを直接示す。
    if hexa:
        c = hexa["center"]
        vs = hexa["vertices"]
        dd, w = hexa["across_corners"], hexa["across_flats"]

        def od(pts, val, i="FAKE"):
            return {"id": i, "view": hexa["view"], "axes": hexa["axes"],
                    "value": val, "angle_deg": 0.0, "points": [list(pts[0]), list(pts[1])]}

        # 正: 相対する頂点対(実在の頂点)
        opp = (vs[0], vs[3])
        good = g2.polygon_covered(hexa, [], [], [od(opp, dd, "REAL")])
        # C3: 実在頂点だが**隣り合う**頂点対(=辺長。中心対称でない)
        adj = (vs[0], vs[1])
        adj_len = math.hypot(adj[0][0] - adj[1][0], adj[0][1] - adj[1][1])
        c3 = g2.polygon_covered(hexa, [], [], [od(adj, adj_len, "ADJ")])
        # C4(核心): 中心対称・長さは対角ぴったり・しかし頂点を30度外した点対
        f1 = _rot(vs[0], c, 30.0)
        f2 = (2 * c[0] - f1[0], 2 * c[1] - f1[1])
        c4 = g2.polygon_covered(hexa, [], [], [od((f1, f2), dd, "FAKEDIAG")])
        # C4b: 長さは二面幅ぴったり・中心対称・しかし辺の中点ではない点対
        m0 = ((vs[0][0] + vs[1][0]) / 2.0, (vs[0][1] + vs[1][1]) / 2.0)
        g1 = _rot(m0, c, 15.0)
        g2p = (2 * c[0] - g1[0], 2 * c[1] - g1[1])
        c4b = g2.polygon_covered(hexa, [], [], [od((g1, g2p), w, "FAKEFLAT")])

        res["C3_C4_oblique_forgery"] = {
            "across_corners": dd, "across_flats": w, "adjacent_len": round(adj_len, 6),
            "real_opposite_vertices": good, "adjacent_vertices": c3,
            "fake_diagonal_same_length": c4, "fake_flats_same_length": c4b,
            "expect": u"実在の相対頂点対だけが採用される。隣接頂点/頂点でない点対/"
                      u"辺中点でない点対は、長さが一致していても採用しない",
            "pass": (good is not None and c3 is None and c4 is None and c4b is None)}
        print(u"C3/C4 斜め寸法の偽装: 正(相対頂点)=%s / 隣接頂点=%s / 対角長だが頂点でない=%s"
              u" / 二面幅長だが辺中点でない=%s -> %s"
              % (good is not None, c3, c4, c4b,
                 u"OK" if res["C3_C4_oblique_forgery"]["pass"] else u"NG"))

    # ================= E3: 管用テーパねじの呼び =================
    okp, np_, repp = gate2(PIPE_CASE["dxf"], PIPE_CASE["plan"])
    tf = repp.get("thread_features") or []
    res["C5_pipe_base"] = {
        "gate2_ok": okp, "unspecified": np_,
        "thread_features": [{"designation": f["designation"], "kind": f["kind"],
                             "center": f["center"], "diameters": f["diameters"]} for f in tf],
        "expect": u"合格・Ｒｃ１／８の同心群(中心Y=-2,Z=0)が1件同定される",
        "pass": (okp is True and len(tf) == 1 and tf[0]["designation"] == "Rc1/8")}
    print(u"C5 3-04 素の判定: gate2=%s 未指定=%d ねじ群=%s -> %s"
          % (okp, np_, [f["designation"] for f in tf],
             u"OK" if res["C5_pipe_base"]["pass"] else u"NG"))

    # ---------- C6/C7: 呼びの偽装 ----------
    for key, old, new, label in (
            ("C6_pipe_designation_too_big", u"Ｒｃ１／８", u"Ｒｃ１／４", u"Ｒｃ１／４"),
            ("C7_pipe_designation_too_small", u"Ｒｃ１／８", u"Ｒｃ１／１６", u"Ｒｃ１／１６")):
        p, hit = tamper_note(PIPE_CASE["dxf"], old, new)
        tmps.append(p)
        okx, nx, repx = gate2(p, PIPE_CASE["plan"])
        tfx = repx.get("thread_features") or []
        res[key] = {
            "replaced_notes": hit, "designation": label, "gate2_ok": okx, "unspecified": nx,
            "thread_features": [f["designation"] for f in tfx],
            "expect": u"実ジオメトリ(φ7.9〜10.13)は%sでは説明できない -> 同心群を採用せず不合格"
                      % label,
            "pass": (hit > 0 and okx is False and not tfx)}
        print(u"%s 呼びを%sに偽装: gate2=%s 未指定=%d ねじ群=%s -> %s"
              % (key[:2], label, okx, nx, [f["designation"] for f in tfx],
                 u"OK" if res[key]["pass"] else u"NG"))

    # ---------- C8: 呼び検算不等式そのもの ----------
    radii = sorted({round(d / 2.0, 4) for f in tf for d in f["diameters"]}) if tf else []
    judged = {s: g2.pipe_thread_designation_ok(s, radii) for s in ("1/16", "1/8", "1/4", "3/8")}
    res["C8_designation_inequality"] = {
        "radii": radii, "judged": judged,
        "expect": u"実測半径群に対し 1/8 のみ True(範囲照合だけでは隣の呼びも通るため、"
                  u"この不等式が呼びを一意にしている)",
        "pass": (judged.get("1/8") is True
                 and not any(v for k, v in judged.items() if k != "1/8"))}
    print(u"C8 呼び検算不等式 半径%s -> %s -> %s"
          % (radii, judged, u"OK" if res["C8_designation_inequality"]["pass"] else u"NG"))

    # ================= E5: 幾何導出(片側弦・円×円) =================
    okg, ng, repg = gate2(GEO_CASE["dxf"], GEO_CASE["plan"])
    geo_edges = [e for a in repg["axes"].values() for e in a["edges"]
                 if u"幾何導出" in (e.get("by") or u"")]
    chord = [e for e in geo_edges if u"切った交点" in e["by"]]
    xx = [e for e in geo_edges if u"の交点" in e["by"] and u"切った交点" not in e["by"]]
    res["C9_geo_base"] = {
        "gate2_ok": okg, "unspecified": ng,
        "chord_edges": [e["by"] for e in chord], "circle_circle_edges": [e["by"] for e in xx],
        "expect": u"合格・片側弦(円φ420をX=161で切る)と円×円(φ360×φ12)の導出が出る",
        "pass": (okg is True and len(chord) >= 2 and len(xx) >= 4)}
    print(u"C9 6-02 素の判定: gate2=%s 未指定=%d 片側弦=%d本 円×円=%d本 -> %s"
          % (okg, ng, len(chord), len(xx), u"OK" if res["C9_geo_base"]["pass"] else u"NG"))

    # ---------- C10: 交点ずらし ----------
    # ❗円×円の交点に効くのはモデル(73,164)の穴だけ(図面座標(223.75,243.0)・1:2)。
    #   `near` を指定せず先頭の穴を動かすと、導出に関与していない穴が動くだけで
    #   反証が空振りする(実際に一度そうなった)。
    scale = 0.5   # 6-02 は 1:2
    p, hit = move_one_hole(GEO_CASE["dxf"], 12.0, 1.0, scale=scale, near=(223.75, 243.0))
    tmps.append(p)
    okc, nc, repc = gate2(p, GEO_CASE["plan"])
    geo_after = [e for a in repc["axes"].values() for e in a["edges"]
                 if u"円φ12" in (e.get("by") or u"") and u"の交点" in (e.get("by") or u"")]
    res["C10_intersection_shifted"] = {
        "moved_arcs": hit, "gate2_ok": okc, "unspecified": nc,
        "circle_circle_edges_before": len(xx), "circle_circle_edges_after": len(geo_after),
        "expect": u"モデル(73,164)のφ12穴を1mm動かすと円×円の交点が実ジオメトリに"
                  u"当たらなくなり、その側の位置ノードが未指定になって不合格",
        "pass": (hit > 0 and okc is False and nc > ng and len(geo_after) < len(xx))}
    print(u"C10 φ12穴(73,164)を1mmずらす: gate2=%s 未指定=%d(素は%d) 円×円導出=%d本(素は%d本) -> %s"
          % (okc, nc, ng, len(geo_after), len(xx),
             u"OK" if res["C10_intersection_shifted"]["pass"] else u"NG"))

    # ---------- 集計 ----------
    total = sum(1 for v in res.values() if "pass" in v)
    passed = sum(1 for v in res.values() if v.get("pass"))
    print(u"\n===== 反証テスト(サイクル2) %d/%d 合格 =====" % (passed, total))
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
