# -*- coding: utf-8 -*-
"""共通(固定)エンティティ113個を、基準ファイル(サンプル先頭)に対して一覧化しファイル出力する"""
import sys
import io
import ezdxf

sys.path.insert(0, "調査")
from sample_files import sample_paths
import extract_common as ec

paths = sample_paths()
ref_path = paths[0]
doc = ezdxf.readfile(ref_path)
msp = doc.modelspace()

per_file_sigs = []
for p in paths:
    d = ezdxf.readfile(p)
    m = d.modelspace()
    sigs = set()
    for e in m:
        s = ec.entity_sig(e, d)
        if s is not None:
            sigs.add(s)
    per_file_sigs.append(sigs)
common = set(per_file_sigs[0])
for s in per_file_sigs[1:]:
    common &= s

rows = []
for e in msp:
    s = ec.entity_sig(e, doc)
    if s in common:
        t = e.dxftype()
        if t == "TEXT":
            rows.append((t, e.dxf.layer, e.dxf.insert.x, e.dxf.insert.y, e.dxf.text))
        elif t == "MTEXT":
            rows.append((t, e.dxf.layer, e.dxf.insert.x, e.dxf.insert.y, e.text))
        elif t == "LINE":
            rows.append((t, e.dxf.layer, e.dxf.start.x, e.dxf.start.y,
                          f"-> ({e.dxf.end.x:.2f},{e.dxf.end.y:.2f})"))
        elif t == "CIRCLE":
            rows.append((t, e.dxf.layer, e.dxf.center.x, e.dxf.center.y, f"r={e.dxf.radius}"))
        elif t == "LWPOLYLINE":
            pts = [(round(p[0], 2), round(p[1], 2)) for p in e.get_points()]
            rows.append((t, e.dxf.layer, pts[0][0], pts[0][1],
                         f"closed={e.closed} n={len(pts)} {pts[:8]}"))
        elif t == "POINT":
            rows.append((t, e.dxf.layer, e.dxf.location.x, e.dxf.location.y, ""))
        elif t == "INSERT":
            rows.append((t, e.dxf.layer, e.dxf.insert.x, e.dxf.insert.y, e.dxf.name))

rows.sort(key=lambda r: (r[0], r[2], r[3]))

with io.open("調査/common_entities_dump.txt", "w", encoding="utf-8") as f:
    for r in rows:
        f.write(f"{r[0]:10s} L{r[1]:>4s} ({r[2]:9.3f},{r[3]:9.3f}) {r[4]}\n")
    f.write(f"TOTAL {len(rows)}\n")

print("wrote 調査/common_entities_dump.txt", len(rows))
