---
name: agent-usage
description: Analyze coding-agent telemetry (Claude Code and GitHub Copilot session logs, subagents included) and produce a token, model, tool-call, run-time, and cost summary as summary.json + a self-contained report.html; choose the agent with --agent. Use when asked to "エージェントの利用状況 / トークン消費 / コスト集計 / 利用モデル / ツール呼び出し / 実行時間 / usage レポート / Claude Code や GitHub Copilot の使用量 / AIU 集計".
---

# agent-usage — コーディングエージェントの利用サマリ

コーディングエージェント（**Claude Code** と **GitHub Copilot**）のローカルセッション
記録を解析し、**消費トークン・利用モデル・ツール呼び出し・実行時間・コスト**を集計して
`summary.json` と自己完結の `report.html` に出力するスキル。メインエージェントに
加えサブエージェント（sidechain / child session）も対象にする。`--agent` で対象を選び、
出力（summary.json / report.html）の形は両者で共通。コストの単位だけが異なる（Claude=
USD、Copilot=**GitHub AI Credits 実測**（1 credit = $0.01））。

Python 3.10+ の標準ライブラリのみで動き外部依存はない。ライセンス（MIT）・変更履歴・
依存・脅威モデルは
[package-meta/agent-usage/](../../package-meta/agent-usage/)（CHANGELOG.md /
dependencies.md / GOVERNANCE.md / threat-model.md）を参照。

## データの出どころと重要な前提

- **Claude Code** はセッションを 1 メッセージ 1 行の JSONL で
  `~/.claude/projects/<プロジェクト>/*.jsonl` に保存する。各 assistant 行の
  `message.usage` に入力/出力/キャッシュ書込(5m・1h)/キャッシュ読込トークンと
  `model` が入っている。
- **コストと実行時間は保存されていない**ため算出する:
  - コスト = トークン × モデル別単価（キャッシュは読込/書込で別単価）。単価表は後述の
    `pricing.json`。
  - 実行時間は 3 系統で示す:
    - **経過**（`duration_seconds`）= セッション内 timestamp の差分（壁時計。思考・
      ストリーミング・人間の待ち時間をすべて含む）。
    - **AI稼働**（`ai_active_seconds`）= 人間プロンプトの送信から、そのターンの最後の
      応答までをターンごとに合計した「AI が動いていた時間」。応答完了から次プロンプト
      までの空白（人間が読んで入力する時間）は含めない。ツール実行時間 ≤ AI稼働 ≤ 経過。
    - **ツール実行時間**（`active_seconds`）= ツール実行（`tool_use`→`tool_result`）の合計。
      サブエージェント内部ツールは親の Agent 呼び出しに内包されるため二重計上しない。
- **サブエージェント（Task/Agent）**の内部は、新しい Claude Code（VS Code 版を含む）では
  親の `<session>.jsonl` に混ざらず `<プロジェクト>/<session>/subagents/agent-*.jsonl`
  に分離記録される（各行 `isSidechain: true`・`usage` 付き）。本スキルはこのサブ
  ディレクトリも読み、隣の `agent-*.meta.json`（`toolUseId` / `agentType`）で**親会話の
  当該 Agent 呼び出しにひも付け**、内部トークン・コストを総計へ算入する。古いログ
  （`subagents/` が無い形式）ではサブエージェント内部は不明のままで、`by_agent.subagent`
  が空・HTML に過小評価の注意が出る。モデルティアの混在（opus/sonnet/haiku/fable）も
  モデル別集計で見える。
- **プロジェクト名**は各メッセージの `cwd` を上方探索し **Git リポジトリルート
  （.git を持つ最上位）の名前**に集約する。サブフォルダ（`.contextdb` / `out` 等）で
  作業しても同じリポジトリにまとまる。`.git` が見つからなければ cwd の basename。

> `pricing.json` は単価の**正本**。`verified: false` の間は最新の公開価格に対して
> 検証する運用にすること（未検証だと HTML 上にも警告が出る）。表に無いモデルは
> トークンだけ集計しコストは unknown（「*」印）になる。

## 使い方

セットアップ不要（Python 3.10+ 標準ライブラリのみ、venv 依存なし）。

```bash
# 直近すべてを集計して out/ に summary.json + report.html を出力
python .github/skills/agent-usage/scripts report --out out/

# 直近 30 日、特定プロジェクトだけ、JSON だけ
python .github/skills/agent-usage/scripts report --days 30 --project ai-ready-pipeline --json-only

# 期間指定・別の Claude ディレクトリ・別単価表
python .github/skills/agent-usage/scripts report --since 2026-06-01 --until 2026-06-30 \
    --claude-dir /path/to/.claude/projects --pricing my-pricing.json
```

主なオプション: `--agent` `--claude-dir` `--pricing` `--out` `--days` `--since` `--until`
`--project` `--top` `--json-only`。詳細は `report --help`。

### GitHub Copilot（`--agent copilot`）

VS Code の **Agent Debug Log を有効化**しておくと、Copilot Chat は
`%APPDATA%/Code/User/workspaceStorage/<workspaceId>/GitHub.copilot-chat/debug-logs/<sessionId>/`
に `main.jsonl`（1 イベント 1 行）と `models.json` を書き出す。これを読んで同じ
`summary.json` / `report.html` を出力する。

```bash
# debug-logs を持つ全ワークスペースを集計（credit 表示）
python .github/skills/agent-usage/scripts report --agent copilot --out out-copilot/

# 特定ワークスペースだけ（フォルダパス指定。id 直指定は --workspace-id）
python .github/skills/agent-usage/scripts report --agent copilot --workspace C:/proj/foo --out out-copilot/
```

- Copilot 用オプション: `--storage-root`（workspaceStorage ルート上書き）、
  `--workspace`（対象フォルダ）、`--workspace-id`（id 直指定）。未指定なら debug-logs を
  持つ全ワークスペースを対象にし、`by_project` は VS Code のワークスペースフォルダ名で分ける。
- **コストは GitHub AI Credits（1 credit = $0.01）で表示する**。各 `llm_request` の
  `attrs.copilotUsageNanoAiu`（1 credit = 1 AIU = 1e9 nano）が **Copilot の実測消費**で、
  これを ÷1e9 で credit に換算して正本のコスト値にする。`models.json` の単価は credit 単位
  （例: Opus 4.8 の output は $25/1M = 2,500 credit/1M）。ここから算出した「推定 credit」と、
  キャッシュ読込による「節約 credit」を totals に併記する（`--pricing` は Copilot では使わない）。
- `child_session_ref` が指すタイトル生成・サブエージェントの子ログも読み、本体総計へ
  畳み込みつつ `subagents` に内訳を出す（Claude の sidechain 扱いと同じ）。

### `--top N` — 会話タイムラインの収録件数（既定 100）

`--top` は**会話詳細モーダルにツール/Agent タイムライン（時系列の呼び出し履歴）を
収録する会話数**を、コスト降順の上位 N 件に限定するオプション。既定は **100**。

- **会話行・詳細モーダル自体は常に全会話ぶん**出力される（`--top` では減らない）。
  上位 N 件を超える会話のモーダルでは、タイムラインの代わりに
  「タイムラインは上位会話のみ収録しています」と表示される。
- タイムラインは 1 会話あたり最大 500 イベント（`report.py` の `MAX_TIMELINE`）に
  なり得るため、全会話ぶん埋め込むと `report.html` が肥大化する。既定 100 はその
  肥大化防止と実用上の網羅性のバランス。
- `--top 0` で**全会話にタイムラインを収録**する（大規模ログでは巨大化に注意）。
- タイムライン以外の集計（`tool_calls` などの件数・コスト・時間）は `--top` に
  よらず**全会話で常に正確**。`summary.json` / `conversations.csv` も常に全件。

## 出力

- `summary.json` — `totals`（トークン内訳・コスト・キャッシュ節約額・総時間）、
  `by_model` / `by_project` / `by_day` / `by_agent`(main/subagent) / `by_tool`
  （呼出数・所要時間・エラー数）、`top_sessions`（コスト上位）。機械可読の正本。
- `report.html` — 単一ファイル・外部依存なし・ライト/ダーク対応。カード＋日次コスト
  グラフ＋モデル/ツール/プロジェクト/セッションの各表。
