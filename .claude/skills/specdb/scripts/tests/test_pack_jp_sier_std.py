# -*- coding: utf-8 -*-
"""jp-sier-std 実パックの結合テスト — 消費側 extends → マージ → 準拠 → 生成。

同梱パック（specdb/packs/jp-sier-std）を実データとして解決し、Phase 1+2 の
機構（チェーン解決・メタモデルマージ・L1/L2 準拠・文書生成）が端から端まで
噛み合うことを固定する。標準パック自体の妥当性回帰も兼ねる。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import standard  # noqa: E402
from engine import Problem, Store  # noqa: E402
from generate import make_env  # noqa: E402

PACK_DIR = Path(__file__).resolve().parents[1] / "packs" / "jp-sier-std"


def write(base: Path, rel: str, text: str) -> None:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def consuming_project() -> Path:
    """jp-sier-std を継承する最小の受発注システム・プロジェクトを組む。"""
    root = Path(tempfile.mkdtemp(prefix="jp-sier-proj-"))
    # 同梱パックを vendored 位置（<root>/packs/jp-sier-std）へ複製して解決させる
    import shutil
    shutil.copytree(PACK_DIR, root / "packs" / "jp-sier-std")
    write(root, "metamodel.yaml", "version: 1\nextends: jp-sier-std@1.0\n")
    write(root, "documents/basic-design.yaml",
          "from_standard: basic-design\nsystem_name: 受発注\ndoc_no: SD-ORD-001\n"
          "version: '1.0'\npreface: { purpose: 目的である, scope: 範囲である }\n")
    write(root, "items/data-item/core.yaml",
          "- { id: di-0001, name: 顧客コード, type: 文字列, length: 8, "
          "description: 一意コード, status: approved }\n"
          "- { id: di-0002, name: 顧客名, type: 文字列, length: 40, status: approved }\n")
    write(root, "items/entity/core.yaml",
          "- id: ent-0001\n  name: 顧客\n  physical_name: M_CUSTOMER\n"
          "  description: 顧客マスタ\n  status: approved\n  columns:\n"
          "    - { item: di-0001, physical_name: CUST_CD, pk: true, required: true }\n"
          "    - { item: di-0002, physical_name: CUST_NM, required: true }\n")
    write(root, "items/screen/core.yaml",
          "- { id: scr-0001, name: 顧客一覧, screen_id: SCR-001, screen_type: 一覧, "
          "description: 顧客を一覧する, status: approved }\n")
    write(root, "relations/displays.yaml",
          "- { type: displays, from: scr-0001, to: di-0001, status: approved }\n"
          "- { type: displays, from: scr-0001, to: di-0002, status: approved }\n")
    return root


def load(root: Path):
    store = Store.load(root)
    return store, [str(p) for p in store.problems]


# ---------- パック自体の妥当性 ----------

def test_pack_resolves_and_merges_clean():
    root = consuming_project()
    store, problems = load(root)
    assert [p.name for p in store.packs] == ["jp-sier-std"]
    assert not store.has_errors(), problems
    # パックのドメイン種別がマージされている
    for t in ("screen", "entity", "data-item", "business-rule", "external-interface"):
        assert t in store.mm.item_types


def test_data_validates_against_pack_model():
    root = consuming_project()
    store, _ = load(root)
    assert store.items["ent-0001"].attrs["physical_name"] == "M_CUSTOMER"
    # has-column が embedded から正規化され、順序が保たれている
    cols = store.relations_of("has-column", src="ent-0001")
    assert [c.attrs["physical_name"] for c in cols] == ["CUST_CD", "CUST_NM"]


def test_no_false_orphan_warnings_for_screens():
    root = consuming_project()
    _store, problems = load(root)
    assert not any("孤児" in m and "scr-0001" in m for m in problems)


# ---------- L2 準拠検証 ----------

def test_conformance_clean_for_valid_project():
    root = consuming_project()
    store, _ = load(root)
    probs: list[Problem] = list(store.problems)
    standard.check_conformance_rules(root, store.packs, store, probs)
    assert not any(p.level == "error" for p in probs), [str(p) for p in probs]


def test_require_documents_missing_basic_design():
    root = consuming_project()
    (root / "documents" / "basic-design.yaml").unlink()
    store, _ = load(root)
    probs: list[Problem] = list(store.problems)
    standard.check_conformance_rules(root, store.packs, store, probs)
    assert any("STD-E201" in str(p) for p in probs)


def test_screen_description_required_at_review():
    root = consuming_project()
    write(root, "items/screen/core.yaml",
          "- { id: scr-0002, name: 登録, screen_id: SCR-002, status: review }\n")
    write(root, "relations/displays.yaml",
          "- { type: displays, from: scr-0002, to: di-0001, status: review }\n")
    store, _ = load(root)
    probs: list[Problem] = list(store.problems)
    standard.check_conformance_rules(root, store.packs, store, probs)
    assert any("STD-E211" in str(p) and "scr-0002" in str(p) for p in probs)


# ---------- 消費側による緩和は拒否される ----------

def test_project_cannot_relax_screen_id_unique():
    root = consuming_project()
    write(root, "metamodel.yaml",
          "version: 1\nextends: jp-sier-std@1.0\n"
          "item_types:\n  screen:\n    attributes: { screen_id: { unique: false } }\n")
    _store, problems = load(root)
    assert any("STD-E103" in m for m in problems)


# ---------- 文書生成（3 文書がテンプレートで描画される） ----------

def test_generates_all_documents():
    root = consuming_project()
    store = Store.load(root)
    packs = store.packs
    docs = dict(standard.collect_documents(root, packs, store.problems))
    assert set(docs) == {"basic-design", "table-spec", "screen-spec"}
    # abstract 基本設計書はパラメータ展開済み
    assert docs["basic-design"]["output"] == "基本設計書_受発注.html"

    env = make_env(store, standard.template_search_dirs(root, packs),
                   standard.prefix_map(packs))
    for name, doc in docs.items():
        text = env.get_template(doc["template"]).render(
            doc=doc, store=store, mm=store.mm,
            generated_at="2026-07-05T00:00:00+09:00", data_rev="testrev",
            data_history=[])
        assert text.strip(), f"{name} が空描画"
    # テーブル定義書に列の物理名が出る
    tbl = env.get_template(docs["table-spec"]["template"]).render(
        doc=docs["table-spec"], store=store, mm=store.mm,
        generated_at="2026-07-05T00:00:00+09:00", data_rev="testrev", data_history=[])
    assert "CUST_CD" in tbl and "M_CUSTOMER" in tbl


def test_block_override_via_std_prefix():
    """消費側が {% extends "std/basic-design.html.j2" %} で preface だけ差し替えられる。"""
    root = consuming_project()
    write(root, "templates/basic-design.html.j2",
          '{% extends "std/basic-design.html.j2" %}'
          '{% block preface %}<div id="custom-preface">独自前書き</div>{% endblock %}')
    store = Store.load(root)
    packs = store.packs
    standard.check_template_overrides(root, packs, store.problems)
    # extends を使った部分上書きなので STD-W303（全置換）は出ない
    assert not any("STD-W303" in str(p) for p in store.problems)
    docs = dict(standard.collect_documents(root, packs, store.problems))
    env = make_env(store, standard.template_search_dirs(root, packs),
                   standard.prefix_map(packs))
    text = env.get_template(docs["basic-design"]["template"]).render(
        doc=docs["basic-design"], store=store, mm=store.mm,
        generated_at="2026-07-05T00:00:00+09:00", data_rev="r", data_history=[])
    assert "custom-preface" in text          # 独自ブロックが効く
    assert "改訂履歴" in text                 # 他ブロックは標準のまま
