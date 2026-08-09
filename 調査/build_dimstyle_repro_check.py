# -*- coding: utf-8 -*-
"""
検証: 図枠/dimstyle_spec.json のパラメータでezdxfにより寸法を新規生成し、
実在図面の同じ寸法(同一defpoint/測定値)と並べてレンダリング比較する。
出力: 調査/dimstyle_repro_check.png
"""
import sys
import io
import json
import ezdxf
from ezdxf.addons.drawing import RenderContext, Frontend
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
from ezdxf.addons.drawing.config import Configuration, BackgroundPolicy
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SPEC = json.load(open("図枠/dimstyle_spec.json", encoding="utf-8"))
BASE = SPEC["dimstyle_base"]
ARROW = SPEC["arrow"]
TXT = SPEC["text_style"]

REAL_A_PATH = r"荏原トライ調整用\DXF\部品表用DXFデータ\1.走行軸\25154-1-07_走行ギア.dxf"
REAL_B_PATH = r"荏原トライ調整用\DXF\部品表用DXFデータ\1.走行軸\25154-1-08_シャフト.dxf"

# 実在図面から採取した対象寸法の座標(調査時にezdxfで読み出し済み)
DIM_A = dict(base=(757.15703599099, 12.825895968332),
             p1=(682.25703599099, 38.073638217885),
             p2=(757.15703599099, 48.073638217885),
             angle=0, meas=74.9,
             crop=(660, 0, 780, 60))  # 実図面側の表示範囲(x0,y0,x1,y1)
DIM_B = dict(base=(329.01636705978, 169.99504143332),
             p1=(341.88150775786, 159.99504143332),
             p2=(341.88150775786, 169.99504143332),
             angle=90, meas=10.0, tm=0.036, tp=0.0,
             crop=(300, 140, 380, 190))


def build_repro_doc():
    doc = ezdxf.new("AC1015", setup=True)
    doc.encoding = "cp932"
    msp = doc.modelspace()

    # 標準矢印ブロック(_OPEN30)を明示的に生成する(setup=Trueだけでは未登録のため)
    from ezdxf.render.arrows import ARROWS
    ARROWS.create_block(doc.blocks, ARROWS.open_30)

    style = doc.styles.add("REPRO_DIMTXT", font=TXT["font"])
    style.dxf.width = TXT["width_factor"]
    style.dxf.oblique = TXT["oblique"]

    def make_dimstyle(name):
        ds = doc.dimstyles.add(name)
        ds.dxf.dimtxt = BASE["dimtxt"]["value"]
        ds.dxf.dimasz = BASE["dimasz"]["value"]
        ds.dxf.dimexo = BASE["dimexo"]["value"]
        ds.dxf.dimexe = BASE["dimexe"]["value"]
        ds.dxf.dimgap = BASE["dimgap"]["value"]
        ds.dxf.dimdec = BASE["dimdec"]["value"]
        ds.dxf.dimtad = BASE["dimtad"]["value"]
        ds.dxf.dimclrd = BASE["dimclrd"]["value"]
        ds.dxf.dimclre = BASE["dimclre"]["value"]
        ds.dxf.dimclrt = BASE["dimclrt"]["value"]
        ds.dxf.dimdli = BASE["dimdli"]["value"]
        ds.dxf.dimscale = BASE["dimscale"]["value"]
        ds.dxf.dimlfac = BASE["dimlfac"]["value"]
        ds.dxf.dimsah = BASE["dimsah"]["value"]
        ds.dxf.dimjust = BASE["dimjust"]["value"]
        ds.dxf.dimcen = BASE["dimcen"]["value"]
        ds.dxf.dimdsep = 46  # '.'(実在図面は小数点表記。未設定だと既定の','になり不一致になる)
        ds.dxf.dimblk1 = ARROW["dimblk1"]["value"]
        ds.dxf.dimblk2 = ARROW["dimblk2"]["value"]
        ds.dxf.dimtxsty = "REPRO_DIMTXT"
        return ds

    make_dimstyle("REPRO_PLAIN")
    ds_tol = make_dimstyle("REPRO_TOL")
    tol = SPEC["tolerance"]["native_limit_tolerance"]
    ds_tol.dxf.dimtol = 1
    ds_tol.dxf.dimtp = DIM_B["tp"]
    ds_tol.dxf.dimtm = DIM_B["tm"]
    ds_tol.dxf.dimtfac = tol["params"]["dimtfac"]
    ds_tol.dxf.dimtdec = 3
    ds_tol.dxf.dimtolj = tol["params"]["dimtolj"]

    dim_a = msp.add_linear_dim(base=DIM_A["base"], p1=DIM_A["p1"], p2=DIM_A["p2"],
                                angle=DIM_A["angle"], dimstyle="REPRO_PLAIN")
    dim_a.render()

    dim_b = msp.add_linear_dim(base=DIM_B["base"], p1=DIM_B["p1"], p2=DIM_B["p2"],
                                angle=DIM_B["angle"], dimstyle="REPRO_TOL")
    dim_b.render()

    doc.saveas("調査/dimstyle_repro_generated.dxf")
    return doc


def render_layout(ax, doc, xlim=None, ylim=None):
    ctx = RenderContext(doc)
    backend = MatplotlibBackend(ax)
    # BackgroundPolicy.WHITE: 紙(白背景)前提でACI7(白/黒automatic)を黒として描画させる。
    # これを付けないとdimclrt=7(白)の寸法文字が白背景に埋もれて見えなくなる。
    cfg = Configuration(background_policy=BackgroundPolicy.WHITE)
    Frontend(ctx, backend, config=cfg).draw_layout(doc.modelspace(), finalize=True)
    if xlim:
        ax.set_xlim(*xlim)
    if ylim:
        ax.set_ylim(*ylim)


def main():
    repro_doc = build_repro_doc()
    real_a = ezdxf.readfile(REAL_A_PATH)
    real_b = ezdxf.readfile(REAL_B_PATH)

    fig, axes = plt.subplots(2, 2, figsize=(16, 10), dpi=130)
    fig.suptitle("dimstyle_spec.json reproduction check: left=real drawing, right=ezdxf regenerated",
                 fontsize=12)

    ax = axes[0][0]
    render_layout(ax, real_a, xlim=(DIM_A["crop"][0], DIM_A["crop"][2]),
                  ylim=(DIM_A["crop"][1], DIM_A["crop"][3]))
    ax.set_title(f"REAL A: 25154-1-07_walking_gear.dxf  linear dim (measured={DIM_A['meas']})")

    ax = axes[0][1]
    render_layout(ax, repro_doc, xlim=(DIM_A["crop"][0], DIM_A["crop"][2]),
                  ylim=(DIM_A["crop"][1], DIM_A["crop"][3]))
    ax.set_title("REPRO A: ezdxf regenerated (dimstyle_spec REPRO_PLAIN)")

    ax = axes[1][0]
    render_layout(ax, real_b, xlim=(DIM_B["crop"][0], DIM_B["crop"][2]),
                  ylim=(DIM_B["crop"][1], DIM_B["crop"][3]))
    ax.set_title(f"REAL B: 25154-1-08_shaft.dxf  linear dim w/ tolerance (10 +0/-0.036)")

    ax = axes[1][1]
    render_layout(ax, repro_doc, xlim=(DIM_B["crop"][0], DIM_B["crop"][2]),
                  ylim=(DIM_B["crop"][1], DIM_B["crop"][3]))
    ax.set_title("REPRO B: ezdxf regenerated (dimstyle_spec REPRO_TOL, dimtol=1)")

    fig.tight_layout()
    fig.savefig("調査/dimstyle_repro_check.png", facecolor="white")
    print("saved 調査/dimstyle_repro_check.png")


if __name__ == "__main__":
    main()
