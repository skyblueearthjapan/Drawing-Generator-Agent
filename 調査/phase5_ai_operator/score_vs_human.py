# -*- coding: utf-8 -*-
u"""採点(ゲート④): AIオペレータが選んだ向き・作った寸法セットを人間図面と突き合わせる。

    python 調査/phase5_ai_operator/score_vs_human.py <key> <meta.json> <views.dxf> <人間DXF> <出力dir>

**この段階で初めて人間図面を開く**(計画立案フェーズでは絶対に開かない・実験の生命線)。

出すもの
  1. 向き判定: 人間のfront役ビューが、AIが選んだ向きのSW front と恒等一致するか
     (`調査/phase4_batch/run_batch.py:evaluate` をそのまま流用。投影は既にAIの選んだ向きで
      出ているので「恒等一致=AIの選択が人間と同じ」になる)
  2. 人間図面の寸法・注記の全リスト(値・種別・所在ビュー)
  3. ゲート④の幾何照合(円直径差・外形差・被覆・鏡像)

安全規約: 人間DXFは読むだけ。
"""
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, u"調査"))
sys.path.insert(0, os.path.join(ROOT, u"調査", "phase4_batch"))

import ezdxf                                    # noqa: E402
import compare_views as cv                      # noqa: E402
import run_batch as rb                          # noqa: E402
import dim_engine                               # noqa: E402


def dump_human_dims(path):
    u"""人間図面の DIMENSION / 穴注記MTEXT を全部拾う(値は defpoint から再計算)。"""
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    dims, notes, texts = [], [], []
    for e in msp:
        t = e.dxftype()
        if t == "DIMENSION":
            st = doc.dimstyles.get(e.dxf.dimstyle) if e.dxf.dimstyle in doc.dimstyles else None
            dims.append({
                "dimtype": e.dxf.dimtype, "base": e.dxf.dimtype & 7,
                "dimstyle": str(e.dxf.dimstyle),
                "dimpost": (st.dxf.get("dimpost", "") if st is not None else None),
                "dimtol": (st.dxf.get("dimtol", 0) if st is not None else None),
                "dimtp": (st.dxf.get("dimtp", None) if st is not None else None),
                "dimtm": (st.dxf.get("dimtm", None) if st is not None else None),
                "text_attr": e.dxf.get("text", ""),
                "measured": dim_engine.measure_from_defpoints(e),
                "rendered": dim_engine.dim_text_of(doc, e),
                "defpoint": [round(e.dxf.defpoint.x, 4), round(e.dxf.defpoint.y, 4)],
                "defpoint2": ([round(e.dxf.defpoint2.x, 4), round(e.dxf.defpoint2.y, 4)]
                              if e.dxf.hasattr("defpoint2") else None),
                "defpoint3": ([round(e.dxf.defpoint3.x, 4), round(e.dxf.defpoint3.y, 4)]
                              if e.dxf.hasattr("defpoint3") else None),
                "angle": e.dxf.get("angle", None),
            })
        elif t in ("MTEXT", "TEXT"):
            s = e.text if t == "MTEXT" else e.dxf.text
            texts.append({"type": t, "text": s,
                          "insert": [round(e.dxf.insert.x, 4), round(e.dxf.insert.y, 4)]})
    for r in texts:
        s = r["text"]
        if ("%%c" in s or u"キリ" in s or u"ザグリ" in s or u"深さ" in s
                or u"タップ" in s or "%%C" in s):
            notes.append(r)
    return {"dims": dims, "hole_notes": notes, "all_texts": texts}


def main():
    key, meta_p, dxf_p, human_p, out_dir = sys.argv[1:6]
    use_frame = ("--no-frame" not in sys.argv)
    meta_p = meta_p if os.path.isabs(meta_p) else os.path.join(ROOT, meta_p)
    dxf_p = dxf_p if os.path.isabs(dxf_p) else os.path.join(ROOT, dxf_p)
    human_p = human_p if os.path.isabs(human_p) else os.path.join(ROOT, human_p)
    out_dir = out_dir if os.path.isabs(out_dir) else os.path.join(ROOT, out_dir)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    with io.open(meta_p, encoding="utf-8") as f:
        meta = json.load(f)
    # run_batch.evaluate は meta['view_orient']['view_plan'] を見る
    meta.setdefault("view_orient", {})["view_plan"] = meta["view_plan"]

    cmp_p = os.path.join(out_dir, "compare_%s.json" % key)
    cmpj = cv.run_compare(key, dxf_p, meta, human_p, use_frame=use_frame, out_path=cmp_p)
    verdict = rb.evaluate(cmpj, meta["view_plan"])

    human = dump_human_dims(human_p)
    out = {"key": key, "chosen_front": meta.get("chosen_front"),
           "verdict": verdict, "human_dims": human}
    p = os.path.join(out_dir, "score_%s.json" % key)
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2, default=str))

    o = verdict["orientation"]
    print(u"\n===== 向き判定 =====")
    print(u"  AIの選択     : %s %+d度" % (meta["chosen_front"]["sw_view"],
                                          meta["chosen_front"]["rotation_deg"]))
    print(u"  人間front役  : SW-%s を %s (同点=%r)" % (
        o.get("matched_sw_view"), o.get("matched_transform"), o.get("ties")))
    print(u"  判定         : %s" % o.get("verdict"))
    print(u"\n===== ゲート④ =====")
    print(u"  %s / fails=%r" % (verdict["gate4"].get("verdict"), verdict["gate4"].get("fails")))
    print(u"\n===== 人間図面の寸法 %d 本 =====" % len(human["dims"]))
    for d in human["dims"]:
        print(u"  base=%d post=%r 実測=%s 文字=%r 位置=%r" % (
            d["base"], d["dimpost"],
            None if d["measured"] is None else round(d["measured"], 3),
            d["rendered"], d["defpoint"]))
    print(u"\n===== 人間図面の穴注記 %d 件 =====" % len(human["hole_notes"]))
    for n in human["hole_notes"]:
        print(u"  %r @%r" % (n["text"], n["insert"]))
    print(u"\n保存: %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
