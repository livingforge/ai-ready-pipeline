# -*- coding: utf-8 -*-
"""init.py（scaffold seed の実体化）のテスト — 空 seed が error 0 で始まることを固定する。

リポジトリ (specdb/tests/) とスキルバンドル (scripts/tests/) のどちらでも
「親ディレクトリに init.py と scaffold（seed）がある」前提で動く。
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import init  # noqa: E402
from engine import Store  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parents[1]


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="specdb-init-"))


def test_find_seed_has_metamodel():
    seed = init.find_seed()
    assert seed is not None and (seed / "metamodel.yaml").is_file()


def test_empty_seed_layout_and_validates():
    target = _tmp() / ".specdb"
    init.instantiate(init.find_seed(), target, with_samples=False, pack=None)
    assert (target / "metamodel.yaml").is_file()
    assert (target / "sync.yaml").is_file()
    assert (target / "items" / ".gitkeep").is_file()
    assert (target / "relations" / ".gitkeep").is_file()
    # 空 seed には正本アイテムの YAML は無い
    assert not list((target / "items").rglob("*.yaml"))
    store = Store.load(target)
    assert [p for p in store.problems if p.level == "error"] == []
    # パック由来の標準種別がメタモデルに載っている
    assert "requirement" in store.mm.item_types
    assert "method" in store.mm.item_types


def test_with_samples_includes_item_yaml():
    target = _tmp() / ".specdb"
    init.instantiate(init.find_seed(), target, with_samples=True, pack=None)
    assert list((target / "items").rglob("*.yaml"))
    store = Store.load(target)
    assert [p for p in store.problems if p.level == "error"] == []


def test_pack_override_rewrites_extends():
    text = "version: 1\nextends: jp-sier-std@1.1\nitem_types: {}\n"
    out = init._rewrite_extends(text, "other-std@2.0")
    assert "extends: other-std@2.0" in out
    assert "jp-sier-std@1.1" not in out


def test_pack_override_inserts_when_absent():
    out = init._rewrite_extends("version: 1\nitem_types: {}\n", "corp@1.0")
    assert "extends: corp@1.0" in out
    assert out.index("version:") < out.index("extends:")


def test_force_cleans_samples_but_keeps_user_out():
    target = _tmp() / ".specdb"
    init.instantiate(init.find_seed(), target, with_samples=True, pack=None)
    (target / "out").mkdir()
    (target / "out" / "keep.txt").write_text("x", encoding="utf-8")
    init.instantiate(init.find_seed(), target, with_samples=False, pack=None,
                     force=True)
    assert not list((target / "items").rglob("*.yaml"))   # サンプルは消えた
    assert (target / "out" / "keep.txt").is_file()          # 成果物は残る


def _run_cli(*args, cwd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(TOOLS_DIR), "init", *args],
        capture_output=True, encoding="utf-8", errors="replace", cwd=cwd, env=env,
    )


def test_cli_default_target_is_dot_specdb_and_error_zero():
    proj = _tmp()
    r = _run_cli(cwd=proj)
    assert r.returncode == 0, r.stderr
    assert (proj / ".specdb" / "metamodel.yaml").is_file()
    assert (proj / ".specdb" / "pack.lock").is_file()


def test_cli_refuses_nonempty_without_force():
    proj = _tmp()
    assert _run_cli(cwd=proj).returncode == 0
    r = _run_cli(cwd=proj)
    assert r.returncode == 1
    assert "--force" in r.stderr
    assert _run_cli("--force", cwd=proj).returncode == 0
