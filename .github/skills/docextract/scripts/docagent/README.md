# 資料活用エージェント（docagent + 3 エージェント）

プロジェクト資料（Office 文書や PDF）を **抽出 → 索引化 → 仕様抽出 → 横断検索** し、
システム開発の後工程（現状把握・設計・仕様の洗い出し）で機械的に使える形にする仕組み。
成果物はすべて**出典（どの文書のどこ）を辿れる構造化 JSON**で、以降の工程に渡せる。

```
                         ┌─ docextract（スキル）… 文書 → output/<id>/result.json（テキスト/表/画像・出典付き）
利用者 ──▶ doc-indexer ──┤   フォルダ一括抽出 → 索引化（衝突しない ID・内容重複の把握）
          （現状把握）    └─ docagent（データ操作API）… 集約 JSON（.docextract/store/）
             │
             ├──▶ spec-extractor（仕様の洗い出し）… 文書 → 出典付きファクト（facts.json）
             └──▶ doc-qa（横断 QA）… 質問 → 出典付き回答（search / facts で接地）
```

## 3 つのカスタムエージェント（`.claude/agents/`）

いずれも「目的を達成する**手段**」を提供する。成果物は機械可読・出典付きで、要約の
ような人間向け終端フォーマットではなく、後工程が食える中間成果物を出す。

| エージェント | 役割（工程） | 使いどころ |
|------------|------|-----------|
| **doc-indexer** | フォルダを一括抽出し、衝突しない ID で索引化。各資料に**文書種別**を付与、内容重複も把握（要約はしない） | 「資料を取り込んで索引化して」 |
| **spec-extractor** | 割り当てられた**ブロック**（`context-get`）から機能要件・データ項目・画面/帳票・非機能要件等を項目化し `context-send` で返す。出典（doc_id + location）はブロック定義から自動付与 | 個別の仕様洗い出し |
| **spec-batch** | 対象文書を `context-set` で**ブロックの作業キュー**に確定し、**ブロックごとに spec-extractor を並列起動**。`context-check` のバリア後にシャードを `facts-merge` で統合する司令塔 | 「全文書をまとめて/並列で洗い出して」 |
| **doc-qa** | 抽出済み資料を横断検索し、**必ず出典付きで**問いに答える（無ければ「該当なし」） | 「既存仕様では〜はどうなっている？」 |

呼び出しは Claude Code 上で `@doc-indexer` のように指定する。通常はまず **@doc-indexer**
で取り込み、その後 **@spec-extractor** / **@doc-qa** を使う。

## データ操作 API（`docagent`）

抽出結果を集約 JSON に束ね、後工程が使う操作を提供する CLI / Python モジュール。

- **文書の索引（library.json）**: prep / add / **sync**（一括登録）/ **set-doctype**（文書種別付与）/
  **doctypes**（種別の表示・編集）/ list / query / stats / export
- **横断検索（doc-qa 用）**: **search**（本文を横断検索し出典 doc_id + location 付きで返す）
- **仕様ファクト（facts.json / spec-extractor 用）**: **fact-add** / **facts** /
  **facts-pending**（まだファクトが1件も無い文書を洗い出す）/ **fact-remove** /
  **facts-stats** / **facts-export** / **facts-merge**（並列抽出したシャードを
  主ストアへ統合。ID 振り直し・語彙は和集合・完全重複はスキップ）/
  **item-types**（種別の表示・編集）/ **rel-types**（ファクト参照 refs の関係種別の表示・編集）
- **ブロック抽出プロトコル（context.json / spec-batch + spec-extractor 用）**:
  **context-set**（文書群をシート/ページ最小単位のブロック作業キューへ確定。上限
  `block_max_chars` まで結合・超過時は文境界で分割）/ **context-get**（次の未処理
  ブロックの本文+語彙を**アトミッククレーム**で払い出す。ID は自動割り当て —
  並列に呼んでも二重払い出ししないので、呼び出し側が ID を配る必要はない）/
  **context-send**（抽出結果 `[{type, statement, refs?}]` をブロック専用シャードへ
  保存。location はブロック定義から自動付与。→done）/ **context-check**（done で
  ないブロックを列挙。`facts-merge` 前のバリア。未完なら exit 3）

> **起動方法**: 以下の `python -m docagent` は、docagent パッケージのある
> ディレクトリ（リポジトリ直下、またはバンドルの `scripts/`）が cwd のときだけ
> 動く書き方。エージェントや任意の場所からは、cwd に依存しない起動スクリプト
> `python <skill-dir>/scripts/run_docagent.py <サブコマンド>` を使うこと
> （`<skill-dir>` は `.claude/skills/docextract` など。プロジェクトルートで実行）。

### 現状把握（doc-indexer）: 抽出 → 一括索引

```bash
python -m docextract --dir <フォルダ> -r                  # フォルダを一括抽出（出典付き result.json）
python -m docagent init                                   # ストア初期化（初回のみ）
python -m docagent sync                                   # 抽出マニフェストの全文書を一括登録/更新
python -m docagent doctypes --json                        # 使える文書種別を確認
python -m docagent set-doctype <id> "基本設計"            # preview を見て文書種別を付与
python -m docagent list --json                            # 索引を確認
python -m docagent list --json -o docs.json               # 大量件数はファイルへ（数値ガード対象外）
python -m docagent list --json --limit 20 --offset 0      # または --limit/--offset でページング
python -m docagent stats                                  # 文書種別別の件数
```

### 仕様の洗い出し（spec-batch + spec-extractor）: ブロック抽出プロトコル

エージェントの入出力を 2 コマンド（`context-get` / `context-send`）に固定し、
出典（`doc_id` + `location`）をツール側で付与する低トークンの標準フロー。
context 系の**既定出力は機械可読の軽量エージェント形式**（メタ行＋生テキスト。
JSON のキー引用符・改行エスケープの冗長を避ける）なので `--json` は不要
（構造化 JSON が要るツール/テストだけ `--json` を付ける）:

```bash
# オーケストレータ (spec-batch)
python -m docagent context-set --docs <id> <id>           # 対象をブロックへ確定（--files/--folder も可）
python -m docagent context-check                          # バリア: complete/incomplete/shards（未完なら exit 3）
python -m docagent facts-merge .docextract/store/shards/facts.*.json   # 完了後に統合

# サブエージェント (spec-extractor)。この 2 コマンドだけで完結する
python -m docagent context-get                            # 次の未処理ブロックを自動獲得
    # → id: / source: / types: / rels: のメタ行 + 「--- 本文 ---」以降に生テキスト
python -m docagent context-send --id <応答の id> \
    --result '[{"type":"機能要件","statement":"ユーザは月次売上をCSVで出力できる"},
               {"type":"メソッド","statement":"register() は予約を登録する",
                "refs":[{"rel":"realizes","to_ref":"F-02"}]}]'   # 工程間トレースは refs で構造化
    # → added: N (種別=件数, …) / rejected: の要約だけが返る
```

単発の手動登録や補正には低レベル API も使える（出典は自分で接地する）:

```bash
python -m docagent item-types --json                      # 使える種別を確認
python -m docagent prep <id または result.json> --json    # 登録＋本文抜粋を取得
python -m docagent search "<原文の一部>" --doc <id> --json # 出典 location を特定（グラウンディング）
python -m docagent fact-add --doc <id> --type "機能要件" \
    --statement "ユーザは月次売上をCSVで出力できる" \
    --evidence "月次売上はCSVエクスポート可能" \
    --location '{"page": 3}'
python -m docagent rel-types --json                       # 参照(refs)に使える関係種別を確認
python -m docagent facts --doc <id>                       # 抽出済みファクトを一覧
python -m docagent facts-pending --json                   # まだファクトが1件も無い文書を洗い出す
python -m docagent facts-export -o facts.json             # ファクトを1ファイルに出力
```

### 横断検索（doc-qa）: 出典付き回答

```bash
python -m docagent search "権限" --json                   # 本文を横断検索し doc_id + location + 抜粋を返す
python -m docagent get <id> --json                        # 1件の詳細（メタ・文書種別）
python -m docagent text <id>                              # 本文全文（座標なしの軽量ビュー）
python -m docagent facts --doc <id>                       # 抽出済みの仕様ファクト
```

- `--json` は全サブコマンドで機械可読出力（前後どちらの位置でも可）。
- `--store` / `--doctypes` / `--facts` / `--item-types-file` で保存先・定義を変更可能。既定は
  `.docextract/store/`（docextract の出力と同じ基点）。環境変数 `DOCEXTRACT_HOME`
  で基点 `.docextract` の場所を docextract と一括で差し替えられる。
- ID は docextract が result.json に書き込んだ値をそのまま使う（再計算しない）。
  ID は入力ファイルの正規化済み絶対パスのハッシュを含み（例: `report_docx_a1b2c3d4`）、
  docextract の出力フォルダ名と必ず一致する。別フォルダの同名ファイルでも衝突しない。

### 集約 JSON（`.docextract/store/library.json`）の構造

```jsonc
{
  "version": 1,
  "doctypes": ["要件定義", "基本設計", "詳細設計", "画面・帳票",
               "インターフェース仕様", "データ定義", "テスト",
               "運用・保守", "議事録", "計画・見積", "その他"],
  "documents": [
    {
      "id": "report_docx_a1b2c3d4",   // docextract の result.json の id をそのまま採用
      "source": "report.docx",
      "source_abspath": "C:/work/資料/report.docx",  // 抽出元の正規化済み絶対パス
      "content_hash": "9f86d0…",      // ファイル内容の sha256 (重複・改変の検知)
      "file_type": "docx",
      "result_path": ".docextract/output/report_docx_a1b2c3d4/result.json",  // 抽出結果への参照
      "metadata": { "title": null, "author": "…" },
      "stats": { "text": 12, "table": 3, "image": 2 },   // 要素数
      "preview": "本文・表見出し・画像OCRの抜粋（種別判定の手がかり、最大600字）",
      "doctype": "基本設計",          // 文書種別を1つ（未設定は null）。doc-indexer が付与
      "added_at": "2026-07-02T…Z",
      "updated_at": "2026-07-02T…Z"
    }
  ]
}
```

要約・分類ラベル・状態フラグは持たない（要約は人間向け終端フォーマットのため排除、
仕様の中身は facts.json、横断検索は search が担う）。

### 文書種別（doctype）

「その資料が要件定義書か・設計書か・議事録か」を表す**現状把握のための分類**。
doc-indexer が `preview` を根拠に付与する。定義は 2 段構え（コードにハードコードしない）:

1. **既定**: パッケージ同梱の `docagent/doctypes.json`（要件定義／基本設計／詳細設計／
   画面・帳票／インターフェース仕様／データ定義／テスト／運用・保守／議事録／計画・見積／
   その他）。既定を変えたいときはこのファイルを編集する。
2. **各自の上書き**: `.docextract/store/doctypes.json`（git 管理外）。存在すれば優先。
   `python -m docagent init` が既定からコピーして生成する。

`set-doctype` はここにある名前のみ許可（表記揺れは正規化、`--force` で例外設定）。編集は

```bash
python -m docagent doctypes add "移行仕様"
python -m docagent doctypes remove "その他"
```

### 仕様ファクト JSON（`.docextract/store/facts.json`）の構造

spec-extractor が保存する、出典付きの仕様/要件項目。各項目は必ず `doc_id`（どの文書）+
`location`（どこ）を持ち、後工程が根拠を辿れる。ブロック抽出プロトコルでは location を
ブロック定義から自動付与し、`evidence`（原文）は持たない（location のシート/ページで
範囲を絞れるため）。低レベル API（fact-add）での手動登録時は evidence を添える。

```jsonc
{
  "version": 1,
  "item_types": ["機能要件", "業務ルール", "データ項目", "画面・帳票",
                 "外部インターフェース", "非機能要件", "制約・前提", "用語"],
  "rel_types": ["realizes", "refines", "constrains", "interfaces",
                "has-method", "has-column", "displays"],  // refs に使える関係種別
  "items": [
    {
      "id": "f0001",                 // ストア内で安定な連番
      "doc_id": "report_docx_a1b2c3d4",  // 抽出元の文書（library / manifest の id）
      "type": "機能要件",            // item_types から1つ（未知は拒否、--force で例外）
      "statement": "ユーザは月次売上をCSVで出力できる",  // 後工程がそのまま使える1文
      "evidence": "月次売上はCSVエクスポート可能",       // 根拠の原文抜粋（言い換えない）
      "location": { "page": 3 },     // result.json の要素 location（search で接地）
      "refs": [],                    // このアイテムから別アイテムへの参照（下記）
      "added_at": "2026-07-03T…Z"
    },
    {
      "id": "f0002",
      "doc_id": "detail_xlsx_…",
      "type": "メソッド",
      "statement": "register() は予約を登録する",
      "refs": [                      // 工程間トレース: 起点は常にこのアイテム（from）
        { "rel": "realizes", "to_ref": "F-02" },   // rel は rel_types から / 未知は拒否
        { "rel": "refines", "to_ref": "SCR-03", "note": "画面遷移元" }
      ],
      // to_ref は抽出時点の自然キー（F-02 / SCR-03 / 物理名）。specdb の item ID は
      // まだ無いため、後工程（spec-designer）が自然キーで実アイテムへ解決して関係を起こす。
      "location": {}, "added_at": "2026-07-03T…Z"
    }
  ]
}
```

種別（`item_types`）も文書種別と同じ 2 段構え: 既定は同梱の `docagent/item_types.json`、
各自の上書きは `.docextract/store/item_types.json`（`init` が生成、git 管理外）。編集は
`python -m docagent item-types add "<種別>"` / `... remove "<種別>"`。参照の関係種別
（`rel_types`）も同じ 2 段構えで、既定は `docagent/rel_types.json`、上書きは
`.docextract/store/rel_types.json`。編集は `python -m docagent rel-types add/remove "<関係種別>"`。

## 使い方（利用者視点：Claude Code 上で）

1. 対象ファイルをこのプロジェクトのルート（`c:\spec-pipeline`）に置く、または場所を控える。
2. `@doc-indexer` に「この資料を取り込んで索引化して」と頼む（抽出 → 索引化まで案内）。
3. 仕様を洗い出すなら `@spec-extractor` に文書 ID を渡す（出典付きファクトを蓄積）。
4. 資料を横断して調べたいときは `@doc-qa` に質問する（出典付きで回答）。

## テスト

```bash
python -m unittest tests.test_docagent tests.test_facts_and_corpus -v
```

フィクスチャはテスト内で生成するため、docextract 実行もネットワークも不要。

## 補足（配布とバージョン管理）

- エージェント定義（`.claude/agents/*.md`）は git 追跡対象。
- `docagent/` と、生成データの基点 `.docextract/`（`output/` と `store/` を含む）は
  リポジトリ直下のため `.gitignore` の許可リスト方式で**非追跡**（`docextract/` 本体と
  同じ扱い＝ローカル動作用）。集約結果を共有したい場合は
  `python -m docagent export -o library.json` で書き出す。
