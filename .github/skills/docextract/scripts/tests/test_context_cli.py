"""ブロック抽出プロトコル (context-set / get / send / check) を end-to-end に検証する。

サブエージェントの入出力を 2 コマンドに固定する設計の要点を押さえる:
ブロックの結合・分割 (シート/ページ最小単位・上限・文境界)、オーケストレータ
割り当て型の払い出し (pending→claimed→done)、location の server-side 付与、
再送の冪等性、facts-merge 前のバリア (context-check の終了コード)。
"""

from __future__ import annotations

import json
import os
import subprocess
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


def test_set_excludes_test_and_entrypoint_doctypes_by_default(store, tmp_path, capsys):
    """種別テスト/エントリポイントは既定でキューから除外し、除外を必ず報告する。"""
    src_id = _register(store, tmp_path, "app.xlsx", {"s": "本文です。"})
    tst_id = _register(store, tmp_path, "spec.xlsx", {"s": "テスト内容です。"})
    store("set-doctype", tst_id, "テスト")
    capsys.readouterr()
    assert store("context-set", "--docs", src_id, tst_id, "--json") == 0
    out = _out_json(capsys)
    assert out["docs"] == 1
    assert [b["doc_id"] for b in out["blocks"]] == [src_id]
    assert [(d["id"], d["doctype"]) for d in out["excluded"]] == [(tst_id, "テスト")]
    # 全対象が除外種別 → エラーで次の一手 (--include-doctype) を案内する
    assert store("context-set", "--docs", tst_id, "--force", "--json") == 1
    assert "--include-doctype" in capsys.readouterr().err
    # --include-doctype で明示的に含められる
    assert store("context-set", "--docs", tst_id,
                 "--include-doctype", "テスト", "--force", "--json") == 0
    out = _out_json(capsys)
    assert out["docs"] == 1 and out["excluded"] == []


# ── context-get: 払い出しとエラー分岐 ────────────────────────────
def test_get_requires_context_set_first(store, capsys):
    assert store("context-get", "--json") == 1
    assert "context-set" in capsys.readouterr().err  # 次の一手を案内する


def test_argless_get_dispenses_distinct_blocks_atomically(store, tmp_path, capsys):
    # 自己サーブ払い出し: 引数なしの get が次の pending を自動獲得し、
    # 連続して呼んでも同じブロックを二重に払い出さない。
    doc_id = _register(store, tmp_path, "p.xlsx", {"a": "あ" * 40, "b": "い" * 40})
    store("context-set", "--docs", doc_id, "--max-chars", "50", "--json")
    capsys.readouterr()
    assert store("context-get", "--json") == 0
    first = _out_json(capsys)
    assert store("context-get", "--json") == 0
    second = _out_json(capsys)
    assert first["id"] != second["id"]  # アトミッククレームで別ブロック
    # クレームはファイルの排他作成で表現される (context.json は書き換えない)
    claims = sorted(p.name for p in (tmp_path / "store" / "claims").glob("*.claim"))
    assert claims == [f"{first['id']}.claim", f"{second['id']}.claim"]
    # 全ブロック claimed → 引数なし get は「引き継ぎは --id」と案内して失敗する
    assert store("context-get", "--json") == 1
    assert "--id" in capsys.readouterr().err


def test_claimed_block_is_skipped_by_argless_get(store, tmp_path, capsys):
    # 別プロセスが claim 済み (= claim ファイルが先に存在する) のブロックは飛ばす。
    doc_id = _register(store, tmp_path, "r.xlsx", {"a": "あ" * 40, "b": "い" * 40})
    store("context-set", "--docs", doc_id, "--max-chars", "50", "--json")
    claim = tmp_path / "store" / "claims" / f"{doc_id}.b01.claim"
    claim.parent.mkdir(parents=True, exist_ok=True)
    claim.write_text("other-process\n", encoding="utf-8")
    capsys.readouterr()
    assert store("context-get", "--json") == 0
    assert _out_json(capsys)["id"] == f"{doc_id}.b02"


def test_force_rebuild_resets_progress(store, tmp_path, capsys):
    # 進捗はファイル (claim/シャード) から導出するため、--force の作り直しは
    # 前回の痕跡を消して全ブロックを pending に戻す。
    doc_id = _register(store, tmp_path, "w.xlsx", {"s": "本文です。"})
    store("context-set", "--docs", doc_id, "--json")
    store("context-get", "--json")
    store("context-send", "--id", f"{doc_id}.b01", "--result",
          json.dumps([{"type": "用語", "statement": "語: 定義"}], ensure_ascii=False),
          "--json")
    capsys.readouterr()
    assert store("context-check", "--json") == 0  # いったん complete
    capsys.readouterr()
    assert store("context-set", "--docs", doc_id, "--force", "--json") == 0
    capsys.readouterr()
    assert store("context-check", "--json") == 3  # pending に戻っている
    state = _out_json(capsys)
    assert state["by_status"]["pending"] == state["total"]


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


# ── 封筒形式: ペイロードの出所を --id と突き合わせる ─────────────
# 並列サブエージェントが共有スクラッチパッドに同名の中間ファイルを書くと、他の
# エージェントのペイロードを自分のブロックとして送る取り違えが起きる。send() は
# doc_id/location をブロック定義から付与するため取り違えても形式的には正常な
# データになり、context-check も「完了」しか見ないので誰も気づけない。
_ENVELOPE_ITEMS = [{"type": "用語", "statement": "封筒: 出所つきペイロード"}]


def test_send_envelope_matching_block_id_is_accepted(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "env.xlsx", {"s": "本文です。"})
    store("context-set", "--docs", doc_id, "--json")
    capsys.readouterr()
    block_id = f"{doc_id}.b01"
    envelope = {"block_id": block_id, "items": _ENVELOPE_ITEMS}
    assert store("context-send", "--id", block_id,
                 "--result", json.dumps(envelope, ensure_ascii=False), "--json") == 0
    out = _out_json(capsys)
    assert out["added"] == 1 and out["block_id_verified"] is True
    # 裸の配列と同じ結果になる (封筒は運搬形式であって内容を変えない)
    shard = json.loads(Path(out["shard"]).read_text(encoding="utf-8"))
    assert shard["items"][0]["statement"] == _ENVELOPE_ITEMS[0]["statement"]
    assert shard["items"][0]["location"] == {"sheet": "s"}


def test_send_envelope_mismatched_block_id_writes_no_shard(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "mis.xlsx", {"a": "あ" * 40, "b": "い" * 40})
    store("context-set", "--docs", doc_id, "--max-chars", "50", "--json")
    capsys.readouterr()
    # b02 のつもりで抽出した結果を b01 として送る = 実際に起きた取り違えの再現
    envelope = {"block_id": f"{doc_id}.b02", "items": _ENVELOPE_ITEMS}
    assert store("context-send", "--id", f"{doc_id}.b01",
                 "--result", json.dumps(envelope, ensure_ascii=False), "--json") == 1
    err = capsys.readouterr().err
    assert f"{doc_id}.b02" in err and f"{doc_id}.b01" in err
    # 送信しなかった = シャードが無く、バリアは incomplete のまま (done にしない)
    assert store("context-check", "--json") == 3
    state = _out_json(capsys)
    assert state["complete"] is False
    assert not state["shards"]
    assert f"{doc_id}.b01" in [i["id"] for i in state["incomplete"]]


def test_send_reads_stdin_as_utf8_regardless_of_locale(store, tmp_path):
    """--result - の日本語が化けないこと (別プロセスで実環境の stdin を通す)。

    結果は stdin で渡す設計 (中間ファイルの取り違え事故を構造的に潰すため) なので、
    stdin のデコードが locale 任せだと成立しない。Windows の既定 cp932 では
    statement も type も復元不能に化け、種別が語彙外になって全項目 rejected に
    なる。PYTHONIOENCODING で敵対的な locale を明示し、それを上書きできることを
    確認する (monkeypatch した StringIO では再現しない — 実プロセスが要る)。
    """
    doc_id = _register(store, tmp_path, "utf8.xlsx", {"s": "本文です。"})
    store("context-set", "--docs", doc_id, "--json")
    block_id = f"{doc_id}.b01"
    sd = tmp_path / "store"
    statement = "注文金額 10,000 円以上は送料無料"
    payload = json.dumps(
        {"block_id": block_id, "items": [{"type": "業務ルール", "statement": statement}]},
        ensure_ascii=False,
    )
    proc = subprocess.run(
        [sys.executable, "-m", "docagent", "context-send",
         "--id", block_id, "--result", "-", "--json",
         "--store", str(sd / "library.json"),
         "--doctypes", str(sd / "doctypes.json"),
         "--facts", str(sd / "facts.json"),
         "--item-types-file", str(sd / "item_types.json"),
         "--rel-types-file", str(sd / "rel_types.json"),
         "--context", str(sd / "context.json")],
        input=payload.encode("utf-8"),
        capture_output=True,
        cwd=str(Path(__file__).resolve().parents[1]),
        env={**os.environ,
             "DOCEXTRACT_HOME": str(tmp_path / "home"),
             "PYTHONIOENCODING": "cp932"},  # 敵対的 locale を上書きできること
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    out = json.loads(proc.stdout.decode("utf-8"))
    assert out["added"] == 1 and not out["rejected"]  # 化けると種別が語彙外で rejected
    shard = json.loads(Path(out["shard"]).read_text(encoding="utf-8"))
    assert shard["items"][0]["statement"] == statement
    assert shard["items"][0]["type"] == "業務ルール"


# ── needs_vision: 画像分析待ちの自己申告キュー ──────────────────
def test_send_needs_vision_flags_block_and_check_lists_it(store, tmp_path, capsys):
    # fact-extractor が「関係が本文から確定できない」と申告 → vision マーカーを
    # 書き、context-check が vision_pending として列挙する。バリア (complete/exit)
    # には影響しない (done とは独立のキュー)。
    doc_id = _register(store, tmp_path, "vis.xlsx", {"構成図": "Web AP DB が矢印で接続。"})
    store("context-set", "--docs", doc_id, "--json")
    capsys.readouterr()
    block_id = f"{doc_id}.b01"
    envelope = {
        "block_id": block_id,
        "items": [{"type": "用語", "statement": "Web: Webサーバ"}],
        "needs_vision": {"reason": "矢印の向きが本文から不明", "range": "B2:L8"},
    }
    assert store("context-send", "--id", block_id,
                 "--result", json.dumps(envelope, ensure_ascii=False), "--json") == 0
    out = _out_json(capsys)
    assert out["added"] == 1
    assert out["needs_vision"]["reason"] == "矢印の向きが本文から不明"
    assert out["needs_vision"]["range"] == "B2:L8"
    # マーカーファイルが存在する (状態はファイル由来)
    assert (tmp_path / "store" / "vision" / f"{block_id}.json").exists()
    # check は complete=true (シャードあり=done) のまま、vision_pending を併記する
    assert store("context-check", "--json") == 0
    state = _out_json(capsys)
    assert state["complete"] is True
    assert [v["id"] for v in state["vision_pending"]] == [block_id]
    assert state["vision_pending"][0]["range"] == "B2:L8"


def test_resend_without_needs_vision_clears_the_flag(store, tmp_path, capsys):
    # pass2 (画像付き再送) で関係が確定したら、needs_vision なしの再送でマーカーを
    # 消して vision 待ちキューから外す (再送は冪等)。
    doc_id = _register(store, tmp_path, "clr.xlsx", {"図": "ノードが線で接続。"})
    store("context-set", "--docs", doc_id, "--json")
    block_id = f"{doc_id}.b01"
    flagged = {"block_id": block_id, "items": [{"type": "用語", "statement": "N: ノード"}],
               "needs_vision": {"reason": "接続不明"}}
    store("context-send", "--id", block_id,
          "--result", json.dumps(flagged, ensure_ascii=False), "--json")
    assert (tmp_path / "store" / "vision" / f"{block_id}.json").exists()
    capsys.readouterr()
    resolved = {"block_id": block_id,
                "items": [{"type": "用語", "statement": "N: ノード (A→B と確定)"}]}
    assert store("context-send", "--id", block_id,
                 "--result", json.dumps(resolved, ensure_ascii=False), "--json") == 0
    assert out_needs_none(_out_json(capsys))
    assert not (tmp_path / "store" / "vision" / f"{block_id}.json").exists()
    capsys.readouterr()
    assert store("context-check", "--json") == 0
    assert _out_json(capsys)["vision_pending"] == []


def test_force_rebuild_clears_vision_markers(store, tmp_path, capsys):
    # --force の作り直しは vision マーカーも消す (前回の申告が残らない)。
    doc_id = _register(store, tmp_path, "fv.xlsx", {"s": "本文です。"})
    store("context-set", "--docs", doc_id, "--json")
    block_id = f"{doc_id}.b01"
    store("context-send", "--id", block_id, "--result",
          json.dumps({"block_id": block_id, "items": [], "needs_vision": True},
                     ensure_ascii=False), "--json")
    assert (tmp_path / "store" / "vision" / f"{block_id}.json").exists()
    store("context-set", "--docs", doc_id, "--force", "--json")
    assert not (tmp_path / "store" / "vision" / f"{block_id}.json").exists()


def out_needs_none(o: dict) -> bool:
    return o.get("needs_vision") in (None, False)


def _fake_render(monkeypatch):
    """render_range_png を差し替え、Excel を起動せずダミー PNG を書く。"""
    import docextract.render as _render

    def fake(src, sheet, cell_range, out, **kw):
        Path(out).write_bytes(b"\x89PNG\r\n fake")
        return str(out)

    monkeypatch.setattr(_render, "render_range_png", fake)


def test_context_render_images_pending_and_get_surfaces_them(store, tmp_path, capsys, monkeypatch):
    # vision 待ちブロックの図面領域を画像化 → マーカーに記録 → context-get が
    # pass2 入力として画像パスを返す、という統合ループを通す。
    _fake_render(monkeypatch)
    doc_id = _register(store, tmp_path, "net.xlsx", {"構成図": "Web AP DB が接続。"})
    store("context-set", "--docs", doc_id, "--json")
    block_id = f"{doc_id}.b01"
    env = {"block_id": block_id,
           "items": [{"type": "用語", "statement": "Web: Webサーバ"}],
           "needs_vision": {"reason": "接続と向きが不明", "range": "B2:L8"}}
    store("context-send", "--id", block_id,
          "--result", json.dumps(env, ensure_ascii=False), "--json")
    capsys.readouterr()

    assert store("context-render", "--json") == 0
    out = _out_json(capsys)
    assert [r["id"] for r in out["rendered"]] == [block_id]
    assert out["skipped"] == []
    png = tmp_path / "store" / "vision" / f"{block_id}.png"
    assert png.exists()  # ダミー PNG が書かれた
    marker = json.loads(
        (tmp_path / "store" / "vision" / f"{block_id}.json").read_text(encoding="utf-8")
    )
    assert marker["image"].endswith(f"{block_id}.png")

    # context-get (pass2) が画像パスと理由を同梱する
    capsys.readouterr()
    assert store("context-get", "--id", block_id, "--json") == 0
    got = _out_json(capsys)
    assert got["image"].endswith(f"{block_id}.png")
    assert got["vision_reason"] == "接続と向きが不明"
    # check の vision_pending は「画像済」になる
    capsys.readouterr()
    store("context-check", "--json")
    assert _out_json(capsys)["vision_pending"][0]["image"].endswith(f"{block_id}.png")


def test_context_render_skips_non_excel_source(store, tmp_path, capsys, monkeypatch):
    # Excel 以外のソースはレンダー対象外 (render は呼ばれない)。
    import docextract.render as _render
    monkeypatch.setattr(
        _render, "render_range_png",
        lambda *a, **k: pytest.fail("Excel 以外で render を呼んではいけない"),
    )
    doc_id = _register(store, tmp_path, "memo.pdf", {"p": "内容です。"})
    store("context-set", "--docs", doc_id, "--json")
    block_id = f"{doc_id}.b01"
    store("context-send", "--id", block_id, "--result",
          json.dumps({"block_id": block_id, "items": [], "needs_vision": True},
                     ensure_ascii=False), "--json")
    capsys.readouterr()
    assert store("context-render", "--json") == 0
    out = _out_json(capsys)
    assert out["rendered"] == []
    assert len(out["skipped"]) == 1 and "Excel 以外" in out["skipped"][0]["reason"]


def test_send_envelope_without_block_id_is_accepted_with_warning(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "nob.xlsx", {"s": "本文です。"})
    store("context-set", "--docs", doc_id, "--json")
    capsys.readouterr()
    envelope = {"items": _ENVELOPE_ITEMS}
    assert store("context-send", "--id", f"{doc_id}.b01",
                 "--result", json.dumps(envelope, ensure_ascii=False), "--json") == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["block_id_verified"] is False
    # 突合をスキップした事実は黙って飲み込まない (silent cap 禁止)
    assert "block_id がありません" in captured.err


# ── 既定出力: 軽量エージェント形式 (--json 不要) ─────────────────
def test_get_default_output_is_agent_format(store, tmp_path, capsys):
    # 既定出力はメタ行 + 生テキスト。JSON のエスケープ・引用符の冗長を避け、
    # 語彙 (types/rels) と send に使う id を含む。
    doc_id = _register(store, tmp_path, "af.xlsx", {"画面": "1行目。\n2行目。"})
    store("context-set", "--docs", doc_id, "--json")
    capsys.readouterr()
    assert store("context-get") == 0
    out = capsys.readouterr().out
    assert f"id: {doc_id}.b01" in out
    assert "types: 機能要件" in out          # 語彙同梱
    assert "rels: realizes" in out
    assert "1行目。\n2行目。" in out          # 生テキスト (\n エスケープなし)
    assert '"text"' not in out               # JSON 形式ではない


def test_send_and_check_default_output_agent_format(store, tmp_path, capsys):
    doc_id = _register(store, tmp_path, "ag.xlsx", {"s": "本文です。"})
    store("context-set", "--docs", doc_id, "--json")
    capsys.readouterr()
    items = [{"type": "用語", "statement": "語: 定義"}, {"type": "謎", "statement": "x"}]
    assert store("context-send", "--id", f"{doc_id}.b01",
                 "--result", json.dumps(items, ensure_ascii=False)) == 0
    out = capsys.readouterr().out
    assert "added: 1 (用語=1)" in out
    assert "rejected: 1" in out
    assert store("context-check") == 0       # 既定出力でも exit コードは同じ
    out = capsys.readouterr().out
    assert "complete: true" in out
    assert "shards:" in out


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
