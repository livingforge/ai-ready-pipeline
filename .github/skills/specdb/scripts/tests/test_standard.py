# -*- coding: utf-8 -*-
"""標準パック（standard.py）のテスト — 解決・カタログ・マージ・上書き可視化の仕様を固定する。

設計: .specdb/docs/standard-pack-design.md（Phase 1 の範囲）。
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import standard  # noqa: E402
import yaml  # noqa: E402
from engine import Problem  # noqa: E402
from generate import make_env  # noqa: E402


def write_tree(base: Path, tree: dict) -> Path:
    for rel, text in tree.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return base


def build(tree: dict) -> Path:
    """一時ディレクトリにプロジェクト+パックのツリーを組み立てる。"""
    return write_tree(Path(tempfile.mkdtemp(prefix="specdb-std-test-")), tree)


def resolve(root: Path):
    problems: list[Problem] = []
    packs = standard.resolve_chain(root, problems)
    return packs, [str(p) for p in problems]


PACK_CORP = "pack: corp-std\nversion: '2.0.1'\ndescription: 全社標準\n"
PACK_DIV = ("pack: div-std\nversion: '1.2.3'\ndescription: 事業部標準\n"
            "extends: corp-std@2.0\n")


# ---------- 継承チェーンの解決 ----------

def test_no_extends_is_standalone():
    root = build({"metamodel.yaml": "version: 1\n"})
    packs, problems = resolve(root)
    assert packs == [] and problems == []


def test_resolve_vendored_chain():
    root = build({
        "proj/metamodel.yaml": "version: 1\nextends: div-std@1.2\n",
        "proj/packs/div-std/pack.yaml": PACK_DIV,
        "proj/packs/corp-std/pack.yaml": PACK_CORP,
    }) / "proj"
    packs, problems = resolve(root)
    assert problems == []
    assert [p.name for p in packs] == ["div-std", "corp-std"]  # 近い層から
    assert packs[0].version == "1.2.3"


def test_resolve_by_path_dev_mode():
    root = build({
        "proj/metamodel.yaml": "version: 1\nextends: ../corp\n",
        "corp/pack.yaml": PACK_CORP,
    }) / "proj"
    packs, problems = resolve(root)
    assert problems == [] and [p.name for p in packs] == ["corp-std"]


def test_version_mismatch_is_std_e002():
    root = build({
        "metamodel.yaml": "version: 1\nextends: corp-std@1.9\n",
        "packs/corp-std/pack.yaml": PACK_CORP,   # 実体は 2.0.1
    })
    packs, problems = resolve(root)
    assert packs == []
    assert any("STD-E002" in m for m in problems)


def test_unresolvable_pack_is_std_e001():
    root = build({"metamodel.yaml": "version: 1\nextends: nobody@1.0\n"})
    packs, problems = resolve(root)
    assert packs == [] and any("STD-E001" in m for m in problems)


def test_cycle_is_std_e003():
    root = build({
        "metamodel.yaml": "version: 1\nextends: a-std@1.0\n",
        "packs/a-std/pack.yaml": "pack: a-std\nversion: '1.0'\nextends: b-std@1.0\n",
        "packs/b-std/pack.yaml": "pack: b-std\nversion: '1.0'\nextends: a-std@1.0\n",
    })
    packs, problems = resolve(root)
    assert [p.name for p in packs] == ["a-std", "b-std"]
    assert any("STD-E003" in m for m in problems)


def test_env_search_path(monkeypatch):
    base = build({
        "proj/metamodel.yaml": "version: 1\nextends: corp-std@2.0\n",
        "central/corp-std/pack.yaml": PACK_CORP,
    })
    monkeypatch.setenv("SPECDB_PACK_PATH", str(base / "central"))
    packs, problems = resolve(base / "proj")
    assert problems == [] and [p.name for p in packs] == ["corp-std"]


# ---------- 文書カタログと from_standard マージ ----------

def catalog_fixture() -> Path:
    return build({
        "proj/metamodel.yaml": "version: 1\nextends: div-std@1.2\n",
        "proj/packs/div-std/pack.yaml": PACK_DIV,
        "proj/packs/corp-std/pack.yaml": PACK_CORP,
        # 全社: abstract な基本設計書 + そのまま使える table-spec
        "proj/packs/corp-std/documents/basic-design.yaml": (
            "abstract: true\n"
            "title: 基本設計書（{system_name}）\n"
            "output: 基本設計書_{system_name}.html\n"
            "template: basic-design.html.j2\n"
            "doc_no: { pattern: 'SD-[A-Z]{2,4}-\\d{3}' }\n"
            "params: { required: [system_name, doc_no, preface.purpose] }\n"),
        "proj/packs/corp-std/documents/table-spec.yaml": (
            "title: テーブル定義書\noutput: table-spec.md\ntemplate: table-spec.md.j2\n"),
        # 事業部: 全社の table-spec を上書き
        "proj/packs/div-std/documents/table-spec.yaml": (
            "title: テーブル定義書（事業部様式）\noutput: table-spec.md\n"
            "template: table-spec.md.j2\n"),
    }) / "proj"


def test_catalog_nearer_layer_wins():
    root = catalog_fixture()
    packs, _ = resolve(root)
    catalog = standard.document_catalog(packs)
    assert catalog["table-spec"][0]["title"] == "テーブル定義書（事業部様式）"
    assert catalog["table-spec"][1].name == "div-std"
    assert catalog["basic-design"][1].name == "corp-std"


def merge(root: Path, doc: dict):
    packs, _ = resolve(root)
    problems: list[Problem] = []
    merged = standard.merge_document(doc, standard.document_catalog(packs),
                                     problems, "documents/x")
    return merged, [str(p) for p in problems]


def test_merge_passthrough_without_from_standard():
    merged, problems = merge(catalog_fixture(), {"title": "独自文書", "output": "x.md",
                                                 "template": "x.md.j2"})
    assert merged["title"] == "独自文書" and problems == []


def test_merge_unknown_from_standard_is_error():
    merged, problems = merge(catalog_fixture(), {"from_standard": "nothing"})
    assert merged is None
    assert any("標準文書カタログに無い" in m for m in problems)


def test_merge_missing_params_is_std_e202():
    merged, problems = merge(catalog_fixture(),
                             {"from_standard": "basic-design", "doc_no": "SD-ORD-001"})
    assert merged is None
    assert any("STD-E202" in m and "system_name" in m for m in problems)
    assert any("STD-E202" in m and "preface.purpose" in m for m in problems)


def test_merge_doc_no_pattern_is_std_e203():
    merged, problems = merge(catalog_fixture(), {
        "from_standard": "basic-design", "system_name": "受発注",
        "doc_no": "設計書001", "preface": {"purpose": "目的"}})
    assert merged is None and any("STD-E203" in m for m in problems)


def test_merge_expands_params_into_title_and_output():
    merged, problems = merge(catalog_fixture(), {
        "from_standard": "basic-design", "system_name": "受発注",
        "doc_no": "SD-ORD-001", "preface": {"purpose": "目的"}})
    assert problems == []
    assert merged["title"] == "基本設計書（受発注）"
    assert merged["output"] == "基本設計書_受発注.html"
    assert "abstract" not in merged and "params" not in merged


def test_collect_documents_skips_abstract_until_instantiated():
    root = catalog_fixture()
    packs, _ = resolve(root)
    problems: list[Problem] = []
    docs = dict(standard.collect_documents(root, packs, problems))
    assert "basic-design" not in docs          # abstract は実体化されるまで対象外
    assert docs["table-spec"]["title"] == "テーブル定義書（事業部様式）"
    # プロジェクトで実体化すれば対象に入る
    write_tree(root, {"documents/basic-design.yaml": (
        "from_standard: basic-design\nsystem_name: 受発注\ndoc_no: SD-ORD-001\n"
        "preface: { purpose: 目的 }\n")})
    docs = dict(standard.collect_documents(root, packs, []))
    assert docs["basic-design"]["title"] == "基本設計書（受発注）"


# ---------- テンプレートの多層検索と上書き可視化 ----------

def template_fixture() -> Path:
    return build({
        "proj/metamodel.yaml": "version: 1\nextends: corp-std@2.0\n",
        "proj/packs/corp-std/pack.yaml": PACK_CORP,
        "proj/packs/corp-std/templates/_house.j2": "{% macro stamp() %}印{% endmacro %}",
        "proj/packs/corp-std/templates/doc.md.j2":
            "{% block title %}標準題名{% endblock %}\n{% block body %}標準本文{% endblock %}",
    }) / "proj"


def check_overrides(root: Path):
    packs, _ = resolve(root)
    problems: list[Problem] = []
    standard.check_template_overrides(root, packs, problems)
    return [str(p) for p in problems]


def test_house_style_override_is_std_w301():
    root = template_fixture()
    write_tree(root, {"templates/_house.j2": "上書き"})
    assert any("STD-W301" in m for m in check_overrides(root))


def test_full_replacement_is_std_w303():
    root = template_fixture()
    write_tree(root, {"templates/doc.md.j2": "全置換した本文"})
    assert any("STD-W303" in m for m in check_overrides(root))


def test_block_override_via_std_prefix_is_clean():
    root = template_fixture()
    write_tree(root, {"templates/doc.md.j2":
                      '{% extends "std/doc.md.j2" %}{% block title %}独自題名{% endblock %}'})
    assert check_overrides(root) == []
    # 実際に部分上書きとして描画されることまで確認する（PrefixLoader 経由）
    packs, _ = resolve(root)
    env = make_env(None, standard.template_search_dirs(root, packs),
                   standard.prefix_map(packs))
    text = env.get_template("doc.md.j2").render()
    assert "独自題名" in text and "標準本文" in text


def test_non_shadowing_template_is_clean():
    root = template_fixture()
    write_tree(root, {"templates/own.md.j2": "プロジェクト独自"})
    assert check_overrides(root) == []


# ---------- pack.lock の移植性（絶対パスを残さない・照合はパス非依存） ----------

MINI_PACK = "pack: mini\nversion: '1.0.0'\ndescription: 極小\n"
MINI_MM = "version: 1\n"


def test_lock_resolved_from_is_relative_never_absolute():
    """root 配下に無いパック（SPECDB_PACK_PATH 経由）でも resolved_from は
    絶対パスにならない（ドライブ名・ユーザ名を lock に残さない）。"""
    packs_dir = build({"mini/pack.yaml": MINI_PACK,
                       "mini/metamodel/core.yaml": MINI_MM})
    root = build({"metamodel.yaml": "version: 1\nextends: mini@1.0\n"})
    old = os.environ.get("SPECDB_PACK_PATH")
    os.environ["SPECDB_PACK_PATH"] = str(packs_dir)
    try:
        packs, problems = resolve(root)
        assert problems == [] and packs
        rf = standard.chain_lock(root, packs)["chain"][0]["resolved_from"]
        assert not os.path.isabs(rf), rf
        assert ":" not in rf, rf          # Windows のドライブ名が出ない
        assert not rf.startswith("/"), rf
    finally:
        if old is None:
            os.environ.pop("SPECDB_PACK_PATH", None)
        else:
            os.environ["SPECDB_PACK_PATH"] = old


def test_verify_lock_ignores_resolved_from():
    """resolved_from はレイアウトで変わる情報。version + 内容ハッシュが一致すれば
    frozen でも一致とみなす（別マシン/別レイアウトで conform が誤検知しない）。"""
    root = build({"metamodel.yaml": "version: 1\nextends: mini@1.0\n",
                  "packs/mini/pack.yaml": MINI_PACK,
                  "packs/mini/metamodel/core.yaml": MINI_MM})
    packs, problems = resolve(root)
    assert problems == []
    standard.write_lock(root, packs)
    lockp = root / "pack.lock"
    data = yaml.safe_load(lockp.read_text(encoding="utf-8"))
    data["chain"][0]["resolved_from"] = "C:/somewhere/else/packs/mini"  # 別マシン風
    lockp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    probs: list[Problem] = []
    standard.verify_lock(root, packs, probs, frozen=True)
    assert probs == []


def test_verify_lock_flags_version_or_hash_mismatch():
    """同一性（版・内容ハッシュ）が食い違えば frozen で error を出す。"""
    root = build({"metamodel.yaml": "version: 1\nextends: mini@1.0\n",
                  "packs/mini/pack.yaml": MINI_PACK,
                  "packs/mini/metamodel/core.yaml": MINI_MM})
    packs, problems = resolve(root)
    standard.write_lock(root, packs)
    lockp = root / "pack.lock"
    data = yaml.safe_load(lockp.read_text(encoding="utf-8"))
    data["chain"][0]["content_hash"] = "sha256:deadbeef"
    lockp.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    probs: list[Problem] = []
    standard.verify_lock(root, packs, probs, frozen=True)
    assert any(p.level == "error" and "STD-W003" in p.message for p in probs)
