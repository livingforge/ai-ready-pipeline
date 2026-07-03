# docextract 運用ガイド

## 対応形式と抽出内容

| 形式 | 拡張子 | 抽出内容 |
|------|--------|----------|
| Word | `.docx` | 段落 (スタイル名付き)・表・インライン画像・テキストボックス — 文書内の出現順 |
| Excel | `.xlsx` `.xlsm` | シートごとの表 (数式は計算結果)・埋め込み画像 (アンカーセル付き) |
| PowerPoint | `.pptx` | テキストフレーム・表・画像 (スライド番号付き)・発表者ノート |
| PDF | `.pdf` | テキスト段落・表 (自動検出)・埋め込み画像 — ページ番号と座標 (bbox) 付き |
| 旧 Office (要 Office) | `.xls` `.doc` `.ppt` | **Windows + Microsoft Office 必須**。COM 自動化で OOXML へ変換してから上記と同じ内容を抽出する |

### 旧形式 (`.xls` / `.doc` / `.ppt`) の扱い — Microsoft Office 必須

`.xls` / `.doc` / `.ppt` は OOXML ではなく OLE2/BIFF バイナリ形式のため、
純 Python ライブラリでは読めない。docextract は **Windows 上でインストール済みの
Microsoft Office (Excel / Word / PowerPoint) を COM 自動化**し、旧形式を一時的に
新形式 (OOXML) へ変換してから通常の抽出器へ委譲する。

**前提 (いずれも満たさないと抽出できない):**

- OS が **Windows** であること
- 対応する **Microsoft Office アプリがインストール済み**であること
  (`.xls`→Excel / `.doc`→Word / `.ppt`→PowerPoint)
- **pywin32** が利用可能であること (`pip install pywin32`)

前提を満たさない環境では、未対応形式として黙って弾くのではなく、**「Microsoft
Office が必要」である旨と回避策を含む明確なエラー**で停止する (CLI では該当
ファイルだけ `[NG]` となり、他ファイルの処理は継続・終了コードは非ゼロ)。
Office を用意できない場合は、あらかじめ `.docx` / `.xlsx` / `.pptx` へ変換してから
渡すこと。Office / pywin32 は再現性固定された `requirements.lock` には含まれない
外部前提であり、別途各環境で用意する ([dependencies.md](../../package-meta/docextract/dependencies.md) 参照)。

> **pywin32 は bootstrap でも自動導入されない。** `requirements.lock` に含めない
> 方針のため、旧形式・IRM/RMS 保護文書を扱う前に手動で追加する:
> `uv pip install --python .venv/Scripts/python.exe pywin32` (または
> `pip install pywin32`)。未導入のまま COM 経路に入ると「Office が必要」の
> エラーになるが、そのメッセージにも同じ導入コマンドを併記している。

## 秘密度ラベル・保護文書の扱い (Microsoft Purview / AIP / IRM)

秘密度ラベルは 2 種類あり、docextract は挙動を分ける（**操作者が対象文書への
アクセス権を持つ前提**）。

| 種類 | 実体 | docextract の挙動 |
|------|------|-------------------|
| **ラベルのみ**（暗号化なし） | 文書プロパティ `MSIP_Label_*` | 通常どおり抽出し、ラベルを `metadata.sensitivity` と `index.json` へ**伝播**する |
| **ラベル＋暗号化 (IRM/RMS)** | 本体が RMS 暗号化された OLE2 コンテナ | **Office COM で復号して抽出**する（操作者の権限で復号）。要 Windows + Office + pywin32 |
| **パスワード暗号化** | パスワードで暗号化された OLE2 コンテナ | アクセス権とは別に鍵（パスワード）が要るため抽出せず **`ProtectedDocumentError`** で停止 |

- **保護 (暗号化) の検知**: 抽出前にファイルを検査し、IRM/RMS 暗号化・パスワード
  暗号化を検知して経路を分ける。通常の抽出器へ素通しすると「zip でない」等の
  不明瞭なエラーになるため、検知して「Office が無い」「未対応形式」などと
  **取り違えない**ようにする。
- **IRM/RMS の復号**: 操作者は対象文書へのアクセス権を持つ前提で、その権限で動く
  Office に COM で開かせて復号し、暗号化なしの OOXML へ変換してから抽出する
  （旧形式変換と同じ COM 経路）。Windows / 対応 Office アプリ / pywin32 が無い環境
  では、「Microsoft Office が必要」である旨を含むエラーで fail-closed する
  （その場合は復号済みのコピーを渡す）。
- **パスワード暗号化**: パスワードはアクセス権とは別物で、COM で開くと入力待ちで
  ハングしうる。復号鍵を扱わない方針のため専用エラーで停止する（復号済みのコピーを
  渡すこと）。
- **ラベルの伝播**: 暗号化されていない文書、および復号後にラベルが残っている文書の
  ラベルは成果物へ運ばれる。下流（docagent コーパス／横断検索）が機密文書を機械
  判定でき、**無印のまま検索へ流入するのを防ぐ**。旧形式 (.xls/.doc/.ppt) は COM
  変換後の OOXML からラベルを読み継ぐ（IRM 復号後にラベルが外れる場合は付かないが、
  それは許容）。
- **注意 (格下げ)**: 抽出物 `result.json`・画像・一時 OOXML は**無保護・無暗号の
  平文**であり、元ラベルの暗号化やアクセス制御を継承しない。機密を扱う場合は
  出力先を保護領域に置く・不要になったら破棄する等、運用側で取り扱いを担保すること
  （[threat-model.md](../../package-meta/docextract/threat-model.md) 参照）。

## CLI リファレンス

```
python .github/skills/docextract/scripts/run_docextract.py <入力...> [オプション]

  <入力...>          入力ファイル。複数指定・ワイルドカード可
  -o, --output-dir   出力先ディレクトリ (既定: .docextract/output)
  --no-ocr           画像内テキストの OCR を無効化
  --ocr-lang <lang>  OCR の言語 (既定: ja)
  --ocr-backend      auto | rapidocr | windows (既定: auto)
  --no-image-tables  画像内の表検出を無効化
  -q, --quiet        ファイルごとの [OK] 等の進捗行を抑制 (エラー [NG] のみ stderr)
  --json-summary     終了時に機械可読な 1 行 JSON サマリを stdout へ
```

終了コード: 全ファイル成功で 0、1 つでも失敗すると 1 (失敗ファイルは stderr に `[NG]`)。

### LLM / エージェントに渡すとき — 標準出力を「レシート」にする

既定の標準出力は **1 ファイル 1 行**（`[OK] …`）で、フォルダ一括 (`--dir -r`) だと
件数に比例して膨らむ。これを丸ごと LLM のコンテキストへ流すと圧迫するため、
呼び出し側 (エージェント) は次の規約で受け取る:

1. **`--quiet --json-summary` を付ける。** stdout は次の 1 行だけになる（進捗行は消え、
   エラーは stderr の `[NG]` に残る）:

   ```json
   {"event":"summary","run_id":"run_…","succeeded":12,"failed":1,
    "output_dir":".docextract/output","index":".docextract/output/index.json",
    "log_path":".docextract/output/logs/run_….jsonl",
    "ids":["report_docx_a1b2c3d4", …],
    "failures":[{"source":"broken.doc","error":"Microsoft Office が必要…"}],
    "duplicates":[["a_docx_1111","b_docx_2222"]]}
   ```

2. **中身はサマリに載せない。** 抽出された本文・表・OCR は `index.json` →各 `result.json`
   に既にある。エージェントは `ids` と `index` パスだけ受け取り、必要な文書だけ
   後工程（docagent の `search` 等）で **オンデマンドに** 読む。
3. **詳細な監査が要るときだけ** `log_path`（JSON Lines, 1 実行 = 1 ファイル）を辿る。
   生 stdout をコンテキストに残す必要はない。

> 標準出力をファイルへ退避してから最終行だけ読むのも有効:
> `run_docextract.py --dir docs -r --quiet --json-summary > run.out`（`run.out` は
> 実質 1 行。`--quiet` 単独ならヒト向けに静かにするだけで JSON は出ない）。

### 初回セットアップ・依存ノイズの扱い（標準出力を汚さない）

`[OK]` 行のほかに標準出力を膨らませる要因は 2 つあり、いずれも既定で退避される:

1. **初回セットアップ（uv venv / 依存インストール数百 MB）** — 進捗が極めて冗長。
   **非対話（パイプ/エージェント）実行では出力をログへ退避**し、標準出力には要点しか
   残さない。詳細は `<home>/logs/bootstrap.log`（`DOCEXTRACT_HOME` に追従）。失敗時は
   ログ末尾を stderr に出す。**対話端末ではライブ進捗をそのまま表示**する。
2. **OCR / 表検出の依存ノイズ**（onnxruntime の警告、モデル初回ダウンロードのログ）
   — RapidOCR / rapid_layout / rapid_table / onnxruntime のログ重大度を **ERROR** に
   寄せて抑制する。デバッグで戻したいときは `DOCEXTRACT_VERBOSE_DEPS=1`。

**warm-up（推奨運用）**: 初回の巨大出力とモデル取得を抽出本番から切り離すため、
**承認付きで一度だけ小さな抽出を流してキャッシュを温めて**おく。以降のエージェント
実行はセットアップ済み＆ノイズ抑制済みで、標準出力は前節のサマリ 1 行に収まる。

```bash
# 一度だけ (承認付き) — venv 構築・依存導入・OCR/表モデル取得をここで済ませる
DOCEXTRACT_AUTOINSTALL=1 python .github/skills/docextract/scripts/run_docextract.py <小さな1ファイル> --quiet --json-summary
# 以降は承認フラグ不要・静か
python .github/skills/docextract/scripts/run_docextract.py --dir <フォルダ> -r --quiet --json-summary
```

## 出力レイアウト

```
<output-dir>/
├── index.json               # 抽出マニフェスト (id で索引・内容重複の検知)
└── <id>/                    # 例: report_docx_a1b2c3d4/ (パスハッシュ入りで衝突しない)
    ├── result.json           # 抽出結果 (UTF-8, ensure_ascii=False)
    └── images/               # 抽出された画像 (image_001.png, ...)
```

フォルダ名 (＝文書 `id`) は入力ファイルの正規化済み絶対パスのハッシュを含むため、
別フォルダにある同名ファイルでも衝突せず、一方が他方を上書きしない。内容が同一の
ファイル (別名コピー等) は `index.json` の `content_hash` 一致で検知でき、抽出時に
`[!] 内容が同一の文書があります` として知らせる。

`--output-dir` を省略した既定の出力先は `.docextract/output/`。docagent の集約
ストア (`.docextract/store/`) と合わせ、プロジェクト直下の単一フォルダ
`.docextract/` にまとまる（ホストプロジェクトの `output/`・`store/` と衝突しない）。
基点を移したいときは環境変数 `DOCEXTRACT_HOME`（docextract / docagent 共通）で
`.docextract` の場所を差し替えられる。バージョン管理から外す場合は
`.docextract/` を `.gitignore` に加える。

## 数値ガードと設定ファイル（`<home>/config.json`）

docagent の参照系コマンドは、LLM/エージェントへ渡す標準出力が肥大化しないよう
**数値ガード**を持つ。既定値は `<home>/config.json`（`DOCEXTRACT_HOME` 準拠、`init`
が生成）で一元管理し、`doctypes.json` と同様に**利用者が編集できる**。優先順位は
**CLI フラグ（明示） > config.json > 組み込み既定**。

| キー | 既定 | 意味 |
|------|------|------|
| `ceiling_chars` | 30000 | `--json` 出力の文字数上限。超えると**拒否して絞り方を案内**する（`0` で無効化）。Claude Code の Bash 出力既定（30,000 字で中央切り詰め）に一致 |
| `text_max_chars` | 20000 | `text` の既定最大文字数（`0` で全文） |
| `prep_max_chars` | 8000 | `prep` が返す本文抜粋の最大文字数 |
| `search_max_hits` | 50 | `search` が返す最大ヒット数 |
| `list_preview_chars` | 200 | `list` / `query` の preview 短縮長 |
| `fact_evidence_chars` | 200 | `facts` 一覧の evidence 短縮長 |
| `preview_chars` | 600 | 登録時に result.json から作る preview の長さ |

`ceiling_chars` を超える出力になったコマンドは、そのままではホスト（Claude Code の
Bash 出力／GitHub Copilot のコンテキスト）で**黙って切り詰められ欠落する**ため、手前で
`exit 2` して stderr に「どう絞るか（`--max-chars` を下げる・`--offset` で分割・`--doc`
や `--type` で絞る）／全出力を強制するには `--stdout`」を案内する。GitHub Copilot 等の
より狭いツールを主対象にする場合は、`ceiling_chars` を 16000 程度へ下げると安全側に倒せる。
別の設定ファイルを使うときは `--config <パス>` で明示指定できる。

## OCR バックエンドの選択

| backend | エンジン | 特徴 |
|---------|---------|------|
| `rapidocr` | RapidOCR (PaddleOCR モデルの ONNX 版) | クロスプラットフォーム。初回にモデルを自動ダウンロード |
| `windows` | Windows 標準 `Windows.Media.Ocr` | 完全オフライン。Windows の言語パックに依存 |
| `auto` (既定) | rapidocr → windows の順にフォールバック | |

## 画像内の表検出パイプライン

1. **rapid_layout** — レイアウト解析で画像内の表領域 (bbox) を検出 (スコア 0.5 未満は棄却)
2. **rapid_table** (SLANet-plus) — 領域を切り出して表構造を復元、セル文字は RapidOCR で認識
3. HTML → `rows` (2次元配列) に変換して `table` 要素として出力

依存パッケージが無い環境やモデル未取得での失敗時は静かにスキップされ、
抽出全体は失敗しない (表要素が出ないだけ)。

## 自己検証 (バンドル同梱テスト)

バンドルには単体テストが同梱されており、配布先の環境でそのまま実行できる。
導入直後や依存更新後の動作確認に使う:

```bash
python -m unittest discover -s .github/skills/docextract/scripts/tests -v
```

数秒で完了する。フィクスチャ (docx/xlsx/pptx/pdf) はテスト実行時に生成される
ため、ネットワークも OCR モデルも不要。

## アンチパターン（やりがちだが誤り）

境界（[Limitations](../SKILL.md)）と対で、**避けるべき使い方**を挙げる。

| やりがちな誤り | なぜ誤りか | 正しいやり方 |
|----------------|-----------|--------------|
| 旧形式 `.doc` / `.xls` / `.ppt` を Office の無い環境で渡す | 変換に Microsoft Office (COM) が必要。無ければ「Office が必要」と明確に失敗する（無音では落ちない） | Windows + Office を用意するか、先に新形式（`.docx`/`.xlsx`/`.pptx`）へ変換してから渡す |
| `location` を手組みして result.json に書き足す | 座標系は形式ごとに異なり、手書きは接地（グラウンディング）を壊す | docagent の `search` が返す `location` をそのまま使う |
| スクリプトのあるフォルダへ `cd` してから実行 | ランチャーは cwd 非依存。`.docextract/` がスクリプト側にできて散らばる | **常にプロジェクトルート**で実行し、入力は相対/絶対パスで渡す |
| result.json を手で編集して「修正」する | 抽出物は再生成される派生物。手編集は次回抽出で失われる | 元文書を直すか、後工程（docagent のファクト）で補正する |
| `DOCEXTRACT_AUTOINSTALL=1` を常時 export する | 承認ゲートを恒常的に無効化し「既定で安全」を失う | 承認できた**その実行に限り**付与する（環境変数を残さない） |
| OCR テキストを正本として扱う | OCR は不完全で誤認識しうる（Limitations 参照） | `ocr_text` は補助情報。重要判断は原文・原表を確認する |
| 別フォルダの同名ファイルを 1 つの ID とみなす | ID はパスハッシュ入りで別物。取り違えない設計 | `index.json` の `content_hash` 一致で**内容重複**だけを別途検知する |

## トラブルシューティング

- **画像内のテキストが取れない**: `--no-ocr` を付けていないか確認。初回はモデル
  ダウンロードのためネットワークが必要 (プロキシ環境では失敗しうる)
- **表が `table` 要素にならない**: 罫線のない PDF 表は検出不可のことがある。
  画像内の表はレイアウト検出スコアが低いと棄却される
- **Excel の数式が None になる**: 保存時に計算結果キャッシュがないファイルは
  `data_only=True` で値が取れない。Excel で開いて保存し直すと解消する
- **文字化けして見える**: result.json は UTF-8。ビューア側のエンコーディングを確認
- **`.xls`/`.doc`/`.ppt` で「Microsoft Office が必要」と出る**: 旧形式は COM で
  Office を使って変換する。Windows であること・対応 Office アプリが導入済みで
  あること・`pip install pywin32` 済みであることを確認する。用意できなければ
  新形式へ変換してから渡す
