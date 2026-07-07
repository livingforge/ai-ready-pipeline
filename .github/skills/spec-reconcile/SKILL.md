---
name: spec-reconcile
description: Reconcile extracted spec facts (facts.json) for semantic consistency before they become specdb items - cluster duplicate/near-duplicate facts with a deterministic non-LLM blocking pass, then use an LLM to adjudicate which facts denote the same concept, flag contradictions (conflicting values, never auto-resolved), and suggest a canonical term map. Output is a review-only reconcile.json proposal that a human curates, then a deterministic specdb mutate plan.json (add-item, status review) validated by specdb mutate apply --dry-run. Closes the semantic-consistency gap that specdb's structural checks miss and makes canonicalization order-independent (feeding documents in sequence no longer changes the merge result). LLM access reuses docsummary's provider/.env config (OpenAI / Azure / Gemini / Anthropic, keys never printed). Use when asked to "名寄せ / 重複ファクトを統合 / 意味的な一貫性 / spec facts の突き合わせ / reconcile / dedupe facts". Requires docextract + docagent facts, and an LLM API key configured via spec-reconcile config.
---

# spec-reconcile — 抽出ファクトを意味的に名寄せする

docextract → docagent で溜めた出典付きファクト（`facts.json`）は **文書ごとに
独立** に積まれるため、同じ概念（同じデータ項目・業務ルール等）が複数文書に
出れば重複ファクトがそのまま蓄積される。specdb エンジンは参照整合性・宣言済み
制約という **構造的一貫性** は順序非依存に保証するが、「別 ID が同一概念を指す」
「相反する値を主張する」といった **意味的一貫性** は検出しない。

このスキルは `facts.json` → specdb の間に **提案生成ステップ** を挟み、意味的
一貫性を 1 箇所に封じ込める:

1. **ブロッキング**（決定的・LLM 不要）— 同一種別内で「同一かもしれない」候補を束ねる
2. **LLM 裁定**（`docsummary` の provider/.env を再利用）— 候補を「同一概念」と「矛盾」に判定
3. **reconcile.json**（提案・人がレビュー）— concept / contradiction / term_map
4. **mutate plan**（決定的）— 承認済み concept を specdb の `add-item`（status review）へ

①④が決定的（文書の提示順に依存しない）、②のみ非決定的だが **必ずレビューを
通る提案**。出力を勝手に適用せず、specdb への反映は人の承認（`specdb mutate
apply` → `approve`）を通す。

- 実行体はこのスキルに同梱（`.github/skills/spec-reconcile/scripts/specreconcile/`）。依存する
  docextract / docagent / docsummary パッケージは同梱せず、同じプロジェクトに
  展開された **兄弟スキル docextract / docsummary** を実行時参照する（両スキルが
  必要）。venv コマンド **`spec-reconcile`** として任意のディレクトリから実行できる
  （venv 未 activate なら `.venv/Scripts/spec-reconcile`（Windows）/
  `.venv/bin/spec-reconcile`（macOS/Linux）、venv 構築前は
  `python .github/skills/spec-reconcile analyze ...` で同じ）
- `plan` サブコマンドは PyYAML を使う（メタモデル読取）。通常は **specdb スキル**が
  同じ共有 venv に入れているので、plan を使うには specdb スキルの展開が前提
- 秘密情報（API キー）は docsummary と共有の **`.env`（既定）または環境変数**で渡す。
  ツールは値を表示・保存しない

## 前提

1. 環境構築済み（@skill-setup。`python .github/skills/docextract setup --check` で確認）
2. ファクトが抽出・登録済み（@spec-extractor が `facts.json` に蓄積している）
3. LLM 接続設定済み（`spec-reconcile config --check`。設定は docsummary と共有）

## 接続設定（.env）

```bash
spec-reconcile config --check   # 設定状態の確認（キーの値は表示されない）
spec-reconcile config --init    # .env / .env.example の雛形を作成
```

`.env` の形式・対応プロバイダ（openai / azure / gemini / anthropic）は docsummary と同じ。

**秘密情報の扱い（エージェント向けの規律）**: `.env` は API キー等を含むため
**Read しない・cat しない・値を会話に出さない**。設定の有無は必ず `config --check` の
出力で判断し、キーの記入は利用者自身に依頼する。`.env` は `.gitignore` に追加する。

## 使い方 — 名寄せ → レビュー → plan

```bash
# ① 名寄せ提案を作る（対象は既定で全ファクト。--doc / --dir で絞れる）
spec-reconcile analyze --out reconcile.json
spec-reconcile analyze --dry-run           # 候補クラスタだけ表示（LLM 未呼び出し・API キー不要）

# ② 提案を人間可読でレビュー（統合提案・矛盾・用語）
spec-reconcile review reconcile.json

# ③ 承認した提案から specdb mutate plan を作る（--root で対象 .specdb を指定）
spec-reconcile plan --in reconcile.json --root .specdb --out plan.json

# ④ 既存の specdb ツールで検証 → 適用 → 承認
specdb mutate apply plan.json --dry-run    # engine error 0 を確認（適用は巻き戻る）
specdb mutate apply plan.json              # status: review で登録
specdb approve <id>                        # レビュー後、承認は人が行う
```

- `analyze` は facts が前回と同一なら再生成しない（内容ハッシュ + プロンプト版で
  鮮度判定）。作り直すときは `--force`
- `--block-threshold`（既定 0.5）で候補クラスタの緩さを調整（低いほど束ねやすい）
- すべてのサブコマンドは `--json` で機械可読出力（エージェント向け）

## 出力の性質（重要）

- **concepts**: 2 件以上を「同一概念」と判定した統合提案。plan で 1 アイテムに畳む。
  単独に割れたファクト（重複でない）は載せない — 通常どおり @spec-designer が扱う
- **contradictions**: 値が食い違うメンバー。**自動でどちらも選ばず両論併記**し、
  人の判断に委ねる（plan には載せない）
- **term_map**: 表記ゆれ → 正準用語の対応
- plan は **全必須属性を facts から埋められた concept だけ** を `add-item` にする。
  enum の型・一意な physical_name など facts に無い属性が要る種別は `plan` が
  **保留（skipped）** として理由付きで報告する → @spec-designer で補完する

## トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| `LLM の接続設定が見つかりません` | `spec-reconcile config --init` で雛形を作り、キーを記入してもらう |
| `複数のプロバイダが設定済み` | `--provider` か `DOCSUMMARY_PROVIDER` で選択 |
| 候補クラスタが 0 個 | facts が少ない/重複が無い。`analyze --dry-run` で候補を確認。閾値を下げる |
| `メタモデルが見つかりません` | `plan --root <.specdb>` か `--metamodel <path>` を指定。specdb スキルが必要 |
| plan の ops が 0・保留ばかり | facts に構造化属性が無い種別（データ項目の型等）。@spec-designer で補完する |
| 依存 docsummary が見つからない | docsummary スキルを同じプロジェクトに展開する（build_skill.py で生成） |
