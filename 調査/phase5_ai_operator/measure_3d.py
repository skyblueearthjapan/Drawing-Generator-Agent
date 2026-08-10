# -*- coding: utf-8 -*-
u"""AIオペレータ層v1: STEPだけから「作図計画を立てるための3D計測」を採る。

    python 調査/phase5_ai_operator/measure_3d.py <STEP相対/絶対パス> <出力JSON>

採るもの(人間図面は一切見ない):
  - 質量特性・体積・bbox・body数(draw_pipeline.part_metrics)
  - 全面: 種類 / 面積 / 面bbox / 平面の法線&点 / 円筒の軸&径&原点 / 円錐 / トーラス
  - 全エッジ: 直線(端点)・円/円弧(中心・軸・半径・全周かどうか)
  - 円エッジを「軸方向・半径・軸線位置」でグループ化 → 穴/段/座ぐりの候補台帳

安全規約(CLAUDE.md): 読み取り専用インポート / QuitDoc で保存せず閉じる /
pre-post スナップショットを記録 / 自分が開いたものだけ閉じる。
"""
import io
import json
import math
import os
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import draw_pipeline as dp  # noqa: E402

SURF = {4001: u"平面", 4002: u"円筒", 4003: u"円錐", 4004: u"球",
        4005: u"トーラス", 4006: u"Bスプライン", 4007: u"回転", 4008: u"押出",
        4009: u"オフセット", 4010: u"その他"}
CURVE = {3001: u"直線", 3002: u"円", 3003: u"楕円", 3004: u"内挿", 3005: u"Bカーブ",
         3006: u"その他"}


def r6(v):
    return round(float(v), 6)


def survey(mod, part):
    ip = mod.IPartDoc(part.doc._oleobj_)
    bodies = ip.GetBodies2(0, True) or []
    faces_out = []
    edges_out = []
    seen_edges = set()
    for bi, b in enumerate(bodies):
        ib = mod.IBody2(b._oleobj_)
        for fi, f in enumerate(list(dp.prop(ib, "GetFaces") or [])):
            iface = mod.IFace2(f._oleobj_)
            surf = mod.ISurface(dp.prop(iface, "GetSurface")._oleobj_)
            st = dp.prop(surf, "Identity")
            rec = {"body": bi, "face": fi, "type": SURF.get(st, st),
                   "type_id": st,
                   "area_mm2": r6(dp.prop(iface, "GetArea") * 1e6)}
            try:
                bx = list(dp.prop(iface, "GetBox"))
                rec["box_mm"] = [r6(v * 1000.0) for v in bx]
            except Exception:
                pass
            if st == 4001:
                p = list(dp.prop(surf, "PlaneParams"))
                rec["normal"] = [r6(v) for v in p[0:3]]
                rec["point_mm"] = [r6(v * 1000.0) for v in p[3:6]]
            elif st == 4002:
                p = list(dp.prop(surf, "CylinderParams"))
                rec["origin_mm"] = [r6(v * 1000.0) for v in p[0:3]]
                rec["axis"] = [r6(v) for v in p[3:6]]
                rec["d_mm"] = r6(p[6] * 2000.0)
            elif st == 4003:
                p = list(dp.prop(surf, "ConeParams"))
                rec["origin_mm"] = [r6(v * 1000.0) for v in p[0:3]]
                rec["axis"] = [r6(v) for v in p[3:6]]
                rec["r_mm"] = r6(p[6] * 1000.0)
                rec["half_angle_deg"] = r6(math.degrees(p[7])) if len(p) > 7 else None
            elif st == 4005:
                p = list(dp.prop(surf, "TorusParams"))
                rec["origin_mm"] = [r6(v * 1000.0) for v in p[0:3]]
                rec["axis"] = [r6(v) for v in p[3:6]]
                rec["major_r_mm"] = r6(p[6] * 1000.0)
                rec["minor_r_mm"] = r6(p[7] * 1000.0) if len(p) > 7 else None
            faces_out.append(rec)

            for e in list(dp.prop(iface, "GetEdges") or []):
                iedge = mod.IEdge(e._oleobj_)
                try:
                    cp = list(dp.prop(iedge, "GetCurveParams3"))
                except Exception:
                    cp = None
                cur = dp.prop(iedge, "GetCurve")
                if cur is None:
                    continue
                icur = mod.ICurve(cur._oleobj_)
                ct = dp.prop(icur, "Identity")
                rec_e = {"type": CURVE.get(ct, ct), "type_id": ct}
                if cp:
                    # [sx,sy,sz, ex,ey,ez, ustart, uend, ...]
                    rec_e["start_mm"] = [r6(v * 1000.0) for v in cp[0:3]]
                    rec_e["end_mm"] = [r6(v * 1000.0) for v in cp[3:6]]
                    if len(cp) >= 8:
                        rec_e["u"] = [r6(cp[6]), r6(cp[7])]
                if ct == 3002:
                    try:
                        cc = list(dp.prop(icur, "CircleParams"))
                        rec_e["center_mm"] = [r6(v * 1000.0) for v in cc[0:3]]
                        rec_e["axis"] = [r6(v) for v in cc[3:6]]
                        rec_e["d_mm"] = r6(cc[6] * 2000.0)
                        if rec_e.get("u"):
                            sweep = abs(rec_e["u"][1] - rec_e["u"][0])
                            rec_e["full_circle"] = bool(abs(sweep - 2 * math.pi) < 1e-6)
                            rec_e["sweep_deg"] = r6(math.degrees(sweep))
                    except Exception:
                        pass
                key = json.dumps(rec_e, sort_keys=True)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                edges_out.append(rec_e)
    return faces_out, edges_out


def group_circles(edges, tol=0.05):
    u"""円エッジを (軸方向, 半径, 軸線の位置) でまとめる。穴・段・座ぐりの台帳になる。"""
    groups = {}
    for e in edges:
        if e.get("type_id") != 3002 or "center_mm" not in e:
            continue
        ax = e["axis"]
        # 軸の向きは符号を正規化(絶対値最大成分を正にする)
        k = max(range(3), key=lambda i: abs(ax[i]))
        sgn = 1.0 if ax[k] >= 0 else -1.0
        axn = tuple(round(v * sgn, 3) for v in ax)
        c = e["center_mm"]
        # 軸線の位置 = 中心から軸成分を除いたもの
        dotv = sum(c[i] * axn[i] for i in range(3))
        perp = tuple(round(c[i] - dotv * axn[i], 2) for i in range(3))
        key = (axn, round(e["d_mm"], 2), perp)
        g = groups.setdefault(key, {"axis": list(axn), "d_mm": round(e["d_mm"], 4),
                                    "axis_line_point_mm": list(perp),
                                    "positions_along_axis_mm": [], "count": 0,
                                    "full_circles": 0})
        g["count"] += 1
        if e.get("full_circle"):
            g["full_circles"] += 1
        g["positions_along_axis_mm"].append(round(dotv, 3))
    out = []
    for g in groups.values():
        pos = sorted(set(g["positions_along_axis_mm"]))
        merged = []
        for p in pos:
            if not merged or abs(merged[-1] - p) > tol:
                merged.append(p)
        g["positions_along_axis_mm"] = merged
        out.append(g)
    out.sort(key=lambda g: (-g["d_mm"], g["axis_line_point_mm"]))
    return out


def main():
    step = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(ROOT, sys.argv[1])
    out_p = sys.argv[2] if os.path.isabs(sys.argv[2]) else os.path.join(ROOT, sys.argv[2])
    sw, mod = dp.connect()
    pre = dp.list_open_docs(sw)
    print(u"pre:", pre)
    part = None
    data = {"step": step, "pre_open": pre}
    try:
        part, info = dp.open_model_readonly(sw, mod, step)
        data["title"] = part.title
        data["open_info"] = info
        m = dp.part_metrics(mod, part)
        data["metrics"] = m
        faces, edges = survey(mod, part)
        data["faces"] = faces
        data["edges"] = edges
        data["circle_groups"] = group_circles(edges)
        data["ok"] = True
        print(u"bbox=%r size=%r vol=%.1fmm3 bodies=%d faces=%d edges=%d" % (
            [round(v, 3) for v in m["bbox_mm"]], [round(v, 3) for v in m["size_mm"]],
            m["volume_mm3"], m["body_count"], len(faces), len(edges)))
    except Exception:
        data["ok"] = False
        data["error"] = traceback.format_exc()
        print(data["error"])
    finally:
        if part is not None:
            try:
                part.close()
            except Exception as e:
                data["close_error"] = str(e)
        post = dp.list_open_docs(sw)
        data["post_open"] = post
        print(u"post:", post)
    d = os.path.dirname(out_p)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    with io.open(out_p, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    print(u"保存:", out_p)
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
