# -*- coding: utf-8 -*-
u"""開いているドキュメントの列挙と、自分が開いた分だけの後始末。

❗`IModelDoc2.GetNext` は gen_py ではプロパティになる(`d.GetNext()` は
  'NoneType' object is not callable で落ちる)。必ず prop() 経由で読む。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))
import draw_pipeline as dp  # noqa: E402


def docs(sw):
    u"""[(title, doctype)] を返す。doctype: 1=部品 2=アセンブリ 3=図面。

    ❗`GetFirstDocument` + `GetNext` の連鎖は当機では**1件目で None になり全件列挙できない**
      (図面+部品の2件開いている状態で部品1件しか返らないのを実測)。
      **`ISldWorks.GetDocuments()` を使うこと**。
    """
    out = []
    try:
        arr = sw.GetDocuments()
    except Exception:
        arr = None
    if arr:
        for d in list(arr):
            try:
                out.append((dp.prop(d, "GetTitle"), dp.prop(d, "GetType")))
            except Exception:
                pass
    return out


def titles(sw):
    return [t for t, _ in docs(sw)]


def close_extras(sw, pre_titles, verbose=True):
    u"""pre_titles に無いタイトルのドキュメントだけ閉じる。

    ❗図面が部品を参照している間は部品を閉じられない(CloseDoc が黙って効かない)。
      **図面(type=3)を先に閉じる**。
    """
    closed = []
    cur = docs(sw)
    order = [d for d in cur if d[1] == 3] + [d for d in cur if d[1] != 3]
    for t, _ty in order:
        if t not in pre_titles:
            sw.CloseDoc(t)
            closed.append(t)
            if verbose:
                print(u"  CloseDoc(%r)" % t)
    return closed, titles(sw)
