# 標準パック設計メモ — 様式・メタモデルのプロジェクト横断標準化

status: draft（レビュー用設計メモ。実装前）
作成日: 2026-07-05 / 最終更新: 2026-07-05（レビュー決定を全件反映: 継承チェーン・enum 丸め・パック specdb 正本化・L2 status 限定・block 規約。未解決事項なし → 実装可）

## 1. 背景と目的

現状、各プロジェクトの `.specdb/` は scaffold の**コピー**で始まり、以後は独立に分岐する。
このため (a) 設計書様式がプロジェクト間で揃わない、(b) メタモデルの統制
（誰が種別・属性を変えてよいか、後方互換をどう守るか）が仕組みとして存在しない、
(c) プロジェクト横断の集計（共通データ項目辞書・システム間 IF 台帳）が原理的に不可能、
という課題がある。

本メモは、様式とメタモデルの共通部分を**バージョン付きの配布物「標準パック」**として
切り出し、プロジェクトは「参照 + 追加差分」だけを持つ構造の設計を定める。

設計の不変条件（これがすべての規則の根拠）:

> **プロジェクトで妥当なデータは、標準パックの範囲においても常に妥当である。**
> （プロジェクトは標準を*厳格化*できるが*緩和*できない）

この不変条件が守られる限り、標準パックのテンプレート・文書定義・横断集計ツールは
どのプロジェクトのデータに対しても無修正で動作することが保証される。

## 2. 用語

| 用語 | 意味 |
|---|---|
| 標準パック（pack） | メタモデルコア + 文書カタログ + テンプレート + 準拠規則の versioned な配布単位 |
| オーバーレイ | プロジェクト側 `.specdb/` が持つ、パックへの追加・厳格化の差分 |
| 実効メタモデル | パックのメタモデルにオーバーレイをマージした結果。engine の検証はこれで行う |
| 準拠検証 | 「このプロジェクトは標準パック vX に準拠している」ことの機械検査 |
| 厳格化 / 緩和 | 妥当なデータの集合を狭める変更 / 広げる変更 |

## 3. パックの構成

```
<パック名>/
  pack.yaml                     # パック宣言（§4）
  metamodel/
    core.yaml                   # 共通メタモデル（種別・関係・名前空間予約）
  documents/
    basic-design.yaml           # 標準文書カタログ（abstract 定義。§6）
    table-spec.yaml
    screen-spec.yaml
  templates/
    _house-style.html.j2        # ハウススタイル部品（表紙・改訂履歴・印刷 CSS 等）
    basic-design.html.j2        # 文書種別ごとの本文テンプレート
    table-spec.md.j2
  conformance/
    rules.yaml                  # データ・文書レベルの準拠規則（§7.2）
  migrations/
    1.x-to-2.0.md               # メジャーアップ時の移行手順（将来はスクリプト）
```

配布形態は当面 2 つ:

1. **スキル同梱**（現行 scaffold と同じ経路）: `build_skill.py` がパックを
   `.claude/skills/specdb/packs/<パック名>/` へ展開する
2. **リポジトリ内 vendored**: `.specdb/packs/<パック名>/` にコピーを置く
   （オフライン・監査要件のあるプロジェクト向け）

将来: 社内 Git リポジトリの tag 参照、pip パッケージ。

### 3.1 パック自体の正本管理 — specdb で正本化する（決定事項）

パックの内容は**最初から specdb データとして正本化**する（ドッグフーディング）。
ただし配布と消費の契約は §3 のファイル群のまま — 鍵は次の分離:

> **正本 = パック開発リポジトリの `.specdb/`。
> 配布物（pack.yaml・documents/・conformance/rules.yaml）= そこから生成されたビュー。**

```
spec-standard/（パック開発リポジトリ）
  .specdb/                      # ★正本。extends なしのスタンドアロン specdb
    metamodel.yaml              #   パック記述用メタモデル（下記）
    items/doc-type/…            #   文書種別（章構成・必須パラメータ・採番規則）
    items/conformance-rule/…    #   準拠規則（attribute_rules 等）
    items/style-part/…          #   様式部品の台帳（実体 j2 への参照）
    relations/…                 #   文書種別→様式部品、規則→対象種別 等
  templates/*.j2                # テンプレート実体はファイルのまま（コードと同格）。
                                #   style-part アイテムが source で参照する
  dist/<パック名>/              # ★配布物。specdb generate で生成（直接編集しない）
    pack.yaml
    documents/*.yaml
    conformance/rules.yaml
    templates/                  #   実体ファイルのコピー
```

この構造が与えるもの:

- **循環依存の遮断**: 消費側 engine が読むのは生成済みの素の YAML であり、
  パックの `.specdb` は extends を持たないスタンドアロン。engine の
  パック対応（§10）が無くてもパック自身は検証・生成できる
- **標準の改訂管理が specdb の流儀に乗る**: パック改版 = baseline diff。
  「標準改訂通知書（前版からの変更点一覧）」も生成ビューとして配布物に含められる
- **標準自体に status フローが効く**: 文書種別・規則の追加は `review` で入り、
  標準化委員会のレビューを経て `approved` → リリース、が機械的に強制される
- テンプレート（.j2）はコードと同格の扱いでファイルのまま。specdb 側には
  style-part アイテムとして台帳化し、`source` で実体を指す（実体の重複管理を
  しない）

消費側プロジェクトから見れば §4〜§7 の契約は一切変わらない。

## 4. pack.yaml スキーマ

```yaml
# --- 識別 ---
pack: div-finance-std           # パック名。[a-z0-9-]+。extends での参照名
version: "1.2.0"                # semver（§8 の互換性規約に従う）
description: 金融事業部 設計書様式標準
requires_engine: ">=1"          # 前提とする metamodel version（engine 能力）
extends: jp-sier-std@2.0        # 親パック（任意）。パック自身も継承できる（§5.1）

# --- 内容の所在（省略時は下記の既定値） ---
metamodel: metamodel/core.yaml  # 単一ファイル or リスト（リストは記載順にマージ）
documents: documents/           # 標準文書カタログのディレクトリ
templates: templates/
conformance: conformance/rules.yaml   # 無ければ準拠検証は L1（§7.1）のみ

# --- 予約（任意） ---
reserved_namespaces:            # プロジェクトが再定義できない名前空間
  std: 標準共通
id_bands:                       # 採番戦略（課題1対応の布石。§9 参照）
  # 名前空間ごとの ID 帯の割当規則。当面は宣言のみで engine は未使用
  policy: namespace             # namespace | central | uuid

# --- 互換性 ---
migrations:
  - { from: "1.*", to: "2.0", guide: migrations/1.x-to-2.0.md }
```

制約:

- `pack` `version` `description` は必須。他は省略可（既定パスを使う）
- pack.yaml 自体の検証は準拠検証コマンドの起動時に行い、不正は即 error
  （コード `STD-E001` 系。§7.3）

## 5. プロジェクト側の宣言と解決

### 5.1 extends 宣言

```yaml
# .specdb/metamodel.yaml
version: 1
extends: div-finance-std@1.2    # 「パック名@major.minor」。patch は固定しない
# extends: ../spec-standard     # 開発時は相対パス直接参照も可
item_types:
  batch-job:                    # プロジェクト固有の追加種別
    ...
```

**継承は単一親のチェーン**とする（決定事項）。各層の `extends` は 1 つだけだが、
パック自身が親パックを `extends` できるため、任意の深さの層が組める:

```
jp-sier-std（全社標準）
  ↑ extends
div-finance-std（事業部標準）
  ↑ extends
プロジェクトの .specdb/metamodel.yaml
```

- プロジェクトは**最も近い層だけ**を参照する（事業部パック）。全社パックの
  存在は事業部パックの pack.yaml が知っている — プロジェクトが継承経路を
  書き換えることはできない（統制上の要点）
- 単一親チェーンなのでダイヤモンド継承は起きない。「複数の標準を混ぜたい」は
  上位層（事業部パック）側で取り込んで解決する
- チェーンの循環は error（`STD-E003`）。深さの上限は設けない（実用上 2〜3 層）
- `extends` が無い metamodel は従来どおり完全にスタンドアロン（後方互換）

### 5.2 パックの解決順序

1. 相対/絶対パス指定ならそのまま（開発モード）
2. `.specdb/packs/<パック名>/`（vendored）
3. 展開済みスキルの `packs/<パック名>/`（cwd から上方探索。venv コマンドの
   既存の探索と同じ機構に載せる）
4. 環境変数 `SPECDB_PACK_PATH`（`;` 区切りの追加検索パス）

見つかった pack.yaml の `version` が `extends` の major.minor と一致しなければ
error（`STD-E002`）。パックが親を持つ場合は同じ解決手順を再帰的に適用し、
チェーン全体（ルートまで）を解決してからマージに進む。途中のどの層でも
解決失敗・バージョン不一致は同じコードで error にする（診断には層名を含める）。

### 5.3 ロックファイル

`.specdb/pack.lock`（機械生成。直接編集しない）:

```yaml
chain:                          # 継承チェーン全体を記録（近い層から順）
  - pack: div-finance-std
    resolved_version: "1.2.3"
    content_hash: sha256:...    # パック全ファイルの正規化ハッシュ
    resolved_from: .specdb/packs/div-finance-std
  - pack: jp-sier-std
    resolved_version: "2.0.1"
    content_hash: sha256:...
    resolved_from: .specdb/packs/jp-sier-std
```

- 解決結果が lock と食い違えば warn（`STD-W003`）、`--frozen` 指定時は error。
  CI は `--frozen` で回し、パック差し替えの混入を検出する
- lock 更新は明示操作（`specdb pack lock`）のみ

## 6. マージ規則

実効メタモデル = 継承チェーンを**ルート（全社）から順に**重ねたもの:

```
全社パック → 事業部パック → プロジェクト metamodel.yaml
（左が土台、右がオーバーレイ。近い層ほど後勝ち）
```

マージは**宣言単位**（種別 → 属性 → オプション）で行い、各レベルで
「追加は自由・緩和は error」を機械判定する。**同じ規則がすべての層に一様に
適用される** — 事業部パックは全社パックに対するオーバーレイとして §6.1〜6.3 の
規則に従い、プロジェクトは「事業部までマージした結果」に対して従う。
つまり事業部パックも全社標準を緩和できない（準拠は推移的:
プロジェクト準拠 ⇒ 事業部準拠 ⇒ 全社準拠）。

違反の診断には**どの層がどの層の宣言を緩和したか**を含める
（例: `STD-E102 [div-finance-std → jp-sier-std] screen.description の required 緩和`）。

### 6.1 item_types

| 操作 | 可否 | 備考 |
|---|---|---|
| 新種別の追加 | ○ | プロジェクト固有種別。名前がパック種別と衝突したら「再宣言」扱い |
| 標準種別への属性追加 | ○ | 追加属性は required でもよい（厳格化） |
| 標準属性の kind 変更 | × `STD-E101` | データ互換を壊す |
| required: true → false | × `STD-E102` | 緩和 |
| required: false → true | ○ | 厳格化 |
| unique の除去 | × `STD-E103` | 緩和 |
| unique の付与 | ○ | 厳格化 |
| enum values の削除 | ○ | 厳格化（使用中なら通常のデータ検証で error になる） |
| enum values の追加 | △ `STD-E104` | 標準側が当該属性に `extensible: true` を宣言した場合のみ ○ |
| id_prefix / sequence の変更 | × `STD-E121` | 横断の ID 一貫性を壊す |
| label / label_field の上書き | ○ | 表示のみに影響 |
| warn_if_unreferenced の上書き | ○ | warn の出方のみに影響 |
| 標準種別の削除 | ×（そもそも表現不可） | 不要な種別は「使わない」だけでよい |

`extensible: true` は標準パック側の属性宣言に書く新オプション:

```yaml
# パック側
attributes:
  type: { kind: enum, values: [数値, 文字列, 日付, 真偽], extensible: true }
```

extensible な enum を拡張したプロジェクトのデータを横断集計する側は、
当該属性を「開いた列挙」として扱い、**標準宣言に無い値は「その他」に丸める**
（決定事項）。丸めた場合、集計器は「その他」の件数に加えて元値ごとの内訳を
付録に出力する（丸めによる情報喪失を可視化し、頻出する拡張値を次期標準への
昇格候補として吸い上げる経路にする）。

### 6.2 relation_types

| 操作 | 可否 | 備考 |
|---|---|---|
| 新関係の追加 | ○ | endpoint に標準種別を使ってよい |
| endpoint（from/to）への種別追加 | ○ | 例: `constrains.to` に自種別 batch-job を足す。標準データの妥当性は不変 |
| endpoint からの種別削除 | × `STD-E111` | 緩和（標準妥当データが不正になる） |
| cardinality の厳格化（min↑ / max↓） | ○ | |
| cardinality の緩和（min↓ / max↑） | × `STD-E112` | |
| ordered: false → true | ○ | 意味の追加 |
| ordered: true → false | × `STD-E113` | 並び情報の喪失 |
| embedded 宣言の変更 | × `STD-E113` | 記述形式の互換を壊す |
| 関係属性 | — | §6.1 の属性規則と同じ |

注: 「endpoint 追加は緩和では？」— 不変条件は「*プロジェクトで妥当なデータが
標準の範囲で妥当*」であり、endpoint 追加後もパック定義の endpoint を使う
データはすべて従来どおり妥当。標準テンプレートは知らない種別との関係を
単に描画しないだけなので、不変条件を破らない。

### 6.3 namespaces / documents / templates

- **namespaces**: 加法マージ。上位層の `reserved_namespaces` と同名の再宣言は
  error（`STD-E131`）。ラベル違いの同名宣言は warn
- **documents**（§6.4 参照）: チェーン全層の文書カタログ + プロジェクト
  documents/。同名は近い層が優先（オーバーライド）
- **templates**: Jinja2 の検索パスを「プロジェクト `templates/` → 事業部パック
  `templates/` → 全社パック `templates/`」の順にする（`ChoiceLoader` 相当。
  generate.py の `FileSystemLoader` をリスト化するだけ）。
  - 加えて各パック層は `std/`（直近層）・`std2/`（その親）… の**プレフィックス
    でも参照可能**にする（`PrefixLoader`）。同名テンプレートを部分上書きする
    際に親版を明示参照するために使う（下記 block 規約）
  - 同名上書きは可。ただし `_` 始まり（ハウススタイル部品）の上書きは、
    **パック層どうしは許可（silent）**、**プロジェクト層による上書きは
    warn（`STD-W301`）**。事業部が全社様式を統制下でカスタマイズするのは
    パックの存在意義そのもの（版管理・レビューを経る）だが、プロジェクトの
    独断による様式逸脱は可視化する、という切り分け

  **block 規約（決定事項）** — パックの文書テンプレートは以下の標準 block を
  定義しなければならない（パック品質の規約。準拠検証ではなくパック開発側の
  リリースチェックで担保する）:

  | block 名 | 内容 |
  |---|---|
  | `cover` | 表紙（題名・文書番号・版・ハンコ枠） |
  | `revision_history` | 改訂履歴表 |
  | `toc` | 目次 |
  | `preface` | 前書き（目的・範囲・前提） |
  | `chapters` | 本文の章（内側に文書種別ごとの章 block を切ってよい） |
  | `appendix` | 付録（出典一覧等） |

  下位層（下位パック・プロジェクト）のカスタマイズは**全置換ではなく
  `{% extends %}` + block 上書き**を正の手段とする:

  ```jinja
  {# プロジェクト templates/basic-design.html.j2 #}
  {% extends "std/basic-design.html.j2" %}   {# ← プレフィックスで親版を参照 #}
  {% block preface %} …プロジェクト固有の前書き… {% endblock %}
  ```

  同名上書きテンプレートが `{% extends "std/…" %}` を含まない**全置換**の場合は
  warn（`STD-W303`）— ファイル丸ごと fork による様式ドリフト（標準化の敵）の
  可視化。検出はテンプレートソースの extends 宣言の有無で行う（ベストエフォート）

### 6.4 文書カタログの継承

パック側の文書定義は**雛形（abstract）**として書ける:

```yaml
# パック documents/basic-design.yaml
abstract: true                  # 直接生成不可。プロジェクトでの実体化が必要
title: システム基本設計書（{system_name}）
output: 基本設計書_{system_name}.html
template: basic-design.html.j2
doc_no: { pattern: "SD-[A-Z]{2,4}-\\d{3}" }   # 採番規則（準拠検証 L2 が使う）
params:                         # 実体化時にプロジェクトが埋める必須パラメータ
  required: [system_name, doc_no, version, preface.purpose, preface.scope]
```

プロジェクト側は穴埋めだけ:

```yaml
# .specdb/documents/basic-design.yaml
from_standard: basic-design
system_name: ○○受発注システム
doc_no: SD-ORD-001
version: "1.0"
preface: { purpose: …, scope: …, premise: … }
```

マージは浅いオーバーレイ（プロジェクト値が勝つ）。`params.required` の欠落は
error（`STD-E202`）、`doc_no.pattern` 違反も error（`STD-E203`）。
`abstract: true` の文書は実体化されない限り生成対象に入らない。
プロジェクト独自文書（`from_standard` なし）は従来どおり自由。

## 7. 準拠検証の仕様

新コマンド: **`specdb conform`**（内部的には engine のロード時マージ + 追加検査）。
出力形式・exit code は engine と同じ規約（error ≥1 で exit 1 → CI ブロック）。
`specdb engine` / `specdb generate` も `extends` があれば L1 を暗黙に実行する
（マージなしには実効メタモデルを作れないため）。

### 7.1 L1: メタモデル準拠（マージ時・常時）

§6 の表の error/warn をそのまま検査する。すべて静的（データ不要）。

### 7.2 L2: データ・文書準拠（conformance/rules.yaml）

パックが宣言する、データと生成文書に対する規則。v1 のスキーマ:

```yaml
# conformance/rules.yaml
require_documents: [basic-design, table-spec]   # 実体化必須の標準文書
attribute_rules:                                # 状態に応じた記載必須
  - { type: screen, attribute: description, when_status: [review, approved], level: error }
  - { type: entity, attribute: description, when_status: [approved], level: warn }
status_rules:
  baseline_requires: approved   # ベースラインタグ作成時、review/draft が残れば error
```

- `require_documents`: 標準文書カタログのうち、プロジェクトが必ず実体化すべき
  もの。欠落は `STD-E201`
- `attribute_rules`: 「レビューに出す時点で説明必須」のような**工程連動の記載
  必須**。engine の required（常時必須）より緩い運用要求を表現する。
  条件指定は **`when_status` のみ**とする（決定事項）。汎用の式言語は導入しない
  — 規則自体の静的検証とパック間の互換性判断を守るため。将来、別の条件形が
  必要になったら `when_xxx` の**個別の宣言的キー**として追加する（minor 改版で
  加法的に足せる）
- `status_rules.baseline_requires`: ベースライン（Git タグ）作成の前提検査。
  `specdb conform --for-baseline` で発動
- 章立て検証（テンプレート内の必須章の描画確認）は **v2 に先送り**。
  文書構成はテンプレート自体がパック配布物なので、v1 では「パックのテンプレートを
  使っていること（`STD-W301` が無いこと）」を章立て準拠の代理指標とする

### 7.3 診断コード一覧

| コード | 水準 | 内容 |
|---|---|---|
| STD-E001 | error | pack.yaml が不正・読めない |
| STD-E002 | error | extends のバージョンと解決されたパックが不一致（チェーンのどの層でも） |
| STD-E003 | error | 継承チェーンの循環 |
| STD-W003 | warn | pack.lock と解決結果の不一致（`--frozen` 時は error） |
| STD-E101 | error | 標準属性の kind 変更 |
| STD-E102 | error | required の緩和 |
| STD-E103 | error | unique の除去 |
| STD-E104 | error | extensible でない enum への値追加 |
| STD-E111 | error | 標準関係の endpoint 削除 |
| STD-E112 | error | cardinality の緩和 |
| STD-E113 | error | ordered の解除 / embedded の変更 |
| STD-E121 | error | id_prefix / sequence の変更 |
| STD-E131 | error | 予約名前空間の再宣言 |
| STD-E201 | error | 必須標準文書が実体化されていない |
| STD-E202 | error | 文書実体化の必須パラメータ欠落 |
| STD-E203 | error | doc_no が採番規則パターンに不一致 |
| STD-E211 | error/warn | attribute_rules 違反（level に従う） |
| STD-E221 | error | baseline_requires 違反（--for-baseline 時） |
| STD-W301 | warn | プロジェクト層によるハウススタイル部品（`_*.j2`）の上書き（パック層間は対象外） |
| STD-W302 | warn | 標準で deprecated 指定された種別・属性の使用 |
| STD-W303 | warn | 標準文書テンプレートの全置換（`{% extends "std/…" %}` を使わない同名上書き） |

## 8. バージョニングと移行

semver の各桁に**互換性の意味**を固定する:

| 桁 | 許される変更 | プロジェクトへの影響 |
|---|---|---|
| patch | テンプレート修正・文言・バグ修正。メタモデル不変 | 再生成のみ。無条件に安全 |
| minor | **加法のみ**: 新種別・新関係・新 optional 属性・enum 値追加（extensible）・新文書カタログ | 既存データはそのまま妥当。lock 更新だけ |
| major | それ以外すべて: required 属性の追加、kind 変更、種別の廃止、embedded 変更 | データ移行が必要。migrations/ の手順に従う |

要点: **標準種別への required 属性追加は minor に見えて major**（既存データが
不正になるため）。minor で入れたければ optional + `attribute_rules` の warn で
予告し、次の major で required に昇格させる、という二段階を標準の運用とする。

- deprecated の運用: パックは種別・属性に `deprecated: true` を宣言できる
  （minor で可）。使用は `STD-W302`。削除は次の major で行う
- migrations/ は v1 では**手順書（md）**。データ件数が増えたら mutate.py の
  プラン機構に載せた変換スクリプトへ発展させる

### チェーンをまたぐバージョンの連動

- 下位層（事業部パック）は親を `extends: jp-sier-std@2.0` の形で
  **major.minor で固定**する。親の patch 更新は自動追従、minor 更新は
  下位層の patch 改版（extends の書き換えのみ）、**親の major 更新は
  下位層も major 改版**（親の破壊的変更が透過するため）
- プロジェクトから見た互換性判断は**直近の層のバージョンだけ**見ればよい
  （事業部パックの semver が親の変更を織り込んで改版される、が統制側の責務）
- lock はチェーン全層を記録している（§5.3）ので、どの層の差し替えも
  `--frozen` の CI で検出できる

## 9. 関連する設計判断（スコープ外だが接続点を明記）

- **採番戦略（課題1: 並行採番の衝突）**: pack.yaml の `id_bands` は宣言の
  置き場所だけ確保した。中央採番 / 名前空間帯 / UUID のいずれを標準にするかは
  別メモで設計する。標準パックに載せる理由は「採番規則こそ全プロジェクト共通で
  なければ意味がない」ため
- **横断集計ビュー**: 不変条件により、標準の範囲の集計器は全準拠プロジェクトで
  動く。集計器自体はパックとは別ツール（複数 `.specdb` を読む）として設計する
- **SQLite ビルド / スケール対策（課題2）**: マージは Metamodel ロード時の
  純関数なので、後続のストレージ変更と直交する

## 10. 実装方針（改修ポイント）

| ファイル | 変更 | 規模 |
|---|---|---|
| `specdb/standard.py`（新規） | パック解決・pack.yaml 検証・メタモデルマージ・L1/L2 準拠検査・lock。engine から独立したモジュールに置き、engine は「マージ済み dict を受け取る」だけに保つ | 本体 |
| `specdb/engine.py` | `Metamodel.load` の前段に standard.resolve を挟む（extends が無ければ完全素通し） | 小 |
| `specdb/generate.py` | テンプレートローダを検索パスのリスト化（プロジェクト → パック）+ `std/` プレフィックス参照（PrefixLoader）。文書カタログの from_standard マージ。STD-W303 の extends 検査 | 小 |
| `specdb/__main__.py` | `conform` サブコマンド追加（`--frozen` `--for-baseline`） | 小 |
| `scripts/build_skill.py` | パックをスキル配布物へ含める | 小 |
| `specdb/mutate.py` | 変更不要（実効メタモデルを受け取るだけ） | — |

原則: **engine は今後も「メタモデルの出所」を知らない**。standard.py が
マージ済みメタモデル（ただの dict）を返し、engine の検証ロジックは一切変えない。
これは README の「エンジンは種別を知らない」という設計原則の延長。

パック開発側（§3.1）に追加実装は不要な見込み: 配布物の pack.yaml /
documents/ / rules.yaml は generate.py の既存機構で生成できる
（テンプレートの出力形式は任意なので `.yaml` も生成ビューにできる）。
必要なのはパック記述用メタモデル（doc-type / conformance-rule / style-part）と
dist 生成テンプレートの整備で、これは最初のパックを作る作業そのもの。

### 段階導入と実装状況

1. **Phase 1（完了）**: テンプレート検索パス多層化（PrefixLoader 込み）+ 文書
   from_standard（standard.py / generate.py）
2. **Phase 2（完了）**: extends + マージ + L1 準拠検証 + lock
   （standard.py / conform.py / pack.py / engine.py 結線）
3. **Phase 3（完了）**: L2 準拠規則（attribute_rules / require_documents /
   baseline_requires）は Phase 2 に前倒し実装。加えて **最初の実パック
   jp-sier-std を作成**（`specdb/packs/jp-sier-std/`。画面・テーブル・データ
   項目・業務ルール・外部IF のドメイン + 基本設計書/テーブル定義書/画面仕様書
   の文書カタログ + Excel 風ハウススタイル）。build_skill が `scripts/packs/`
   へ同梱し、消費側は `extends: jp-sier-std@1.0` で解決する。結合テスト
   test_pack_jp_sier_std.py が extends→マージ→L1/L2→生成を端から端まで固定。
4. **Phase 4（未）**: 章立て検証・移行スクリプト・横断集計ツール（extensible
   enum の「その他」丸め + 内訳付録を含む）

各 Phase 完了時に既存プロジェクト（この repo の `.specdb`）を最初の準拠
プロジェクトとして移行し、ドッグフーディングする。

#### Phase 3.5（完了）: パックの specdb 自己正本化

Phase 3 で保留していた §3.1 のパック自己正本化を実装した。摩擦だった点は
次の小改修で解消:

- **generate `foreach`**: 文書定義に `foreach: <種別>` を書くと、その種別の
  アイテム 1 件ごとに 1 ファイルを出力する（`output` は `{属性}` 展開、
  テンプレートに `item` が渡る）。dist の `documents/*.yaml` を doc-type
  アイテムから 1 対 1 で生成できる
- **engine の `kind: list` / `kind: map`**: パックの文書カタログ・準拠規則は
  list/map 値の設定（`params.required`・`doc_no.pattern`・`when_status` 等）を
  持つ。これらを正本アイテムの属性として持てるよう浅い型検査の list/map を
  追加した（README の既知の限界だった「構造化された値」への一歩でもある）
- **`specdb pack build <正本dir> --into <配布dir>`**: 正本 specdb を generate
  して `documents/*.yaml` と `conformance/rules.yaml` を配布パックへ配置する

正本は `specdb/packs-src/jp-sier-std/`（doc-type / conformance-rule /
style-part アイテム + renders 関係。extends なしのスタンドアロン）。配布物の
`documents/*.yaml` と `conformance/rules.yaml` は**そこから生成されたビュー**
（機械生成ヘッダ付き）になった。`pack.yaml`・`metamodel/core.yaml`・
`templates/*.j2` は §3.1 どおり配布物側で直接オーサリングし、テンプレートは
style-part として台帳化する（実体二重管理をしない）。

回帰テスト test_pack_selfhost.py が `pack build` の出力と配布物の data-equal
（no-drift）を固定する。決定事項 3（specdb 正本化）はこれで実装済みとなった。

補足（リポジトリ規約）: 正本 `packs-src/` は他の src（specdb/*.py 等）と同じく
Git 追跡外で、ビルド成果物側（.claude / .github の配布パック）が追跡される。
パック正本自体を版管理したい場合は追跡対象への移設が別途必要（未対応）。

## 11. レビュー決定事項（2026-07-05 — 全論点決着済み）

1. **継承の層構造: 採用**。「全社 → 事業部 → プロジェクト」の 3 層を、
   パック自身が親パックを `extends` する**単一親チェーン**で実現する（§5.1）。
   マージ規則は全層に一様に適用され、準拠は推移的（§6）
2. **extensible enum の横断集計: 未知値は「その他」に丸める**。
   元値の内訳は付録に出す（§6.1）
3. **パック自体の正本管理: 最初から specdb で正本化**。正本 = パック開発
   リポジトリの `.specdb/`（extends なしのスタンドアロン）、配布物 = そこから
   生成されたビュー。消費側の契約は不変で循環依存も生じない（§3.1）
4. **L2 の条件指定: `when_status` 限定**。汎用式言語は導入せず、将来の条件形は
   `when_xxx` の宣言的キーとして加法追加する（§7.2）
5. **テンプレート上書き: block 規約を採用**。パック文書テンプレートは標準
   block（cover / revision_history / toc / preface / chapters / appendix）を
   定義し、下位層は `{% extends "std/…" %}` + block 上書きを正の手段とする。
   全置換は `STD-W303` で可視化（§6.3）

未解決事項は現時点でなし。本メモは実装着手可能な状態にある。
