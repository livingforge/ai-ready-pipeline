# -*- coding: utf-8 -*-
"""一覧 — アイテム/関係を status などで絞って列挙する（読み取り専用）

    python specdb/list.py --status review           # レビュー中を一覧
    python list.py --status review --json            # 機械可読（ref 配列→plan 生成に流せる）
    python list.py --status review --type function   # 種別でさらに絞る
    python list.py --kind relation --status review   # 関係だけ
    python list.py --root <データディレクトリ> …

engine の Store をそのまま読むだけで、YAML は書き換えない。承認（mutate approve /
plan の apply）の前段で「どれが対象か」をツールで列挙するために使う。ref は mutate の
参照形式（アイテムは id、関係は `型:from->to`）で出すので、--json 出力の各 ref を
そのまま plan.json の approve 操作へ渡せる。特定のアイテム種別の知識は持たない。
"""
from __future__ import annotations

import json
import sys

from engine import STATUSES, Store, parse_root


def _rel_ref(r) -> str:
    return f"{r.type}:{r.src}->{r.dst}"


def collect(store: Store, kind: str | None = None,
            status: str | None = None, type_: str | None = None) -> list[dict]:
    """条件に合うアイテム/関係を {kind, ref, type, status, label} の行にして返す。"""
    rows: list[dict] = []
    if kind in (None, "item"):
        for i in sorted(store.items.values(), key=lambda x: x.id):
            if (status is None or i.status == status) and (type_ is None or i.type == type_):
                rows.append({"kind": "item", "ref": i.id, "type": i.type,
                             "status": i.status, "label": i.label(store.mm)})
    if kind in (None, "relation"):
        for r in store.relations:
            if (status is None or r.status == status) and (type_ is None or r.type == type_):
                ref = _rel_ref(r)
                rows.append({"kind": "relation", "ref": ref, "type": r.type,
                             "status": r.status, "label": ref})
    return rows


def render(rows: list[dict]) -> str:
    if not rows:
        return "該当なし\n"
    items = [x for x in rows if x["kind"] == "item"]
    rels = [x for x in rows if x["kind"] == "relation"]
    lines: list[str] = []
    if items:
        lines.append(f"アイテム ({len(items)} 件):")
        lines += [f"  {x['ref']:<22} [{x['status']}] {x['type']} — {x['label']}"
                  for x in items]
    if rels:
        lines.append(f"関係 ({len(rels)} 件):")
        lines += [f"  {x['ref']:<30} [{x['status']}] {x['type']}" for x in rels]
    return "\n".join(lines) + "\n"


def main() -> int:
    # Windows の cp932 コンソールでも非 ASCII を出せるよう UTF-8 に固定する
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data_root, args = parse_root(sys.argv[1:])
    kind = status = type_ = None
    as_json = False
    i = 0
    while i < len(args):
        if args[i] == "--status" and i + 1 < len(args):
            status = args[i + 1]; i += 2
        elif args[i] == "--type" and i + 1 < len(args):
            type_ = args[i + 1]; i += 2
        elif args[i] == "--kind" and i + 1 < len(args):
            kind = args[i + 1]; i += 2
        elif args[i] == "--json":
            as_json = True; i += 1
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2
    if status is not None and status not in STATUSES:
        print(f"不明な status '{status}'（{' | '.join(STATUSES)}）", file=sys.stderr)
        return 2
    if kind is not None and kind not in ("item", "relation"):
        print(f"不明な kind '{kind}'（item | relation）", file=sys.stderr)
        return 2

    store = Store.load(data_root)
    rows = collect(store, kind=kind, status=status, type_=type_)
    if as_json:
        sys.stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
