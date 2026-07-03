# Changelog — docextract

## Unreleased

Monosashi 評価 (`agents-20260702T164626Z`) のフィードバック反映。信頼性・観測性・
再現性・ガバナンス・ハーネスの底上げ（後方互換、公開 API・出力スキーマは非破壊）。

### Added
- 標準出力の**数値ガード**と設定ファイル `<home>/config.json`（`docextract/config.py`）。
  docagent の参照系（`text`/`search`/`list`/`query`/`facts`/`prep`/`get`）は、`--json`
  出力が `ceiling_chars`（既定 30000＝Claude Code の Bash 出力上限に一致）を超えると
  **拒否して絞り方を stderr に案内**し、ホスト（Claude Code／GitHub Copilot）による
  無言の切り詰め・情報欠落を手前で止める。`--stdout` で承知の上の全出力を強制できる。
  各コマンドの既定（`text_max_chars` ほか）も同ファイルで一元管理し、`doctypes.json`
  同様に利用者が編集可能。優先順位は CLI フラグ > config.json > 組み込み既定。`init` が
  既定値の config.json を生成（既存は保持）。`--config <パス>` で別ファイル指定可。
- 秘密度ラベル (Microsoft Purview / AIP) と暗号化・IRM(RMS) 保護への対応
  （`docextract/sensitivity.py`）。**標準ライブラリのみ**で実装し依存は増やさない。
  **操作者が対象文書へのアクセス権を持つ前提**で設計。
  - **保護検知と経路分け**: 抽出前に暗号化/IRM 構造 (MS-OFFCRYPTO の DataSpaces/
    EncryptedPackage/DRM) を検知。通常抽出器へ素通しした際の不明瞭な失敗や、保護起因の
    失敗を「Office が無い」「未対応」と取り違えることを防ぐ。
  - **IRM/RMS 復号**: IRM/RMS 暗号化文書は、操作者の権限で動く Office に COM で開かせて
    復号し、平文 OOXML へ変換してから抽出する（旧形式変換と共通の COM 経路）。Office /
    pywin32 が無い環境では「Microsoft Office が必要」である旨で fail-closed。
  - **パスワード暗号化**: アクセス権とは別に鍵が要り、COM で開くとハングしうるため、
    これだけは `ProtectedDocumentError` で fail-closed（復号済みコピーを渡す旨を提示）。
  - **ラベル伝播**: ラベルが残っている文書の `MSIP_Label_*` を読み、`result.json` の
    `metadata.sensitivity` と `index.json` へ伝播（無印のまま下流コーパスへ流入させない）。
    旧形式・IRM 復号後は変換後 OOXML からラベルを読み継ぐ（外れていれば付けない＝許容）。
  - 注意: 抽出物・一時 OOXML は無保護平文であり元ラベルの暗号化・アクセス制御を継承
    しない（格下げ）。操作者権限での復号・平文化を許容する運用前提として `threat-model.md`
    に明記。出力そのものの保護は運用側の責務。
- 旧 Office バイナリ形式 `.xls` / `.doc` / `.ppt` の抽出（`docextract/extractors/legacy_com.py`）。
  Windows 上のインストール済み Microsoft Office を **COM 自動化**して一時的に OOXML へ
  変換し既存抽出器へ委譲する（OCR・画像内表検出など既存パイプラインを再利用）。
  Office / pywin32 が無い環境では未対応形式として黙って弾かず、**「Microsoft Office が
  必要」である旨と回避策を含む `OfficeUnavailableError`** で fail-closed（CLI は該当
  ファイルのみ `[NG]`・非ゼロ終了で分離継続）。Office / pywin32 は再現性固定の
  `requirements.lock` に含めない外部前提として `dependencies.md` に明記。
- 構造化イベントログ `docextract/obs.py`（JSON Lines）。1 実行を相関 ID (`run_id`)
  で貫き、`docextract → docagent` に環境変数 `DOCEXTRACT_RUN_ID` / `--run-id` で伝播。
  監査ログだけから 1 run を再構成できる。
- 評価ハーネス `scripts/eval/`（`run_eval.py` + `cases.jsonl`）。合否基準を data として
  宣言し列挙実行する、視点分離の評価ランナー。
- カバレッジ設計 `docs/coverage.md`（視点別カバレッジ + 未評価サーフェスの明示列挙）。
- 脅威モデル `package-meta/docextract/threat-model.md`（脅威 → 防御層 → 検証テストの対応表）。
- ハッシュ固定ロックファイル `requirements.lock`。`_bootstrap` が優先して決定論的に
  インストールする。OCR モデルの明示ピン用 env（`DOCEXTRACT_OCR_VERSION` /
  `DOCEXTRACT_OCR_DET_MODEL` / `DOCEXTRACT_OCR_REC_MODEL`）。
- GOVERNANCE に解決可能なオーナー連絡先と定期棚卸しスケジュールを明文化。

### Changed
- PDF 画像抽出の `bare except: return`（silent degradation）を廃止。劣化を握り潰さず
  `result.json` の `degraded` に構造化記録し、監査ログに相関 ID 付きで残す（observable）。

## 0.1.0 (2026-07-02)

初回リリース。

- Office 文書 (docx / xlsx / xlsm / pptx) と PDF からテキスト・表・画像を抽出し
  JSON 形式で出力する CLI / Python API
- 画像内テキストの OCR (`ocr_text`)。バックエンドは RapidOCR (Apache-2.0、既定) と
  Windows 標準 OCR (winocr 経由) の 2 系統、`auto` でフォールバック
- 画像として貼られた表の検出と構造復元 (rapid_layout + rapid_table / SLANet-plus)。
  行・列を復元し通常の `table` 要素として出力
- Word のテキストボックス内テキストの抽出 (`style: "textbox"`)
- PDF 解析は pdfplumber (MIT) + pypdf (BSD-3-Clause)。全依存を商用利用可能な
  OSS (MIT / BSD / Apache-2.0) で構成
- 単体テスト (18 件) をバンドルに同梱 (`scripts/tests/`)。フィクスチャは
  実行時生成でネットワーク・OCR モデル不要、配布先で自己検証できる
