# -*- coding: utf-8 -*-
"""標準パック準拠検証 — L1（メタモデル）+ L2（データ・文書）+ lock 照合

    python specdb/conform.py                 # 準拠検証（error で exit 1）
    python specdb/conform.py --for-baseline  # ベースライン前提（status_rules）も検査
    python specdb/conform.py --frozen        # pack.lock 不一致を error 扱いにする（CI 用）

L1（メタモデルのマージ + 緩和禁止）は Store.load が常時実行するので、ここでは
その結果に L2（conformance/rules.yaml）と lock 照合を足して総合判定する。
extends を持たないプロジェクトでは「パック未使用」と表示して 0 を返す。
"""
from __future__ import annotations

import sys
from pathlib import Path

import standard
from engine import Store, parse_root


def main() -> int:
    root, args = parse_root(sys.argv[1:])
    for_baseline = "--for-baseline" in args
    frozen = "--frozen" in args

    store = Store.load(root)              # L1（マージ + 準拠）を含む
    packs = store.packs
    if not packs:
        print("このプロジェクトは標準パックを継承していない（extends 未宣言）。")
        return 0

    standard.check_template_overrides(root, packs, store.problems)
    standard.check_conformance_rules(root, packs, store, store.problems,
                                     for_baseline=for_baseline)
    standard.verify_lock(root, packs, store.problems, frozen=frozen)

    for p in store.problems:
        print(p, file=sys.stderr)
    errors = sum(1 for p in store.problems if p.level == "error")
    warns = sum(1 for p in store.problems if p.level == "warn")
    chain = " → ".join(f"{p.name}@{p.version}" for p in packs)
    print(f"継承チェーン: {chain}")
    print(f"準拠検証: error {errors} 件 / warn {warns} 件")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
