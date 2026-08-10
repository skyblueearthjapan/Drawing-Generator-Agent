# -*- coding: utf-8 -*-
u"""正面ビュー候補セット(決定論部品・純粋データ)。

CLAUDE.md「正面ビュー選定の確定戦略」(2026-08-10 ディレクター裁定・60部品の実測)に基づく
事前分布順の上位6候補。`調査/phase5_ai_operator/cand_1-18.json`(全体・非旋盤物)と
`cand_3-05.json`(旋盤物)をそのまま定数化したもの(スクリプトは調査/を一切変更しない)。

- 全体(非旋盤物、plate/holed_block/other 共通): 上位6候補で人間選択の90%を被覆
- 旋盤物(lathe): 「*正面+0」は33%しか当たらず順位が入れ替わる(CLAUDE.md 知見)ため専用セット

`workshop.py` はこの2セットから `shape_class` に応じて1つを選び、
`project_candidates.py` へそのまま渡す(id/sw_view/rot/noteのリスト)。
"""

CANDIDATES_LATHE = [
    {"id": "C1", "sw_view": u"*正面", "rot": 0, "note": u"旋盤物1位(33%)"},
    {"id": "C2", "sw_view": u"*正面", "rot": 270, "note": u"旋盤物2位"},
    {"id": "C3", "sw_view": u"*右側面", "rot": 0, "note": u"旋盤物3位"},
    {"id": "C4", "sw_view": u"*平面", "rot": 0, "note": u"全体3位"},
    {"id": "C5", "sw_view": u"*正面", "rot": 90, "note": u"全体8位"},
    {"id": "C6", "sw_view": u"*右側面", "rot": 270, "note": u"全体候補"},
]

CANDIDATES_DEFAULT = [
    {"id": "C1", "sw_view": u"*正面", "rot": 0, "note": u"全体事前分布1位(56%)"},
    {"id": "C2", "sw_view": u"*右側面", "rot": 0, "note": u"同2位(累積72%)"},
    {"id": "C3", "sw_view": u"*平面", "rot": 0, "note": u"同3位(累積78%)"},
    {"id": "C4", "sw_view": u"*平面", "rot": 270, "note": u"同4位(累積82%)"},
    {"id": "C5", "sw_view": u"*正面", "rot": 270, "note": u"同5位(累積86%)"},
    {"id": "C6", "sw_view": u"*平面", "rot": 90, "note": u"同6位(累積90%)"},
]


def candidates_for(shape_class):
    u"""形状クラス(view_orient.classify の shape_class)に応じた候補リストを返す。"""
    if shape_class == "lathe":
        return CANDIDATES_LATHE
    return CANDIDATES_DEFAULT
