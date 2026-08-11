# -*- coding: utf-8 -*-
"""SolidWorks が「計測に使える状態か」を確かめる。使えなければ 1 を返す。

プロセスが居るだけでは足りない ── 2026-08-11 の実測で、計測が
TIMEOUT_MEASURE=1800秒 で打ち切られたあと **プロセスは生きているのに
LoadFile4 が None(error=1) を返す**(取り込み途中の SLDPRT が開きっぱなし)
状態になり、次の部品が巻き添えで落ちた。だから **COM で実際に触って**確かめる。

安全規約: ユーザーの開いている文書は触らない。閉じるのは
**この作業場のパス配下から開いたもの**だけ(それ以外が開いていたら異常として 1)。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent\engine")

WORKDIR_MARK = r"\scratchpad\random5\data\納品箱"


def _attr(obj, name: str):
    """gen_py ではプロパティ、素の COM ではメソッドという揺れを吸収する。"""
    value = getattr(obj, name, "")
    if callable(value):
        try:
            value = value()
        except Exception:  # noqa: BLE001
            return ""
    return value or ""


def main() -> int:
    import sw_compat
    try:
        sw = sw_compat.connect_sw()
    except Exception as exc:  # noqa: BLE001
        print(f"NG: 接続できません ({exc})")
        return 1
    try:
        n = sw.GetDocumentCount()
    except Exception as exc:  # noqa: BLE001
        print(f"NG: GetDocumentCount が落ちました ({exc})")
        return 1
    print(f"progid={sw_compat.detected_progid()} docs={n}")
    if not n:
        print("OK: 文書0件・すぐ使えます")
        return 0

    # 開きっぱなしがある。**この作業場のもの**だけ閉じる。
    doc = sw.GetFirstDocument()
    leftovers, foreign = [], []
    while doc:
        # gen_py 版では GetTitle / GetPathName は **プロパティ**(呼ぶと TypeError)
        title = str(_attr(doc, "GetTitle"))
        path = str(_attr(doc, "GetPathName"))
        (leftovers if (WORKDIR_MARK in path or not path) else foreign).append((title, path))
        doc = doc.GetNext()
    for title, path in foreign:
        print(f"NG: 見覚えの無い文書が開いています(触りません): {title} {path}")
    if foreign:
        return 1
    for title, path in leftovers:
        print(f"  取り込み途中の残骸を閉じます: {title!r}")
        try:
            sw.QuitDoc(title)
        except Exception as exc:  # noqa: BLE001
            print(f"NG: QuitDoc に失敗 ({exc})")
            return 1
    try:
        left = sw.GetDocumentCount()
    except Exception as exc:  # noqa: BLE001
        print(f"NG: 閉じたあとに落ちました ({exc})")
        return 1
    print(f"閉じたあとの文書数 = {left}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
