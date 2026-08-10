# -*- coding: utf-8 -*-
u"""教師STEP → SolidWorks投影 → 4ビューDXF+meta を出す(フェーズ2残課題)。

    python 調査/run_step_projection.py <ラベル> <STEP相対パス>

安全: pre/post スナップショット照合 / 自分が開いたものだけ QuitDoc・CloseDoc / 一切保存しない。
"""
import os
import sys
import io
import json
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import draw_pipeline as dp  # noqa: E402

OUT_DIR = os.path.join(ROOT, u"調査", u"step_check")


def run(label, step_path, tangent=dp.swTangentEdgesVisibleAndFonted):
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    out_dxf = os.path.join(OUT_DIR, u"views_%s.dxf" % label)
    out_meta = os.path.join(OUT_DIR, u"meta_%s.json" % label)

    meta = {"label": label, "step": step_path, "tangent_mode": tangent}
    part = dwgdoc = sw = None
    try:
        sw, mod = dp.connect()
        meta["sw_progid"] = dp.sw_compat.detected_progid()
        pre = dp.list_open_docs(sw)
        meta["pre_open"] = pre
        print(u"pre_open:", pre)

        part, info = dp.open_model_readonly(sw, mod, step_path)
        meta["open_info"] = info
        meta["doc_title"] = part.title
        meta["doc_pathname"] = dp.prop(part.doc, "GetPathName")
        print(u"opened: title=%r pathname=%r" % (part.title, meta["doc_pathname"]))

        m = dp.part_metrics(mod, part)
        meta["metrics"] = m
        print(u"  bbox_mm=%r size_mm=%r vol_mm3=%.3f com_mm=%r bodies=%d" % (
            m["bbox_mm"], m["size_mm"], m["volume_mm3"],
            [round(c, 4) for c in m["com_mm"]], m["body_count"]))

        dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
        meta["dwg_title"] = dwgdoc.title
        meta["sheet"] = dp.sheet_info(mod, sheet)

        views = dp.insert_standard_views(mod, dwg, part.model_name, m["size_mm"],
                                         tangent=tangent)
        meta["views"] = views
        for k in ("front", "top", "right", "iso"):
            v = views[k]
            print(u"  %-5s geom=%r size=%r scale=%r" % (
                k, [round(x, 3) for x in v["geom_mm"]],
                [round(x, 3) for x in v["geom_size_mm"]], v["scale_decimal"]))

        dwgdoc.doc.ForceRebuild3(False)
        dwgdoc.doc.ViewZoomtofit2()
        res = dp.export_dxf(sw, dwgdoc, out_dxf)
        meta["export"] = {"path": res["path"], "bytes": res["bytes"]}
        meta["ok"] = True
        print(u"  DXF: %s (%d bytes)" % (res["path"], res["bytes"]))
    except Exception:
        meta["ok"] = False
        meta["error"] = traceback.format_exc()
        print(meta["error"])
    finally:
        for od, lab in ((dwgdoc, u"図面"), (part, u"部品")):
            if od is None:
                continue
            try:
                od.close()
            except Exception as e:
                meta.setdefault("close_errors", []).append("%s: %s" % (lab, e))
        if sw is not None:
            post = dp.list_open_docs(sw)
            meta["post_open"] = post
            pre_titles = [t for t, _ in (meta.get("pre_open") or [])]
            leaked = [t for t, _ in post if t not in pre_titles]
            meta["leaked_docs"] = leaked
            print(u"post_open:", post, u"leaked:", leaked)

    with io.open(out_meta, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
    return meta


if __name__ == "__main__":
    label = sys.argv[1]
    rel = sys.argv[2]
    tan = int(sys.argv[3]) if len(sys.argv) > 3 else dp.swTangentEdgesVisibleAndFonted
    p = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
    r = run(label, p, tangent=tan)
    sys.exit(0 if r.get("ok") else 1)
