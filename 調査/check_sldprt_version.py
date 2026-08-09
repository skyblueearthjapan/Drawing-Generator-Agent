# -*- coding: utf-8 -*-
"""SLDPRTの保存バージョンを調べる(SW2023=バージョン31で開けるか判定)。

SolidWorksファイルはOLE複合ファイルで、中に "SolidWorks YYYY" 等の文字列を含む。
UTF-16LEで検索して保存版を推定する。
"""
import sys, io, re, glob, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

def sw_version_of(path):
    with open(path, "rb") as f:
        data = f.read()
    hits = set()
    # UTF-16LE の "SolidWorks 20xx" / "SOLIDWORKS 20xx"
    for m in re.finditer(
        b"S\x00(o\x00l\x00i\x00d\x00|O\x00L\x00I\x00D\x00)W\x00(o\x00r\x00k\x00s\x00|O\x00R\x00K\x00S\x00)\x20\x002\x000\x00(\\d)\x00(\\d)\x00",
        data,
    ):
        hits.add("20" + m.group(3).decode("ascii") + m.group(4).decode("ascii"))
    # ASCII でも
    for m in re.finditer(rb"S(?:olid|OLID)W(?:orks|ORKS) 20(\d\d)", data):
        hits.add("20" + m.group(1).decode("ascii"))
    return sorted(hits)

targets = sys.argv[1:] or [
    r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent\荏原トライ調整用\3D\1.走行軸\1-09.SLDPRT",
    r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent\荏原トライ調整用\3D\1.走行軸\1-03.SLDPRT",
    r"C:\Users\imaizumi.LINEWORKS-NET\Documents\部品図作成agent\荏原トライ調整用\3D\2.ターン軸",
]
for t in targets:
    if os.path.isdir(t):
        files = glob.glob(os.path.join(t, "*.SLDPRT"))[:3]
    else:
        files = [t]
    for f in files:
        print(os.path.basename(f), "->", sw_version_of(f))
