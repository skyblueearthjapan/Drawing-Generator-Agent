# -*- coding: utf-8 -*-
"""
図枠差し引きエンジン(恒久モジュール)。
`図枠/frame_template.dxf` の共通(固定)エンティティ集合を基準に、任意のDXFドキュメントの
modelspaceから図枠に属するエンティティを差し引き、部品の作図+寸法のみを残す。

想定用途:
  - フェーズ1検証: 実部品図から図枠を差し引いて取りこぼし/取りすぎを確認する
  - フェーズ2以降: 自社パイプラインが frame_template.dxf を合成して作った成果物DXFから、
    検証ゲート用に「図枠以外」の部分だけを抽出する

制約(既知の限界。frame_analysis.md 参照):
  - 図枠は用紙サイズ・尺度により座標がスケールする「バケツ」が複数存在する。
    本モジュールは frame_template.dxf が代表する1バケツ(バケツA: 表題欄基準(220.24,27.41)/
    用紙410x287/Ａ３)にのみ座標が一致する。他バケツのファイルに対しては一致率が低く、
    その場合は subtract_frame() の戻り値 removed が少数/0件になる(=図枠が引けていない)。
    呼び出し側は summary["removed_ratio_hint"] 等で結果を確認すること。

使い方:
    python engine/frame_extract.py <dxf> [--render out.png]
"""
import sys
import io
import argparse
import ezdxf

TOL = 6  # round digits (~1e-6 tolerance)

# 差し引き対象として扱うエンティティタイプ(DIMENSION/LEADER/HATCH等、部品側の作図要素は対象外)
FRAME_CANDIDATE_TYPES = {"LINE", "LWPOLYLINE", "CIRCLE", "ARC", "POINT", "TEXT", "MTEXT", "INSERT"}


def _r(v):
    return round(float(v), TOL)


def _pt(p):
    return (_r(p.x), _r(p.y))


def _style_key(doc, style_name):
    """STYLE名はファイルごとに採番され直る(GMM001,GMM002...)ため、名前ではなく
    実体(フォント/幅係数/斜体角)で比較する。"""
    try:
        s = doc.styles.get(style_name)
        return (str(s.dxf.font).lower(), round(s.dxf.width, 6), round(s.dxf.oblique, 6))
    except Exception:
        return (style_name,)


def entity_signature(e, doc):
    """位置・幾何・内容を含む比較用シグネチャ。tuple。対象外タイプはNoneを返す。"""
    t = e.dxftype()
    if t not in FRAME_CANDIDATE_TYPES:
        return None
    layer = e.dxf.layer
    try:
        color = e.dxf.color
    except Exception:
        color = 256
    try:
        lt = e.dxf.linetype
    except Exception:
        lt = "BYLAYER"

    if t == "LINE":
        return (t, layer, color, lt, _pt(e.dxf.start), _pt(e.dxf.end))
    elif t == "LWPOLYLINE":
        pts = tuple((_r(p[0]), _r(p[1])) for p in e.get_points())
        return (t, layer, color, lt, e.closed, pts)
    elif t == "CIRCLE":
        return (t, layer, color, lt, _pt(e.dxf.center), _r(e.dxf.radius))
    elif t == "ARC":
        return (t, layer, color, lt, _pt(e.dxf.center), _r(e.dxf.radius),
                _r(e.dxf.start_angle), _r(e.dxf.end_angle))
    elif t == "POINT":
        return (t, layer, color, lt, _pt(e.dxf.location))
    elif t == "TEXT":
        h = _r(e.dxf.height) if e.dxf.hasattr("height") else None
        return (t, layer, color, _pt(e.dxf.insert), e.dxf.text, h,
                _style_key(doc, e.dxf.style), _r(getattr(e.dxf, "rotation", 0.0)))
    elif t == "MTEXT":
        h = _r(e.dxf.char_height) if e.dxf.hasattr("char_height") else None
        return (t, layer, color, _pt(e.dxf.insert), e.text, h,
                _style_key(doc, e.dxf.style), _r(getattr(e.dxf, "rotation", 0.0)))
    elif t == "INSERT":
        return (t, layer, color, e.dxf.name, _pt(e.dxf.insert),
                _r(getattr(e.dxf, "xscale", 1.0)), _r(getattr(e.dxf, "yscale", 1.0)),
                _r(getattr(e.dxf, "rotation", 0.0)))
    return None


def load_frame_signatures(template_path="図枠/frame_template.dxf"):
    """frame_template.dxf を読み込み、そのmodelspace全エンティティのシグネチャ集合を返す。"""
    doc = ezdxf.readfile(template_path)
    msp = doc.modelspace()
    sigs = set()
    for e in msp:
        s = entity_signature(e, doc)
        if s is not None:
            sigs.add(s)
    return doc, sigs


def subtract_frame(doc, frame_signatures=None, template_path="図枠/frame_template.dxf"):
    """
    doc(ezdxf.Drawing)のmodelspaceから図枠エンティティを差し引く。

    Args:
        doc: 対象のezdxf.Drawing
        frame_signatures: あらかじめ load_frame_signatures() で得たシグネチャ集合(再利用時に指定)
        template_path: frame_signatures未指定時に読み込むテンプレートパス

    Returns:
        (remaining_entities, summary) のタプル。
        remaining_entities: 図枠に一致しなかったエンティティのリスト(DIMENSION/LEADER等、
            比較対象外タイプは常に残る)
        summary: dict(total, frame_matched, remaining, remaining_ratio)
    """
    if frame_signatures is None:
        _, frame_signatures = load_frame_signatures(template_path)

    msp = doc.modelspace()
    total = 0
    matched = 0
    remaining = []
    for e in msp:
        total += 1
        s = entity_signature(e, doc)
        if s is not None and s in frame_signatures:
            matched += 1
            continue
        remaining.append(e)

    summary = {
        "total": total,
        "frame_matched": matched,
        "remaining": len(remaining),
        "remaining_ratio": (len(remaining) / total) if total else 0.0,
    }
    return remaining, summary


def _main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("dxf")
    ap.add_argument("--template", default="図枠/frame_template.dxf")
    ap.add_argument("--render", default=None, help="残りエンティティのみを新規DXFとして保存するパス")
    args = ap.parse_args()

    doc = ezdxf.readfile(args.dxf)
    remaining, summary = subtract_frame(doc, template_path=args.template)
    print("== subtract_frame summary ==")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    from collections import Counter
    print("  remaining type counts:", dict(Counter(e.dxftype() for e in remaining)))

    if args.render:
        from ezdxf.addons.importer import Importer
        new_doc = ezdxf.new("AC1015", setup=False)
        new_doc.encoding = doc.encoding
        new_doc.header["$DWGCODEPAGE"] = doc.header.get("$DWGCODEPAGE", "ANSI_932")
        importer = Importer(doc, new_doc)
        importer.import_entities(remaining, target_layout=new_doc.modelspace())
        importer.finalize()
        new_doc.saveas(args.render)
        print("  saved remaining-only DXF:", args.render)


if __name__ == "__main__":
    _main()
