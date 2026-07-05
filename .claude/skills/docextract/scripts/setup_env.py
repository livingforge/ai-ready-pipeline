# -*- coding: utf-8 -*-
"""スキル実行環境の構築 — 共有 venv・依存・venv コマンドを一括で用意する

    python <docextract スキルディレクトリ> setup            # 構築（承認ゲートあり）
    python <docextract スキルディレクトリ> setup --check    # 状態確認のみ（無変更・承認不要）

構築する内容:

  1. 共有 venv（<プロジェクトルート>/.venv。uv で作成）
  2. docextract の依存（requirements.lock があればハッシュ固定で優先）
  3. specdb の依存（隣接スキル specdb の requirements.txt。PyYAML + Jinja2）
  4. venv コマンド specdb / docextract / docsummary（探索係 launcher/ の install）

@skill-setup エージェントがこのコマンドを駆動する（スキル利用前に必ず実行
される前提）。冪等: 構築済みの項目は marker により素通りする。外部取得・
インストールは _bootstrap の承認ゲート（DOCEXTRACT_AUTOINSTALL / 対話確認 /
fail-closed）を必ず通る。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_scripts = Path(__file__).resolve().parent
sys.path.insert(0, str(_scripts))

import _bootstrap  # noqa: E402


def _paths() -> tuple[Path, Path, Path]:
    root = _bootstrap._project_root(Path(__file__).resolve())
    venv = root / ".venv"
    return root, venv, _bootstrap._venv_python(venv)


def _command(venv: Path, name: str) -> Path:
    sub = "Scripts" if os.name == "nt" else "bin"
    return venv / sub / (f"{name}.exe" if os.name == "nt" else name)


def _specdb_requirements() -> Path | None:
    """隣接する specdb スキルの依存記述（展開先 / 開発リポジトリの両レイアウト）。"""
    skills = _scripts.parent.parent
    for cand in (skills / "specdb" / "scripts" / "requirements.txt",
                 skills / "specdb" / "requirements.txt"):
        if cand.is_file():
            return cand
    return None


def check() -> int:
    """状態確認のみ（何も変更しない）。すべて OK なら exit 0。"""
    _, venv, venv_python = _paths()
    ok = True

    def status(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        if not good:
            ok = False
        line = f"[{'OK' if good else 'NG'}] {label}"
        print(line + (f" — {detail}" if detail else ""))

    status(f"共有 venv ({venv})", venv_python.exists(),
           "" if venv_python.exists() else "未作成")
    for name in ("specdb", "docextract", "docsummary"):
        cmd = _command(venv, name)
        status(f"venv コマンド {name}", cmd.is_file(),
               "" if cmd.is_file() else "未インストール")
    if venv_python.exists():
        probe = subprocess.run(
            [str(venv_python), "-c", "import yaml, jinja2"], capture_output=True
        )
        status("specdb 依存 (PyYAML + Jinja2)", probe.returncode == 0)
        pytest_probe = subprocess.run(
            [str(venv_python), "-c", "import pytest"], capture_output=True
        )
        status("テスト依存 (pytest)", pytest_probe.returncode == 0,
               "" if pytest_probe.returncode == 0 else "未インストール")
        # docextract 依存は marker（bootstrap の記録）で判定する。手動構築の venv
        # では未記録がありうるため、失敗にはせず情報として出す。
        if (venv / ".docextract.reqhash").exists():
            print("[OK] docextract 依存 (marker 記録あり)")
        else:
            print("[--] docextract 依存 (marker 未記録。setup 実行で確認・導入する)")
    # LLM 接続設定 (docsummary 用) は必須ではないため OK/NG に含めず情報として出す。
    # 設定の中身 (API キー) はここでは一切読まない — 確認は docsummary config --check。
    root, _, _ = _paths()
    env_file = root / ".env"
    print(f"[--] LLM 接続設定 (.env): {'あり' if env_file.is_file() else '未作成'}"
          " — 要約 (docsummary) を使う場合のみ必要。"
          "状態確認: docsummary config --check / 雛形作成: docsummary config --init")
    print("状態:", "構築済み" if ok else "未構築の項目あり（setup で構築する）")
    return 0 if ok else 1


def setup() -> int:
    """構築を実行する。高リスク操作は _bootstrap の承認ゲートを通る。"""
    root, venv, venv_python = _paths()
    uv = _bootstrap._find_uv() or _bootstrap._install_uv()
    boot_log = _bootstrap._bootstrap_log_path(root)

    _bootstrap._ensure_venv(uv, venv, venv_python, boot_log)
    _bootstrap._ensure_requirements(
        uv, venv, venv_python, _scripts / "requirements.txt", "docextract",
        boot_log,
        note="初回は数百 MB のダウンロードが発生します"
             " (OCR/表検出モデルは実行時に別途取得)。",
    )
    specdb_req = _specdb_requirements()
    if specdb_req is not None:
        _bootstrap._ensure_requirements(
            uv, venv, venv_python, specdb_req, "specdb", boot_log,
            note="PyYAML + Jinja2 のみの軽量なインストールです。",
        )
    _bootstrap._ensure_launcher(uv, venv, venv_python, _scripts / "launcher",
                                boot_log)
    print()
    return check()


def main(argv: list[str]) -> int:
    if "--check" in argv:
        return check()
    return setup()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
