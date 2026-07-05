"""docsummary (登録済み文書の LLM 要約) のエントリポイント。

docsummary スキルに同梱された docsummary パッケージを sys.path に載せて CLI を
起動する。docsummary は import 時に **docextract / docagent パッケージへ依存する**
（`docextract.paths` / `docagent.store`）ため、同じプロジェクトに展開された
**兄弟スキル docextract** の scripts から両パッケージを解決して sys.path に載せる
（コピー同梱はせず実行時参照する）。初回は共有仮想環境（プロジェクトルート直下の
.venv）を uv で用意し、その python で実行し直す（_bootstrap 参照）。共有 venv・
依存インストールのマーカーは docextract と共用する（skill="docextract"）ので、
docextract のセットアップ済み環境をそのまま再利用し二重インストールしない。

使い方: python run_docsummary.py <サブコマンド> [オプション]
"""

import sys
from pathlib import Path

_scripts = Path(__file__).resolve().parent
sys.path.insert(0, str(_scripts))  # docsummary パッケージ + _bootstrap

from _bootstrap import _project_root, ensure_env  # noqa: E402


def _resolve_siblings(script: Path) -> Path:
    """依存する docextract / docagent のある場所を解決し sys.path に載せて返す。

    開発リポジトリではトップレベル（<root>/docextract, <root>/docagent）、配布物では
    docextract スキルの scripts 配下に両パッケージが同梱される。両方を含む最初の
    ディレクトリを採用し、その場所（base）を返す。docsummary は自前の requirements を
    持たず、この base の requirements(.lock) を共用する（依存集合は docextract と一致）。
    見つからなければ docextract スキル未展開なので停止する。
    """
    root = _project_root(script)
    candidates = [
        root,  # 開発リポジトリ: docextract/ docagent/ がトップレベル
        root / ".claude" / "skills" / "docextract" / "scripts",
        root / ".github" / "skills" / "docextract" / "scripts",
    ]
    for base in candidates:
        if (base / "docextract" / "__init__.py").is_file() and \
                (base / "docagent" / "__init__.py").is_file():
            sys.path.insert(0, str(base))
            return base
    raise SystemExit(
        "docsummary: 依存する docextract / docagent パッケージが見つからない。"
        "docsummary は docextract スキルの実行体を参照するため、同じ"
        "プロジェクトに docextract スキルが展開されている必要がある。"
    )


_dx = _resolve_siblings(Path(__file__))
# requirements・依存インストールのマーカーは兄弟 docextract スキルと共用する
# （docsummary の依存集合は docextract のフル依存に一致するため自前に持たない）。
ensure_env(Path(__file__), _dx / "requirements.txt", skill="docextract")

from docsummary.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
