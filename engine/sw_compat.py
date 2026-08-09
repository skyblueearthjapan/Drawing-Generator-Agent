# -*- coding: utf-8 -*-
u"""SolidWorks のバージョン差(ProgID / gen_py の版)を1箇所で吸収する(2026-07-29)。

開発機(現行PC) : SolidWorks 2023 → ProgID `SldWorks.Application.31` / gen_py (0,31,0)
Z2(常時稼働機) : SolidWorks 2021 → ProgID `SldWorks.Application.29` / gen_py (0,29,0)

決め打ちの `.31` がコード中に散っていると Z2 で全滅するので、
接続と gen_py の取得は必ずこのモジュールを通すこと。

- 環境変数 `SOLIDIFY_SW_PROGID`(例 "SldWorks.Application.29")があればそれだけを使う
- 無ければ候補(2023→2021→2022→2024→2020)を順に試す。**無印 ProgID は試さない**
  (このPCで失敗が実証済み — CLAUDE.md)
- 一度掴めた版はプロセス内で記憶し、以後の gen_py も同じ版で包む

⚠ SolidWorks のファイル互換は**後方のみ**。2023 で保存した SLDPRT は 2021 では開けない。
  既存の `生成3D/` 資産を Z2 で開き直す用途には使えない(STEP は開ける)。
"""
import os

GEN_PY_GUID = "{83A33D31-27C5-11CE-BFD4-00400513BB57}"

#: ProgID 末尾の版番号。実績のある版(2023)→ Z2 の版(2021)→ 近傍の順
CANDIDATE_VERS = (31, 29, 30, 32, 28)

_detected_ver = None


def _ver_of(progid):
    try:
        return int(str(progid).rsplit(".", 1)[-1])
    except (ValueError, TypeError):
        return None


def progid_candidates():
    env = os.environ.get("SOLIDIFY_SW_PROGID", "").strip()
    if env:
        return (env,)
    return tuple("SldWorks.Application.%d" % v for v in CANDIDATE_VERS)


def connect_sw():
    u"""起動済みの SolidWorks に接続する(GetActiveObject。起動はしない)。

    掴めた版を記憶する。全候補で失敗したら最後の例外をそのまま上げる
    (呼び出し側の従来の except に同じ形で落ちる)。
    """
    global _detected_ver
    import win32com.client

    last_exc = None
    for progid in progid_candidates():
        try:
            sw = win32com.client.GetActiveObject(progid)
        except Exception as exc:  # noqa: BLE001 - COM は多様な例外を投げる
            last_exc = exc
            continue
        ver = _ver_of(progid)
        if ver:
            _detected_ver = ver
        return sw
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(u"SolidWorks の ProgID 候補がありません")


def detected_progid():
    u"""接続に成功した ProgID(まだ無ければ "")。ログ表示用。"""
    if _detected_ver:
        return "SldWorks.Application.%d" % _detected_ver
    return ""


def gen_module():
    u"""makepy 済みタイプライブラリ(IBody2/IFace2/ISurface/IPartDoc 等)を返す。

    接続済みならその版で、未接続なら候補を順に試す。
    Z2 の初期設定では sldworks.tlb / swconst.tlb の makepy を済ませておくこと。
    """
    global _detected_ver
    from win32com.client import gencache

    vers = [_detected_ver] if _detected_ver else [
        _ver_of(p) for p in progid_candidates()
    ]
    last_exc = None
    for ver in vers:
        if not ver:
            continue
        try:
            mod = gencache.EnsureModule(GEN_PY_GUID, 0, ver, 0)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
        if mod is not None:
            _detected_ver = ver
            return mod
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(u"sldworks タイプライブラリの gen_py が見つかりません(makepy 未実施?)")
