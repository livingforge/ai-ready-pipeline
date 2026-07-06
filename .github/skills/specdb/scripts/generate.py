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

from jinja2 import (ChoiceLoader, Environment, FileSystemLoader, PrefixLoader,
                    StrictUndefined, Undefined)

import standard
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


def make_env(store: Store, template_dirs: Path | list[Path],
             prefixes: dict[str, Path] | None = None) -> Environment:
    """テンプレート環境を作る。

    template_dirs はリストなら多層検索（先頭が優先 = プロジェクト → パック層）。
    prefixes は親層版の明示参照（{% extends "std/…" %} 用。standard.prefix_map）。
    """
    dirs = [template_dirs] if isinstance(template_dirs, Path) else list(template_dirs)
    loader = FileSystemLoader([str(d) for d in dirs])
    if prefixes:
        loader = ChoiceLoader([loader, PrefixLoader(
            {k: FileSystemLoader(str(d)) for k, d in prefixes.items()})])
    env = Environment(loader=loader,
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


def _expand_foreach(doc: dict, store: Store):
    """文書定義を出力単位に展開する。foreach が無ければ単一 (output, None)。"""
    if "foreach" not in doc:
        return [(doc["output"], None)]
    targets = []
    for it in store.items_of(doc["foreach"]):
        ctx = {**it.attrs, "id": it.id}
        targets.append((doc["output"].format(**ctx), it))
    return targets


def main(argv: list[str] | None = None) -> int:
    root, args = parse_root(sys.argv[1:] if argv is None else argv)
    only = args[0] if args else None
    out_dir = root / "out"

    store = Store.load(root)
    # 標準パックの継承チェーンは Store.load が解決済み（store.packs）。
    # extends が無ければ packs は空で従来動作のまま。
    packs = store.packs
    standard.check_template_overrides(root, packs, store.problems)
    defs = standard.collect_documents(root, packs, store.problems)
    for p in store.problems:
        print(p, file=sys.stderr)
    if store.has_errors():
        print("検証エラーのため生成を中止しました。", file=sys.stderr)
        return 1

    if only:
        all_names = [name for name, _ in defs]
        defs = [d for d in defs if d[0] == only]
        if not defs:
            print(f"文書定義 '{only}' が見つからない。候補: {', '.join(all_names)}",
                  file=sys.stderr)
            return 1

    env = make_env(store, standard.template_search_dirs(root, packs),
                   standard.prefix_map(packs))
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    rev = git_rev(root)
    # 変更履歴 (Git から意味的に再構成)。改訂履歴シート等が実履歴から埋まる。
    # Git 管理外・履歴なしなら空リストで、テンプレート側がフォールバックする。
    try:
        from history import collect_history
        data_history = collect_history(root)
    except Exception as exc:  # 履歴が取れなくても文書生成は止めない
        print(f"変更履歴を取得できませんでした ({exc})", file=sys.stderr)
        data_history = []
    out_dir.mkdir(exist_ok=True)

    n_out = 0
    for _name, doc in defs:
        template = env.get_template(doc["template"])
        # foreach: <種別> を宣言した文書定義は、その種別のアイテム 1 件ごとに
        # 1 ファイルを出力する。output は "{属性}" を含められ、item 属性 + id で
        # 展開される。テンプレートには item が追加で渡る。
        for out_rel, item in _expand_foreach(doc, store):
            text = template.render(
                doc=doc, item=item, store=store, mm=store.mm,
                generated_at=generated_at, data_rev=rev, data_history=data_history)
            dest = out_dir / out_rel
            dest.parent.mkdir(parents=True, exist_ok=True)   # 副ディレクトリ出力に対応
            dest.write_text(text, encoding="utf-8", newline="\n")
            print(f"生成しました: {dest}")
            n_out += 1

    warns = sum(1 for p in store.problems if p.level == "warn")
    print(f"  アイテム {len(store.items)} 件 / 関係 {len(store.relations)} 件 / "
          f"文書 {len(defs)} 件 / 警告 {warns} 件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
