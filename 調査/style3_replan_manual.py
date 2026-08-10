# -*- coding: utf-8 -*-
u"""様式第3弾の計画改訂のうち、**規則では自動化できない部分**(AIオペレータの判断)を適用する。

`調査/style3_replan.py`(辺の付け替え・段の振り直し・auto_place)で機械化できるのはここまでで、
以下は部品ごとの判断が要るため個別に書く。1回だけ適用する冪等スクリプト。

  M1 25154-1-27 走行フレーム踏板(論点8・11)
     - 生pattern の穴注記2本を `spec` へ構造化 -> 呼び値翻訳が効く
       (φ7.04 -> 呼びφ7 / 90°皿もみφ13.44 は丸め先が自明でないので**未確定フラグ**)
     - Z298(全スパン1本)を **中心の六角穴列(z=0)から±149 へ振り分け**(146+146 型)。
       測定点は実在する穴中心(六角穴 z=0 / 皿穴 z=±149)なのでゲート①のsnapを満たす。
  M2 25154-2-16 指針(レイアウト)
     - 改訂で下側が3段になり Z88_total が表題欄へ食い込んだ。上側(平面図との間)へ移す。
  M3 25154-6-02 傾動面板(論点14)
     - 円形ビュー(front)の径3本 -> **外径φ420の1本だけ**を残し、
       φ360(インロー)・φ75(中央穴)は輪郭ビュー(right)へ線形+%%c で移す。
  M4 25154-5-05 減速機フランジ(論点14)
     - 円形ビュー(top)の径3本 -> φ219.1(外径)だけ残し、φ200・φ190 は輪郭ビュー(front)へ。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INBOX = os.path.join(ROOT, u"data", u"依頼箱")


def _read(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(p, d):
    with io.open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2))
        f.write(u"\n")


def _dim(plan, did):
    for d in plan["dimensions"]:
        if d["id"] == did:
            return d
    return None


def _note(plan, nid):
    for n in plan["hole_notes"]:
        if n["id"] == nid:
            return n
    return None


def m1_1_27():
    p = os.path.join(INBOX, u"BLIND2-25154-1-27", "plan.json")
    plan = _read(p)
    ch = []

    n = _note(plan, "N_csk")
    if n and n.get("pattern"):
        n.pop("pattern")
        n["spec"] = {"count": 6, "drill": 7.04,
                     "countersink": {"angle": 90, "dia": 13.44}}
        n["comment"] = (u"皿もみ穴6個。**実測値をそのまま spec に書き、呼び値への翻訳は "
                        u"engine/nominal_size.py に任せる**(作法16)。"
                        u"φ7.04 -> 呼びφ7(差0.04 ≦ 窓0.05)。"
                        u"90°皿もみのφ13.44 は許容窓内に呼び値が無いので**丸めず**"
                        u"「呼び値未確定」として質問票へ回る"
                        u"(ユーザー裁定『事実に基づかない補完はしない』)。")
        ch.append("N_csk -> spec")

    n = _note(plan, "N_hex")
    if n and n.get("pattern"):
        n.pop("pattern")
        n["spec"] = {"count": 2, "drill": 14,
                     "extra_lines": [u"裏面六角座　二面幅19", u"%%c12通し"]}
        n["comment"] = (u"板側φ14通し+裏面に二面幅19の六角座(φ12通し)。"
                        u"φ12は呼び径ちょうどでМ12溶接ナットの可能性が高いが、"
                        u"運用ルール『М判定は呼び径±0.1一致 かつ 3個以上が円周等配』を"
                        u"満たさない(2個・非等配)ためφ表記のままとした。"
                        u"採らなかった解釈=М12ナット溶接。")
        ch.append("N_hex -> spec")

    # --- 論点11: 対称は中心から振り分ける ---
    if _dim(plan, "Z298_csk") is not None:
        idx = [i for i, d in enumerate(plan["dimensions"]) if d["id"] == "Z298_csk"][0]
        base = plan["dimensions"][idx]
        pl = dict(base.get("placement") or {})
        common = {"kind": "linear", "view": "front", "tolerance": None}
        half_a = dict(common, id="Z149_csk_minus", value_expected=149.0,
                      measure={"space": "model",
                               "p1": [250, 0, 0], "p2": [466, 0, -149],
                               "direction": "vertical"},
                      placement={"side": pl.get("side", "left"),
                                 "chain_group": "Zsym", "level": pl.get("level", 1)},
                      comment=(u"論点11: 板の対称穴列は全スパン1本(298)でなく"
                               u"**中心から±で振り分ける**(146+146型)。"
                               u"中心側の測定点は六角穴の中心(z=0)、外側は皿穴の中心(z=-149)で"
                               u"どちらも実在フィーチャーなのでゲート①のsnapを満たす。"))
        half_b = dict(common, id="Z149_csk_plus", value_expected=149.0,
                      measure={"space": "model",
                               "p1": [250, 0, 0], "p2": [466, 0, 149],
                               "direction": "vertical"},
                      placement={"side": pl.get("side", "left"),
                                 "chain_group": "Zsym", "level": pl.get("level", 1)},
                      comment=u"同上(中心から+側)。")
        plan["dimensions"][idx:idx + 1] = [half_a, half_b]
        ch.append("Z298_csk -> Z149x2(chain Zsym)")
    if ch:
        _write(p, plan)
    print(u"M1 25154-1-27: %s" % (ch or u"変更なし"))


def m2_2_16():
    p = os.path.join(INBOX, u"BLIND2-25154-2-16", "plan.json")
    plan = _read(p)
    d = _dim(plan, "Z88_total")
    ch = []
    if d and (d.get("placement") or {}).get("side") != "above":
        d["placement"] = {"side": "above", "level": 2, "side_fixed": True}
        d["comment"] = ((d.get("comment") or u"") +
                        u" / 様式第3弾: 下側は連記(Z43|Z45)で1段使うため、全長は上側へ回す"
                        u"(下側3段だと表題欄へ食い込む)。")
        ch.append("Z88_total below -> above(side_fixed)")
        _write(p, plan)
    print(u"M2 25154-2-16: %s" % (ch or u"変更なし"))


def _to_profile_diameter(plan, did, view, p1, p2, side, level, comment):
    d = _dim(plan, did)
    if d is None or d["view"] == view:
        return None
    d["view"] = view
    d["context"] = "profile_view"
    d["measure"] = {"space": "model", "p1": p1, "p2": p2, "direction": "vertical"}
    d["placement"] = {"side": side, "level": level}
    d["comment"] = comment
    return did


def m3_6_02():
    p = os.path.join(INBOX, u"BLIND-25154-6-02", "plan.json")
    plan = _read(p)
    c = (u"論点14: 円形ビューの径は外径1本まで。"
         u"インロー/中央穴の径は輪郭ビュー(right)へ線形+%%cで移した。")
    ch = [x for x in (
        _to_profile_diameter(plan, "D360", "right", [0, -180, 2], [0, 180, 2],
                             "left", 1, c),
        _to_profile_diameter(plan, "D75", "right", [0, -37.5, 16], [0, 37.5, 16],
                             "right", 1, c),
    ) if x]
    if ch:
        _write(p, plan)
    print(u"M3 25154-6-02: %s" % (ch or u"変更なし"))


def m4_5_05():
    p = os.path.join(INBOX, u"BLIND2-25154-5-05", "plan.json")
    plan = _read(p)
    for d in plan["dimensions"]:
        print(u"   (参考) %s %s %s %s" % (d["id"], d["kind"], d.get("context"), d["view"]))
    print(u"M4 25154-5-05: 手当は下の個別編集で行う")


if __name__ == "__main__":
    m1_1_27()
    m2_2_16()
    m3_6_02()
