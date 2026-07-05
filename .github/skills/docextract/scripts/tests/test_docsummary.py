"""docsummary (LLM 要約) のユニットテスト。

LLM 呼び出しは transport / providers.complete の差し替えで完全にモックし、
ネットワークも API キー (実物) も不要。ストアは一時ディレクトリ上で操作する。
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docsummary import cli, providers, settings, store
from docsummary.settings import LLMConfig, Settings, SettingsError
from docsummary.store import DocSummaryError, SummaryStore

# 秘密情報のフィクスチャ値。テスト出力に漏れていないことの検証にも使う。
FAKE_KEY = "sk-test-secret-000"


# ── フィクスチャ ─────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """プロバイダ関連の実環境変数を隔離し、home を一時領域へ向ける。"""
    for spec_list in settings.PROVIDERS.values():
        for spec in spec_list:
            monkeypatch.delenv(spec.name, raising=False)
    for name in (*settings.PROVIDER_ENVS, settings.ENV_FILE_ENV,
                 "DOCSUMMARY_MAX_INPUT_CHARS", "DOCSUMMARY_MAX_OUTPUT_TOKENS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOCEXTRACT_HOME", str(tmp_path / ".docextract"))
    monkeypatch.chdir(tmp_path)


def _make_result(root: Path, doc_id: str, text: str, content_hash: str = "a" * 64) -> Path:
    rp = root / f"{doc_id}-result.json"
    rp.write_text(json.dumps({
        "id": doc_id,
        "source": f"{doc_id}.docx",
        "source_abspath": str(root / "docs" / f"{doc_id}.docx"),
        "content_hash": content_hash,
        "file_type": "docx",
        "metadata": {},
        "summary": {"text": 1},
        "elements": [{"type": "text", "content": text, "location": {"order": 1}}],
    }, ensure_ascii=False), encoding="utf-8")
    return rp


def _register(tmp_path: Path, *docs: tuple[str, str]) -> None:
    """(doc_id, 本文) を library.json に登録する。"""
    from docagent.store import Library

    lib = Library.load(Path(tmp_path / ".docextract" / "store" / "library.json"))
    for doc_id, text in docs:
        lib.add_from_result(_make_result(tmp_path, doc_id, text), overwrite=True)
    lib.save()


# ── settings: .env の読み込み ────────────────────────────────
def test_parse_env_file_quotes_comments_export(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# comment\n"
        "OPENAI_API_KEY='quoted-key'\n"
        'OPENAI_MODEL="gpt-x"\n'
        "export GEMINI_API_KEY=g-key\n"
        "BROKEN LINE\n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    values = settings.parse_env_file(p)
    assert values["OPENAI_API_KEY"] == "quoted-key"
    assert values["OPENAI_MODEL"] == "gpt-x"
    assert values["GEMINI_API_KEY"] == "g-key"
    assert values["EMPTY"] == ""
    assert "BROKEN LINE" not in values


def test_find_env_file_searches_upward(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert settings.find_env_file() == tmp_path / ".env"


def test_find_env_file_explicit_missing_raises(tmp_path):
    with pytest.raises(SettingsError):
        settings.find_env_file(tmp_path / "nope.env")


def test_os_environ_wins_over_env_file(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENAI_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_MODEL", "from-env")
    s = Settings.load(tmp_path / ".env")
    assert s.get("OPENAI_MODEL") == "from-env"
    assert s.source_of("OPENAI_MODEL") == "env"


# ── settings: プロバイダ解決 ────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("openai", "openai"),
    ("Azure", "azure"),
    ("azureopenai", "azure"),
    ("azure_openai", "azure"),
    ("azure-openai", "azure"),
    ("GEMINI", "gemini"),
    ("claude", "anthropic"),
    ("anthropic", "anthropic"),
])
def test_normalize_provider(raw, expected):
    assert settings.normalize_provider(raw) == expected


def test_normalize_provider_unknown():
    with pytest.raises(SettingsError):
        settings.normalize_provider("watson")


def test_resolve_provider_auto_single_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    assert settings.resolve_provider(Settings()) == "gemini"


def test_resolve_provider_none_configured():
    with pytest.raises(SettingsError, match="接続設定が見つかりません"):
        settings.resolve_provider(Settings())


def test_resolve_provider_multiple_needs_choice(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    with pytest.raises(SettingsError, match="複数のプロバイダ"):
        settings.resolve_provider(Settings())
    monkeypatch.setenv("DOCSUMMARY_PROVIDER", "anthropic")
    assert settings.resolve_provider(Settings()) == "anthropic"


def test_resolve_config_defaults_and_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    cfg = settings.resolve_config(Settings())
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-opus-4-8"  # 既定モデル
    assert cfg.values["ANTHROPIC_VERSION"] == "2023-06-01"
    cfg2 = settings.resolve_config(Settings(), model="my-model")
    assert cfg2.model == "my-model"
    assert FAKE_KEY not in repr(cfg)  # repr に秘密を出さない


def test_resolve_config_missing_required(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", FAKE_KEY)
    with pytest.raises(SettingsError, match="AZURE_OPENAI_ENDPOINT"):
        settings.resolve_config(Settings(), provider="azure")


def test_check_payload_never_contains_secret(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    s = Settings.load(tmp_path / ".env")
    payload = settings.check_payload(s)
    dumped = json.dumps(payload, ensure_ascii=False)
    assert FAKE_KEY not in dumped
    openai_vars = {v["name"]: v for v in payload["providers"]["openai"]["vars"]}
    assert openai_vars["OPENAI_API_KEY"]["set"] is True
    assert openai_vars["OPENAI_API_KEY"]["source"] == "file"
    assert payload["selected_provider"] == "openai"


# ── providers: リクエスト組み立てと応答解釈 ─────────────────
def _cfg(provider: str, **values) -> LLMConfig:
    defaults = {s.name: s.default for s in settings.PROVIDERS[provider] if s.default}
    return LLMConfig(provider=provider, model=values.pop("model", "m"),
                     values={**defaults, **values})


def test_build_request_openai_key_in_header_only():
    cfg = _cfg("openai", OPENAI_API_KEY=FAKE_KEY)
    url, headers, payload = providers._build_request(cfg, "sys", "usr", 100)
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in json.dumps(payload)
    assert payload["messages"][0]["role"] == "system"


def test_build_request_azure_url_and_header():
    cfg = _cfg("azure", AZURE_OPENAI_API_KEY=FAKE_KEY,
               AZURE_OPENAI_ENDPOINT="https://r.openai.azure.com",
               AZURE_OPENAI_DEPLOYMENT="dep", model="dep")
    url, headers, payload = providers._build_request(cfg, "s", "u", 100)
    assert url == ("https://r.openai.azure.com/openai/deployments/dep/"
                   "chat/completions?api-version=2024-10-21")
    assert headers["api-key"] == FAKE_KEY
    assert "model" not in payload  # Azure はデプロイ名が URL に入る


def test_build_request_gemini_and_anthropic():
    g = _cfg("gemini", GEMINI_API_KEY=FAKE_KEY, model="gemini-2.0-flash")
    url, headers, payload = providers._build_request(g, "s", "u", 77)
    assert url.endswith("/models/gemini-2.0-flash:generateContent")
    assert headers["x-goog-api-key"] == FAKE_KEY
    assert payload["generationConfig"]["maxOutputTokens"] == 77

    a = _cfg("anthropic", ANTHROPIC_API_KEY=FAKE_KEY, model="claude-opus-4-8")
    url, headers, payload = providers._build_request(a, "s", "u", 88)
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == FAKE_KEY
    assert headers["anthropic-version"] == "2023-06-01"
    assert payload["max_tokens"] == 88
    assert payload["system"] == "s"


def _openai_response(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


def test_complete_extracts_text_per_provider():
    responses = {
        "openai": _openai_response("要約A"),
        "azure": _openai_response("要約A"),
        "gemini": {"candidates": [{"content": {"parts": [{"text": "要約"}, {"text": "A"}]}}]},
        "anthropic": {"stop_reason": "end_turn",
                      "content": [{"type": "text", "text": "要約A"}]},
    }
    for provider, resp in responses.items():
        cfg = _cfg(provider,
                   **{s.name: FAKE_KEY for s in settings.PROVIDERS[provider]
                      if s.required})
        got = providers.complete(cfg, "s", "u",
                                 transport=lambda *a, resp=resp: resp)
        assert got == "要約A", provider


def test_complete_anthropic_refusal_raises():
    cfg = _cfg("anthropic", ANTHROPIC_API_KEY=FAKE_KEY)
    with pytest.raises(providers.ProviderError, match="refusal"):
        providers.complete(cfg, "s", "u",
                           transport=lambda *a: {"stop_reason": "refusal",
                                                 "content": []})


def test_complete_retries_once_on_retryable(monkeypatch):
    monkeypatch.setattr(providers.time, "sleep", lambda *_: None)
    cfg = _cfg("openai", OPENAI_API_KEY=FAKE_KEY)
    calls = []

    def flaky(url, headers, payload, timeout):
        calls.append(1)
        if len(calls) == 1:
            err = providers.ProviderError("HTTP 503")
            err.__cause__ = urllib.error.HTTPError(url, 503, "busy", None,
                                                   io.BytesIO(b""))
            raise err
        return _openai_response("ok")

    assert providers.complete(cfg, "s", "u", transport=flaky) == "ok"
    assert len(calls) == 2


def test_complete_no_retry_on_auth_error():
    cfg = _cfg("openai", OPENAI_API_KEY=FAKE_KEY)

    def denied(url, headers, payload, timeout):
        err = providers.ProviderError("HTTP 401")
        err.__cause__ = urllib.error.HTTPError(url, 401, "no", None,
                                               io.BytesIO(b""))
        raise err

    with pytest.raises(providers.ProviderError, match="401"):
        providers.complete(cfg, "s", "u", transport=denied)


# ── store: 状態判定と対象選択 ────────────────────────────────
def _doc(doc_id: str, content_hash: str = "a" * 64, abspath: str | None = None) -> dict:
    return {"id": doc_id, "source": f"{doc_id}.docx",
            "source_abspath": abspath or f"/x/{doc_id}.docx",
            "content_hash": content_hash}


def test_status_of_none_stale_fresh(tmp_path):
    sums = SummaryStore(path=tmp_path / "summaries.json")
    doc = _doc("d1")
    assert sums.status_of(doc, "spec1") == "none"
    sums.upsert({"doc_id": "d1", "content_hash": "a" * 64,
                 "spec_hash": "spec1", "updated_at": "t"})
    assert sums.status_of(doc, "spec1") == "fresh"
    assert sums.status_of(_doc("d1", content_hash="b" * 64), "spec1") == "stale"
    assert sums.status_of(doc, "spec2") == "stale"  # 観点/カテゴリー変更でも stale


def test_spec_hash_changes_with_guide_or_categories():
    base = store.spec_hash("観点A", ["c1", "c2"])
    assert base != store.spec_hash("観点B", ["c1", "c2"])  # 観点変更
    assert base != store.spec_hash("観点A", ["c1"])        # カテゴリー変更
    assert base == store.spec_hash("観点A", ["c1", "c2"])  # 同一なら一致


def test_select_targets_modes(tmp_path):
    sums = SummaryStore(path=tmp_path / "summaries.json")
    folder = tmp_path / "docs"
    folder.mkdir()
    docs = [
        _doc("d1", abspath=str(folder / "d1.docx")),
        _doc("d2", abspath=str(tmp_path / "other" / "d2.docx")),
    ]
    sums.upsert({"doc_id": "d2", "content_hash": "a" * 64,
                 "spec_hash": "f", "updated_at": "t"})

    # pending: 未要約の d1 だけ
    got = store.select_targets(docs, sums, "f", pending=True)
    assert [d["id"] for d in got] == ["d1"]
    # all + force: fresh も含む
    got = store.select_targets(docs, sums, "f", all_docs=True, force=True)
    assert [d["id"] for d in got] == ["d1", "d2"]
    # ID 指定で fresh はスキップ、--force で対象化
    assert store.select_targets(docs, sums, "f", ids=["d2"]) == []
    assert [d["id"] for d in store.select_targets(docs, sums, "f", ids=["d2"],
                                                  force=True)] == ["d2"]
    # --dir はフォルダ配下の元ファイルだけ
    got = store.select_targets(docs, sums, "f", folder=folder, force=True)
    assert [d["id"] for d in got] == ["d1"]
    # 未登録 ID・無指定はエラー
    with pytest.raises(DocSummaryError):
        store.select_targets(docs, sums, "f", ids=["nope"])
    with pytest.raises(DocSummaryError):
        store.select_targets(docs, sums, "f")


def test_resolve_guide_prefers_override(tmp_path):
    path, text = store.resolve_guide()
    assert path == store.PACKAGED_GUIDE
    assert "観点" in text
    override = store.guide_override_path()
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("# 独自観点\n", encoding="utf-8")
    path2, text2 = store.resolve_guide()
    assert path2 == override
    assert text != text2


def test_resolve_categories_default_and_override(tmp_path):
    path, cats = store.resolve_categories()
    assert path == store.PACKAGED_CATEGORIES
    assert "設計" in cats and "その他" in cats
    override = store.categories_override_path()
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(json.dumps({"categories": ["甲", "乙"]}),
                        encoding="utf-8")
    path2, cats2 = store.resolve_categories()
    assert path2 == override and cats2 == ["甲", "乙"]


# ── cli: カテゴリー抽出・正規化 ─────────────────────────────
def test_split_category_extracts_and_normalizes():
    cats = ["要件・仕様", "設計", "その他"]
    # 明示マーカー + 正規化 (区切り揺れを吸収)
    cat, body = cli._split_category("カテゴリー: 設計\n本文だ", cats)
    assert cat == "設計" and body == "本文だ"
    cat, body = cli._split_category("category：要件/仕様\n\n本文", cats)
    assert cat == "要件・仕様"
    # 語彙外は未分類にフォールバック (本文はマーカー行だけ除去)
    cat, body = cli._split_category("カテゴリー: 謎\n中身", cats)
    assert cat == store.UNCATEGORIZED and body == "中身"
    # マーカー無しなら未分類・全文が本文
    cat, body = cli._split_category("いきなり本文", cats)
    assert cat == store.UNCATEGORIZED and body == "いきなり本文"


# ── cli: end-to-end (LLM はモック) ──────────────────────────
def test_cli_run_all_and_pending(tmp_path, monkeypatch, capsys):
    _register(tmp_path, ("d1", "第一章 これは仕様書である。"), ("d2", "議事録の本文。"))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    captured = {}

    def fake_complete(cfg, system, user, **kw):
        captured["system"] = system
        captured["user"] = user
        return "カテゴリー: 要件・仕様\n## 概要\nモック要約"

    monkeypatch.setattr(cli.providers, "complete", fake_complete)

    rc = cli.main(["run", "--all", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert {d["id"] for d in out["summarized"]} == {"d1", "d2"}
    assert out["provider"] == "openai"
    assert all(d["category"] == "要件・仕様" for d in out["summarized"])
    # プロンプトに観点ガイドとカテゴリー候補と本文が入っている
    assert "要約の観点" in captured["system"]
    assert "カテゴリー候補" in captured["system"]
    assert "議事録の本文。" in captured["user"] or "仕様書" in captured["user"]

    # 要約ファイルとメタデータ (category + spec_hash) が保存されている
    sums = SummaryStore.load()
    entry = sums.find("d1")
    assert entry and entry["provider"] == "openai"
    assert entry["category"] == "要件・仕様" and entry["spec_hash"]
    body = Path(entry["summary_path"]).read_text(encoding="utf-8")
    assert "モック要約" in body
    assert "doc_id: d1" in body
    assert "| カテゴリー | 要件・仕様 |" in body  # 固定構造のメタ表
    assert "カテゴリー: 要件・仕様" not in body     # マーカー行は本文から除去済み

    # 2 回目の --pending は対象なし (fresh)
    rc = cli.main(["run", "--pending", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summarized"] == []


def test_cli_dry_run_needs_no_key(tmp_path, capsys):
    _register(tmp_path, ("d1", "本文"))
    rc = cli.main(["run", "--all", "--dry-run", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["targets"][0]["id"] == "d1"


def test_cli_run_without_config_fails_helpfully(tmp_path, capsys):
    _register(tmp_path, ("d1", "本文"))
    rc = cli.main(["run", "--all"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "config --init" in err or "接続設定" in err


def test_cli_list_and_show(tmp_path, monkeypatch, capsys):
    _register(tmp_path, ("d1", "本文"))
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    monkeypatch.setattr(cli.providers, "complete", lambda *a, **k: "要約text")
    assert cli.main(["run", "d1"]) == 0
    capsys.readouterr()

    assert cli.main(["list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["id"] == "d1" and rows[0]["status"] == "fresh"
    assert rows[0]["category"] == store.UNCATEGORIZED  # マーカー無し応答

    assert cli.main(["show", "d1"]) == 0
    assert "要約text" in capsys.readouterr().out
    assert cli.main(["show", "nope"]) == 1


def test_cli_run_partial_failure_returns_1(tmp_path, monkeypatch, capsys):
    _register(tmp_path, ("d1", "本文1"), ("d2", "本文2"))
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)

    def sometimes(cfg, system, user, **kw):
        if "本文2" in user:
            raise providers.ProviderError("boom")
        return "ok"

    monkeypatch.setattr(cli.providers, "complete", sometimes)
    rc = cli.main(["run", "--all", "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert len(out["summarized"]) == 1 and len(out["failed"]) == 1
    # 成功分は保存済み (途中失敗で失われない)
    assert SummaryStore.load().find(out["summarized"][0]["id"])


def test_cli_config_init_and_check(tmp_path, capsys):
    assert cli.main(["config", "--init"]) == 0
    capsys.readouterr()
    assert (tmp_path / ".env").is_file()
    assert (tmp_path / ".env.example").is_file()
    # 雛形に実キーは無い (プレースホルダのみ)
    assert FAKE_KEY not in (tmp_path / ".env").read_text(encoding="utf-8")

    # check は未設定なら exit 1、値は一切出力しない
    (tmp_path / ".env").write_text(f"ANTHROPIC_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    rc = cli.main(["config", "--check", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert FAKE_KEY not in out
    payload = json.loads(out)
    assert payload["selected_provider"] == "anthropic"
