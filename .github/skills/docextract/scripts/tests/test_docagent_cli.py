"""docagent CLI の標準出力量を抑える仕組みを検証する (end-to-end に main を叩く)。

エージェントに渡す stdout がコーパス/文書サイズに比例して膨らまないよう、
text の既定上限・ページング、list のスリム射影、--json のコンパクト化、
export の全出力ガード、facts の evidence 短縮を確認する。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from docagent import cli

# 既存ユニットテストの result.json フィクスチャ生成を流用する。
from test_docagent import make_result


@pytest.fixture
def store(tmp_path, monkeypatch):
    """一時ストア。監査ログも tmp に隔離する。共通オプションを添えた main ラッパを返す。"""
    monkeypatch.setenv("DOCEXTRACT_HOME", str(tmp_path / "home"))
    store_path = tmp_path / "store" / "library.json"
    facts_path = tmp_path / "store" / "facts.json"
    doctypes = tmp_path / "store" / "doctypes.json"
    item_types = tmp_path / "store" / "item_types.json"
    common = [
        "--store", str(store_path),
        "--doctypes", str(doctypes),
        "--facts", str(facts_path),
        "--item-types-file", str(item_types),
    ]

    def run(*argv: str) -> int:
        return cli.main([*argv, *common])

    run("init")
    run._common = common  # type: ignore[attr-defined]
    run._root = tmp_path  # type: ignore[attr-defined]
    return run


def _add_doc(run, tmp_path, name: str, texts) -> str:
    rp = tmp_path / f"{name}_result.json"
    rp.write_text(json.dumps(make_result(name, texts=texts), ensure_ascii=False), encoding="utf-8")
    run("add", str(rp))
    return Path(name).stem + "_" + Path(name).suffix.lstrip(".").lower()


# ── ① text: 既定上限・ページング・全文オプト ───────────────────────
def test_text_default_caps_and_paginates(store, tmp_path, capsys):
    doc_id = _add_doc(store, tmp_path, "big.docx", ["あ" * 500])
    capsys.readouterr()  # add の出力を捨てる

    store("text", doc_id, "--max-chars", "100", "--json")
    rec = json.loads(capsys.readouterr().out.strip())
    assert rec["returned_chars"] == 100
    assert rec["truncated"] is True
    assert rec["next_offset"] == 100
    assert rec["total_chars"] == 500

    # 続きを next_offset から読む
    store("text", doc_id, "--offset", "100", "--max-chars", "100", "--json")
    rec2 = json.loads(capsys.readouterr().out.strip())
    assert rec2["offset"] == 100
    assert rec2["returned_chars"] == 100

    # --max-chars 0 は全文
    store("text", doc_id, "--max-chars", "0", "--json")
    rec3 = json.loads(capsys.readouterr().out.strip())
    assert rec3["truncated"] is False
    assert rec3["returned_chars"] == 500


def test_text_human_reports_truncation_on_stderr(store, tmp_path, capsys):
    doc_id = _add_doc(store, tmp_path, "big.docx", ["b" * 300])
    capsys.readouterr()
    store("text", doc_id, "--max-chars", "50")
    captured = capsys.readouterr()
    assert "b" * 50 in captured.out
    assert "--offset" in captured.err  # 続きの読み方を案内


# ── ② list: 既定スリム / --full で完全 dict ─────────────────────────
def test_list_json_is_slim_by_default(store, tmp_path, capsys):
    _add_doc(store, tmp_path, "a.docx", ["x" * 400])
    capsys.readouterr()
    store("list", "--json")
    docs = json.loads(capsys.readouterr().out.strip())
    assert len(docs) == 1
    d = docs[0]
    assert set(d) == {"id", "source", "file_type", "doctype", "stats", "result_path", "preview"}
    # かさむフィールドは落ちる（要素数 stats・出力先 result_path は分類/報告用に残す）
    assert "metadata" not in d and "content_hash" not in d and "source_abspath" not in d
    assert len(d["preview"]) <= 201  # 200 字 + 省略記号


def test_list_full_includes_complete_dict(store, tmp_path, capsys):
    _add_doc(store, tmp_path, "a.docx", ["x" * 50])
    capsys.readouterr()
    store("list", "--full", "--json")
    docs = json.loads(capsys.readouterr().out.strip())
    assert "result_path" in docs[0] and "metadata" in docs[0]


# ── ③ --json はコンパクト既定 / --pretty で整形 ──────────────────────
def test_json_is_compact_by_default(store, tmp_path, capsys):
    _add_doc(store, tmp_path, "a.docx", ["hi"])
    capsys.readouterr()
    store("list", "--json")
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # 末尾改行のみ = 1 行
    assert "\n  " not in out  # インデントされていない


def test_pretty_indents_json(store, tmp_path, capsys):
    _add_doc(store, tmp_path, "a.docx", ["hi"])
    capsys.readouterr()
    store("list", "--json", "--pretty")
    out = capsys.readouterr().out
    assert "\n  " in out  # indent=2 で整形される


# ── ④ export: 非対話で -o 省略時は全出力を拒否 ─────────────────────
def test_export_refuses_full_dump_when_noninteractive(store, tmp_path):
    _add_doc(store, tmp_path, "a.docx", ["hi"])
    with pytest.raises(SystemExit) as exc:
        store("export")  # capsys 下では stdout は非 tty
    assert exc.value.code == 2


def test_export_to_file_ok(store, tmp_path, capsys):
    _add_doc(store, tmp_path, "a.docx", ["hi"])
    out_file = tmp_path / "dump.json"
    store("export", "-o", str(out_file))
    assert out_file.exists()
    assert "書き出しました" in capsys.readouterr().out


def test_export_stdout_flag_forces_dump(store, tmp_path, capsys):
    _add_doc(store, tmp_path, "a.docx", ["hi"])
    capsys.readouterr()
    store("export", "--stdout")
    data = json.loads(capsys.readouterr().out.strip())
    assert "documents" in data


# ── ⑤ facts: evidence は既定短縮 / --full で全文 ───────────────────
def test_facts_evidence_trimmed_by_default(store, tmp_path, capsys):
    doc_id = _add_doc(store, tmp_path, "a.docx", ["hi"])
    store("item-types", "add", "機能要件")
    long_ev = "根" * 500
    store(
        "fact-add", "--doc", doc_id, "--type", "機能要件",
        "--statement", "s", "--evidence", long_ev,
    )
    capsys.readouterr()
    store("facts", "--json")
    items = json.loads(capsys.readouterr().out.strip())
    assert items[0]["evidence_truncated"] is True
    assert len(items[0]["evidence"]) <= 201

    store("facts", "--full", "--json")
    full = json.loads(capsys.readouterr().out.strip())
    assert full[0]["evidence"] == long_ev
    assert "evidence_truncated" not in full[0]


def test_fact_add_refs_via_flag_and_json(store, tmp_path, capsys):
    doc_id = _add_doc(store, tmp_path, "a.docx", ["hi"])
    store("item-types", "add", "メソッド")
    capsys.readouterr()
    # --ref を繰り返し + |note、および --refs JSON を併用
    store(
        "fact-add", "--doc", doc_id, "--type", "メソッド", "--statement", "register()",
        "--ref", "realizes=F-02", "--ref", "refines=SCR-03|画面遷移元",
        "--refs", '[{"rel":"has-method","to_ref":"予約Service"}]',
        "--json",
    )
    item = json.loads(capsys.readouterr().out.strip())
    assert item["refs"] == [
        {"rel": "has-method", "to_ref": "予約Service"},
        {"rel": "realizes", "to_ref": "F-02"},
        {"rel": "refines", "to_ref": "SCR-03", "note": "画面遷移元"},
    ]
    # refs は検索対象 (facts --text F-02 で拾える)
    store("facts", "--text", "F-02", "--json")
    assert len(json.loads(capsys.readouterr().out.strip())) == 1


def test_fact_add_ref_bad_format_rejected(store, tmp_path, capsys):
    doc_id = _add_doc(store, tmp_path, "a.docx", ["hi"])
    store("item-types", "add", "メソッド")
    capsys.readouterr()
    # rel=to_ref 形式でない --ref は非0で拒否
    rc = store(
        "fact-add", "--doc", doc_id, "--type", "メソッド", "--statement", "s",
        "--ref", "F-02",
    )
    assert rc != 0


def test_rel_types_lists_defaults(store, capsys):
    capsys.readouterr()
    store("rel-types", "--json")
    rels = json.loads(capsys.readouterr().out.strip())
    assert "realizes" in rels and "refines" in rels


def test_facts_merge_consolidates_shards(store, tmp_path, capsys):
    doc_id = _add_doc(store, tmp_path, "a.docx", ["hi"])
    store("item-types", "add", "機能要件")
    # 2 つのシャードに並列抽出したていで書き分ける（--facts で保存先を分離）
    shard_a = tmp_path / "facts.a.json"
    shard_b = tmp_path / "facts.b.json"
    common = store._common  # type: ignore[attr-defined]
    it = common[common.index("--item-types-file") + 1]
    rt_flag = ["--rel-types-file", str(tmp_path / "store" / "rel_types.json")]
    cli.main(["fact-add", "--doc", doc_id, "--type", "機能要件", "--statement", "A",
              "--ref", "realizes=F-01", "--facts", str(shard_a),
              "--item-types-file", it, *rt_flag])
    cli.main(["fact-add", "--doc", doc_id, "--type", "機能要件", "--statement", "B",
              "--facts", str(shard_b), "--item-types-file", it, *rt_flag])
    capsys.readouterr()
    # 主ストアへ統合
    rc = store("facts-merge", str(shard_a), str(shard_b), "--json")
    assert rc == 0
    result = json.loads(capsys.readouterr().out.strip())
    assert result["added"] == 2
    store("facts", "--json")
    items = json.loads(capsys.readouterr().out.strip())
    assert {it["id"] for it in items} == {"f0001", "f0002"}   # 振り直し済み
    a = next(i for i in items if i["statement"] == "A")
    assert a["refs"] == [{"rel": "realizes", "to_ref": "F-01"}]


# ── ⑥ 数値ガード: --json 出力が上限を超えたら拒否し、絞り方を案内 ─────────
def test_text_full_refused_over_ceiling(store, tmp_path, capsys):
    # 既定上限 (30,000 字) を超える文書の全文 (--max-chars 0) は拒否される。
    doc_id = _add_doc(store, tmp_path, "huge.docx", ["あ" * 40000])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        store("text", doc_id, "--max-chars", "0", "--json")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "上限" in err and "--max-chars" in err  # 絞り方を案内する


def test_stdout_flag_bypasses_guard(store, tmp_path, capsys):
    doc_id = _add_doc(store, tmp_path, "huge.docx", ["あ" * 40000])
    capsys.readouterr()
    store("text", doc_id, "--max-chars", "0", "--json", "--stdout")
    rec = json.loads(capsys.readouterr().out.strip())
    assert rec["returned_chars"] == 40000  # ガードを迂回して全出力


# ── ⑥b list/query/facts: 大量件数でも -o / --limit でデータを取り出せる ─────
def _add_many(store, tmp_path, n: int, chars: int = 500) -> None:
    for i in range(n):
        _add_doc(store, tmp_path, f"doc{i:03}.docx", ["あ" * chars])


def test_list_over_ceiling_refused_without_escape(store, tmp_path, capsys):
    # 大量文書の list --json は従来どおり数値ガードで拒否される (回帰防止)。
    _add_many(store, tmp_path, 80)
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        store("list", "--json")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "上限" in err and "-o" in err  # 書き出しの逃げ道を案内する


def test_list_output_file_bypasses_guard(store, tmp_path, capsys):
    # -o <file> なら上限超過でもファイルへ全件書き出せる。
    _add_many(store, tmp_path, 80)
    out_file = tmp_path / "docs.json"
    capsys.readouterr()
    store("list", "--json", "-o", str(out_file))
    assert "書き出しました" in capsys.readouterr().out
    docs = json.loads(out_file.read_text(encoding="utf-8"))
    assert len(docs) == 80
    assert {d["id"] for d in docs}  # ID を機械可読に取得できる


def test_list_limit_offset_paginates(store, tmp_path, capsys):
    _add_many(store, tmp_path, 80)
    capsys.readouterr()
    store("list", "--json", "--limit", "10")
    captured = capsys.readouterr()
    page = json.loads(captured.out.strip())
    assert len(page) == 10
    assert "--offset 10" in captured.err  # 続きの読み方を案内

    store("list", "--json", "--limit", "10", "--offset", "75")
    captured = capsys.readouterr()
    tail = json.loads(captured.out.strip())
    assert len(tail) == 5  # 末尾は残り 5 件
    assert "--offset" not in captured.err  # 続きなし


def test_query_output_file_bypasses_guard(store, tmp_path, capsys):
    _add_many(store, tmp_path, 80)
    out_file = tmp_path / "q.json"
    capsys.readouterr()
    store("query", "--json", "-o", str(out_file))
    assert out_file.exists()
    assert len(json.loads(out_file.read_text(encoding="utf-8"))) == 80


def test_facts_limit_offset_paginates(store, tmp_path, capsys):
    doc_id = _add_doc(store, tmp_path, "a.docx", ["hi"])
    store("item-types", "add", "機能要件")
    for i in range(12):
        store("fact-add", "--doc", doc_id, "--type", "機能要件",
              "--statement", f"s{i}", "--evidence", "e")
    capsys.readouterr()
    store("facts", "--json", "--limit", "5")
    captured = capsys.readouterr()
    assert len(json.loads(captured.out.strip())) == 5
    assert "--offset 5" in captured.err


# ── ⑥c facts-pending: まだファクトが1件も無い文書を洗い出す ─────────────
def test_facts_pending_lists_docs_without_facts(store, tmp_path, capsys):
    with_facts = _add_doc(store, tmp_path, "a.docx", ["hi"])
    without = _add_doc(store, tmp_path, "b.docx", ["yo"])
    store("item-types", "add", "機能要件")
    store("fact-add", "--doc", with_facts, "--type", "機能要件", "--statement", "s")
    capsys.readouterr()

    store("facts-pending", "--json")
    docs = json.loads(capsys.readouterr().out.strip())
    ids = {d["id"] for d in docs}
    assert ids == {without}  # ファクトのある文書は除外される

    # ファクトを付けたら未抽出リストから消える
    store("fact-add", "--doc", without, "--type", "機能要件", "--statement", "t")
    capsys.readouterr()
    store("facts-pending", "--json")
    assert json.loads(capsys.readouterr().out.strip()) == []


def test_facts_pending_filters_by_doctype(store, tmp_path, capsys):
    a = _add_doc(store, tmp_path, "a.docx", ["hi"])
    b = _add_doc(store, tmp_path, "b.docx", ["yo"])
    store("set-doctype", a, "要件定義")
    store("set-doctype", b, "議事録")
    capsys.readouterr()

    store("facts-pending", "--doctype", "要件定義", "--json")
    docs = json.loads(capsys.readouterr().out.strip())
    assert {d["id"] for d in docs} == {a}  # 指定種別かつファクト無しのみ


def _write_config(tmp_path: Path, **values) -> Path:
    cfg = tmp_path / "myconfig.json"
    cfg.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
    return cfg


# ── ⑦ config.json: 上限と既定値を利用者が変更できる ──────────────────
def test_config_file_lowers_ceiling(store, tmp_path, capsys):
    # ceiling_chars を極小にすると、小さな list 出力でも拒否される。
    cfg = _write_config(tmp_path, ceiling_chars=50)
    _add_doc(store, tmp_path, "a.docx", ["x" * 400])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        store("--config", str(cfg), "list", "--json")
    assert exc.value.code == 2
    assert "ceiling_chars" in capsys.readouterr().err  # 変更方法を案内


def test_config_ceiling_zero_disables_guard(store, tmp_path, capsys):
    cfg = _write_config(tmp_path, ceiling_chars=0)
    _add_doc(store, tmp_path, "huge.docx", ["あ" * 40000])
    capsys.readouterr()
    store("--config", str(cfg), "text", "huge_docx", "--max-chars", "0", "--json")
    rec = json.loads(capsys.readouterr().out.strip())
    assert rec["returned_chars"] == 40000  # 0 で無効化


def test_config_overrides_text_default(store, tmp_path, capsys):
    # text_max_chars を config で下げると、--max-chars 未指定の既定が変わる。
    cfg = _write_config(tmp_path, text_max_chars=10)
    doc_id = _add_doc(store, tmp_path, "a.docx", ["b" * 100])
    capsys.readouterr()
    store("--config", str(cfg), "text", doc_id, "--json")
    rec = json.loads(capsys.readouterr().out.strip())
    assert rec["returned_chars"] == 10
    assert rec["truncated"] is True


def test_explicit_flag_beats_config(store, tmp_path, capsys):
    # 明示フラグは config より優先される。
    cfg = _write_config(tmp_path, text_max_chars=10)
    doc_id = _add_doc(store, tmp_path, "a.docx", ["b" * 100])
    capsys.readouterr()
    store("--config", str(cfg), "text", doc_id, "--max-chars", "25", "--json")
    rec = json.loads(capsys.readouterr().out.strip())
    assert rec["returned_chars"] == 25


def test_init_writes_config_defaults(store, tmp_path):
    # fixture の init 時に <home>/config.json が既定値で作られている。
    cfg_path = tmp_path / "home" / "config.json"
    assert cfg_path.exists()
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert data["ceiling_chars"] == 30000
    assert data["text_max_chars"] == 20000


def test_invalid_config_falls_back_to_defaults(store, tmp_path, capsys):
    # 不正値 (負数) は既定にフォールバックし、理由を stderr に出す。
    cfg = _write_config(tmp_path, ceiling_chars=-5)
    _add_doc(store, tmp_path, "a.docx", ["hi"])
    capsys.readouterr()
    store("--config", str(cfg), "list", "--json")
    captured = capsys.readouterr()
    assert "ceiling_chars" in captured.err  # 不正値の警告
    json.loads(captured.out.strip())  # 既定 30000 で通る
