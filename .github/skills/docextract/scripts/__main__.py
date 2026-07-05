# -*- coding: utf-8 -*-
"""docextract スキルの統一エントリポイント — ディレクトリごと実行する

    python <スキルディレクトリ> extract  <入力...> [オプション]     # 資料の抽出
    python <スキルディレクトリ> docagent <サブコマンド> [オプション]  # 集約 JSON の操作

サブコマンドは同じディレクトリのランチャー (run_*.py) にそのまま委譲するので、
従来の `python <dir>/run_docextract.py ...` 形式も引き続き使える。
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

COMMANDS: dict[str, tuple[str, str]] = {
    "extract": ("run_docextract.py", "Office/PDF を構造化 JSON へ抽出"),
    "docagent": ("run_docagent.py", "集約 JSON のデータ操作 (init/sync/search/facts …)"),
    "summarize": ("run_docsummary.py", "登録済み文書を LLM で要約 (要 API キー設定)"),
    "setup": ("setup_env.py", "スキル実行環境の構築 (venv・依存・venv コマンド)"),
}
ALIASES = {"agent": "docagent", "docsummary": "summarize"}


def _usage(stream) -> None:
    print("使い方: python <スキルディレクトリ> <サブコマンド> [オプション...]",
          file=stream)
    print("サブコマンド:", file=stream)
    for name, (_, desc) in COMMANDS.items():
        print(f"  {name:<9} {desc}", file=stream)


def main(argv: list[str]) -> int:
    if not argv:
        _usage(sys.stderr)
        return 2
    if argv[0] in ("-h", "--help"):
        _usage(sys.stdout)
        return 0
    cmd = ALIASES.get(argv[0], argv[0])
    if cmd not in COMMANDS:
        print(f"不明なサブコマンド: {argv[0]}", file=sys.stderr)
        _usage(sys.stderr)
        return 2
    script = HERE / COMMANDS[cmd][0]
    sys.path.insert(0, str(HERE))
    sys.argv = [str(script)] + argv[1:]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
