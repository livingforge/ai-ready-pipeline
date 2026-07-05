"""docsummary — 登録済み文書を LLM で要約するスキルの本体パッケージ。

docextract が抽出し docagent が索引化した文書 (library.json) を入力に、
同梱の要約フォーマット (templates/summary_format.md) に従って LLM で要約し、
`.docextract/summaries/<doc_id>.md` と集約 JSON (store/summaries.json) に保存する。

- 対象選択:  文書 ID / 元ファイルのフォルダ (--dir) / 未要約・陳腐化 (--pending) / 全件 (--all)
- プロバイダ: openai / azure (Azure OpenAI) / gemini / anthropic (標準ライブラリのみで実装)
- 秘密情報:  API キー等は環境変数または `.env` で渡す。コード・ストアには保存しない
"""

__version__ = "1.0.0"
