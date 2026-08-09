# -*- coding: utf-8 -*-
"""
図枠共通(固定)エンティティ(113個)を基準ファイルから複製し、図枠/frame_template.dxf を生成する。
ezdxf.addons.importer.Importer を使い、レイヤ・線種・文字スタイル・ブロック定義を含めて
忠実に複製する(手動コピーによる属性の取りこぼしを避けるため)。
"""
import sys
import io
import ezdxf
from ezdxf.addons.importer import Importer

sys.path.insert(0, "調査")
from sample_files import sample_paths
import extract_common as ec

paths = sample_paths()
ref_path = paths[0]  # 25154-1-07_走行ギア.dxf (バケツA代表)

# 1) 全18ファイルの完全一致シグネチャ集合(共通=図枠)を再計算
per_file_sigs = []
for p in paths:
    d = ezdxf.readfile(p)
    m = d.modelspace()
    sigs = set()
    for e in m:
        s = ec.entity_sig(e, d)
        if s is not None:
            sigs.add(s)
    per_file_sigs.append(sigs)
common = set(per_file_sigs[0])
for s in per_file_sigs[1:]:
    common &= s
print(f"共通(固定)エンティティ数: {len(common)}")

# 2) 基準ファイルから該当エンティティを収集
doc_src = ezdxf.readfile(ref_path)
msp_src = doc_src.modelspace()
to_copy = []
for e in msp_src:
    s = ec.entity_sig(e, doc_src)
    if s in common:
        to_copy.append(e)
print(f"基準ファイル({ref_path})から抽出: {len(to_copy)} 個")
assert len(to_copy) == len(common), "抽出数が共通集合数と一致しません"

# 3) 新規ドキュメント作成(AC1015・コードページ踏襲)
doc_new = ezdxf.new("AC1015", setup=False)
doc_new.encoding = doc_src.encoding  # doc.encoding属性を合わせないとsaveas時に$DWGCODEPAGEが上書きされる
doc_new.header["$DWGCODEPAGE"] = doc_src.header.get("$DWGCODEPAGE", "ANSI_932")
doc_new.header["$LTSCALE"] = doc_src.header.get("$LTSCALE", 1.0)
try:
    doc_new.header["$INSUNITS"] = doc_src.header.get("$INSUNITS", 4)  # 4=mm
except Exception:
    pass

# 4) Importerで忠実複製(レイヤ・線種・文字スタイル・ブロック定義を解決)
importer = Importer(doc_src, doc_new)
importer.import_entities(to_copy, target_layout=doc_new.modelspace())
importer.finalize()

doc_new.saveas("図枠/frame_template.dxf")
print("saved 図枠/frame_template.dxf")
print("dxfversion=", doc_new.dxfversion)
print("modelspace entity count=", len(doc_new.modelspace()))
