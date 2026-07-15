# -*- coding: utf-8 -*-
"""GitHub Copilot Chat の Agent Debug Log を読み、agent-usage の集計スキーマ（AIU 版）へ
正規化するコレクタ。

ログの出どころ（Windows 既定）:
    %APPDATA%/Code/User/workspaceStorage/<workspaceId>/
        GitHub.copilot-chat/debug-logs/<sessionId>/{main.jsonl, models.json}

Copilot は VS Code の設定「Agent Debug Log を有効化」時のみこのログを出す。1 セッション
1 ディレクトリで、``main.jsonl`` に 1 イベント 1 行の JSONL（``session_start`` /
``turn_start`` / ``user_message`` / ``llm_request`` / ``tool_call`` / ``child_session_ref``
/ ``agent_response`` …）が並ぶ。各 ``llm_request`` の ``attrs`` に model / debugName /
inputTokens / outputTokens / cachedTokens と **実測 AIU（``copilotUsageNanoAiu``。1 AIU =
1e9 nano）** が入っている。

**コストは GitHub AI Credits（1 credit = $0.01）で表す**。消費量は ``copilotUsageNanoAiu``
（1 credit = 1 AIU = 1e9 nano）に実測記録されるので、``NANO_PER_AIU`` で割って credit に
換算し、これを正本のコスト値とする。``models.json`` の単価から算出した推定 credit と、
キャッシュ読込による節約 credit を補助（クロスチェック・削減効果）として併記する。

サブエージェント（``child_session_ref`` が指す ``title-*.jsonl`` / 子セッションログ）は
親セッションの ``main.jsonl`` と同じディレクトリに置かれ、``include_children`` で本体総計へ
畳み込みつつ ``subagents`` に内訳を出す。

Claude 版 collect.py と同じ ``summary`` スキーマ（totals / by_model / by_project / by_day /
by_agent / by_tool / conversations …）を返すので、report.py / render.py をそのまま共有できる。
第三者依存なし・標準ライブラリのみ。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote

NANO_PER_AIU = 1_000_000_000
# コスト表示は GitHub AI Credits（1 credit = $0.01）で行う。GitHub の実測
# ``copilotUsageNanoAiu`` は 1 credit = 1 AIU = 1e9 nano で記録されるので、nano から
# credit へは ÷NANO_PER_AIU で換算する（models.json の単価も credit 単位）。
MAX_TIMELINE = 500  # 1 会話あたりのタイムライン最大イベント数（肥大化防止）


# ---------------------------------------------------------------------------
# パス / ワークスペース解決（参考プログラムと同一のエンコード規則）
# ---------------------------------------------------------------------------
def default_storage_root() -> Path:
    """VS Code Stable の workspaceStorage ルート（Win/mac/Linux）。"""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if not base:
            raise SystemExit("APPDATA 環境変数が設定されていません")
        return Path(base) / "Code" / "User" / "workspaceStorage"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / "workspaceStorage"
    return Path.home() / ".config" / "Code" / "User" / "workspaceStorage"


def path_to_vscode_folder_uri(path: Path) -> str:
    """VS Code の ``folder`` URI エンコード（例: file:///c%3A/xxx）を再現する。"""
    resolved = str(path.resolve()).replace("\\", "/")
    if len(resolved) >= 2 and resolved[1] == ":":
        resolved = f"{resolved[0]}%3A{resolved[2:]}"
    return f"file:///{resolved}"


def resolve_workspace_id(storage_root: Path, workspace_path: Path) -> str:
    """workspace_path に対応する workspaceStorage サブディレクトリ名を探す。"""
    target = path_to_vscode_folder_uri(workspace_path).lower()
    if not storage_root.is_dir():
        raise SystemExit(f"workspaceStorage が見つかりません: {storage_root}")
    for entry in storage_root.iterdir():
        folder = _read_workspace_folder(entry)
        if folder and folder.lower() == target:
            return entry.name
    raise SystemExit(
        f"{workspace_path} に対応する workspaceStorage id を特定できません。"
        f"--workspace-id を明示してください。"
    )


def debug_logs_dir(storage_root: Path, workspace_id: str) -> Path:
    return storage_root / workspace_id / "GitHub.copilot-chat" / "debug-logs"


def _read_workspace_folder(entry: Path) -> Optional[str]:
    ws_json = entry / "workspace.json"
    if not ws_json.is_file():
        return None
    try:
        data = json.loads(ws_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    folder = data.get("folder")
    return folder if isinstance(folder, str) else None


def _project_from_entry(entry: Path) -> str:
    """workspace.json の folder URI から人間可読なプロジェクト名（末尾セグメント）を得る。
    取れなければ workspaceStorage の id（ハッシュ）にフォールバックする。"""
    folder = _read_workspace_folder(entry)
    if folder:
        decoded = unquote(folder)
        name = decoded.rstrip("/").rsplit("/", 1)[-1]
        if name:
            return name
    return entry.name


# ---------------------------------------------------------------------------
# 単価（models.json） — 推定 AIU の算出に使う（実測 AIU があるので補助扱い）
# ---------------------------------------------------------------------------
def load_model_prices(session_dir: Path) -> Dict[str, Dict[str, float]]:
    models_path = session_dir / "models.json"
    prices: Dict[str, Dict[str, float]] = {}
    if not models_path.is_file():
        return prices
    try:
        raw = json.loads(models_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return prices
    if not isinstance(raw, list):
        return prices
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        billing = entry.get("billing") or {}
        token_prices = billing.get("token_prices") or {}
        default = token_prices.get("default") or {}
        if not isinstance(model_id, str) or not default:
            continue
        batch = token_prices.get("batch_size")
        prices[model_id] = {
            "input_price": float(default.get("input_price", 0)),
            "output_price": float(default.get("output_price", 0)),
            "cache_price": float(default.get("cache_price", 0)),
            "cache_write_price": float(default.get("cache_write_price", 0)),
            "batch_size": float(batch) if isinstance(batch, (int, float)) and batch else 1_000_000.0,
        }
    return prices


def estimate_all_cw_nano_aiu(
    input_tokens: int, output_tokens: int, cached_tokens: int,
    price: Optional[Dict[str, float]],
) -> Tuple[int, float]:
    """(cache_write 推定トークン, 推定 nano_aiu) を返す。

    フレッシュ入力の単価は適応的に選ぶ:
      * cache_write_price > 0（例: Copilot 上の Claude Opus 4.7）なら cache_write 単価
        （Copilot 請求と ~0.01% 一致）。
      * cache_write_price == 0（例: GPT-5.x）なら input 単価（gpt-5.5 で厳密一致）。
    """
    cache_write = max(0, input_tokens - cached_tokens)
    if not price:
        return cache_write, 0.0
    batch = price["batch_size"] or 1_000_000.0
    fresh_input_price = (
        price["cache_write_price"] if price["cache_write_price"] > 0 else price["input_price"]
    )
    per_batch_aiu = (
        cached_tokens * price["cache_price"]
        + cache_write * fresh_input_price
        + output_tokens * price["output_price"]
    ) / batch
    return cache_write, per_batch_aiu * NANO_PER_AIU


def cache_saving_nano(cached_tokens: int, price: Optional[Dict[str, float]]) -> float:
    """キャッシュ読込で節約できた nano_aiu を返す。キャッシュが無ければ満額（フレッシュ入力
    単価）で課金されるので、``cached_tokens ×（フレッシュ入力単価 − キャッシュ読込単価）`` が
    節約分。フレッシュ入力単価は estimate_all_cw_nano_aiu と同じ選び方（cache_write_price>0
    ならそれ、0 なら input_price）にする。単価不明・キャッシュ無しなら 0。"""
    if not price or cached_tokens <= 0:
        return 0.0
    batch = price["batch_size"] or 1_000_000.0
    fresh_input_price = (
        price["cache_write_price"] if price["cache_write_price"] > 0 else price["input_price"]
    )
    saved_per_batch = cached_tokens * (fresh_input_price - price["cache_price"])
    return max(0.0, saved_per_batch / batch) * NANO_PER_AIU


# ---------------------------------------------------------------------------
# JSONL 読み込み / セッションタイトル
# ---------------------------------------------------------------------------
def iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def load_session_title(session_dir: Path) -> Optional[str]:
    """title-*.jsonl から自動生成タイトルを取り出す（無ければ None）。

    タイトルは要約子の出力で、``agent_response`` イベントの ``attrs.response``（JSON 文字列）
    の中の text パートに入っている。"""
    for tf in sorted(session_dir.glob("title-*.jsonl")):
        title: Optional[str] = None
        for entry in iter_jsonl(tf):
            if entry.get("type") != "agent_response":
                continue
            resp = (entry.get("attrs") or {}).get("response")
            if not isinstance(resp, str):
                continue
            try:
                parsed = json.loads(resp)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, list):
                continue
            for msg in parsed:
                if not isinstance(msg, dict):
                    continue
                for part in msg.get("parts") or []:
                    if (isinstance(part, dict) and part.get("type") == "text"
                            and isinstance(part.get("content"), str) and part["content"].strip()):
                        title = part["content"].strip()
        if title:
            return title
    return None


# ---------------------------------------------------------------------------
# バケット / 1 ログの取り込み（参考プログラムの _ingest_jsonl 相当）
# ---------------------------------------------------------------------------
def _empty_model_bucket() -> Dict[str, Any]:
    return {"requests": 0, "inputTokens": 0, "outputTokens": 0, "cachedTokens": 0,
            "cacheWriteTokensEst": 0, "copilotUsageNanoAiu": 0, "estimatedNanoAiu": 0.0,
            "savingsNanoAiu": 0.0, "hasPrice": False}


def _empty_tool_bucket() -> Dict[str, Any]:
    return {"invocations": 0, "okCount": 0, "errorCount": 0, "totalDurationMs": 0}


def _empty_agent_bucket() -> Dict[str, Any]:
    return {"requests": 0, "inputTokens": 0, "outputTokens": 0, "cachedTokens": 0,
            "cacheWriteTokensEst": 0, "copilotUsageNanoAiu": 0, "estimatedNanoAiu": 0.0,
            "savingsNanoAiu": 0.0, "models": set()}


def _ingest_jsonl(main_path: Path, prices: Dict[str, Dict[str, float]],
                  per_model: Dict[str, Dict[str, Any]], per_tool: Dict[str, Dict[str, Any]],
                  per_agent: Dict[str, Dict[str, Any]], missing_prices: set,
                  timeline: Optional[List[Dict[str, Any]]], source: str) -> Dict[str, Any]:
    """1 つの JSONL ログを走査し共有バケットへ集計する。``source`` は "main" か
    "child:<label>" で、タイムライン項目のタグと agent キーの修飾に使う。"""
    first_ts: Optional[int] = None
    last_ts: Optional[int] = None
    session_start_ts: Optional[int] = None
    copilot_version: Optional[str] = None
    vscode_version: Optional[str] = None
    child_refs: List[Dict[str, Any]] = []
    last_user_msg: Optional[str] = None

    for entry in iter_jsonl(main_path):
        event_type = entry.get("type")
        if not isinstance(event_type, str):
            continue
        ts = entry.get("ts")
        if isinstance(ts, int):
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        attrs = entry.get("attrs") or {}
        if not isinstance(attrs, dict):
            attrs = {}

        if event_type == "session_start":
            if session_start_ts is None and isinstance(ts, int):
                session_start_ts = ts
            if isinstance(attrs.get("copilotVersion"), str):
                copilot_version = attrs["copilotVersion"]
            if isinstance(attrs.get("vscodeVersion"), str):
                vscode_version = attrs["vscodeVersion"]
            continue

        if event_type == "user_message":
            if isinstance(attrs.get("content"), str):
                last_user_msg = attrs["content"]
            continue

        if event_type == "child_session_ref":
            child_refs.append({
                "childSessionId": attrs.get("childSessionId"),
                "childLogFile": attrs.get("childLogFile"),
                "label": attrs.get("label"),
                "ts": ts if isinstance(ts, int) else None,
                "iso": _iso(ts) if isinstance(ts, int) else None,
            })
            continue

        if event_type == "llm_request":
            model = attrs.get("model") or "(unknown)"
            debug_name = attrs.get("debugName") or "(unknown)"
            input_tokens = int(attrs.get("inputTokens") or 0)
            output_tokens = int(attrs.get("outputTokens") or 0)
            cached_tokens = int(attrs.get("cachedTokens") or 0)
            copilot_nano = int(attrs.get("copilotUsageNanoAiu") or 0)
            price = prices.get(model)
            if price is None:
                missing_prices.add(model)
            cache_write, est_nano = estimate_all_cw_nano_aiu(
                input_tokens, output_tokens, cached_tokens, price)
            saving_nano = cache_saving_nano(cached_tokens, price)

            m = per_model[model]
            m["requests"] += 1
            m["inputTokens"] += input_tokens
            m["outputTokens"] += output_tokens
            m["cachedTokens"] += cached_tokens
            m["cacheWriteTokensEst"] += cache_write
            m["copilotUsageNanoAiu"] += copilot_nano
            m["estimatedNanoAiu"] += est_nano
            m["savingsNanoAiu"] += saving_nano
            m["hasPrice"] = m["hasPrice"] or (price is not None)

            agent_key = debug_name if source == "main" else f"{source}::{debug_name}"
            a = per_agent[agent_key]
            a["requests"] += 1
            a["inputTokens"] += input_tokens
            a["outputTokens"] += output_tokens
            a["cachedTokens"] += cached_tokens
            a["cacheWriteTokensEst"] += cache_write
            a["copilotUsageNanoAiu"] += copilot_nano
            a["estimatedNanoAiu"] += est_nano
            a["savingsNanoAiu"] += saving_nano
            a["models"].add(model)
            continue

        if event_type == "tool_call":
            name = entry.get("name") or "(unknown)"
            status = entry.get("status") or ""
            dur = entry.get("dur") or 0
            t = per_tool[name]
            t["invocations"] += 1
            if status == "ok":
                t["okCount"] += 1
            else:
                t["errorCount"] += 1
            if isinstance(dur, (int, float)):
                t["totalDurationMs"] += int(dur)
            if timeline is not None and len(timeline) < MAX_TIMELINE:
                timeline.append({
                    "ts": _iso(ts) if isinstance(ts, int) else None,
                    "tool": name, "model": None,
                    "seconds": round(dur / 1000.0, 1) if isinstance(dur, (int, float)) and dur else None,
                    "is_error": status != "ok",
                })
            continue

    return {
        "firstTs": first_ts, "lastTs": last_ts, "sessionStartTs": session_start_ts,
        "copilotVersion": copilot_version, "vscodeVersion": vscode_version,
        "childRefs": child_refs, "lastUserMsg": last_user_msg,
    }


def _iso(ts_ms: Optional[int]) -> Optional[str]:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()


def _dt(ts_ms: Optional[int]) -> Optional[datetime]:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)


def _fold_model(dst: Dict[str, Any], src: Dict[str, Dict[str, Any]]) -> None:
    for k, v in src.items():
        d = dst[k]
        for key in ("requests", "inputTokens", "outputTokens", "cachedTokens",
                    "cacheWriteTokensEst", "copilotUsageNanoAiu", "estimatedNanoAiu",
                    "savingsNanoAiu"):
            d[key] += v[key]
        d["hasPrice"] = d["hasPrice"] or v["hasPrice"]


def _fold_tool(dst: Dict[str, Any], src: Dict[str, Dict[str, Any]]) -> None:
    for k, v in src.items():
        d = dst[k]
        for key in ("invocations", "okCount", "errorCount", "totalDurationMs"):
            d[key] += v[key]


# ---------------------------------------------------------------------------
# 1 セッションの要約（main + 子ログ）
# ---------------------------------------------------------------------------
def _summarize_session(session_dir: Path, emit_timeline: bool) -> Dict[str, Any]:
    """1 つの debug-log セッションを集計し、集計に必要な素材一式を返す。

    main.jsonl を本体として読み、``child_session_ref`` が指す子ログ（タイトル生成・
    サブエージェント）も読んで **子は subagent として** 分離集計しつつ、本体総計へは
    畳み込む（Claude 版の main/subagent 分割・sidechain を総計へ算入する挙動に対応）。"""
    prices = load_model_prices(session_dir)
    title = load_session_title(session_dir)

    per_model: Dict[str, Dict[str, Any]] = defaultdict(_empty_model_bucket)
    per_tool: Dict[str, Dict[str, Any]] = defaultdict(_empty_tool_bucket)
    per_agent: Dict[str, Dict[str, Any]] = defaultdict(_empty_agent_bucket)
    missing: set = set()
    timeline: List[Dict[str, Any]] = [] if emit_timeline else None

    meta = _ingest_jsonl(session_dir / "main.jsonl", prices, per_model, per_tool,
                         per_agent, missing, timeline, source="main")

    main_tool_seconds = sum(t["totalDurationMs"] for t in per_tool.values()) / 1000.0

    # combined_tool は main + 子の全ツール実行を合算（by_tool 用。Claude 版が sidechain の
    # ツール時間も by_tool に載せるのに合わせる）。active_seconds は main のみで数える。
    combined_tool: Dict[str, Dict[str, Any]] = defaultdict(_empty_tool_bucket)
    _fold_tool(combined_tool, per_tool)

    # ---- 子ログ（サブエージェント）を分離集計しつつ本体へ畳み込む ----
    sub_by_label: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "requests": 0, "input": 0, "output": 0,
                 "reported_nano": 0, "est_nano": 0.0, "seconds": 0.0})
    sub_model: Dict[str, Dict[str, Any]] = defaultdict(_empty_model_bucket)
    for ref in meta["childRefs"]:
        cf = ref.get("childLogFile")
        if not isinstance(cf, str):
            continue
        cpath = session_dir / cf
        if not cpath.is_file():
            continue
        label = ref.get("label") or "child"
        cmodel: Dict[str, Dict[str, Any]] = defaultdict(_empty_model_bucket)
        ctool: Dict[str, Dict[str, Any]] = defaultdict(_empty_tool_bucket)
        cagent: Dict[str, Dict[str, Any]] = defaultdict(_empty_agent_bucket)
        cmeta = _ingest_jsonl(cpath, prices, cmodel, ctool, cagent, missing,
                              None, source=f"child:{label}")
        _fold_model(sub_model, cmodel)
        _fold_tool(combined_tool, ctool)
        b = sub_by_label[label]
        b["calls"] += 1
        b["requests"] += sum(m["requests"] for m in cmodel.values())
        b["input"] += sum(m["inputTokens"] for m in cmodel.values())
        b["output"] += sum(m["outputTokens"] for m in cmodel.values())
        b["reported_nano"] += sum(m["copilotUsageNanoAiu"] for m in cmodel.values())
        b["est_nano"] += sum(m["estimatedNanoAiu"] for m in cmodel.values())
        if cmeta["firstTs"] and cmeta["lastTs"]:
            b["seconds"] += (cmeta["lastTs"] - cmeta["firstTs"]) / 1000.0
        # child_session_ref をタイムラインに Agent 項目として差し込む
        if timeline is not None and len(timeline) < MAX_TIMELINE:
            timeline.append({
                "ts": ref.get("iso"), "tool": "Agent",
                "subagent_type": label, "description": "",
                "seconds": round((cmeta["lastTs"] - cmeta["firstTs"]) / 1000.0, 1)
                if (cmeta["firstTs"] and cmeta["lastTs"]) else None,
                "sub_msgs": sum(m["requests"] for m in cmodel.values()),
                "sub_input": sum(m["inputTokens"] for m in cmodel.values()),
                "sub_output": sum(m["outputTokens"] for m in cmodel.values()),
                "sub_cost": sum(m["copilotUsageNanoAiu"] for m in cmodel.values()) / NANO_PER_AIU,
                "sub_cost_known": True,
            })

    # 本体へ子を畳み込んで「combined」を作る（totals/by_model は combined 基準）
    combined_model: Dict[str, Dict[str, Any]] = defaultdict(_empty_model_bucket)
    _fold_model(combined_model, per_model)
    _fold_model(combined_model, sub_model)

    if timeline is not None:
        timeline.sort(key=lambda e: (e.get("ts") is None, e.get("ts") or ""))

    first_ts = meta["sessionStartTs"] if meta["sessionStartTs"] is not None else meta["firstTs"]
    last_ts = meta["lastTs"]

    return {
        "session": session_dir.name,
        "title": title,
        "last_prompt": meta["lastUserMsg"],
        "combined_model": combined_model,
        "main_model": per_model,
        "sub_model": sub_model,
        "combined_tool": combined_tool,   # main + 子（by_tool / tool_calls 用）
        "main_tool_seconds": main_tool_seconds,  # main のみ（active_seconds 用）
        "combined_tool_calls": sum(t["invocations"] for t in combined_tool.values()),
        "sub_by_label": sub_by_label,
        "child_count": len(sub_by_label) and sum(b["calls"] for b in sub_by_label.values()),
        "agent_calls": sum(b["calls"] for b in sub_by_label.values()),
        "first_ts": first_ts,
        "last_ts": last_ts,
        "copilot_version": meta["copilotVersion"],
        "vscode_version": meta["vscodeVersion"],
        "timeline": timeline,
        "missing_prices": missing,
    }


# ---------------------------------------------------------------------------
# summary（agent-usage スキーマ・credit 版）
# ---------------------------------------------------------------------------
def _bucket() -> Dict[str, Any]:
    return {"messages": 0, "tool_calls": 0, "input": 0, "output": 0, "cache_read": 0,
            "cache_write": 0, "cost_usd": 0.0, "cost_known": True}


# _bucket は tool_calls キーを持つ（totals/by_model が Claude 版と同形になるよう合わせる）。


def _add_model(bucket: Dict[str, Any], mb: Dict[str, Any]) -> None:
    bucket["messages"] += mb["requests"]
    bucket["input"] += mb["inputTokens"]
    bucket["output"] += mb["outputTokens"]
    bucket["cache_read"] += mb["cachedTokens"]
    bucket["cache_write"] += mb["cacheWriteTokensEst"]
    bucket["cost_usd"] += mb["copilotUsageNanoAiu"] / NANO_PER_AIU


def _round_bucket(b: Dict[str, Any]) -> Dict[str, Any]:
    b = dict(b)
    b["cost_usd"] = round(b["cost_usd"], 4)
    return b


def _sorted_map(m: Dict[str, Dict[str, Any]], key: str) -> list:
    rows = [{key: name, **_round_bucket(b)} for name, b in m.items()]
    rows.sort(key=lambda r: r["cost_usd"], reverse=True)
    return rows


def _workspaces(storage_root: Path, args) -> List[Tuple[str, Path]]:
    """(project 名, debug-logs ディレクトリ) の一覧。--workspace-id / --workspace 指定時は
    その 1 件、未指定なら debug-logs を持つ全ワークスペースを対象にする。"""
    if getattr(args, "workspace_id", None):
        wid = args.workspace_id
        return [(_project_from_entry(storage_root / wid), debug_logs_dir(storage_root, wid))]
    if getattr(args, "workspace", None):
        wid = resolve_workspace_id(storage_root, Path(args.workspace))
        return [(_project_from_entry(storage_root / wid), debug_logs_dir(storage_root, wid))]
    out: List[Tuple[str, Path]] = []
    for entry in sorted(storage_root.iterdir()):
        dbg = entry / "GitHub.copilot-chat" / "debug-logs"
        if dbg.is_dir():
            out.append((_project_from_entry(entry), dbg))
    return out


def _join_sources(dirs: List[str], limit: int = 3) -> str:
    """source ヘッダ用に集計対象フォルダを連結する。複数ある場合は先頭 ``limit`` 件
    （既定 3）までを見せ、残りは「…他 N 件」と省略する（無指定時の全ワークスペース走査で
    大量のパスがヘッダを埋めるのを防ぐ）。"""
    shown = dirs[:limit]
    text = " ; ".join(shown)
    extra = len(dirs) - len(shown)
    if extra > 0:
        text += f" ; …他 {extra} 件"
    return text


def build_summary(args) -> dict:
    """report.py から呼ばれる集計エントリ。Claude 版 report.build_summary と同じスキーマ
    （GitHub AI Credits をコスト値とし cost_unit="credit"）の dict を返す。"""
    from datetime import timedelta

    storage_root = Path(args.storage_root) if getattr(args, "storage_root", None) else default_storage_root()
    if not storage_root.is_dir():
        raise SystemExit(f"VS Code の workspaceStorage が見つかりません: {storage_root}")

    since = _parse_day(args.since)
    until = _parse_day(args.until, end=True)
    if getattr(args, "days", None):
        since = datetime.now(timezone.utc) - timedelta(days=args.days)

    top = getattr(args, "top", 100)

    totals = _bucket()
    est_nano_total = 0.0
    savings_nano_total = 0.0
    by_model: Dict[str, Dict[str, Any]] = defaultdict(_bucket)
    by_project: Dict[str, Dict[str, Any]] = defaultdict(_bucket)
    by_day: Dict[str, Dict[str, Any]] = defaultdict(_bucket)
    by_agent = {"main": _bucket(), "subagent": _bucket()}
    by_tool: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"calls": 0, "errors": 0, "seconds": 0.0})
    subtype: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "seconds": 0.0, "input": 0, "output": 0, "cost_usd": 0.0, "requests": 0})
    conversations: List[dict] = []
    missing_models: set = set()
    duration_total = 0.0
    active_total = 0.0
    first_all = last_all = None
    source_dirs: List[str] = []
    versions: set = set()

    for project, debug_root in _workspaces(storage_root, args):
        if not debug_root.is_dir():
            continue
        if getattr(args, "project", None) and args.project not in project:
            continue
        workspace_used = False  # 実際に 1 件でも集計に寄与したワークスペースだけ source に載せる
        for sess_dir in sorted(debug_root.iterdir()):
            if not (sess_dir / "main.jsonl").is_file():
                continue
            s = _summarize_session(sess_dir, emit_timeline=True)
            sdt = _dt(s["first_ts"])
            if sdt is not None:
                if since and sdt < since:
                    continue
                if until and sdt > until:
                    continue
            if not workspace_used:
                source_dirs.append(str(debug_root))
                workspace_used = True
            missing_models |= s["missing_prices"]
            if s["copilot_version"]:
                versions.add(s["copilot_version"])

            combined = s["combined_model"]
            for mb in combined.values():
                _add_model(totals, mb)
                est_nano_total += mb["estimatedNanoAiu"]
                savings_nano_total += mb["savingsNanoAiu"]
            totals["tool_calls"] += s["combined_tool_calls"]
            for name, mb in combined.items():
                _add_model(by_model[name], mb)
            for mb in combined.values():
                _add_model(by_project[project], mb)
            # main / subagent 分割
            for mb in s["main_model"].values():
                _add_model(by_agent["main"], mb)
            for mb in s["sub_model"].values():
                _add_model(by_agent["subagent"], mb)

            # by_tool（main + 子のツール実行時間・回数）
            for name, t in s["combined_tool"].items():
                bt = by_tool[name]
                bt["calls"] += t["invocations"]
                bt["errors"] += t["errorCount"]
                bt["seconds"] += t["totalDurationMs"] / 1000.0

            # subagents 内訳
            for label, b in s["sub_by_label"].items():
                st = subtype[label]
                st["calls"] += b["calls"]
                st["requests"] += b["requests"]
                st["input"] += b["input"]
                st["output"] += b["output"]
                st["seconds"] += b["seconds"]
                st["cost_usd"] += b["reported_nano"] / NANO_PER_AIU

            # 日次（セッション開始日にロールアップ）
            if sdt is not None:
                day = by_day[sdt.date().isoformat()]
                for mb in combined.values():
                    _add_model(day, mb)

            duration = ((s["last_ts"] - s["first_ts"]) / 1000.0
                        if (s["first_ts"] and s["last_ts"]) else 0.0)
            duration_total += duration
            active_total += s["main_tool_seconds"]
            if sdt is not None:
                first_all = sdt if first_all is None else min(first_all, sdt)
                last_end = _dt(s["last_ts"])
                if last_end is not None:
                    last_all = last_end if last_all is None else max(last_all, last_end)

            models_detail = [
                {"model": name, **_round_bucket(_model_row(mb))}
                for name, mb in sorted(combined.items(),
                                       key=lambda kv: kv[1]["copilotUsageNanoAiu"], reverse=True)
            ]
            reported_credit = sum(mb["copilotUsageNanoAiu"] for mb in combined.values()) / NANO_PER_AIU
            conversations.append({
                "session": s["session"],
                "title": s["title"],
                "last_prompt": (s["last_prompt"] or "").strip() or None,
                "project": project,
                "models": sorted(combined.keys()),
                "models_detail": models_detail,
                "messages": sum(mb["requests"] for mb in combined.values()),
                "tool_calls": s["combined_tool_calls"],
                "agent_calls": s["agent_calls"],
                "cost_usd": round(reported_credit, 4),
                "cost_known": True,
                "duration_seconds": round(duration, 1),
                "active_seconds": round(s["main_tool_seconds"], 1),
                "started": _iso(s["first_ts"]),
                "timeline": s["timeline"],
                "timeline_truncated": False,
            })

    conversations.sort(key=lambda r: r["cost_usd"], reverse=True)
    limit = len(conversations) if top in (0, None) else top
    for i, c in enumerate(conversations):
        if i >= limit:
            c["timeline"] = None

    tools_out = [{
        "tool": name, "calls": t["calls"], "errors": t["errors"],
        "total_seconds": round(t["seconds"], 1),
        "avg_seconds": round(t["seconds"] / t["calls"], 2) if t["calls"] else 0.0,
    } for name, t in by_tool.items()]
    tools_out.sort(key=lambda r: r["total_seconds"], reverse=True)

    return {
        "schema": "agent-usage/1",
        "agent": "copilot",
        "agent_label": "GitHub Copilot",
        "cost_unit": "credit",
        "source_dir": _join_sources(source_dirs) if source_dirs else str(storage_root),
        "project_grouping": "vscode-workspace",
        "pricing_version": None,
        "pricing_verified": True,  # AIU は実測。単価未検証の警告は出さない
        "copilot_versions": sorted(versions),
        "filters": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "days": getattr(args, "days", None),
            "project": getattr(args, "project", None),
        },
        "range": {
            "first": first_all.isoformat() if first_all else None,
            "last": last_all.isoformat() if last_all else None,
        },
        "totals": {
            **_round_bucket(totals),
            "sessions": len(conversations),
            "duration_seconds": round(duration_total, 1),
            "active_seconds": round(active_total, 1),
            "cache_savings_usd": round(savings_nano_total / NANO_PER_AIU, 4),
            "cost_estimated_aiu": round(est_nano_total / NANO_PER_AIU, 4),
            "unknown_models": sorted(missing_models),
        },
        "by_model": _sorted_map(by_model, key="model"),
        "by_project": _sorted_map(by_project, key="project"),
        "by_agent": {k: _round_bucket(v) for k, v in by_agent.items()},
        "subagents": {
            "total_calls": sum(v["calls"] for v in subtype.values()),
            "total_seconds": round(sum(v["seconds"] for v in subtype.values()), 1),
            "tokens_recorded": any(v["requests"] for v in subtype.values()),
            "internal_input": by_agent["subagent"]["input"],
            "internal_output": by_agent["subagent"]["output"],
            "internal_cost_usd": round(by_agent["subagent"]["cost_usd"], 4),
            "by_type": sorted(
                ({"subagent_type": label, "calls": v["calls"],
                  "total_seconds": round(v["seconds"], 1),
                  "avg_seconds": round(v["seconds"] / v["calls"], 1) if v["calls"] else 0.0,
                  "messages": v["requests"], "input": v["input"], "output": v["output"],
                  "cost_usd": round(v["cost_usd"], 4), "cost_known": True}
                 for label, v in subtype.items()),
                key=lambda r: r["calls"], reverse=True),
        },
        "by_day": [{"date": d, **_round_bucket(by_day[d])} for d in sorted(by_day)],
        "by_tool": tools_out,
        "conversation_count": len(conversations),
        "conversations": conversations,
    }


def _model_row(mb: Dict[str, Any]) -> Dict[str, Any]:
    b = _bucket()
    _add_model(b, mb)
    return b


def _parse_day(value: Optional[str], end: bool = False):
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
