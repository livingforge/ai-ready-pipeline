# -*- coding: utf-8 -*-
"""mutate.py（変更操作 CLI）のテスト — 採番・規約強制・部分置換・巻き戻しを固定する。

リポジトリ (specdb/tests/) とスキルバンドル (scripts/tests/) のどちらでも
「親ディレクトリに mutate.py がある」前提で動く。
"""
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Store          # noqa: E402
from mutate import Editor, MutateError, apply_plan  # noqa: E402

METAMODEL = """
version: 1
item_types:
  function:
    label: 機能
    label_field: name
    id_prefix: fn-
    sequence: { attribute: func_id, format: "F-{:02d}" }
    attributes:
      func_id:     { kind: string, required: true, unique: true }
      name:        { kind: string, required: true }
      description: { kind: string }
  skill:
    label: スキル
    label_field: name
    id_prefix: sk-
    attributes:
      name: { kind: string, required: true }
  data-item:
    label: データ項目
    id_prefix: di-
    attributes:
      name: { kind: string, required: true }
relation_types:
  realizes:
    from: skill
    to: function
"""

FUNCTIONS = """# 機能の正本（コメントは保存される）

- id: fn-base
  func_id: F-01
  name: 基本機能
  description: >-
    既存の説明文。
  status: approved
  source:
    doc: README.md
"""

SKILLS = "- { id: sk-a, name: skill-a, status: approved, source: { doc: README.md } }\n"
DATA = "- { id: di-0001, name: 顧客コード, status: approved, source: { doc: README.md } }\n"
RELS = """# 実現する
- { type: realizes, from: sk-a, to: fn-base, status: approved }
"""

SOURCE = {"doc": "README.md", "location": {"section": "冒頭"}, "evidence": "根拠"}


def build_root(tree: dict | None = None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="specdb-mutate-"))
    defaults = {
        "metamodel.yaml": METAMODEL,
        "items/function/core.yaml": FUNCTIONS,
        "items/skill/core.yaml": SKILLS,
        "items/data-item/core.yaml": DATA,
        "relations/realizes.yaml": RELS,
    }
    for rel, text in {**defaults, **(tree or {})}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def test_add_item_assigns_id_and_sequence():
    root = build_root()
    ed = Editor(root)
    iid = ed.add_item("function", {"name": "新機能"}, SOURCE, slug="new")
    assert iid == "fn-new"
    store = Store.load(root)
    assert not store.has_errors(), [str(p) for p in store.problems]
    item = store.items["fn-new"]
    assert item.attrs["func_id"] == "F-02"      # F-01 の次番が自動で入る
    assert item.status == "review"              # 登録は必ず review
    assert item.source[0]["doc"] == "README.md"


def test_add_item_numeric_id_when_no_slug():
    root = build_root()
    ed = Editor(root)
    assert ed.add_item("data-item", {"name": "受注番号"}, SOURCE) == "di-0002"


def test_add_item_requires_source_and_prefix():
    ed = Editor(build_root())
    with pytest.raises(MutateError, match="source"):
        ed.add_item("function", {"name": "x"}, None, slug="x")
    with pytest.raises(MutateError, match="接頭辞"):
        ed.add_item("function", {"name": "x"}, SOURCE, explicit_id="bad-id")


def test_add_item_preserves_existing_text():
    root = build_root()
    Editor(root).add_item("function", {"name": "新機能"}, SOURCE, slug="new")
    text = (root / "items/function/core.yaml").read_text(encoding="utf-8")
    assert text.startswith("# 機能の正本")          # 先頭コメントが残る
    assert "- id: fn-base" in text and "- id: fn-new" in text


def test_add_relation_appends_to_type_file():
    root = build_root()
    ed = Editor(root)
    ed.add_item("function", {"name": "新機能"}, SOURCE, slug="new")
    ed.add_relation("realizes", "sk-a", "fn-new")
    recs = yaml.safe_load((root / "relations/realizes.yaml").read_text(encoding="utf-8"))
    added = [r for r in recs if r["to"] == "fn-new"]
    assert added and added[0]["status"] == "review"


def test_add_relation_validates_endpoints_and_duplicates():
    ed = Editor(build_root())
    with pytest.raises(MutateError, match="存在しない"):
        ed.add_relation("realizes", "sk-a", "fn-nothing")
    with pytest.raises(MutateError, match="使えない"):
        ed.add_relation("realizes", "di-0001", "fn-base")
    with pytest.raises(MutateError, match="既に存在"):
        ed.add_relation("realizes", "sk-a", "fn-base")


def test_approved_only_via_approve():
    root = build_root()
    ed = Editor(root)
    with pytest.raises(MutateError, match="approve"):
        ed.set_status("fn-base", "approved")
    ed.set_status("fn-base", "approved", _via="approve")
    assert Store.load(root).items["fn-base"].status == "approved"


def test_set_status_on_flow_relation():
    root = build_root()
    Editor(root).set_status("realizes:sk-a->fn-base", "review")
    store = Store.load(root)
    rel = store.relations_of("realizes")[0]
    assert rel.status == "review"
    text = (root / "relations/realizes.yaml").read_text(encoding="utf-8")
    assert text.startswith("# 実現する")            # コメント・整形は保存される


def test_set_attr_replaces_block_scalar_and_reviews():
    root = build_root()
    Editor(root).set_attr("fn-base", "description", "書き換えた説明")
    store = Store.load(root)
    assert not store.has_errors(), [str(p) for p in store.problems]
    item = store.items["fn-base"]
    assert item.attrs["description"] == "書き換えた説明"
    assert item.status == "review"                 # 変更したら review に戻る
    assert item.attrs["name"] == "基本機能"         # 他属性は無傷


def test_set_source_replaces_source_block():
    root = build_root()
    Editor(root).set_source("fn-base", {"doc": "docs/new.md", "evidence": "新しい原文"})
    store = Store.load(root)
    assert not store.has_errors(), [str(p) for p in store.problems]
    item = store.items["fn-base"]
    assert item.source == [{"doc": "docs/new.md", "evidence": "新しい原文"}]
    assert item.status == "review"
    assert item.attrs["description"] == "既存の説明文。"   # 他属性は無傷


def test_set_source_accepts_relation_ref():
    # 関係の evidence も文書改稿で古くなるため、set-status/approve と同じ
    # 関係参照 (rtype:from->to) で出典を差し替えられる。
    root = build_root()
    Editor(root).set_source(
        "realizes:sk-a->fn-base", {"doc": "docs/new.md", "evidence": "関係の新しい原文"}
    )
    store = Store.load(root)
    assert not store.has_errors(), [str(p) for p in store.problems]
    rel = store.relations_of("realizes")[0]
    assert rel.source == [{"doc": "docs/new.md", "evidence": "関係の新しい原文"}]
    assert rel.status == "review"


def test_apply_plan_and_rollback_on_new_error():
    root = build_root()
    before = (root / "items/function/core.yaml").read_text(encoding="utf-8")
    ed = Editor(root)
    # 必須属性 name が無いアイテムを混ぜた plan → 新たな error → 巻き戻し対象
    apply_plan(ed, {"ops": [
        {"op": "add-item", "type": "function", "slug": "ok",
         "attrs": {"name": "良い方"}, "source": SOURCE},
        {"op": "add-item", "type": "function", "slug": "bad",
         "attrs": {}, "source": SOURCE},
    ]})
    new_errors, _ = ed.validate()
    assert new_errors
    ed.rollback()
    assert (root / "items/function/core.yaml").read_text(encoding="utf-8") == before


def test_apply_plan_full_cycle():
    root = build_root()
    ed = Editor(root)
    apply_plan(ed, {"ops": [
        {"op": "add-item", "type": "function", "slug": "cycle",
         "attrs": {"name": "一括追加"}, "source": SOURCE},
        {"op": "add-relation", "type": "realizes", "from": "sk-a", "to": "fn-cycle"},
        {"op": "set-attr", "ref": "fn-base", "attr": "description", "value": "更新"},
        {"op": "deprecate", "ref": "di-0001"},
        {"op": "approve", "ref": "realizes:sk-a->fn-base"},
    ]})
    new_errors, _ = ed.validate()
    assert not new_errors, [str(p) for p in new_errors]
    store = Store.load(root)
    assert store.items["fn-cycle"].status == "review"
    assert store.items["di-0001"].status == "deprecated"
    assert store.relations_of("realizes", src="sk-a", dst="fn-base")[0].status == "approved"
