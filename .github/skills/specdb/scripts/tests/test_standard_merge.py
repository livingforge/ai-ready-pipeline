# -*- coding: utf-8 -*-
"""標準パック Phase 2 — メタモデルマージ・L1/L2 準拠検証・pack.lock の仕様を固定する。

設計: .specdb/docs/standard-pack-design.md §6, §7, §5.3。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import standard  # noqa: E402
from engine import Problem, Store  # noqa: E402


def build(tree: dict) -> Path:
    base = Path(tempfile.mkdtemp(prefix="specdb-mrg-test-"))
    for rel, text in tree.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return base


# 全社パック: screen 種別（description 必須・screen_id unique）+ displays 関係
PACK_CORP = "pack: corp-std\nversion: '2.0.0'\ndescription: 全社標準\n"
CORP_MM = """
version: 1
item_types:
  screen:
    label: 画面
    label_field: name
    id_prefix: scr-
    attributes:
      name:        { kind: string, required: true }
      screen_id:   { kind: string, required: true, unique: true }
      description: { kind: string, required: true }
      kind:        { kind: enum, values: [入力, 照会], extensible: true }
  data-item:
    label: データ項目
    label_field: name
    id_prefix: di-
    attributes:
      name: { kind: string, required: true }
relation_types:
  displays:
    label: 表示する
    from: screen
    to: data-item
    cardinality: { from: "1..*" }
    ordered: true
"""


def corp_tree(extra: dict | None = None, mm: str = CORP_MM) -> dict:
    t = {"packs/corp-std/pack.yaml": PACK_CORP,
         "packs/corp-std/metamodel/core.yaml": mm}
    if extra:
        t.update(extra)
    return t


def merged(root: Path):
    problems: list[Problem] = []
    packs = standard.resolve_chain(root, problems)
    with open(root / "metamodel.yaml", encoding="utf-8") as f:
        import yaml
        data = yaml.safe_load(f)
    eff = standard.merge_and_check(root, data, packs, problems)
    return eff, [str(p) for p in problems]


# ---------- マージ ----------

def test_merge_brings_pack_types_into_effective_model():
    root = build(corp_tree({"metamodel.yaml": "version: 1\nextends: corp-std@2.0\n"}))
    eff, problems = merged(root)
    assert problems == []
    assert "screen" in eff["item_types"] and "displays" in eff["relation_types"]
    assert eff["item_types"]["screen"]["attributes"]["screen_id"]["unique"] is True


def test_project_adds_type_freely():
    root = build(corp_tree({"metamodel.yaml": """
version: 1
extends: corp-std@2.0
item_types:
  batch-job:
    label: バッチ
    id_prefix: bat-
    attributes: { name: { kind: string, required: true } }
"""}))
    eff, problems = merged(root)
    assert problems == [] and "batch-job" in eff["item_types"]


def test_project_can_strengthen_required():
    # data-item.name は元々 required。追加属性を required にするのは自由（厳格化）
    root = build(corp_tree({"metamodel.yaml": """
version: 1
extends: corp-std@2.0
item_types:
  data-item:
    attributes: { code: { kind: string, required: true } }
"""}))
    eff, problems = merged(root)
    assert problems == []
    assert eff["item_types"]["data-item"]["attributes"]["code"]["required"] is True


# ---------- L1 緩和禁止 ----------

def relax(overlay_mm: str):
    root = build(corp_tree({"metamodel.yaml": overlay_mm}))
    return merged(root)[1]


def test_required_relaxation_is_std_e102():
    probs = relax("""
version: 1
extends: corp-std@2.0
item_types:
  screen:
    attributes: { description: { required: false } }
""")
    assert any("STD-E102" in m and "corp-std" in m for m in probs)


def test_unique_removal_is_std_e103():
    probs = relax("""
version: 1
extends: corp-std@2.0
item_types:
  screen:
    attributes: { screen_id: { unique: false } }
""")
    assert any("STD-E103" in m for m in probs)


def test_kind_change_is_std_e101():
    probs = relax("""
version: 1
extends: corp-std@2.0
item_types:
  screen:
    attributes: { name: { kind: int } }
""")
    assert any("STD-E101" in m for m in probs)


def test_enum_extension_gated_by_extensible():
    # kind は extensible なので値追加は許される
    root = build(corp_tree({"metamodel.yaml": """
version: 1
extends: corp-std@2.0
item_types:
  screen:
    attributes: { kind: { kind: enum, values: [入力, 照会, 帳票] } }
"""}))
    eff, problems = merged(root)
    assert problems == [] and "帳票" in eff["item_types"]["screen"]["attributes"]["kind"]["values"]


def test_enum_extension_without_extensible_is_std_e104():
    # displays 関係の…ではなく、non-extensible な enum を作って検証する
    mm = CORP_MM.replace("extensible: true", "")   # kind を non-extensible に
    root = build(corp_tree(extra={"metamodel.yaml": """
version: 1
extends: corp-std@2.0
item_types:
  screen:
    attributes: { kind: { kind: enum, values: [入力, 照会, 帳票] } }
"""}, mm=mm))
    assert any("STD-E104" in m for m in merged(root)[1])


def test_id_prefix_change_is_std_e121():
    probs = relax("""
version: 1
extends: corp-std@2.0
item_types:
  screen: { id_prefix: gmn- }
""")
    assert any("STD-E121" in m for m in probs)


def test_cardinality_relaxation_is_std_e112():
    probs = relax("""
version: 1
extends: corp-std@2.0
relation_types:
  displays: { cardinality: { from: "0..*" } }
""")
    assert any("STD-E112" in m for m in probs)


def test_endpoint_removal_is_std_e111():
    probs = relax("""
version: 1
extends: corp-std@2.0
relation_types:
  displays: { from: [data-item] }
""")
    assert any("STD-E111" in m for m in probs)


def test_ordered_removal_is_std_e113():
    probs = relax("""
version: 1
extends: corp-std@2.0
relation_types:
  displays: { ordered: false }
""")
    assert any("STD-E113" in m for m in probs)


def test_endpoint_addition_is_allowed():
    root = build(corp_tree({"metamodel.yaml": """
version: 1
extends: corp-std@2.0
item_types:
  note:
    label: 注記
    id_prefix: nt-
    attributes: { name: { kind: string, required: true } }
relation_types:
  displays: { from: [screen, note] }
"""}))
    eff, problems = merged(root)
    assert problems == []
    assert set(eff["relation_types"]["displays"]["from"]) == {"screen", "note"}


# ---------- チェーン: 事業部も全社を緩和できない（推移的） ----------

def test_chain_layer_cannot_relax_and_names_layers():
    root = build({
        "metamodel.yaml": "version: 1\nextends: div-std@1.0\n",
        "packs/div-std/pack.yaml":
            "pack: div-std\nversion: '1.0.0'\ndescription: 事業部\nextends: corp-std@2.0\n",
        "packs/div-std/metamodel/core.yaml":
            "version: 1\nitem_types:\n  screen:\n    attributes: { description: { required: false } }\n",
        "packs/corp-std/pack.yaml": PACK_CORP,
        "packs/corp-std/metamodel/core.yaml": CORP_MM,
    })
    _eff, problems = merged(root)
    assert any("STD-E102" in m and "div-std" in m and "corp-std" in m for m in problems)


# ---------- 予約名前空間 STD-E131 ----------

def test_reserved_namespace_redeclare_is_std_e131():
    root = build({
        "metamodel.yaml": "version: 1\nextends: corp-std@2.0\nnamespaces: { std: 独自 }\n",
        "packs/corp-std/pack.yaml": PACK_CORP + "reserved_namespaces: { std: 標準共通 }\n",
        "packs/corp-std/metamodel/core.yaml": CORP_MM,
    })
    assert any("STD-E131" in m for m in merged(root)[1])


# ---------- L2 準拠検証 ----------

def l2_fixture(rules: str, project_docs: dict | None = None,
               items: dict | None = None) -> Path:
    tree = {
        "metamodel.yaml": "version: 1\nextends: corp-std@2.0\n",
        "packs/corp-std/pack.yaml": PACK_CORP,
        "packs/corp-std/metamodel/core.yaml": CORP_MM,
        "packs/corp-std/conformance/rules.yaml": rules,
        "packs/corp-std/documents/basic-design.yaml":
            "abstract: true\ntitle: 基本設計書（{system_name}）\n"
            "output: bd_{system_name}.md\ntemplate: bd.md.j2\n"
            "params: { required: [system_name] }\n",
    }
    tree.update(project_docs or {})
    tree.update(items or {})
    return build(tree)


def run_l2(root: Path, for_baseline: bool = False):
    store = Store.load(root)
    problems: list[Problem] = list(store.problems)
    standard.check_conformance_rules(root, store.packs, store, problems, for_baseline)
    return [str(p) for p in problems]


def test_require_documents_missing_is_std_e201():
    root = l2_fixture("require_documents: [basic-design]\n")
    assert any("STD-E201" in m for m in run_l2(root))


def test_require_documents_satisfied_when_instantiated():
    root = l2_fixture("require_documents: [basic-design]\n", {
        "documents/basic-design.yaml": "from_standard: basic-design\nsystem_name: 受発注\n"})
    assert not any("STD-E201" in m for m in run_l2(root))


def test_attribute_rule_fires_on_status():
    root = l2_fixture(
        "attribute_rules:\n"
        "  - { type: screen, attribute: description, when_status: [approved], level: error }\n",
        items={"items/screen/s.yaml":
               "- { id: scr-1, name: 一覧, screen_id: S01, status: approved }\n"})
    # description は engine の required でもあるので、まず required 欠落が出る。
    # ここでは status 連動の STD-E211 が出ることを確認する。
    msgs = run_l2(root)
    assert any("STD-E211" in m for m in msgs)


def test_baseline_requires_approved_is_std_e221():
    root = l2_fixture("status_rules: { baseline_requires: approved }\n", items={
        "items/screen/s.yaml":
        "- { id: scr-1, name: 一覧, screen_id: S01, description: 説明, status: review }\n"})
    assert not any("STD-E221" in m for m in run_l2(root, for_baseline=False))
    assert any("STD-E221" in m for m in run_l2(root, for_baseline=True))


# ---------- pack.lock ----------

def test_lock_write_and_verify_roundtrip():
    root = build(corp_tree({"metamodel.yaml": "version: 1\nextends: corp-std@2.0\n"}))
    problems: list[Problem] = []
    packs = standard.resolve_chain(root, problems)
    standard.write_lock(root, packs)
    assert (root / "pack.lock").is_file()
    standard.verify_lock(root, packs, problems)
    assert problems == []


def test_lock_mismatch_is_warn_and_frozen_is_error():
    root = build(corp_tree({"metamodel.yaml": "version: 1\nextends: corp-std@2.0\n"}))
    problems: list[Problem] = []
    packs = standard.resolve_chain(root, problems)
    standard.write_lock(root, packs)
    # パック内容を書き換える → ハッシュ不一致
    (root / "packs/corp-std/metamodel/core.yaml").write_text(CORP_MM + "\n# 変更\n",
                                                             encoding="utf-8")
    warn_probs: list[Problem] = []
    standard.verify_lock(root, packs, warn_probs, frozen=False)
    assert warn_probs and warn_probs[0].level == "warn"
    err_probs: list[Problem] = []
    standard.verify_lock(root, packs, err_probs, frozen=True)
    assert err_probs and err_probs[0].level == "error"


def test_no_lock_is_silent():
    root = build(corp_tree({"metamodel.yaml": "version: 1\nextends: corp-std@2.0\n"}))
    problems: list[Problem] = []
    packs = standard.resolve_chain(root, problems)
    standard.verify_lock(root, packs, problems)   # lock 未作成
    assert problems == []


# ---------- Store.load を通じた統合（L1 が data 検証と併走する） ----------

def test_store_load_validates_data_against_merged_model():
    root = build(corp_tree({
        "metamodel.yaml": "version: 1\nextends: corp-std@2.0\n",
        # screen は description 必須 → 欠落データは engine が error にする
        "items/screen/s.yaml": "- { id: scr-1, name: 一覧, screen_id: S01 }\n",
    }))
    store = Store.load(root)
    assert store.has_errors()
    assert any("description" in str(p) for p in store.problems)
    assert [p.name for p in store.packs] == ["corp-std"]
