# specdb — メタスキーマ駆動の仕様データ管理プロトタイプ

「文書を保存する」のではなく「仕様アイテムと関係をデータ（正本）として保存し、
設計書はそこから生成されるビューにする」構想の実証。

エンジンは特定のアイテム種別を一切知らない。何が存在してよいか
（種別・属性・関係）はすべて `metamodel.yaml` の宣言で決まり、
**新しい種別・関係の追加にエンジンのコード改修は不要**。

## 構成

```
specdb/
  metamodel.yaml       # メタモデル: 種別・属性・関係・名前空間の宣言（プロジェクトごとに定義）
  engine.py            # 汎用エンジン: ローダ/バリデータ/クエリ API（種別非依存）
  generate.py          # 汎用ジェネレータ: 文書定義を回してテンプレートを描画するだけ
  diff.py              # ベースライン差分: 2 リビジョン間の仕様データ差分レポート
  items/<種別>/*.yaml  # アイテムの正本。種別はディレクトリ名で決まる。
                       #   1 ファイル複数件（リスト）でも 1 件 1 ファイルでも可
                       #   名前空間を使う場合は items/<名前空間>/<種別>/
  relations/*.yaml     # 独立した関係レコード {type, from, to, 属性...}
                       #   名前空間を使う場合は relations/<名前空間>/
  documents/*.yaml     # 文書定義: タイトル・出力先・使用テンプレートの宣言
  templates/*.md.j2    # Jinja2 テンプレート（種別の知識はここに置く）
  out/                 # 生成物（ビュー。直接編集しない）
```

依存: PyYAML + Jinja2。

## 使い方

```
python specdb/engine.py                 # 検証レポート + 統計（error で exit 1 → CI でブロック）
python specdb/generate.py               # 検証 → documents/ の全文書を out/ に生成
python specdb/generate.py table-spec    # 指定した文書定義だけ生成
python specdb/diff.py <基準> [対象]      # リビジョン間の差分レポート（対象省略 = 作業ツリー）
python specdb/diff.py --baselines       # ベースライン一覧
```

ベースライン = Git タグ。`git tag baseline/R1.0` で作成し、
`python specdb/diff.py baseline/R1.0` で「ベースラインからの変更点一覧」が出る。
（この仕組みは specdb/ が Git 管理されていることが前提。）

ツールとデータを分離して使う場合（スキル配布版: ツールはスキル同梱、データは
プロジェクト側）は、各コマンドの先頭に `--root <データディレクトリ>` を付ける:

```
python <スキル>/scripts/engine.py --root <データディレクトリ>
```

`--root` を省略した場合はカレントディレクトリの `.specdb/`（`metamodel.yaml` を
持つもの）を自動的にデータルートにし、それも無ければツールと同じディレクトリ
（このサンプルデータ）にフォールバックする。プロジェクトのデータディレクトリを
`.specdb` という名前で置けば `--root` の指定を省略できる。

## 文書種別の追加方法（コード改修なし）

1. `documents/<名前>.yaml` を書く（title / output / template の 3 行）
2. `templates/<名前>.md.j2` を書く。テンプレートからは検証済み Store に
   アクセスできる:
   - `store.items_of('<種別>')` / `store.items[id]` — アイテム取得
   - `store.relations_of('<関係>', src=…, dst=…)` — 関係の絞り込み
   - `store.relating_to('<関係>', [id, …])` — 逆引き（これらを参照している from 側）
   - フィルタ: `|status`（状態の表示名）、`|source`（出典の整形）、
     `|evidence`（出典の原文）、`|item_label`（ID→表示名）
   - 変数: `doc`（文書定義）、`mm`（メタモデル）、`generated_at`、`data_rev`

例として `screen-spec`（画面仕様書）はこの手順だけで追加されている。

出力は Markdown に限らず、`output:` / `template:` を `.html` にすれば HTML も
生成できる（HTML テンプレートでは値が自動エスケープされる）。
`templates/_excel.html.j2` は伝統的な日本の Excel 設計書風 HTML の部品集で、
様式（表紙のハンコ枠・改訂履歴表・シートタブ・方眼紙背景・状態セル色・
A4 横印刷 CSS）に加えて、設計書の情報設計の部品（目次・章番号見出し・
概要枠・前書き・関係から自動生成する関連図・画面の模式図・規則の節形式・
出典一覧の付録）を提供する。完全な実例は `design-doc-excel`（基本設計書:
1 画面 1 シート構成）。文書定義に書いた追加キー（`doc_no:` `version:`
`preface:` 等）は `doc` としてテンプレートへ渡る。

## メタモデルの書き方（要点）

```yaml
item_types:
  screen:                              # 種別名 = items/ 下のディレクトリ名
    label: 画面                         # 表示名
    label_field: name                  # 一覧で表示名に使う属性
    warn_if_unreferenced: true         # 孤児検出の対象にする（任意）
    attributes:
      name:      { kind: string, required: true }   # kind: string|int|bool|enum
      screen_id: { kind: string, required: true, unique: true }  # 種別内で一意

relation_types:
  has-column:
    from: entity                       # from/to は種別名（リスト可）
    to: data-item
    cardinality: { from: "1..*" }      # 各アイテムが持つべき本数（任意。to も可）
    embedded: { field: columns, target_key: item }  # アイテム内への埋め込み記述を許可
    attributes:
      physical_name: { kind: string, required: true, unique: true }  # 同じ from 内で一意
```

- 共通コア属性（全アイテム・**全関係**が自動で持つ）: `id`（アイテムのみ）/
  `status`（draft, review, approved, deprecated）/ `source`（出典 = doc +
  location + evidence。**複数あればリストで書ける**）
- 関係は `relations/*.yaml` の独立レコードが基本形。列定義のように親アイテムと
  一緒に書くのが自然なものは `embedded` を宣言すると、アイテム内に記述でき、
  読み込み時に独立レコードへ正規化される（データモデル上は同じもの）。
  埋め込み記述の中でも `status:` / `source:` を書ける
- `cardinality` の `from` は出ていく本数、`to` は入ってくる本数の範囲。
  `"1..*"` `"0..1"` `"2"` の形式。deprecated のアイテムは検査対象外
- `ordered: true` の関係は並びに意味がある: 記述順が保存され、明示したければ
  `order` 属性（int）を書くとクエリ時にその順で返る
- `namespaces: { pay: 決済 }` を宣言すると `items/pay/<種別>/` と
  `relations/pay/` が使え、配下の ID・参照には自動で `pay:` が付く。
  名前空間をまたぐ参照はフル ID（`pay:di-0001`）で書く

## エンジンが行う汎用検証

- メタモデル自体の整合性（未知の kind、未定義種別への from/to 参照、
  cardinality の書式）
- ID の一意性、未知の status（アイテム・関係とも）、source の形式（doc 必須）
- 属性: 必須欠落 = error、宣言外 = warn、kind/enum 違反 = error、
  `unique` 違反 = error（アイテム属性は種別内、関係属性は同じ from 内）
- 関係: 未定義アイテム参照 = error、endpoint の種別違反 = error、
  多重度（`cardinality`）違反 = error、同一レコードの重複 = warn
- 孤児検出: `warn_if_unreferenced` の種別で、どの関係からも参照されないもの = warn

error が 1 件でもあれば生成は中止され exit 1（CI で PR をブロックできる）。

## 次のステップ

1. ~~メタスキーマ + 汎用ローダ/バリデータ~~（完了）
2. ~~関係の独立レコード化 + 逆引きクエリ~~（完了）
3. ~~文書定義 + Jinja2 テンプレートの外出し~~（完了。generate.py は種別非依存）
4. ~~関係のコア属性（status/source）、複数 source、多重度制約（cardinality）、
   一意性制約（unique）~~（完了）
5. ~~ベースライン（Git タグ）+ 差分ビュー（diff.py）、順序（ordered）、
   名前空間（namespaces）~~（完了）
6. SQLite ビルド、検証プラグイン、docextract/spec-extractor からの
   取り込みアダプタ、Word/Excel 出力、ReqIF エクスポート

まだ表現できないもの（既知の限界）: 関係を対象にした関係（具体化）と 3 項以上の
関係（どちらも「関係をアイテムに格上げする」モデリングで回避可能）、業務ルールの
条件・帰結の構造化、分岐のあるフロー。
