# -*- coding: utf-8 -*-
u"""フェーズ2: SLDPRT → 図面 → 第三角法3面図+等角投影 → DXF のパイプライン実行。

usage: python 調査/run_phase2_pipeline.py [SLDPRTパス]
出力  : 調査/phase2_out_<部品名>.dxf, 調査/phase2_meta_<部品名>.json
"""
import sys, io, os, json, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import draw_pipeline as dp

DEFAULT_PART = (r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3D CAD Operator Agent"
                r"\生成3D\15015-P3-013_ホルダー.SLDPRT")

part_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PART
stem = os.path.splitext(os.path.basename(part_path))[0]
out_dxf = os.path.join(ROOT, u"調査", u"phase2_out_%s.dxf" % stem)
out_json = os.path.join(ROOT, u"調査", u"phase2_meta_%s.json" % stem)

meta = {"part_path": part_path}
part = None
dwgdoc = None
sw = None

try:
    sw, mod = dp.connect()
    print("connected:", dp.sw_compat.detected_progid())

    # --- 開いていたドキュメントの記録(自分のもの以外に触らないための保険) ---
    pre_titles = []
    d = sw.GetFirstDocument()
    while d is not None:
        try:
            pre_titles.append(dp.prop(d, "GetTitle"))
        except Exception:
            pass
        d = d.GetNext()
    print(u"実行前に開いていたドキュメント:", pre_titles)
    meta["pre_open_titles"] = pre_titles

    # ---------- 1. 部品を読み取り専用で開いて計測 ----------
    part, vh = dp.open_part_readonly(sw, mod, part_path)
    print(u"opened part:", part.title, "VersionHistory=", vh)
    meta["version_history"] = vh
    m = dp.part_metrics(mod, part)
    meta["metrics"] = m
    print(u"mass=%.6f kg  volume=%.3f mm^3" % (m["mass_kg"], m["volume_mm3"]))
    print(u"bbox_mm=", [round(v, 4) for v in m["bbox_mm"]])
    print(u"size_mm=", [round(v, 4) for v in m["size_mm"]])
    print(u"com_mm =", [round(v, 4) for v in m["com_mm"]])
    print(u"material:", m.get("material"), u"/ density=%.3f kg/m^3 (既定の水?=%s)"
          % (m["density_kgm3"], m.get("density_is_default_water")))
    print(u"model views:", m["model_view_names"])

    # ---------- 2. 図面ドキュメント新規作成(A1・尺度1:1・第三角法・図枠なし) ----------
    dwgdoc, dwg, sheet = dp.new_drawing(sw, mod)
    print(u"drawing title:", dwgdoc.title)
    si = dp.sheet_info(mod, sheet)
    meta["sheet"] = si
    print(u"sheet:", si)

    # ---------- 3. ビュー配置 ----------
    views = dp.insert_standard_views(mod, dwg, part.path, m["size_mm"])
    meta["views"] = views
    for k in ("front", "top", "right", "iso"):
        v = views[k]
        print(u"  %-6s name=%-10s geom_mm=%s size=%s scale=%s"
              % (k, v["name"], [round(c, 3) for c in v["geom_mm"]],
                 [round(c, 3) for c in v["geom_size_mm"]], v["scale_decimal"]))
    print(u"  layout:", views["_layout"])
    meta["views_on_sheet"] = dp.list_views(mod, dwg)

    dwgdoc.doc.ForceRebuild3(False)
    dwgdoc.doc.ViewZoomtofit2()

    # ---------- 4. DXF 出力 ----------
    res = dp.export_dxf(sw, dwgdoc, out_dxf)
    meta["export"] = {"path": res["path"], "bytes": res["bytes"],
                      "warnings": res["warnings"]}
    print(u"DXF:", res["path"], res["bytes"], "bytes")
    meta["ok"] = True

except Exception:
    meta["ok"] = False
    meta["error"] = traceback.format_exc()
    traceback.print_exc()

finally:
    # 自分が作った図面 → 閉じる。部品 → 閉じる。順序は図面が先(参照が切れないように)
    for od, label in ((dwgdoc, u"図面"), (part, u"部品")):
        if od is None:
            continue
        try:
            cur = dp.prop(od.doc, "GetTitle")
            print(u"closing %s: %r (mine=%s)" % (label, cur, od.mine))
            od.close()
        except Exception as e:
            print(u"close %s 失敗: %s" % (label, e))
    if sw is not None:
        post = []
        d = sw.GetFirstDocument()
        while d is not None:
            try:
                post.append(dp.prop(d, "GetTitle"))
            except Exception:
                pass
            d = d.GetNext()
        print(u"実行後に開いているドキュメント:", post)
        meta["post_open_titles"] = post

with io.open(out_json, "w", encoding="utf-8") as f:
    f.write(json.dumps(meta, ensure_ascii=False, indent=2, default=str))
print("meta:", out_json)
sys.exit(0 if meta.get("ok") else 1)
