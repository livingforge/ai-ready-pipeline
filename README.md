# spec-pipeline

プロジェクト資料を **抽出 → 索引化 → 仕様の洗い出し → 仕様の正本化 (.specdb) → 設計書生成** へと
つなぐパイプライン一式です。中核の抽出ツール **docextract** は、Office 文書
(Word / Excel / PowerPoint) と PDF を解析し、**テキスト・表・画像**を抽出して JSON 形式で出力します。

## 対応形式

| 形式 | 拡張子 | 抽出内容 |
|------|--------|----------|
| Word | `.docx` | 段落 (スタイル名付き)・表・インライン画像 — 文書内の出現順 |
| Excel | `.xlsx` `.xlsm` | シートごとの表 (数式は計算結果)・埋め込み画像 (アンカーセル付き) |
| PowerPoint | `.pptx` | テキストフレーム・表・画像 (スライド番号付き)・発表者ノート |
| PDF | `.pdf` | テキスト段落・表 (自動検出)・埋め込み画像 — ページ番号と座標 (bbox) 付き |

## セットアップ

依存は共有仮想環境 `.venv` に入れる（環境を直接汚さない）。[uv](https://docs.astral.sh/uv/) を使う:

```powershell
uv venv                              # プロジェクトルート直下に .venv を作成
uv pip install -r requirements.txt   # .venv へ依存をインストール
```

スキルの起動スクリプト（`run_docextract.py` / `run_docagent.py`）経由なら、この
セットアップは初回に自動で行われる（`.venv` が無ければ uv で作成し、依存を入れて
その python で実行し直す）。`uv` 未導入なら初回に公式インストーラで自動導入する。
下記の `python -m docextract` を直接使う場合は、先に `.venv` を有効化しておくこと
（`.venv\Scripts\Activate.ps1`）。

## 使い方

### CLI

```powershell
python -m docextract report.docx                   # 既定 .docextract/output/ へ
python -m docextract docs\*.pdf slides.pptx        # 複数・ワイルドカード可
python -m docextract --dir 資料フォルダ            # フォルダ内の対応ファイルを一括
python -m docextract --dir 資料フォルダ -r          # サブフォルダも再帰的に
python -m docextract report.pdf --no-ocr           # 画像内テキストの OCR を無効化
python -m docextract report.docx -o out            # 出力先を明示指定
```

`--dir/-d <フォルダ>`（複数指定可）で、そのフォルダ内の対応ファイル
（`.docx` `.xlsx` `.xlsm` `.pptx` `.pdf`）をすべて処理します。`-r/--recursive` で
サブフォルダも走査。位置引数にフォルダを渡しても同じ動作です（`~$` で始まる Office の
一時ファイルは自動的に除外）。

入力ファイルごとに `.docextract/output/<id>/` が作られ、以下が出力されます。`<id>` は
入力ファイルの正規化済み絶対パスのハッシュを含むため、別フォルダの同名ファイルでも
衝突しません（一方が他方を上書きしない）:

```
.docextract/
└── output/
    ├── index.json          # 抽出マニフェスト (id で索引・内容重複の検知)
    └── report_docx_a1b2c3d4/
        ├── result.json      # 抽出結果
        └── images/          # 抽出された画像
            ├── image_001.png
            └── ...
```

抽出結果 (`output/`) と docagent の集約ストア (`store/`) はいずれもプロジェクト
直下の単一フォルダ `.docextract/` にまとまります（ホストプロジェクトの `output/`・
`store/` と衝突しない）。基点を移すには環境変数 `DOCEXTRACT_HOME`（docextract /
docagent 共通）で `.docextract` の場所を差し替えます。バージョン管理から外す場合は
`.docextract/` を `.gitignore` に追加してください。

### Python API

```python
from docextract import extract

data = extract("report.docx")   # output_dir 省略時は .docextract/output/
print(data["summary"])   # 例: {'text': 12, 'table': 3, 'image': 2}
```

## 出力 JSON の形式

```jsonc
{
  "id": "report_docx_a1b2c3d4",   // 衝突しない文書 ID (出力フォルダ名と一致)
  "source": "report.docx",
  "source_abspath": "C:/work/資料/report.docx",  // ID の基準となる正規化済み絶対パス
  "source_hash": "a1b2c3d4",      // source_abspath の sha256 先頭8桁 (ID 末尾と一致)
  "content_hash": "9f86d0…",      // ファイル内容の sha256 (重複・改変の検知)
  "file_type": "docx",
  "metadata": { "title": "...", "author": "...", "created": "...", "modified": "..." },
  "summary": { "text": 3, "table": 1, "image": 1 },
  "elements": [
    // テキスト
    { "type": "text", "content": "月次報告書", "style": "Heading 1",
      "location": { "order": 1 } },
    // 表 (2次元配列)
    { "type": "table", "n_rows": 2, "n_cols": 3,
      "rows": [["項目", "4月", "5月"], ["売上", "100", "110"]],
      "location": { "order": 3 } },
    // 画像 (ファイルとして保存され、相対パスで参照)
    // ocr_text には画像内から OCR で読み取ったテキストが入る
    { "type": "image", "file": "images/image_001.png", "format": "png",
      "width": 60, "height": 40, "ocr_text": "図1: 売上推移 …",
      "location": { "order": 4 } }
  ]
}
```

`location` は形式ごとに異なります:

- **docx**: `order` (文書内の出現順)
- **xlsx**: `sheet` (シート名)、画像は `anchor` (A1 形式のセル)
- **pptx**: `slide` (スライド番号)、`shape_name`
- **pdf**: `page` (ページ番号)、`bbox` (座標 `[x0, y0, x1, y1]`)

## 資料活用エージェント（doc-indexer / spec-extractor / doc-qa + docagent）

抽出だけでなく、システム開発の後工程（現状把握・設計・仕様の洗い出し）で機械的に
使える形に落とすカスタムエージェント一式を同梱している。成果物はすべて**出典
（どの文書のどこ）を辿れる構造化 JSON**で、要約のような人間向け終端フォーマットは
持たない:

```
                         ┌─ docextract（スキル）… 文書 → output/<id>/result.json（出典付き）
利用者 ──▶ doc-indexer ──┤   フォルダ一括抽出 → 索引化＋文書種別の付与（現状把握）
          （現状把握）    └─ docagent（データ操作API）… 集約 JSON（.docextract/store/）
             │
             ├──▶ spec-extractor（仕様の洗い出し）… 文書 → 出典付きファクト（facts.json）
             └──▶ doc-qa（横断 QA）… 質問 → 出典付き回答（search / facts で接地）
```

| エージェント | 役割（工程） | 使いどころ |
|------------|------|-----------|
| **doc-indexer** | フォルダを一括抽出し衝突しない ID で索引化。各資料に**文書種別**を付与、内容重複も把握（要約はしない） | 「資料を取り込んで索引化して」 |
| **spec-extractor** | 文書から機能要件・データ項目・画面/帳票・非機能要件等を**出典付きファクト**に項目化 | 個別/バッチの仕様洗い出し、並列処理 |
| **doc-qa** | 抽出済み資料を横断検索し**必ず出典付きで**問いに答える（無ければ「該当なし」） | 「既存仕様では〜はどうなっている？」 |

Claude Code 上で `@doc-indexer` に「この資料を取り込んで索引化して」と頼めば、抽出から
文書種別付与まで案内してくれる。その後 `@spec-extractor` で仕様を洗い出し、
`@doc-qa` で横断的に調べる。データ操作 API `docagent` はスキルに同梱されており
（`.claude/skills/docextract/scripts/docagent/`）、CLI の詳細・集約 JSON の構造・
文書種別やファクトの扱いは同梱の
[README](.claude/skills/docextract/scripts/docagent/README.md) を参照。

## スキルとしての配布

このツールは Claude Code / GitHub Copilot のエージェントスキルとして配布できる。
単一のソース `src/skills/` (スキル) + `src/agents/` (エージェント) + 本体パッケージ
`docextract/` から、ビルドスクリプトが `.claude/` と `.github/` の両方へ同一内容を
出力する:

```powershell
python scripts\build_skill.py        # --no-zip で zip 作成を省略
```

| 出力先 | 内容 |
|--------|------|
| `.claude/skills/docextract/` `.github/skills/docextract/` | SKILL.md・docs/・scripts/ (本体パッケージ + テスト同梱、自己完結) |
| `.claude/agents/*.md` `.github/agents/*.agent.md` | doc-indexer / spec-extractor / doc-qa の各エージェント定義 (GitHub は `*.agent.md` 拡張子) |
| `.claude/package-meta/docextract/` `.github/package-meta/docextract/` | LICENSE (MIT)・CHANGELOG.md・dependencies.md (依存ライセンス一覧) |
| `dist/docextract-skill.zip` | 上記をまとめた配布物 (展開先リポジトリのルートに解凍するだけで導入完了) |

SKILL.md とエージェント .md は「共通 body + プラットフォーム別フロントマター」から
組み立てる。ソースは 1 スキル / 1 エージェントごとに次の 4 ファイル:

```
src/skills/docextract/  または  src/agents/<エージェント名>/
├── body.md                    # 両プラットフォーム共通の本文
├── frontmatter.common.yaml    # name / description など共通フロントマター
├── frontmatter.claude.yaml    # .claude 固有 (例: tools: Bash, Read)
└── frontmatter.github.yaml    # .github 固有 (例: tools: ['runCommands', 'search'])
```

共通部分は common / body を1箇所直せば両方に反映され、プラットフォーム固有の
フィールド (Claude の `license` や model 指定の表記差など) は各フラグメントに置く。

body.md と docs/ 内の `{{skill_dir}}` はビルド時にプラットフォーム別のスキルパス
(`.claude/skills/docextract` / `.github/skills/docextract`) へ展開される。
エージェントが実行するコマンドは、cwd に依存する `python -m docextract` ではなく、
どこから実行しても動く起動スクリプト
`python {{skill_dir}}/scripts/run_docextract.py` / `run_docagent.py` を使って書く。

### 消費者向け git テンプレートリポ

新規プロジェクトへ導入する配布物は **git テンプレートリポ**として提供する。
`build_template.py` が展開済みの `.claude/.github`（skills + agents + 標準パック
`jp-sier-std` + scaffold）と導入文書から、消費側が「Use this template」で使える
ツリーを組み立てる:

```powershell
python scripts\build_template.py --build   # build_skill を先に走らせてから組む
```

| 出力 | 内容 |
|--------|------|
| `dist/spec-pipeline-template/` | `.claude/` `.github/` + 空の `.specdb` seed + `GETTING-STARTED.md`・`README.md`・`.gitignore` |

テンプレートには**空の `.specdb` seed**（`extends: jp-sier-std`。解凍直後から
`specdb engine` が error 0）を焼き込む。**開発元の「育った自己仕様」は含めない**
（正本アイテムが混入すれば中断する）。開発元自身の仕様データ（dogfooding）は
`examples/spec-pipeline-selfspec/.specdb` に置く。消費側は同梱 seed から始めて、
既存文書を各工程（要件〜詳細設計）へ構造化していく。導入手順は
[src/template/GETTING-STARTED.md](src/template/GETTING-STARTED.md) が単一ソース。

## テスト

```powershell
python -m unittest discover -s tests -v                                    # リポジトリで実行
python -m unittest discover -s .claude\skills\docextract\scripts\tests -v  # ビルド済みバンドルで実行
```

フィクスチャ (docx/xlsx/pptx/pdf) はテスト実行時に生成されるため、バイナリの
コミットもネットワークも OCR モデルも不要。テストはバンドルにも同梱され、
配布先の環境でそのまま自己検証に使える。

`.claude/` `.github/` 配下は生成物なので直接編集しないこと。変更は
`src/skills/` (スキル文書)・`src/agents/` (エージェント定義)・`docextract/` (コード)
に対して行い、再ビルドする。

## 構成

```
docextract/
├── __init__.py          # extract() エントリポイント・形式判定
├── cli.py               # コマンドライン処理
├── models.py            # 抽出要素のデータモデル (text / table / image)
└── extractors/
    ├── docx_extractor.py   # python-docx
    ├── xlsx_extractor.py   # openpyxl
    ├── pptx_extractor.py   # python-pptx
    └── pdf_extractor.py    # pdfplumber + pypdf
```

## 画像内テキストの OCR

スクリーンショットや図として貼り付けられた画像の中のテキスト・表は、
文書ファイル内には「ピクセル」としてしか存在しないため、通常の抽出では取得できません。
既定で各画像に OCR を実行し、読み取れたテキストを画像要素の `ocr_text` に付加します。

バックエンドは `--ocr-backend` で選択できます:

| backend | エンジン | ライセンス | 備考 |
|---------|---------|-----------|------|
| `rapidocr` | RapidOCR (PaddleOCR モデルの ONNX 版) | Apache-2.0 | クロスプラットフォーム。初回実行時にモデルを自動ダウンロード |
| `windows` | Windows 標準 `Windows.Media.Ocr` | OS 機能 | オフラインで動作。Windows の言語パックに依存 |
| `auto` (既定) | rapidocr 優先、なければ windows | — | |

- 言語は `--ocr-lang` (既定 `ja`)
- 無効化する場合は `--no-ocr` (API では `extract(..., ocr=False)`)

## 画像内の表検出 (OSS)

画像として貼られた表を検出し、行・列構造を復元して通常の `table` 要素として出力します。
パイプラインはすべて Apache-2.0 の OSS です:

1. **rapid_layout** — レイアウト解析で画像内の表領域を検出
2. **rapid_table** (SLANet-plus) — 検出領域の表構造を復元し、セル文字列を RapidOCR で認識

検出された表の `location` には元画像への参照が入ります:

```json
{ "type": "table", "n_rows": 2, "n_cols": 3,
  "rows": [["Item", "Q1", "Q2"], ["Sales", "100", "110"]],
  "location": { "order": 2, "from_image": "images/image_001.png",
                "bbox_in_image": [135.6, 302.9, 959.0, 422.9] } }
```

- 無効化する場合は `--no-image-tables` (API では `extract(..., image_tables=False)`)
- 初回実行時にモデル (数十 MB) を自動ダウンロードします

## 依存ライブラリとライセンス

| ライブラリ | 用途 | ライセンス |
|-----------|------|-----------|
| python-docx | Word 解析 | MIT |
| openpyxl | Excel 解析 | MIT |
| python-pptx | PowerPoint 解析 | MIT |
| pdfplumber (pdfminer.six) | PDF テキスト・表 | MIT |
| pypdf | PDF 画像抽出 | BSD-3-Clause |
| rapidocr / rapid-table / rapid-layout | OCR・表検出 | Apache-2.0 |
| Pillow | 画像処理 | MIT-CMU |
| winocr | Windows OCR ラッパー | MIT (エンジンは OS 機能) |

すべて MIT / BSD / Apache-2.0 系で、**商用利用可能**な構成です。

## 備考

- PDF の表は pdfplumber の `find_tables()` による自動検出です (罫線ベース)。罫線のない表は検出できない場合があります。**画像として貼られた表**は画像内の表検出 (rapid_layout + rapid_table) で `table` 要素として抽出されます。
- PDF のテキストは、行間の広さで段落ブロックにまとめて出力します。表領域と重なるテキストは重複を避けるため除外されます。
- Word のテキストボックス内テキストは `style: "textbox"` のテキスト要素として抽出されます。
- 旧形式 (`.doc` `.xls` `.ppt`) は未対応です。事前に新形式へ変換してください。
