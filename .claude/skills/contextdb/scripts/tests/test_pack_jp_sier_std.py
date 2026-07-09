# -*- coding: utf-8 -*-
"""jp-sier-std 実パックの結合テスト — 消費側 extends → マージ → 準拠 → 生成。

同梱パック（contextdb/packs/jp-sier-std）を実データとして解決し、Phase 1+2 の
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
    write(root, "metamodel.yaml", "version: 1\nextends: jp-sier-std@1.1\n")
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
    # 工程の両端（要件・詳細設計）とトレース。要件 req-0001 は realizes で実現され、
    # 詳細設計 mod-0001 は refines で画面を、has-method でメソッドを持つ。
    write(root, "items/requirement/core.yaml",
          "- { id: req-0001, req_id: F-01, name: 顧客照会, kind: 機能, "
          "statement: 顧客を一覧照会できる, status: approved }\n")
    write(root, "items/module/core.yaml",
          "- { id: mod-0001, module_id: MOD-01, class_name: CustomerListController, "
          "layer: Controller, package: jp.co.demo, description: 顧客一覧の制御, status: approved }\n")
    write(root, "items/method/core.yaml",
          "- { id: mth-0001, method_id: MTH-01, signature: 'CustomerListController#list', "
          "description: 顧客一覧を返す, status: approved }\n")
    write(root, "relations/realizes.yaml",
          "- { type: realizes, from: scr-0001, to: req-0001, status: approved }\n"
          "- { type: realizes, from: mod-0001, to: req-0001, status: approved }\n")
    write(root, "relations/refines.yaml",
          "- { type: refines, from: mod-0001, to: scr-0001, status: approved }\n")
    write(root, "relations/has-method.yaml",
          "- { type: has-method, from: mod-0001, to: mth-0001, status: approved }\n")
    # テスト工程（V字右側）: 単体テスト tc-0001 が要件とメソッドを verifies で検証する。
    write(root, "items/test-case/core.yaml",
          "- { id: tc-0001, test_id: T-01, name: 顧客一覧の表示確認, level: 単体, "
          "precondition: 顧客が登録済み, steps: 顧客一覧画面を開く, "
          "expected: 登録済みの顧客が一覧に表示される, status: approved }\n")
    write(root, "relations/verifies.yaml",
          "- { type: verifies, from: tc-0001, to: req-0001, status: approved }\n"
          "- { type: verifies, from: tc-0001, to: mth-0001, status: approved }\n")
    # テスト実行結果（設計 test-case と分離）: tr-0001 が tc-0001 を実行し合格。
    write(root, "items/test-run/core.yaml",
          "- { id: tr-0001, run_id: R-01, result: 合格, executed_on: '2026-07-01', "
          "tester: 田中, status: approved }\n")
    write(root, "relations/executes.yaml",
          "- { type: executes, from: tr-0001, to: tc-0001, status: approved }\n")
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
    # パックのドメイン種別（要件〜詳細設計〜テスト設計/実行の全工程）がマージされている
    for t in ("requirement", "screen", "entity", "data-item", "business-rule",
              "external-interface", "module", "method", "test-case", "test-run"):
        assert t in store.mm.item_types
    for r in ("realizes", "refines", "has-method", "verifies", "executes"):
        assert r in store.mm.relation_types


def test_data_validates_against_pack_model():
    root = consuming_project()
    store, _ = load(root)
    assert store.items["ent-0001"].attrs["physical_name"] == "M_CUSTOMER"
    # has-column が embedded から正規化され、順序が保たれている
    cols = store.relations_of("has-column", src="ent-0001")
    assert [c.attrs["physical_name"] for c in cols] == ["CUST_CD", "CUST_NM"]


def test_trace_relations_across_phases():
    """要件↔基本設計↔詳細設計のトレース（realizes/refines/has-method）が張れて検証を通る。"""
    root = consuming_project()
    store, problems = load(root)
    assert not store.has_errors(), problems
    # 画面もモジュールも要件を実現している
    realizers = {r.src for r in store.relations_of("realizes", dst="req-0001")}
    assert realizers == {"scr-0001", "mod-0001"}
    # モジュールは画面を詳細化し、メソッドを持つ
    assert store.relations_of("refines", src="mod-0001")[0].dst == "scr-0001"
    assert store.relations_of("has-method", src="mod-0001")[0].dst == "mth-0001"
    # 実現された要件・付属メソッドは孤児警告されない
    assert not any("req-0001" in m and "孤児" in m for m in problems)
    assert not any("mth-0001" in m and "孤児" in m for m in problems)


def test_unrealized_requirement_warns_as_coverage_gap():
    """どの設計要素からも realizes されない要件はカバレッジ・ギャップとして warn。"""
    root = consuming_project()
    write(root, "items/requirement/core.yaml",
          "- { id: req-0001, req_id: F-01, name: 顧客照会, kind: 機能, "
          "statement: 顧客を一覧照会できる, status: approved }\n"
          "- { id: req-0002, req_id: F-02, name: 未実装機能, kind: 機能, "
          "statement: まだ設計に落ちていない, status: approved }\n")
    _store, problems = load(root)
    assert any("req-0002" in m and "孤児" in m for m in problems)


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
          "version: 1\nextends: jp-sier-std@1.1\n"
          "item_types:\n  screen:\n    attributes: { screen_id: { unique: false } }\n")
    _store, problems = load(root)
    assert any("STD-E103" in m for m in problems)


# ---------- 文書生成（3 文書がテンプレートで描画される） ----------

def test_generates_all_documents():
    root = consuming_project()
    store = Store.load(root)
    packs = store.packs
    docs = dict(standard.collect_documents(root, packs, store.problems))
    assert set(docs) == {"basic-design", "requirement-spec", "detail-design",
                         "test-spec", "test-result", "traceability-matrix"}
    # abstract 基本設計書はパラメータ展開済み
    assert docs["basic-design"]["output"] == "基本設計書_受発注.html"

    env = make_env(store, standard.template_search_dirs(root, packs),
                   standard.prefix_map(packs))
    rendered = {}
    for name, doc in docs.items():
        rendered[name] = env.get_template(doc["template"]).render(
            doc=doc, store=store, mm=store.mm,
            generated_at="2026-07-05T00:00:00+09:00", data_rev="testrev",
            data_history=[])
        assert rendered[name].strip(), f"{name} が空描画"
    # 基本設計書（テーブル一覧の章）に列の物理名が出る
    assert "CUST_CD" in rendered["basic-design"] and "M_CUSTOMER" in rendered["basic-design"]
    # 詳細設計書にモジュール（クラス）とメソッドが出る
    assert "CustomerListController" in rendered["detail-design"]
    assert "CustomerListController#list" in rendered["detail-design"]
    # トレーサビリティ表に要件と実現の連鎖が出る
    assert "F-01" in rendered["traceability-matrix"]
    assert "CustomerListController" in rendered["traceability-matrix"]
    # テスト仕様書にテストケースとレベル・期待結果が出る
    assert "T-01" in rendered["test-spec"]
    assert "顧客一覧の表示確認" in rendered["test-spec"]
    assert "単体" in rendered["test-spec"]
    # テスト結果報告書に実施サマリと最新結果（合格）が出る
    assert "テスト実施サマリ" in rendered["test-result"]
    assert "T-01" in rendered["test-result"]
    assert "合格" in rendered["test-result"]
    # V字右側（verifies）が対応表の要件行「検証するテスト」列に現れる
    assert "顧客一覧の表示確認" in rendered["traceability-matrix"]


def test_verifies_trace_and_coverage_gap():
    """テスト工程: verifies が張れて検証を通り、未検証の要件は検証ギャップになる。"""
    root = consuming_project()
    # req-0002 は設計にも落ち、テストでも検証されない（検証ギャップ候補）
    write(root, "items/requirement/core.yaml",
          "- { id: req-0001, req_id: F-01, name: 顧客照会, kind: 機能, "
          "statement: 顧客を一覧照会できる, status: approved }\n"
          "- { id: req-0002, req_id: F-02, name: 顧客登録, kind: 機能, "
          "statement: 顧客を登録できる, status: approved }\n")
    write(root, "relations/realizes.yaml",
          "- { type: realizes, from: scr-0001, to: req-0001, status: approved }\n"
          "- { type: realizes, from: mod-0001, to: req-0001, status: approved }\n"
          "- { type: realizes, from: mod-0001, to: req-0002, status: approved }\n")
    store, problems = load(root)
    assert not store.has_errors(), problems
    # tc-0001 は req-0001 と mth-0001 を検証している
    verified_reqs = {r.dst for r in store.relations_of("verifies", src="tc-0001")}
    assert verified_reqs == {"req-0001", "mth-0001"}
    # req-0001 は検証済み、req-0002 は未検証（検証ギャップ）
    assert store.relations_of("verifies", dst="req-0001")
    assert not store.relations_of("verifies", dst="req-0002")

    packs = store.packs
    docs = dict(standard.collect_documents(root, packs, store.problems))
    env = make_env(store, standard.template_search_dirs(root, packs),
                   standard.prefix_map(packs))
    matrix = env.get_template(docs["traceability-matrix"]["template"]).render(
        doc=docs["traceability-matrix"], store=store, mm=store.mm,
        generated_at="2026-07-05T00:00:00+09:00", data_rev="r", data_history=[])
    # テスト検証シートに未検証要件 F-02 が検証ギャップとして出る
    assert "F-02" in matrix and "検証ギャップ" in matrix


def test_executes_cardinality_requires_a_case():
    """executes は from 多重度 1: どのケースも実行しない test-run は error。"""
    root = consuming_project()
    write(root, "items/test-run/core.yaml",
          "- { id: tr-0001, run_id: R-01, result: 合格, executed_on: '2026-07-01', status: approved }\n"
          "- { id: tr-9999, run_id: R-99, result: 合格, executed_on: '2026-07-02', status: approved }\n")
    _store, problems = load(root)
    # tr-9999 は executes を持たない → from 多重度 1 違反
    assert any("tr-9999" in m and "executes" in m for m in problems)


def test_latest_run_and_requirement_verdict():
    """最新の test-run（実施日で決定）が結果一覧・要件別合否に反映される。"""
    root = consuming_project()
    # tc-0001 を 2 回実行: 先に不合格、後で合格 → 最新は合格
    write(root, "items/test-run/core.yaml",
          "- { id: tr-0001, run_id: R-01, result: 不合格, executed_on: '2026-07-01', "
          "tester: 田中, defect: BUG-1, status: approved }\n"
          "- { id: tr-0002, run_id: R-02, result: 合格, executed_on: '2026-07-05', "
          "tester: 田中, status: approved }\n")
    write(root, "relations/executes.yaml",
          "- { type: executes, from: tr-0001, to: tc-0001, status: approved }\n"
          "- { type: executes, from: tr-0002, to: tc-0001, status: approved }\n")
    store, problems = load(root)
    assert not store.has_errors(), problems
    packs = store.packs
    docs = dict(standard.collect_documents(root, packs, store.problems))
    env = make_env(store, standard.template_search_dirs(root, packs),
                   standard.prefix_map(packs))
    report = env.get_template(docs["test-result"]["template"]).render(
        doc=docs["test-result"], store=store, mm=store.mm,
        generated_at="2026-07-09T00:00:00+09:00", data_rev="r", data_history=[])
    # req-0001 は tc-0001（最新=合格）で検証されるので要件別判定が「合格」
    assert "T-01：合格" in report
    # 合格率 100.0% が実施サマリに出る（実施 1・合格 1）
    assert "100.0%" in report


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
