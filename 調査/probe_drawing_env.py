# -*- coding: utf-8 -*-
u"""フェーズ2 事前調査: 図面テンプレートの所在と、開発用SLDPRTの保存版を確認する。

ドキュメントは一切開かない(VersionHistory は開かずに版を返す)。
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

import sw_compat

sw = sw_compat.connect_sw()
print("connected:", sw_compat.detected_progid())

# --- ユーザープリファレンス文字列(テンプレートパス) ---
# swDefaultTemplatePart=8 / Assembly=9 / Drawing=10 が定石。周辺も舐める
for i in range(0, 30):
    try:
        v = sw.GetUserPreferenceStringValue(i)
    except Exception as e:
        v = "<err %s>" % e
    if v:
        print("UserPrefString[%2d] = %s" % (i, v))

print("--- テンプレートフォルダ ---")
# swFileLocationsDocumentTemplates = 8
for idx in (8, 9, 10):
    try:
        s = sw.GetUserPreferenceStringListValue(idx)
        print("StringList[%d] = %r" % (idx, s))
    except Exception as e:
        print("StringList[%d] err %s" % (idx, e))

print("--- 開発用SLDPRT の保存版 ---")
BASE = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\3D CAD Operator Agent\生成3D"
for name in ("15015-P3-012_端子棒.SLDPRT",
             "15015-P3-013_ホルダー.SLDPRT",
             "22129-P1-06_カラー.SLDPRT",
             "15015-P3-008_回転指針.SLDPRT"):
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        print(name, "-> 存在しない")
        continue
    try:
        vh = sw.VersionHistory(p)
        print(name, "->", vh)
    except Exception as e:
        print(name, "-> VersionHistory err", e)
