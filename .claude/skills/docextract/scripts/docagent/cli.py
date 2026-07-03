"""docagent コマンドラインインターフェース (データ操作 API の入口)。

    python -m docagent <サブコマンド> [オプション]

サブコマンド一覧:
  現状把握 (doc-indexer):
    init          ストアと doctypes.json / facts.json を初期化
    add           docextract の result.json を取り込み登録
    sync          抽出マニフェストの全文書を一括で登録/更新
    prep          取り込み準備 (必要なら登録し、種別候補+本文抜粋を1回で返す)
    set-doctype   文書種別を設定 (定義内に正規化)
    doctypes      文書種別の表示・追加・削除
    list/query/stats/get/text/export/remove   参照・整理
  横断検索 (corpus-qa):
    search        本文を横断検索し出典 (doc_id + location) 付きで返す
  仕様の洗い出し (spec-extractor):
    fact-add / facts / fact-remove / facts-stats / facts-export / item-types

すべてのサブコマンドは ``--json`` で機械可読な JSON を出力する
(エージェントはこれをパースして次の操作を決める)。``--store`` で保存先を変更できる。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from docextract import config as _config
from docextract import obs as _obs
from docextract import paths as _paths

from .facts import FactStore
from .store import (
    DEFAULT_DOCTYPES,
    DEFAULT_STORE,
    DocAgentError,
    Library,
)


def _load(args: argparse.Namespace) -> Library:
    lib = Library.load(args.store, args.doctypes)
    # 登録時 preview の長さを config.json (preview_chars) で差し替える。
    lib.preview_chars = args.cfg["preview_chars"]
    return lib


def _load_facts(args: argparse.Namespace) -> FactStore:
    return FactStore.load(args.facts, args.item_types_file)


# --json は「機械が読む」出力なので既定はコンパクト (整形空白でトークンを倍増させない)。
# 人が目視したいときだけ --pretty で indent=2 に戻す。main() が実行時に設定する。
_PRETTY = False

# 数値ガード。main() が config.json (env DOCEXTRACT_HOME 準拠) から実行時に設定する。
# _CEILING: --json 出力の文字数上限。超えると拒否する (0 で無効)。ホスト (Claude
# Code の Bash 出力 30,000 字・Copilot のコンテキスト枯渇) が stdout を黙って切り
# 詰めて情報欠落するのを、その手前で検知して止めるための番人。
# _FORCE_STDOUT: --stdout。上限を承知で全出力を強制する脱出ハッチ。
_CEILING = _config.DEFAULTS["ceiling_chars"]
_FORCE_STDOUT = False


def _refuse_oversize(size: int, hint: str | None) -> None:
    """stdout が上限を超えたとき、絞り方を案内して終了する (fail-closed)。

    全出力すると呼び出し側 (LLM/エージェント) のコンテキストがホスト側で黙って
    切り詰められ、欠落したことにすら気づけない。手前で止め、どう絞るか・強制する
    にはどうするかを stderr で必ず案内する。上限は config.json で変更できる。
    """
    print(
        f"[guard] 出力 {size:,} 文字が上限 {_CEILING:,} 文字を超えます"
        f" (LLM/エージェントの stdout はこの辺りで切り詰められ、静かに欠落します)。\n"
        f"        {hint or '対象を絞るか、全出力を強制するなら --stdout を付けてください。'}\n"
        f'        上限は config.json の "ceiling_chars" で変更できます (0 で無効化)。',
        file=sys.stderr,
    )
    sys.exit(2)


def _emit(obj, as_json: bool, human, *, hint: str | None = None) -> None:
    """結果を出力する。``--json`` の場合のみ数値ガード (上限) を適用する。

    ``hint`` はコマンド固有の「絞り方」の案内 (例: text なら --max-chars を下げる)。
    人向け出力 (対話端末想定) はガードしない — 端末は自前でスクロールでき、欠落
    しないため。エージェントは常に ``--json`` を使うので、そこだけ守れば十分。
    """
    if as_json:
        s = json.dumps(obj, ensure_ascii=False, indent=2 if _PRETTY else None)
        if _CEILING and not _FORCE_STDOUT and len(s) > _CEILING:
            _refuse_oversize(len(s), hint)
        print(s)
    else:
        human(obj)


def _doc_line(d: dict) -> str:
    dt = d.get("doctype") or "—"
    preview = (d.get("preview") or "").replace("\n", " ")
    if len(preview) > 48:
        preview = preview[:48] + "…"
    return f"[{dt:12}] {d['id']:26} {preview}"


# 一覧 (list/query) の既定射影。分類・絞り込みに要る項目だけに絞り、preview も
# 短縮して、コーパス規模に比例した巨大な JSON が stdout に流れるのを防ぐ。
# 完全な dict が要るときは各コマンドの --full か、個別の `get <id>` で取る。
_LIST_PREVIEW_CHARS = 200


def _slim_doc(d: dict) -> dict:
    # 分類・報告に要る軽量フィールド (要素数 stats・出力先 result_path を含む) は残し、
    # かさむ metadata・content_hash・source_abspath・timestamp と 600 字 preview を落とす。
    preview = d.get("preview") or ""
    if len(preview) > _LIST_PREVIEW_CHARS:
        preview = preview[:_LIST_PREVIEW_CHARS] + "…"
    return {
        "id": d["id"],
        "source": d.get("source"),
        "file_type": d.get("file_type"),
        "doctype": d.get("doctype"),
        "stats": d.get("stats", {}),
        "result_path": d.get("result_path"),
        "preview": preview,
    }


def _project(docs: list[dict], full: bool) -> list[dict]:
    return docs if full else [_slim_doc(d) for d in docs]


# ── サブコマンド実装 ─────────────────────────────────────────
def cmd_init(args):
    lib = _load(args)
    lib.save()
    lib.save_doctypes()
    # ファクトストアと種別定義も同時に用意する (spec-extractor 用)。
    fs = _load_facts(args)
    fs.save()
    fs.save_item_types()
    # 数値ガードの既定値を config.json に敷く (既存は上書きせず利用者の編集を守る)。
    config_written = _config.write_defaults(args.config)
    _emit(
        {
            "store": str(lib.path),
            "facts": str(fs.path),
            "config": str(args.config),
            "config_created": config_written,
            "doctypes": lib.doctypes,
            "item_types": fs.item_types,
            "documents": len(lib.documents),
        },
        args.json,
        lambda o: print(
            f"初期化しました。\n  ストア: {o['store']}\n  ファクト: {o['facts']}\n"
            f"  設定: {o['config']}"
            + ("" if o["config_created"] else " (既存を保持)")
            + f"\n  文書種別: {', '.join(o['doctypes'])}\n"
            f"  ファクト種別: {', '.join(o['item_types'])}\n"
            f"  登録済み文書: {o['documents']} 件"
        ),
    )


def cmd_prep(args):
    lib = _load(args)
    # 明示 > config.json > 組み込み既定。未指定 (None) のとき config 値を使う。
    max_chars = args.max_chars if args.max_chars is not None else args.cfg["prep_max_chars"]
    payload = lib.prep(args.target, max_chars=max_chars)

    def human(o):
        state = "分類済み" if o["already_classified"] else "未分類"
        print(f"準備完了: {o['id']} (文書種別={o['doctype'] or '—'} / {state})")
        print(f"文書種別の候補: {', '.join(o['doctypes'])}")
        print(f"次の一手: {o['next_action']}")

    _emit(payload, args.json, human, hint="--max-chars を下げるか --stdout で全出力")


def cmd_add(args):
    lib = _load(args)
    entry = lib.add_from_result(args.result, overwrite=args.overwrite)
    lib.save()
    _emit(
        entry,
        args.json,
        lambda o: print(
            f"登録しました: {o['id']} (source={o['source']}, type={o['file_type']})"
        ),
    )


def cmd_set_doctype(args):
    lib = _load(args)
    doc_id, auto = _resolve_target(lib, args.id)
    doc = lib.set_doctype(doc_id, args.doctype, force=args.force)
    lib.save()
    normalized_from = args.doctype if args.doctype != doc["doctype"] else None
    payload = _doc_payload(doc, auto_registered=auto, doctype_normalized_from=normalized_from)

    def human(o):
        if auto:
            print(f"自動登録しました (前段の登録を補完): {doc_id}")
        if normalized_from:
            print(f"文書種別を正規化: 「{normalized_from}」→「{doc['doctype']}」")
        print(f"文書種別を設定: {o['id']} -> {o['doctype']}")

    _emit(payload, args.json, human)


def cmd_get(args):
    lib = _load(args)
    doc = lib.get(args.id)
    _emit(
        doc,
        args.json,
        lambda o: print(json.dumps(o, ensure_ascii=False, indent=2)),
        hint="本文だけなら text、全体を強制するなら --stdout",
    )


def cmd_text(args):
    lib = _load(args)
    # 明示 > config.json > 組み込み既定。--max-chars 0 は「全文」の明示指定で、
    # 既定は上限つきにして巨大文書の全文直流を防ぐ。
    max_chars = args.max_chars if args.max_chars is not None else args.cfg["text_max_chars"]
    max_chars = None if max_chars == 0 else max_chars
    doc = lib.extract_text(args.id, max_chars=max_chars, offset=args.offset)

    def human(o):
        print(o["text"])
        if o["truncated"]:
            print(
                f"\n… [{o['offset']}–{o['offset'] + o['returned_chars']} / "
                f"全 {o['total_chars']} 字]。続きは --offset {o['next_offset']}、"
                f"全文は --max-chars 0",
                file=sys.stderr,
            )

    _emit(doc, args.json, human, hint="--max-chars を下げる・--offset で分割・--stdout で強制")


def cmd_list(args):
    lib = _load(args)
    docs = _project(lib.documents, args.full)
    _emit(
        docs,
        args.json,
        lambda o: (
            print(f"登録文書 {len(o)} 件:")
            or [print("  " + _doc_line(d)) for d in o]
            or (print("  (なし)") if not o else None)
        ),
        hint="query で --doctype/--text で絞る、または --stdout で全出力",
    )


def cmd_query(args):
    lib = _load(args)
    docs = _project(lib.query(doctype=args.doctype, text=args.text), args.full)
    _emit(
        docs,
        args.json,
        lambda o: (
            print(f"該当 {len(o)} 件:") or [print("  " + _doc_line(d)) for d in o]
        ),
        hint="--doctype/--text でさらに絞る、または --stdout で全出力",
    )


def cmd_stats(args):
    lib = _load(args)
    s = lib.stats()

    def human(o):
        print(f"合計: {o['total']} 件")
        print("文書種別別:")
        for k, v in sorted(o["by_doctype"].items(), key=lambda x: -x[1]):
            print(f"  {k:16} {v}")

    _emit(s, args.json, human)


def _dump_or_refuse(data: dict, output: str | None, to_stdout: bool, summary: str, cmd: str) -> None:
    """全体ダンプ系 (export) の出力先を捌く。

    ファイル (`-o`) 指定があればそこへ。無い場合、**非対話実行で `--stdout` も
    無ければ拒否**する — ストア全体を標準出力へ直流すると呼び出し側 (LLM/
    エージェント) のコンテキストを一気に圧迫するため。対話端末や `--stdout`
    明示のときだけ標準出力へ出す。
    """
    if output:
        Path(output).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"書き出しました: {output} ({summary})")
        return
    interactive = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if not interactive and not to_stdout:
        print(
            f"[{cmd}] {summary} を標準出力に全出力するとコンテキストを圧迫します。"
            f"-o <ファイル> に書き出すか、全出力を強制するなら --stdout を付けてください。",
            file=sys.stderr,
        )
        sys.exit(2)
    print(json.dumps(data, ensure_ascii=False, indent=2 if _PRETTY else None))


def cmd_export(args):
    lib = _load(args)
    data = lib.export()
    _dump_or_refuse(
        data, args.output, args.stdout, f"{len(data['documents'])} 件の文書", "export"
    )


def cmd_remove(args):
    lib = _load(args)
    doc = lib.remove(args.id)
    lib.save()
    _emit(doc, args.json, lambda o: print(f"削除しました: {o['id']}"))


def cmd_doctypes(args):
    lib = _load(args)
    if args.action == "add" and args.name:
        lib.add_doctype(args.name)
        lib.save_doctypes()
        lib.save()
    elif args.action == "remove" and args.name:
        lib.remove_doctype(args.name)
        lib.save_doctypes()
        lib.save()
    _emit(
        lib.doctypes,
        args.json,
        lambda o: print("文書種別:\n" + "\n".join(f"  - {c}" for c in o)),
    )


# ── 現状把握 (doc-indexer): 抽出済みを一括登録 ───────────────────
def cmd_sync(args):
    lib = _load(args)
    manifest = args.manifest or str(_paths.manifest_path())
    result = lib.sync_from_manifest(manifest)
    lib.save()

    def human(o):
        print(
            f"索引を更新しました: 新規 {len(o['added'])} 件 / 更新 {len(o['updated'])} 件"
            f" / スキップ {len(o['skipped'])} 件"
        )
        if o["skipped"]:
            print(f"  スキップ (result.json 不明): {', '.join(o['skipped'])}")

    _emit(result, args.json, human)


# ── 横断検索 (corpus-qa): 出典付きグラウンデッド検索 ─────────────
def cmd_search(args):
    lib = _load(args)
    # 明示 > config.json > 組み込み既定。
    max_hits = args.max_hits if args.max_hits is not None else args.cfg["search_max_hits"]
    hits = lib.search(args.term, doc_id=args.doc, max_hits=max_hits)

    def human(o):
        print(f"「{args.term}」に一致 {len(o)} 件 (関連度順):")
        for h in o:
            loc = json.dumps(h["location"], ensure_ascii=False)
            print(f"  {h['doc_id']} [{h['kind']}] score={h['score']} {loc}")
            print(f"    {h['snippet']}")

    _emit(hits, args.json, human, hint="--max-hits を下げる・--doc で絞る・--stdout で強制")


# ── 仕様の洗い出し (spec-extractor): ファクト操作 ────────────────
def _fact_line(it: dict) -> str:
    conf = f" ({it['confidence']})" if it.get("confidence") else ""
    loc = json.dumps(it.get("location", {}), ensure_ascii=False)
    return f"{it['id']} [{it.get('type','?')}]{conf} {it.get('doc_id','?')} {loc}\n    {it.get('statement','')}"


def cmd_fact_add(args):
    fs = _load_facts(args)
    location = None
    if args.location:
        try:
            location = json.loads(args.location)
        except json.JSONDecodeError as e:
            raise DocAgentError(
                f"--location は JSON で指定してください (例: '{{\"page\": 3}}'): {e}"
            ) from e
    item = fs.add(
        doc_id=args.doc,
        type=args.type,
        statement=args.statement,
        evidence=args.evidence,
        location=location,
        keywords=_split_keywords(args.keywords),
        confidence=args.confidence,
        force=args.force,
    )
    fs.save()
    _emit(item, args.json, lambda o: print(f"追加しました: {o['id']} [{o['type']}] <- {o['doc_id']}"))


# 一覧での evidence (原文抜粋) の既定表示上限。原文はファクト件数ぶん積み上がるため
# 一覧では短縮し、全文が要るときは --full か facts-export で取る。
_FACT_EVIDENCE_CHARS = 200


def _slim_fact(it: dict) -> dict:
    ev = it.get("evidence")
    if isinstance(ev, str) and len(ev) > _FACT_EVIDENCE_CHARS:
        it = dict(it)
        it["evidence"] = ev[:_FACT_EVIDENCE_CHARS] + "…"
        it["evidence_truncated"] = True
    return it


def cmd_facts(args):
    fs = _load_facts(args)
    items = fs.query(doc_id=args.doc, type=args.type, text=args.text)
    payload = items if args.full else [_slim_fact(it) for it in items]
    _emit(
        payload,
        args.json,
        lambda o: (
            print(f"ファクト {len(o)} 件:") or [print("  " + _fact_line(it)) for it in o]
            or (print("  (なし)") if not o else None)
        ),
        hint="--doc/--type/--text で絞る、または --stdout で全出力",
    )


def cmd_fact_remove(args):
    fs = _load_facts(args)
    item = fs.remove(args.id)
    fs.save()
    _emit(item, args.json, lambda o: print(f"削除しました: {o['id']}"))


def cmd_facts_stats(args):
    fs = _load_facts(args)
    s = fs.stats()

    def human(o):
        print(f"ファクト合計: {o['total']} 件")
        print("種別別:")
        for k, v in sorted(o["by_type"].items(), key=lambda x: -x[1]):
            print(f"  {k:16} {v}")
        print("文書別:")
        for k, v in sorted(o["by_doc"].items(), key=lambda x: -x[1]):
            print(f"  {k:24} {v}")

    _emit(s, args.json, human)


def cmd_facts_export(args):
    fs = _load_facts(args)
    data = fs.export()
    _dump_or_refuse(
        data, args.output, args.stdout, f"{len(data['items'])} 件のファクト", "facts-export"
    )


def cmd_item_types(args):
    fs = _load_facts(args)
    if args.action == "add" and args.name:
        fs.add_item_type(args.name)
        fs.save_item_types()
        fs.save()
    elif args.action == "remove" and args.name:
        fs.remove_item_type(args.name)
        fs.save_item_types()
        fs.save()
    _emit(
        fs.item_types,
        args.json,
        lambda o: print("ファクト種別:\n" + "\n".join(f"  - {c}" for c in o)),
    )


# ── 補助 ─────────────────────────────────────────────────────
# キーワードの区切り揺れ (半角/全角カンマ・読点・セミコロン・改行) を吸収する。
_KEYWORD_DELIMS = re.compile(r"[,、，;；\n\r\t]+")


def _split_keywords(value: str | None) -> list[str] | None:
    """カンマ区切り想定の文字列を、区切り揺れを吸収しつつ語のリストへ。

    LLM は ``a、b`` ``a; b`` のように区切りを揺らすことがある。複数の区切りで
    分割し、前後空白除去・重複除去 (出現順維持) する。
    """
    if value is None:
        return None
    out: list[str] = []
    seen: set[str] = set()
    for k in _KEYWORD_DELIMS.split(value):
        k = k.strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _resolve_target(lib: Library, target: str) -> tuple[str, bool]:
    """set 系で ID の代わりに result.json パスが渡された事故を吸収する。

    前段の ``prep``/``add`` (登録) を飛ばして ``set`` を叩いても失敗しないよう、
    ターゲットが未登録かつ result.json のパスなら自動登録してから続行する。
    返り値は ``(doc_id, auto_registered)``。未登録 ID (パスでもない) の場合は
    そのまま返し、後続の ``get`` が「次の一手」付きエラーを出すのに委ねる。
    """
    if lib.find(target) is not None:
        return target, False
    if Path(target).is_file():
        # add_from_result は元ファイル直渡し・壊れた JSON を親切なエラーで弾く。
        entry = lib.add_from_result(target, overwrite=True)
        return entry["id"], True
    return target, False


def _doc_payload(doc: dict, **flags) -> dict:
    """出力用に doc のコピーへ透明化フラグを添える (ストアには保存しない)。

    ``auto_registered`` / ``doctype_normalized_from`` のように「スクリプトが
    何を自動補正したか」を呼び出し元へ必ず返し、黙って直さない。
    """
    payload = dict(doc)
    for k, v in flags.items():
        if v:
            payload[k] = v
    return payload


def build_parser() -> argparse.ArgumentParser:
    # 共通オプションは親パーサにまとめ、各サブコマンドにも継承させることで
    # `--json` / `--store` をサブコマンドの前後どちらに置いても効くようにする。
    # default=SUPPRESS が重要: サブパーサも同じ親を継承するため、通常の default だと
    # 「メインパーサで解析済みの値をサブパーサの default が上書きする」argparse の
    # 落とし穴があり、サブコマンドの前に置いた --store 等が黙って無視される。
    # SUPPRESS なら未指定時に属性を触らないので前置きの値が生き残る
    # (未指定時の既定値は main() で補う)。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--store", default=argparse.SUPPRESS, help=f"集約 JSON の保存先 (既定 {DEFAULT_STORE})"
    )
    common.add_argument(
        "--doctypes", default=argparse.SUPPRESS, help="文書種別の定義ファイル"
    )
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help="機械可読な JSON で出力"
    )
    common.add_argument(
        "--pretty",
        action="store_true",
        default=argparse.SUPPRESS,
        help="--json を整形して出力 (既定はコンパクト。目視確認用)",
    )
    common.add_argument(
        "--facts", default=argparse.SUPPRESS, help="ファクト集約 JSON の保存先 (既定 store/facts.json)"
    )
    common.add_argument(
        "--item-types-file", default=argparse.SUPPRESS, help="ファクト種別の定義ファイル"
    )
    common.add_argument(
        "--config",
        default=argparse.SUPPRESS,
        help="数値ガード等の設定ファイル (既定 <home>/config.json)",
    )
    common.add_argument(
        "--stdout",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "出力の数値ガード (config.json の ceiling_chars) を無視して全出力する。"
            "export/facts-export では -o 省略時でも標準出力へ全出力する"
        ),
    )
    common.add_argument(
        "--run-id",
        default=argparse.SUPPRESS,
        help=(
            "この実行の相関 ID (既定: 環境変数 DOCEXTRACT_RUN_ID、無ければ自動採番)。"
            "docextract から引き継いで一連の処理を同じ ID で追跡する"
        ),
    )

    p = argparse.ArgumentParser(
        prog="docagent", description="集約 JSON ストアのデータ操作 API", parents=[common]
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, help_):
        return sub.add_parser(name, help=help_, parents=[common])

    add("init", "ストアと doctypes.json / facts.json を初期化").set_defaults(func=cmd_init)

    sp = add("prep", "取り込み準備: 必要なら登録し、種別候補+本文抜粋を1回で返す")
    sp.add_argument("target", help="result.json のパス、または登録済み文書 ID")
    sp.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help="本文抜粋の最大文字数 (既定は config.json の prep_max_chars=8000)",
    )
    sp.set_defaults(func=cmd_prep)

    sp = add("add", "docextract の result.json を取り込み登録")
    sp.add_argument("result", help="result.json のパス")
    sp.add_argument("--overwrite", action="store_true", help="同一 ID を上書き")
    sp.set_defaults(func=cmd_add)

    sp = add("set-doctype", "文書種別を設定 (定義内に正規化)")
    sp.add_argument("id")
    sp.add_argument("doctype")
    sp.add_argument("--force", action="store_true", help="定義外でも許可")
    sp.set_defaults(func=cmd_set_doctype)

    sp = add("get", "1 文書を表示")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_get)

    sp = add("text", "本文テキストのみを出力 (座標等を除いた軽量ビュー)")
    sp.add_argument("id")
    sp.add_argument(
        "--max-chars",
        type=int,
        default=None,
        help=(
            "出力する最大文字数 (既定は config.json の text_max_chars=20000。0 で全文。"
            "巨大文書の全文直流を防ぐ)"
        ),
    )
    sp.add_argument(
        "--offset",
        type=int,
        default=0,
        help="読み出し開始位置 (既定 0)。前回の next_offset を渡して続きをページングする",
    )
    sp.set_defaults(func=cmd_text)

    sp = add("list", "全文書を一覧 (既定はスリム: id/source/doctype/短縮 preview)")
    sp.add_argument(
        "--full",
        action="store_true",
        help="metadata・パス等を含む完全な dict を出力 (既定はスリム射影)",
    )
    sp.set_defaults(func=cmd_list)

    sp = add("query", "条件で絞り込み")
    sp.add_argument("--doctype")
    sp.add_argument("--text", help="ソース名・文書種別・抜粋・メタデータへの部分一致")
    sp.add_argument(
        "--full",
        action="store_true",
        help="metadata・パス等を含む完全な dict を出力 (既定はスリム射影)",
    )
    sp.set_defaults(func=cmd_query)

    add("stats", "文書種別別の集計").set_defaults(func=cmd_stats)

    sp = add("export", "集約 JSON 全体を出力")
    sp.add_argument("-o", "--output", help="書き出し先ファイル (推奨。省略時は要 --stdout)")
    # --stdout は common で定義済み (非対話で -o 省略時の全出力を許可)。
    sp.set_defaults(func=cmd_export)

    sp = add("remove", "文書を削除")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_remove)

    sp = add("doctypes", "文書種別の表示・追加・削除")
    sp.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove"])
    sp.add_argument("name", nargs="?")
    sp.set_defaults(func=cmd_doctypes)

    # ── 現状把握 (doc-indexer) ──
    sp = add("sync", "抽出マニフェストの全文書を一括で索引に登録/更新")
    sp.add_argument("--manifest", help="output/index.json のパス (既定は基点配下)")
    sp.set_defaults(func=cmd_sync)

    # ── 横断検索 (corpus-qa) ──
    sp = add("search", "登録済み文書の本文を横断検索し出典 (doc_id+location) 付きで返す")
    sp.add_argument(
        "term",
        help="検索語。空白区切りで複数指定すると AND (全語を含む要素のみ)。"
        " 全角/半角・大文字小文字・改行や空白の揺れは吸収される",
    )
    sp.add_argument("--doc", help="特定の文書 ID に絞る")
    sp.add_argument(
        "--max-hits",
        type=int,
        default=None,
        help="返す最大ヒット数 (関連度順の上位。既定は config.json の search_max_hits=50)",
    )
    sp.set_defaults(func=cmd_search)

    # ── 仕様の洗い出し (spec-extractor): ファクト ──
    sp = add("fact-add", "抽出した仕様・要件ファクトを1件追加 (出典必須)")
    sp.add_argument("--doc", required=True, help="抽出元の文書 ID")
    sp.add_argument("--type", required=True, help="ファクト種別 (item-types のいずれか)")
    sp.add_argument("--statement", required=True, help="抽出した事実 (機械可読な1文)")
    sp.add_argument("--evidence", help="根拠となる原文抜粋")
    sp.add_argument("--location", help='要素の location を JSON で (例: \'{"page": 3}\')')
    sp.add_argument("--keywords", help="カンマ区切りのキーワード")
    sp.add_argument("--confidence", choices=["high", "medium", "low"], help="確信度")
    sp.add_argument("--force", action="store_true", help="種別定義外でも許可")
    sp.set_defaults(func=cmd_fact_add)

    sp = add("facts", "ファクトを一覧/絞り込み")
    sp.add_argument("--doc", help="文書 ID で絞る")
    sp.add_argument("--type", help="種別で絞る")
    sp.add_argument("--text", help="本文・根拠・キーワードへの部分一致")
    sp.add_argument(
        "--full",
        action="store_true",
        help="evidence (原文) を短縮せず全文出力する (既定は 200 字で短縮)",
    )
    sp.set_defaults(func=cmd_facts)

    sp = add("fact-remove", "ファクトを削除")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_fact_remove)

    add("facts-stats", "ファクトの種別別・文書別の集計").set_defaults(func=cmd_facts_stats)

    sp = add("facts-export", "ファクト集約 JSON 全体を出力")
    sp.add_argument("-o", "--output", help="書き出し先ファイル (推奨。省略時は要 --stdout)")
    # --stdout は common で定義済み (非対話で -o 省略時の全出力を許可)。
    sp.set_defaults(func=cmd_facts_export)

    sp = add("item-types", "ファクト種別の表示・追加・削除")
    sp.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove"])
    sp.add_argument("name", nargs="?")
    sp.set_defaults(func=cmd_item_types)

    return p


def main(argv: list[str] | None = None) -> int:
    # Windows コンソール (cp932) でも日本語・記号を安全に出力するため UTF-8 に固定。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    # 共通オプションは default=SUPPRESS のため、未指定なら属性ごと無い。ここで補う。
    # 既定パスは実行時に解決し、環境変数 DOCEXTRACT_HOME を docextract と一括で
    # 反映させる (import 時に固定しない)。
    args.store = getattr(args, "store", str(_paths.store_path()))
    args.doctypes = getattr(args, "doctypes", str(_paths.doctypes_path()))
    args.facts = getattr(args, "facts", str(_paths.facts_path()))
    args.item_types_file = getattr(args, "item_types_file", str(_paths.item_types_path()))
    args.config = getattr(args, "config", str(_paths.config_path()))
    args.stdout = getattr(args, "stdout", False)
    args.json = getattr(args, "json", False)
    # 数値ガードの設定を読み込み、各ハンドラと出力ガードへ反映する。
    # 優先順位: CLI フラグ > config.json > 組み込み既定 (ハンドラ側で解決)。
    args.cfg = _config.load(args.config)
    global _PRETTY, _CEILING, _FORCE_STDOUT, _LIST_PREVIEW_CHARS, _FACT_EVIDENCE_CHARS
    _PRETTY = getattr(args, "pretty", False)
    _CEILING = args.cfg["ceiling_chars"]
    _FORCE_STDOUT = args.stdout
    _LIST_PREVIEW_CHARS = args.cfg["list_preview_chars"]
    _FACT_EVIDENCE_CHARS = args.cfg["fact_evidence_chars"]
    # docextract から引き継いだ相関 ID (環境変数 or --run-id) で監査ログを残す。
    # これで docextract→docagent の一連の処理を同じ run_id で再構成できる。
    log = _obs.open_run("docagent.cli", getattr(args, "run_id", None))
    log.event("command.start", command=args.command)
    try:
        args.func(args)
    except DocAgentError as e:
        log.error("command.error", command=args.command, error=str(e))
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    log.event("command.done", command=args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
