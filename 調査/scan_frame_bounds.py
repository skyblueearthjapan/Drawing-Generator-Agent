# -*- coding: utf-8 -*-
"""
指定DXFのmodelspaceを展開込み(INSERTをvirtual_entities()で再帰的に展開)でスキャンし、
図枠(0,0)-(420,297)の外に描画されるエンティティが無いことを確認する。

使い方:
    python 調査/scan_frame_bounds.py <dxf> [--margin 0.5]
"""
import sys
import io
import argparse

import ezdxf

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

FRAME_X0, FRAME_Y0, FRAME_X1, FRAME_Y1 = 0.0, 0.0, 420.0, 297.0


def bbox_of_entity(e):
    t = e.dxftype()
    xs, ys = [], []
    try:
        if t == "LINE":
            xs += [e.dxf.start.x, e.dxf.end.x]
            ys += [e.dxf.start.y, e.dxf.end.y]
        elif t in ("ARC", "CIRCLE"):
            c = e.dxf.center
            rad = e.dxf.radius
            xs += [c.x - rad, c.x + rad]
            ys += [c.y - rad, c.y + rad]
        elif t == "LWPOLYLINE":
            for p in e.get_points():
                xs.append(p[0])
                ys.append(p[1])
        elif t in ("TEXT", "MTEXT"):
            xs.append(e.dxf.insert.x)
            ys.append(e.dxf.insert.y)
        elif t == "POINT":
            xs.append(e.dxf.location.x)
            ys.append(e.dxf.location.y)
        elif t == "DIMENSION":
            dp = e.dxf.defpoint
            xs.append(dp.x)
            ys.append(dp.y)
        elif t == "LEADER":
            for v in e.vertices:
                xs.append(v[0])
                ys.append(v[1])
    except Exception:
        pass
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def iter_expanded(msp):
    """modelspace直下のエンティティをINSERTのみ再帰展開してyieldする。"""
    for e in msp:
        if e.dxftype() == "INSERT":
            try:
                for ve in e.virtual_entities():
                    yield ve
            except Exception:
                pass
        else:
            yield e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dxf")
    ap.add_argument("--margin", type=float, default=0.5)
    args = ap.parse_args()

    doc = ezdxf.readfile(args.dxf)
    msp = doc.modelspace()

    total = 0
    out_of_frame = []
    for e in iter_expanded(msp):
        total += 1
        bbox = bbox_of_entity(e)
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        if (x0 < FRAME_X0 - args.margin or y0 < FRAME_Y0 - args.margin or
                x1 > FRAME_X1 + args.margin or y1 > FRAME_Y1 + args.margin):
            out_of_frame.append((e.dxftype(), bbox))

    print(f"file: {args.dxf}")
    print(f"展開後エンティティ総数(直下+INSERT展開): {total}")
    print(f"枠(0,0)-(420,297)外のエンティティ数(margin={args.margin}): {len(out_of_frame)}")
    for t, bbox in out_of_frame[:50]:
        print(f"  OUT: {t} bbox={bbox}")
    if len(out_of_frame) > 50:
        print(f"  ... 他 {len(out_of_frame) - 50} 件")

    return 0 if not out_of_frame else 1


if __name__ == "__main__":
    sys.exit(main())
