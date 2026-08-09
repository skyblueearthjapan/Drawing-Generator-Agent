# -*- coding: utf-8 -*-
"""DXFエンティティダンプ: 図面解釈用に円/円弧/線/ポリライン/注記/寸法を線種付きで出力"""
import sys
import io
import ezdxf

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

path = sys.argv[1]
doc = ezdxf.readfile(path)
msp = doc.modelspace()

counts = {}
for e in msp:
    counts[e.dxftype()] = counts.get(e.dxftype(), 0) + 1
print("== COUNTS ==", counts)

def lt(e):
    l = e.dxf.linetype if e.dxf.hasattr("linetype") else "BYLAYER"
    if l == "BYLAYER":
        try:
            l = doc.layers.get(e.dxf.layer).dxf.linetype
        except Exception:
            pass
    return f"{l}/L{e.dxf.layer}"

print("\n== CIRCLES ==")
for e in msp.query("CIRCLE"):
    c = e.dxf.center
    print(f"c=({c.x:.2f},{c.y:.2f}) r={e.dxf.radius:.3f} d={e.dxf.radius*2:.3f} [{lt(e)}]")

print("\n== ARCS (r>=3) ==")
for e in msp.query("ARC"):
    if e.dxf.radius < 3: continue
    c = e.dxf.center
    print(f"c=({c.x:.2f},{c.y:.2f}) r={e.dxf.radius:.3f} a={e.dxf.start_angle:.1f}..{e.dxf.end_angle:.1f} [{lt(e)}]")

print("\n== LWPOLYLINES ==")
for e in msp.query("LWPOLYLINE"):
    pts = [(round(p[0],2), round(p[1],2)) for p in e.get_points()]
    print(f"closed={e.closed} n={len(pts)} {pts[:12]}{'...' if len(pts)>12 else ''} [{lt(e)}]")

print("\n== LINES (len>=5) ==")
lines = []
for e in msp.query("LINE"):
    s, t = e.dxf.start, e.dxf.end
    ln = ((t.x-s.x)**2 + (t.y-s.y)**2) ** 0.5
    if ln < 5: continue
    lines.append((ln, s, t, lt(e)))
lines.sort(key=lambda x: -x[0])
for ln, s, t, l in lines[:150]:
    print(f"({s.x:.1f},{s.y:.1f})-({t.x:.1f},{t.y:.1f}) len={ln:.1f} [{l}]")
if len(lines) > 150: print(f"... +{len(lines)-150} more")

print("\n== TEXT/MTEXT ==")
for e in msp:
    if e.dxftype() == "TEXT":
        p = e.dxf.insert
        print(f"({p.x:.1f},{p.y:.1f}) '{e.dxf.text}'")
    elif e.dxftype() == "MTEXT":
        p = e.dxf.insert
        print(f"({p.x:.1f},{p.y:.1f}) '{e.text}'")

print("\n== DIMENSIONS ==")
for e in msp.query("DIMENSION"):
    try:
        m = e.get_measurement()
        if hasattr(m, "x"):
            m = f"vec({m.x:.2f},{m.y:.2f})"
        else:
            m = f"{m:.3f}"
    except Exception:
        m = "?"
    txt = e.dxf.text if e.dxf.hasattr("text") else ""
    dp = e.dxf.defpoint
    print(f"type={e.dimtype} meas={m} text='{txt}' def=({dp.x:.1f},{dp.y:.1f})")

print("\n== INSERTS ==")
for e in msp.query("INSERT"):
    p = e.dxf.insert
    print(f"block={e.dxf.name} at ({p.x:.1f},{p.y:.1f})")
