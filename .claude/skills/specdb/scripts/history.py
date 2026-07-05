# -*- coding: utf-8 -*-
"""変更履歴ビュー — 仕様データの変遷を Git 履歴から意味的に再構成する

    python specdb/history.py                     # 全履歴 (Markdown、古い順)
    python specdb/history.py --id fn-visualize   # アイテム単位の変遷
    python specdb/history.py --json              # 機械可読 (JSON)
    python specdb/history.py --limit 5           # 直近 5 コミットぶん
    python specdb/history.py --uncommitted       # 未コミット分だけ (spec-sync の報告用)
    python history.py --root <データディレクトリ> …  # ツールとデータを分離して使う場合

仕様データ (metamodel.yaml / items/ / relations/) を触った各コミットについて、
直前の状態との差分を「アイテム・関係の追加/削除/変更」のレベルで復元する。
行レベルの差分 (git log -p) と違い、どの仕様がどう変わったかで読める。
未コミットの作業ツリー変更も末尾に「(未コミット)」として現れる。

前提: データルートは Git リポジトリ内のサブディレクトリ (例: .specdb/)。
generate.py はこの履歴を data_history としてテンプレートに渡すので、
生成設計書の改訂履歴シートは実履歴から自動で埋まる。

エンジン・diff 同様、特定のアイテム種別の知識は持たない。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from engine import Store, parse_root
from diff import _field_changes, _git, _rel_key, _state, store_at


def _clip(s: str, n: int = 120) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _item_row(store: Store, item) -> dict:
    tdef = store.mm.item_types.get(item.type) or {}
    return {"id": item.id, "type": item.type,
            "type_label": tdef.get("label", item.type),
            "label": item.label(store.mm)}


def _rel_row(store: Store, rel) -> dict:
    rdef = store.mm.relation_types.get(rel.type) or {}
    def label(iid):
        it = store.items.get(iid)
        return it.label(store.mm) if it else iid
    return {"type": rel.type, "from": rel.src, "to": rel.dst,
            "desc": f"{label(rel.src)} —{rdef.get('label', rel.type)}→ {label(rel.dst)}"}


def semantic_changes(a: Store, b: Store) -> dict | None:
    """2 つの Store の意味的差分。差分がなければ None。"""
    ch = {
        "items_added": [_item_row(b, b.items[i])
                        for i in sorted(b.items.keys() - a.items.keys())],
        "items_removed": [_item_row(a, a.items[i])
                          for i in sorted(a.items.keys() - b.items.keys())],
        "items_changed": [
            {**_item_row(b, b.items[i]),
             "fields": _field_changes(_state(a.items[i]), _state(b.items[i]))}
            for i in sorted(a.items.keys() & b.items.keys())
            if _state(a.items[i]) != _state(b.items[i])
            or a.items[i].type != b.items[i].type
        ],
    }
    rels_a = {_rel_key(r): r for r in a.relations}
    rels_b = {_rel_key(r): r for r in b.relations}
    ch["rels_added"] = [_rel_row(b, rels_b[k])
                        for k in sorted(rels_b.keys() - rels_a.keys())]
    ch["rels_removed"] = [_rel_row(a, rels_a[k])
                          for k in sorted(rels_a.keys() - rels_b.keys())]
    ch["rels_changed"] = [
        {**_rel_row(b, rels_b[k]),
         "fields": _field_changes(_state(rels_a[k]), _state(rels_b[k]))}
        for k in sorted(rels_a.keys() & rels_b.keys())
        if _state(rels_a[k]) != _state(rels_b[k])
    ]
    return ch if any(ch.values()) else None


def _summary(ch: dict) -> str:
    """変更内容の一行要約 (改訂履歴の「変更内容」欄に使える粒度)。"""
    def ids(rows, n=4):
        names = [r["id"] for r in rows]
        return "、".join(names[:n]) + ("…" if len(names) > n else "")
    parts = []
    if ch["items_added"]:
        parts.append(f"アイテム追加 {len(ch['items_added'])} ({ids(ch['items_added'])})")
    if ch["items_changed"]:
        parts.append(f"変更 {len(ch['items_changed'])} ({ids(ch['items_changed'])})")
    if ch["items_removed"]:
        parts.append(f"削除 {len(ch['items_removed'])} ({ids(ch['items_removed'])})")
    n_rel = len(ch["rels_added"]) + len(ch["rels_changed"]) + len(ch["rels_removed"])
    if n_rel:
        parts.append(f"関係 {n_rel} 件")
    return "・".join(parts)


def _touches(ch: dict, item_id: str) -> dict | None:
    """変更セットを指定アイテムに関わる部分だけに絞る。無関係なら None。"""
    out = {}
    for key in ("items_added", "items_removed", "items_changed"):
        out[key] = [r for r in ch[key] if r["id"] == item_id]
    for key in ("rels_added", "rels_removed", "rels_changed"):
        out[key] = [r for r in ch[key] if item_id in (r["from"], r["to"])]
    return out if any(out.values()) else None


def collect_history(data_root: Path, item_id: str | None = None,
                    limit: int | None = None,
                    include_worktree: bool = True) -> list[dict]:
    """仕様データの変更履歴 (古い順)。Git が使えなければ空リスト。

    各エントリ: rev / short / date / author / subject / initial /
    items_added … rels_changed / summary
    """
    top = _git("rev-parse", "--show-toplevel", cwd=data_root).stdout.strip()
    if not top:
        return []
    top_path = Path(top)
    prefix = data_root.resolve().relative_to(top_path).as_posix()
    # 変更者は %aN (mailmap 適用後の author 名)。ルートの .mailmap で別名を定義できる。
    log = _git("log", "--format=%H%x09%h%x09%ad%x09%aN%x09%s", "--date=short",
               "--", prefix, cwd=top_path)
    commits = []
    for line in reversed(log.stdout.splitlines()):
        full, short, date, author, subject = line.split("\t", 4)
        commits.append({"rev": full, "short": short, "date": date,
                        "author": author, "subject": subject})

    base_store = None
    if limit is not None and len(commits) > limit:
        base = commits[-limit - 1]
        commits = commits[-limit:]
        base_store = store_at(base["rev"], data_root)

    entries: list[dict] = []
    prev = base_store
    for c in commits:
        cur = store_at(c["rev"], data_root)
        if prev is None:  # 履歴上の最初のコミット = 初版
            ch = {k: [] for k in ("items_added", "items_removed", "items_changed",
                                  "rels_added", "rels_removed", "rels_changed")}
            entry = {**c, "initial": True, **ch,
                     "summary": f"初版 (アイテム {len(cur.items)} 件・"
                                f"関係 {len(cur.relations)} 件)"}
            if item_id is None or item_id in cur.items:
                entries.append(entry)
        else:
            ch = semantic_changes(prev, cur)
            if ch is not None:
                if item_id is not None:
                    ch = _touches(ch, item_id)
                if ch is not None:
                    entries.append({**c, "initial": False, **ch,
                                    "summary": _summary(ch)})
        prev = cur

    if include_worktree and prev is not None:
        wt = Store.load(data_root)
        ch = semantic_changes(prev, wt)
        if ch is not None and item_id is not None:
            ch = _touches(ch, item_id)
        if ch is not None:
            entries.append({"rev": None, "short": None,
                            "date": datetime.now().astimezone().date().isoformat(),
                            "author": "—", "subject": "(未コミット)",
                            "initial": False, **ch, "summary": _summary(ch)})
    return entries


# ---------- Markdown レンダリング ----------

def render_markdown(entries: list[dict], root_name: str,
                    item_id: str | None = None) -> str:
    title = f"# 仕様データ変更履歴 — {root_name}"
    if item_id:
        title += f" / {item_id}"
    lines = [title, ""]
    if not entries:
        lines.append("履歴なし (Git 管理外か、該当する変更が無い)。")
        return "\n".join(lines) + "\n"
    for i, e in enumerate(entries, 1):
        rev = e["short"] or "未コミット"
        head = f"## 版 {i} — {e['date']} `{rev}`"
        if e["subject"]:
            head += f" {e['subject']}"
        head += f"（{e['author']}）"
        lines += [head, ""]
        if e["initial"]:
            lines += [f"- {e['summary']}", ""]
            continue
        for key, verb in (("items_added", "追加"), ("items_removed", "削除")):
            for r in e[key]:
                lines.append(f"- アイテム{verb}: **{r['id']}**"
                             f"（{r['type_label']}）: {r['label']}")
        for r in e["items_changed"]:
            lines.append(f"- アイテム変更: **{r['id']}**"
                         f"（{r['type_label']}）: {r['label']}")
            lines += [f"  - {_clip(f)}" for f in r["fields"]]
        for key, verb in (("rels_added", "追加"), ("rels_removed", "削除")):
            for r in e[key]:
                lines.append(f"- 関係{verb}: {r['desc']} (`{r['type']}`)")
        for r in e["rels_changed"]:
            lines.append(f"- 関係変更: {r['desc']} (`{r['type']}`)")
            lines += [f"  - {_clip(f)}" for f in r["fields"]]
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    # Windows の cp932 コンソールでは em-dash 等が書けないため UTF-8 で出す
    # (レポートはリダイレクトしてファイル化する使い方が主)。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data_root, args = parse_root(sys.argv[1:])
    item_id = None
    limit = None
    as_json = False
    uncommitted = False
    i = 0
    while i < len(args):
        if args[i] == "--id" and i + 1 < len(args):
            item_id = args[i + 1]; i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1]); i += 2
        elif args[i] == "--json":
            as_json = True; i += 1
        elif args[i] == "--uncommitted":
            uncommitted = True; i += 1
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    entries = collect_history(data_root, item_id=item_id, limit=limit)
    if uncommitted:
        entries = [e for e in entries if e["rev"] is None]
    if as_json:
        sys.stdout.write(json.dumps(entries, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_markdown(entries, data_root.resolve().name, item_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
