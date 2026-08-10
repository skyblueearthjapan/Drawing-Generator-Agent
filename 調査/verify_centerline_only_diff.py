# -*- coding: utf-8 -*-
u"""2つのDXFの差分が「中心線の追加だけ」であることを**機械確認**する。

回帰の基準更新(既存図面は中心線が増えるので基準DXFを差し替える)を、
「見た目で同じだから良い」ではなく決定論で正当化するための道具。

判定:
  - 中心線種(DASHDOT/CENTER系)を除いたエンティティの**署名多重集合が完全一致**すること
  - 追加されたエンティティが**全て中心線種**であること
  - 削除されたエンティティが**1つも無い**こと

実行:
    python 調査/verify_centerline_only_diff.py <old.dxf> <new.dxf> [...ペアを繰り返す]
    python 調査/verify_centerline_only_diff.py --preset regress
    python 調査/verify_centerline_only_diff.py --preset blind2
"""
import argparse
import io
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8")

import ezdxf  # noqa: E402
from engine import dim_engine  # noqa: E402

R = 4      # 座標の丸め桁


def _pt(p):
    return (round(float(p[0]), R), round(float(p[1]), R))


def sig(e):
    u"""エンティティの内容署名(名前空間ではなく実体で比較する。CLAUDE.mdの教訓)。"""
    t = e.dxftype()
    lt = str(e.dxf.get("linetype", "BYLAYER"))
    col = e.dxf.get("color", 256)
    head = (t, lt.upper(), col)
    if t == "LINE":
        return head + (_pt(e.dxf.start), _pt(e.dxf.end))
    if t == "CIRCLE":
        return head + (_pt(e.dxf.center), round(e.dxf.radius, R))
    if t == "ARC":
        return head + (_pt(e.dxf.center), round(e.dxf.radius, R),
                       round(e.dxf.start_angle, R), round(e.dxf.end_angle, R))
    if t == "LWPOLYLINE":
        return head + (tuple(_pt(p) for p in e.get_points("xy")), bool(e.closed))
    if t in ("MTEXT",):
        return head + (_pt(e.dxf.insert), e.text, round(e.dxf.char_height, R))
    if t == "TEXT":
        return head + (_pt(e.dxf.insert), e.dxf.text)
    if t == "INSERT":
        return head + (str(e.dxf.name), _pt(e.dxf.insert),
                       round(e.dxf.get("rotation", 0.0), R))
    if t == "DIMENSION":
        return head + (str(e.dxf.dimstyle), _pt(e.dxf.defpoint),
                       str(e.dxf.get("text", "")))
    if t == "LEADER":
        return head + (tuple(_pt(v) for v in e.vertices),)
    if t == "POINT":
        return head + (_pt(e.dxf.location),)
    if t in ("SPLINE", "ELLIPSE"):
        try:
            return head + (tuple(_pt(p) for p in e.flattening(0.1)),)
        except Exception:
            return head + ("<curve>",)
    return head + ("<%s>" % t,)


def load_sigs(path):
    doc = ezdxf.readfile(path)
    center, other = Counter(), Counter()
    for e in doc.modelspace():
        (center if dim_engine.is_centerline(e) else other)[sig(e)] += 1
    return center, other


def compare(old_path, new_path):
    c0, o0 = load_sigs(old_path)
    c1, o1 = load_sigs(new_path)
    only_old = o0 - o1
    only_new = o1 - o0
    rec = {
        "old": os.path.relpath(old_path, ROOT).replace(os.sep, "/"),
        "new": os.path.relpath(new_path, ROOT).replace(os.sep, "/"),
        "non_centerline_entities": [sum(o0.values()), sum(o1.values())],
        "centerline_entities": [sum(c0.values()), sum(c1.values())],
        "removed_non_centerline": sum(only_old.values()),
        "added_non_centerline": sum(only_new.values()),
        "added_centerline": sum((c1 - c0).values()),
        "removed_centerline": sum((c0 - c1).values()),
    }
    rec["ok"] = (rec["removed_non_centerline"] == 0
                 and rec["added_non_centerline"] == 0
                 and rec["removed_centerline"] == 0
                 and rec["added_centerline"] > 0)
    if not rec["ok"]:
        rec["sample_removed"] = [str(k) for k in list(only_old)[:5]]
        rec["sample_added"] = [str(k) for k in list(only_new)[:5]]
    return rec


PRESETS = {
    # 回帰3件: 旧納品(生成図面/)と 中心線ONの再生成(調査/regress_out/)
    "regress": [
        (u"生成図面/テスト-004_ホルダー.dxf", u"調査/regress_out/テスト-004_ホルダー.dxf"),
        (u"生成図面/AUTO-001_クッション.dxf", u"調査/regress_out/AUTO-001_クッション.dxf"),
        (u"生成図面/AUTO-002_ベアリングカラー.dxf", u"調査/regress_out/AUTO-002_ベアリングカラー.dxf"),
    ],
    # BLIND2納品5枚: 旧納品箱 と 中心線ONの再生成(調査/centerline/blind2/)
    "blind2": [
        (u"data/納品箱/BLIND2-25154-1-27/25154-1-27_走行フレーム踏板.dxf",
         u"調査/centerline/blind2/25154-1-27_走行フレーム踏板.dxf"),
        (u"data/納品箱/BLIND2-25154-2-16/25154-2-16_指針.dxf",
         u"調査/centerline/blind2/25154-2-16_指針.dxf"),
        (u"data/納品箱/BLIND2-25154-3-02/25154-3-02_モータブラケット.dxf",
         u"調査/centerline/blind2/25154-3-02_モータブラケット.dxf"),
        (u"data/納品箱/BLIND2-25154-4-05/25154-4-05_駆動ユニットブラケット.dxf",
         u"調査/centerline/blind2/25154-4-05_駆動ユニットブラケット.dxf"),
        (u"data/納品箱/BLIND2-25154-5-05/25154-5-05_減速機フランジ.dxf",
         u"調査/centerline/blind2/25154-5-05_減速機フランジ.dxf"),
    ],
}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="*")
    ap.add_argument("--preset", choices=sorted(PRESETS))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv[1:])

    pairs = []
    if a.preset:
        pairs += [(os.path.join(ROOT, o), os.path.join(ROOT, n))
                  for o, n in PRESETS[a.preset]]
    for i in range(0, len(a.pairs) - 1, 2):
        pairs.append((a.pairs[i], a.pairs[i + 1]))
    if not pairs:
        ap.error(u"比較ペアが指定されていません")

    recs = []
    for o, n in pairs:
        if not (os.path.exists(o) and os.path.exists(n)):
            recs.append({"old": o, "new": n, "ok": False, "error": u"ファイルが無い"})
            continue
        recs.append(compare(o, n))
    n_ok = sum(1 for r in recs if r.get("ok"))
    print(u"中心線のみの差分: %d/%d" % (n_ok, len(recs)))
    for r in recs:
        print(u"  %s %s" % ("○" if r.get("ok") else "×", os.path.basename(r["new"])))
        if "error" in r:
            print(u"     ", r["error"])
            continue
        print(u"     中心線以外 %s->%s (追加%d/削除%d) / 中心線 %s->%s (追加%d)"
              % (r["non_centerline_entities"][0], r["non_centerline_entities"][1],
                 r["added_non_centerline"], r["removed_non_centerline"],
                 r["centerline_entities"][0], r["centerline_entities"][1],
                 r["added_centerline"]))
        for k in ("sample_removed", "sample_added"):
            if r.get(k):
                print(u"      %s: %s" % (k, r[k][:3]))
    if a.out:
        with io.open(a.out, "w", encoding="utf-8") as f:
            f.write(json.dumps(recs, ensure_ascii=False, indent=1))
        print(u"出力:", a.out)
    return 0 if n_ok == len(recs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
