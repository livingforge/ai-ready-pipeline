# -*- coding: utf-8 -*-
"""venv コマンド `specdb` / `docextract` / `docsummary` の実体 — cwd から上方探索して委譲する

共有 venv に console script として install され（_bootstrap.py が担当）、
venv が有効なら任意のディレクトリで

    specdb engine
    docextract extract --dir 資料/ -r

のように起動できる。コードの正本は install しない。実行時にカレント
ディレクトリから上方向へ、スキルが解決できる最初のディレクトリを探し、

    <root>/<スキル名>（ソース正本） → <root>/.claude/skills/<スキル名>
    → <root>/.github/skills/<スキル名>

の順で見つかった `__main__.py` へ委譲するだけの探索係なので、
zip 再展開でスキルを更新してもコマンドの再インストールは不要。

specdb のデータルート既定 (./.specdb) は cwd 依存のため、--root 未指定で
サブディレクトリから実行された場合は <root>/.specdb を自動補完する。
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def _force_utf8_io() -> None:
    """非 UTF-8 コンソール (Windows 既定の cp932 等) でも非 ASCII 出力で
    クラッシュしないよう、標準出力/標準エラーを UTF-8・エラー耐性つきに再設定する。

    venv コマンド specdb / docextract は日本語や em-dash (—) を出すため、素の
    cp932 コンソールでは UnicodeEncodeError になりうる。launcher は独立した
    install 対象 (本体パッケージに依存しない) なので、ここに自己完結で持つ。
    ``PYTHONIOENCODING=utf-8`` を毎回設定するのと同じ効果を利用者に意識させず効かせる。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass


def _is_skill_dir(candidate: Path) -> bool:
    """スキル CLI として実行できるディレクトリか。

    __init__.py を持つディレクトリは通常の Python パッケージ
    （docextract 等、`python -m` 用の相対 import の __main__.py を持つ）
    なので除外する。スキルの __main__.py は単体スクリプトとして動く。
    """
    return (candidate / "__main__.py").is_file() and \
        not (candidate / "__init__.py").is_file()


def _resolve_skill(base: Path, name: str) -> Path | None:
    """base をプロジェクトルートとみなしてスキルディレクトリを解決する。"""
    candidates = [
        base / name,  # ソース正本（開発リポジトリ）
        base / ".claude" / "skills" / name,
        base / ".github" / "skills" / name,
    ]
    for candidate in candidates:
        if _is_skill_dir(candidate):
            return candidate
    return None


def _resolve_upward(name: str, start: Path) -> tuple[Path, Path] | None:
    """start から上方向に、スキルが解決できる最初のディレクトリを探す。

    「.claude があるか」ではなく「そのスキルが実際に解決できるか」で判定する
    （ホームディレクトリの ~/.claude 等を誤ってルート扱いしないため）。
    """
    for base in [start, *start.parents]:
        target = _resolve_skill(base, name)
        if target is not None:
            return base, target
    return None


def _with_default_root(argv: list[str], root: Path) -> list[str]:
    """specdb ツールのデータルート既定 (./.specdb) を cwd 非依存にする補完。

    サブコマンドがあり、--root 未指定で、cwd に .specdb が無く、
    プロジェクトルートに .specdb がある場合だけ --root を付け足す。
    """
    if not argv or argv[0].startswith("-") or "--root" in argv:
        return argv
    default = root / ".specdb"
    if (Path.cwd() / ".specdb").is_dir() or not default.is_dir():
        return argv
    return [*argv, "--root", str(default)]


def main(name: str, argv: list[str]) -> int:
    _force_utf8_io()
    resolved = _resolve_upward(name, Path.cwd())
    if resolved is None:
        print(
            f"{name}: スキルが見つからない。プロジェクト（{name} が展開された"
            "ディレクトリ、または .claude/skills / .github/skills を持つ"
            "ディレクトリ）の配下で実行する。",
            file=sys.stderr,
        )
        return 2
    root, target = resolved
    if name == "specdb":
        argv = _with_default_root(argv, root)
    old_argv = sys.argv[:]
    sys.argv = [str(target), *argv]
    sys.path.insert(0, str(target))
    try:
        runpy.run_path(str(target), run_name="__main__")
    finally:
        sys.argv = old_argv
        try:
            sys.path.remove(str(target))
        except ValueError:
            pass
    return 0


def main_specdb() -> int:
    return main("specdb", sys.argv[1:])


def main_docextract() -> int:
    return main("docextract", sys.argv[1:])


def main_docsummary() -> int:
    """venv コマンド `docsummary`。実体は docextract スキルの summarize
    サブコマンド (docsummary パッケージは docextract スキルに同梱される)。"""
    return main("docextract", ["summarize", *sys.argv[1:]])
