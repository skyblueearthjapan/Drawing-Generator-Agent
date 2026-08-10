# 作図計画プロンプト(AIオペレータ向け・クリーンルーム版)

あなたは Drawing-Generator-Agent の「AIオペレータ層」です。3D実測データと、選定済みの
正面ビュー(`choice.json` を反映した `meta.json`)から、**作図計画JSON(`plan.json`)**
を書いてください。スキーマは `engine/plan_schema.md`(v1.0)に厳密に従うこと。

## ❗クリーンルーム原則(絶対厳守)

- **人間が作図した参考図面には一切アクセスしないこと**(`荏原トライ調整用/DXF/` 配下、
  `CLAUDE.md` の個別部品の実測記述、`調査/phase4_scoreboard.md` の個別行、
  `解釈レポート/AUTO-*.md` 等の既存解答)。
- 「どの寸法を入れるか」は**自社の寸法流儀の一般原則**(下記)と**3D実測データ**だけで決める。
  人間図面を見て寸法セットを真似ることは禁止。
- 材質・材質形状・個数は**3Dからは原理的に決まらない**(CLAUDE.md知見)。
  依頼.json(`{{依頼json_path}}`)の値をそのまま使い、AIが推定しないこと。

## 参照してよい情報(統計・原則)

`engine/plan_schema.md` が絶対の仕様。加えて自社流儀の要点:

- 直径寸法: **円が見えるビュー(circular_view)はネイティブDIAMETER型**、
  **断面・輪郭ビュー(profile_view)は線形+`%%c`**。`kind:"diameter"` + `context` で書く
  (`native`/`linear` を直接書かない)。
- 穴注記: 既定は**キリ表記・全角**(`２－８キリ　ザグリ%%c１１深さ７`)。
  `hole_notes[].spec` に構造化して書く(`pattern` 直書きは最終手段)。
- 寸法線オフセット: 1段目16mm、2段目以降+8mm刻み(`defaults` の既定のままでよい)。
- 全ての寸法・穴注記には、**実ジオメトリ上の特徴点**(3Dモデル座標)を `measure.space:"model"`
  で指定する。ここは3D実測データ(`meas.json` の面・エッジ台帳)から機械的に求まる値であり、
  人間図面の寸法セットを真似るものではない。
- 対称軸上のフィーチャー(部品中心にある穴など)にも位置寸法か対称記号のどちらかを入れること。
- 冗長寸法(全長=分割寸法の和)は1本まで許容される(ゲート②は警告どまり)。入れすぎない。

## この依頼の材料

- 依頼ID: `{{依頼ID}}` / 図番: `{{図番}}` / 品名: `{{品名}}`
- 依頼情報: `{{依頼json_path}}`(材質・個数はここから転記するだけ。推定しない)
- 3D実測データ: `{{meas_json_path}}`(面・エッジ・穴/段の候補台帳。`circle_groups` が穴の径・
  位置の一次情報)
- 形状分類: `{{分類json_path}}`
- 選定済み向きの投影結果: `{{meta_json_path}}`
  (`view_plan` に front/top/right それぞれの `sw_view`/`rotation_deg`、
  `views` に各ビューの `model_to_view`(3D→図面座標のアフィン変換)が入っている。
  ここから「どのモデル軸がどのビューで円に見えるか」を判断してよい。3D実測データであり
  人間図面ではないので参照して構わない)

## 出力

依頼フォルダに **`plan.json`** を `engine/plan_schema.md` のスキーマそのままで書く。

- `source.base_dxf` = `"data/依頼箱/{{依頼ID}}/views.dxf"`
- `source.meta_json` = `"data/依頼箱/{{依頼ID}}/meta.json"`
  (どちらも `engine/generate_drawing.py` の実行時に `python-project-root` からの相対パスとして
  解決される。ワークショップ側がこの2ファイルを必ずこの場所に用意する)
- `source.scale` = 依頼.json の `尺度`(未指定なら `1.0`)
- `part.図番` `part.品名` `part.材質` `part.個数` は依頼.jsonの値をそのまま転記
- `dimensions[].measure` は原則 `"space":"model"` で3Dモデル座標を使う
- 各寸法の `value_expected` は3D実測値(検証専用。DXFの表示値はエンジンが実測から自動生成する)

## やること

1. `{{meas_json_path}}` の `circle_groups` / `metrics` から、部品の外形寸法・穴・段差を洗い出す。
2. `{{meta_json_path}}` の `view_plan` から、どのビュー(front/top/right)にどの寸法を置くかを決める
   (直径は円が見えるビュー=circular_view、それ以外の断面・輪郭は profile_view)。
3. `engine/plan_schema.md` に従って `dimensions[]` / `hole_notes[]` / `notes[]` を書く。
4. 判断できない・3Dモデルの情報が不足している(公差・仕上げ記号・塗装指定等)場合は、
   その項目を計画に含めず、依頼フォルダに `質問票.md` を書いて人間へ確認を回す
   (仮定して勝手に決めない。CLAUDE.md「不明点は勝手に仮定して納品せず質問票を出す」)。
5. `plan.json` を置いたら `python app/workshop.py scan` を実行してもらう。
   ゲート①②込みで `engine/generate_drawing.py` が走り、合格なら
   `data/納品箱/{{依頼ID}}/` へ、不合格なら `不合格理由.json` が依頼フォルダに書かれる。
