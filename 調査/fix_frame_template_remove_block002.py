# -*- coding: utf-8 -*-
"""
frame_block_audit.py の判定に基づく修正スクリプト。

判定結果(調査/frame_block_audit.json):
  - BLOCK002 (32エンティティ、bbox 519-639,813-1077。図枠(0,0)-(420,297)の外)は、
    バケットA実ファイル80枚中78枚で「その位置に対応するINSERTが存在しない(不在)」が多数派。
    残り2枚(25154-1-07, 25154-S1-07 のいずれも「走行ギア」)にのみ同一内容で存在。
    図枠(0,0)-(420,297)の外に描画される、部品とは無関係な残骸(ピニオン断面図)であり、
    テンプレート構築時の基準ファイル(25154-1-07)固有の混入物と判断。
    → INSERTごと完全に削除する(多数派=不在のため、置き換えではなく除去)。
  - BLOCK004/005/006/007 は80/80で多数派と一致(監査OK)。変更しない。

本スクリプトは 図枠/frame_template.dxf からBLOCK002のINSERTと、それが参照する
ブロック定義(他から参照されていなければ)を削除し、AC1015/cp932を維持したまま上書き保存する。
"""
import ezdxf

TEMPLATE_PATH = "図枠/frame_template.dxf"
TARGET_BLOCK_NAME = "BLOCK002"


def main():
    doc = ezdxf.readfile(TEMPLATE_PATH)
    msp = doc.modelspace()

    before_count = len(msp)
    print(f"修正前 modelspace エンティティ数: {before_count}")

    to_delete = [e for e in msp if e.dxftype() == "INSERT" and e.dxf.name == TARGET_BLOCK_NAME]
    print(f"削除対象INSERT数 (name={TARGET_BLOCK_NAME}): {len(to_delete)}")
    assert len(to_delete) == 1, f"想定外: BLOCK002のINSERTが{len(to_delete)}個見つかりました"

    for e in to_delete:
        msp.delete_entity(e)

    after_count = len(msp)
    print(f"INSERT削除後 modelspace エンティティ数: {after_count}")
    assert after_count == before_count - 1

    # 他に参照されていなければブロック定義自体も削除してファイルをクリーンに保つ
    remaining_refs = [e for e in msp if e.dxftype() == "INSERT" and e.dxf.name == TARGET_BLOCK_NAME]
    if not remaining_refs and TARGET_BLOCK_NAME in doc.blocks:
        doc.blocks.delete_block(TARGET_BLOCK_NAME, safe=True)
        print(f"ブロック定義 {TARGET_BLOCK_NAME} を削除しました")

    # AC1015/cp932を明示維持(CLAUDE.md既知knowledge: doc.encodingを明示しないと化ける)
    print("dxfversion=", doc.dxfversion)
    print("encoding=", doc.encoding)
    print("$DWGCODEPAGE=", doc.header.get("$DWGCODEPAGE"))

    doc.saveas(TEMPLATE_PATH)
    print(f"保存: {TEMPLATE_PATH}")

    # 検証読み直し
    doc2 = ezdxf.readfile(TEMPLATE_PATH)
    msp2 = doc2.modelspace()
    print(f"再読込後 modelspace エンティティ数: {len(msp2)}")
    print("dxfversion=", doc2.dxfversion, "encoding=", doc2.encoding,
          "$DWGCODEPAGE=", doc2.header.get("$DWGCODEPAGE"))
    names = sorted(e.dxf.name for e in msp2 if e.dxftype() == "INSERT")
    print("残存INSERT名:", names)
    assert TARGET_BLOCK_NAME not in names


if __name__ == "__main__":
    main()
