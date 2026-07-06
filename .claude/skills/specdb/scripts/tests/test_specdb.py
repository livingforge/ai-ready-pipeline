# -*- coding: utf-8 -*-
"""specdb エンジンのテスト — 検証ルールと正規化の仕様を固定する。

リポジトリ (specdb/tests/) とスキルバンドル (scripts/tests/) のどちらでも
「親ディレクトリに engine.py がある」前提で動く。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import Store  # noqa: E402

METAMODEL = """
version: 1
item_types:
  entity:
    label: エンティティ
    label_field: name
    attributes:
      name:          { kind: string, required: true }
      physical_name: { kind: string, required: true, unique: true }
  data-item:
    label: データ項目
    label_field: name
    attributes:
      name: { kind: string, required: true }
relation_types:
  has-column:
    from: entity
    to: data-item
    cardinality: { from: "1..*" }
    embedded: { field: columns, target_key: item }
    attributes:
      physical_name: { kind: string, required: true, unique: true }
"""

ITEMS_OK = """
- id: e1
  name: 顧客
  physical_name: M_CUST
  columns:
    - { item: d1, physical_name: CODE }
"""
DATA_OK = "- { id: d1, name: コード }\n"


def build(tree: dict) -> Store:
    """一時ディレクトリに仕様データツリーを組み立てて読み込む。"""
    root = Path(tempfile.mkdtemp(prefix="specdb-test-"))
    (root / "metamodel.yaml").write_text(
        tree.get("metamodel", METAMODEL), encoding="utf-8")
    for rel, text in tree.items():
        if rel == "metamodel":
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return Store.load(root)


def assert_problem(store: Store, *fragments: str) -> None:
    msgs = [str(p) for p in store.problems]
    for frag in fragments:
        assert any(frag in m for m in msgs), f"'{frag}' が検出されない。実際: {msgs}"


def test_clean_tree_has_no_problems():
    s = build({"items/entity/e.yaml": ITEMS_OK, "items/data-item/d.yaml": DATA_OK})
    assert not s.problems, [str(p) for p in s.problems]


def test_yaml_syntax_error_becomes_problem():
    """引用符なしの ': '（コロン+空白）で壊れた YAML は例外にせず error として報告し、
    コロン起因のヒントを添える（他ファイルの読み込みは続行する）。"""
    bad = "- id: e1\n  name: 顧客\n  physical_name: 補足: 内部コード\n"
    s = build({"items/entity/e.yaml": bad, "items/data-item/d.yaml": DATA_OK})
    assert_problem(s, "YAML 文法エラー", "コロン+空白", "e.yaml")
    assert "d1" in s.items                                  # 他ファイルは読み込まれる


def test_quoted_colon_value_loads_cleanly():
    """': ' を含む値でも引用符（specdb mutate 相当）なら正常に読める。"""
    ok = ('- id: e1\n  name: 顧客\n  physical_name: M_CUST\n'
          '  columns:\n    - { item: d1, physical_name: CODE }\n'
          '  note: "補足: 内部コード"\n')
    s = build({"items/entity/e.yaml": ok, "items/data-item/d.yaml": DATA_OK})
    assert not any("文法エラー" in str(p) for p in s.problems)
    assert s.items["e1"].attrs["note"] == "補足: 内部コード"


def test_cardinality_violation():
    s = build({
        "items/entity/e.yaml": ITEMS_OK + "- { id: e2, name: 空, physical_name: T_EMPTY }\n",
        "items/data-item/d.yaml": DATA_OK})
    assert_problem(s, "多重度 1..* に違反", "実際 0 本")


def test_cardinality_skips_deprecated():
    s = build({
        "items/entity/e.yaml": ITEMS_OK +
            "- { id: e2, name: 廃止, physical_name: T_OLD, status: deprecated }\n",
        "items/data-item/d.yaml": DATA_OK})
    assert not s.problems, [str(p) for p in s.problems]


LISTMAP_MM = """
version: 1
item_types:
  doc-type:
    label: 文書種別
    label_field: name
    attributes:
      name:   { kind: string, required: true }
      params: { kind: list }
      doc_no: { kind: map }
"""


def test_list_and_map_kinds_accept_nested_values():
    s = build({"metamodel": LISTMAP_MM, "items/doc-type/d.yaml":
               "- { id: dt1, name: 基本設計, params: [a, b], doc_no: { prefix: SD } }\n"})
    assert not s.problems, [str(p) for p in s.problems]
    assert s.items["dt1"].attrs["params"] == ["a", "b"]
    assert s.items["dt1"].attrs["doc_no"] == {"prefix": "SD"}


def test_list_kind_rejects_scalar():
    s = build({"metamodel": LISTMAP_MM,
               "items/doc-type/d.yaml": "- { id: dt1, name: x, params: notalist }\n"})
    assert_problem(s, "'params' は list であるべき")


def test_item_attribute_unique():
    s = build({
        "items/entity/e.yaml": ITEMS_OK + """
- id: e2
  name: 複製
  physical_name: M_CUST
  columns:
    - { item: d1, physical_name: CODE }
""",
        "items/data-item/d.yaml": DATA_OK})
    assert_problem(s, "'physical_name' の値 'M_CUST' が e1 と重複")


def test_relation_attribute_unique_per_src():
    s = build({
        "items/entity/e.yaml": """
- id: e1
  name: 顧客
  physical_name: M_CUST
  columns:
    - { item: d1, physical_name: CODE }
    - { item: d2, physical_name: CODE }
""",
        "items/data-item/d.yaml": DATA_OK + "- { id: d2, name: 名称 }\n"})
    assert_problem(s, "'physical_name' の値 'CODE' が同じ from 内で")


def test_duplicate_relation_record_warns():
    s = build({
        "items/entity/e.yaml": ITEMS_OK,
        "items/data-item/d.yaml": DATA_OK,
        "relations/r.yaml":
            "- { type: has-column, from: e1, to: d1, physical_name: CODE2 }\n"
            "- { type: has-column, from: e1, to: d1, physical_name: CODE3 }\n"})
    assert_problem(s, "warn", "同じ関係レコードが重複")


def test_relation_status_validated():
    s = build({
        "items/entity/e.yaml": ITEMS_OK,
        "items/data-item/d.yaml": DATA_OK,
        "relations/r.yaml":
            "- { type: has-column, from: e1, to: d1, physical_name: X, status: done }\n"})
    assert_problem(s, "未知の status 'done'")


def test_embedded_core_attributes_validated():
    s = build({
        "items/entity/e.yaml": """
- id: e1
  name: 顧客
  physical_name: M_CUST
  columns:
    - { item: d1, physical_name: CODE, status: bogus, source: { location: { page: 1 } } }
""",
        "items/data-item/d.yaml": DATA_OK})
    assert_problem(s, "未知の status 'bogus'", "source は doc を持つ")


def test_source_requires_doc():
    s = build({
        "items/entity/e.yaml": ITEMS_OK,
        "items/data-item/d.yaml": """
- id: d1
  name: コード
  source:
    - { doc: a.docx }
    - { note: docがない }
"""})
    assert_problem(s, "source は doc を持つ")


def test_source_normalized_to_list():
    s = build({
        "items/entity/e.yaml": ITEMS_OK,
        "items/data-item/d.yaml":
            "- { id: d1, name: コード, source: [ { doc: a.docx }, { doc: b.docx } ] }\n"})
    assert not s.problems
    assert s.items["d1"].source == [{"doc": "a.docx"}, {"doc": "b.docx"}]
    s = build({
        "items/entity/e.yaml": ITEMS_OK,
        "items/data-item/d.yaml": "- { id: d1, name: コード, source: { doc: a.docx } }\n"})
    assert s.items["d1"].source == [{"doc": "a.docx"}]


ORDERED_MM = METAMODEL.replace(
    'cardinality: { from: "1..*" }', 'cardinality: { from: "1..*" }\n    ordered: true')


def test_ordered_sorts_by_explicit_order():
    s = build({
        "metamodel": ORDERED_MM,
        "items/entity/e.yaml": """
- id: e1
  name: 顧客
  physical_name: M_CUST
  columns:
    - { item: d2, physical_name: NAME, order: 2 }
    - { item: d1, physical_name: CODE, order: 1 }
""",
        "items/data-item/d.yaml": DATA_OK + "- { id: d2, name: 名称 }\n"})
    assert not s.problems, [str(p) for p in s.problems]  # order は宣言不要の暗黙属性
    assert [r.dst for r in s.relations_of("has-column", src="e1")] == ["d1", "d2"]


def test_ordered_preserves_insertion_order():
    s = build({
        "metamodel": ORDERED_MM,
        "items/entity/e.yaml": """
- id: e1
  name: 顧客
  physical_name: M_CUST
  columns:
    - { item: d2, physical_name: NAME }
    - { item: d1, physical_name: CODE }
""",
        "items/data-item/d.yaml": DATA_OK + "- { id: d2, name: 名称 }\n"})
    assert [r.dst for r in s.relations_of("has-column", src="e1")] == ["d2", "d1"]


NS_MM = METAMODEL + "\nnamespaces: { pay: 決済 }\n"


def test_namespace_qualifies_ids_and_refs():
    s = build({
        "metamodel": NS_MM,
        "items/pay/entity/e.yaml": ITEMS_OK,       # → pay:e1、列参照 d1 → pay:d1
        "items/pay/data-item/d.yaml": DATA_OK + "- { id: d2, name: 名称 }\n",  # → pay:d1, pay:d2
        "items/data-item/g.yaml": "- { id: g1, name: 共通コード }\n",
        "relations/pay/r.yaml":
            "- { type: has-column, from: pay:e1, to: pay:d2, physical_name: NAME }\n"})
    assert not s.problems, [str(p) for p in s.problems]
    assert {"pay:e1", "pay:d1", "pay:d2", "g1"} <= set(s.items)


def test_namespace_unqualified_ref_resolves_within_namespace():
    s = build({
        "metamodel": NS_MM,
        "items/pay/entity/e.yaml": ITEMS_OK,
        "items/pay/data-item/d.yaml": DATA_OK,
        "items/data-item/g.yaml": "- { id: g1, name: 共通コード }\n",
        "relations/pay/r.yaml":
            "- { type: has-column, from: e1, to: g1, physical_name: G_CODE }\n"})
    assert_problem(s, "'pay:g1' を参照")   # g1 は pay:g1 に解決され未定義になる


def test_undeclared_namespace_directory_is_error():
    s = build({
        "items/entity/e.yaml": ITEMS_OK,
        "items/data-item/d.yaml": DATA_OK,
        "items/unknown-ns/entity/x.yaml": "- { id: x1, name: X, physical_name: T_X }\n"})
    assert_problem(s, "メタモデルに無い種別ディレクトリ 'unknown-ns'")


def test_metamodel_cardinality_format_checked():
    bad = METAMODEL.replace('cardinality: { from: "1..*" }',
                            'cardinality: { from: "abc", both: "1" }')
    s = build({"metamodel": bad,
               "items/entity/e.yaml": ITEMS_OK, "items/data-item/d.yaml": DATA_OK})
    assert_problem(s, "多重度 'abc' が不正", "cardinality のキーは from/to")


def test_parse_root_defaults_to_conventional_dir(tmp_path, monkeypatch):
    """--root 省略時は <cwd>/.specdb (metamodel.yaml があるもの) を優先し、
    無ければツール同梱データ (ROOT) にフォールバックする。"""
    from engine import DEFAULT_DATA_DIR, ROOT, parse_root

    monkeypatch.chdir(tmp_path)
    assert parse_root(["arg"]) == (ROOT, ["arg"])          # .specdb 不在 → フォールバック

    spec = tmp_path / DEFAULT_DATA_DIR
    spec.mkdir()
    assert parse_root([]) == (ROOT, [])                    # metamodel.yaml 無し → 対象外
    (spec / "metamodel.yaml").write_text("version: 1\n", encoding="utf-8")
    assert parse_root(["arg"]) == (Path(DEFAULT_DATA_DIR), ["arg"])

    other = tmp_path / "other"
    other.mkdir()
    (other / "metamodel.yaml").write_text("version: 1\n", encoding="utf-8")
    assert parse_root(["--root", "other"]) == (Path("other"), [])  # --root は常に最優先
