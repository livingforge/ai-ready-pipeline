---
name: docsummary
description: Summarize extracted/registered documents with an LLM - the output structure is fixed (a metadata table + category + body appended to the parsed document info), so instead of an output format you define the summary perspective (summary_guide.md) and a category taxonomy (summary_categories.json; the LLM picks one, normalized like doctypes). Pick targets by document id, source folder (--dir), unsummarized/stale only (--pending), or all; supports OpenAI / Azure OpenAI / Gemini / Anthropic with API keys kept in .env (never printed); summaries save to .docextract/summaries/<doc_id>.md with freshness tracked by content hash + spec hash (guide+categories). Use when asked to "要約 / summarize / サマリ作成 / 文書を要約して / カテゴリー分類 / 未要約の文書をまとめて". Requires docextract extraction + docagent registration first, and an LLM API key configured via docsummary config.
---

# docsummary — 登録済み文書を LLM で要約する

docextract で抽出し docagent で索引化した文書を LLM で要約する。要約は
**パース済み文書情報に付加する固定構造**（メタ表 + カテゴリー + 本文）で
保存されるため、出力フォーマットの定義は不要。利用者が定めるのは
**要約の観点**と**カテゴリー（既定の統制語彙）**の 2 つで、どちらを変えても
要約は作り直し対象（stale）になる。鮮度は内容ハッシュ + 要約仕様ハッシュで追跡する。

- 実行体はこの docsummary スキルに同梱（`.github/skills/docsummary/scripts/docsummary/`）。
  依存する docextract / docagent パッケージは同梱せず、同じプロジェクトに展開された
  **兄弟スキル docextract** を実行時参照する（docextract スキルが必要）。
  venv コマンド **`docsummary`** として任意のディレクトリから実行できる
  （venv 未 activate なら `.venv/Scripts/docsummary`（Windows）/
  `.venv/bin/docsummary`（macOS/Linux）、venv 構築前は
  `python .github/skills/docsummary run ...` で同じ）
- 対応プロバイダ: **openai / azure (Azure OpenAI) / gemini / anthropic**。
  追加ライブラリ不要（標準ライブラリのみで REST API を呼ぶ）
- 秘密情報（API キー）は **`.env`（既定）または環境変数**で渡す。
  ツールは値を表示・保存しない

## 前提

1. 環境構築済み（@skill-setup。`python .github/skills/docextract setup --check` で確認）
2. 文書が抽出・登録済み:

   ```bash
   docextract extract --dir 資料/ -r
   docextract docagent sync
   ```

3. LLM 接続設定済み（下の「接続設定」参照）

## 接続設定（.env）

```bash
docsummary config --check   # 設定状態の確認（キーの値は表示されない）
docsummary config --init    # プロジェクトルートに .env / .env.example の雛形を作成
```

`.env` に使うプロバイダ 1 つ分のキーを記入する（複数設定した場合は
`DOCSUMMARY_PROVIDER` で選択）:

| プロバイダ | 必須 | 任意（既定値） |
| --- | --- | --- |
| openai | `OPENAI_API_KEY` | `OPENAI_MODEL` (gpt-4o-mini), `OPENAI_BASE_URL` |
| azure | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT` | `AZURE_OPENAI_API_VERSION` (2024-10-21) |
| gemini | `GEMINI_API_KEY` | `GEMINI_MODEL` (gemini-2.0-flash) |
| anthropic | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` (claude-opus-4-8) |

**秘密情報の扱い（エージェント向けの規律）**: `.env` は API キー等を含むため
**Read しない・cat しない・値を会話に出さない**。設定の有無は必ず `config --check` の
出力で判断し、キーの記入は利用者自身に依頼する。`.env` は `.gitignore` に追加する。

## 使い方 — 対象の指定

```bash
docsummary run <doc_id> [<doc_id> ...]   # 特定の文書（ID は docsummary list で確認）
docsummary run --dir 資料/設計書/         # 元ファイルがフォルダ配下にある登録文書
docsummary run --pending                 # 未要約・陳腐化した文書だけ（差分運用の既定）
docsummary run --all                     # 全登録文書
```

- 既定では **fresh（要約済みで内容が変わっていない）文書はスキップ**する。
  再要約したいときは `--force`
- `--dry-run` で対象一覧だけ確認できる（LLM を呼ばない・API キー不要）
- `--provider` / `--model` で接続設定を一時的に上書きできる
- 長い文書は先頭 `--max-input-chars`（既定 24,000 字）だけを渡し、
  要約にその旨を明記させる

出力:

- 要約本文: `.docextract/summaries/<doc_id>.md`
- メタデータ（内容ハッシュ・プロバイダ・モデル・日時）: `.docextract/store/summaries.json`

## 状態確認・参照

```bash
docsummary list          # 全登録文書の要約状態（fresh / stale / none）
docsummary show <doc_id> # 要約 Markdown を表示
```

すべてのサブコマンドは `--json` で機械可読な JSON を出力する（エージェント向け）。

## 要約の観点とカテゴリー

出力の構造（メタ表・カテゴリー行・本文）はツールが固定するので、定義するのは
次の 2 つ（どちらも既定はスキル同梱、`.docextract/store/` に置けば上書き）:

- **要約の観点** `summary_guide.md` — どの観点で内容を拾うか（目的・結論・仕様・
  課題・未確定事項など）。LLM への指示になる
- **カテゴリー** `summary_categories.json` — 要約をどの既定カテゴリーへ分類するか
  の統制語彙。LLM が 1 つ選び、docextract の doctype と同じ仕組みで表記揺れを
  正規化する（語彙外・欠落は「未分類」）

観点・カテゴリーのどちらを変えても既存要約は stale になり、`--pending` で
再要約対象になる。カテゴリーは `docsummary list` / 各要約の先頭メタ表に出る。

## トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| `LLM の接続設定が見つかりません` | `docsummary config --init` で雛形を作り、キーを記入してもらう |
| `複数のプロバイダが設定済み` | `--provider` か `DOCSUMMARY_PROVIDER` で選択 |
| `HTTP 401/403` | キーの値・権限を利用者に確認してもらう（値は表示しない） |
| `HTTP 429 / 5xx` | 自動で 1 回再試行される。続くなら時間をおいて再実行 |
| `result.json が見つかりません` | 元文書を再抽出して登録し直す（docextract → docagent sync） |
| 文書が対象に出ない | `docsummary list` で登録を確認。未登録なら docagent sync |
