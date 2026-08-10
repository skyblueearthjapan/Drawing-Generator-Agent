# -*- coding: utf-8 -*-
u"""不一致だった部品の目視確認PNGを作る(SolidWorks不要・保存済みDXF/JSONだけで動く)。

    python 調査/phase4_batch/make_pngs.py            # 合格以外を全部
    python 調査/phase4_batch/make_pngs.py 4-15 3-12  # 図番指定
"""
import os
import sys
import io
import json
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, os.path.join(ROOT, u"調査"))

import render_step_check as rsc  # noqa: E402


def main():
    want = [a for a in sys.argv[1:] if not a.startswith("-")]
    with io.open(os.path.join(HERE, "targets.json"), encoding="utf-8") as f:
        targets = json.load(f)["targets"]
    n = 0
    for t in targets:
        d = os.path.join(HERE, t["key"])
        rp = os.path.join(d, "result.json")
        if not os.path.exists(rp):
            continue
        with io.open(rp, encoding="utf-8") as f:
            r = json.load(f)
        verdict = (r.get("verdict") or {}).get("verdict")
        if want:
            if t["key"] not in want:
                continue
        elif verdict == u"合格":
            continue
        if not os.path.exists(os.path.join(d, "compare.json")):
            print(u"skip %s(照合が無い: %s)" % (t["key"], verdict))
            continue
        with io.open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        with io.open(os.path.join(d, "compare.json"), encoding="utf-8") as f:
            cmpj = json.load(f)
        try:
            rsc.render(u"%s %s (%s)" % (t["key"], t["name"], verdict),
                       os.path.join(d, "views.dxf"), meta, cmpj,
                       os.path.join(ROOT, t["human_dxf"]),
                       os.path.join(d, "check.png"),
                       use_frame=bool(t["bucket_a"]))
            n += 1
        except Exception:
            traceback.print_exc()
    print(u"%d 枚作成" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
