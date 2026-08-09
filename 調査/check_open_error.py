# -*- coding: utf-8 -*-
"""1-09.SLDPRT が OpenDoc6 で開けない原因調査。

1) ISldWorks.VersionHistory(path) で保存版を確認(開かずに済む)
2) GetOpenDocSpec + OpenDoc7 でエラーコードを取得
swFileLoadError_e: 1=NotFound? 2=GenericError, 4=FutureVersion(新しい版で保存済み) など
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))
import sw_compat

sw = sw_compat.connect_sw()
PART = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent\荏原トライ調整用\3D\1.走行軸\1-09.SLDPRT"

vh = sw.VersionHistory(PART)
print("VersionHistory:", vh)

spec = sw.GetOpenDocSpec(PART)
spec.Silent = True
spec.ReadOnly = True
doc = sw.OpenDoc7(spec)
if isinstance(doc, tuple):
    doc = doc[0]
print("OpenDoc7 doc:", doc)
err = spec.Error
warn = spec.Warning
print("spec.Error:", err() if callable(err) else err)
print("spec.Warning:", warn() if callable(warn) else warn)

if doc is not None:
    title = doc.GetTitle
    title = title() if callable(title) else title
    print("opened:", title)
    sw.CloseDoc(title)
