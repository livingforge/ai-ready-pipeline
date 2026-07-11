# -*- coding: utf-8 -*-
"""`report` サブコマンド — Claude Code の利用実績を集計し summary.json + report.html を出力する。

使い方:
    python <skill-dir> report [オプション]

オプション:
    --claude-dir PATH   走査対象（既定: ~/.claude/projects）
    --pricing PATH      単価表（既定: 同梱 pricing.json）
    --out DIR           出力先ディレクトリ（既定: ./agent-usage-out）
    --days N            直近 N 日だけ集計
    --since YYYY-MM-DD  この日以降
    --until YYYY-MM-DD  この日まで
    --project SUBSTR    プロジェクト名/cwd に SUBSTR を含むものだけ
    --top N             会話詳細モーダルにツール/Agent タイムラインを収録する
                        会話数（コスト降順の上位 N 件・既定 100・0=全件）。会話行と
                        モーダル自体は常に全会話ぶん出力され、上位 N 件を超える会話は
                        モーダルに「上位会話のみ収録」と表示される。summary.json /
                        conversations.csv は常に全会話を含む
    --json-only         HTML を出さず summary.json / conversations.csv だけ

出力（--out 配下）:
    summary.json        機械可読の集計正本（conversations に全会話をタイトル付きで収録）
    conversations.csv   全会話台帳（タイトル・プロジェクト・モデル・トークン・コスト・時間）
    report.html         自己完結ビュー（会話はタイトルで表示）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import collect  # noqa: E402
import render  # noqa: E402


def _load_pricing(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _empty_bucket() -> dict:
    return {
        "messages": 0,
        "tool_calls": 0,
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
        "cost_usd": 0.0,
        "cost_known": True,
    }


def _add_usage(bucket: dict, usage: dict, cost: float | None) -> None:
    bucket["messages"] += 1
    bucket["input"] += usage.get("input_tokens", 0) or 0
    bucket["output"] += usage.get("output_tokens", 0) or 0
    bucket["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
    bucket["cache_write"] += usage.get("cache_creation_input_tokens", 0) or 0
    if cost is None:
        bucket["cost_known"] = False
    else:
        bucket["cost_usd"] += cost


def build_summary(args: argparse.Namespace) -> dict:
    claude_dir = Path(args.claude_dir) if args.claude_dir else collect.default_claude_dir()
    pricing = _load_pricing(Path(args.pricing) if args.pricing else HERE / "pricing.json")

    since = _parse_day(args.since)
    until = _parse_day(args.until, end=True)
    if args.days:
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    totals = _empty_bucket()
    by_model: dict[str, dict] = defaultdict(_empty_bucket)
    by_project: dict[str, dict] = defaultdict(_empty_bucket)
    by_day: dict[str, dict] = defaultdict(_empty_bucket)
    by_agent = {"main": _empty_bucket(), "subagent": _empty_bucket()}
    by_tool: dict[str, dict] = defaultdict(lambda: {"calls": 0, "errors": 0, "seconds": 0.0})
    # サブエージェント型ごとの内部消費（subagents/*.jsonl 由来のトークン・コスト）
    subtype_usage: dict[str, dict] = defaultdict(_empty_bucket)
    sessions: dict[str, dict] = {}
    meta: dict[str, dict] = {}
    unknown_models: set[str] = set()
    cache_savings = 0.0
    active_total = 0.0

    if not claude_dir.is_dir():
        raise SystemExit(f"Claude Code のセッションが見つかりません: {claude_dir}")

    for ev in collect.iter_events(claude_dir):
        ts = ev.get("ts")
        if ts is not None:
            if since and ts < since:
                continue
            if until and ts > until:
                continue
        if ev["kind"] == "session_meta":
            # タイトル/最終プロンプトはセッション横断のメタ。最後に見たものを採用
            # （タイトルは会話が進むと更新されるため）。プロジェクト絞り込みの対象外。
            m = meta.setdefault(ev["session"], {"title": None, "last_prompt": None})
            if ev.get("title"):
                m["title"] = ev["title"]
            if ev.get("last_prompt"):
                m["last_prompt"] = ev["last_prompt"]
            continue

        project = _project_label(ev)
        if args.project and args.project not in (ev.get("cwd") or "") \
                and args.project not in ev.get("project", ""):
            continue

        if ev["kind"] == "assistant":
            usage = ev["usage"]
            model = ev.get("model") or "(unknown)"
            cost = collect.price_usage(usage, model, pricing)
            billable = (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0) \
                + (usage.get("cache_read_input_tokens", 0) or 0) \
                + (usage.get("cache_creation_input_tokens", 0) or 0)
            if cost is None and billable > 0:
                unknown_models.add(model)
            cache_savings += collect.cache_saving(usage, model, pricing)

            _add_usage(totals, usage, cost)
            totals["tool_calls"] += len(ev["tool_uses"])
            _add_usage(by_model[model], usage, cost)
            by_model[model]["tool_calls"] += len(ev["tool_uses"])
            _add_usage(by_project[project], usage, cost)
            _add_usage(by_agent["subagent" if ev["is_sidechain"] else "main"], usage, cost)
            if ts is not None:
                _add_usage(by_day[ts.date().isoformat()], usage, cost)

            s = sessions.setdefault(ev["session"], _new_session(ev, project))
            _add_usage(s, usage, cost)
            _add_usage(s["by_model"][model], usage, cost)
            s["tool_calls"] += len(ev["tool_uses"])
            s["models"].add(model)
            if ts is not None:
                s["first"] = min(s["first"], ts) if s["first"] else ts
                s["last"] = max(s["last"], ts) if s["last"] else ts

            if ev["is_sidechain"]:
                # サブエージェント内部：メインのタイムラインには混ぜず、親会話の当該
                # Agent 呼び出しへ消費（トークン/コスト/内部ツール）を寄せて可視化する。
                stype = ev.get("subagent_type") or "?"
                _add_usage(subtype_usage[stype], usage, cost)
                subtype_usage[stype]["tool_calls"] += len(ev["tool_uses"])
                entry = s["_tl_index"].get(ev.get("parent_tool_use_id"))
                if entry is not None:
                    entry["sub_input"] = entry.get("sub_input", 0) + (usage.get("input_tokens", 0) or 0)
                    entry["sub_output"] = entry.get("sub_output", 0) + (usage.get("output_tokens", 0) or 0)
                    entry["sub_cost"] = entry.get("sub_cost", 0.0) + (cost or 0.0)
                    entry["sub_cost_known"] = entry.get("sub_cost_known", True) and (cost is not None)
                    entry["sub_msgs"] = entry.get("sub_msgs", 0) + 1
                    entry["sub_model"] = model
                    # 内部ログ（meta.json 由来）の agentType を確実な種別として親呼び出しに
                    # 逆伝播する。呼び出し側が subagent_type を省略していると親側は None
                    # （"?"）になるため、こちらを優先して種別を確定・トークンと結合する。
                    if ev.get("subagent_type"):
                        entry["sub_type"] = ev["subagent_type"]
                    st = entry.setdefault("sub_tools", {})
                    for tu in ev["tool_uses"]:
                        st[tu["name"]] = st.get(tu["name"], 0) + 1
            else:
                # ツール実行の時系列（Agent 呼び出しはメタ付きで強調できるようにする）
                for tu in ev["tool_uses"]:
                    if len(s["timeline"]) >= MAX_TIMELINE:
                        s["timeline_truncated"] = True
                        break
                    entry = {
                        "ts": ts.isoformat() if ts else None,
                        "tool": tu["name"],
                        "model": model,
                        "seconds": None,
                        "is_error": False,
                    }
                    if tu.get("meta"):
                        entry["subagent_type"] = tu["meta"].get("subagent_type")
                        entry["description"] = tu["meta"].get("description")
                    s["timeline"].append(entry)
                    if tu.get("id"):
                        s["_tl_index"][tu["id"]] = entry

        elif ev["kind"] == "tool_time":
            t = by_tool[ev["tool"]]
            t["calls"] += 1
            t["seconds"] += ev["seconds"]
            if ev["is_error"]:
                t["errors"] += 1
            # サブエージェント内部ツールの時間は親の Agent 呼び出し（ラッパ）の所要時間に
            # 内包されるため、実働(active)合計には足さない（二重計上を避ける）。ツール別の
            # 内訳表には出す。
            if not ev.get("is_sidechain"):
                active_total += ev["seconds"]
            s = sessions.get(ev["session"])
            if s:
                if not ev.get("is_sidechain"):
                    s["active_seconds"] += ev["seconds"]
                entry = s["_tl_index"].get(ev.get("tool_use_id"))
                if entry is not None:
                    entry["seconds"] = round(ev["seconds"], 1)
                    entry["is_error"] = ev["is_error"]

    return _finalize(
        totals, by_model, by_project, by_day, by_agent, by_tool,
        sessions, meta, unknown_models, cache_savings, active_total,
        pricing, claude_dir, args, since, until, subtype_usage,
    )


def _round_bucket(b: dict) -> dict:
    b = dict(b)
    b["cost_usd"] = round(b["cost_usd"], 4)
    return b


def _finalize(totals, by_model, by_project, by_day, by_agent, by_tool,
              sessions, meta, unknown_models, cache_savings, active_total, pricing, claude_dir,
              args, since, until, subtype_usage) -> dict:
    duration = 0.0
    first_all = last_all = None
    conversations = []
    # 呼出回数・所要時間は親のタイムライン（Agent 項目）から、内部トークン・コストは
    # subtype_usage（subagents/*.jsonl 由来）から集める。
    sub_by_type: dict[str, dict] = defaultdict(lambda: {"calls": 0, "seconds": 0.0})
    for sid, s in sessions.items():
        for e in s["timeline"]:
            if e["tool"] in ("Agent", "Task"):
                # 種別は内部ログ由来の sub_type（meta.json の agentType）を優先し、無ければ
                # 呼び出し側の subagent_type にフォールバックする。こうすると calls/seconds
                # と内部トークン（subtype_usage は agentType でキー化）が同じキーで結合され、
                # 「subagent_type 省略で呼ばれた Agent」が "?"・コスト 0 に化けない。
                tt = e.get("sub_type") or e.get("subagent_type") or "?"
                sub_by_type[tt]["calls"] += 1
                if e.get("seconds"):
                    sub_by_type[tt]["seconds"] += e["seconds"]
        if s["first"] and s["last"]:
            span = (s["last"] - s["first"]).total_seconds()
            s["duration_seconds"] = span
            duration += span
            first_all = s["first"] if first_all is None else min(first_all, s["first"])
            last_all = s["last"] if last_all is None else max(last_all, s["last"])
        m = meta.get(sid, {})
        models_detail = [
            {"model": name, **_round_bucket(b)}
            for name, b in sorted(
                s["by_model"].items(), key=lambda kv: kv[1]["cost_usd"], reverse=True
            )
        ]
        agent_calls = sum(1 for e in s["timeline"] if e["tool"] in ("Agent", "Task"))
        conversations.append({
            "session": sid,
            "title": m.get("title"),
            "last_prompt": (m.get("last_prompt") or "").strip() or None,
            "project": s["project"],
            "models": sorted(s["models"]),
            "models_detail": models_detail,
            "messages": s["messages"],
            "tool_calls": s["tool_calls"],
            "agent_calls": agent_calls,
            "cost_usd": round(s["cost_usd"], 4),
            "cost_known": s["cost_known"],
            "duration_seconds": round(s.get("duration_seconds", 0.0), 1),
            "active_seconds": round(s["active_seconds"], 1),
            "started": s["first"].isoformat() if s["first"] else None,
            "timeline": s["timeline"],
            "timeline_truncated": s["timeline_truncated"],
        })
    conversations.sort(key=lambda r: r["cost_usd"], reverse=True)
    # タイムラインは肥大化するため、表示対象（コスト上位 limit 件）だけ残す。
    limit = len(conversations) if args.top in (0, None) else args.top
    for i, c in enumerate(conversations):
        if i >= limit:
            c["timeline"] = None

    tools_out = [
        {
            "tool": name,
            "calls": t["calls"],
            "errors": t["errors"],
            "total_seconds": round(t["seconds"], 1),
            "avg_seconds": round(t["seconds"] / t["calls"], 2) if t["calls"] else 0.0,
        }
        for name, t in by_tool.items()
    ]
    tools_out.sort(key=lambda r: r["total_seconds"], reverse=True)

    return {
        "schema": "agent-usage/1",
        "agent": "claude-code",
        "source_dir": str(claude_dir),
        "project_grouping": "git-root",
        "pricing_version": pricing.get("version"),
        "pricing_verified": pricing.get("verified", False),
        "filters": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "days": args.days,
            "project": args.project,
        },
        "range": {
            "first": first_all.isoformat() if first_all else None,
            "last": last_all.isoformat() if last_all else None,
        },
        "totals": {
            **_round_bucket(totals),
            "sessions": len(sessions),
            "duration_seconds": round(duration, 1),
            "active_seconds": round(active_total, 1),
            "cache_savings_usd": round(cache_savings, 4),
            "unknown_models": sorted(unknown_models),
        },
        "by_model": _sorted_map(by_model, _round_bucket, key="model"),
        "by_project": _sorted_map(by_project, _round_bucket, key="project"),
        "by_agent": {k: _round_bucket(v) for k, v in by_agent.items()},
        "subagents": {
            "total_calls": sum(v["calls"] for v in sub_by_type.values()),
            "total_seconds": round(sum(v["seconds"] for v in sub_by_type.values()), 1),
            # 新しい Claude Code は subagents/*.jsonl に内部を分離記録するため、内部の
            # トークン/コストも計上できる（by_agent.subagent に入る）。古いログ（分離
            # ファイルなし）では未記録となり tokens_recorded が False になる。
            "tokens_recorded": by_agent["subagent"]["messages"] > 0,
            "internal_input": by_agent["subagent"]["input"],
            "internal_output": by_agent["subagent"]["output"],
            "internal_cost_usd": round(by_agent["subagent"]["cost_usd"], 4),
            "by_type": sorted(
                ({"subagent_type": t, "calls": v["calls"],
                  "total_seconds": round(v["seconds"], 1),
                  "avg_seconds": round(v["seconds"] / v["calls"], 1) if v["calls"] else 0.0,
                  "messages": subtype_usage.get(t, {}).get("messages", 0),
                  "input": subtype_usage.get(t, {}).get("input", 0),
                  "output": subtype_usage.get(t, {}).get("output", 0),
                  "cost_usd": round(subtype_usage.get(t, {}).get("cost_usd", 0.0), 4),
                  "cost_known": subtype_usage.get(t, {}).get("cost_known", True)}
                 for t, v in sub_by_type.items()),
                key=lambda r: r["calls"], reverse=True,
            ),
        },
        "by_day": [
            {"date": d, **_round_bucket(by_day[d])} for d in sorted(by_day)
        ],
        "by_tool": tools_out,
        "conversation_count": len(conversations),
        "conversations": conversations,
    }


def _sorted_map(m: dict, round_fn, key: str) -> list:
    rows = [{key: name, **round_fn(b)} for name, b in m.items()]
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)
    return rows


MAX_TIMELINE = 500  # 1 会話あたりのタイムライン最大イベント数（肥大化防止）


def _new_session(ev: dict, project: str) -> dict:
    b = _empty_bucket()
    b.update({
        "project": project,
        "models": set(),
        "first": None,
        "last": None,
        "active_seconds": 0.0,
        "by_model": defaultdict(_empty_bucket),
        "timeline": [],
        "timeline_truncated": False,
        "_tl_index": {},
    })
    return b


_ROOT_CACHE: dict[str, str] = {}


def _project_label(ev: dict) -> str:
    """プロジェクト名を決める。cwd を上方探索して .git を持つ最上位（Git リポジトリ
    ルート）の basename を使い、サブフォルダ（.contextdb / out 等）での作業が同じ
    リポジトリに集約されるようにする。.git が見つからない（リポ外・パス消失・別マシンの
    ログ）場合は cwd の basename にフォールバックする。"""
    cwd = ev.get("cwd") or ""
    if not cwd:
        return ev.get("project", "?")
    cached = _ROOT_CACHE.get(cwd)
    if cached is not None:
        return cached
    label = _git_root_label(cwd)
    _ROOT_CACHE[cwd] = label
    return label


def _git_root_label(cwd: str) -> str:
    try:
        p = Path(cwd)
        for cand in (p, *p.parents):
            # .git はディレクトリ（通常）/ファイル（worktree・submodule）の両方があり得る
            if (cand / ".git").exists():
                return cand.name or str(cand)
    except OSError:
        pass
    return Path(cwd).name or cwd


def _parse_day(value: str | None, end: bool = False):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"日付の書式が不正です: {value} (YYYY-MM-DD)")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    if end:
        d = d.replace(hour=23, minute=59, second=59)
    return d


def _write_ledger(path: Path, conversations: list[dict]) -> None:
    """全会話台帳を CSV で出力（表計算で開ける・cost 降順）。"""
    cols = [
        ("title", "タイトル"), ("project", "プロジェクト"), ("models", "モデル"),
        ("messages", "メッセージ"), ("tool_calls", "ツール呼出"),
        ("cost_usd", "コストUSD"), ("duration_seconds", "経過秒"),
        ("active_seconds", "実働秒"), ("started", "開始"),
        ("session", "セッションID"), ("last_prompt", "最終指示"),
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([label for _, label in cols])
        for c in conversations:
            row = []
            for key, _ in cols:
                v = c.get(key)
                if key == "models":
                    v = " / ".join(v or [])
                elif key == "title" and not v:
                    v = (c.get("last_prompt") or "")[:40] or c["session"][:8]
                elif key == "last_prompt" and v:
                    v = v.replace("\n", " ")[:200]
                row.append("" if v is None else v)
            w.writerow(row)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="agent-usage report", add_help=True)
    ap.add_argument("--agent", choices=["claude-code", "copilot"], default="claude-code",
                    help="集計対象のエージェント（既定: claude-code）")
    # Claude Code 用
    ap.add_argument("--claude-dir")
    ap.add_argument("--pricing")
    # GitHub Copilot 用（Agent Debug Log = VS Code workspaceStorage）
    ap.add_argument("--storage-root", help="[copilot] VS Code workspaceStorage ルートを上書き")
    ap.add_argument("--workspace", help="[copilot] 対象ワークスペースのフォルダパス"
                    "（未指定なら debug-logs を持つ全ワークスペースを集計）")
    ap.add_argument("--workspace-id", help="[copilot] workspaceStorage の id を直接指定")
    # 共通
    ap.add_argument("--out", default="agent-usage-out")
    ap.add_argument("--days", type=int)
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--project")
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args(argv)

    if args.agent == "copilot":
        import copilot_collect  # noqa: E402
        summary = copilot_collect.build_summary(args)
    else:
        summary = build_summary(args)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "summary.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {json_path}")

    csv_path = out_dir / "conversations.csv"
    _write_ledger(csv_path, summary["conversations"])
    print(f"wrote {csv_path}")

    if not args.json_only:
        html_path = out_dir / "report.html"
        html_path.write_text(render.render_html(summary, top=args.top), encoding="utf-8")
        print(f"wrote {html_path}")

    t = summary["totals"]
    unit = summary.get("cost_unit", "USD")
    cost_str = f"${t['cost_usd']:.2f}" if unit == "USD" else f"{t['cost_usd']:.2f} AIU"
    print(
        f"  sessions={t['sessions']}  messages={t['messages']}  "
        f"tokens(in/out)={t['input']:,}/{t['output']:,}  "
        f"cost={cost_str}"
        + ("" if t["cost_known"] else f" (+unknown models: {', '.join(t['unknown_models'])})")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
