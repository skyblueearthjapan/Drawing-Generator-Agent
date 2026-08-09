# -*- coding: utf-8 -*-
"""SolidWorks API 共通ヘルパー (実証済みパターン集約)"""
import os
import math
import win32com.client
import pythoncom

NULL = win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)
# 納品先。Z2 など別マシンでも動くようにリポジトリ相対で解決する(env で上書き可)
OUT_DIR = os.environ.get("SOLIDIFY_OUT3D_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "生成3D"
)


def connect():
    # 版(SW2023=.31 / Z2のSW2021=.29)は sw_compat が自動判別する
    from sw_compat import connect_sw
    return connect_sw()


def new_part(sw):
    tmpl = sw.GetUserPreferenceStringValue(8)
    doc = sw.NewDocument(tmpl, 0, 0, 0)
    assert doc, "NewDocument failed"
    return doc


def sel_plane(doc, name="正面"):
    ok = doc.Extension.SelectByID2(name, "PLANE", 0, 0, 0, False, 0, NULL, 0)
    assert ok, f"plane {name} not selected"
    return ok


def open_sketch(doc, plane="正面"):
    """スケッチを確実に開く (InsertSketchはトグルなので、開きっぱなしならまず閉じる)"""
    skm = doc.SketchManager
    if skm.ActiveSketch is not None:
        skm.InsertSketch(True)
    doc.ClearSelection2(True)
    sel_plane(doc, plane)
    skm.InsertSketch(True)
    assert skm.ActiveSketch is not None, "sketch did not open"
    # スナップ/推測拘束を無効化して直接DBに追加 (これをしないと既存形状近傍の作図が失敗する)
    skm.AddToDB = True
    skm.DisplayWhenAdded = False


def close_sketch_db(doc):
    skm = doc.SketchManager
    skm.AddToDB = False
    skm.DisplayWhenAdded = True


def sel_face(doc, x, y, z):
    doc.ClearSelection2(True)
    return doc.Extension.SelectByID2("", "FACE", x, y, z, False, 0, NULL, 0)


def extrude_blind(doc, depth_m, flip=False):
    """アクティブスケッチをブラインド押し出し"""
    close_sketch_db(doc)
    f = doc.FeatureManager.FeatureExtrusion2(
        True, flip, False, 0, 0, depth_m, 0.0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, True, True, True, 0, 0.0, False)
    assert f, "extrusion failed"
    return f


def cut_through_all_both(doc):
    """アクティブスケッチで両方向貫通カット (SW2023: 26引数)"""
    close_sketch_db(doc)
    f = doc.FeatureManager.FeatureCut3(
        False, False, False, 1, 1, 0.0, 0.0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, False,
        True, True, False, False, False,
        0, 0.0, False)
    assert f, "cut failed"
    return f


def cut_blind(doc, depth_m, draft_deg=0.0, flip_side=False, dir_flip=False):
    """アクティブスケッチをブラインドカット (ドラフト付き可: 皿ザグリ用)"""
    close_sketch_db(doc)
    f = doc.FeatureManager.FeatureCut3(
        True, flip_side, dir_flip, 0, 0, depth_m, 0.0,
        draft_deg > 0, False, False, False, math.radians(draft_deg), 0.0,
        False, False, False, False, False,
        True, True, False, False, False,
        0, 0.0, False)
    assert f, "cut failed"
    return f


def cut_blind_try(doc, depth_m, expected_delta_mm3, redraw, tol=500.0):
    """ブラインドカットを方向違いで試行。redraw()でスケッチを再作成して両方向試す"""
    v0 = volume_mm3(doc)
    for dir_flip in (False, True):
        skm = doc.SketchManager
        if skm.ActiveSketch is None:
            open_sketch_last = None  # 呼び出し側がスケッチを開いておく初回のみ
        close_sketch_db(doc)
        f = doc.FeatureManager.FeatureCut3(
            True, False, dir_flip, 0, 0, depth_m, 0.0,
            False, False, False, False, 0.0, 0.0,
            False, False, False, False, False,
            True, True, False, False, False,
            0, 0.0, False)
        if f is not None:
            delta = v0 - volume_mm3(doc)
            if abs(delta - expected_delta_mm3) <= tol:
                return f
            # 削れ方が想定外 -> 削除してやり直し
            doc.Extension.SelectByID2(f.Name, "BODYFEATURE", 0, 0, 0, False, 0, NULL, 0)
            doc.EditDelete()
        if not dir_flip:
            redraw()
    raise AssertionError("cut_blind_try failed both directions (expected %.0f)" % expected_delta_mm3)


def volume_mm3(doc):
    try:
        mp = doc.Extension.CreateMassProperty()
    except Exception:
        mp = doc.Extension.CreateMassProperty
    return mp.Volume * 1e9


def save_and_shot(doc, name):
    """生成3Dフォルダへ SLDPRT + PNG 保存。PNG変換はPILがあれば使用、なければBMPのまま"""
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, name + ".SLDPRT")
    bmp = os.path.join(OUT_DIR, name + ".bmp")
    doc.ShowNamedView2("*等角投影", 7)
    doc.ViewZoomtofit2()
    errs = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warns = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    ok = doc.Extension.SaveAs3(out, 0, 2, NULL, NULL, errs, warns)
    assert ok and errs.value == 0, f"SaveAs3 failed errs={errs.value}"
    doc.SaveBMP(bmp, 1200, 900)
    png = os.path.join(OUT_DIR, name + ".png")
    try:
        from PIL import Image
        Image.open(bmp).save(png)
        os.remove(bmp)
    except ImportError:
        png = bmp
    return out, png


def rounded_rect_sketch(doc, w, h, r, cx=0.0, cy=0.0):
    """角R付き長方形 (原点=左下基準, 単位m)。アクティブスケッチに描く"""
    skm = doc.SketchManager
    x0, y0, x1, y1 = cx, cy, cx + w, cy + h
    assert skm.CreateLine(x0 + r, y0, 0, x1 - r, y0, 0)
    assert skm.CreateLine(x1, y0 + r, 0, x1, y1 - r, 0)
    assert skm.CreateLine(x1 - r, y1, 0, x0 + r, y1, 0)
    assert skm.CreateLine(x0, y1 - r, 0, x0, y0 + r, 0)
    # CreateArc(cx,cy,cz, sx,sy,sz, ex,ey,ez, dir) dir=+1:CCW
    assert skm.CreateArc(x1 - r, y0 + r, 0, x1 - r, y0, 0, x1, y0 + r, 0, 1)
    assert skm.CreateArc(x1 - r, y1 - r, 0, x1, y1 - r, 0, x1 - r, y1, 0, 1)
    assert skm.CreateArc(x0 + r, y1 - r, 0, x0 + r, y1, 0, x0, y1 - r, 0, 1)
    assert skm.CreateArc(x0 + r, y0 + r, 0, x0, y0 + r, 0, x0 + r, y0, 0, 1)


def slot_sketch(doc, x0, y0, x1, y1, r):
    """直線スロット (中心線 (x0,y0)-(x1,y1)、半径r)。CreateSketchSlot使用(手描き輪郭はカット失敗する)"""
    slot = doc.SketchManager.CreateSketchSlot(
        0, 1, r * 2, x0, y0, 0.0, x1, y1, 0.0, 0.0, 0.0, 0.0, 1, False)
    assert slot, "slot creation failed"
    return slot
