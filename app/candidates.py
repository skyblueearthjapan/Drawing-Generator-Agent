# -*- coding: utf-8 -*-
u"""正面ビュー候補セット(決定論部品・純粋データ+並べ替え規則)。

CLAUDE.md「正面ビュー選定の確定戦略」に基づき、**事前分布順の上位N候補**を投影して
AIオペレータが目視で1つ選ぶ。ここはその候補集合と順位だけを持つ。

―― 2026-08-10 改訂(盲検10部品の実測を反映)――――――――――――――――――――
盲検(調査/blind_test_report.md §2.1)で **上位6候補の被覆率が 90% → 70%** に落ちた。
落ちた3件の原因は候補集合の**構造的な穴**だった:

  * `+180` 回転が **どの形状クラスの候補にも無い**(円板の裏表を原理的に当てられない)  … 3-09
  * `*左側面` が候補に無い                                                        … 5-04
  * 旋盤物の候補が「円が見えるビュー」に偏っていた                                 … 2-06

対策(本ファイルの変更点):
  1. 事前分布を **60点(フェーズ4の50点 + 盲検10点)** で引き直した
     (`調査/recompute_front_prior.py`。生データ `調査/front_prior_60.json`)
  2. 候補数を **6 → 8** に増やし、`+180` と `*左側面` を全クラスの候補集合へ入れた
  3. **旋盤物は主軸(`view_orient.classify` の main_axis)で候補を並べ替える**。
     60点中13点の旋盤物で、人間が選んだ正面において主軸が紙面上どう見えていたかの実測は
     **水平 8/13(61.5%) / 円形(主軸=紙面法線)4/13(30.8%) / 垂直 1/13(7.7%)**。
     → 「旋盤物は例外なく主軸を紙面水平」(盲検の教訓B)は **過大な一般化**で、
       円形ビューを正面にする例も 4件ある。**1位から外すが候補からは外さない**のが実測に合う。
     実装は比率マージ(水平:円形:垂直 = 8:4:1)なので、円形は3番目に必ず現れる。

`workshop.py` は `candidates_for(shape_class, main_axis)` を呼び、
`project_candidates.py` へそのまま渡す(id/sw_view/rot/note のリスト)。
"""

# CLAUDE.md「SW標準6ビューの軸マップ(実測)」: (paper_x, paper_y, 紙面手前)
SW_AXIS_MAP = {
    u"*正面":   ("+X", "+Y", "+Z"),
    u"*背面":   ("-X", "+Y", "-Z"),
    u"*左側面": ("+Z", "+Y", "-X"),
    u"*右側面": ("-Z", "+Y", "+X"),
    u"*平面":   ("+X", "-Z", "+Y"),
    u"*底面":   ("+X", "+Z", "-Y"),
}

# ---------------------------------------------------------------------------
# 全体(非旋盤物)の事前分布順。60点実測(調査/front_prior_60.json の overall)。
# 8位だけは実測順位ではなく **構造的な穴の穴埋め**: `+180` はどの回転族にも無かったので
# 一番出やすい `*平面` 系に 180 を足して4回転を揃える(盲検3-09型=円板の裏表を救う)。
# ---------------------------------------------------------------------------
CANDIDATES_DEFAULT = [
    {"id": "C1", "sw_view": u"*正面", "rot": 0, "note": u"事前分布1位 53.3%(累積53.3%)"},
    {"id": "C2", "sw_view": u"*右側面", "rot": 0, "note": u"2位 15.0%(累積68.3%)"},
    {"id": "C3", "sw_view": u"*平面", "rot": 0, "note": u"3位 6.7%(累積75.0%)"},
    {"id": "C4", "sw_view": u"*平面", "rot": 270, "note": u"4位 6.7%(累積81.7%)"},
    {"id": "C5", "sw_view": u"*正面", "rot": 270, "note": u"5位 3.3%(累積85.0%)"},
    {"id": "C6", "sw_view": u"*平面", "rot": 90, "note": u"6位 3.3%(累積88.3%)"},
    {"id": "C7", "sw_view": u"*左側面", "rot": 0, "note": u"7位 3.3%(累積91.7%)・鏡像側"},
    {"id": "C8", "sw_view": u"*平面", "rot": 180, "note": u"+180の穴埋め(円板の裏表)"},
]

# 旋盤物の候補ユニバース。順序は **旋盤物13点の実測頻度** → 全体事前分布 の順。
# 主軸が判っている場合はこの順序を「主軸の見え方」で並べ替える(下記 candidates_for)。
LATHE_UNIVERSE = [
    (u"*正面", 0, u"旋盤13点で4件"), (u"*正面", 270, u"同2件"),
    (u"*右側面", 0, u"同2件"), (u"*平面", 270, u"同2件"),
    (u"*右側面", 90, u"同1件"), (u"*平面", 180, u"同1件"),
    (u"*左側面", 180, u"同1件"),
    (u"*平面", 0, u"全体3位"), (u"*平面", 90, u"全体6位"),
    (u"*左側面", 0, u"全体7位"), (u"*正面", 90, u"全体8位"),
    (u"*右側面", 180, u"全体10位"),
]

# 旋盤物で人間が選んだ正面における主軸の見え方の実測比(60点中13点)
LATHE_ROLE_WEIGHT = {"horizontal": 8, "circular": 4, "vertical": 1}

N_CANDIDATES = 8


def axis_role(sw_view, rot, main_axis):
    u"""主軸 main_axis('X'/'Y'/'Z')が、そのビュー・紙面内回転で紙面上どう見えるか。

    Returns: "horizontal" | "vertical" | "circular"(主軸が紙面法線=円が見えるビュー)
    """
    px, py, nz = (a[1] for a in SW_AXIS_MAP[sw_view])
    if main_axis == nz:
        return "circular"
    if main_axis == px:
        role = "horizontal"
    elif main_axis == py:
        role = "vertical"
    else:
        return "circular"
    if int(rot) % 180 == 90:
        role = "vertical" if role == "horizontal" else "horizontal"
    return role


def _ratio_merge(buckets, weights):
    u"""比率マージ(Sainte-Laguë風)。実測比 weights の割合で各リストから順に取り出す。

    「水平を1位にするが円形も3番目には出す」を**実測比だけで**決めるための決定論規則。
    ハードな段組み(水平を全部並べてから円形)にすると、実測 4/13 ある円形ビューが
    8候補に入らなくなるため採らない。
    """
    taken = {k: 0 for k in buckets}
    out = []
    while any(taken[k] < len(buckets[k]) for k in buckets):
        cand = [k for k in buckets if taken[k] < len(buckets[k])]
        # (取得数+1)/重み が最小のリストから取る。同点は重みの大きい方(=実測で多い方)
        k = min(cand, key=lambda k_: ((taken[k_] + 1.0) / weights[k_], -weights[k_], k_))
        out.append(buckets[k][taken[k]])
        taken[k] += 1
    return out


def candidates_for(shape_class, main_axis=None):
    u"""形状クラス(+旋盤物は主軸)に応じた候補リストを返す(上位 N_CANDIDATES 件)。

    shape_class: view_orient.classify の shape_class(lathe / plate / holed_block / other)
    main_axis  : 同 main_axis('X'/'Y'/'Z')。旋盤物の並べ替えに使う。None なら実測頻度順。
    """
    if shape_class != "lathe":
        return [dict(c) for c in CANDIDATES_DEFAULT[:N_CANDIDATES]]

    if main_axis in ("X", "Y", "Z"):
        buckets = {"horizontal": [], "circular": [], "vertical": []}
        for v, r, note in LATHE_UNIVERSE:
            buckets[axis_role(v, r, main_axis)].append((v, r, note))
        merged = _ratio_merge(buckets, LATHE_ROLE_WEIGHT)
        rolemap = {(v, r): axis_role(v, r, main_axis) for v, r, _n in LATHE_UNIVERSE}
    else:
        merged = list(LATHE_UNIVERSE)
        rolemap = {}

    out = []
    for i, (v, r, note) in enumerate(merged[:N_CANDIDATES], start=1):
        role = rolemap.get((v, r))
        out.append({"id": "C%d" % i, "sw_view": v, "rot": r,
                    "note": (u"旋盤物%d位(主軸%s=%s / %s)" % (i, main_axis, role, note))
                            if role else (u"旋盤物%d位(%s)" % (i, note))})
    return out
