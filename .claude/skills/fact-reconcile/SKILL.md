---
name: fact-reconcile
description: "Reconcile extracted spec facts (facts.json) for semantic consistency before they become contextdb items: a deterministic blocking pass clusters duplicates, then an LLM adjudicates same-concept groups, flags contradictions (never auto-resolved), and proposes a canonical term map. Output is a review-only reconcile.json a human curates, then a deterministic contextdb mutate plan. LLM access reuses docsummary's provider/.env config (keys never printed). Use when asked to \"名寄せ / 重複ファクトを統合 / 意味的な一貫性 / reconcile / dedupe facts\". Needs extracted facts (docextract → docagent) and an LLM API key."
license: MIT
---

# fact-reconcile — 抽出ファクトを意味的に名寄せする

ライセンス（MIT）・変更履歴・依存・脅威モデル（秘密の扱い・外部送信・矛盾の非自動解決）は
[package-meta/fact-reconcile/](../../package-meta/fact-reconcile/)（CHANGELOG.md /
dependencies.md / GOVERNANCE.md / threat-model.md）を参照。

docextract → docagent で溜めた出典付きファクト（`facts.json`）は **文書ごとに独立** に
積まれるため、同じ概念が複数文書に出れば重複ファクトが蓄積される。contextdb エンジンは
参照整合性などの **構造的一貫性** は保証するが、「別 ID が同一概念を指す」「相反する値を
主張する」という **意味的一貫性** は検出しない。

このスキルは `facts.json` → contextdb の間に **提案生成ステップ** を挟み、意味的
一貫性を 1 箇所に封じ込める:

1. **ブロッキング**（決定的・LLM 不要）— 同一種別内で「同一かもしれない」候補を束ねる
2. **LLM 裁定**（`docsummary` の provider/.env を再利用）— 候補を「同一概念」と「矛盾」に判定
3. **reconcile.json**（提案・人がレビュー）— concept / contradiction / term_map
4. **mutate plan**（決定的）— 承認済み concept を contextdb の `add-item`（status review）へ

①④が決定的（文書の提示順に依存しない）、②のみ非決定的だが **必ずレビューを
通る提案**。出力を勝手に適用せず、contextdb への反映は人の承認（`contextdb mutate
apply` → `approve`）を通す。

- venv コマンド **`fact-reconcile`**。同じプロジェクトに展開済みの
  **docextract / docsummary スキルが必要**（実行時に参照する）
- `plan` サブコマンドは PyYAML を使う（メタモデル読取）ため、**contextdb スキル**の
  展開も前提（同じ共有 venv に PyYAML を入れている）
- 秘密情報（API キー）は docsummary と共有の **`.env`（既定）または環境変数**で渡す。
  ツールは値を表示・保存しない

共有 venv の console script として任意のディレクトリから実行できる。venv 未 activate なら
`.venv/Scripts/<コマンド>`（Windows）/ `.venv/bin/<コマンド>`（macOS/Linux）で呼ぶ。venv 構築前は
`python .claude/skills/fact-reconcile <サブコマンド>` でも同じ。

## 前提

1. 環境構築済み（@skill-setup。`python .claude/skills/docextract setup --check` で確認）
2. ファクトが抽出・登録済み（@fact-extractor が `facts.json` に蓄積している）
3. LLM 接続設定済み（`fact-reconcile config --check`。設定は docsummary と共有）

## 接続設定（.env）

```bash
fact-reconcile config --check   # 設定状態の確認（キーの値は表示されない）
fact-reconcile config --init    # .env / .env.example の雛形を作成
```

`.env` の形式・対応プロバイダ（openai / azure / gemini / anthropic）は docsummary と同じ。

**秘密情報の扱い（エージェント向けの規律）**: `.env` は API キー等を含むため
**Read しない・cat しない・値を会話に出さない**。設定の有無は必ず `config --check` の
出力で判断し、キーの記入は利用者自身に依頼する。`.env` は `.gitignore` に追加する。

## 使い方 — 名寄せ → レビュー → plan

```bash
# ① 名寄せ提案を作る（対象は既定で全ファクト。--doc / --dir で絞れる）
fact-reconcile analyze --out reconcile.json
fact-reconcile analyze --dry-run           # 候補クラスタだけ表示（LLM 未呼び出し・API キー不要）

# ② 提案を人間可読でレビュー（統合提案・矛盾・用語）
fact-reconcile review reconcile.json

# ③ 承認した提案から contextdb mutate plan を作る（--root で対象 .contextdb を指定）
fact-reconcile plan --in reconcile.json --root .contextdb --out plan.json

# ④ 既存の contextdb ツールで検証 → 適用 → 承認
contextdb mutate apply plan.json --dry-run    # engine error 0 を確認（適用は巻き戻る）
contextdb mutate apply plan.json              # status: review で登録
contextdb approve <id>                        # レビュー後、承認は人が行う
```

- `analyze` は facts が前回と同一なら再生成しない（内容ハッシュ + プロンプト版で
  鮮度判定）。作り直すときは `--force`
- `--block-threshold`（既定 0.5）で候補クラスタの緩さを調整（低いほど束ねやすい）
- すべてのサブコマンドは `--json` で機械可読出力（エージェント向け）

## 出力の性質（重要）

- **concepts**: 2 件以上を「同一概念」と判定した統合提案。plan で 1 アイテムに畳む。
  単独に割れたファクト（重複でない）は載せない — 通常どおり @doc-author が扱う
- **contradictions**: 値が食い違うメンバー。**自動でどちらも選ばず両論併記**し、
  人の判断に委ねる（plan には載せない）
- **term_map**: 表記ゆれ → 正準用語の対応
- plan は **全必須属性を facts から埋められた concept だけ** を `add-item` にする。
  enum の型・一意な physical_name など facts に無い属性が要る種別は `plan` が
  **保留（skipped）** として理由付きで報告する → @doc-author で補完する

## トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| `LLM の接続設定が見つかりません` | `fact-reconcile config --init` で雛形を作り、キーを記入してもらう |
| `複数のプロバイダが設定済み` | `--provider` か `DOCSUMMARY_PROVIDER` で選択 |
| 候補クラスタが 0 個 | facts が少ない/重複が無い。`analyze --dry-run` で候補を確認。閾値を下げる |
| `メタモデルが見つかりません` | `plan --root <.contextdb>` か `--metamodel <path>` を指定。contextdb スキルが必要 |
| plan の ops が 0・保留ばかり | facts に構造化属性が無い種別（データ項目の型等）。@doc-author で補完する |
| 依存 docsummary が見つからない | docsummary スキルを同じプロジェクトに展開する（build_skill.py で生成） |
