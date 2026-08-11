# -*- coding: utf-8 -*-
"""取り込み途中で置き去りになった部品を、**タイトルを照合してから**閉じる(保存しない)。

    python close_leftover.py 1-18.SLDPRT 3-13.SLDPRT ...

安全規約: ユーザーの文書には触らない。閉じるのは
  - 引数で名指しされたタイトルと一致し、かつ
  - `GetPathName` が空(= STEP 取り込みで作られた未保存の部品)
のものだけ。1つでも条件から外れたら何もしないで 1 を返す。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent\engine")

WANTED = {a.lower() for a in sys.argv[1:]}


def attr(obj, name):
    v = getattr(obj, name, "")
    if callable(v):
        try:
            v = v()
        except Exception:  # noqa: BLE001
            return ""
    return v or ""


def main() -> int:
    import sw_compat
    sw = sw_compat.connect_sw()
    print(f"progid={sw_compat.detected_progid()} docs={sw.GetDocumentCount()}")

    doc, targets = sw.GetFirstDocument(), []
    while doc:
        title = str(attr(doc, "GetTitle"))
        path = str(attr(doc, "GetPathName"))
        keep = "保存済み(触りません)" if path else "未保存"
        match = title.lower() in WANTED if WANTED else not path
        print(f"  文書: title={title!r} path={path!r} {keep} 対象={match}")
        if match and not path:
            targets.append(title)
        nxt = getattr(doc, "GetNext", None)      # gen_py ではプロパティのことがある
        doc = nxt() if callable(nxt) else nxt

    for title in targets:
        print(f"  QuitDoc(保存しない): {title}")
        sw.QuitDoc(title)
    left = sw.GetDocumentCount()
    print(f"閉じたあとの文書数 = {left}")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
