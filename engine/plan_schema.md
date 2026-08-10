# 作図計画JSON スキーマ v1.0(寸法記入エンジン `engine/dim_engine.py` の入力仕様)

> 位置づけ: `調査/フェーズ3設計メモ_寸法記入エンジン.md` の原案を、実装可能・機械検証可能な形に確定したもの。
> **将来はAIオペレータがこのJSONを自動生成する**。そのため「人間が読んで意図が分かる」ことと
> 「エンジンが決定論的に実行でき、ゲート①で数値検証できる」ことの両立を設計原則とする。
>
> 準拠元: `調査/dimension_style_analysis.md` §8(ルール10項目)、`図枠/dimstyle_spec.json`、
> `調査/ディレクター裁定_フェーズ3A質問票.md`(Q1〜Q5)。
>
> **2026-08-10 追記(後方互換・`schema_version` は "1.0" のまま)**:
> ①尺度(`source.scale`)が 1.0 以外でも使えるようになった(§1.2。寸法値は常にモデル実寸)/
> ②`layout` セクションを追加(§1.3。使用ビュー集合・寸法予約帯によるビュー間隔決定)/
> ③`placement` の段数がビュー間隔の根拠になった(§2.3)。
> **既存の計画JSONは1文字も変えずに従来と同一の出力になる**(回帰確認済み)。

---

## 0. 設計原則(3つ)

1. **表示値は実測駆動、計画値は検証専用**(§8ルール4)。
   `value_expected` はAIの読解値であり、DXFに書き込まれる寸法値ではない。
   DXFの寸法値は測定点(defpoint)から自動計測される。両者が 0.01mm を超えてずれたら
   **エンジンはエラーで停止する**(ゲート①内蔵)。
2. **測定点は必ず実ジオメトリ上の特徴点**。計画に書いた測定点は、合成済みDXF内の実在
   エンティティの特徴点(線分端点・円/円弧の中心と四分点・ポリライン頂点)に
   `snap_tol_mm`(既定0.01mm)以内で一致していなければならない。
   これにより「実測値 = 実ジオメトリの寸法」が構造的に保証される。
3. **スタイルはコーパス流儀に固定**。計画側でDIMSTYLE変数を直接いじることはできない
   (指定できるのは `dimpost` / `tolerance` / `dimdec` 等の意味を持つ項目のみ)。
   1寸法につき専用DIMSTYLEを1つ生成する(XDATAオーバーライドは使わない・コーパス実測0件)。

---

## 1. トップレベル

```jsonc
{
  "schema_version": "1.0",
  "part":   { ... },   // 部品メタ情報(表題欄はcompose側の責務。ここは参照用)
  "source": { ... },   // 入力(合成済みDXF・phase2 meta・尺度)
  "layout": { ... },   // 省略可。使用ビュー集合・寸法予約帯(§1.3)
  "defaults": { ... }, // 配置の既定値(省略可)
  "dimensions": [ ... ],
  "hole_notes": [ ... ],
  "notes":      [ ... ]  // 省略可(塗装注記等の自由注記)
}
```

> **❗レイアウトの正はこの計画JSON**(`source.scale` + `layout`)。compose / dim_engine /
> gate2 / generate_drawing はすべて `dim_engine.plan_layout(plan)` から同じ3点セット
> (尺度・使用ビュー・寸法予約帯)を読む。依頼JSONの `尺度` と食い違うと
> generate_drawing がエラーで止まる(レイアウトがずれた図面を作らないため)。

### 1.1 `part`

| キー | 型 | 必須 | 内容 |
|---|---|---|---|
| `model` | string | ○ | 3Dモデル名(例 `15015-P3-013_ホルダー`) |
| `図番` | string | ○ | 図番 |
| `品名` | string | ○ | 品名 |
| `shape_class` | string | ○ | 形状クラス(`旋盤物` / `板物` / `曲げ物` / `溶接構造`) |
| `材質` `個数` | string/int | − | 参考情報(表題欄記入はcompose側) |

### 1.2 `source`

| キー | 型 | 必須 | 内容 |
|---|---|---|---|
| `base_dxf` | path | ○ | 寸法を足す土台の合成済みDXF(`engine/compose_drawing.py` の出力) |
| `meta_json` | path | ○ | フェーズ2の meta json。**3Dモデル座標→ビュー座標の変換行列 `model_to_view` の供給元** |
| `scale` | float | ○ | 尺度(1.0=実寸、0.5=1:2)。**作図(ジオメトリ)だけが scale 倍**される |

> エンジンは `meta_json` + `scale`(+ `layout`)から compose と同じレイアウト計算を再実行し、
> **モデル3D座標 → 最終A3図面座標** のアフィン変換を各ビューについて復元する
> (`調査/phase2_5_compose_report.md` §5.1 の経路)。計画JSON側に座標変換行列を焼き込まない。

#### ❗尺度と寸法値の関係(2026-08-10 確立。自社流儀)

**寸法値は尺度に関係なく「モデル実寸」を表示する**(人間図面も1:2図面に実寸を記入する)。
実装は **DIMSTYLE の `dimlfac = 1/scale`**(寸法測定値の倍率)。したがって:

- 計画JSONに書く値(`value_expected` / `measure` の座標・`diameter` / `radius` /
  `tolerance` / `cross_check.diameter` / `anchor_check.diameter`)は**すべてモデル実寸mm**。
  尺度を意識して書き換える必要はない(1:1の計画は `source.scale` を変えるだけで1:2になる)
- ゲート①の照合も**モデル実寸空間**で行う(`dim_engine.measure_model_value(dim, scale)` が
  図面上の実測を 1/scale して戻す)。図面座標のまま比較すると scale≠1 で全寸法が落ちる
- 実ジオメトリとの突き合わせ(円の実在確認)だけは図面座標(=実寸×scale)で探す
- 表題欄の尺度欄は compose が `_scale_text(scale)` で全角表記にする(0.5 → 「１：２」)

### 1.3 `layout`(省略可・2026-08-10 追加)

```jsonc
"layout": {
  "views": ["front", "right"],   // 使用ビュー集合。省略=従来どおり4ビュー(後方互換)
  "dim_reserve": true,           // 省略=true。寸法予約帯からビュー間隔を決める
  "dim_band_mm": 5.5             // 省略=5.5。寸法線から外側へ食い出す帯(下記)
}
```

| キー | 既定 | 内容 |
|---|---|---|
| `views` | 省略(=`["front","top","right","iso"]`) | 使うビュー。`front`/`top`/`right`/`iso` の部分集合。**人間図面は2ビュー(正面+右側面)が普通**なので、2ビューで足りる部品は `["front","right"]` と書く |
| `dim_reserve` | `true` | ビュー間隔を寸法の段数から計算する(§1.3a)。`false` で従来の固定15mm |
| `dim_band_mm` | `5.5` | 寸法線から**さらに外側**へ食い出す帯 |

- **使わないビューのエンティティは取り込まれない**(compose の戻り値
  `dropped_view_entity_counts` に除外数が出る)。ビュー数が減った分は
  **残ったビューが紙面中央へバランス配置**される(第三角法の相対整列は保持)。
- 計画の `dimensions[].view` / `hole_notes[].view` に使用ビュー外を書くとエンジンがエラーで止まる。

#### 1.3a ビュー間隔は「寸法の予約帯」から決まる(固定値の握り合わせをやめた)

`compose_drawing.plan_view_reserves(plan)` が `dimensions[].placement` から
**ビュー×辺(above/below/left/right)ごとの予約帯**を作る:

```
予約帯 = max(そのビュー・その辺の寸法線オフセット) + dim_band_mm
       ( オフセット = offset_mm、無ければ first_offset_mm + (level-1) * stack_step_mm )
```

`_layout_targets` は「隣り合うビューの実ジオメトリの隙間 ≧ 双方の予約帯の和」を満たす
最小の間隔を採る(下限は `VIEW_GAP_MM=15`)。これで
**`VIEW_GAP_MM=15 < first_offset_mm=16` に起因する『寸法線が隣のビューへ食い込む』欠陥**が
構造的に消える(反証つき実証: `調査/run_layout_interference_test.py`)。

- `dim_band_mm=5.5` の根拠(実測): 文字が寸法線の外に出るケースで
  `dimgap 0.5 + 文字の実描画高さ 4.5552`(= `dimtxt 4.0 × 1.1388`。ezdxfのフォント実測値で、
  char_height そのままではない)= 5.0552mm。これに余裕0.45mmを足した値
- v1の限界: 予約帯は**寸法だけ**を見込む。穴注記・引出線・自由注記は絶対座標指定のため
  見込んでいない(衝突は `layout.collisions` の報告で拾う)。
  紙面中央への配置も**ビュー幾何だけ**で計算する(外周の寸法帯は含めない)
- ❗2026-08-10: **この限界が実害になった**(盲検 25154-5-08 で下側の寸法が表題欄に重なった)。
  当面の対処は自動再配置ではなく **検出**: `layout.frame_collisions` に
  表題欄・左上ノート・図枠エンティティ・図枠外との衝突を出し、1件でもあれば
  `generate_drawing.py` が **不合格(`layout_ok=false`)** にして納品させない。
  判定は寸法文字ではなく**寸法の描画実体(補助線・寸法線・矢印を含む)**で行う

### 1.4 `defaults`(省略可)

| キー | 既定 | 内容 |
|---|---|---|
| `first_offset_mm` | 16.0 | 1段目の寸法線オフセット(輪郭から。§8ルール8・コーパス中央値16.1) |
| `stack_step_mm` | 8.0 | 2段目以降の加算量(dimdli=8.0) |
| `snap_tol_mm` | 0.01 | 測定点が実ジオメトリ特徴点に一致すべき許容差 |
| `gate_tol_mm` | 0.01 | ゲート①(実測 vs 期待値)の許容差 |
| `diameter_style` | `{"circular_view":"native","profile_view":"linear"}` | `kind:"diameter"` の実装方式(§2.1a) |
| `hole_note` | `{"notation":"phi","width":"zenkaku","separator":" "}` | 穴注記の既定書式(§3) |

> **`diameter_style` / `hole_note` は「ユーザー確認中だった流儀」を分離したパラメータ**である。
> 既定値の実体は `engine/dim_engine.py` の `DIAMETER_STYLE_DEFAULT` / `HOLE_NOTE_DEFAULT`。
> 裁定が変わったらこの2定数(または計画の `defaults`)を差し替えるだけで全計画に反映される。
> 現行既定:
> 円形ビューの外径=ネイティブDIAMETER型(2026-08-09 ユーザー確定) /
> **穴注記=φ(`%%c`)表記・全角(2026-08-10 ユーザー裁定。2026-08-09の「キリ既定」を更新)**。
> キリ表記は**オプションとして温存**しており、`defaults.hole_note` に
> `{"notation":"kiri"}` と書けば従来どおり `２－８キリ　…` になる。

---

## 2. `dimensions[]`

```jsonc
{
  "id": "D33_counterbore",            // 一意ID。専用DIMSTYLE名・レポート行の見出しになる
  "kind": "diameter_linear",          // 下表
  "view": "front",                    // front / top / right / iso
  "measure": { ... },                 // 測定点の指定(§2.2)
  "placement": { ... },               // 配置(§2.3)
  "value_expected": 33.0,             // AI読解値(検証専用。DXFには出ない)
  "tolerance": null,                  // §2.4。null=公差なし
  "dimdec": 2,                        // 省略可。既定2(dimstyle_spec)
  "text_override": null,              // 省略可。§2.5(角度・参考寸法・PCD/OD・%%p対称公差の4用途限定)
  "cross_check": { ... },             // 省略可。§2.6 実ジオメトリとの独立照合
  "comment": "ザグリ径φ33"            // 省略可。人間可読メモ
}
```

### 2.1 `kind`

| kind | DXF実装 | dimpost | 根拠 |
|---|---|---|---|
| `linear` | rotated linear DIMENSION(dimtype=0) | `""` | §8ルール1・コーパス855本 |
| `diameter` | **`context` と `defaults.diameter_style` で下2つへ解決**(§2.1a) | `"%%c<>"` | 裁定Q1更新(2026-08-09) |
| `diameter_linear` | rotated linear DIMENSION | `"%%c<>"` | §8ルール1(輪郭・断面ビューの径) |
| `diameter_native` | DIAMETER DIMENSION(dimtype=3) | `"%%c<>"` | 円形ビューの外径(人間図面 GMM006 実測) |
| `radius` | RADIUS DIMENSION(dimtype=4) | `"R<>"`(複数箇所は `"2-R<>"` 等) | §8ルール2 |
| `angle` | ANGULAR DIMENSION(dimtype=2) | `""` | §8ルール3(値は必ず `text_override` で明示) |

`aligned`(dimtype=1)は使わない(コーパス0件)。斜め辺は `linear` + `direction` で測る。

#### 2.1a `kind:"diameter"` の解決(推奨の書き方)

```jsonc
{"kind": "diameter", "context": "circular_view", ...}   // 円が見えるビューの外径 → native
{"kind": "diameter", "context": "profile_view",  ...}   // 断面・側面の径      → linear
```

`defaults.diameter_style` が `{"circular_view":"native","profile_view":"linear"}` のとき上記に解決される。
**AIオペレータは実装方式(native/linear)ではなく文脈(context)を書くこと。** 方式は流儀パラメータであり、
裁定が変われば `defaults.diameter_style` の差し替えだけで全図面が追随する。
`diameter_native` / `diameter_linear` を直接書くと文脈解決を迂回して強制指定になる。

`diameter_native` の `measure` は特殊で、`p1`=円中心 + (`diameter` か `p2` か `value_expected`)、
任意で `leader_angle`(寸法線の角度deg・既定45)。任意角の円周点は特徴点にならないため
**snap検証は中心のみ**とし、代わりに「そのビューに中心一致・直径一致の実在円がある」ことを
必須検証する(レポートの `circle_check`)。

### 2.2 `measure` — 測定点の指定方法

**2通り**を用意する。同一計画内で混在してよい。

#### (a) ビュー座標直接指定 `"space": "view"`

```jsonc
"measure": {
  "space": "view",
  "p1": [141.8414, 82.9080],
  "p2": [141.8414, 115.9080],
  "direction": "vertical"       // linear/diameter_linear のみ: horizontal | vertical | <角度deg>
}
```

最終A3図面座標(mm)。合成済みDXFを読んで測定点を決めた場合はこちら。

> **❗`space:"view"` はレイアウトが変わると壊れる**(2026-08-10 実害)。図面座標は
> 尺度・使用ビュー・寸法予約帯で動くため、`source.scale` や `layout` を変えた瞬間に
> 実ジオメトリから外れて `snap` / `anchor_check` が落ちる。
> **`space:"model"` で書けば座標変換はエンジンが追随する**ので、原則モデル座標で書くこと
> (引出線 `hole_notes[].leader` / `text_insert` も同じ。`notes[].insert` だけは
> 図枠基準の絶対座標で構わない)。

#### (b) 3Dモデル座標指定 `"space": "model"`

```jsonc
"measure": {
  "space": "model",
  "p1": [-40.0, 16.5, 0.0],
  "p2": [-40.0, -16.5, 0.0],
  "direction": "vertical"
}
```

SolidWorksモデルの3D座標(mm)。エンジンが `source.meta_json` の
`views.<view>.model_to_view`(列優先12+要素)と compose の再レイアウト変換を合成して
図面座標へ変換する。AIオペレータが3Dモデルの解釈から直接計画を書く場合はこちらが本命。

- `direction`:
  - `horizontal` → rotated dimension の `angle=0`
  - `vertical` → `angle=90`
  - 数値 → その角度(度)方向に投影して測る
- `radius` の場合は `p1`=円中心、`p2`=円周上の点(または `radius` を直接指定)。
  **`radius` / `diameter` のスカラー値は `space` に関係なく常にモデル実寸mm**
  (エンジンが scale 倍して作図する)
- `angle` の場合は `vertex` / `p1` / `p2`(2辺の端点)と `base`(円弧を通す点)を指定する(§2.2a)

> **どちらの指定方法でも、変換後の点は §0-2 のスナップ検証を受ける。**
> 実ジオメトリ上に無い点を指定した計画は必ず落ちる。

#### 2.2a `kind:"angle"` の書き方(2026-08-10 改善サイクル3で実装)

```jsonc
{
  "id": "A30R", "kind": "angle", "view": "front",
  "measure": {
    "space": "model",
    "vertex": [49.0604878, 0, 24.8609842],   // 角の頂点(実在の特徴点であること)
    "p1":     [34.3062494, 0, 16.3426207],   // 1辺目の端点 -> line1 = (vertex, p1)
    "p2":     [-49.0604878, 0, 24.8609842],  // 2辺目の端点 -> line2 = (vertex, p2)
    "base":   [37.47, 0, 21.755]             // 寸法円弧を通す点(頂点まわりの半径を決める)
  },
  "placement": {"side": "below", "level": 1},  // ❗角度寸法は予約帯を作らない(下記)
  "value_expected": 30.0,                      // **度**。mmではない
  "text_override": "\\A1;３０%%d"               // 必須(§8ルール3)。度は %%d(制御コード)
}
```

- **測定値の単位は度**。`value_expected` も許容差も度で扱う
  (`defaults.angle_tol_deg` 既定 **0.05度** = 円周等分/群配置の検算と同じ厳しさ)。
  尺度換算(`dimlfac`)は**掛けない**(角度は長さではない)。
- 実測は `dim_engine.measure_angle_deg()` が defpoint から再計算する。
  値は **line1 から line2 へ反時計回り(CCW)に測った角**。
  ❗`space:"model"` で書いた点は、ビューによって軸の符号が反転する
  (例: front で Z が紙面yの -1 倍)。**紙面上でどちら回りになるかで p1/p2 の順序が決まる**ので、
  期待する値にならない場合は p1 と p2 を入れ替える。
- **`text_override` が必須**なので、そのままでは「描いた角度文字が実測角と食い違っていても
  誰も気付かない」穴になる。エンジンは `parse_angle_text_value()` で文字から数値を復元して
  実測角と突き合わせる(ゲート①・独立検証の両方)。`３０%%d` `30度` `30` を解釈する。
- **角度寸法は寸法予約帯(§1.3a)を作らない**(頂点まわりの円弧でビュー輪郭からのオフセットではない)。
  `compose_drawing.plan_view_reserves` が `kind=="angle"` を除外している。
- ゲート②はこの角度寸法を**傾斜フィーチャーの方向指定**としてのみ使う(§5.1)。
  どの傾斜フィーチャーにも紐づかない角度寸法は「宙に浮いた寸法」警告になる。

### 2.3 `placement`

```jsonc
"placement": {
  "side": "left",        // above | below | left | right : ビュー輪郭のどちら側に寸法線を置くか
  "level": 1,            // 段数(1=輪郭から first_offset_mm、2段目以降 +stack_step_mm)
  "offset_mm": null      // 省略可。指定するとlevelを無視して輪郭からの距離を直接指定(密集箇所用)
}
```

- 寸法線の位置 = ビューの**実ジオメトリ外接矩形**の指定辺 ± オフセット。
  (`level=1` → 16mm、`level=2` → 24mm、`level=3` → 32mm …)
- `side` は寸法の向きも決める:`above`/`below` → 水平寸法線、`left`/`right` → 垂直寸法線。
  `measure.direction` と矛盾する場合はエンジンがエラーにする。
- **`side` と段数(`level`/`offset_mm`)は同時に『ビュー間隔』の根拠にもなる**(§1.3a)。
  隣のビュー側へ何段積んでも、ビュー間隔がその分だけ広がるので**干渉を避けるために
  `offset_mm` を人手で詰める必要はない**(TEST-002 の `offset_mm: 11` は修正前の名残)。

### 2.4 `tolerance`

**裁定Q2: エンジンは計画で明示指定された公差だけを付ける。機械推定はしない。**

```jsonc
// (1) 片側限界公差(はめあい系) — ネイティブ dimtol 方式
"tolerance": {"mode": "limit", "upper": 0.0, "lower": -0.021, "dec": 3}

// (2) 対称公差 — text_override の %%p 方式
"tolerance": {"mode": "symmetric", "value": 0.026}
```

- `mode: "limit"` → DIMSTYLE に `dimtol=1` / `dimtp=upper` / `dimtm=|lower|` /
  `dimtfac=0.625` / `dimtdec=dec` / `dimtolj=1` を設定。
  **ゼロ側は描画後に text を「0」へ整形する**(裁定の追記。`dimtzin` では再現不可)。
- `mode: "symmetric"` → `text_override` を `"<値>%%p<公差>"` 形式で自動生成する。
- ISOはめあい記号(H7/g6等)は使用禁止(コーパス0件)。

### 2.5 `text_override`

§8ルール4より **4用途に限定**。それ以外で指定するとエンジンが警告を出す。

| 用途 | 例 |
|---|---|
| 角度寸法 | `"\\A1;120\\U+00B0"` |
| 参考寸法 | `"\\A1;(45)"` |
| PCD/OD ラベル | `"\\A1;(PCD108)"` |
| 対称公差 | `"\\A1;20%%p0.026"`(`tolerance.mode=symmetric` から自動生成される) |

> ❗**括弧で囲んだ参考値(`(45)` / `(ＰＣＤ１０８)`)は、検証側で「寸法文字の数値 vs 実測値」の
> 照合対象から外れる**(`dim_engine.is_reference_text`)。代わりに
> **「計画が指定した文字がそのままDXFに描かれているか」**を検査する(`text_override_applied`)。
> 括弧を付けないと数値照合が走る(対称公差 `20%%p0.026` は数値照合されるのが正しい)。
> 2026-08-10: 括弧付き参考値から数値を読んで実測と比較し、
> **盲検4件を誤って不合格にした**バグを修正した(調査/blind_test_report.md §4)。

### 2.6 `cross_check` — 実ジオメトリとの独立照合(ゲート①(b))

測定点のスナップ検証に加えて、**別ビューの実ジオメトリ**と突き合わせる任意の追加検証。

```jsonc
"cross_check": {
  "type": "circle_in_view",
  "view": "right",              // 照合先ビュー
  "center": [237.5, 99.4080],   // 図面座標
  "diameter": 33.0              // 実測寸法値と一致すべき円の直径
}
```

エンジンは指定ビュー内に `center` 一致・`diameter` 一致(0.01mm以内)の CIRCLE/ARC が
実在することを確認し、さらに**その実半径×2 == 寸法の実測値**であることを検証する。
(例: 縦断面図に線形寸法で入れたφ33が、正面図の実在円 r=16.5 と一致することを独立に確認)

---

## 3. `hole_notes[]`

**推奨は `spec`(構造化指定)。書式は `defaults.hole_note` が決める。**

```jsonc
{
  "id": "N_bolt_holes",
  "view": "right",
  "spec": {"count": 2, "drill": 8, "counterbore": {"dia": 11, "depth": 7}, "placement": "PCD60"},
  // -> phi/zenkaku(既定): '\A1;２－%%c８　ザグリ%%c１１深さ７\PＰＣＤ６０'
  // -> kiri/zenkaku     : '\A1;２－８キリ　ザグリ%%c１１深さ７\PＰＣＤ６０'(2026-08-09までの既定)
  // -> phi/hankaku      : '\A1;2-%%c8 ザグリ%%c11深さ7\PPCD60'
  "leader": { ... }
}
{
  "id": "N_tap",
  "view": "right",
  "spec": {"thread": "M10", "depth": 20},   // -> '\A1;Ｍ１０深さ２０'
  "leader": { ... }
}
```

`spec` のキー: `count`(個数) / `drill`(ドリル径) / `thread`(ねじ呼び) / `depth`(深さ) /
`counterbore:{dia,depth}`(ザグリ) / `placement`(2行目=PCD等) / `extra_lines[]`。
`style` を書くとその注記だけ書式を上書きできる(`{"notation":"kiri","width":"hankaku"}`)。

> ❗**`placement` に `PCD<値>` を書くと、ゲート②が「その円周上の穴中心の位置は決まっている」と
> 判定できる**(円周等分穴群対応・2026-08-10)。ただし採用されるのは
> **注記のPCD値と個数が実ジオメトリと一致し、かつ穴が円周等分に並んでいることを
> エンジンが検算できた場合だけ**。PCD値が実物と違う注記や等配でない穴群は却下され、
> 各穴の位置は従来どおり未指定(=ゲート②不合格)になる。

### 3.1 円周上の「群(クラスタ)配置」穴群の書式(2026-08-10 改善サイクル3で追加)

**円周等分でない**穴群(数個を1組にして円周上に何組か配置する形)は、
`ＰＣＤ` に加えて**群構成と群内の振分角**を書く。これで配置が一意に決まる。

```jsonc
{
  "id": "N_h9", "view": "right",
  "spec": {
    "count": 12, "drill": 9, "counterbore": {"dia": 14, "depth": 8},
    "placement": "PCD142",
    "extra_lines": ["4個×3群", "振分角12-18-12"]
  },
  // -> '\A1;１２－%%c９　ザグリ%%c１４深さ８\PＰＣＤ１４２\P４個×３群\P振分角１２－１８－１２'
}
```

書式(全角。半角へ正規化してから解釈する):

    ＰＣＤ<値>   <n>個×<m>群   振分角<a1>－<a2>－…－<a(n-1)>

- `<n>個×<m>群` = 「n個を1組とした群が m 組」。総数は n×m で、注記先頭の個数と一致すること。
- `振分角` は**群内の隣り合う穴の角度**を n−1 個並べる(区切りは全角ハイフン `－`)。
- **群間の角は書かない**。`360/m − Σ(振分角)` で一意に決まるため
  (例: 4個×3群・振分角12-18-12 → 群間角 = 120 − 42 = **78度**)。
- 区切り記号は `×`(U+00D7)/`ｘ`/`＊` のいずれでもよい。n=2 のときは振分角1個。

> ❗**採用条件(=反証が効く条件)は等配PCDと同じ思想で、全部を実ジオメトリで検算する**:
> (1) 総数(n×m)が同径の穴の実数・注記先頭の個数と一致
> (2) 全穴中心が共通中心から等距離で、その直径が注記のＰＣＤと **0.01mm 以内**で一致
> (3) 隣接角の並びが `[a1..a(n-1), 群間角] × m` と**巡回シフト込みで 0.05度以内**で一致。
> **並びの順序も見る**(角度の集合だけ合わせた偽装は落ちる)。**反転(逆回り)は許さない**。
> どれか1つでも合わなければ却下され、各穴の位置は未指定(=ゲート②不合格)になる。
> 位相(基準角)は等配PCDと同じく判定対象外。
> 検算の実装は `gate2_completeness.find_cluster_groups()`、反証は
> `調査/run_cycle3_falsification.py` の G2〜G7。

`pattern` を直接書けば強制指定になる(`spec` より優先):

```jsonc
{
  "id": "N_bolt_holes",
  "view": "right",
  "pattern": "\\A1;２－８キリ　ザグリ%%c１１深さ７\\PＰＣＤ６０",
  "leader": {
    "space": "view",
    "points": [[271.389, 103.297], [288.0, 120.0], [298.0, 120.0]]
  },
  "text_insert": [299.0, 120.0],
  "attachment": "bottom-left",
  "anchor_check": {                 // 省略可: 引出線の始点が実在ジオメトリ上にあることの検証
    "type": "on_circle", "view": "right", "center": [267.5, 99.4080], "diameter": 11.0
  },
  "comment": "φ8通し・φ11ザグリ深さ7・2箇所・PCD60"
}
```

- `pattern` は §8ルール7 の書式。**φは必ず `%%c` 制御コード**(Unicodeのφ/Φは禁止)。
  `\P` で改行し、1行目=個数+径+加工種別+深さ、2行目=配置(PCD/振り角)。
- **既定はφ(`%%c`)表記・全角**(2026-08-10 ユーザー裁定。2026-08-09の「キリ既定」を更新)。
  ドリル穴は `<個数>－%%c<径>`(全角)、ザグリ径も `%%c`(制御コードは半角のまま)。
  根拠: 盲検10部品の人間図面に**キリ表記は1件も無かった**(調査/blind_test_report.md §6.2)。
  キリ表記へ戻す場合は `defaults.hole_note` を `{"notation":"kiri","width":"zenkaku"}` にする。
- `leader.points` は引出線の折れ点列(最後の1本が水平のランディング)。`space` は `view`/`model`。
- `anchor_check` を書くと、引出線始点が指定円の円周上(0.01mm以内)にあることを検証する。

---

## 4. `notes[]`(省略可)

```jsonc
{
  "id": "N_paint",
  "text": "注記．\\P　塗装色は工番指定色とする。\\P　指示無き角部はＣ０．２、隅部はＲ２以下とする。",
  "insert": [295.0, 72.0],
  "attachment": "top-left",
  "height": 3.5
}
```

§8ルール10(塗装注記テンプレート)。表題欄・自由注記は**全角**(裁定Q5)。

---

## 5. 傾斜フィーチャー(傾斜穴)の書き方(2026-08-10 改善サイクル3で実装)

### 5.1 投影面内で傾いた穴 —— `kind:"angle"` + 基準点

投影が**平行な斜線2本**(=傾斜円筒の見え掛かり輪郭)になる穴は、
角度寸法を1本入れれば1フィーチャーとして扱われる。ゲート②の採用条件:

1. 平行な斜線2本の**垂直距離が、図面でカバー済みの直径**(寸法か穴注記で決まっている径)と
   0.01mm 以内で一致する
2. **その穴に紐づいた角度寸法**が図面にある。紐づけの条件は4つ全部:
   (a) 頂点が2本のどちらかの**実在端点**と一致 (b) 一方の辺が斜線と平行(=傾斜方向)
   (c) もう一方の辺が**ビューの軸方向**(=基準方向) (d) 実測角が2辺の成す角と一致
3. 軸線(2本の中線)が**到達済みの位置ノードの組**を通る(=基準点が図面で決まっている)
4. 輪郭線の各端点が「**図面でカバー済みの円**(中心も到達済み)との交点」として説明できる

> 実例(25154-3-09): 外径φ110・内段φ76 の板に30度で開いた横穴φ6が2本。
> 角度寸法を2本(左右それぞれ)入れると、軸線が部品中心(X=0,Z=0)を通ることと
> 端点がφ76/φ110 上にあることをエンジンが検算して未指定12件→0件になる。
> 検算の実装は `gate2_completeness.find_inclined_features()` /
> `_apply_inclined_derivations()`、反証は `調査/run_cycle3_falsification.py` の H1〜H7。

❗**角度寸法の測定点も §0-2 のスナップ検証を受ける**ので、実在の特徴点しか使えない。
穴の中心線は投影ジオメトリに存在しないため、**輪郭線の端点**を頂点に取り、
基準辺は「同じ高さにある対称位置の実在点」へ引くのが実際に通る書き方
(25154-3-09 の A30R/A30L がその形)。

### 5.2 未対応: 投影面から外れて傾いた穴(E2b)

軸が投影面から外れて傾いている穴は **上記の条件1を原理的に満たさない**
(投影幅が直径そのものではなく `d/sinθ` になるため)。軸の3次元方向は2つの角度で決まるので、
**2ビューの角度寸法をビュー横断で合成する機構**が要る。25154-2-06 がこの型で未対応
(詳細は `data/依頼箱/BLIND-25154-2-06/質問票.md`)。

---

## 6. 未対応(将来拡張)

| 項目 | 状況 |
|---|---|
| `tap_symbols[]`(JIS B 0205 同心円+中心線・§8ルール9) | 本部品にタップ穴が無いため v1.0 では未実装。裁定Q4-1でフェーズ3-B対象だが、該当ジオメトリで実証してから入れる |
| 仕上げ記号(JIS B 0031) | 裁定Q4-2でフェーズ3-C |
| 溶接記号 | 裁定Q4-3で保留 |
| 中心線・PCD円の自動生成(§5.2) | ビュー側(compose/SW)の責務として整理待ち |
| 自動レイアウト最適化 | 2026-08-10: **ビュー間隔は計画駆動(寸法予約帯)で自動化済み**(§1.3a)。残る未対応は「段数・辺の自動決定」「注記/引出線の予約」「紙面中央寄せに寸法帯を含める」 |
| 尺度の自動選択 | `source.scale` は計画側の明示指定のみ(A3に収まらなければ compose が例外)。bboxから 1/2・1/2.5・1/5 … を自動で選ぶ機構は未実装 |

---

## 7. エンジンの出力(検証レポート)

`apply_plan()` は寸法入りDXFを保存すると同時に、以下を含む dict を返す。

```jsonc
{
  "out_path": "...",
  "gate1": [ {"id": "...", "kind": "...", "expected": 33.0, "measured": 33.0,
              "diff_mm": 0.0, "text": "%%c33", "snap": [0.0, 0.0],
              "cross_check": {"ok": true, "found_diameter": 33.0, "diff_mm": 0.0},
              "ok": true}, ... ],
  "gate1_ok": true,
  "dimstyles": { "<id>": {"name": "GEN001", "effective": {...}} },
  "style_check": {"ok": true, "mismatches": []},
  "layout": {"text_boxes": {...}, "collisions": [...],
             "geom_boxes": {...},        // 寸法の描画実体(virtual_entities展開)の外接矩形
             "frame_collisions": [{"id": "...", "zone": "title_block", "box": [...]}, ...],
             "frame_ok": true},          // 偽なら generate_drawing が不合格にする(§1.3a)
  "scale": 0.5,                  // 適用した尺度
  "dimlfac": 2.0,                // = 1/scale(全DIMSTYLEに入る。寸法値=モデル実寸の担保)
  "views": ["front", "right"],   // 実際に使ったビュー
  "view_reserves": {"front": {"above":0.0,"below":21.5,"left":21.5,"right":21.5}, ...},
  "warnings": [...]
}
```

`gate1_ok` が偽なら `apply_plan()` は例外を送出し、DXFを保存しない(不合格品を出さない)。
`gate1[].expected` / `measured` / `diff_mm` は**モデル実寸mm**(尺度を戻した値)。

compose 側の戻り値にも `views` / `dropped_view_entity_counts` /
`layout`(`gap_x_mm` `gap_y_mm` `group_wh_mm` `reserves`)が入る。
