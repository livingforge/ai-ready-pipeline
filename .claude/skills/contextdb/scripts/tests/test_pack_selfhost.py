# -*- coding: utf-8 -*-
"""パック自己正本化（§3.1）— jp-sier-std の配布物 config が正本 contextdb から
生成した結果と一致することを固定する。

正本 = contextdb/packs-src/jp-sier-std（doc-type / conformance-rule / style-part）。
配布物 = contextdb/packs/jp-sier-std/{documents,conformance}（生成ビュー）。
pack build を temp へ流し、配布物と data-equal であることを検証する（no-drift）。
"""
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pack as packmod  # noqa: E402
from engine import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AUTHORING = ROOT / "packs-src" / "jp-sier-std"
DIST = ROOT / "packs" / "jp-sier-std"

# パック正本 (packs-src) が無い環境（万一同梱されていない配布等）ではスキップ。
pytestmark = pytest.mark.skipif(
    not AUTHORING.is_dir(), reason="pack authoring source (packs-src) が無い")


def _authoring_copy() -> Path:
    """正本を temp へ複製して返す（generate が out/ を書くため、追跡ツリーを汚さない）。"""
    tmp = Path(tempfile.mkdtemp(prefix="pack-authoring-"))
    dst = tmp / "jp-sier-std"
    shutil.copytree(AUTHORING, dst, ignore=shutil.ignore_patterns("out", "__pycache__"))
    return dst


def test_authoring_contextdb_is_valid():
    store = Store.load(AUTHORING)
    assert not store.has_errors(), [str(p) for p in store.problems]
    # list / map kind の属性が読めている
    dt = store.items["dt-basic-design"]
    assert isinstance(dt.attrs["required_params"], list)
    assert isinstance(dt.attrs["doc_no"], dict)


def test_build_reproduces_committed_dist():
    tmp = Path(tempfile.mkdtemp(prefix="pack-build-"))
    into = tmp / "dist"
    into.mkdir()
    rc = packmod._cmd_build(_authoring_copy(), into)
    assert rc == 0
    targets = ["documents/requirement-spec.yaml", "documents/basic-design.yaml",
               "documents/detail-design.yaml", "documents/test-spec.yaml",
               "documents/test-result.yaml", "documents/traceability-matrix.yaml",
               "conformance/rules.yaml"]
    for name in targets:
        built = yaml.safe_load((into / name).read_text(encoding="utf-8"))
        committed = yaml.safe_load((DIST / name).read_text(encoding="utf-8"))
        assert built == committed, f"{name}: 生成と配布物が不一致"


def test_committed_dist_matches_authoring_no_drift():
    """配布物が正本から乖離していない（正本を直さず dist だけ手編集した等を検出）。"""
    store = Store.load(AUTHORING)
    doc_types = {i.attrs["name"] for i in store.items_of("doc-type")}
    committed_docs = {p.stem for p in (DIST / "documents").glob("*.yaml")}
    assert doc_types == committed_docs
