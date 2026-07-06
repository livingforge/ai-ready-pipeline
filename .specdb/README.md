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
python specdb/generate.py requirement-spec  # 指定した文書定義だけ生成
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

これはプロジェクト固有の文書を足す手順。要件定義書・基本設計書・詳細設計書・
トレーサビリティ・マトリクスといった標準文書は継承元パック jp-sier-std が
配布しており、このスキャフォルドでは `documents/basic-design.yaml`
（`from_standard: basic-design` で abstract 文書を穴埋め実体化）だけを置けば
残りは自動生成される。

出力は Markdown に限らず、`output:` / `template:` を `.html` にすれば HTML も
生成できる（HTML テンプレートでは値が自動エスケープされる）。伝統的な日本の
Excel 設計書風 HTML の部品集はパックの `_house-style.html.j2` が提供し、
プロジェクトのテンプレートからは `{% extends "std/basic-design.html.j2" %}` +
block 上書きで章だけ差し替えられる（様式: 表紙のハンコ枠・改訂履歴表・シートタブ・
方眼紙背景・A4 横印刷 CSS 等）。完全な実例はパックの `basic-design.html.j2`。
文書定義に書いた追加キー（`doc_no:` `version:` `preface:` 等）は `doc` として
テンプレートへ渡る。

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

## 標準パック（プロジェクト横断の様式標準化 — Phase 1）

設計書様式・文書カタログ・メタモデルをプロジェクト間で統一するための仕組み
（設計の全体像は `.specdb/docs/standard-pack-design.md`。Phase 1 = テンプレート
多層検索 + 文書カタログ、Phase 2 = メタモデルのマージ + 準拠検証 + pack.lock
まで実装済み）。

```yaml
# metamodel.yaml に 1 行足すとパックを継承する（無ければ完全に従来動作）
extends: div-std@1.2        # 「パック名@major.minor」または相対パス（開発モード）
```

- パックは `pack.yaml`（pack / version / extends 等）+ `templates/` +
  `documents/` を持つディレクトリ。パック自身も `extends` でき、
  「全社 → 事業部 → プロジェクト」の単一親チェーンが組める
- 解決順: `<データルート>/packs/<名前>/` → 環境変数 `SPECDB_PACK_PATH` →
  ツール同梱 `packs/`。version の major.minor 不一致は error（STD-E002）、
  循環は error（STD-E003）
- **テンプレート**は「プロジェクト → 近い層 → 遠い層」の順で検索される。
  同名の部分上書きは `{% extends "std/<名前>.j2" %}` + block 上書きが正の手段
  （`std/` = 直近層、`std2/` = その親）。ハウススタイル部品（`_*.j2`）の
  プロジェクト層での上書きは warn（STD-W301）、extends を使わない全置換も
  warn（STD-W303）
- **文書カタログ**: パックの documents/ は標準文書の雛形になる。
  `abstract: true` の文書はプロジェクトが `from_standard:` で実体化するまで
  生成されない。必須パラメータ欠落は error（STD-E202）、`doc_no` の
  採番規則違反も error（STD-E203）。カタログ側の title / output に書いた
  `{パラメータ名}` はマージ後の値で展開される

```yaml
# プロジェクト documents/basic-design.yaml — 標準文書の実体化は穴埋めだけ
from_standard: basic-design
system_name: 受発注システム
doc_no: SD-ORD-001
preface: { purpose: …, scope: … }
```

### メタモデルのマージと準拠検証（Phase 2）

`extends` があると、実効メタモデルは**チェーンをルート（全社）から順に重ねた
結果**になり、`specdb engine` はそのマージ済みモデルでデータを検証する。
各層は「その層より下をマージした結果」に対して**緩和禁止**の規則に従う
（プロジェクト準拠 ⇒ 事業部準拠 ⇒ 全社準拠の推移律）。

- 追加は自由（新種別・新関係・新属性・属性の required 化 = 厳格化）
- 緩和は error: kind 変更（STD-E101）/ required 緩和（E102）/ unique 除去
  （E103）/ 非 extensible enum への値追加（E104）/ id_prefix・sequence 変更
  （E121）/ endpoint 種別削除（E111）/ cardinality 緩和（E112）/ ordered
  解除・embedded 変更（E113）/ 予約名前空間の再宣言（E131）。診断には
  どの層がどの層の宣言を緩めたかを `[事業部 → 全社]` の形で含める

```bash
specdb conform                # L1（メタモデル）+ L2（データ・文書）+ lock 照合
specdb conform --for-baseline # ベースライン前提（status_rules）も検査
specdb conform --frozen       # pack.lock 不一致を error 扱い（CI 用）
specdb pack lock              # 解決結果から pack.lock を生成/更新
```

L2 はパックの `conformance/rules.yaml` が宣言する規則:
`require_documents`（実体化必須の標準文書。欠落 = STD-E201）、`attribute_rules`
（`when_status` に該当する状態で属性の記載を必須化 = STD-E211）、
`status_rules.baseline_requires`（`--for-baseline` 時、review/draft の残存を
禁止 = STD-E221）。`pack.lock` は継承チェーンの版・内容ハッシュを固定し、
`--frozen` でパック差し替えの混入を CI が検出できる（不一致 = STD-W003）。

### 横断集計とパックのリリースチェック

```bash
specdb aggregate <root1> <root2> ...   # 複数プロジェクトの横断集計台帳（Markdown）
specdb aggregate <root...> --out 台帳.md --type data-item
specdb pack check <パックdir>           # パックの block 規約リリースチェック
```

`aggregate` は標準パックを共有する複数プロジェクトを横串で読み、種別ごとの
共通台帳（共通データ項目辞書・システム間IF台帳）と、**extensible enum の全社
集計**を出す。標準宣言に無い値は「その他」に丸め、元値の内訳を次期標準への
昇格候補として付録に掲出する（設計メモ §6.1）。同名アイテムの属性ゆらぎ
（例: 同じデータ項目名で桁数が違う）は「要確認」として指摘する。

`pack check` はパック開発側のリリースチェックで、文書テンプレートが block 規約
（cover / revision_history / toc / preface / chapters / appendix）を満たすかを
検査する（一部だけ定義するテンプレートは STD-W401）。

### パックの自己正本化（pack build）

パック自体を specdb で正本化できる（設計メモ §3.1）。正本
（`specdb/packs-src/<名前>/`）は doc-type / conformance-rule / style-part を
アイテムとして持ち、配布物の `documents/*.yaml` と `conformance/rules.yaml` を
そこから生成する:

```bash
specdb pack build <正本dir> --into <配布dir>   # 正本 specdb → 配布物 config を生成
```

生成には generate の `foreach: <種別>`（種別のアイテム 1 件ごとに 1 ファイル
出力。`output` は `{属性}` で展開）と、engine の `kind: list` / `kind: map`
（入れ子の設定値をアイテム属性として持つ）を使う。`pack.yaml`・
`metamodel/core.yaml`・`templates/*.j2` は配布物側で直接オーサリングする。

### パック改版の移行（pack migrate）

パックのメジャー改版でプロジェクトデータの変換が必要なとき、mutate プランを
パックの `migrations/` に同梱し、`pack.yaml` の `migrations:` で宣言する:

```yaml
# pack.yaml
migrations:
  - { from: "1.*", to: "2.0", plan: migrations/1.x-to-2.0.json }
```

```bash
specdb pack migrate --root <project> --to 2.0 --dry-run   # 適用内容を確認
specdb pack migrate --root <project> --to 2.0             # 適用（transactional）
```

現在のパック版に `from` がマッチするエントリの mutate プランを選び、
transactional に適用する（新たな error が生じれば巻き戻す）。

### 同梱パック jp-sier-std

日本 SIer 向けの実パックを `specdb/packs/jp-sier-std/` に同梱している
（ビルドで `scripts/packs/` へ展開され、消費側は `extends: jp-sier-std@1.1`
で解決）。要件（機能/非機能）・画面・エンティティ・データ項目・業務ルール・
外部インターフェース・モジュール（クラス）・メソッドの全工程ドメインメタモデルと、
工程間トレース（realizes / refines / has-method）、要件定義書 / 基本設計書
（Excel 風 HTML・block 規約準拠）/ 詳細設計書 / トレーサビリティ・マトリクスの
文書カタログ、L2 準拠規則を提供する。新しいパックは
`specdb/packs/<名前>/` に同じ構成（pack.yaml + metamodel/ + documents/ +
templates/ + conformance/rules.yaml）で足せば同梱される。

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
6. ~~標準パック Phase 1（テンプレート多層検索 + std/ プレフィックス +
   文書カタログ from_standard）~~（完了。standard.py）
7. ~~標準パック Phase 2（メタモデルのマージ + L1/L2 準拠検証 + pack.lock）~~
   （完了。standard.py / conform.py / pack.py）
8. 標準パック Phase 3〜4（章立て検証・移行スクリプト・横断集計ツール。
   設計: .specdb/docs/standard-pack-design.md）
9. SQLite ビルド、検証プラグイン、docextract/spec-extractor からの
   取り込みアダプタ、Word/Excel 出力、ReqIF エクスポート

まだ表現できないもの（既知の限界）: 関係を対象にした関係（具体化）と 3 項以上の
関係（どちらも「関係をアイテムに格上げする」モデリングで回避可能）、業務ルールの
条件・帰結の構造化、分岐のあるフロー。
