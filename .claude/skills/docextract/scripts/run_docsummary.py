"""docsummary (登録済み文書の LLM 要約) のエントリポイント。

スキル内に同梱された docsummary パッケージを sys.path に追加して CLI を起動する。
カレントディレクトリに依存せず、どこから実行しても動く。初回は共有仮想環境
(プロジェクトルート直下の .venv) を uv で用意し、その python で実行し直す。
使い方: python run_docsummary.py <サブコマンド> [オプション]
"""

import sys
from pathlib import Path

_scripts = Path(__file__).resolve().parent
sys.path.insert(0, str(_scripts))

from _bootstrap import ensure_env

ensure_env(Path(__file__), _scripts / "requirements.txt")

from docsummary.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
