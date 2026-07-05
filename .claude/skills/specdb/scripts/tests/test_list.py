# -*- coding: utf-8 -*-
"""list.py（status 等で絞った列挙）のテスト — 承認前の対象抽出を固定する。

リポジトリ (specdb/tests/) とスキルバンドル (scripts/tests/) のどちらでも
「親ディレクトリに list.py がある」前提で動く。
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Store  # noqa: E402
from list import collect, render  # noqa: E402

TOOLS_DIR = Path(__file__).resolve().parents[1]

METAMODEL = """
version: 1
item_types:
  function:
    label: 機能
    label_field: name
    id_prefix: fn-
    attributes:
      name: { kind: string, required: true }
  skill:
    label: スキル
    label_field: name
    id_prefix: sk-
    attributes:
      name: { kind: string, required: true }
relation_types:
  realizes:
    from: skill
    to: function
"""

FUNCTIONS = """
- { id: fn-a, name: 承認済み機能, status: approved, source: { doc: README.md } }
- { id: fn-b, name: レビュー中機能, status: review, source: { doc: README.md } }
"""
SKILLS = "- { id: sk-a, name: skill-a, status: approved, source: { doc: README.md } }\n"
RELS = """
- { type: realizes, from: sk-a, to: fn-a, status: approved }
- { type: realizes, from: sk-a, to: fn-b, status: review }
"""


def build_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="specdb-list-"))
    tree = {
        "metamodel.yaml": METAMODEL,
        "items/function/core.yaml": FUNCTIONS,
        "items/skill/core.yaml": SKILLS,
        "relations/realizes.yaml": RELS,
    }
    for rel, text in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def test_collect_filters_by_status_across_items_and_relations():
    store = Store.load(build_root())
    rows = collect(store, status="review")
    refs = {r["ref"] for r in rows}
    assert refs == {"fn-b", "realizes:sk-a->fn-b"}
    assert {r["kind"] for r in rows} == {"item", "relation"}


def test_collect_kind_and_type_narrow_further():
    store = Store.load(build_root())
    items = collect(store, kind="item", status="review")
    assert [r["ref"] for r in items] == ["fn-b"]
    rels = collect(store, kind="relation", type_="realizes")
    assert len(rels) == 2  # status 未指定なら approved も含む


def test_collect_ref_is_mutate_reference_form():
    """--json の ref を plan.json の approve にそのまま渡せる形であること。"""
    store = Store.load(build_root())
    rel = [r for r in collect(store, kind="relation", status="review")][0]
    assert rel["ref"] == "realizes:sk-a->fn-b"


def test_render_reports_none_when_empty():
    store = Store.load(build_root())
    assert render(collect(store, status="deprecated")) == "該当なし\n"


def _run_cli(*args, cwd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(TOOLS_DIR), "list", *args],
        capture_output=True, encoding="utf-8", errors="replace", cwd=cwd, env=env,
    )


def test_cli_json_lists_review_targets():
    root = build_root()  # --root は先頭でのみ解釈される（parse_root の規約）
    r = _run_cli("--root", str(root), "--status", "review", "--json", cwd=root)
    assert r.returncode == 0, r.stderr
    refs = {row["ref"] for row in json.loads(r.stdout)}
    assert refs == {"fn-b", "realizes:sk-a->fn-b"}


def test_cli_rejects_unknown_status():
    root = build_root()
    r = _run_cli("--root", str(root), "--status", "bogus", cwd=root)
    assert r.returncode == 2
    assert "不明な status" in r.stderr
