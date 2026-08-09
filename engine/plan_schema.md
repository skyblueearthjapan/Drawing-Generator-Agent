# 作図計画JSON スキーマ v1.0(寸法記入エンジン `engine/dim_engine.py` の入力仕様)

> 位置づけ: `調査/フェーズ3設計メモ_寸法記入エンジン.md` の原案を、実装可能・機械検証可能な形に確定したもの。
> **将来はAIオペレータがこのJSONを自動生成する**。そのため「人間が読んで意図が分かる」ことと
> 「エンジンが決定論的に実行でき、ゲート①で数値検証できる」ことの両立を設計原則とする。
>
> 準拠元: `調査/dimension_style_analysis.md` §8(ルール10項目)、`図枠/dimstyle_spec.json`、
> `調査/ディレクター裁定_フェーズ3A質問票.md`(Q1〜Q5)。

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
  "defaults": { ... }, // 配置の既定値(省略可)
  "dimensions": [ ... ],
  "hole_notes": [ ... ],
  "notes":      [ ... ]  // 省略可(塗装注記等の自由注記)
}
```

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
| `scale` | float | ○ | compose に渡した尺度(モデル座標→図面座標変換の再構成に必要) |

> エンジンは `meta_json` + `scale` から compose と同じレイアウト計算を再実行し、
> **モデル3D座標 → 最終A3図面座標** のアフィン変換を各ビューについて復元する
> (`調査/phase2_5_compose_report.md` §5.1 の経路)。計画JSON側に座標変換行列を焼き込まない。

### 1.3 `defaults`(省略可)

| キー | 既定 | 内容 |
|---|---|---|
| `first_offset_mm` | 16.0 | 1段目の寸法線オフセット(輪郭から。§8ルール8・コーパス中央値16.1) |
| `stack_step_mm` | 8.0 | 2段目以降の加算量(dimdli=8.0) |
| `snap_tol_mm` | 0.01 | 測定点が実ジオメトリ特徴点に一致すべき許容差 |
| `gate_tol_mm` | 0.01 | ゲート①(実測 vs 期待値)の許容差 |

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
| `diameter_linear` | rotated linear DIMENSION | `"%%c<>"` | **§8ルール1・裁定Q1**(83:4でネイティブDIAMETER型より優勢) |
| `radius` | RADIUS DIMENSION(dimtype=4) | `"R<>"`(複数箇所は `"2-R<>"` 等) | §8ルール2 |
| `angle` | ANGULAR DIMENSION(dimtype=2) | `""` | §8ルール3(値は必ず `text_override` で明示) |

`aligned`(dimtype=1)は使わない(コーパス0件)。斜め辺は `linear` + `direction` で測る。

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
- `radius` の場合は `p1`=円中心、`p2`=円周上の点(または `radius` を直接指定)
- `angle` の場合は `vertex` / `p1` / `p2`(2辺の端点)を指定する

> **どちらの指定方法でも、変換後の点は §0-2 のスナップ検証を受ける。**
> 実ジオメトリ上に無い点を指定した計画は必ず落ちる。

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

```jsonc
{
  "id": "N_bolt_holes",
  "view": "right",
  "pattern": "2-%%c8ザグリ%%c11深さ7\\PPCD60",  // %%cでφ。半角統一(裁定Q5)
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
- 半角ハイフン・半角数字に統一(裁定Q5「穴注記は半角」)。
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

## 5. 未対応(将来拡張)

| 項目 | 状況 |
|---|---|
| `tap_symbols[]`(JIS B 0205 同心円+中心線・§8ルール9) | 本部品にタップ穴が無いため v1.0 では未実装。裁定Q4-1でフェーズ3-B対象だが、該当ジオメトリで実証してから入れる |
| 仕上げ記号(JIS B 0031) | 裁定Q4-2でフェーズ3-C |
| 溶接記号 | 裁定Q4-3で保留 |
| 中心線・PCD円の自動生成(§5.2) | ビュー側(compose/SW)の責務として整理待ち |
| 自動レイアウト最適化 | v1.0は「段数明示+衝突検出(報告)」まで。フェーズ4で改善 |

---

## 6. エンジンの出力(検証レポート)

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
  "layout": {"text_boxes": {...}, "collisions": [...]},
  "warnings": [...]
}
```

`gate1_ok` が偽なら `apply_plan()` は例外を送出し、DXFを保存しない(不合格品を出さない)。
