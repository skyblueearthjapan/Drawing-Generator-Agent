# -*- coding: utf-8 -*-
u"""正面ビュー候補(上位N個)を投影してDXF化する(AIオペレータ目視選択の材料)。

    python 調査/phase5_ai_operator/project_candidates.py <STEPパス> <出力ディレクトリ> <候補JSON>

候補JSON = [{"id":"C1","sw_view":"*正面","rot":0,"note":"..."}, ...]
「SW標準ビュー名 + 紙面内回転角(度・CCW)」= CLAUDE.md「候補集合は有限(最大24通り)」の座標。

各候補につき **正面ビュー1枚だけ**の図面を作ってDXFへ出す(目視で正面を選ぶための材料なので
平面/右側面は不要。SW時間を1/4に節約できる)。

安全規約(CLAUDE.md): 読み取り専用 / QuitDoc / 自分が開いたものだけ閉じる / pre-post記録。
"""
import io
import json
import os
import sys
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import draw_pipeline as dp  # noqa: E402


def main():
    step = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(ROOT, sys.argv[1])
    out_dir = sys.argv[2] if os.path.isabs(sys.argv[2]) else os.path.join(ROOT, sys.argv[2])
    cand_p = sys.argv[3] if os.path.isabs(sys.argv[3]) else os.path.join(ROOT, sys.argv[3])
    with io.open(cand_p, encoding="utf-8") as f:
        cands = json.load(f)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    sw, mod = dp.connect()
    pre = dp.list_open_docs(sw)
    print(u"pre:", pre)
    part = None
    out = {"step": step, "pre_open": pre, "candidates": []}
    try:
        part, info = dp.open_model_readonly(sw, mod, step)
        print(u"opened:", part.title)
        m = dp.part_metrics(mod, part)
        out["metrics"] = m
        for c in cands:
            dwgdoc = None
            rec = dict(c)
            try:
                dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
                v = dwg.CreateDrawViewFromModelView3(part.model_name, c["sw_view"],
                                                     0.4205, 0.297, 0.0)
                if v is None:
                    raise RuntimeError(u"CreateDrawViewFromModelView3 が None: %s" % c["sw_view"])
                v = mod.IView(v._oleobj_)
                v.SetDisplayMode3(False, dp.swHIDDEN_GREYED, False, False)
                # ❗STEP由来B-repは円筒が半割り → 接線エッジは非表示が既定(CLAUDE.md)
                v.SetDisplayTangentEdges2(dp.swTangentEdgesHidden)
                dp._set_view_scale(v, 1.0)
                if c.get("rot"):
                    dp._set_view_angle(v, float(c["rot"]))
                ol = dp._view_outline_mm(v)
                mgn = dp.OUTLINE_MARGIN_MM
                rec["geom_mm"] = [ol[0] + mgn, ol[1] + mgn, ol[2] - mgn, ol[3] - mgn]
                rec["geom_size_mm"] = [ol[2] - ol[0] - 2 * mgn, ol[3] - ol[1] - 2 * mgn]
                rec["model_to_view"] = list(dp.prop(v, "ModelToViewTransform").ArrayData)
                dwgdoc.doc.ForceRebuild3(False)
                dwgdoc.doc.ViewZoomtofit2()
                dxf_p = os.path.join(out_dir, "cand_%s.dxf" % c["id"])
                res = dp.export_dxf(sw, dwgdoc, dxf_p)
                rec["dxf"] = res["path"]
                rec["ok"] = True
                print(u"  %s %s%+d度 -> %.1f x %.1f mm" % (
                    c["id"], c["sw_view"], c.get("rot", 0),
                    rec["geom_size_mm"][0], rec["geom_size_mm"][1]))
            except Exception:
                rec["ok"] = False
                rec["error"] = traceback.format_exc()
                print(rec["error"])
            finally:
                if dwgdoc is not None:
                    try:
                        dwgdoc.close()
                    except Exception as e:
                        rec["close_error"] = str(e)
            out["candidates"].append(rec)
        out["ok"] = True
    except Exception:
        out["ok"] = False
        out["error"] = traceback.format_exc()
        print(out["error"])
    finally:
        if part is not None:
            try:
                part.close()
            except Exception as e:
                out["close_error"] = str(e)
        post = dp.list_open_docs(sw)
        out["post_open"] = post
        print(u"post:", post)
    p = os.path.join(out_dir, "candidates_meta.json")
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(u"保存:", p)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
