# -*- coding: utf-8 -*-
"""計測がどこで止まるのかを、段ごとに秒を刻んで確かめる(読み取りだけ)。

    python probe_measure.py <STEPのパス>

measure_3d.py と同じ順序で dp.connect / open_model_readonly / part_metrics / survey を
呼び、各段の所要秒を即時に出す。survey は面・稜線を1本ずつ数えながら進む。
"""
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
ROOT = Path(r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

step = sys.argv[1]
t0 = time.time()


def mark(label: str) -> None:
    print(f"  [{time.time() - t0:7.1f}s] {label}", flush=True)


from engine import draw_pipeline as dp  # noqa: E402

mark("import 完了")
sw, mod = dp.connect()
mark("connect 完了")
print("  開いている文書:", dp.list_open_docs(sw), flush=True)

part = None
try:
    part, info = dp.open_model_readonly(sw, mod, step)
    mark(f"open_model_readonly 完了 {info}")
    m = dp.part_metrics(mod, part)
    mark(f"part_metrics 完了 bbox={[round(v, 1) for v in m['bbox_mm']]} bodies={m['body_count']}")

    # survey 相当を自前で刻む
    bodies = part.GetBodies2(0, True)
    bodies = list(bodies) if bodies else []
    mark(f"GetBodies2 完了 bodies={len(bodies)}")
    nface = nedge = 0
    for bi, body in enumerate(bodies):
        body = mod.IBody2(body)
        face = body.GetFirstFace()
        while face:
            face = mod.IFace2(face)
            nface += 1
            if nface % 25 == 0:
                mark(f"面 {nface} 本目")
            try:
                face.GetSurface()
                face.GetArea()
            except Exception as exc:  # noqa: BLE001
                mark(f"面 {nface} で例外 {exc}")
            face = face.GetNextFace()
        mark(f"body {bi}: 面 {nface} 本を数え終えました")
        edges = body.GetEdges()
        edges = list(edges) if edges else []
        nedge += len(edges)
        mark(f"body {bi}: 稜線 {len(edges)} 本")
    mark(f"survey 相当 完了 faces={nface} edges={nedge}")
finally:
    if part is not None:
        try:
            part.close()
            mark("part.close 完了")
        except Exception as exc:  # noqa: BLE001
            mark(f"close で例外 {exc}")
    print("  残り文書:", dp.list_open_docs(sw), flush=True)
mark("おわり")
