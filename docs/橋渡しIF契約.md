# 橋渡しI/F契約 — Drawing-Generator-Agent と SOLIDIFY の境界

> 作成: 2026-08-11(Z2工房統合手順書_2026-08-11.md M3-2 の一部として、部品図作成agentリポジトリ側で作成)
> 読者: SOLIDIFY側の橋渡しモジュール(`phase2/worker/drawing2d.py` 相当)を書く人・エージェント。
> 位置づけ: **`app/workshop.py`(以下 workshop.py)の CLI・ファイル契約を、現状のコードから
> 機械読みできる精度で書き起こしたもの**。理想像ではなく実装済みの挙動を記述する
> (現状と手順書側の記述が食い違う場合は、この文書は現状を優先して書いている。§9参照)。
> 検証: 2026-08-11、環境変数を一時ディレクトリへ向けた `new`→`scan`(×2回)で
> 受付済→計測中→候補提示中→計画待ち→生成中→合格 の全遷移を実機(SW2023)で1周確認済み
> (実行ログはディレクターの検証記録を参照)。
>
> **追補 2026-08-11(SOLIDIFY 契約v3.6 の M3/M4 実装を受けて)**: §1(環境変数の使い分け)・
> §7(質問票の `topic` 見出しと **`添付:` 必須**)・§9.1(SOLIDIFY側の実装状況と状態写像)を追加。
> **この3節が、橋渡しの実装(`phase2/worker/drawing2d.py`)と現に噛み合っている契約**である。

---

## 0. 前提・非対象範囲

- **部品図作成agent の中身(engine/・app/prompts/*.md)は書き換えない**。橋渡しは
  **CLI(`python app/workshop.py <cmd>`)経由でのみ**呼ぶ(このリポジトリの安全規約)。
- SolidWorks の同時1占有はこのリポジトリ側では保証しない。**橋渡し側(SOLIDIFYワーカーの
  単一claimループ)が同時に1ジョブしか動かさないことで担保する**(手順書§3.1-2)。
- 向き選択(`choice.json`)と作図計画(`plan.json`)は **`claude -p` + このリポジトリの
  `app/prompts/orientation_prompt.md` / `plan_prompt.md`** で作る想定(手順書§3.2 段3/段5)。
  この文書はそれらのプロンプトの中身には立ち入らない(ファイル参照で二重管理しない)。

---

## 1. 環境変数

| 変数 | 既定値 | 意味 |
|---|---|---|
| `SOLIDIFY_DRAWING_ROOT` | このリポジトリ自身(`app/workshop.py` の実位置から2階層上) | **`data/`(依頼箱・納品箱)と `台帳.md` の置き場所**。橋渡し実行ではここを別ディレクトリ(例: Z2配置後のリポジトリ自身のパス、またはSOLIDIFY側が用意する作業ディレクトリ)へ向ける。**`engine/`・`app/prompts/`・`調査/phase5_ai_operator/*.py` 等コード資産の呼び出し位置には影響しない**(常にコードの実ファイル位置基準)。`app/workshop.py:57-65` |

> ❗**同じ変数名を SOLIDIFY 側は「DGA リポジトリの場所」の意味でも使う**
> (契約v3.6 変更4「DGA の場所は環境変数 `SOLIDIFY_DRAWING_ROOT`、既定
> `C:\workshop\Drawing-Generator-Agent`。ハードコード禁止」)。**矛盾ではない**。
> 実装(`phase2/worker/drawing2d.py`)では次のように使い分けている:
>
> | 誰が読むか | 値 | 意味 |
> |---|---|---|
> | 橋渡し(SOLIDIFYワーカー)の**自プロセス** | `C:\workshop\Drawing-Generator-Agent` | **DGA リポジトリの場所**。`app/workshop.py` と `app/prompts/*.md` を探すのに使う |
> | 橋渡しが起動する**子プロセス**(`python app/workshop.py …`) | `納品箱/{job_id}/_work/dga` | **そのジョブ専用のデータ置き場所**(本表のとおりの意味) |
>
> **子プロセスへ渡す値をジョブ専用にしているのは §3.2 の「scan は全件を回す」への対処**である。
> 依頼箱にそのジョブ1件しか置かないので、`scan` が他の依頼を巻き込むことが構造的に起こらない
> (= 手順書§M3-2 が求めた「依頼ID指定の scan」と同じ効果を、**このリポジトリを変更せずに**得ている)。
> したがって **`scan` に依頼ID引数を足す改修は当面不要**。
| `SOLIDIFY_SW_PROGID` | 未設定(自動判別: `.31→.29→.30→.32→.28` の順に試す) | SolidWorks ProgID を固定したい時に使う(例 Z2で明示するなら `SldWorks.Application.29`)。`engine/sw_compat.py:34-38` |
| `OMC_WS_TIMEOUT_SCALE` | `1` | `workshop.py` がサブプロセス(計測・候補投影・生成等)に与えるタイムアウトの倍率。Z2実機が開発機より遅い場合はこれを上げる。既定値(倍率1)での上限: 計測1800s/候補投影1800s/候補PNG300s/向き反映投影900s/生成900s。`app/workshop.py:68-73` |

**設定例(PowerShell)**:
```powershell
$env:SOLIDIFY_DRAWING_ROOT = "C:\workshop\Drawing-Generator-Agent"
python app/workshop.py scan
```
`SOLIDIFY_DRAWING_ROOT` 未設定時は**このリポジトリ自身の `data/` と `台帳.md`**を使う
(=開発機で人間が使っている本番相当データと同じ場所。**橋渡しのテスト・CI では必ず設定すること**。
未設定のままテストを回すと開発機の実データを壊す)。

---

## 2. ディレクトリ・ファイル配置規約

```
$SOLIDIFY_DRAWING_ROOT/
├─ data/
│  ├─ 依頼箱/<依頼ID>/         1依頼1フォルダ。以下すべてこの中
│  │  ├─ <モデルファイル>       STEP/STP/SLDPRT(拡張子小文字判定・1件のみ)
│  │  ├─ 依頼.json              受付情報(§4)
│  │  ├─ status.json            状態機械(§5)
│  │  ├─ meas.json              計測結果(計測中で生成。§6.1)
│  │  ├─ 分類.json              形状クラス判定(計測中で生成)
│  │  ├─ 候補設定.json / 候補/ / 候補.png   候補提示(候補提示中で生成。§6.2)
│  │  ├─ choice.json            向き選択(AIオペレータが置く。§6.3)
│  │  ├─ views.dxf / meta.json  選択反映後の投影(計画待ち中、choice.json検出後に自動生成)
│  │  ├─ plan.json              作図計画(AIオペレータが置く。schema: engine/plan_schema.md)
│  │  ├─ 質問票.md              人間確認が要る時にAIオペレータが置く(§7)
│  │  ├─ 生成/                  生成結果(生成中で作成。§6.4)
│  │  │  ├─ <out_stem>.dxf / .png / _result.json   (合格時)
│  │  │  └─ 不合格/<out_stem>.dxf / .png            (不合格時。result.jsonは移動しない)
│  │  ├─ 不合格理由.json        不合格時のみ(§6.5)
│  │  └─ 生成完了.json          内部の復旧マーカー(橋渡しは無視してよい)
│  └─ 納品箱/<依頼ID>/          合格時のみ。deliver()がコピーする(§8)
└─ 台帳.md                      人間可読の処理台帳(upsert。§10。橋渡しは書き込まない)
```

`<out_stem>` = `<安全化した図番>_<品名>`(図番中の `\/:*?"<>|` は `_` に置換。
品名省略時はモデルファイル名の `_` 以降、無ければステム全体)。`app/workshop.py:308-314`。

---

## 3. CLIコマンド契約

すべて `python app/workshop.py <cmd>`(cwd任意・スクリプト自身の絶対パスで解決するため)。
**stdout/stderrは UTF-8 固定**(`sys.stdout.reconfigure(encoding="utf-8", errors="replace")`。
`app/workshop.py:1053`)。橋渡し側は `subprocess.run(..., encoding="utf-8")` で受けること。

### 3.1 `new`(受付。任意 — §3.5 の直接ファイル生成でも代替可)

```
python app/workshop.py new <依頼ID> --model <path> --材質 <str> [--図番 <str>] [--品名 <str>]
    [--装置名 <str>] [--材質形状 <str>] [--個数 <num>] [--尺度 <str>] [--製図者 <str>]
    [--連番 <str>] [--note <str>] [--force]
```

- `<依頼ID>` = `data/依頼箱/<依頼ID>/` フォルダ名。**橋渡し側のジョブIDをそのまま使ってよい**
  (英数字・ハイフン推奨。フォルダ名として不正な文字は避ける)。
- 必須: `--model`(存在確認・拡張子 `.step/.stp/.sldprt` のみ)、`--材質`(**3Dから決めない**)。
- **CLIフラグ名が日本語**(`--図番` 等)。Windows のコンソール経由だと文字化けの実績がある
  (Z2工房統合手順書§2.1)。**橋渡しが Python から `subprocess.run([...])` で argv リストとして
  渡す分には問題ない**(シェル文字列経由を避ける)。不安なら §3.5 の直接ファイル生成を使う。
- 終了コード: `0`=成功 / `1`=失敗(フォルダ既存で`--force`無し、モデル無し、拡張子不正、
  尺度/個数の値不正、材質未指定)。失敗時は日本語エラーメッセージがstdoutに出る(構造化なし)。
- 副作用: `依頼.json` 生成 + モデルファイルを依頼フォルダへコピー。**`status.json` はまだ作らない**
  (最初の `scan` で `受付済` として生成される)。

### 3.2 `scan`(状態機械を1呼び出しぶん進める。ポーリングで使う想定)

```
python app/workshop.py scan
```

- 引数なし。`data/依頼箱/` 配下の**全依頼**を1回ずつ走査し、各依頼について
  「進められる状態遷移を(最大20回まで)連続で」進める(`app/workshop.py:854-884`)。
  1回の `scan` 呼び出しで複数状態を一気に通過し得る(例: 検証済み).
- **終了コードは常に `0`**(個々の依頼のエラーは握り潰して次の依頼へ進む設計。
  失敗検知は `status.json`/`不合格理由.json` を見ること。CLIの終了コードでは分からない)。
- stdout はログ的な人間向けテキスト(`=== <依頼ID> ===` の見出し + 最終state)。**機械判定には
  使わない**(§5のstatus.jsonを読むこと)。
- 副作用: 対象ディレクトリ配下のファイル群(§2)を書く。SolidWorksが必要な段(計測中・
  候補提示中・向き反映投影)を含む依頼があれば、その間SWを占有する。
- **推奨運用**: 橋渡しは各ステージごとに個別コマンドを叩くのではなく、**`scan` を定期的に
  (または依頼の追加/AI成果物設置の直後に)呼び、`status.json` の `state` で進捗を判定する**。

### 3.3 `status`(全依頼の状態一覧。人間向け表示。機械判定には不十分)

```
python app/workshop.py status
```
整形済みテキスト表を stdout に出すだけ(JSON化されていない)。**橋渡しは個別の
`status.json` を直接読むこと**(§5)。終了コードは常に `0`。

### 3.4 `retry`(終端状態からの巻き戻し。§5の「エラー」とは別物)

```
python app/workshop.py retry <依頼ID> [--force] [--向き再選択]
```
- **`不合格` / `質問あり` の依頼、または `--force` 付きで `合格` の依頼だけを** `計画待ち`
  へ巻き戻す(§5.2)。`status.json.errors` が残っているだけ(state はまだ非終端)の依頼には
  **使わない**(次の `scan` が自動で再試行する。§5.3)。
- `--force`: 合格済みも巻き戻す(納品箱の複製も削除)。
- `--向き再選択`: `choice.json`/`meta.json`/`views.dxf` も削除し、次の `scan` で
  向き投影からやり直す(SolidWorksを再度使う)。省略時は `choice.json`/`plan.json` を
  温存したまま生成だけやり直す(SW不要)。
- 終了コード: `0`=巻き戻し成功(または「エラー履歴クリア」処理成功) / `1`=依頼フォルダが無い、
  または対象外の状態(巻き戻し不要)。

### 3.5 CLIを経由しない直接ファイル生成(受付のみ・許容されている代替経路)

`受付済 → 計測中` の遷移は「`依頼.json` が存在し必須項目(図番/材質/個数)が揃っていて、
モデルファイルが1件ある」ことしか見ない(`app/workshop.py:743-754`)。**`new` を呼ばず、
橋渡しが直接 `data/依頼箱/<依頼ID>/` を作って `依頼.json` を書き、モデルファイルを置いても
同じように動く**(日本語CLIフラグの文字化けリスクを避けたい場合はこちらを推奨)。
`依頼.json` のスキーマは §4。

---

## 4. 依頼.json 契約

```jsonc
{
  "図番": "string",       // 必須(空文字/空白のみは不可)
  "材質": "string",       // 必須。3DからAI推定させない(手順書§3.4)
  "個数": 1,               // 必須。正の数値(文字列なら float 変換できること)
  "品名": "string",        // ❗**実質必須**(§4.1)。生成の最終段(表題欄)で要る
  "装置名": "string",      // ❗**実質必須**(§4.1)。生成の最終段(表題欄)で要る
  "材質形状": "string",    // 省略可(例 "マル40" "ＰＬ６")
  "尺度": 1.0,              // 省略可(既定1.0)。❗plan.jsonのsource.scaleと一致必須
                             //   (不一致だと生成中にエラーで停止。plan.jsonが正)
  "製図者": "AI",           // 省略可(既定"AI")
  "連番": "string",        // 省略可
  "_note": "string"        // 省略可・任意メモ(処理に影響しない)
}
```
### 4.1 ❗「省略可」と書いてあるが**実質必須**の項目(2026-08-11 実測・重要)

`validate_request()` が受付時に見るのは **図番・材質・個数**の3つだけだが、
**`engine/compose_drawing.py:505-508` が表題欄を書く段で `品名`・`図番`・`装置名` の
3つを必須にしている**:

```python
for req in ("品名", "図番", "装置名"):
    if not field_values.get(req):
        raise ValueError("fields['%s'] は必須です" % req)
```

つまり **`装置名`(および `品名`)を空のまま出すと、受付・計測・候補提示・向き選択・
作図計画をすべて通過したあと、`生成中` の最終段で落ちる**。
2026-08-11 の開発機E2E(実SW2023・実 `claude -p`)で実際に踏んだ:
計測47秒 + 向き選択163秒 + 向き反映8秒 + **作図計画944秒** を使い切ってから
`ValueError: fields['装置名'] は必須です` で停止し、`生成中` に留まった。

- **SOLIDIFY 側の対処(2026-08-11 ディレクター裁定・実装済み)**:
  1. **受付で `装置名` を必須(400)にした**(`phase2/app/server.py` の
     `REQUIRED_REQUEST_FIELDS = ("図番","材質","個数","装置名")`)。
  2. **`品名` は任意のまま**だが、空なら**モデルのファイル名から付ける** ──
     決め方はこのリポジトリの `app/workshop.py:322-328`(`_out_stem`)と同じで、
     `<図番>_<品名>.STEP` なら `_` の後ろ、`_` が無ければステム全体。
     **ずらすと納品物のファイル名と表題欄の品名が食い違う**ので、変えるときは両方を揃えること。
  3. 保険として `phase2/worker/pipeline_real.py` の `_run_drawing` が**着手前に**
     品名・図番・装置名の空をチェックし、空なら SolidWorks も `claude -p` も動かさずに
     **赤い札(topic `運用:title_block_missing`)** で人へ返す(古い依頼・API直叩き対策)。
- **このリポジトリ側は未変更**(engine/ は書き換えない安全規約)。
  `validate_request()` の `REQUIRED_REQUEST_FIELDS` は `(図番, 材質, 個数)` のまま。
  **SOLIDIFY 経由でない直接利用では、依然として `装置名` 空のまま生成の最終段まで進む**ので注意。

検証ロジック: `app/workshop.py:validate_request()`(177-190行)。必須項目の欠落/個数が
0以下または非数値だと、依頼は `受付済` のまま `errors[]` にエラーが積まれて先へ進めない
(§5.3 のとおり `scan` を再度呼べば自動再試行されるが、**依頼.json 自体を修正しない限り
何度呼んでも失敗し続ける**)。

---

## 5. 状態機械(status.json)契約

### 5.1 状態遷移表

| state | 意味 | 次に必要なもの | SW使用 |
|---|---|---|---|
| `受付済` | 依頼.json検証待ち | 依頼.json + モデルファイル | 不要 |
| `計測中` | 計測実行待ち | (自動。SW計測) | **要** |
| `候補提示中` | 候補投影実行待ち | (自動。SW投影×候補数) | **要** |
| `計画待ち` | AIオペレータの成果物待ち | `choice.json` → 自動で views.dxf/meta.json 生成(**要SW**) → `plan.json` で次へ | choice.json検出時のみ**要** |
| `生成中` | 生成・検証ゲート実行待ち | (自動。`--skip-sw` で views.dxf/meta.json を再利用するため**SW不要**) | 不要 |
| `合格`(終端) | 納品済み | — | — |
| `不合格`(終端) | ゲート不合格 | `retry` での巻き戻し | — |
| `質問あり`(終端) | `質問票.md` 検出 | `retry` での巻き戻し(質問票削除) | — |

`計画待ち` は `質問票.md` の存在を**この状態にいる時だけ**チェックする
(`app/workshop.py:781-789`)。他の状態にいる間に置いても検出されない。

### 5.2 status.json スキーマ

```jsonc
{
  "request_id": "string",
  "state": "受付済|計測中|候補提示中|計画待ち|生成中|合格|不合格|質問あり",
  "created_at": "ISO8601(秒精度・ローカル時刻)",
  "updated_at": "ISO8601",
  "history": [
    {"at": "ISO8601", "from": "state", "to": "state", "note": "string|null"}
  ],
  "errors": [                          // 直近20件。非終端状態でも積まれる(§5.3)
    {"at": "ISO8601", "step": "その時のstate", "message": "ExceptionType: message"}
  ],
  "notes": [                            // 省略され得る(計画待ち中の途中経過用)
    {"at": "ISO8601", "note": "string"}
  ]
}
```

### 5.3 「エラー」は終端状態ではない(重要・ポーリング設計に直結)

`advance_one()` は例外を捕捉すると **state を変えずに** `errors[]` に追記して `False` を返す
(`app/workshop.py:845-848`)。つまり:
- 一時的なSW不調・タイムアウト等でどこかの状態遷移が失敗しても、**依頼は同じ state に留まり
  次の `scan` 呼び出しで自動的に再試行される**(`retry` コマンドは不要)。
- `retry` コマンドは**終端状態(不合格/質問あり/合格)からの巻き戻し専用**であり、
  非終端状態でエラーが溜まっている依頼に使うと「対象外」として`1`を返すだけで何もしない。
- 橋渡し側が「進捗が止まっている」を検知したい場合は、`updated_at` が一定時間更新されない
  依頼を監視するか、`errors[]` の件数・最新の `message` を見て人間へアラートする設計にする
  (このリポジトリ自身にはリトライ上限も自動アラートも無い)。

---

## 6. 中間成果物の契約(橋渡しがライブプレビュー等に使う場合)

### 6.1 meas.json(計測中で生成)
主要キー: `ok`(bool)、`metrics`(`mass_kg`/`volume_mm3`/`density_kgm3`/`surface_area_mm2`/
`com_mm`/`material`/`density_is_default_water`/`bbox_mm`/`size_mm`/`body_count`/
`model_view_names`)、`faces`/`edges`/`circle_groups`(形状クラス判定用の生データ)。
`ok=false` の場合 `error` キーにエラー内容(この場合 workshop.py 側が例外を投げ、
計測中に留まって次の scan で自動再試行される)。

### 6.2 候補提示(候補提示中で生成)
- `候補.png`: AIオペレータ(orientation_prompt.md)へ見せる候補一覧画像。
  **橋渡しのライブプレビューにそのまま使える**(依頼IDフォルダ直下・固定ファイル名)。
- `候補/candidates_meta.json`: `candidates[]` に各候補の `id`/`sw_view`/`rot`/`ok`/
  `geom_mm`/`model_to_view` 等。AIオペレータ向けの内部データであり、橋渡しが
  直接パースする必要は通常ない(candidates が全滅すると `候補提示中` のままエラー扱い)。

### 6.3 choice.json(AIオペレータが置く。読み取り専用でworkshop側が消費)
```jsonc
{"id": "string", "sw_view": "SW標準ビュー名(例 \"*正面\")",
 "rotation_deg": 0, "rationale": "string"}
```
`rotation_deg` は無ければ `rot` を見る(後方互換フィールド)。`sw_view` 必須。

### 6.4 生成結果(生成中で作成。result.jsonの構造)

`data/依頼箱/<依頼ID>/生成/<out_stem>_result.json`:
```jsonc
{
  "summary": {
    "model": "abs path", "plan": "abs path", "request": "abs path",
    "zuban": "string", "part_name": "string", "skip_sw": true,
    "steps": { "sw_projection": {...}, "density": {...}, "compose": {...},
               "dim_engine": {...}, "gate2": {...}, "independent_verify": {...},
               "centerline": {...}, "layout_verify": {...} },  // 途中経過。キーは条件次第で有無変わる
    "final_dxf": "abs path|null",   // 不合格時は 生成/不合格/ 配下を指す
    "final_png": "abs path|null",
    "gate1_ok": true, "gate2_ok": true, "verify_ok": true,
    "layout_ok": true, "centerline_ok": true,
    "overall_ok": true,             // ★ これが合否の一次情報
    "status": "検証通過|不合格",
    "dim_error": "string(不合格理由。gate1_ok=false時のみ)",
    "result_json": "abs path(workshop.py側で補完。§6.4.1)"
  },
  "compose": {...}, "dim": {...}, "gate2": {...},
  "independent_verify": {...}, "centerline": {...}, "centerline_check": {...}
}
```
`overall_ok = gate1_ok and gate2_ok and verify_ok and layout_ok and centerline_ok`。

#### 6.4.1 ❗result.json 読み戻し時の既知の癖
`engine/generate_drawing.py` は `result_json` ファイルへ書き出す**前**に
`summary["result_json"]` を追記するため、書き出されたJSONファイル自体には
`summary.result_json` が入っていない。**workshop.py はファイルを読み戻した後に
`result["summary"]["result_json"] = result_json` で補完している**
(`app/workshop.py:342-345`)。橋渡しが `generate_drawing.py` の結果ファイルを直接読む場合は
同様に自分でパスを補ってから使うこと(通常は §3.2 の `scan` 経由で完結するので意識不要)。

#### 6.4.2 exit code と result.json の対応
`engine/generate_drawing.py` は **クラッシュ時は result.json を書かずに非0で終了する**
(例外がそのまま伝播)。`workshop.py.step_generate()` はこれを検知し、`result.json` が
存在しなければ `RuntimeError` を上げて `生成中` のまま次scanへ委ねる(§5.3のエラー扱い)。
**result.json が存在する = クラッシュしていない**(ゲート合否に関わらず)。

### 6.5 不合格理由.json(不合格時のみ・生成中→不合格の遷移で書かれる)
```jsonc
{
  "at": "ISO8601",
  "gate1_ok": false, "gate2_ok": false, "verify_ok": true, "layout_ok": true,
  "centerline_ok": true, "centerline": {...}, "dim_error": "string|null",
  "gate2_unspecified": [ {"feature": "position|circle|...", "axis": "X|Y|Z", ...} ],
  "gate2_redundant_dimensions": [...],
  "frame_collisions": [ {"zone": "string", "id": "string"} ],
  "result_json": "abs path"
}
```
橋渡しが人間可読サマリを出したい場合は `app/workshop.py:summarize_reject_reason()`
と同じ分類(位置/直径/その他・図枠衝突ゾーン別)をSOLIDIFY側でも実装するか、
このJSONをそのままUIに出す(構造化済みなので後者で足りることが多い)。

---

## 7. 質問票.md 契約(裁定台帳への自動転記用)

`計画待ち` 中に AIオペレータが `data/依頼箱/<依頼ID>/質問票.md` を置くと、次の `scan` で
`state` が `質問あり`(終端)へ遷移する。

**書式規約(2026-08-11追加)**: 1行目は `# 質問票 <図番> ...` の見出し。
**2行目に `topic: <短い名詞句>` を1行だけ置く**(例 `topic: 面外傾斜穴`
`topic: 正面ビュー拮抗` `topic: ねじ呼び径未確定`)。同種の争点には毎回同じ言い回しを使う
(SOLIDIFY側の裁定台帳が「同じ図番×同じ争点」を自動照合するキーとして使うため)。

```markdown
# 質問票 25154-2-06(改善サイクル3 2026-08-10 更新)
topic: 面外傾斜穴

## エンジン未対応: ...
```

### 7.1 複数の争点を1ファイルに書くとき(2026-08-11 追加・SOLIDIFY契約v3.6 変更6)

SOLIDIFY の裁定台帳 `rulings` は **`(図番, topic)` が主キー**で、**完全一致でしか自動転記しない**。
1ファイルに争点が複数あると1つの topic に潰れてしまうので、**争点ごとに見出しで区切る**:

```markdown
# 質問票 25154-2-06(向き選択)

### topic: 正面ビュー拮抗
候補C1(*正面+0)と候補C3(*平面+270)が拮抗しています。どちらを正面図にしますか。
添付: 候補.png

### topic: ねじ呼び径未確定
φ8.0 の通し穴4個は М8 タップですか、ボルト通し穴ですか。
添付: 生成/不合格/25154-2-06_クッション.png
```

SOLIDIFY側は **1 topic = 1枚の赤い札(blocker)** に分解する。

### 7.2 `添付:` 行(**必須**・2026-08-11 追加)

**SOLIDIFY契約v3.6 変更6(承認時ユーザー裁定)により、blocker には図が必須**である。
そのため質問票には `添付: <依頼フォルダ基準の相対パス>` を **1行以上必ず**書く:

- 置き場所は `topic:` 行(または `### topic:` 見出し)の直後。争点ごとの節がある場合はその節の中。
  節に `添付:` が無い争点には、見出しより前に書かれた `添付:` が配られる。
- **その時点で実在するファイル**だけを書く。橋渡しは**納品箱の現物を機械照合**し、
  実在しない名前・**画像でないファイル**(`.json`/`.dxf` 等)は図と認めない。
  認められる拡張子は `.png/.jpg/.jpeg/.gif/.webp/.bmp`。
- 指せる絵が無いときでも `添付: 候補.png` を書く。**画像ゼロの質問票は契約違反**で、
  橋渡しが候補コンタクトシート(`候補.png`)で代替したうえで
  「工房が候補一覧の絵を代わりに添えています」と本文に付記する(質問の意図が伝わりにくくなる)。
- 1行に複数書くときは `、` か `,` で区切ってよい。

### 7.3 橋渡し側のパース規約(実装済みの挙動)

`phase2/worker/drawing2d.py:parse_questionnaire()` は次の3書式を**すべて**受ける
(**質問票そのものを捨てないのが最優先**):

| 書式 | 判定 |
|---|---|
| `### topic: <名詞句>`(H2〜H6)で区切られている | 見出しごとに1 blocker |
| 先頭数行に `topic: <名詞句>` が1行(§7 の現行書式) | 全体で1 blocker |
| `topic` が無い(旧形式・書き漏れ) | 全体で1 blocker・topic は `図面化:不明` |

`topic:` / `添付:` の行は blocker の本文からは取り除かれる(人が読む札に制御行を出さない)。
全角コロン `：` も受ける。
- `質問あり` の依頼を `retry` すると質問票.md は削除される(§3.4)。**裁定台帳へ転記するのは
  `retry` する前**に行うこと(転記後に人間が回答→AIオペレータへの指示として
  `plan_prompt.md`/`orientation_prompt.md` の追加コンテキストに載せる運用は、
  この文書のスコープ外=橋渡しモジュール側の設計)。

---

## 8. 納品物契約(合格時。deliver() が book_ledgerと対で呼ばれる)

`data/依頼箱/<依頼ID>/生成/` から `data/納品箱/<依頼ID>/` へ以下をコピー
(`app/workshop.py:deliver()` 430-446行。**移動ではなくコピー**。依頼箱側にも残る):

| ファイル | 元 |
|---|---|
| `<out_stem>.dxf` | `summary.final_dxf` |
| `<out_stem>.png` | `summary.final_png` |
| `<out_stem>_result.json` | `summary.result_json` |
| `解釈レポート_<図番>_<品名>.md` | workshop.py が新規生成(§8.1) |

いずれも `os.path.exists` を確認してからコピーする(存在しないキーは黙ってスキップ)。
**橋渡しが「納品が実在するか」を確認する場合は、この4ファイルの存在を直接チェックすること**
(手順書§4 M2「承認は成果物実在チェックで409」と同じ思想。`status.json.state=="合格"` だけを
信用しない)。

### 8.1 解釈レポート.md
`write_interpretation_report()`(398-427行)が自動生成する固定書式のMarkdown。
検証ゲート結果・材質/個数・様式警告・「呼び値未確定」等の人間確認事項を含む。
橋渡しのUI表示にそのまま転記してよい(人間向けMarkdown。構造化データが要る場合は
`不合格理由.json`/`result.json` 側を使う)。

---

## 9. 手順書(Z2工房統合手順書_2026-08-11.md)との既知の差分

- §3.2 は状態機械を英語風の名前(`queued → measuring → orienting → ...`)で概説しているが、
  **実際の `status.json.state` は日本語の8状態(§5.1)**。SOLIDIFY側の契約v3.6変更案で
  `kind="drawing"` の状態機械を設計する際は、この日本語state文字列をそのままマッピング元にする
  (workshop.py側の文言を英語化する変更はしていない。手順書は概念図であり実装契約ではない)。
- §3.3 の環境変数例 `SOLIDIFY_DRAWING_ROOT=C:\workshop\Drawing-Generator-Agent`(=リポジトリ全体の
  配置パス)と、本契約の「data/ と 台帳.md だけを切り替える」は**役割が違う**:
  Z2でこのリポジトリを配置すれば `SOLIDIFY_DRAWING_ROOT` 未設定でもコード側は自動的に
  配置先を基準に動く(`ROOT` は `__file__` から動的解決)。`SOLIDIFY_DRAWING_ROOT` が要るのは
  **データの置き場所をコードの置き場所と分離したい場合**(橋渡しが依頼を複製する作業ディレクトリを
  リポジトリ外に置きたい時、またはテスト時)。Z2で「リポジトリ配置=データ置き場所」のまま
  運用するなら、この環境変数は**設定しなくてもよい**(既定でリポジトリ自身のdata/を使う)。

---

## 9.1 SOLIDIFY 側の実装状況(2026-08-11・M3/M4 実装後の実測)

橋渡しの実体は SOLIDIFY 側 `phase2/worker/drawing2d.py`(+ `pipeline_real.py` の
`kind="drawing"` 分岐)。**この文書との乖離を作らないため**、実装が現に何をしているかを書く。

| このリポジトリが提供するもの | 橋渡しの使い方 |
|---|---|
| `new`(§3.1) | **使っていない**。日本語CLIフラグを避け、§3.5 の直接ファイル生成で `依頼.json` を書く |
| `scan`(§3.2) | 段ごとに1回ずつ呼ぶ。**終了コードは見ず** `status.json.state` で判定(§5) |
| `retry`(§3.4) | `不合格`/`質問あり` からの巻き戻しに使う。`--向き再選択` は使っていない(向きは温存) |
| `status`(§3.3) | 使っていない(`status.json` を直接読む) |
| `候補.png`(§6.2) | 実況のライブプレビュー(`_live.png`)と、質問票の図の代替に使う |
| `生成/*.png`・`生成/不合格/*.png` | 生成・検査中のライブプレビューに使う |
| `不合格理由.json`(§6.5) | 差し戻し文(次の `plan.json` への指摘)に整形して使う |
| `_result.json`(§6.4) | ゲート合否と `dim_engine.style_warnings` / `nominal.pending` / `nominal.review_dimensions` を読む |
| 納品箱の4ファイル(§8) | **現物の存在を確認**してから SOLIDIFY の納品箱へ複製する |

**SOLIDIFY 側の状態への写像**(SOLIDIFY は状態を増やさない = 契約v3.6 変更2):

| このリポジトリの state | SOLIDIFY の status | 画面の表示名 |
|---|---|---|
| `受付済` / `計測中` | `reading` | 計測 |
| `候補提示中` →(AIが `choice.json`) | `verifying` | 向き |
| `計画待ち`(向き反映投影 →(AIが `plan.json`)) | `building` | 計画 |
| `生成中` | `inspecting` | 作図・検査 |
| `合格` | `delivered`(人の承認後) | おとどけ |
| `不合格` | `building` へ差し戻し(上限3回)。超えたら `fuda` | — |
| `質問あり` / 質問票を検出 | `fuda`(赤い札) | — |

❗**橋渡しは `retry` した直後に `plan.json` を退避する**(`plan_不合格N.json`)。
`retry` は `plan.json` を残す仕様(§3.4)なので、残したままだと同じ計画で作り直してしまうため。
❗**橋渡しは古い `質問票.md` を必ず片付けてから走り直す**。残すと次の `scan` が
「計画待ち→質問あり」を検出して同じ札を出し続ける(人が答えたのに何も進まない)。

## 10. 台帳.md(橋渡しは書き込まない・読み取りも非推奨)

`$SOLIDIFY_DRAWING_ROOT/台帳.md` は `upsert_ledger_row()` が依頼IDをnote列に埋め込んで
1依頼1行を維持する人間向けMarkdownテーブル。**書き込みはworkshop.py専用**
(橋渡しが直接書くと二重管理・フォーマット崩れの原因になる)。橋渡しが機械的に記帳状況を
知りたい場合は `status.json`/`result.json` を見ること(§5・§6.4)。SOLIDIFY自身の裁定台帳・
ジョブ台帳とは別物であり、統合しない(手順書§3.1 理由3「コードベースの合併はしない」)。
