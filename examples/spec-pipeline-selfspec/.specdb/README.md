# .specdb — DocExtract 資料活用基盤の仕様データ（正本）

このディレクトリは、本リポジトリ（docextract / specdb）**自体の設計**を
仕様データとして管理するデータルート。README 等の記述は各アイテムの
出典 (source) として参照され、設計書はここから生成されるビューになる。

## 語彙（metamodel.yaml）

- **構成要素**: スキル (skill)・エージェント (agent)・ソースモジュール (module)
- **機能** (function): 機能一覧表の正本。実現主体のいない機能は検証 error
- **データ成果物** (artifact): 工程間で受け渡す構造化データ（データ設計の正本）
- **対応文書形式** (file-format) / **設計方針** (design-rule) / **外部ライブラリ** (library)
- 関係: 実現する (realizes)・生成する (produces)・入力とする (consumes)・
  対応する (supports)・利用する (uses)・方針に従う (follows, 埋め込み `rules:`)・
  依存する (depends-on, 埋め込み `depends:`)

## 使い方

```bash
specdb engine      # 検証（error で exit 1）
specdb generate    # 設計書を out/ に生成
specdb visualize   # 対話型グラフ out/specdb.html
specdb diff <tag>  # ベースライン差分
```

`specdb` は共有 venv の console script（bootstrap が install。venv 未 activate
なら `.venv/Scripts/specdb`）。cwd から上方探索して展開済みスキルへ委譲する
ので、プロジェクト配下ならどこからでも実行でき、`--root` 未指定なら
プロジェクトの `.specdb` を自動補完する。venv 準備前のフォールバックは
`python .claude/skills/specdb engine`（GitHub Copilot 環境では `.github` に
読み替え、同一内容）。サブコマンド一覧は `specdb --help`。

生成される設計書:

- `out/基本設計書_DocExtract資料活用基盤.html` — 伝統的な Excel 設計書風の
  自己完結 HTML（表紙・改訂履歴・目次・概要/全体構成図・機能一覧・
  構成要素仕様・データ設計・設計方針・外部ライブラリ・出典一覧付録）
- `out/specdb.html` — 仕様データ全体の対話型関係グラフ

`out/` は生成物なので直接編集しない。仕様変更は items/・relations/ を直して
再生成する（README を変えたら該当アイテムの source も追随させること）。
