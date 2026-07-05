# -*- coding: utf-8 -*-
"""標準パックの補助操作

    python specdb/pack.py lock    # pack.lock を解決結果から生成/更新する

pack.lock は継承チェーンの解決結果（版・内容ハッシュ）を固定する。CI は
`specdb conform --frozen` で lock と実際の解決結果の一致を機械的に検査できる。
"""
from __future__ import annotations

import sys
from pathlib import Path

import standard
from engine import Problem, parse_root


def main() -> int:
    root, args = parse_root(sys.argv[1:])
    action = args[0] if args else None
    if action != "lock":
        print("使い方: specdb pack lock", file=sys.stderr)
        return 2

    problems: list[Problem] = []
    packs = standard.resolve_chain(root, problems)
    for p in problems:
        print(p, file=sys.stderr)
    if any(p.level == "error" for p in problems):
        print("チェーンを解決できないため lock を更新しなかった。", file=sys.stderr)
        return 1
    if not packs:
        print("extends が宣言されていない（lock は不要）。", file=sys.stderr)
        return 0
    lock = standard.write_lock(root, packs)
    chain = " → ".join(f"{p.name}@{p.version}" for p in packs)
    print(f"pack.lock を更新した: {lock}")
    print(f"  チェーン: {chain}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
