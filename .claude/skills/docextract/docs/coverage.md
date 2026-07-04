# カバレッジ設計 — docextract

テスト・評価が「何を・どの視点で」検証しているかを **視点分離**で設計し、
併せて **未評価サーフェス（何をテストしていないか）** を明示する文書。
実装の自己検証（`scripts/tests/`）と評価ハーネス（`scripts/eval/`）の役割分担も
ここで定義する。なお **評価ハーネス（`scripts/eval/`）はリポジトリ限定の開発用資産で、
配布バンドル（`.claude/` `.github/` および zip）には同梱しない**。バンドル同梱の
`tests/test_eval.py` は eval 資産が無ければ自動 skip する。

## 二層構成

| 層 | 場所 | 役割 | 視点 |
|---|---|---|---|
| ユニット自己検証 | `scripts/tests/` (pytest) | 自コードを exercise し境界挙動を固定 | 正常系・境界・失敗系・劣化系 |
| 評価ハーネス（配布非同梱） | `scripts/eval/` (`run_eval.py` + `cases.jsonl`) | 合否基準を data として宣言し列挙実行 | end-to-end の出力契約 |

## 視点別カバレッジ

### 正常系（happy path）
- 各形式の end-to-end 抽出: `test_docx.py` / `test_xlsx.py` / `test_pptx.py` / `test_pdf.py`
- 公開 API `extract()` の返却契約: `test_extract_api.py`
- 秘密度ラベル (MSIP) の解析と result.json / index.json への伝播: `test_sensitivity.py`
- 出力データモデルの直列化: `test_models.py`
- eval: `cases.jsonl` の docx/xlsx/pptx ケース（要素種別ごとの最小件数・本文一致）

### 失敗系（error path / fail-closed）
- 未対応形式・存在しないファイル → 明確な例外 / 非ゼロ終了: `test_cli.py`, `test_extract_api.py`
- docagent が壊れた `result.json` / `elements` 欠落を拒否: `test_docagent.py`
- 抽出器レジストリの重複登録拒否（`register_extractor` の `ValueError`）: `test_registry.py`
- 旧形式 (`.xls`/`.doc`/`.ppt`) を Office/pywin32 不在で渡す → 「Office が必要」を含む
  `OfficeUnavailableError` で fail-closed（変換例外の包み直し・委譲成功系も）: `test_legacy_com.py`
- IRM/RMS 保護文書 → 操作者権限で Office COM 復号して抽出（Office 不在は Office 必須で停止）。
  パスワード暗号化のみ `ProtectedDocumentError` で fail-closed（通常ファイルは非誤検知・
  チャンク境界も検知）: `test_sensitivity.py`

### 境界（boundary）
- 空段落・空表・不揃い行・0 寸法画像・空 style/location: `test_models.py`
- 画像連番のゼロ埋め上限（999→1000）・拡張子正規化: `test_base.py`
- ワイルドカード非マッチ・重複パス除去・再帰/非再帰の走査: `test_cli.py`

### 劣化系（degradation, silent → observable）
- PDF 画像デコード失敗を握り潰さず痕跡化: `test_pdf.py::test_image_extraction_records_degradation_not_silent`
- 劣化痕跡が `result.json` の `degraded` に載り直列化で残る: `test_obs.py`, `test_models.py`
- 相関 ID の解決順序（明示 > 環境変数 > 採番）と JSON Lines 監査ログ: `test_obs.py`

### セキュリティ（threat-driven）
- 対応は脅威モデル [../../package-meta/docextract/threat-model.md](../../package-meta/docextract/threat-model.md) に、
  脅威 → 防御層 → 検証テストの対応表として集約する。

## 未評価サーフェス（意図的に対象外 / 今後の宿題）

「何をテストしていないか」を残すのが本節の主眼。silent に未カバーな面を可視化する。

| サーフェス | 状態 | 理由 / 代替 |
|---|---|---|
| OCR 実モデル出力（RapidOCR / Windows OCR の認識精度） | 未評価 | 非決定・大容量モデル DL 依存。eval は `ocr=False` で無効化。実モデルはゴールデン未整備（宿題） |
| 画像内の表復元（rapid_layout + rapid_table） | 未評価 | 同上（モデル依存・非決定）。`image_tables=False` で無効化 |
| PDF フィクスチャの eval | 部分 | 生成に PyMuPDF(AGPL) が必要で runtime 非依存にできず、eval は docx/xlsx/pptx のみ。PDF はユニット (`test_pdf.py`) でカバー |
| docextract → docagent の E2E 連携（実 CLI 間の run_id 伝播） | 部分 | 各層のユニットで担保。プロセス間 E2E は未整備（宿題） |
| CLI 終了コードの網羅（全サブコマンド × 異常系） | 部分 | 主要経路のみ。docagent サブコマンド個別の異常系は一部未網羅 |
| 巨大ファイル / メモリ上限 / タイムアウト | 未評価 | 性能・資源上限は範囲外。DoS 耐性は保証しない |
| 文字コード・破損 Office ファイルの網羅 | 部分 | 代表ケースのみ。ファジングは未導入（宿題） |
| 旧形式 (`.xls`/`.doc`/`.ppt`) の**実 COM 変換**（Office ありでの成功系） | 未評価 | Microsoft Office + pywin32 という外部前提が CI に無く非決定。ユニットは fail-closed 経路と変換ダミー委譲で担保し、実 Office 変換はゴールデン未整備（宿題） |
| 秘密度ラベルの**実ファイル**（Purview で実ラベル付与した Office 文書、実 IRM/RMS 暗号化） | 部分 | 検知・解析は合成フィクスチャ（OLE マーカー / 手組み custom.xml）で担保。実 Purview ラベル・実 RMS 暗号化ファイルでの往復は環境依存で未整備（宿題） |
| 並行実行時のマニフェスト/ストア競合 | 未評価 | 単一プロセス前提。ロック機構なし（宿題） |

## 更新方針

- 新しい抽出器・CLI フラグ・出力フィールドを足したら、対応する視点の行を本表に追加する。
- 「未評価」を 1 つ潰したら、該当行を削除し視点別カバレッジへ移す（表が現状と乖離しないよう保つ）。
