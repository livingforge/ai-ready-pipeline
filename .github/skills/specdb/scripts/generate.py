# -*- coding: utf-8 -*-
"""汎用文書ジェネレータ

documents/*.yaml（文書定義）を読み、対応する Jinja2 テンプレートに
検証済みの Store を渡してレンダリングする。特定のアイテム種別・文書種別の
知識はここには無い — すべて文書定義とテンプレート側にある。

    python specdb/generate.py                     # 全文書を生成
    python specdb/generate.py table-spec          # 指定した文書定義だけ生成
    python generate.py --root <データディレクトリ> [文書名]  # ツールとデータを分離
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, Undefined

from engine import ROOT, Store, parse_root

STATUS_LABEL = {"draft": "起票", "review": "レビュー中",
                "approved": "承認済", "deprecated": "廃止"}


def fmt_source(src) -> str:
    """出典（エンジンが正規化したリスト。旧来の単数マップも受ける）を整形する。"""
    if not src or isinstance(src, Undefined):
        return "—"
    entries = src if isinstance(src, list) else [src]
    parts = []
    for e in entries:
        loc = ", ".join(f"{k}={v}" for k, v in (e.get("location") or {}).items())
        parts.append(f"{e['doc']}" + (f" ({loc})" if loc else ""))
    return "; ".join(parts)


def fmt_evidence(src) -> str:
    """出典リストから原文（evidence）を取り出して結合する。"""
    if not src or isinstance(src, Undefined):
        return "—"
    entries = src if isinstance(src, list) else [src]
    texts = [e["evidence"] for e in entries if e.get("evidence")]
    return " / ".join(texts) if texts else "—"


def git_rev(root: Path) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except OSError:
        return "unknown"


def make_env(store: Store, templates_dir: Path) -> Environment:
    env = Environment(loader=FileSystemLoader(templates_dir),
                      trim_blocks=True, lstrip_blocks=True,
                      # HTML 文書ではアイテム値の < & " を自動エスケープする
                      # （Markdown 等それ以外のテンプレートには影響しない）
                      autoescape=lambda name: bool(name) and ".html" in name)
    env.filters["status"] = lambda s: STATUS_LABEL.get(s, s)
    env.filters["source"] = fmt_source
    env.filters["evidence"] = fmt_evidence
    env.filters["item_label"] = lambda iid: (
        store.items[iid].label(store.mm) if iid in store.items else iid)
    return env


def main() -> int:
    root, args = parse_root(sys.argv[1:])
    only = args[0] if args else None
    docs_dir, templates_dir, out_dir = root / "documents", root / "templates", root / "out"

    store = Store.load(root)
    for p in store.problems:
        print(p, file=sys.stderr)
    if store.has_errors():
        print("検証エラーのため生成を中止しました。", file=sys.stderr)
        return 1

    defs = sorted(docs_dir.glob("*.yaml"))
    if only:
        defs = [d for d in defs if d.stem == only]
        if not defs:
            print(f"文書定義 '{only}' が見つからない。候補: "
                  f"{', '.join(p.stem for p in sorted(docs_dir.glob('*.yaml')))}",
                  file=sys.stderr)
            return 1

    env = make_env(store, templates_dir)
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    rev = git_rev(root)
    out_dir.mkdir(exist_ok=True)

    for deffile in defs:
        with open(deffile, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        text = env.get_template(doc["template"]).render(
            doc=doc, store=store, mm=store.mm,
            generated_at=generated_at, data_rev=rev)
        dest = out_dir / doc["output"]
        dest.write_text(text, encoding="utf-8")
        print(f"生成しました: {dest}")

    warns = sum(1 for p in store.problems if p.level == "warn")
    print(f"  アイテム {len(store.items)} 件 / 関係 {len(store.relations)} 件 / "
          f"文書 {len(defs)} 件 / 警告 {warns} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
