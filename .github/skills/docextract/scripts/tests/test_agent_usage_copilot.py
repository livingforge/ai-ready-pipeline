# -*- coding: utf-8 -*-
"""agent-usage の GitHub Copilot 版（--agent copilot）集計とレンダリングの検証。

VS Code workspaceStorage 配下の Agent Debug Log（debug-logs/<sid>/main.jsonl）を
合成し、copilot_collect.build_summary が credit ベースの agent-usage スキーマを返すこと、
render.render_html が credit 表示になることを end-to-end で確認する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "skills" / "agent-usage" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import copilot_collect  # noqa: E402
import render  # noqa: E402

T0 = 1_720_000_000_000  # ms


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                    encoding="utf-8")


def _models_json() -> str:
    return json.dumps([
        {"id": "claude-opus-4.7", "billing": {"token_prices": {
            "batch_size": 1_000_000,
            "default": {"input_price": 0, "output_price": 15,
                        "cache_price": 1.5, "cache_write_price": 3.75}}}},
    ], ensure_ascii=False)


def _make_session(debug_root: Path, sid: str, base_ts: int, *, child: bool) -> None:
    d = debug_root / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "models.json").write_text(_models_json(), encoding="utf-8")
    ev = [
        {"type": "session_start", "ts": base_ts,
         "attrs": {"copilotVersion": "0.31.0", "vscodeVersion": "1.99.0"}},
        {"type": "turn_start", "ts": base_ts + 100, "attrs": {"turnId": "t1"}},
        {"type": "user_message", "ts": base_ts + 150, "attrs": {"content": "テスト仕様書を作って"}},
        {"type": "llm_request", "ts": base_ts + 200, "dur": 1800,
         "attrs": {"model": "claude-opus-4.7", "debugName": "editAgent",
                   "inputTokens": 12000, "cachedTokens": 8000, "outputTokens": 1500,
                   "copilotUsageNanoAiu": 250_000_000, "ttft": 640}},
        {"type": "tool_call", "ts": base_ts + 2100, "dur": 320, "name": "read_file", "status": "ok"},
        {"type": "tool_call", "ts": base_ts + 2800, "dur": 90, "name": "run_in_terminal", "status": "error"},
    ]
    if child:
        ev.append({"type": "child_session_ref", "ts": base_ts + 3200,
                   "attrs": {"childSessionId": "c1", "childLogFile": "child-1.jsonl",
                             "label": "explore"}})
        _write_jsonl(d / "child-1.jsonl", [
            {"type": "session_start", "ts": base_ts + 3210, "attrs": {}},
            {"type": "llm_request", "ts": base_ts + 3300, "dur": 500,
             "attrs": {"model": "claude-opus-4.7", "debugName": "exploreAgent",
                       "inputTokens": 3000, "cachedTokens": 500, "outputTokens": 200,
                       "copilotUsageNanoAiu": 20_000_000}},
            {"type": "tool_call", "ts": base_ts + 3500, "dur": 120, "name": "grep_search", "status": "ok"},
        ])
    ev.append({"type": "turn_end", "ts": base_ts + 4000, "attrs": {"turnId": "t1"}})
    _write_jsonl(d / "main.jsonl", ev)
    # タイトル生成ログ
    parts = [{"parts": [{"type": "text", "content": "テスト仕様書の作成"}]}]
    _write_jsonl(d / "title-x.jsonl", [
        {"type": "agent_response", "ts": base_ts + 50,
         "attrs": {"response": json.dumps(parts, ensure_ascii=False)}}])


def _make_storage(tmp_path: Path) -> Path:
    root = tmp_path / "workspaceStorage"
    ws = root / "wshash01"
    (ws).mkdir(parents=True, exist_ok=True)
    (ws / "workspace.json").write_text(
        json.dumps({"folder": "file:///c%3A/proj/meeting-room"}), encoding="utf-8")
    dbg = ws / "GitHub.copilot-chat" / "debug-logs"
    _make_session(dbg, "11111111-0001", T0, child=True)
    _make_session(dbg, "22222222-0002", T0 + 86_400_000, child=False)
    return root


def _args(root: Path, **over):
    base = dict(storage_root=str(root), workspace=None, workspace_id=None,
                since=None, until=None, days=None, project=None, top=100)
    base.update(over)
    return SimpleNamespace(**base)


def test_copilot_summary_aiu(tmp_path):
    root = _make_storage(tmp_path)
    s = copilot_collect.build_summary(_args(root))

    assert s["agent"] == "copilot"
    assert s["cost_unit"] == "credit"
    # 実測消費を credit（1 credit = 1 AIU = 1e9 nano）で表示:
    # session1 = opus(0.25)+child opus(0.02)=0.27, session2 = 0.25 → 0.52
    assert s["totals"]["cost_usd"] == 0.52
    assert s["totals"]["sessions"] == 2
    # main/subagent 分割（child のみ subagent）
    assert s["by_agent"]["subagent"]["cost_usd"] == 0.02
    assert round(s["by_agent"]["main"]["cost_usd"], 2) == 0.50
    # キャッシュ節約 credit = cached ×(fresh入力単価 − cache単価)/batch を合算:
    # 各 main セッション cached 8000 ×(3.75−1.5)/1e6 = 0.018、child cached 500 ×… = 0.001125
    # 合計 0.018 + 0.001125 + 0.018 = 0.037125 → 0.0371
    assert s["totals"]["cache_savings_usd"] == 0.0371
    # by_tool は main + 子ツールを合算（grep_search は子由来）
    tools = {t["tool"]: t for t in s["by_tool"]}
    assert tools["grep_search"]["calls"] == 1
    assert tools["run_in_terminal"]["errors"] == 2
    # subagents 内訳
    assert s["subagents"]["total_calls"] == 1
    assert s["subagents"]["by_type"][0]["subagent_type"] == "explore"
    # プロジェクトは workspace フォルダ名から
    assert s["by_project"][0]["project"] == "meeting-room"
    # タイトルが拾えている
    titles = {c["title"] for c in s["conversations"]}
    assert "テスト仕様書の作成" in titles


def test_copilot_render_uses_credit(tmp_path):
    root = _make_storage(tmp_path)
    s = copilot_collect.build_summary(_args(root))
    html = render.render_html(s)
    assert "GitHub Copilot" in html
    assert "credit" in html
    assert "総消費 credit（実測）" in html
    # ヒーローにキャッシュ節約額（Claude 同様）が出る
    assert "キャッシュにより" in html
    assert "節約済み" in html
    # USD 記号がコスト表示に紛れ込まない（JS 内の非活性 USD 分岐は本文外）
    body = html.split("<script")[0]
    assert "$" not in body


def test_project_filter(tmp_path):
    root = _make_storage(tmp_path)
    s = copilot_collect.build_summary(_args(root, project="does-not-match"))
    assert s["conversation_count"] == 0
    assert s["totals"]["cost_usd"] == 0.0


def test_join_sources_truncates_to_three():
    dirs = [f"C:/ws{i}/debug-logs" for i in range(5)]
    text = copilot_collect._join_sources(dirs)
    # 先頭 3 件のみ表示、残りは「…他 N 件」で省略
    assert "C:/ws0/debug-logs" in text
    assert "C:/ws2/debug-logs" in text
    assert "C:/ws3/debug-logs" not in text
    assert "…他 2 件" in text
    # 3 件以下なら省略しない
    assert copilot_collect._join_sources(dirs[:2]) == "C:/ws0/debug-logs ; C:/ws1/debug-logs"


def test_source_dir_lists_only_contributing_workspaces(tmp_path):
    root = _make_storage(tmp_path)
    # 実データが寄与した debug-logs だけが source に載る（1 ワークスペースなので省略なし）
    s = copilot_collect.build_summary(_args(root))
    assert "debug-logs" in s["source_dir"]
    assert "…他" not in s["source_dir"]
    # project フィルタで全ワークスペースが外れたら storage_root へフォールバック
    s2 = copilot_collect.build_summary(_args(root, project="does-not-match"))
    assert s2["source_dir"] == str(root)
