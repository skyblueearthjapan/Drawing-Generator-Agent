# -*- coding: utf-8 -*-
u"""STEP由来モデルの円筒面半径を SW で直接実測する(人間図面との差の切り分け用)。

    python 調査/measure_step_faces.py <STEP相対パス>

安全: 読み取り専用インポート / QuitDoc で保存せず閉じる。
"""
import os
import sys
import io
import json
import traceback
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import draw_pipeline as dp  # noqa: E402

# swSurfaceTypes_e
SURF = {4001: u"平面", 4002: u"円筒", 4003: u"円錐", 4004: u"球",
        4005: u"トーラス", 4006: u"B-Spline", 4007: u"回転", 4008: u"押出"}

step = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(ROOT, sys.argv[1])

sw, mod = dp.connect()
pre = dp.list_open_docs(sw)
print(u"pre:", pre)
part = None
try:
    part, info = dp.open_model_readonly(sw, mod, step)
    print(u"opened:", part.title)
    ip = mod.IPartDoc(part.doc._oleobj_)
    bodies = ip.GetBodies2(0, True) or []
    counts = Counter()
    cyls = []
    for b in bodies:
        ib = mod.IBody2(b._oleobj_)
        faces = dp.prop(ib, "GetFaces") or []
        for f in list(faces):
            iface = mod.IFace2(f._oleobj_)
            surf = mod.ISurface(dp.prop(iface, "GetSurface")._oleobj_)
            st = dp.prop(surf, "Identity")
            counts[SURF.get(st, st)] += 1
            if st == 4002:      # 円筒
                par = list(dp.prop(surf, "CylinderParams"))
                # [x, y, z, ax, ay, az, radius] (m)
                r_mm = par[6] * 1000.0
                cyls.append({"d_mm": round(r_mm * 2, 4),
                             "axis": [round(v, 4) for v in par[3:6]],
                             "origin_mm": [round(v * 1000, 4) for v in par[0:3]],
                             "area_mm2": round(dp.prop(iface, "GetArea") * 1e6, 4)})
    print(u"面の種類:", dict(counts))
    print(u"円筒面(直径mm):")
    for c in sorted(cyls, key=lambda z: -z["d_mm"]):
        print(u"  d=%-9.4f 軸=%r 原点=%r 面積=%.2f" % (
            c["d_mm"], c["axis"], c["origin_mm"], c["area_mm2"]))
    out = {"step": step, "surface_counts": {str(k): v for k, v in counts.items()},
           "cylinders": cyls}
    op = os.path.join(ROOT, u"調査", u"step_check",
                      u"faces_%s.json" % os.path.splitext(os.path.basename(step))[0])
    with io.open(op, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2))
    print(u"保存:", op)
except Exception:
    traceback.print_exc()
finally:
    if part is not None:
        try:
            part.close()
        except Exception as e:
            print(u"close err:", e)
    print(u"post:", dp.list_open_docs(sw))
