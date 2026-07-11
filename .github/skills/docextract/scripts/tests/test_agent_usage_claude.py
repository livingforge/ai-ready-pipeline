# -*- coding: utf-8 -*-
"""agent-usage の Claude Code 版（report.build_summary）サブエージェント集計の検証。

特に「呼び出し側が subagent_type を省略した Agent 呼び出し」でも、内部ログ
（subagents/agent-*.jsonl）の meta.json にある agentType で種別が確定し、
内部トークン/コストが同じ行に正しく結合される（"?"・コスト 0 に化けない）ことを
回帰的に確認する。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS = Path(__file__).resolve().parents[1] / "src" / "skills" / "agent-usage" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import report  # noqa: E402

PROJ = "c--demo"
SESSION = "sess-0001"
TOOL_ID = "toolu_ABC123"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def _make_tree(root: Path, *, meta_agent_type: str | None,
               call_subagent_type: str | None) -> None:
    """親セッション + 分離サブエージェントログ（meta.json 付き）を合成する。"""
    proj = root / PROJ
    # --- 親セッション: Agent 呼び出し（input に subagent_type を入れる/入れない）+ tool_result ---
    call_input = {"description": "設計プランを作る", "prompt": "..."}
    if call_subagent_type is not None:
        call_input["subagent_type"] = call_subagent_type
    _write_jsonl(proj / f"{SESSION}.jsonl", [
        {"type": "assistant", "timestamp": "2026-07-11T07:11:10.000Z", "sessionId": SESSION,
         "cwd": f"/c/{PROJ}", "message": {"model": "claude-opus-4-8", "usage": {
             "input_tokens": 100, "output_tokens": 50}, "content": [
                 {"type": "tool_use", "id": TOOL_ID, "name": "Agent", "input": call_input}]}},
        {"type": "user", "timestamp": "2026-07-11T07:11:46.500Z", "sessionId": SESSION,
         "message": {"content": [
             {"type": "tool_result", "tool_use_id": TOOL_ID, "content": "done"}]}},
    ])
    # --- 分離サブエージェント内部ログ + meta.json ---
    sub_dir = proj / SESSION / "subagents"
    _write_jsonl(sub_dir / "agent-xyz.jsonl", [
        {"type": "assistant", "timestamp": "2026-07-11T07:11:20.000Z", "isSidechain": True,
         "message": {"model": "claude-haiku-4-5-20251001",
                     "usage": {"input_tokens": 98, "output_tokens": 2791}, "content": []}},
    ])
    meta = {"toolUseId": TOOL_ID, "description": "設計プランを作る"}
    if meta_agent_type is not None:
        meta["agentType"] = meta_agent_type
    (sub_dir / "agent-xyz.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _args(root: Path, **over):
    base = dict(claude_dir=str(root), pricing=None, since=None, until=None,
                days=None, project=None, top=100)
    base.update(over)
    return SimpleNamespace(**base)


def _by_type(root: Path):
    s = report.build_summary(_args(root))
    return {r["subagent_type"]: r for r in s["subagents"]["by_type"]}, s


def test_omitted_subagent_type_resolves_from_meta(tmp_path):
    """呼び出し側 subagent_type 省略でも meta の agentType で確定し、トークンが結合される。"""
    _make_tree(tmp_path, meta_agent_type="general-purpose", call_subagent_type=None)
    bt, s = _by_type(tmp_path)

    # "?" に化けず、meta の agentType の行にまとまる
    assert "?" not in bt
    assert "general-purpose" in bt
    row = bt["general-purpose"]
    assert row["calls"] == 1
    assert row["input"] == 98 and row["output"] == 2791  # 内部トークンが結合されている
    assert row["messages"] == 1
    # 所要時間は親の tool_use→tool_result 差（約 36.5s）から
    assert row["total_seconds"] > 0
    # 内部消費は総計にも入っている
    assert s["by_agent"]["subagent"]["output"] == 2791


def test_explicit_subagent_type_still_grouped(tmp_path):
    """呼び出し側で明示した場合も従来どおり正しく1行に集計される。"""
    _make_tree(tmp_path, meta_agent_type="skill-setup", call_subagent_type="skill-setup")
    bt, _ = _by_type(tmp_path)
    assert set(bt) == {"skill-setup"}
    assert bt["skill-setup"]["calls"] == 1
    assert bt["skill-setup"]["output"] == 2791


def test_no_meta_falls_back_to_question_mark(tmp_path):
    """meta にも呼び出し側にも型が無ければ従来どおり "?"（内部ログ欠落時の挙動を保つ）。"""
    _make_tree(tmp_path, meta_agent_type=None, call_subagent_type=None)
    bt, _ = _by_type(tmp_path)
    assert "?" in bt
    assert bt["?"]["calls"] == 1
