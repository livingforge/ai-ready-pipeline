# -*- coding: utf-8 -*-
"""汎用ビューア生成 — 仕様データを自己完結の対話型 HTML に描画する

特定のアイテム種別を一切知らない。メタモデルの宣言（種別・関係）から
ノード色・凡例・フィルタを組み立てる。生成物は単一 HTML（依存なし・CDN なし）で、
ブラウザで開くだけで動く。

    python specdb/visualize.py                    # out/specdb.html を生成
    python visualize.py --root <データディレクトリ> [-o 出力パス]

検証エラーがあっても生成は中止しない — エラー・警告はビューア上に
オーバーレイ表示される（どこが壊れているかを見るための道具でもあるため）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from engine import Store, parse_root

OUTPUT_NAME = "specdb.html"

# dataviz 検証済みカテゴリカルパレット（light / dark は同一色相の別ステップ）。
# 種別はメタモデル宣言順にスロットへ固定割当。9 種別以降はニュートラルに畳む
# （色を循環させない）。
CAT_LIGHT = ["#2a78d6", "#1baf7a", "#eda100", "#008300",
             "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
CAT_DARK = ["#3987e5", "#199e70", "#c98500", "#008300",
            "#9085e9", "#e66767", "#d55181", "#d95926"]
OVERFLOW = "#898781"


def git_rev(root: Path) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def build_payload(store: Store, root: Path) -> dict:
    mm = store.mm
    item_types = []
    for i, (t, tdef) in enumerate(mm.item_types.items()):
        item_types.append({
            "name": t,
            "label": tdef.get("label", t),
            "color": CAT_LIGHT[i] if i < len(CAT_LIGHT) else OVERFLOW,
            "colorDark": CAT_DARK[i] if i < len(CAT_DARK) else OVERFLOW,
        })
    relation_types = [
        {"name": r, "label": rdef.get("label", r)}
        for r, rdef in mm.relation_types.items()
    ]
    items = [
        {"id": it.id, "type": it.type, "label": it.label(mm),
         "status": it.status, "attrs": it.attrs, "source": it.source}
        for it in store.items.values()
    ]
    relations = [
        {"type": r.type, "from": r.src, "to": r.dst,
         "attrs": r.attrs, "status": r.status, "source": r.source}
        for r in store.relations
    ]
    problems = [
        {"level": p.level, "where": p.where, "message": p.message}
        for p in store.problems
    ]
    return {
        "root": root.resolve().name,
        "rev": git_rev(root),
        "generatedAt": datetime.now(timezone.utc).astimezone()
                       .isoformat(timespec="seconds"),
        "itemTypes": item_types,
        "relationTypes": relation_types,
        "items": items,
        "relations": relations,
        "problems": problems,
    }


def render(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA_JSON__", data)


def main() -> int:
    root, args = parse_root(sys.argv[1:])
    out_path = None
    if args[:1] == ["-o"]:
        if len(args) < 2:
            sys.exit("-o には出力パスを指定する。")
        out_path = Path(args[1])
    store = Store.load(root)
    for p in store.problems:
        print(p, file=sys.stderr)

    dest = out_path or (root / "out" / OUTPUT_NAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(build_payload(store, root)), encoding="utf-8")
    errs = sum(1 for p in store.problems if p.level == "error")
    warns = len(store.problems) - errs
    print(f"生成しました: {dest}")
    print(f"  アイテム {len(store.items)} 件 / 関係 {len(store.relations)} 件 / "
          f"error {errs} 件 / warn {warns} 件")
    return 0


# ---------------------------------------------------------------------------
# HTML テンプレート。<style>/<script> を body 内に置く（フラグメント抽出可能に
# するため）。テーマは CSS トークンで宣言し、OS 設定（prefers-color-scheme）と
# 明示指定（:root[data-theme=…]）の両方に従う。
# ---------------------------------------------------------------------------

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>specdb ビューア</title>
</head>
<body>
<style>
:root {
  --bg: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #898781; --hairline: #e1e0d9; --border: rgba(11,11,11,.10);
  --edge: #c3c2b7; --edge-hi: #52514e;
  --accent: #2a78d6; --err: #d03b3b; --warn: #fab219; --good: #0ca30c;
  --halo: #fcfcfb;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
    --muted: #898781; --hairline: #2c2c2a; --border: rgba(255,255,255,.10);
    --edge: #494944; --edge-hi: #c3c2b7;
    --accent: #3987e5; --err: #d03b3b; --warn: #fab219; --good: #0ca30c;
    --halo: #1a1a19;
  }
}
:root[data-theme="light"] {
  --bg: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e;
  --muted: #898781; --hairline: #e1e0d9; --border: rgba(11,11,11,.10);
  --edge: #c3c2b7; --edge-hi: #52514e;
  --accent: #2a78d6; --err: #d03b3b; --warn: #fab219; --good: #0ca30c;
  --halo: #fcfcfb;
}
:root[data-theme="dark"] {
  --bg: #0d0d0d; --surface: #1a1a19; --ink: #ffffff; --ink-2: #c3c2b7;
  --muted: #898781; --hairline: #2c2c2a; --border: rgba(255,255,255,.10);
  --edge: #494944; --edge-hi: #c3c2b7;
  --accent: #3987e5; --err: #d03b3b; --warn: #fab219; --good: #0ca30c;
  --halo: #1a1a19;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 13px/1.5 system-ui, -apple-system, "Segoe UI", "Hiragino Sans",
        "Yu Gothic UI", "Meiryo", sans-serif;
}
#app { display: grid; grid-template-rows: auto 1fr; height: 100vh; min-height: 480px; }
header {
  display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
  padding: 10px 16px; border-bottom: 1px solid var(--hairline);
  background: var(--surface);
}
header h1 { font-size: 15px; font-weight: 650; margin: 0; }
header .meta { color: var(--muted); font-size: 11.5px; }
header .stats { display: flex; gap: 14px; margin-left: auto; font-variant-numeric: tabular-nums; }
header .stat { display: flex; align-items: baseline; gap: 5px; }
header .stat b { font-size: 15px; font-weight: 650; }
header .stat span { color: var(--muted); font-size: 11px; }
header .stat.err b { color: var(--err); }
header .stat.warn b { color: var(--warn); }
header .stat.zero b { color: var(--good); }
#cols { display: grid; grid-template-columns: 232px 1fr 300px; min-height: 0; }
@media (max-width: 900px) { #cols { grid-template-columns: 200px 1fr; } #detail { display: none; } }
.panel { overflow-y: auto; background: var(--surface); }
#side { border-right: 1px solid var(--hairline); padding: 12px; }
#detail { border-left: 1px solid var(--hairline); padding: 14px; }
.sec { margin-bottom: 16px; }
.sec > h2 {
  font-size: 10.5px; font-weight: 650; letter-spacing: .08em; color: var(--muted);
  text-transform: uppercase; margin: 0 0 6px;
}
#q {
  width: 100%; padding: 6px 8px; border: 1px solid var(--hairline); border-radius: 6px;
  background: var(--bg); color: var(--ink); font: inherit;
}
#q:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
.chk { display: flex; align-items: center; gap: 7px; padding: 3px 2px; cursor: pointer;
       border-radius: 5px; user-select: none; }
.chk:hover { background: var(--bg); }
.chk input { margin: 0; accent-color: var(--accent); }
.chk .sw { width: 10px; height: 10px; border-radius: 3px; flex: none; }
.chk .n { margin-left: auto; color: var(--muted); font-variant-numeric: tabular-nums; }
.chk.rel .sw { height: 2px; border-radius: 1px; background: var(--edge-hi); }
#problems .p { display: flex; gap: 6px; padding: 4px 6px; border-radius: 5px;
               cursor: pointer; align-items: baseline; }
#problems .p:hover { background: var(--bg); }
#problems .p .lv { flex: none; font-size: 10px; font-weight: 700; padding: 0 5px;
                   border-radius: 8px; color: #fff; }
#problems .p .lv.error { background: var(--err); }
#problems .p .lv.warn { background: var(--warn); color: #1a1a19; }
#problems .p .tx { color: var(--ink-2); font-size: 11.5px; }
#problems .empty { color: var(--good); font-size: 12px; }
#stage { position: relative; min-width: 0; background: var(--bg); }
#toolbar { position: absolute; top: 10px; left: 12px; z-index: 4; display: flex; gap: 6px; }
#toolbar button, #toolbar label {
  border: 1px solid var(--hairline); background: var(--surface); color: var(--ink-2);
  border-radius: 6px; padding: 4px 10px; font: inherit; font-size: 12px; cursor: pointer;
  display: flex; align-items: center; gap: 5px;
}
#toolbar button:hover, #toolbar label:hover { color: var(--ink); border-color: var(--muted); }
#toolbar button:focus-visible { outline: 2px solid var(--accent); }
#toolbar .on { color: var(--ink); border-color: var(--accent); }
#svg { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
#svg.panning { cursor: grabbing; }
.link { stroke: var(--edge); stroke-width: 1.5; fill: none; }
.link.hi { stroke: var(--edge-hi); stroke-width: 2; }
.link.dim { opacity: .12; }
.node { cursor: pointer; }
.node circle { stroke: var(--halo); stroke-width: 1.5; }
.node.deprecated { opacity: .45; }
.node.deprecated circle { stroke-dasharray: 3 2; }
.node.dim { opacity: .14; }
.node.sel circle { stroke: var(--accent); stroke-width: 3; }
.node text {
  font-size: 11px; fill: var(--ink); paint-order: stroke; stroke: var(--halo);
  stroke-width: 3px; stroke-linejoin: round; pointer-events: none;
}
.node .ring { fill: none; stroke-width: 2.5; }
.edgelabel { font-size: 9.5px; fill: var(--muted); paint-order: stroke;
             stroke: var(--halo); stroke-width: 2.5px; pointer-events: none; }
#tip {
  position: absolute; z-index: 6; pointer-events: none; display: none; max-width: 320px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 7px;
  padding: 7px 10px; font-size: 11.5px; color: var(--ink-2);
  box-shadow: 0 4px 14px rgba(0,0,0,.18);
}
#tip b { color: var(--ink); }
#tableview { display: none; overflow: auto; height: 100%; padding: 14px; }
#tableview table { border-collapse: collapse; width: 100%; font-size: 12px; }
#tableview th, #tableview td { text-align: left; padding: 5px 10px;
  border-bottom: 1px solid var(--hairline); white-space: nowrap; }
#tableview th { color: var(--muted); font-size: 10.5px; letter-spacing: .06em;
  text-transform: uppercase; position: sticky; top: 0; background: var(--bg); }
#tableview tr { cursor: pointer; }
#tableview tbody tr:hover { background: var(--surface); }
#tableview .sw { display: inline-block; width: 9px; height: 9px; border-radius: 3px;
  margin-right: 6px; vertical-align: baseline; }
#detail .placeholder { color: var(--muted); }
#detail h3 { margin: 2px 0 2px; font-size: 15px; line-height: 1.35; }
#detail .idline { color: var(--muted); font-size: 11px; font-family: ui-monospace,
  Consolas, monospace; margin-bottom: 8px; word-break: break-all; }
.chip { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--hairline);
  border-radius: 10px; padding: 1px 8px; font-size: 11px; color: var(--ink-2); }
.chip .sw { width: 9px; height: 9px; border-radius: 3px; }
.pill { display: inline-block; border-radius: 10px; padding: 1px 8px; font-size: 11px;
  border: 1px solid var(--hairline); color: var(--ink-2); }
.pill.approved { color: var(--good); border-color: var(--good); }
.pill.deprecated { text-decoration: line-through; }
#detail table.attrs { width: 100%; border-collapse: collapse; font-size: 12px; margin: 6px 0; }
#detail table.attrs td { padding: 3px 6px 3px 0; vertical-align: top;
  border-bottom: 1px solid var(--hairline); }
#detail table.attrs td.k { color: var(--muted); white-space: nowrap; width: 1%;
  padding-right: 12px; }
#detail table.attrs td.v { word-break: break-word; }
#detail .src { border-left: 3px solid var(--hairline); padding: 2px 0 2px 10px;
  margin: 6px 0; font-size: 11.5px; color: var(--ink-2); }
#detail .src .ev { color: var(--muted); font-style: italic; }
#detail .relrow { padding: 3px 0; font-size: 12px; }
#detail .relrow a { color: var(--accent); text-decoration: none; cursor: pointer; }
#detail .relrow a:hover { text-decoration: underline; }
#detail .relrow .rl { color: var(--muted); }
#detail .prob { font-size: 11.5px; padding: 4px 8px; border-radius: 6px; margin: 4px 0; }
#detail .prob.error { background: color-mix(in srgb, var(--err) 12%, transparent);
  color: var(--err); }
#detail .prob.warn { background: color-mix(in srgb, var(--warn) 16%, transparent);
  color: var(--ink-2); }
</style>

<div id="app">
  <header>
    <h1>specdb ビューア</h1>
    <span class="meta" id="meta"></span>
    <div class="stats" id="stats"></div>
  </header>
  <div id="cols">
    <nav class="panel" id="side">
      <div class="sec"><input id="q" type="search" placeholder="検索 (ラベル / ID)"
        aria-label="ノード検索"></div>
      <div class="sec"><h2>アイテム種別</h2><div id="ftypes"></div></div>
      <div class="sec"><h2>関係種別</h2><div id="frels"></div></div>
      <div class="sec"><h2>検証</h2><div id="problems"></div></div>
    </nav>
    <main id="stage">
      <div id="toolbar">
        <button id="btnFit" type="button">全体表示</button>
        <button id="btnRelayout" type="button">再レイアウト</button>
        <button id="btnLabels" type="button">関係ラベル</button>
        <button id="btnTable" type="button">一覧表示</button>
      </div>
      <svg id="svg" role="img" aria-label="仕様アイテムの関係グラフ">
        <defs>
          <marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7"
            markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0.6 L7.4,4 L0,7.4 Z" fill="var(--edge)"></path>
          </marker>
          <marker id="arrHi" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7"
            markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0.6 L7.4,4 L0,7.4 Z" fill="var(--edge-hi)"></path>
          </marker>
        </defs>
        <g id="viewport">
          <g id="glinks"></g><g id="glabels"></g><g id="gnodes"></g>
        </g>
      </svg>
      <div id="tableview"></div>
      <div id="tip"></div>
    </main>
    <aside class="panel" id="detail">
      <div class="placeholder">ノードまたはエッジを選択すると、属性・出典・関係が
        ここに表示されます。</div>
    </aside>
  </div>
</div>

<script type="application/json" id="specdb-data">__DATA_JSON__</script>
<script>
"use strict";
const DB = JSON.parse(document.getElementById("specdb-data").textContent);
const STATUS_LABEL = {draft:"起票", review:"レビュー中", approved:"承認済",
                      deprecated:"廃止"};
const dark = () => {
  const t = document.documentElement.getAttribute("data-theme");
  if (t) return t === "dark";
  return matchMedia("(prefers-color-scheme: dark)").matches;
};
const typeDef = Object.fromEntries(DB.itemTypes.map(t => [t.name, t]));
const relDef  = Object.fromEntries(DB.relationTypes.map(r => [r.name, r]));
const typeColor = t => dark() ? typeDef[t].colorDark : typeDef[t].color;

// ---- グラフモデル ----------------------------------------------------------
const nodes = DB.items.map((it, i) => ({...it, i, x:0, y:0, vx:0, vy:0, deg:0}));
const byId = Object.fromEntries(nodes.map(n => [n.id, n]));
const links = DB.relations
  .filter(r => byId[r.from] && byId[r.to])
  .map((r, i) => ({...r, i, s: byId[r.from], t: byId[r.to]}));
links.forEach(l => { l.s.deg++; l.t.deg++; });
nodes.forEach(n => { n.r = 7 + Math.min(8, Math.sqrt(n.deg) * 2.4); });

const probsOf = {};   // アイテム ID → {error:n, warn:n}
DB.problems.forEach(p => {
  if (!byId[p.where]) return;
  (probsOf[p.where] = probsOf[p.where] || {error:0, warn:0})[p.level]++;
});

// 決定論的な初期配置: 種別ごとのクラスタ中心 + 黄金角スパイラル
function seed() {
  const T = DB.itemTypes.length, cnt = {};
  nodes.forEach(n => {
    const ti = DB.itemTypes.findIndex(t => t.name === n.type);
    const k = (cnt[n.type] = (cnt[n.type] || 0) + 1);
    const a = 2 * Math.PI * ti / Math.max(1, T);
    const cx = 300 * Math.cos(a), cy = 300 * Math.sin(a);
    const sa = k * 2.39996, sr = 14 * Math.sqrt(k);
    n.x = cx + sr * Math.cos(sa); n.y = cy + sr * Math.sin(sa);
    n.vx = n.vy = 0;
  });
}

// ---- 力学シミュレーション ---------------------------------------------------
let alpha = 0;
function step() {
  const REP = 6400, SPRING = 0.045, REST = 150, CENTER = 0.018;
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx*dx + dy*dy || 1; if (d2 > 250000) continue;
      const f = REP / d2 * alpha, d = Math.sqrt(d2);
      dx = dx/d*f; dy = dy/d*f;
      a.vx += dx; a.vy += dy; b.vx -= dx; b.vy -= dy;
    }
  }
  links.forEach(l => {
    const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y;
    const d = Math.sqrt(dx*dx + dy*dy) || 1;
    const f = (d - REST) * SPRING * alpha;
    const fx = dx/d*f, fy = dy/d*f;
    l.s.vx += fx; l.s.vy += fy; l.t.vx -= fx; l.t.vy -= fy;
  });
  nodes.forEach(n => {
    if (n === dragNode) { n.vx = n.vy = 0; return; }
    n.vx -= n.x * CENTER * alpha; n.vy -= n.y * CENTER * alpha;
    n.vx *= 0.6; n.vy *= 0.6; n.x += n.vx; n.y += n.vy;
  });
  alpha *= 0.988;
}
let rafId = null;
function warm(a) {
  alpha = Math.max(alpha, a);
  if (rafId) return;
  const loop = () => {
    step(); position();
    rafId = alpha > 0.005 ? requestAnimationFrame(loop) : null;
  };
  rafId = requestAnimationFrame(loop);
}

// ---- SVG 構築 --------------------------------------------------------------
const svg = document.getElementById("svg"), vp = document.getElementById("viewport");
const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs) => {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
};
links.forEach(l => {
  l.line = el("line", {class: "link", "marker-end": "url(#arr)"});
  l.hit = el("line", {stroke: "transparent", "stroke-width": "10"});
  l.hit.style.pointerEvents = "stroke";
  document.getElementById("glinks").append(l.line, l.hit);
  l.text = el("text", {class: "edgelabel", "text-anchor": "middle"});
  l.text.textContent = relDef[l.type].label;
  document.getElementById("glabels").appendChild(l.text);
});
nodes.forEach(n => {
  n.g = el("g", {class: "node", tabindex: "0", role: "button"});
  const pr = probsOf[n.id];
  if (pr) {
    n.ringEl = el("circle", {class: "ring", r: n.r + 4});
    n.ringEl.style.stroke = pr.error ? "var(--err)" : "var(--warn)";
    n.g.appendChild(n.ringEl);
  }
  n.circle = el("circle", {r: n.r});
  n.text = el("text", {dy: n.r + 13, "text-anchor": "middle"});
  // 長文ラベル（文を label_field にする種別）は切り詰める。全文は
  // ツールチップと詳細パネルで見せる
  n.text.textContent = n.label.length > 20 ? n.label.slice(0, 19) + "…" : n.label;
  n.g.append(n.circle, n.text);
  if (n.status === "deprecated") n.g.classList.add("deprecated");
  n.g.addEventListener("keydown", ev => {
    if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select({kind:"item", ref:n}); }
  });
  n.g.addEventListener("pointerenter", ev => showTip(ev,
    `<b>${esc(n.label)}</b><br>${esc(typeDef[n.type].label)} · ${esc(n.id)}`));
  n.g.addEventListener("pointerleave", hideTip);
  n.g.addEventListener("pointerdown", ev => startDrag(n, ev));
  document.getElementById("gnodes").appendChild(n.g);
});
links.forEach(l => {
  l.hit.addEventListener("pointerenter", ev => {
    const extra = Object.entries(l.attrs).map(([k,v]) =>
      `<br>${esc(k)}: ${esc(String(v))}`).join("");
    showTip(ev, `<b>${esc(relDef[l.type].label)}</b> (${esc(l.type)})<br>` +
      `${esc(l.s.label)} → ${esc(l.t.label)}${extra}`);
  });
  l.hit.addEventListener("pointerleave", hideTip);
  l.hit.addEventListener("pointerdown", ev => ev.stopPropagation());
  l.hit.addEventListener("pointerup", ev => {
    ev.stopPropagation(); select({kind:"rel", ref:l});
  });
});
function paint() {   // テーマ依存色の適用（詳細・一覧のスウォッチも描き直す）
  nodes.forEach(n => { n.circle.style.fill = typeColor(n.type); });
  renderDetail(); renderTable();
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paint);
new MutationObserver(paint).observe(document.documentElement,
  {attributes: true, attributeFilter: ["data-theme"]});

// ---- 表示状態 --------------------------------------------------------------
const hiddenTypes = new Set(), hiddenRels = new Set();
let query = "", selected = null, showEdgeLabels = false, tableMode = false;
const nodeVisible = n => !hiddenTypes.has(n.type);
const linkVisible = l => !hiddenRels.has(l.type) && nodeVisible(l.s) && nodeVisible(l.t);

function refresh() {
  const q = query.trim().toLowerCase();
  const match = n => !q || n.label.toLowerCase().includes(q) ||
                     n.id.toLowerCase().includes(q);
  const neigh = new Set();
  if (selected && selected.kind === "item") {
    neigh.add(selected.ref.id);
    links.forEach(l => {
      if (l.s === selected.ref) neigh.add(l.t.id);
      if (l.t === selected.ref) neigh.add(l.s.id);
    });
  }
  nodes.forEach(n => {
    n.g.style.display = nodeVisible(n) ? "" : "none";
    const dim = (q && !match(n)) ||
                (neigh.size && !neigh.has(n.id));
    n.g.classList.toggle("dim", !!dim);
    n.g.classList.toggle("sel", !!(selected && selected.kind === "item" &&
                                   selected.ref === n));
  });
  links.forEach(l => {
    const vis = linkVisible(l);
    l.line.style.display = vis ? "" : "none";
    l.hit.style.display = vis ? "" : "none";
    const hi = selected && (
      (selected.kind === "item" && (l.s === selected.ref || l.t === selected.ref)) ||
      (selected.kind === "rel" && l === selected.ref));
    l.line.classList.toggle("hi", !!hi);
    l.line.setAttribute("marker-end", hi ? "url(#arrHi)" : "url(#arr)");
    const dim = (neigh.size || q) && !hi;
    l.line.classList.toggle("dim", !!dim);
    l.text.style.display = vis && (showEdgeLabels || hi) && !dim ? "" : "none";
  });
  renderTable();
}
function position() {
  nodes.forEach(n => n.g.setAttribute("transform", `translate(${n.x},${n.y})`));
  links.forEach(l => {
    const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.sqrt(dx*dx+dy*dy) || 1;
    // 矢印がノード縁で止まるよう半径ぶん短縮
    const sx = l.s.x + dx/d*l.s.r, sy = l.s.y + dy/d*l.s.r;
    const tx = l.t.x - dx/d*(l.t.r+3), ty = l.t.y - dy/d*(l.t.r+3);
    for (const ln of [l.line, l.hit]) {
      ln.setAttribute("x1", sx); ln.setAttribute("y1", sy);
      ln.setAttribute("x2", tx); ln.setAttribute("y2", ty);
    }
    l.text.setAttribute("x", (sx+tx)/2); l.text.setAttribute("y", (sy+ty)/2 - 4);
  });
}

// ---- パン / ズーム / ドラッグ -----------------------------------------------
let view = {x: 0, y: 0, k: 1};
const applyView = () =>
  vp.setAttribute("transform", `translate(${view.x},${view.y}) scale(${view.k})`);
function fit() {
  const vis = nodes.filter(nodeVisible);
  if (!vis.length) return;
  const xs = vis.map(n => n.x), ys = vis.map(n => n.y);
  const x0 = Math.min(...xs)-60, x1 = Math.max(...xs)+60;
  const y0 = Math.min(...ys)-60, y1 = Math.max(...ys)+60;
  const w = svg.clientWidth, h = svg.clientHeight;
  view.k = Math.min(2, w/(x1-x0), h/(y1-y0));
  view.x = w/2 - (x0+x1)/2*view.k; view.y = h/2 - (y0+y1)/2*view.k;
  applyView();
}
svg.addEventListener("wheel", ev => {
  ev.preventDefault();
  const k = Math.min(4, Math.max(0.15, view.k * (ev.deltaY < 0 ? 1.15 : 0.87)));
  const r = svg.getBoundingClientRect();
  const mx = ev.clientX - r.left, my = ev.clientY - r.top;
  view.x = mx - (mx - view.x) * k / view.k;
  view.y = my - (my - view.y) * k / view.k;
  view.k = k; applyView();
}, {passive: false});
// クリックとドラッグは移動量しきい値で区別する（ポインタキャプチャ中は
// click イベントの標的が変わるため、click には依存しない）。
let panFrom = null, dragNode = null, downAt = null, moved = false;
svg.addEventListener("pointerdown", ev => {
  if (dragNode) return;
  panFrom = {x: ev.clientX - view.x, y: ev.clientY - view.y};
  downAt = {x: ev.clientX, y: ev.clientY}; moved = false;
  svg.classList.add("panning"); svg.setPointerCapture(ev.pointerId);
});
svg.addEventListener("pointermove", ev => {
  if (downAt && Math.hypot(ev.clientX - downAt.x, ev.clientY - downAt.y) > 3) moved = true;
  if (dragNode) {
    if (!moved) return;
    const r = svg.getBoundingClientRect();
    dragNode.x = (ev.clientX - r.left - view.x) / view.k;
    dragNode.y = (ev.clientY - r.top - view.y) / view.k;
    warm(0.12); return;
  }
  if (panFrom) { view.x = ev.clientX - panFrom.x; view.y = ev.clientY - panFrom.y; applyView(); }
});
const endPointer = ev => {
  if (!moved) {
    if (dragNode) select({kind: "item", ref: dragNode});
    else if (downAt && selected) { selected = null; refresh(); renderDetail(); }
  }
  panFrom = null; dragNode = null; downAt = null;
  svg.classList.remove("panning");
};
svg.addEventListener("pointerup", endPointer);
svg.addEventListener("pointercancel", endPointer);
function startDrag(n, ev) {
  ev.stopPropagation(); dragNode = n;
  downAt = {x: ev.clientX, y: ev.clientY}; moved = false;
  svg.setPointerCapture(ev.pointerId);
}

// ---- ツールチップ ------------------------------------------------------------
const tip = document.getElementById("tip");
function showTip(ev, html) {
  tip.innerHTML = html; tip.style.display = "block";
  const r = document.getElementById("stage").getBoundingClientRect();
  tip.style.left = Math.min(ev.clientX - r.left + 14, r.width - 330) + "px";
  tip.style.top = (ev.clientY - r.top + 12) + "px";
}
function hideTip() { tip.style.display = "none"; }

// ---- 左パネル ----------------------------------------------------------------
const esc = s => String(s).replace(/[&<>"']/g,
  c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function buildFilters() {
  const ft = document.getElementById("ftypes");
  DB.itemTypes.forEach(t => {
    const n = nodes.filter(x => x.type === t.name).length;
    const row = document.createElement("label");
    row.className = "chk";
    row.innerHTML = `<input type="checkbox" checked><span class="sw"></span>` +
      `<span>${esc(t.label)}</span><span class="n">${n}</span>`;
    row.querySelector(".sw").style.background = typeColor(t.name);
    row.querySelector("input").addEventListener("change", ev => {
      ev.target.checked ? hiddenTypes.delete(t.name) : hiddenTypes.add(t.name);
      refresh();
    });
    ft.appendChild(row);
    new MutationObserver(() => {
      row.querySelector(".sw").style.background = typeColor(t.name);
    }).observe(document.documentElement, {attributes:true, attributeFilter:["data-theme"]});
  });
  const fr = document.getElementById("frels");
  DB.relationTypes.forEach(r => {
    const n = links.filter(l => l.type === r.name).length;
    const row = document.createElement("label");
    row.className = "chk rel";
    row.innerHTML = `<input type="checkbox" checked><span class="sw"></span>` +
      `<span>${esc(r.label)}</span><span class="n">${n}</span>`;
    row.querySelector("input").addEventListener("change", ev => {
      ev.target.checked ? hiddenRels.delete(r.name) : hiddenRels.add(r.name);
      refresh();
    });
    fr.appendChild(row);
  });
  const pr = document.getElementById("problems");
  if (!DB.problems.length) {
    pr.innerHTML = `<div class="empty">✓ error 0 件 / warn 0 件</div>`;
  } else {
    DB.problems.forEach(p => {
      const row = document.createElement("div");
      row.className = "p";
      row.innerHTML = `<span class="lv ${p.level}">${p.level}</span>` +
        `<span class="tx"><b>${esc(p.where)}</b> — ${esc(p.message)}</span>`;
      if (byId[p.where]) row.addEventListener("click",
        () => select({kind:"item", ref: byId[p.where]}));
      pr.appendChild(row);
    });
  }
}
document.getElementById("q").addEventListener("input", ev => {
  query = ev.target.value; refresh();
});

// ---- ヘッダ ------------------------------------------------------------------
function buildHeader() {
  document.getElementById("meta").textContent =
    `${DB.root} @ ${DB.rev} · ${DB.generatedAt}`;
  const errs = DB.problems.filter(p => p.level === "error").length;
  const warns = DB.problems.length - errs;
  document.getElementById("stats").innerHTML =
    `<span class="stat"><b>${nodes.length}</b><span>アイテム</span></span>` +
    `<span class="stat"><b>${links.length}</b><span>関係</span></span>` +
    `<span class="stat ${errs ? "err" : "zero"}"><b>${errs}</b><span>error</span></span>` +
    `<span class="stat ${warns ? "warn" : "zero"}"><b>${warns}</b><span>warn</span></span>`;
}

// ---- 詳細パネル ---------------------------------------------------------------
function select(sel) {
  selected = sel; refresh(); renderDetail();
  if (tableMode && sel.kind === "item") toggleTable(false);
}
function fmtSource(src) {
  if (!src || !src.length) return "";
  return src.map(e => {
    const loc = Object.entries(e.location || {}).map(([k,v]) => `${k}=${v}`).join(", ");
    return `<div class="src">${esc(e.doc)}${loc ? ` <span>(${esc(loc)})</span>` : ""}` +
      (e.evidence ? `<div class="ev">“${esc(e.evidence)}”</div>` : "") + `</div>`;
  }).join("");
}
function attrsTable(attrs) {
  const rows = Object.entries(attrs);
  if (!rows.length) return "";
  return `<table class="attrs">` + rows.map(([k,v]) =>
    `<tr><td class="k">${esc(k)}</td><td class="v">${esc(String(v))}</td></tr>`
  ).join("") + `</table>`;
}
function renderDetail() {
  const d = document.getElementById("detail");
  if (!selected) {
    d.innerHTML = `<div class="placeholder">ノードまたはエッジを選択すると、` +
      `属性・出典・関係がここに表示されます。</div>`;
    return;
  }
  if (selected.kind === "rel") {
    const l = selected.ref;
    d.innerHTML =
      `<div class="sec"><span class="chip">${esc(relDef[l.type].label)} (${esc(l.type)})</span> ` +
      `<span class="pill ${esc(l.status)}">${esc(STATUS_LABEL[l.status] || l.status)}</span></div>` +
      `<h3>${esc(l.s.label)} → ${esc(l.t.label)}</h3>` +
      `<div class="idline">${esc(l.from)} → ${esc(l.to)}</div>` +
      attrsTable(l.attrs) + fmtSource(l.source);
    return;
  }
  const n = selected.ref;
  const probs = DB.problems.filter(p => p.where === n.id);
  const outRels = links.filter(l => l.s === n), inRels = links.filter(l => l.t === n);
  const relRow = (l, other, arrow) =>
    `<div class="relrow"><span class="rl">${esc(relDef[l.type].label)} ${arrow}</span> ` +
    `<a data-id="${esc(other.id)}">${esc(other.label)}</a>` +
    (Object.keys(l.attrs).length ? ` <span class="rl">· ${esc(Object.entries(l.attrs)
       .map(([k,v]) => `${k}=${v}`).join(", "))}</span>` : "") + `</div>`;
  d.innerHTML =
    `<div class="sec"><span class="chip"><span class="sw" style="background:${typeColor(n.type)}"></span>` +
    `${esc(typeDef[n.type].label)}</span> ` +
    `<span class="pill ${esc(n.status)}">${esc(STATUS_LABEL[n.status] || n.status)}</span></div>` +
    `<h3>${esc(n.label)}</h3><div class="idline">${esc(n.id)}</div>` +
    probs.map(p => `<div class="prob ${p.level}">${esc(p.message)}</div>`).join("") +
    attrsTable(n.attrs) + fmtSource(n.source) +
    (outRels.length ? `<div class="sec"><h2 style="font-size:10.5px;letter-spacing:.08em;
       color:var(--muted);text-transform:uppercase;margin:14px 0 4px">この項目から</h2>` +
       outRels.map(l => relRow(l, l.t, "→")).join("") + `</div>` : "") +
    (inRels.length ? `<div class="sec"><h2 style="font-size:10.5px;letter-spacing:.08em;
       color:var(--muted);text-transform:uppercase;margin:14px 0 4px">この項目へ</h2>` +
       inRels.map(l => relRow(l, l.s, "←")).join("") + `</div>` : "");
  d.querySelectorAll("a[data-id]").forEach(a =>
    a.addEventListener("click", () => select({kind:"item", ref: byId[a.dataset.id]})));
}

// ---- 一覧表示 -----------------------------------------------------------------
function renderTable() {
  if (!tableMode) return;
  const tv = document.getElementById("tableview");
  const q = query.trim().toLowerCase();
  const rows = nodes.filter(nodeVisible).filter(n =>
    !q || n.label.toLowerCase().includes(q) || n.id.toLowerCase().includes(q));
  tv.innerHTML = `<table><thead><tr><th>種別</th><th>ラベル</th><th>ID</th>` +
    `<th>status</th><th>関係数</th><th>検証</th></tr></thead><tbody>` +
    rows.map(n => {
      const pr = probsOf[n.id];
      return `<tr data-id="${esc(n.id)}">` +
        `<td><span class="sw" style="background:${typeColor(n.type)}"></span>` +
        `${esc(typeDef[n.type].label)}</td>` +
        `<td>${esc(n.label)}</td><td>${esc(n.id)}</td>` +
        `<td>${esc(STATUS_LABEL[n.status] || n.status)}</td><td>${n.deg}</td>` +
        `<td>${pr ? (pr.error ? `error ${pr.error}` : "") +
                    (pr.warn ? ` warn ${pr.warn}` : "") : "—"}</td></tr>`;
    }).join("") + `</tbody></table>`;
  tv.querySelectorAll("tr[data-id]").forEach(tr =>
    tr.addEventListener("click", () => select({kind:"item", ref: byId[tr.dataset.id]})));
}
function toggleTable(on) {
  tableMode = on;
  document.getElementById("tableview").style.display = on ? "block" : "none";
  svg.style.display = on ? "none" : "block";
  document.getElementById("btnTable").classList.toggle("on", on);
  renderTable();
}

// ---- ツールバー ---------------------------------------------------------------
document.getElementById("btnFit").addEventListener("click", fit);
document.getElementById("btnRelayout").addEventListener("click", () => {
  seed(); warm(1); setTimeout(fit, 700);
});
document.getElementById("btnLabels").addEventListener("click", ev => {
  showEdgeLabels = !showEdgeLabels;
  ev.currentTarget.classList.toggle("on", showEdgeLabels);
  refresh();
});
document.getElementById("btnTable").addEventListener("click", () => toggleTable(!tableMode));

// ---- 起動 --------------------------------------------------------------------
buildHeader(); buildFilters(); paint(); refresh();
seed(); warm(1);
setTimeout(fit, 750); setTimeout(fit, 2200);   // 収束途中と収束後に画面へ収める
addEventListener("resize", fit);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    sys.exit(main())
