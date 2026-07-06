# -*- coding: utf-8 -*-
"""init — 消費側プロジェクトに空の .specdb seed を作る（scaffold を実体化する）

    specdb init                         # cwd に .specdb を作る（空 seed。extends 標準パック）
    specdb init <プロジェクトdir>          # 指定ディレクトリ直下に .specdb を作る
    specdb init --with-samples           # 学習用に scaffold のサンプル一式も入れる
    specdb init --pack jp-sier-std@1.1   # metamodel の extends 行を差し替える
    specdb init --into <dir>             # .specdb ではなく指定パスをデータルートにする
    specdb init --force                  # 既存の非空データルートを上書きする

seed は「メタモデル（extends 標準パック）+ sync.yaml + README + 空の items/ relations/」。
要件〜詳細設計の標準種別・工程間トレース・文書様式はすべて継承パック（jp-sier-std）が
持つので、プロジェクトは資料から抽出したアイテムを items/ に足していくだけでよい。

生成後に engine 検証を回して error 0 を確認し、継承チェーンが解決できれば pack.lock も
書く。これで消費側は最初から `specdb engine`（error 0）と `specdb conform --frozen` が
通る骨格から始められる。scaffold そのものは pack build の対にあたる「パック → 空プロジェクト」
の実体化で、ツール本体（*.py）・標準パック（packs/）・自己仕様（.specdb）は持ち込まない。
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from engine import DEFAULT_DATA_DIR, Store

TOOL_DIR = Path(__file__).resolve().parent

# 常にコピーする単一ファイル（metamodel は extends 差し替えのため別扱い）
_SEED_FILES = ("sync.yaml", "README.md")
# サンプルとして中身も入れうるデータディレクトリ
_DATA_DIRS = ("items", "relations", "documents")


def find_seed(tool_dir: Path = TOOL_DIR) -> Path | None:
    """scaffold seed のディレクトリを返す。

    展開済みスキルでは scripts/ の隣に scaffold/ が置かれ、開発リポジトリでは
    ツールディレクトリ（specdb/）自身が seed を兼ねる。どちらも metamodel.yaml の
    有無で判定する。"""
    for cand in (tool_dir.parent / "scaffold", tool_dir):
        if (cand / "metamodel.yaml").is_file():
            return cand
    return None


def _rewrite_extends(mm_text: str, pack: str) -> str:
    """metamodel.yaml の `extends:` 行を差し替える（無ければ version 行の直後に挿入）。"""
    if re.search(r"(?m)^extends:.*$", mm_text):
        return re.sub(r"(?m)^extends:.*$", f"extends: {pack}", mm_text)
    if re.search(r"(?m)^version:.*$", mm_text):
        return re.sub(r"(?m)^(version:.*)$", rf"\1\nextends: {pack}", mm_text, count=1)
    return f"extends: {pack}\n" + mm_text


def _copy_data_dir(src: Path, dst: Path) -> None:
    """items/ relations/ documents/ を .yaml だけ・階層を保ってコピーする。"""
    for f in sorted(src.rglob("*.yaml")):
        rel = f.relative_to(src)
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, dst / rel)


# seed が管理する要素（--force はこれだけを掃除し、out/ 等ユーザー成果物は残す）
_MANAGED = ("metamodel.yaml", "sync.yaml", "README.md", "pack.lock",
            "items", "relations", "documents")


def _clean_managed(target: Path) -> None:
    """再生成前に seed 管理下の要素だけを消す（--force）。"""
    for name in _MANAGED:
        p = target / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


def instantiate(seed: Path, target: Path, with_samples: bool,
                pack: str | None, force: bool = False) -> list[str]:
    """seed から target（データルート）へ骨格を書き出し、作った要素名を返す。"""
    target.mkdir(parents=True, exist_ok=True)
    if force:
        _clean_managed(target)
    created: list[str] = []

    mm_text = (seed / "metamodel.yaml").read_text(encoding="utf-8")
    if pack:
        mm_text = _rewrite_extends(mm_text, pack)
    (target / "metamodel.yaml").write_text(mm_text, encoding="utf-8", newline="\n")
    created.append("metamodel.yaml")

    for name in _SEED_FILES:
        if (seed / name).is_file():
            shutil.copyfile(seed / name, target / name)
            created.append(name)

    for d in _DATA_DIRS:
        out = target / d
        if with_samples and (seed / d).is_dir():
            _copy_data_dir(seed / d, out)
            created.append(f"{d}/ (サンプル)")
        elif d in ("items", "relations"):
            # 空 seed でも items/ relations/ は用意する（.gitkeep で追跡させる。
            # engine はサブディレクトリのみ種別として読むので誤読しない）。
            out.mkdir(parents=True, exist_ok=True)
            (out / ".gitkeep").write_text("", encoding="utf-8", newline="\n")
            created.append(f"{d}/ (空)")
    return created


def _write_lock(target: Path) -> str | None:
    """継承チェーンが解決できれば pack.lock を書き、チェーン表記を返す。"""
    import standard
    from engine import Problem

    problems: list[Problem] = []
    packs = standard.resolve_chain(target, problems)
    if not packs or any(p.level == "error" for p in problems):
        return None
    standard.write_lock(target, packs)
    return " → ".join(f"{p.name}@{p.version}" for p in packs)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    target_arg: str | None = None
    into: Path | None = None
    with_samples = force = False
    pack: str | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help"):
            print(__doc__.strip())
            return 0
        elif a == "--with-samples":
            with_samples = True; i += 1
        elif a == "--force":
            force = True; i += 1
        elif a == "--pack" and i + 1 < len(args):
            pack = args[i + 1]; i += 2
        elif a == "--into" and i + 1 < len(args):
            into = Path(args[i + 1]); i += 2
        elif not a.startswith("-"):
            if target_arg is not None:
                print(f"位置引数が多い: {a}", file=sys.stderr)
                return 2
            target_arg = a; i += 1
        else:
            print(__doc__.strip(), file=sys.stderr)
            return 2

    if into is not None:
        target = into
    else:
        base = Path(target_arg) if target_arg else Path.cwd()
        target = base / DEFAULT_DATA_DIR

    if target.exists() and any(target.iterdir()) and not force:
        print(f"{target} は既に存在し空でない。--force で上書きする。", file=sys.stderr)
        return 1

    seed = find_seed()
    if seed is None:
        print(f"scaffold seed が見つからない（探索: {TOOL_DIR.parent / 'scaffold'}, "
              f"{TOOL_DIR}）。", file=sys.stderr)
        return 1

    created = instantiate(seed, target, with_samples, pack, force=force)

    store = Store.load(target)
    errors = [p for p in store.problems if p.level == "error"]
    for p in store.problems:
        print(p, file=sys.stderr)

    print(f"{target} に seed を作成した:")
    for c in created:
        print(f"  + {c}")

    lock_chain = _write_lock(target)
    if lock_chain:
        print(f"  + pack.lock（チェーン: {lock_chain}）")

    if errors:
        print(f"検証 error {len(errors)} 件（上記）。seed は残したので metamodel を"
              "確認すること。", file=sys.stderr)
        return 1

    print("検証 error 0。次の手順:")
    print("  1) 資料を抽出・洗い出し（docextract → spec-extractor）してファクト化する")
    print("  2) spec-designer で items/ にアイテムを status: review で足す")
    print("  3) specdb engine（error 0）→ specdb generate / visualize でビュー生成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
