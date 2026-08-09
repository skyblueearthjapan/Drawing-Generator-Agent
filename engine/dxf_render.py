# -*- coding: utf-8 -*-
"""DXF→PNGレンダリング (領域指定可): python dxf_render.py <dxf> <out.png> [xmin ymin xmax ymax]"""
import sys
import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

path, out = sys.argv[1], sys.argv[2]
doc = ezdxf.readfile(path)
msp = doc.modelspace()

fig = plt.figure(figsize=(16, 12), dpi=120)
ax = fig.add_axes([0, 0, 1, 1])
ctx = RenderContext(doc)
backend = MatplotlibBackend(ax)
Frontend(ctx, backend).draw_layout(msp, finalize=True)

if len(sys.argv) >= 7:
    x0, y0, x1, y1 = map(float, sys.argv[3:7])
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)

fig.savefig(out, facecolor="white")
print("saved", out)
