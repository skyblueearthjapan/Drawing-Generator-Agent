# -*- coding: utf-8 -*-
u"""フェーズ4 第2弾バッチの対象30部品を決める(決定論・第1弾30点とは重複しない)。

方針(第1弾の教訓 = `調査/phase4_scoreboard.md` §7-3)
  1. **第1弾の30点を除外**する(向き選定ルール v2 は第1弾のデータから作った仮説なので、
     同じ部品で測ったら自己申告にしかならない)
  2. **ギア・ねじ部品を事前に「照合対象外(簡略図示)」へ隔離**する。
     第1弾では30点中5点(17%)が「歯形・ねじ山の簡略図示」のせいで幾何ラスタ照合が
     原理的に成立せず、判定不能になった。名前だけでなく**人間図面の中身**でも判定する
     (歯車図面は必ず `モジュール` `歯数` `圧力角` 等の要目表を持つ)
  3. 残りから **軸バランス**と**形状クラスの多様性**を確保して30点。
     第1弾で最大の外し要因だった**曲げ物ブラケット・ドグ類を厚めに**取る(v2の勝負どころ)

    python 調査/phase4_batch2/pick_targets2.py
"""
import os
import sys
import io
import json
import re
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(ROOT, u"荏原トライ調整用")

N_TOTAL = 30

# ---------------------------------------------------------------- 照合対象外(簡略図示)の判定
#: 品名に出たら「歯形・ねじ山の簡略図示を持つ」と疑うキーワード
#: ❗荏原の品名は**半角カタカナ**混じり(`ﾗｯｸ3-2000`)。全角だけで書くと素通りする
NAME_HINTS = (u"ギア", u"ギヤ", u"ラック", u"ﾗｯｸ", u"ボールネジ", u"ボールねじ",
              u"ﾎﾞｰﾙﾈｼﾞ", u"ウォーム", u"ピニオン", u"スプライン", u"ねじ軸")
#: 上のキーワードを持っていても、歯・ねじを**持たない付属部品**であることが確実な語
NAME_EXEMPT = (u"カバー", u"ｶﾊﾞｰ", u"フタ", u"蓋", u"受け", u"ブラケット", u"ケーブルベア")
#: 人間図面の文字に出たら歯車要目表がある(= 歯形が簡略図示されている)と確定できる語
DXF_TEXT_HINTS = (u"モジュール", u"歯数", u"圧力角", u"転位", u"またぎ歯厚",
                  u"歯先円", u"基準円", u"歯車", u"歯直角", u"ころがり円")
#: 外径ねじの簡略図示を示す図面文字(タップ穴の M5 等とは区別するため「ねじ」語で見る)
DXF_TEXT_THREAD = (u"転造", u"ねじ部", u"おねじ", u"ネジ部")


def dxf_texts(path):
    u"""人間図面の TEXT/MTEXT の中身を素のテキストで返す(ezdxf・読み取りのみ)。"""
    import ezdxf
    try:
        doc = ezdxf.readfile(path)
    except Exception:
        return u""
    out = []
    for e in doc.modelspace():
        t = e.dxftype()
        if t == "TEXT":
            out.append(e.dxf.text or u"")
        elif t == "MTEXT":
            out.append(e.text or u"")
    return u"\n".join(out)


def zen2han(s):
    u"""全角ASCII→半角(注記は全角なので、機械解釈の前に必ず正規化する。CLAUDE.md知見)。"""
    return u"".join(unichr(ord(c) - 0xFEE0) if u"！" <= c <= u"～" else
                    (u" " if c == u"　" else c) for c in s)


try:
    unichr
except NameError:                                   # py3
    unichr = chr


def screen(name, dxf_path):
    u"""(照合対象外か, 理由) を返す。名前 → 図面文字 の順に見る。"""
    hit_name = [k for k in NAME_HINTS if k in name]
    exempt = [k for k in NAME_EXEMPT if k in name]
    txt = zen2han(dxf_texts(dxf_path))
    hit_txt = [k for k in DXF_TEXT_HINTS if k in txt]
    hit_thr = [k for k in DXF_TEXT_THREAD if k in txt]
    if hit_txt:
        return True, u"図面に歯車要目表の語 %r" % (hit_txt,)
    if hit_thr:
        return True, u"図面にねじ簡略図示の語 %r" % (hit_thr,)
    if hit_name and not exempt:
        return True, u"品名に %r(付属部品語なし)" % (hit_name,)
    if hit_name and exempt:
        return False, u"品名に %r があるが %r なので歯・ねじは持たない" % (hit_name, exempt)
    return False, u""


# ---------------------------------------------------------------- 形状クラス(名前からの事前推定)
#: SWで面を見るまで本当のクラスは分からないので、**選定時の多様性確保のためだけ**の粗い推定。
#: 判定順に意味がある(先に当たったものを採る)。
NAME_CLASS = (
    (u"lathe_like",   (u"シャフト", u"カラー", u"端子棒", u"ホルダー", u"フランジ",
                       u"ベアリングケース", u"クッション", u"間座")),
    (u"bend_bracket", (u"ブラケット", u"ドグ", u"ストライカー", u"ベース", u"取付")),
    (u"plate_like",   (u"プレート", u"カバー", u"フタ", u"指針", u"面板", u"踏板",
                       u"ダクト", u"ジャバラ")),
    (u"block_like",   (u"メカストッパー", u"受け", u"押しボルト座", u"補強材",
                       u"ターミナル", u"座")),
)
#: クラスごとの目標点数(第1弾で最大の外し要因だった曲げ物を厚めに)
QUOTA = {"bend_bracket": 10, "plate_like": 8, "lathe_like": 7, "block_like": 5}
#: 巨大なフレーム類は投影・照合が重く時間を溶かすので上限を設ける(KB)
STEP_KB_MAX = 4000
#: 人間DXFの上限(KB)。5-08 メカストッパー受け は 20MB あり照合が現実的な時間で終わらない
DXF_KB_MAX = 2000


def guess_class(name):
    for cls, keys in NAME_CLASS:
        if any(k in name for k in keys):
            return cls
    return "other"


# ---------------------------------------------------------------- main
def main():
    with io.open(os.path.join(ROOT, u"調査", u"教師STEP棚卸し.json"), encoding="utf-8") as f:
        inv = json.load(f)
    with io.open(os.path.join(ROOT, u"調査", u"bucket_A_files.json"), encoding="utf-8") as f:
        bucket = json.load(f)
    with io.open(os.path.join(ROOT, u"調査", "phase4_batch", "targets.json"),
                 encoding="utf-8") as f:
        batch1 = {t["key"] for t in json.load(f)["targets"]}
    a_names = set()
    for _, files in bucket.items():
        for p in files:
            a_names.add(os.path.basename(p))

    rows, excluded = [], []
    for m in inv["matched"]:
        key = m[u"図番キー"]
        if key in batch1:
            continue
        step_rel = m["step"]
        axis = re.split(r"[\\/]", step_rel)[1]
        dxf_name = m["dxf"][0]
        step_abs = os.path.join(DATA, step_rel)
        dxf_abs = os.path.join(DATA, u"DXF", u"部品表用DXFデータ", axis, dxf_name)
        if not (os.path.exists(step_abs) and os.path.exists(dxf_abs)):
            continue
        name = os.path.splitext(dxf_name)[0].split("_", 1)[-1]
        skip, why = screen(name, dxf_abs)
        rec = {
            "key": key, "axis": axis,
            "step": os.path.relpath(step_abs, ROOT),
            "human_dxf": os.path.relpath(dxf_abs, ROOT),
            "name": name,
            "bucket_a": dxf_name in a_names,
            "step_bytes": os.path.getsize(step_abs),
            "dxf_bytes": os.path.getsize(dxf_abs),
            "name_class": guess_class(name),
        }
        if skip:
            rec["excluded_reason"] = why
            excluded.append(rec)
        else:
            if why:
                rec["screen_note"] = why
            rows.append(rec)

    print(u"===== 照合対象外(簡略図示) %d 点 =====" % len(excluded))
    for r in sorted(excluded, key=lambda r: r["key"]):
        print(u"  %-8s %-14s %-28s %s" % (r["key"], r["axis"], r["name"][:28],
                                          r["excluded_reason"]))

    def small_enough(r):
        return (r["step_bytes"] <= STEP_KB_MAX * 1024 and
                r["dxf_bytes"] <= DXF_KB_MAX * 1024)

    pool = [r for r in rows if small_enough(r)]
    dropped_big = [r for r in rows if not small_enough(r)]
    print(u"\n候補 %d 点(巨大ファイル除外 %d 点: %r)"
          % (len(pool), len(dropped_big), [r["key"] for r in dropped_big]))

    by_axis = defaultdict(list)
    for r in pool:
        by_axis[r["axis"]].append(r)
    axes = sorted(by_axis)

    # --- クラス枠を軸ラウンドロビンで埋める(同じ品名は後回しにして形状の多様性を稼ぐ) ---
    picked, seen, names = [], set(), set()

    def take(pred, n_target):
        pools = {a: [r for r in sorted(by_axis[a], key=lambda r: (not r["bucket_a"],
                                                                 r["step_bytes"]))
                     if pred(r) and r["key"] not in seen] for a in axes}
        for pass_no in (0, 1):
            while len(picked) < n_target:
                moved = False
                for a in axes:
                    if len(picked) >= n_target:
                        break
                    p = pools[a]
                    idx = next((i for i, r in enumerate(p)
                                if pass_no or r["name"] not in names), None)
                    if idx is None:
                        continue
                    r = p.pop(idx)
                    picked.append(r)
                    seen.add(r["key"])
                    names.add(r["name"])
                    moved = True
                if not moved:
                    break

    acc = 0
    for cls in ("bend_bracket", "plate_like", "lathe_like", "block_like"):
        acc += QUOTA[cls]
        take(lambda r, c=cls: r["name_class"] == c, min(acc, N_TOTAL))
    take(lambda r: True, N_TOTAL)                    # 枠が埋まらなければ何でも

    picked.sort(key=lambda r: (r["axis"], r["key"]))
    print(u"\n===== 第2弾 対象 %d 点 =====" % len(picked))
    for i, r in enumerate(picked):
        r["order"] = i
        print(u"[%2d] %-8s %-14s %-28s %-13s %s step=%5dKB dxf=%5dKB" % (
            i, r["key"], r["axis"], r["name"][:28], r["name_class"],
            u"A " if r["bucket_a"] else u"非A",
            r["step_bytes"] // 1024, r["dxf_bytes"] // 1024))
    cc = defaultdict(int)
    ac = defaultdict(int)
    for r in picked:
        cc[r["name_class"]] += 1
        ac[r["axis"]] += 1
    print(u"\n名前クラス: %r" % dict(cc))
    print(u"軸        : %r" % dict(ac))
    print(u"バケットA %d / 非A %d" % (sum(1 for r in picked if r["bucket_a"]),
                                      sum(1 for r in picked if not r["bucket_a"])))

    with io.open(os.path.join(HERE, "targets.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"targets": picked}, ensure_ascii=False, indent=2))
    with io.open(os.path.join(HERE, "excluded.json"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"excluded": excluded, "too_big": dropped_big},
                           ensure_ascii=False, indent=2))
    print(u"\n保存: %s / excluded.json" % os.path.join(HERE, "targets.json"))


if __name__ == "__main__":
    main()
