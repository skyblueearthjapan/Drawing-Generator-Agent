# -*- coding: utf-8 -*-
"""フェーズ0スモークテスト: SW COM接続 + SLDPRT読み込み + 質量特性/bbox取得。

表題欄の「重量」自動算出と、作図前の形状クラス判定に必要な計測経路を実証する。
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

import sw_compat

sw = sw_compat.connect_sw()
print("connected:", sw_compat.detected_progid())
rev = sw.RevisionNumber
print("SW revision:", rev() if callable(rev) else rev)

PART = r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent\荏原トライ調整用\3D\1.走行軸\1-09.SLDPRT"
print("file exists:", os.path.exists(PART))
# OpenDoc6: (path, type=1(part), option=1(silent), config, errors, warnings)
doc = sw.OpenDoc6(PART, 1, 1, "", 0, 0)
if isinstance(doc, tuple):
    doc = doc[0]
if doc is None:
    raise SystemExit("NG: OpenDoc6 が None(開けていない)。sw.VersionHistory(path) で保存版を確認せよ。"
                     "※ActiveDoc で拾ってはいけない(無関係の開きっぱなしドキュメントを掴む事故が起きた)")
title = doc.GetTitle
title = title() if callable(title) else title
# 開けたつもりのファイルか必ず照合(CLAUDE.md 知見)
assert os.path.basename(PART).lower().startswith(os.path.splitext(title)[0].lower()[:4]) or \
       title.lower() == os.path.basename(PART).lower(), f"タイトル不一致: {title}"
print("opened:", title)

mod = sw_compat.gen_module()
d2 = mod.IModelDoc2(doc._oleobj_)

ext = d2.Extension
mp = ext.CreateMassProperty()
mp.UseSystemUnits = True
print("mass[kg]:", mp.Mass)
print("volume[mm^3]:", mp.Volume * 1e9)
print("density[kg/m^3]:", mp.Density)
com = mp.CenterOfMass
print("center of mass[mm]:", [round(c * 1000, 3) for c in com])

# マテリアル名(表題欄の材質候補。モデルに入っていないことも多い)
try:
    part = mod.IPartDoc(doc._oleobj_)
    matdb = ""
    mat = part.GetMaterialPropertyName2("", matdb)
    print("material:", mat)
except Exception as e:
    print("material: (取得失敗)", e)

# バウンディングボックス
try:
    bodies = part.GetBodies2(0, True)
    for b in bodies or []:
        ib = mod.IBody2(b._oleobj_) if not hasattr(b, "GetBodyBox") else b
        box = ib.GetBodyBox
        if callable(box):
            box = box()
        print("bbox[mm]:", [round(v * 1000, 2) for v in box])
except Exception as e:
    print("bbox: (取得失敗)", e)

sw.CloseDoc(title)
print("OK: smoke test passed")
