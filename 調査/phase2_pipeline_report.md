# フェーズ2 実行レポート — SLDPRT → SW図面 → 第三角法ビュー → DXF の貫通

実施日: 2026-08-09 / 環境: SolidWorks 2023 (`SldWorks.Application.31`) + Python 3.14 + pywin32 + ezdxf 1.4.4

## 0. 結論

**パイプラインは貫通した。スケール誤差 0.000000%(検証基準 0.1% 以内)。**
3面図+等角投影の4ビューが DXF 内で領域分離して特定でき、隠れ線は `HIDDEN` 線種で区別できる。
さらに **`IView.ModelToViewTransform` によってモデル座標 → DXF座標の厳密な変換行列が取れる**ことが判った
(実測差 **0.000000 mm**)。これはフェーズ3(寸法挿入)の土台になる最重要の成果。

### 成果物

| パス | 内容 |
|---|---|
| `engine/draw_pipeline.py` | 再利用可能なパイプライン関数群(connect / open_part_readonly / part_metrics / new_drawing / insert_standard_views / export_dxf / prefs_override) |
| `調査/run_phase2_pipeline.py` | 実行スクリプト(部品パスを引数で受ける)。`調査/phase2_meta_<部品名>.json` に全計測値を残す |
| `調査/analyze_phase2_dxf.py` | 出力DXFの構造解析+5種の検証(スケール/変換行列/円径/領域分離/第三角整列) |
| `調査/render_phase2_dxf.py` | 線種色分けPNG化(目視ゲート用) |
| `調査/phase2_out_15015-P3-013_ホルダー.dxf` / `.png` | 実出力サンプル①(旋盤物・穴あり) |
| `調査/phase2_out_15015-P3-012_端子棒.dxf` / `.png` | 実出力サンプル②(段付き軸・二面取り) |
| `調査/probe_drawing_env.py` | 図面テンプレート所在・SLDPRT保存版の事前調査 |
| `調査/probe_view_position_scale.py` | `IView.Position` / 尺度設定の受付形式の切り分け |
| `調査/probe_tangent_edges.py` / `probe_tangent.dxf` | 接線エッジ表現の切り分け(0/1/2の3条件比較) |
| `調査/probe_std3views.py` | 標準3面図API `Create3rdAngleViews2` の挙動確認 |

---

## 1. 動いた API 列(実引数つき)

```python
# --- 接続(版はマシン差を sw_compat が吸収) ---
sw  = sw_compat.connect_sw()                 # GetActiveObject("SldWorks.Application.31")
mod = sw_compat.gen_module()                 # gencache.EnsureModule(GUID, 0, 31, 0)

# --- 事前判定(開かずに保存版を見る) ---
sw.VersionHistory(path)                      # -> ('16000[2022/300]',)  ※タプルで返る

# --- 部品を読み取り専用+サイレントで開く ---
doc = sw.OpenDoc6(path, 1, 1 | 2, "", 0, 0)  # swDocPART=1 / Silent=1|ReadOnly=2
if isinstance(doc, tuple): doc = doc[0]      # gen_py はタプルで返す
doc = mod.IModelDoc2(doc._oleobj_)
title = doc.GetTitle                         # gen_py では**プロパティ**

# --- 計測 ---
mp = doc.Extension.CreateMassProperty(); mp.UseSystemUnits = True
mp.Mass / mp.Volume / mp.Density / mp.CenterOfMass / mp.SurfaceArea
part   = mod.IPartDoc(doc._oleobj_)
part.GetMaterialPropertyName2("", "")        # -> ('', '') なら材質未設定
bodies = part.GetBodies2(0, True)
mod.IBody2(b._oleobj_).GetBodyBox            # プロパティ(単位 m)
doc.GetModelViewNames                        # プロパティ。日本語 '*正面' '*平面' '*右側面' '*等角投影'

# --- 図面を新規作成(A1・尺度1:1・第三角法・図枠なし) ---
tmpl = sw.GetUserPreferenceStringValue(10)   # 図面テンプレ = index 10 (部品8/アセンブリ9 の続き)
#  -> C:\ProgramData\SolidWorks\SOLIDWORKS 2023\templates\図面.drwdot
d    = sw.NewDocument(tmpl, 10, 0.841, 0.594)   # swDwgPaperA1size = 10
dwg  = mod.IDrawingDoc(d._oleobj_)
sh   = mod.ISheet(dwg.GetCurrentSheet()._oleobj_)
dwg.SetupSheet5(sh.GetName(), 10, 13, 1.0, 1.0, False, "", 0.841, 0.594, "Default", True)
#   引数: Name, PaperSize, TemplateIn(13=swDwgTemplateNone), Scale1, Scale2,
#         FirstAngle(False=第三角法), TemplateName, Width, Height, PropertyViewName,
#         RemoveModifiedNotes                                            -> True

# --- ビュー生成(独立ビュー4枚) ---
v = dwg.CreateDrawViewFromModelView3(part_path, "*正面", x_m, y_m, 0.0)   # -> IView(Dispatch)
v = mod.IView(v._oleobj_)
v.SetDisplayMode3(False, 1, False, False)    # UseParent=False, Mode=swHIDDEN_GREYED(=隠れ線表示)
v.SetDisplayTangentEdges2(1)                 # swTangentEdgesVisibleAndFonted
v.ScaleDecimal = 1.0                         # ★これを入れないと勝手に 2:1 になる(後述)
v.Position = VARIANT(VT_ARRAY|VT_R8, [x_m, y_m])   # ★tuple/list は不可(後述)
v.GetOutline()                               # [xmin,ymin,xmax,ymax] シート座標 m
v.ModelToViewTransform.ArrayData             # ★モデル→シートの厳密変換(後述)

# --- DXF出力 ---
sw.SetUserPreferenceIntegerValue(0,   3)     # swDxfVersion = swDxfFormat_R2000(AC1015)
sw.SetUserPreferenceIntegerValue(253, 0)     # swDxfMultiSheetOption = swDxfActiveSheetOnly
ok, errs, warns = doc.Extension.SaveAs3(out_dxf, 0, 1, None, None, 0, 0)   # 戻りはタプル

# --- 後始末 ---
sw.CloseDoc(doc.GetTitle)                    # 図面のタイトルは "Draw7 - ｼｰﾄ1" 形式
```

---

## 2. ハマった点と回避策(★=新規の罠。CLAUDE.md の知見節へ)

### ★T1. `IView.Position` に Python の tuple / list を代入すると **例外を出さずに壊れる**
`Position` は `VT_VARIANT`。`v.Position = (0.5, 0.3)` は成功したように見えて、
読み戻すと **`[0.0, 500.0]`**(x が捨てられ、y に x×1000 が入る)になる。
最初の実装ではこれが原因で 4 ビューが全部 x=0 の縦一列に重なった。

```python
v.Position = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [x_m, y_m])
```
**代入後は必ず読み戻して検証する**(`_set_position_mm()` に組み込み済み)。
実測ログ(`probe_view_position_scale.py`):

| 代入形式 | 読み戻した Position(mm) |
|---|---|
| `(0.5, 0.3)` tuple | `[0.0, 500.0]` ← 壊れる |
| `[0.5, 0.3]` list | `[0.0, 500.0]` ← 壊れる |
| `VARIANT(VT_ARRAY\|VT_R8, [0.5,0.3])` | `[500.0, 300.0]` ✔ |
| `VARIANT(VT_ARRAY\|VT_R8, [0.5,0.3,0.0])` | `[500.0, 300.0]` ✔(3要素でも可) |

### ★T2. シート尺度が 1:1 でもビューは勝手に **2:1** になる
`SetupSheet5` でシートを 1:1 にし、`GetProperties2` も `scale=[1.0,1.0]` を返すのに、
`CreateDrawViewFromModelView3` で作ったビューは **`ScaleDecimal=2.0`**(小物を自動拡大)。
しかも **`UseSheetScale` は `True` と答える**(嘘をつく)。
→ **`v.ScaleDecimal = 1.0` を明示代入する**(すると `UseSheetScale` は `False` に落ちる)。
代入後 `ScaleDecimal` を読み戻して検証すること(`_set_view_scale()`)。

### ★T3. `ISheet.GetSize` は引数なしでは呼べない
byref 出力引数を2つ取るため `sheet.GetSize` → 「種類が一致しません」。
`sheet.GetSize(0.0, 0.0)` と呼ぶ。用紙寸法は `GetProperties2()[5],[6]`(m)からも取れる。

### ★T4. 図面テンプレートは `GetUserPreferenceStringValue(10)`
姉妹プロジェクトの推定どおり。実測値の一覧(`probe_drawing_env.py`):
`6`=テンプレフォルダ / `7`=シートフォーマットフォルダ / **`8`=部品 / `9`=アセンブリ / `10`=図面** /
`14`=既定ハッチング / `24`=ユーザ設定フォルダ。
なお `GetUserPreferenceStringListValue(8/9/10)` は空文字を返すので使えない。

### ★T5. 図面ドキュメントの `GetTitle` は **"Draw7 - ｼｰﾄ1"**(シート名付き)
部品と違い「ファイル名」ではない。`CloseDoc` はこの文字列で通る。
未保存の新規図面なので `CloseDoc` は保存確認ダイアログを出さない。
**DXFに `SaveAs3` してもタイトルは変わらない**(エクスポート扱い。`.dxf` の名前にはならない)。
本実装ではクローズ直前に `GetTitle` を読み直してから閉じている。

### ★T6. `IView.GetOutline` は **ジオメトリ外形 + 11.88mm/片側**(尺度非依存)
正投影(正面/平面/右側面)では `outline - 11.88` がジオメトリ範囲に **厳密一致**した。
ただし **等角投影ビューでは一致しない**(ホルダーで縦に 11.70mm 過大)。
→ **ジオメトリの実範囲が要るときは GetOutline ではなく `ModelToViewTransform` から算出する**。
GetOutline はレイアウト(重なり回避)用と割り切る。

### ★T7. 中心マークは `SW_CENTERMARKSYMBOL_*` の **INSERT ブロック**として出る
中身は **CONTINUOUS の直線8本**(中心十字4本 + 延長4本)で、`CENTER` 線種ではない。
色は `BYBLOCK`(0)。**ビューの実測 bbox を片側 2.5mm 膨らませる**ので、
スケール検証では必ず除外する(除外前は右側面で 80.0mm vs 期待 75.0mm = 誤差 6.67% と誤判定した)。
姉妹プロジェクトの「タップ記号は INSERT ブロックの中にある」と同じ罠。

### ★T8. 接線エッジの線種(`probe_tangent_edges.py` の実測・回転指針の等角ビュー)

| `SetDisplayTangentEdges2` | 出力 |
|---|---|
| `0` swTangentEdgesHidden | 接線エッジは **出力されない**(計46) |
| `1` swTangentEdgesVisibleAndFonted | 可視の接線エッジ = **`PHANTOM` 線種**(2本)/計50 |
| `2` swTangentEdgesVisible | 接線エッジが **`Continuous`** で出る(実エッジと区別不能)/計50 |

→ **必ず `1` を使う**。`2` は実形状線と混ざるのでフェーズ3で破綻する。
❗ただし **隠れ側の接線エッジは `1` でも `HIDDEN` に混ざる**(HIDDEN 10本→12本)。
「HIDDEN のうち一部は接線エッジ」であることは DXF だけからは判別できない。

### ★T9. 材質未設定のモデルは密度 **1000 kg/m³**(水)で質量が出る
`15015-P3-013_ホルダー` は `GetMaterialPropertyName2` が `('', '')` で、
質量 0.139477 kg = 体積そのまま。**表題欄の「重量」を SW 質量特性から自動算出する仕様は、
材質が入っていないモデルでは成立しない**。`part_metrics()` は `material` と
`density_is_default_water` を必ず返すようにした。運用では
「材質未設定 → 依頼時入力の材質から密度を当てて再計算 or 質問票」が要る。

### ★T10. matplotlib で `MS Gothic` は **1文字も描画されない**
`msgothic.ttc` は `findfont` では解決するのに、実際には文字が消える(タイトルも目盛も全滅)。
**`Meiryo` または `Yu Gothic` を使う**(`matplotlib.rcParams["font.family"] = "Meiryo"`)。

### T11. その他(既知の再確認)
- `SaveAs3` の byref 引数はプレーン `0`、戻り値は `(ok, errors, warnings)` のタプル
- `OpenDoc6` の戻り値もタプルになる → `doc = doc[0]`
- 図枠(シートフォーマット)を付けないのは `SetupSheet5` の `TemplateIn=13`(`swDwgTemplateNone`)
- システムオプションは `prefs_override()` コンテキストマネージャで**必ず復元**する。
  実測: `swDxfVersion` は元々 `3`(R2000) → 一時的に `8` にして戻ることを確認済み

---

## 3. 出力DXFの構造分析

### 3.1 ファイル諸元(両サンプル共通)

| 項目 | 値 |
|---|---|
| DXFバージョン | **AC1015 (R2000)** ※`swDxfVersion` の既定が 3 = R2000。参考図枠DXFと同じ |
| `$INSUNITS` | `4` = ミリメートル |
| `$EXTMIN` / `$EXTMAX` | `(0,0,0)` / `(841,594,0)` = **用紙左下が DXF 原点、用紙実寸そのまま** |
| `$LTSCALE` / `$CELTSCALE` | `1.0` / `1.0` |
| レイアウト | `Model` に全エンティティ。`Layout1`/`Layout2` は**空** |
| レイヤ | **`0` と `Defpoints` のみ**。全ジオメトリが `0` に出る |
| 定義される線種 | `ByBlock` `ByLayer` `Continuous` `HIDDEN` `PHANTOM` `CENTER` `CENTERX2` `DOT2` |
| 実際に使われる線種 | `Continuous` / `HIDDEN` / (接線エッジ有効時)`PHANTOM` |
| ブロック | `SW_CENTERMARKSYMBOL_<n>`(中心マーク・LINE×8)のみ |

**重要**: SolidWorks 側でレイヤを定義していないため、**レイヤでは何も区別できない。区別は線種のみ**。
`swDxfUseSolidworksLayers`(toggle 305)は今回未使用。フェーズ3で
`IDrawingDoc.CreateLayer2` により「外形/隠れ線/中心線/寸法」をレイヤ分けできるか要検証。

### 3.2 サンプル① `15015-P3-013_ホルダー`(φ75×L40 の穴あきホルダー)

計測: 質量 0.139477 kg(**材質未設定**)/ 体積 139,476.504 mm³ / 表面積 23,177.100 mm² /
重心 (−19.8707, 0, 0) mm / bbox `[−40, −37.5, −37.5] .. [0, 37.5, 37.5]` = **40 × 75 × 75 mm** / ボディ1

DXF: 82,963 bytes / 82 エンティティ

| 種別 | 数 | 線種内訳 |
|---|---|---|
| LINE | 50 | — |
| LWPOLYLINE | 18 | — |
| CIRCLE | 7 | — |
| ARC | 4 | — |
| INSERT | 3 | 中心マーク |
| **合計** | **82** | `HIDDEN` 52 / `Continuous` 30 |

ビュー別:

| ビュー | エンティティ | 種別 | 線種 |
|---|---|---|---|
| front(*正面) | 17 | LINE 17 | Continuous 6 / HIDDEN 11 |
| top(*平面) | 19 | LINE 19 | Continuous 4 / HIDDEN 15 |
| right(*右側面) | 10 | CIRCLE 7, INSERT 3 | Continuous 9 / HIDDEN 1 |
| iso(*等角投影) | 36 | LWPOLYLINE 18, LINE 14, ARC 4 | Continuous 11 / HIDDEN 25 |

右側面ビューの円径: **φ8, φ8, φ11, φ11, φ26, φ33, φ75**(外形φ75+中央φ33/φ26段+2-φ8のφ11ザグリ)

### 3.3 サンプル② `15015-P3-012_端子棒`(段付き軸・二面取り)

計測: 質量 0.041432 kg / 体積 41,431.774 mm³ / 重心 (−43.0601, 0, 0) / bbox **78 × 30 × 30 mm**

DXF: 75,082 bytes / 105 エンティティ
LINE 62 / LWPOLYLINE 17 / **SPLINE 12** / ARC 11 / CIRCLE 2 / INSERT 1、
`Continuous` 83 / `HIDDEN` 22。円径 **φ8.5, φ30**。

→ **二面取りと円筒の交線は `SPLINE` で出力される**(`Continuous`)。
DXF読み側は LINE/ARC/CIRCLE/LWPOLYLINE に加え **SPLINE / ELLIPSE の平坦化**が必須。

---

## 4. スケール検証結果(数値)

### 検証① 部品 bbox 実寸 vs DXF 内ビュー実測(中心マーク INSERT を除外)

**`15015-P3-013_ホルダー`**

| ビュー | 期待幅(mm) | 実測幅 | 誤差 | 期待高(mm) | 実測高 | 誤差 |
|---|---|---|---|---|---|---|
| front (X×Y) | 40.0000 | 40.000000 | **0.000000%** | 75.0000 | 75.000000 | **0.000000%** |
| top (X×Z) | 40.0000 | 40.000000 | **0.000000%** | 75.0000 | 75.000000 | **0.000000%** |
| right (Z×Y) | 75.0000 | 75.000000 | **0.000000%** | 75.0000 | 75.000000 | **0.000000%** |

**`15015-P3-012_端子棒`**

| ビュー | 期待幅 | 実測幅 | 誤差 | 期待高 | 実測高 | 誤差 |
|---|---|---|---|---|---|---|
| front | 78.0000 | 78.000000 | **0.000000%** | 30.0000 | 30.000000 | **0.000000%** |
| top | 78.0000 | 78.000000 | **0.000000%** | 30.0000 | 30.000000 | **0.000000%** |
| right | 30.0000 | 30.000000 | **0.000000%** | 30.0000 | 30.000000 | **0.000000%** |

→ **最大誤差 0.000000%(基準 0.1% 以内)**。円径も φ75 / φ30 と実寸一致。

### 検証② `ModelToViewTransform` による厳密予測との差(ホルダー)

`ArrayData = [r0..r8, tx, ty, tz, s, 0,0,0]` は **列優先**で、変換は

```
sheet_x_mm = s*(r0*X + r3*Y + r6*Z) + tx*1000
sheet_y_mm = s*(r1*X + r4*Y + r7*Z) + ty*1000       (X,Y,Z はモデル座標 mm)
```

| ビュー | s | 予測bbox(mm) | 実測bbox(mm) | 最大差 |
|---|---|---|---|---|
| front | 1.000000 | `[327.9614, 173.5280, 367.9614, 248.5280]` | 同左 | **0.000000 mm** |
| top | 1.000000 | `[327.9614, 328.8800, 367.9614, 403.8800]` | 同左 | **0.000000 mm** |
| right | 1.000000 | `[434.8800, 173.5280, 509.8800, 248.5280]` | 同左 | **0.000000 mm** |
| iso | 1.000000 | `[431.7214, 312.2880, 513.0386, 420.4720]` | `[431.7214, 323.9861, 513.0386, 408.7739]` | 11.698 mm ※ |

※ 等角のみ差が出るのは「モデル bbox の8隅を投影した外接矩形」と「円筒の実投影」がずれるため(予測が過大側)。
正投影3ビューでは **0.000000 mm** で一致 = **変換行列は完全に信用できる**。

実測された回転行列(姉妹プロジェクトのスケッチ座標規約と一致):

| ビュー | sheet_x | sheet_y |
|---|---|---|
| `*正面` | `+X` | `+Y` |
| `*平面` | `+X` | `−Z` |
| `*右側面` | `−Z` | `+Y` |
| `*等角投影` | `0.7071X − 0.7071Z` | `−0.4082X + 0.8165Y − 0.4082Z` |

### 検証③ ビュー領域の分離と第三角法の整列(ホルダー)

| ビュー | DXF実測 bbox(mm) |
|---|---|
| front | `[327.9614, 173.5280, 367.9614, 248.5280]` |
| top | `[327.9614, 328.8800, 367.9614, 403.8800]` |
| right | `[432.3800, 171.0280, 512.3800, 251.0280]`(中心マーク込み) |
| iso | `[431.7214, 323.9861, 513.0386, 408.7739]` |

- **全6ペアで重なりゼロ** → 4ビューとも領域で一意に特定できる
- 正面/平面の x 中心差 = **0.000000 mm**(鉛直整列)
- 正面/右側面の y 中心差 = **0.000000 mm**(水平整列)
- 平面は正面の上 / 右側面は正面の右 = **第三角法**

### 検証④ 隠れ線の区別

全ビューで `HIDDEN` 線種のエンティティが存在し、`Continuous` と明確に分かれている
(ホルダー: HIDDEN 52 / Continuous 30)。**線種で機械的に判別可能**。
レイヤはすべて `0` なのでレイヤでは区別できない。

### 検証⑤ 目視(`調査/phase2_out_*.png`)

左下=正面、左上=平面、右下=右側面(φ75円+2-φ8ザグリ穴+中心マーク)、右上=等角投影。
黒=CONTINUOUS、赤破線=HIDDEN、紫=中心マークで色分け済み。図の欠落・重なりなし。

---

## 5. 標準3面図API `Create3rdAngleViews2` の評価(参考)

```python
dwg.Create3rdAngleViews2(part_path)   # -> True
```
- 生成されるのは **3ビューのみ(等角投影は付かない)**。正面が `Type=7`(モデルビュー)、
  平面・右側面が `Type=4`(投影ビュー)で **親子リンクされる**(`UseParentScale=True`)
- **尺度はやはり自動で 2:1**、隠れ線・接線エッジの設定も入らない
- 配置は自動(用紙中央基準・間隔広め)で**制御できない**

→ **採用しない**。理由: (a)等角が別途要る (b)配置を決定論的に制御したい
(フェーズ3で寸法をシート座標に置くため) (c)結局スケール/表示設定は個別に叩く必要がある。
本実装の「独立4ビュー + 実測グリッド配置」で整列誤差 0.000000mm を達成できており、
親子リンクの利点は失われていない。
※ ただし将来「投影ビューの自動整列を SW に保証させたい」場合の選択肢としては残る。

---

## 6. 後工程への申し送り

### 6.1 図枠合成(ezdxf)へ
1. **SW の出力DXFは「用紙左下 = 原点 (0,0)、単位 mm、実寸」**。
   `$EXTMAX = (用紙幅, 用紙高)`。図枠DXF を同じ座標系に置けば単純合成できる
2. 今回は **A1(841×594)・尺度1:1・図枠なし**で出している。
   最終用紙(A3等)に合わせるときは **ezdxf 側でビュー単位に平行移動+一様スケール**すればよい。
   ビューごとの移動は `調査/phase2_meta_*.json` の `views.<key>.outline_mm` /
   `geom_mm` / `model_to_view` で完結する
3. **全ジオメトリがレイヤ `0`** なので、合成時に線種を見て
   `Continuous`→外形レイヤ / `HIDDEN`→隠れ線レイヤ / `PHANTOM`→接線エッジレイヤ
   に**振り分けるのは ezdxf 側の仕事**
4. `SW_CENTERMARKSYMBOL_*` の INSERT は**中心マーク(注記)**。
   自社流儀の中心線を引くならこれは捨てて引き直す方が早い(中の線は CONTINUOUS で
   `CENTER` 線種ですらない)。捨てないなら必ず「注記」として分類すること

### 6.2 寸法挿入(フェーズ3)へ ★最重要
5. **`IView.ModelToViewTransform.ArrayData` でモデル3D座標 → DXFシート座標(mm)が厳密に出る**
   (正投影で誤差 0.000000 mm)。つまり
   **「SW側で穴の中心・面の位置を実測 → その座標をDXF上のどこに寸法線を引くかへ直に変換」**できる。
   ゲート①(寸法値照合)も同じ経路で閉じられる。`part_metrics()` と併せて
   `verify_hole_phase.py`(姉妹リポジトリ)の円筒面抽出をそのまま繋げられる
6. スケールは **必ずビュー単位に `ScaleDecimal` を明示設定して読み戻し検証**すること。
   放置すると 2:1 になり、寸法値と作図長さが2倍ずれる(最も危険な事故モード)
7. 隠れ線のうち一部は接線エッジ(T8)。**「HIDDEN だから穴の稜線」と決めつけない**
8. **SPLINE/ELLIPSE が出る**(円筒と平面の交線など)。DXF読み側は必ず平坦化に対応する

### 6.3 表題欄へ
9. **材質未設定モデルは密度1000で質量が出る**(T9)。`density_is_default_water` を必ず見て、
   True なら依頼時入力の材質から密度を掛け直すか、質問票に上げる
10. 品名は `doc.GetTitle`(拡張子なしのファイル名)、体積・表面積・重心も `part_metrics()` が返す

### 6.4 安全規約(維持すること)
11. `open_part_readonly()` は **`swOpenDocOptions_ReadOnly` 固定**で、開いた後に
    タイトルとファイル名を照合して不一致なら**例外を投げる(閉じない)**。
    `OpenDoc6` が None のとき `ActiveDoc` へフォールバックしない。
    `OpenedDoc.close()` は `mine=True` のものしか閉じない。
    実行前後の開いているドキュメント一覧を meta に記録している(今回は前後とも空)
12. システムオプションは `prefs_override()` で必ず復元(復元動作を実測確認済み)

---

## 7. 未解決 / 次に確かめること

1. **レイヤ分けを SW 側でできるか未検証**。`IDrawingDoc.CreateLayer2` + `SetCurrentLayer` と
   `swDxfUseSolidworksLayers`(toggle 305)で「外形/隠れ線」を別レイヤに出せると
   フェーズ3の分類が堅くなる。ezdxf 側で線種から振り直せば済むので優先度は中
2. **隠れ側の接線エッジが `HIDDEN` に混ざる**(T8)問題の回避策が未確立。
   `IView.HiddenEdges` や `IView.GetPolylines7` でエッジ種別が取れるかは未調査
3. **等角投影ビューの `GetOutline` がジオメトリ実範囲と一致しない**(T6)。
   レイアウト用途では過大側なので実害は無いが、詰めて配置したい場合は
   `ModelToViewTransform` で凸包を計算する必要がある
4. **大物部品での A1 1:1 のはみ出し**は未検証(今回の2部品は最大78mm)。
   仕様どおり「見切れてよい」としているが、フェーズ3で寸法を置くときに
   用紙外の座標が出ることは織り込むこと
5. **荏原の実データ(SW2026保存)では未検証**。教師STEPが届いたら
   「STEP → SLDPRT インポート → 本パイプライン」の経路を通す必要がある
   (STEP由来ボディの `*正面` の向きが設計意図と合うかは別途要検討。
   **向きが合わないと第三角法の3面が意味を成さない** — フェーズ3の前提リスク)
6. **`Defpoints` レイヤ**が出力に含まれる理由は未調査(エンティティは0個)
