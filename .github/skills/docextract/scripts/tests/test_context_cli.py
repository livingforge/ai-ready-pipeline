"""ブロック抽出プロトコル (context-set / get / send / check) を end-to-end に検証する。

サブエージェントの入出力を 2 コマンドに固定する設計の要点を押さえる:
ブロックの結合・分割 (シート/ページ最小単位・上限・文境界)、オーケストレータ
割り当て型の払い出し (pending→claimed→done)、location の server-side 付与、
再送の冪等性、facts-merge 前のバリア (context-check の終了コード)。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from docagent import cli
from docagent.context import split_text


def make_sheet_result(source: str, sheets: dict[str, str], abspath: str) -> dict:
    """シート単位の location を持つ xlsx 風 result.json フィクスチャ。"""
    elements = [
        {"type": "text", "content": text, "location": {"sheet": name}}
        for name, text in sheets.items()
    ]
    return {
        "id": Path(source).stem + "_xlsx",
        "source": source,
        "source_abspath": abspath,
        "content_hash": "0" * 64,
        "file_type": "xlsx",
        "metadata": {},
        "summary": {"text": len(elements)},
        "elements": elements,
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    """一時ストア。context.json も DOCEXTRACT_HOME 配下に隔離される。"""
    monkeypatch.setenv("DOCEXTRACT_HOME", str(tmp_path / "home"))
    sd = tmp_path / "store"
    common = [
        "--store", str(sd / "library.json"),
        "--doctypes", str(sd / "doctypes.json"),
        "--facts", str(sd / "facts.json"),
        "--item-types-file", str(sd / "item_types.json"),
        "--rel-types-file", str(sd / "rel_types.json"),
        "--context", str(sd / "context.json"),
    ]

    def run(*argv: str) -> int:
        return cli.main([*argv, *common])

    run("init")
    run._root = tmp_path  # type: ignore[attr-defined]
    return run


def _register(run, tmp_path, source: str, sheets: dict[str, str]) -> str:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(exist_ok=True)
    result = make_sheet_result(source, sheets, str(docs_dir / source))
    rp = tmp_path / f"{Path(source).stem}_result.json"
    rp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    run("add", str(rp))
    return result["id"]


def _out_json(capsys):
    return json.loads(capsys.readouterr().out)


# ── split_text: 文境界を優先した分割 ─────────────────────────────
def test_split_text_prefers_sentence_breaks():
    text = ("あ" * 40 + "。") + ("い" * 40 + "。") + ("う" * 10)
    parts = split_text(text, 60)
    assert parts[0] == "あ" * 40 + "。"  # 上限 60 の中の最後の文境界で切る
    assert "".join(parts) == text  # 欠落なし
    assert all(len(p) <= 60 for p in parts)


def test_split_text_hard_cut_without_breaks():
    text = "x" * 130
    parts = split_text(text, 50)
    assert "".join(parts) == text
    assert all(len(p) <= 50 for p in parts)


# ── context-set: 結合・分割・選定 ────────────────────────────────
def test_set_merges_sheets_under_limit(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "demo.xlsx", {"画面": "あ" * 30, "帳票": "い" * 30})
    capsys.readouterr()
    assert store("context-set", "--docs", doc_id, "--json") == 0
    out = _out_json(capsys)
    assert [b["id"] for b in out["blocks"]] == [f"{doc_id}.b01"]
    assert out["blocks"][0]["units"] == ["sheet=画面", "sheet=帳票"]


def test_set_splits_oversized_sheet_with_part_location(store, tmp_path, capsys):
    long_text = ("要件です。" * 30)[:-1]  # 149 字・文境界あり
    doc_id = _register(store, tmp_path, "big.xlsx", {"仕様": long_text})
    capsys.readouterr()
    assert store("context-set", "--docs", doc_id, "--max-chars", "60", "--json") == 0
    out = _out_json(capsys)
    assert len(out["blocks"]) >= 3
    # 分割ブロックの location には sheet と part/parts が入る
    assert store("context-get", "--id", out["blocks"][0]["id"], "--json") == 0
    got = _out_json(capsys)
    assert got["location"]["sheet"] == "仕様"
    assert got["location"]["part"] == 1
    assert got["location"]["parts"] == len(out["blocks"])
    assert got["text"].endswith("。")  # 文境界で切れている


def test_set_selects_by_file_name_and_folder(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "sel.xlsx", {"s": "内容です。"})
    capsys.readouterr()
    assert store("context-set", "--files", "sel.xlsx", "--json") == 0
    assert _out_json(capsys)["docs"] == 1
    # フォルダ指定 (登録時の source_abspath の親) でも選べる
    assert store("context-set", "--folder", str(tmp_path / "docs"), "--force", "--json") == 0
    assert [b["doc_id"] for b in _out_json(capsys)["blocks"]] == [doc_id]


def test_set_errors_on_no_match_and_incomplete_queue(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "q.xlsx", {"s": "内容です。"})
    capsys.readouterr()
    # 一致なし → エラー
    assert store("context-set", "--files", "存在しない.xlsx", "--json") == 1
    assert "選べませんでした" in capsys.readouterr().err
    # 未完キューが残っていると --force なしでは作り直せない
    assert store("context-set", "--docs", doc_id, "--json") == 0
    capsys.readouterr()
    assert store("context-set", "--docs", doc_id, "--json") == 1
    assert "未完" in capsys.readouterr().err
    assert store("context-set", "--docs", doc_id, "--force", "--json") == 0


# ── context-get: 払い出しとエラー分岐 ────────────────────────────
def test_get_requires_context_set_first(store, capsys):
    assert store("context-get", "--json") == 1
    assert "context-set" in capsys.readouterr().err  # 次の一手を案内する


def test_get_returns_text_vocab_and_claims(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "v.xlsx", {"項目": "顧客コードは8桁。"})
    store("context-set", "--docs", doc_id, "--json")
    capsys.readouterr()
    assert store("context-get", "--id", f"{doc_id}.b01", "--json") == 0
    out = _out_json(capsys)
    assert "顧客コード" in out["text"]
    assert "機能要件" in out["item_types"]  # 語彙同梱で追加コール不要
    assert "realizes" in out["rel_types"]
    # claimed の再取得は許す (再開の冪等性)。unknown / done は拒否
    assert store("context-get", "--id", f"{doc_id}.b01", "--json") == 0
    capsys.readouterr()
    assert store("context-get", "--id", "no.such.b99", "--json") == 1
    capsys.readouterr()
    store("context-send", "--id", f"{doc_id}.b01", "--result", "[]", "--json")
    capsys.readouterr()
    assert store("context-get", "--id", f"{doc_id}.b01", "--json") == 1
    assert "処理済み" in capsys.readouterr().err
    # 全ブロック done で引数なし get もエラー (すべて処理された合図)
    assert store("context-get", "--json") == 1
    assert "処理済み" in capsys.readouterr().err


# ── context-send: server-side 付与・部分拒否・冪等 ───────────────
def test_send_attaches_location_rejects_invalid_and_is_idempotent(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "s.xlsx", {"処理": "F-02 を実現する register()。"})
    store("context-set", "--docs", doc_id, "--json")
    capsys.readouterr()
    items = [
        {"type": "メソッド", "statement": "register() は予約を登録する",
         "refs": [{"rel": "realizes", "to_ref": "F-02"}]},
        {"type": "語彙にない種別", "statement": "却下される"},
    ]
    assert store("context-send", "--id", f"{doc_id}.b01",
                 "--result", json.dumps(items, ensure_ascii=False), "--json") == 0
    out = _out_json(capsys)
    assert out["added"] == 1 and len(out["rejected"]) == 1  # 部分拒否で全体は止めない
    shard = json.loads(Path(out["shard"]).read_text(encoding="utf-8"))
    fact = shard["items"][0]
    assert fact["location"] == {"sheet": "処理"}  # ブロック定義から自動付与
    assert fact["evidence"] is None  # evidence は設計上持たない
    assert fact["refs"] == [{"rel": "realizes", "to_ref": "F-02"}]
    # 再送はシャード全量の置き換え (追記ではない)
    assert store("context-send", "--id", f"{doc_id}.b01",
                 "--result", json.dumps(items[:1], ensure_ascii=False), "--json") == 0
    out2 = _out_json(capsys)
    shard2 = json.loads(Path(out2["shard"]).read_text(encoding="utf-8"))
    assert len(shard2["items"]) == 1


def test_send_result_accepts_file_reference(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "f.xlsx", {"s": "本文です。"})
    store("context-set", "--docs", doc_id, "--json")
    payload = tmp_path / "result.json"
    payload.write_text(
        json.dumps([{"type": "用語", "statement": "本文: テスト用の文"}], ensure_ascii=False),
        encoding="utf-8",
    )
    capsys.readouterr()
    assert store("context-send", "--id", f"{doc_id}.b01",
                 "--result", f"@{payload}", "--json") == 0
    assert _out_json(capsys)["added"] == 1


def test_send_rejects_non_array_result(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "e.xlsx", {"s": "本文です。"})
    store("context-set", "--docs", doc_id, "--json")
    capsys.readouterr()
    assert store("context-send", "--id", f"{doc_id}.b01", "--result", "{}", "--json") == 1
    assert store("context-send", "--id", f"{doc_id}.b01", "--result", "壊れたJSON", "--json") == 1


# ── context-check + facts-merge: バリアと統合 ────────────────────
def test_check_barrier_and_merge_integration(store, tmp_path, capsys):
    doc_id = _register(
        store, tmp_path, "m.xlsx",
        {"a": "あ" * 40, "b": "い" * 40},
    )
    store("context-set", "--docs", doc_id, "--max-chars", "50", "--json")
    capsys.readouterr()
    # 未完のうちは非ゼロ終了 (オーケストレータのバリアに使う)
    assert store("context-check", "--json") == 3
    state = _out_json(capsys)
    assert state["complete"] is False and state["total"] == 2
    # 全ブロックを処理すると 0 になり、統合対象のシャードが列挙される
    for b in state["incomplete"]:
        store("context-send", "--id", b["id"], "--result",
              json.dumps([{"type": "用語", "statement": f"{b['id']} の項目"}],
                         ensure_ascii=False), "--json")
    capsys.readouterr()
    assert store("context-check", "--json") == 0
    state = _out_json(capsys)
    assert state["complete"] is True and len(state["shards"]) == 2
    # check が列挙したシャードをそのまま facts-merge へ
    assert store("facts-merge", *state["shards"], "--json") == 0
    assert _out_json(capsys)["added"] == 2
    assert store("facts", "--json") == 0
    facts = _out_json(capsys)
    assert {f["location"]["sheet"] for f in facts} == {"a", "b"}
