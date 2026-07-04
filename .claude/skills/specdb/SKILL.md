---
name: specdb
description: Manage specifications as data (the single source of truth) instead of Word/Excel documents - a metamodel-driven YAML store of spec items and relations with machine validation (required attrs, cardinality, uniqueness, orphans), generated Markdown design docs, baseline diff reports, and a self-contained interactive HTML graph viewer. Use when asked to "仕様をデータとして管理 / 仕様DB / 設計書を生成 / テーブル定義書・画面仕様書の自動生成 / ベースライン比較 / 変更点一覧 / 仕様の可視化 / 関係グラフ / spec as data". Requires Python 3.10+.
license: MIT
---

# specdb — 仕様をデータとして管理する

「文書（Word/Excel）を正本にする」のではなく、**仕様アイテムと関係を YAML の
データ（正本）として保存し、設計書はそこから生成されるビュー**にする仕組み。

- エンジンは特定のアイテム種別を一切知らない。何が存在してよいか
  （種別・属性・関係）はすべてプロジェクトごとの `metamodel.yaml` の宣言で決まり、
  **新しい種別・関係・文書の追加にコード改修は不要**
- 機械検証つき: ID 一意性、必須属性、enum、多重度（cardinality）、
  一意性（unique）、未定義参照、孤児検出。error があれば生成は中止され exit 1
  （CI で PR をブロックできる）
- すべてのアイテム・関係が出典（`source` = doc + location + evidence）を持てるので、
  既存資料からの移行でもトレーサビリティが残る
- `visualize.py` が仕様データ全体を**自己完結の対話型 HTML**（依存・CDN なし）に
  描画する: 種別で色分けした関係グラフ、種別/関係フィルタ、検索、ノード詳細
  （属性・出典・関係）、検証 error/warn のオーバーレイ、一覧テーブル表示

## セットアップ

依存は PyYAML + Jinja2 のみ（`.claude/skills/specdb/scripts/requirements.txt`）:

```bash
pip install PyYAML Jinja2
```

## プロジェクトの初期化

仕様データはユーザープロジェクト側に置く（ツールはスキル同梱のものを使う）。
雛形 `.claude/skills/specdb/scaffold/` をプロジェクトへコピーして開始する:

```bash
cp -r .claude/skills/specdb/scaffold .specdb     # .specdb なら --root の指定を省略できる
```

scaffold にはサンプルのメタモデル（データ項目・エンティティ・業務ルール・画面）、
アイテム、文書定義、Jinja2 テンプレートが入っている。まず
`.specdb/README.md` を読み、メタモデルとアイテムをプロジェクトの語彙に
置き換える。サンプルの items/・relations/ は削除して構わない。

## 使い方

```bash
python .claude/skills/specdb/scripts/engine.py                    # 検証レポート + 統計
python .claude/skills/specdb/scripts/generate.py                  # 全文書を out/ に生成
python .claude/skills/specdb/scripts/generate.py table-spec       # 指定文書だけ生成
python .claude/skills/specdb/scripts/diff.py     baseline/R1.0    # ベースライン差分
python .claude/skills/specdb/scripts/diff.py     --baselines      # ベースライン一覧
python .claude/skills/specdb/scripts/visualize.py                 # 対話型ビューア out/specdb.html
```

- `--root` 省略時はカレントディレクトリの `.specdb/`（`metamodel.yaml` を持つもの）が
  データルートになる。無ければツール同梱のサンプルデータにフォールバックするので、
  別名・別場所のデータは `--root <dir>` を先頭引数で明示する
- ベースライン = Git タグ（`git tag baseline/R1.0`）。データディレクトリが
  Git 管理されていることが前提
- 生成物（`out/`）はビューなので直接編集しない。仕様変更は items/relations を
  直し、再生成する

## 文書種別の追加（コード改修なし）

1. `documents/<名前>.yaml` を書く（title / output / template の 3 行。
   追加のキーはそのまま `doc` としてテンプレートへ渡る）
2. `templates/<名前>.md.j2` を書く。テンプレートから使える API:
   - `store.items_of('<種別>')` / `store.items[id]` — アイテム取得
   - `store.relations_of('<関係>', src=…, dst=…)` — 関係の絞り込み（ordered な関係は並び順で返る）
   - `store.relating_to('<関係>', [id, …])` — 逆引き
   - フィルタ: `|status` `|source` `|evidence` `|item_label`
   - 変数: `doc` / `mm` / `generated_at` / `data_rev`

### Excel 風 HTML 設計書（伝統的な日本の設計書レイアウト）

出力は Markdown に限らない。`output:` と `template:` を `.html` にすれば
HTML 文書を生成できる（HTML テンプレートでは値が自動エスケープされる）。
テンプレート部品集 `templates/_excel.html.j2`（scaffold 同梱）が、様式と
情報設計の両方の部品を提供する — 様式: 表紙（承認/審査/作成のハンコ枠）・
改訂履歴表・シートタブ切り替え・方眼紙背景・状態のセル色・A4 横の印刷 CSS。
情報設計: クリックで移動できる目次（toc）・章番号見出し（sec/subsec）・
概要枠（kv）・前書き（prose）・関係から自動生成する関連図（bipartite）・
一覧画面の模式図（wireframe_list）・規則の節形式（rule_article）・
出典を付録に集約する出典一覧（appendix_sources）。
生成物は**自己完結（依存・CDN なし）の Excel 設計書風 HTML** になる:

```jinja
{% import "_excel.html.j2" as ex %}
{{ ex.page_start(doc.title) }}
{{ ex.cover(doc.title, doc.doc_no, doc.version, generated_at[:10]) }}
{% call ex.sheet('1. 概要', doc.title, doc.doc_no, doc.version) %}
  {{ ex.sec('1.1', '目的') }}{{ ex.prose(doc.preface.purpose) }}
{% endcall %}
{{ ex.page_end() }}
```

完全な実例は scaffold の `design-doc-excel`（基本設計書: 1 画面 1 シート・
レイアウト模式図・テーブル定義・データ辞書・業務ルール・出典付録）を参照。
本文は日本語名称主体にし、ID・出典・原文は付録シートへ集約するのが流儀。

メタモデルの書き方（属性 kind・unique、関係の cardinality・ordered・embedded、
名前空間）の詳細は `.claude/skills/specdb/scaffold/README.md` と
`.claude/skills/specdb/scaffold/metamodel.yaml` のコメントを参照。

## docextract / spec-extractor との関係

@doc-indexer で資料を索引化し @spec-extractor で仕様ファクトを洗い出した後、
その確定版を specdb のアイテム（`source` に出典を引き継ぐ）として登録すると、
「資料の山 → 検証可能な仕様データ → 生成される設計書」のパイプラインになる。
自動取り込みアダプタは未実装なので、現状は抽出済みファクトから items/*.yaml を
起こす（Claude が変換を手伝う）。

## 自己検証

同梱テストで動作確認できる: `python -m pytest .claude/skills/specdb/scripts/tests -q`
