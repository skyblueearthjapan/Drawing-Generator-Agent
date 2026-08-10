# -*- coding: utf-8 -*-
u"""subtract_frame が効かない原因を突き止める(テンプレとの署名差分)。"""
import os
import sys
import io
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))

import ezdxf  # noqa: E402
import frame_extract as fe  # noqa: E402

TPL = os.path.join(ROOT, u"図枠", u"frame_template.dxf")
path = sys.argv[1] if os.path.isabs(sys.argv[1]) else os.path.join(ROOT, sys.argv[1])

tdoc, tsigs = fe.load_frame_signatures(TPL)
doc = ezdxf.readfile(path)
msp = doc.modelspace()
print(u"template sigs: %d / target ents: %d" % (len(tsigs), len(msp)))
print(u"template types:", Counter(e.dxftype() for e in tdoc.modelspace()).most_common())

sigs = []
for e in msp:
    s = fe.entity_signature(e, doc)
    if s is not None:
        sigs.append((e, s))

matched = [x for x in sigs if x[1] in tsigs]
print(u"matched:", len(matched), Counter(e.dxftype() for e, _ in matched).most_common())

# LINE だけ座標集合で突き合わせる
tlines = set()
for e in tdoc.modelspace():
    if e.dxftype() == "LINE":
        tlines.add((fe._pt(e.dxf.start), fe._pt(e.dxf.end)))
dlines = set()
for e in msp:
    if e.dxftype() == "LINE":
        dlines.add((fe._pt(e.dxf.start), fe._pt(e.dxf.end)))
print(u"LINE 幾何だけの一致:", len(tlines & dlines), u"/ tpl", len(tlines), u"/ dxf", len(dlines))

# 同じ座標のLINEで属性が違うものを列挙
tmap = {}
for e in tdoc.modelspace():
    if e.dxftype() == "LINE":
        tmap[(fe._pt(e.dxf.start), fe._pt(e.dxf.end))] = e
n = 0
for e in msp:
    if e.dxftype() != "LINE":
        continue
    k = (fe._pt(e.dxf.start), fe._pt(e.dxf.end))
    t = tmap.get(k)
    if t is None:
        continue
    a = (e.dxf.layer, e.dxf.color, e.dxf.linetype)
    b = (t.dxf.layer, t.dxf.color, t.dxf.linetype)
    if a != b and n < 10:
        print(u"  差異 %r  dxf=%r tpl=%r" % (k, a, b))
        n += 1
print(u"属性差異のあるLINE:", n)

# テンプレのLINE座標が全く出てこない場合はオフセットを推定する
if not (tlines & dlines):
    import collections
    off = collections.Counter()
    dl = list(dlines)[:400]
    for (ts, te) in list(tlines)[:200]:
        for (ds, de) in dl:
            if abs((te[0] - ts[0]) - (de[0] - ds[0])) < 1e-3 and \
               abs((te[1] - ts[1]) - (de[1] - ds[1])) < 1e-3:
                off[(round(ds[0] - ts[0], 3), round(ds[1] - ts[1], 3))] += 1
    print(u"推定オフセット上位:", off.most_common(5))
