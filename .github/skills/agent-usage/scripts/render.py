# -*- coding: utf-8 -*-
"""summary.json から自己完結（単一ファイル・外部依存なし）の report.html を生成する。

CSP や外部取得に依存しない：CSS/データはすべてインラインで埋め込み、モーダル（会話詳細）
も JavaScript を使わず CSS の :target だけで開閉する。デザインは「白背景＋薄い黄土色の
小さな円パターン」のゲーム風で、大事な数字は大きく・補足は小さく、情報の優先度を見た目の
サイズで表す。日次コストは目盛り（Y軸）付きで横スクロールが出ないよう列を可変幅にする。
"""
from __future__ import annotations

import html
import json
import math


def _fmt_int(n) -> str:
    return f"{int(n):,}"


def _fmt_usd(n) -> str:
    return f"${float(n):,.2f}"


def _fmt_usd_compact(n) -> str:
    """軸ラベル用。$1.2 のように短く。"""
    n = float(n)
    if n <= 0:
        return "$0"
    if n >= 100:
        return f"${n:,.0f}"
    if n >= 10:
        return f"${n:.0f}"
    return f"${n:.1f}"


# コストの表示単位。Claude Code は USD、GitHub Copilot は GitHub AI Credits（1 credit=$0.01）。
# render_html の冒頭で summary["cost_unit"] を反映する。既定 USD なら従来と完全一致。
_UNIT = "USD"


def _fmt_cost(n) -> str:
    """コスト値を表示単位で整形。USD は $1,234.56、credit は 1,234.56 credit。"""
    if _UNIT == "credit":
        return f"{float(n):,.2f} credit"
    return _fmt_usd(n)


def _fmt_cost_compact(n) -> str:
    """チャート軸ラベル用の短い表記。"""
    if _UNIT != "credit":
        return _fmt_usd_compact(n)
    n = float(n)
    if n <= 0:
        return "0"
    if n >= 100:
        return f"{n:,.0f}"
    if n >= 10:
        return f"{n:.0f}"
    return f"{n:.1f}"


def _fmt_dur(seconds) -> str:
    seconds = float(seconds or 0)
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}m"
    return f"{seconds/3600:.1f}h"


def _esc(s) -> str:
    return html.escape(str(s))


def _nice_max(v: float) -> float:
    """軸の上限を 1/2/2.5/5/10×10^n の「きりのよい」値に丸める。"""
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10 ** exp
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * base:
            return m * base
    return 10 * base


def render_html(summary: dict, top: int = 100) -> str:
    global _UNIT
    _UNIT = summary.get("cost_unit", "USD")
    agent_name = summary.get("agent_label") or (
        "GitHub Copilot" if summary.get("agent") == "copilot" else "Claude Code")

    t = summary["totals"]
    rng = summary["range"]

    # ---- 注意書き（単価未検証・サブエージェント未計上など）を必要なときだけ ----
    notes = []
    unknown = t.get("unknown_models") or []
    if unknown:
        # credit 版では実測値を採用しているため「推定 credit にのみ影響」と補足する。
        suffix = "（推定 credit にのみ影響）" if _UNIT == "credit" else ""
        notes.append(f'単価未登録のモデル: {_esc(", ".join(unknown))}{suffix}')
    if _UNIT == "USD" and not summary.get("pricing_verified"):
        notes.append("単価表は未検証です（pricing.json を最新の公開価格でご確認ください）")
    sub = summary.get("subagents", {})
    if sub.get("total_calls") and not sub.get("tokens_recorded"):
        notes.append(f'サブエージェント {sub["total_calls"]} 回分の消費は未計上（総コストは過小評価）')
    warn_banner = (
        '<div class="banner">' + " ・ ".join(notes) + "</div>" if notes else ""
    )

    # ---- ヒーロー（総コスト）＋ サブ指標タイル ----
    hero = _hero(t)
    tiles = _tiles(summary, t)

    # ---- 日次コスト（目盛り付き） ----
    day_chart = _day_chart(summary["by_day"])

    # ---- モデル別（複数モデルのときだけ意味があるので単一なら省略気味に） ----
    model_section = _model_section(summary)

    # ---- サブエージェント / プロジェクト ----
    subagent_panel = _subagent_panel(summary)
    proj_rows = "".join(
        _row([
            _esc(p["project"]),
            _fmt_int(p["messages"]),
            _fmt_int(p["input"]),
            _fmt_int(p["output"]),
            _fmt_cost(p["cost_usd"]) + ("" if p["cost_known"] else " *"),
        ])
        for p in summary["by_project"]
    )

    # ---- ツール別 ----
    max_tool = max((x["total_seconds"] for x in summary["by_tool"]), default=1) or 1
    tool_rows = "".join(
        _row([
            _esc(x["tool"]),
            _fmt_int(x["calls"]),
            (f'{x["errors"]}' if x["errors"] else "—"),
            _fmt_dur(x["total_seconds"]),
            _bar(x["total_seconds"], max_tool),
        ])
        for x in summary["by_tool"]
    )

    # ---- 会話別（表 ＋ 詳細ポップアップ） ----
    convs = summary.get("conversations", [])
    conv_rows = "".join(_conv_row(c) for c in convs)
    conv_modals = "".join(_conv_modal(c) for c in convs)
    total_conv = summary.get("conversation_count", len(convs))
    conv_note = f"全 {total_conv} 会話（コスト降順）・詳細アイコンで内訳を表示"

    subtitle = f'{_esc((rng.get("first") or "?")[:10])} 〜 {_esc((rng.get("last") or "?")[:10])}'
    if _UNIT == "credit":
        ver = " / ".join(summary.get("copilot_versions") or [])
        meta_line = (
            f'source: {_esc(summary.get("source_dir"))} ・ 単位 credit（GitHub AI Credits・1 credit=0.01 USD）'
            + (f' ・ Copilot {_esc(ver)}' if ver else "")
        )
        footer_note = (
            "コスト = Copilot が記録した実測消費を GitHub AI Credits（1 credit=0.01 USD）で表示。"
            "「推定 credit」は models.json の単価から算出した参考値です。時間はログの"
            "タイムスタンプ差から導出した推定値です。"
        )
    else:
        meta_line = (
            f'source: {_esc(summary.get("source_dir"))} ・ pricing {_esc(summary.get("pricing_version"))}'
        )
        footer_note = (
            "コスト = トークン × 単価（pricing.json から算出。保存値ではありません）。"
            "「*」は単価未登録のモデル。時間・コストはセッション記録から導出した推定値です。"
        )

    # 埋め込み JSON はチャートの日次/週次/月次トグル再集計にしか使わないため、
    # by_day だけを載せる。summary 全体（会話 334 件・タイムライン等で ~660KB）を
    # 埋めると、既に HTML 化済みのテーブル/モーダルと二重になり report.html が倍増
    # する。機械可読の正本は別ファイルの summary.json 側にある。
    # <script> はテキスト（raw text）要素で HTML エンティティを復号しないため、
    # ここを _esc すると JSON.parse が &quot; 等で失敗する。エスケープは
    # </script> 破壊の防止だけに絞り、"<" を <（JSON として等価）に置換する。
    chart_data = {"by_day": summary.get("by_day", []), "cost_unit": _UNIT}
    data_json = json.dumps(chart_data, ensure_ascii=False).replace("<", "\\u003c")

    body = _TEMPLATE.format(
        subtitle=subtitle,
        meta_line=_esc(meta_line),
        agent_name=_esc(agent_name),
        footer_note=_esc(footer_note),
        warn_banner=warn_banner,
        hero=hero,
        tiles=tiles,
        day_chart=day_chart,
        model_section=model_section,
        subagent_panel=subagent_panel,
        proj_rows=proj_rows,
        proj_note=_star_note(proj_rows),
        tool_rows=tool_rows,
        conv_rows=conv_rows,
        conv_star_note=_star_note(conv_rows),
        conv_modals=conv_modals,
        conv_note=_esc(conv_note),
        data_json=data_json,
    )
    # CSS は本文末尾（_TEMPLATE の最後）に置かれているが、そのままだと巨大な文書
    # （会話モーダル＋埋め込み JSON で ~1.8MB）をブラウザが末尾の <style> に到達する
    # 前に一度素のテキストで描画してしまい、FOUC（一瞬だけ無スタイル表示）になる。
    # 整形後の body から <style> ブロックを切り出して <head> に移すと、描画前に
    # スタイルが確定するため FOUC が消える。
    head_style = ""
    if "<style>" in body:
        i = body.index("<style>")
        head_style = body[i:].rstrip() + "\n"
        body = body[:i].rstrip() + "\n"

    return (
        '<!doctype html>\n<html lang="ja">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>エージェント利用状況 — {_esc(agent_name)}</title>\n"
        + head_style
        + "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )


# ---------------------------------------------------------------------------
# ヒーロー / タイル
# ---------------------------------------------------------------------------
def _hero(t: dict) -> str:
    if _UNIT == "credit":
        savings = float(t.get("cache_savings_usd") or 0)
        est = float(t.get("cost_estimated_aiu") or 0)
        parts = []
        if savings > 0:
            parts.append(f'キャッシュにより <b>{_fmt_cost(savings)}</b> 節約済み')
        if est:
            parts.append(f'単価推定 <b>{_fmt_cost(est)}</b>（クロスチェック）')
        note = ' ・ '.join(parts) or 'Copilot が記録した実測 AI Credits'
        return (
            '<div class="hero">'
            '<div class="hero-label">総消費 credit（実測）</div>'
            f'<div class="hero-value">{_fmt_cost(t["cost_usd"])}</div>'
            f'<div class="hero-note">{note}</div>'
            '</div>'
        )
    savings = float(t.get("cache_savings_usd") or 0)
    return (
        '<div class="hero">'
        '<div class="hero-label">総コスト（推定）</div>'
        f'<div class="hero-value">{_fmt_cost(t["cost_usd"])}</div>'
        f'<div class="hero-note">キャッシュにより <b>{_fmt_usd(savings)}</b> 節約済み</div>'
        '</div>'
    )


def _tiles(summary: dict, t: dict) -> str:
    tok_total = int(t["input"]) + int(t["output"])
    items = [
        ("トークン", _fmt_int(tok_total), f'入力 {_fmt_int(t["input"])} / 出力 {_fmt_int(t["output"])}'),
        ("会話 / セッション", _fmt_int(summary.get("conversation_count", t["sessions"])),
         f'{_fmt_int(t["messages"])} メッセージ'),
        ("ツール呼び出し", _fmt_int(t["tool_calls"]), ""),
        ("AI稼働時間", _fmt_dur(t.get("ai_active_seconds", 0)),
         f'経過 {_fmt_dur(t["duration_seconds"])}・ツール実行時間 {_fmt_dur(t.get("active_seconds", 0))}'),
    ]
    return "".join(
        f'<div class="tile"><div class="t-label">{_esc(lbl)}</div>'
        f'<div class="t-value">{val}</div>'
        + (f'<div class="t-note">{_esc(note)}</div>' if note else "")
        + "</div>"
        for lbl, val, note in items
    )


# ---------------------------------------------------------------------------
# 日次コスト（Y軸目盛り・横スクロールなし）
# ---------------------------------------------------------------------------
def _render_chart_bars(days: list[dict], label_fn) -> str:
    """共通のバーレンダリング（label_fn は date 文字列から表示ラベルを生成）。"""
    if not days:
        return '<p class="note">データなし</p>'
    max_cost = max((d["cost_usd"] for d in days), default=0)
    top = _nice_max(max_cost)

    # Y軸ラベル＋グリッド線（上から）
    ticks = []
    for i in range(4, -1, -1):
        val = top * i / 4
        ticks.append(
            f'<div class="tick"><span class="tick-label">{_esc(_fmt_cost_compact(val))}</span>'
            '<span class="tick-line"></span></div>'
        )
    grid = '<div class="chart-grid">' + "".join(ticks) + "</div>"

    cols = []
    for d in days:
        h = (100 * d["cost_usd"] / top) if top else 0
        cols.append(
            '<div class="daycol">'
            f'<div class="daybar" style="height:{h:.1f}%">'
            f'<span class="daycost">{_esc(_fmt_cost(d["cost_usd"]))}</span></div>'
            f'<span class="daylabel">{_esc(label_fn(d["date"]))}</span></div>'
        )
    bars = '<div class="daybars">' + "".join(cols) + "</div>"
    return f'<div class="daychart">{grid}{bars}</div>'

def _day_chart(days: list[dict]) -> str:
    return _render_chart_bars(days, lambda d: d[5:])


# ---------------------------------------------------------------------------
# モデル別
# ---------------------------------------------------------------------------
def _model_section(summary: dict) -> str:
    rows = "".join(
        _row([
            _esc(m["model"]),
            _fmt_int(m["messages"]),
            _fmt_int(m["input"]),
            _fmt_int(m["output"]),
            _fmt_int(m["cache_read"]),
            _fmt_cost(m["cost_usd"]) + ("" if m["cost_known"] else " *"),
        ])
        for m in summary["by_model"]
    )
    return (
        '<div class="scroll"><table>'
        '<thead><tr><th>モデル</th><th>メッセージ</th><th>入力</th><th>出力</th>'
        '<th>cache read</th><th>コスト</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div>' + _star_note(rows)
    )


# ---------------------------------------------------------------------------
# サブエージェント
# ---------------------------------------------------------------------------
def _subagent_panel(summary: dict) -> str:
    sub = summary.get("subagents", {})
    calls = sub.get("total_calls", 0)
    if not calls:
        return '<p class="note">サブエージェント（Task/Agent）の呼び出しはありません</p>'

    if sub.get("tokens_recorded"):
        rows = "".join(
            _row([
                _esc(x["subagent_type"]),
                _fmt_int(x["calls"]),
                _fmt_int(x.get("input", 0)),
                _fmt_int(x.get("output", 0)),
                _fmt_cost(x.get("cost_usd", 0)) + ("" if x.get("cost_known", True) else " *"),
                _fmt_dur(x["total_seconds"]),
            ])
            for x in sub.get("by_type", [])
        )
        table = (
            '<div class="scroll"><table><thead><tr><th>型</th><th>呼出</th>'
            '<th>入力</th><th>出力</th><th>コスト</th><th>時間</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>'
        )
        internal_tok = sub.get("internal_input", 0) + sub.get("internal_output", 0)
        note = (
            f'<p class="note">合計 {_fmt_int(calls)} 回 ・ 内部 {_fmt_int(internal_tok)} tok ・ '
            f'{_fmt_cost(sub.get("internal_cost_usd", 0))}（総コストに算入済み）</p>'
        )
        return table + _star_note(rows) + note

    rows = "".join(
        _row([
            _esc(x["subagent_type"]),
            _fmt_int(x["calls"]),
            _fmt_dur(x["total_seconds"]),
        ])
        for x in sub.get("by_type", [])
    )
    table = (
        '<div class="scroll"><table><thead><tr><th>型</th><th>呼出</th>'
        '<th>時間</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></div>'
    )
    warn = ('<p class="banner">⚠ 内部記録（subagents/）が無く、サブエージェントの'
            'トークン・コストは未計上です</p>')
    return warn + table


# ---------------------------------------------------------------------------
# 会話別：表の1行 ＋ 詳細モーダル（:target）
# ---------------------------------------------------------------------------
def _conv_title_text(c: dict) -> str:
    return c.get("title") or (c.get("last_prompt") or "")[:44] or c["session"][:8]


def _modal_id(c: dict) -> str:
    return "d-" + _esc(c["session"])


def _conv_row(c: dict) -> str:
    title = _esc(_conv_title_text(c))
    cost = _fmt_cost(c["cost_usd"]) + ("" if c["cost_known"] else " *")
    agent_calls = c.get("agent_calls", 0)
    agent_sub = f'<div class="c-sub">SubAgent {agent_calls}</div>' if agent_calls else ""
    return (
        "<tr>"
        f'<td class="c-title"><span class="ct">{title}</span>'
        f'<span class="cp">{_esc(c["project"])}</span></td>'
        f'<td class="c-cost">{cost}</td>'
        f'<td>{_fmt_int(c["messages"])}</td>'
        f'<td>{_fmt_int(c["tool_calls"])}{agent_sub}</td>'
        f'<td>{_fmt_dur(c["duration_seconds"])}</td>'
        f'<td class="c-detail"><a class="detail-btn" href="#{_modal_id(c)}" '
        f'title="詳細を表示" aria-label="詳細を表示">ⓘ</a></td>'
        "</tr>"
    )


def _conv_modal(c: dict) -> str:
    title = _esc(_conv_title_text(c))
    cost = _fmt_cost(c["cost_usd"]) + ("" if c["cost_known"] else " *")
    models = _esc(" / ".join(c["models"]))

    stat = (
        '<div class="m-stats">'
        f'<div><span>コスト</span><b>{cost}</b></div>'
        f'<div><span>メッセージ</span><b>{_fmt_int(c["messages"])}</b></div>'
        f'<div><span>ツール</span><b>{_fmt_int(c["tool_calls"])}</b></div>'
        f'<div><span>時間</span><b>{_fmt_dur(c["duration_seconds"])}</b></div>'
        f'<div><span>AI稼働</span><b>{_fmt_dur(c.get("ai_active_seconds", 0))}</b></div>'
        '</div>'
    )

    lp = c.get("last_prompt")
    prompt = f'<p class="lastp">最終指示: {_esc(lp[:160])}</p>' if lp else ""

    md_rows = "".join(
        _row([
            _esc(m["model"]),
            _fmt_int(m["messages"]),
            _fmt_int(m["input"]),
            _fmt_int(m["output"]),
            _fmt_cost(m["cost_usd"]) + ("" if m["cost_known"] else " *"),
        ])
        for m in c.get("models_detail", [])
    )
    md_table = (
        '<div class="scroll"><table class="mini"><thead><tr><th>モデル</th>'
        '<th>メッセージ</th><th>入力</th><th>出力</th><th>コスト</th></tr></thead>'
        f'<tbody>{md_rows}</tbody></table></div>' + _star_note(md_rows)
    )

    tl = c.get("timeline")
    if tl:
        items = "".join(_timeline_item(e) for e in tl)
        trunc = ('<li class="tl-note">… 以降は省略（先頭 500 件まで）</li>'
                 if c.get("timeline_truncated") else "")
        timeline = (
            '<h4>ツール / Agent タイムライン <small>(UTC)</small></h4>'
            f'<ol class="timeline">{items}{trunc}</ol>'
        )
    elif tl is None and c.get("tool_calls"):
        timeline = '<p class="note">タイムラインは上位会話のみ収録しています</p>'
    else:
        timeline = ""

    return (
        f'<div class="modal" id="{_modal_id(c)}">'
        '<a class="modal-backdrop" href="#" aria-label="閉じる"></a>'
        '<div class="modal-card">'
        '<div class="modal-head">'
        f'<div><div class="m-title">{title}</div>'
        f'<div class="m-sub">{_esc(c["session"][:8])} · {_esc(c["project"])} · {models}</div></div>'
        '<a class="modal-close" href="#" aria-label="閉じる">×</a>'
        '</div>'
        f'{stat}{prompt}<h4>モデル内訳</h4>{md_table}{timeline}'
        '</div></div>'
    )


def _timeline_item(e: dict) -> str:
    t = (e.get("ts") or "")[11:19] or "--:--:--"
    dur = f' <span class="tdur">{e["seconds"]:.0f}s</span>' if e.get("seconds") else ""
    err = ' <span class="terr">error</span>' if e.get("is_error") else ""
    if e["tool"] in ("Agent", "Task"):
        sub = _esc(e.get("subagent_type") or "?")
        desc = _esc(e.get("description") or "")
        usage = ""
        if e.get("sub_msgs"):
            tok = (e.get("sub_input", 0) or 0) + (e.get("sub_output", 0) or 0)
            cost = _fmt_cost(e.get("sub_cost", 0)) + ("" if e.get("sub_cost_known", True) else " *")
            usage = f' <span class="subusage">{_fmt_int(tok)}tok · {cost}</span>'
        return (
            f'<li class="tl-agent"><span class="ttime">{t}</span> '
            f'<span class="badge">Agent</span> <b>{sub}</b> {desc}{dur}{err}{usage}</li>'
        )
    return (
        f'<li><span class="ttime">{t}</span> '
        f'<span class="tname">{_esc(e["tool"])}</span>{dur}{err}</li>'
    )


# ---------------------------------------------------------------------------
# 小物
# ---------------------------------------------------------------------------
def _row(cells: list[str]) -> str:
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


# コスト末尾の「 *」（単価未登録マーク）を含む表にだけ、その表の直下へ出す注釈。
_STAR_NOTE = ('<p class="note star-note">* 単価未登録のモデル'
              '（pricing.json に価格がなくコスト未算出）</p>')


def _star_note(*fragments: str) -> str:
    return _STAR_NOTE if any(" *</td>" in (f or "") for f in fragments) else ""


def _bar(value: float, maximum: float) -> str:
    pct = max(2, round(100 * value / maximum)) if maximum else 0
    return f'<span class="ibar" style="width:{pct}%"></span>'


_TEMPLATE = """<div class="wrap">
<header>
  <h1>エージェント利用状況 <small>{agent_name}</small></h1>
  <p class="sub">{subtitle}</p>
  <p class="meta">{meta_line}</p>
  {warn_banner}
</header>

<section class="overview">
  {hero}
  <div class="tiles">{tiles}</div>
</section>

<section>
  <h2>日次コスト</h2>
  <fieldset class="granule-toggle">
    <legend>集計粒度</legend>
    <label><input type="radio" name="granularity" value="daily" checked> 日次</label>
    <label><input type="radio" name="granularity" value="weekly"> 週次</label>
    <label><input type="radio" name="granularity" value="monthly"> 月次</label>
  </fieldset>
  <div id="chart-container">{day_chart}</div>
</section>

<section>
  <h2>モデル別</h2>
  {model_section}
</section>

<section class="two">
  <div>
    <h2>サブエージェント</h2>
    {subagent_panel}
  </div>
  <div>
    <h2>プロジェクト別</h2>
    <div class="scroll"><table>
      <thead><tr><th>プロジェクト</th><th>メッセージ</th><th>入力</th><th>出力</th><th>コスト</th></tr></thead>
      <tbody>{proj_rows}</tbody>
    </table></div>
    {proj_note}
  </div>
</section>

<section>
  <h2>ツール別</h2>
  <div class="scroll"><table>
    <thead><tr><th>ツール</th><th>呼出</th><th>エラー</th><th>合計時間</th><th></th></tr></thead>
    <tbody>{tool_rows}</tbody>
  </table></div>
</section>

<section>
  <h2>会話別</h2>
  <p class="note">{conv_note}</p>
  <div class="scroll"><table class="convtable">
    <thead><tr><th>会話</th><th>コスト</th><th>msg</th><th>tool</th><th>時間</th><th></th></tr></thead>
    <tbody>{conv_rows}</tbody>
  </table></div>
  {conv_star_note}
</section>

<footer><p class="note">{footer_note}</p></footer>
<script type="application/json" id="summary-data">{data_json}</script>
</div>
{conv_modals}

<script id="chart-control">
(function() {{
  const summary = JSON.parse(document.getElementById('summary-data').textContent);
  const dailyData = summary.by_day || [];

  function aggregateWeekly(days) {{
    const weeks = {{}};
    days.forEach(day => {{
      const d = new Date(day.date + 'T00:00:00Z');
      const weekStart = new Date(d);
      weekStart.setDate(d.getDate() - d.getUTCDay() + 1);
      const key = weekStart.toISOString().split('T')[0];
      if (!weeks[key]) weeks[key] = {{ date: key, cost_usd: 0 }};
      weeks[key].cost_usd += day.cost_usd;
    }});
    return Object.values(weeks).sort((a, b) => a.date.localeCompare(b.date));
  }}

  function aggregateMonthly(days) {{
    const months = {{}};
    days.forEach(day => {{
      const key = day.date.slice(0, 7);
      if (!months[key]) months[key] = {{ date: key + '-01', cost_usd: 0 }};
      months[key].cost_usd += day.cost_usd;
    }});
    return Object.values(months).sort((a, b) => a.date.localeCompare(b.date));
  }}

  function renderChartBars(data, format) {{
    if (!data.length) return '<p class="note">データなし</p>';
    const maxCost = Math.max(...data.map(d => d.cost_usd));
    const niceMax = niceMaxValue(maxCost);

    const ticks = [];
    for (let i = 4; i >= 0; i--) {{
      const val = niceMax * i / 4;
      ticks.push(`<div class="tick"><span class="tick-label">${{formatUsdCompact(val)}}</span><span class="tick-line"></span></div>`);
    }}
    const grid = '<div class="chart-grid">' + ticks.join('') + '</div>';

    const cols = data.map(d => {{
      const h = niceMax ? (100 * d.cost_usd / niceMax) : 0;
      return `<div class="daycol"><div class="daybar" style="height:${{h.toFixed(1)}}%"><span class="daycost">${{formatUsd(d.cost_usd)}}</span></div><span class="daylabel">${{format(d.date)}}</span></div>`;
    }});
    const bars = '<div class="daybars">' + cols.join('') + '</div>';
    return '<div class="daychart">' + grid + bars + '</div>';
  }}

  const isAiu = (summary.cost_unit === 'credit');

  function formatUsd(n) {{
    const s = parseFloat(n).toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
    return isAiu ? (s + ' credit') : ('$' + s);
  }}

  function formatUsdCompact(n) {{
    const sym = isAiu ? '' : '$';
    if (n <= 0) return isAiu ? '0' : '$0';
    if (n >= 100) return sym + n.toLocaleString('en-US', {{maximumFractionDigits: 0}});
    if (n >= 10) return sym + n.toLocaleString('en-US', {{maximumFractionDigits: 0}});
    return sym + n.toFixed(1);
  }}

  function niceMaxValue(v) {{
    if (v <= 0) return 1;
    const exp = Math.floor(Math.log10(v));
    const base = Math.pow(10, exp);
    for (const m of [1, 2, 2.5, 5, 10]) {{
      if (v <= m * base) return m * base;
    }}
    return 10 * base;
  }}

  const radios = document.querySelectorAll('input[name="granularity"]');
  radios.forEach(radio => {{
    radio.addEventListener('change', () => {{
      let data, fmt;
      if (radio.value === 'weekly') {{
        data = aggregateWeekly(dailyData);
        fmt = d => d.slice(5, 10);
      }} else if (radio.value === 'monthly') {{
        data = aggregateMonthly(dailyData);
        fmt = d => d.slice(0, 7);
      }} else {{
        data = dailyData;
        fmt = d => d.slice(5);
      }}
      document.getElementById('chart-container').innerHTML = renderChartBars(data, fmt);
    }});
  }});
}})();
</script>

<script id="modal-scroll">
(function() {{
  // 詳細モーダルは :target（href の切替）で開閉するが、モーダル自体は
  // position:fixed なので本文をスクロールさせる必要はない。特に閉じる時の
  // href="#" は空フラグメントとなりページ先頭へ飛んでしまうため、開閉リンクの
  // クリックでは直前のスクロール位置を復元して「一番上に戻る」不具合を防ぐ。
  document.addEventListener('click', function(e) {{
    var a = e.target.closest('a.detail-btn, a.modal-close, a.modal-backdrop');
    if (!a) return;
    var y = window.scrollY;
    requestAnimationFrame(function() {{ window.scrollTo(0, y); }});
  }});
}})();
</script>

<style>
  :root {{
    --ink:#2a2520; --sub:#8a7f70; --line:#ece3d2; --card:#ffffff;
    --accent:#e8823c; --accent2:#3aa981; --pink:#e8467a; --warn:#b45309;
    --bar:#f0b45e; --track:#f5efe2; --shadow:0 2px 10px rgba(150,120,70,0.10);
  }}
  * {{ box-sizing:border-box; }}
  body {{
    color:var(--ink); margin:0;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Hiragino Sans","Noto Sans JP",system-ui,sans-serif;
    background-color:#ffffff;
    background-image:radial-gradient(rgba(190,150,80,0.16) 1.6px, transparent 1.7px);
    background-size:20px 20px;
    background-position:0 0;
  }}
  .wrap {{ max-width:1000px; margin:0 auto; padding:34px 22px 30px; }}
  header h1 {{ font-size:1.5rem; margin:0 0 2px; letter-spacing:.01em; }}
  header h1 small {{ font-weight:500; color:var(--sub); font-size:0.85rem; }}
  .sub {{ color:var(--ink); font-size:0.95rem; font-weight:600; margin:0 0 2px; }}
  .meta {{ color:var(--sub); font-size:0.72rem; margin:0; word-break:break-all; }}
  .banner {{
    margin:12px 0 0; padding:8px 12px; border-radius:10px; font-size:0.78rem; font-weight:600;
    color:var(--warn); background:#fff5e6; border:1px solid #f4dcae;
  }}
  h2 {{ font-size:1.02rem; margin:30px 0 12px; padding-left:10px; border-left:4px solid var(--accent); }}
  h4 {{ font-size:0.82rem; margin:16px 0 6px; color:var(--sub); }}
  .note {{ color:var(--sub); font-size:0.75rem; margin:6px 0; }}

  /* ---- 概況：ヒーロー＋タイル ---- */
  .overview {{ display:grid; grid-template-columns:minmax(240px,1fr) 2fr; gap:16px; margin-top:20px; }}
  @media (max-width:640px) {{ .overview {{ grid-template-columns:1fr; }} }}
  .hero {{
    background:linear-gradient(135deg,#fff,#fff8ee); border:1px solid var(--line);
    border-radius:18px; padding:22px 24px; box-shadow:var(--shadow);
    display:flex; flex-direction:column; justify-content:center;
  }}
  .hero-label {{ color:var(--sub); font-size:0.82rem; font-weight:600; }}
  .hero-value {{ font-size:3rem; font-weight:800; line-height:1.05; margin:4px 0; color:var(--accent); letter-spacing:-.01em; }}
  .hero-note {{ color:var(--sub); font-size:0.8rem; }}
  .hero-note b {{ color:var(--accent2); }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; }}
  .tile {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; box-shadow:var(--shadow); }}
  .t-label {{ color:var(--sub); font-size:0.74rem; font-weight:600; }}
  .t-value {{ font-size:1.5rem; font-weight:700; margin-top:2px; font-variant-numeric:tabular-nums; }}
  .t-note {{ color:var(--sub); font-size:0.7rem; margin-top:3px; }}

  /* ---- 日次コスト ---- */
  .daychart {{
    position:relative; height:210px; padding:14px 8px 26px 46px;
    background:var(--card); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow);
  }}
  .chart-grid {{ position:absolute; inset:14px 8px 26px 46px; display:flex; flex-direction:column; justify-content:space-between; }}
  .tick {{ position:relative; height:0; }}
  .tick-label {{ position:absolute; right:calc(100% + 6px); top:-0.55em; font-size:0.66rem; color:var(--sub); white-space:nowrap; font-variant-numeric:tabular-nums; }}
  .tick-line {{ display:block; border-top:1px dashed var(--line); }}
  .daybars {{ position:absolute; inset:14px 8px 26px 46px; display:flex; align-items:flex-end; gap:10px; }}
  .daycol {{ position:relative; flex:1 1 0; min-width:0; height:100%; display:flex; justify-content:center; align-items:flex-end; }}
  .daybar {{ position:relative; width:100%; max-width:46px; background:linear-gradient(180deg,var(--bar),var(--accent)); border-radius:6px 6px 0 0; min-height:3px; box-shadow:0 1px 3px rgba(180,120,40,0.25); }}
  .daycost {{ position:absolute; left:50%; bottom:calc(100% + 3px); transform:translateX(-50%); font-size:0.68rem; font-weight:700; color:var(--accent); white-space:nowrap; }}
  .daylabel {{ position:absolute; left:0; right:0; top:calc(100% + 6px); text-align:center; font-size:0.68rem; color:var(--sub); white-space:nowrap; }}

  /* ---- テーブル ---- */
  .scroll {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; font-size:0.85rem; background:#fff; }}
  th,td {{ text-align:right; padding:8px 11px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  th:first-child,td:first-child {{ text-align:left; }}
  th {{ color:var(--sub); font-weight:600; font-size:0.74rem; }}
  tbody tr:hover {{ background:#fffaf0; }}
  .ibar {{ display:inline-block; height:8px; background:var(--accent2); border-radius:4px; min-width:2px; vertical-align:middle; }}
  .badge {{ display:inline-block; background:var(--accent); color:#fff; font-size:0.64rem; font-weight:700; padding:1px 6px; border-radius:6px; }}

  /* ---- 会話テーブル ---- */
  .convtable td.c-title {{ max-width:340px; white-space:normal; }}
  .c-sub {{ font-size:0.66rem; color:var(--sub); margin-top:2px; }}
  .ct {{ font-weight:600; }}
  .cp {{ display:block; color:var(--sub); font-size:0.7rem; }}
  .c-cost {{ font-weight:700; color:var(--accent); font-variant-numeric:tabular-nums; }}
  .c-detail {{ text-align:center; }}
  .detail-btn {{
    display:inline-flex; align-items:center; justify-content:center; width:26px; height:26px;
    border-radius:50%; text-decoration:none; color:var(--accent); font-size:1rem;
    border:1px solid var(--line); background:#fff8ee; transition:all .12s;
  }}
  .detail-btn:hover {{ background:var(--accent); color:#fff; border-color:var(--accent); }}

  /* ---- モーダル（:target で開閉・JSなし） ---- */
  .modal {{ display:none; position:fixed; inset:0; z-index:50; align-items:center; justify-content:center; padding:20px; }}
  .modal:target {{ display:flex; }}
  .modal-backdrop {{ position:absolute; inset:0; background:rgba(60,45,25,0.42); }}
  .modal-card {{
    position:relative; background:#fff; border-radius:16px; box-shadow:0 12px 40px rgba(60,40,10,0.30);
    max-width:640px; width:100%; max-height:85vh; overflow:auto; padding:20px 22px 24px;
  }}
  .modal-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }}
  .m-title {{ font-size:1.05rem; font-weight:700; }}
  .m-sub {{ color:var(--sub); font-size:0.72rem; margin-top:2px; word-break:break-all; }}
  .modal-close {{ flex:none; width:30px; height:30px; border-radius:50%; display:flex; align-items:center; justify-content:center;
    text-decoration:none; color:var(--sub); font-size:1.3rem; line-height:1; background:var(--track); }}
  .modal-close:hover {{ background:var(--accent); color:#fff; }}
  .m-stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:16px 0 4px; }}
  .m-stats > div {{ background:var(--track); border-radius:10px; padding:10px; text-align:center; }}
  .m-stats span {{ display:block; color:var(--sub); font-size:0.68rem; }}
  .m-stats b {{ font-size:1.05rem; font-variant-numeric:tabular-nums; }}
  .lastp {{ font-size:0.78rem; color:var(--sub); margin:12px 0 0; }}
  table.mini {{ font-size:0.8rem; }}
  ol.timeline {{ list-style:none; margin:0; padding:0; font-size:0.78rem; max-height:300px; overflow:auto; border:1px solid var(--line); border-radius:8px; }}
  ol.timeline li {{ padding:4px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }}
  ol.timeline li:last-child {{ border-bottom:none; }}
  li.tl-agent {{ background:#fff3e2; }}
  li.tl-note {{ color:var(--sub); font-style:italic; }}
  .ttime {{ color:var(--sub); font-variant-numeric:tabular-nums; margin-right:8px; }}
  .tname {{ font-weight:500; }}
  .tdur {{ color:var(--accent2); }}
  .terr {{ color:#dc2626; font-weight:600; }}
  .subusage {{ color:var(--accent2); font-variant-numeric:tabular-nums; }}

  /* ---- 集計粒度の切り替え ---- */
  .granule-toggle {{ border:none; padding:0; margin:12px 0; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }}
  .granule-toggle legend {{ font-size:0.82rem; font-weight:600; color:var(--sub); margin:0; display:block; width:100%; margin-bottom:6px; }}
  .granule-toggle label {{ display:inline-flex; align-items:center; gap:6px; cursor:pointer; font-size:0.85rem; }}
  .granule-toggle label:hover {{ color:var(--accent); }}
  .granule-toggle input[type="radio"] {{ cursor:pointer; }}

  .two {{ display:grid; grid-template-columns:1fr; gap:24px; }}
  @media (max-width:640px) {{ .m-stats {{ grid-template-columns:repeat(2,1fr); }} }}
  footer {{ margin-top:26px; }}
</style>
"""
