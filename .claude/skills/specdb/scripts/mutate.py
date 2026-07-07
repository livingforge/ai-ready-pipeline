# -*- coding: utf-8 -*-
"""変更操作 CLI — 仕様データ（正本）への追記・状態変更を機械的に行う

    python specdb/mutate.py add-item function --set name=... --set description=... \\
        --set category=仕様管理 --source-doc README.md --source-section 使い方 \\
        --source-evidence "根拠の原文"                # ID・連番 (func_id) は自動採番
    python mutate.py add-item skill --slug my-skill --set name=... …   # id = sk-my-skill
    python mutate.py add-relation realizes --from sk-x --to fn-y
    python mutate.py set-status fn-x review           # 関係は realizes:sk-x->fn-y 形式
    python mutate.py set-attr fn-x description "新しい説明"   # status も review に戻る
    python mutate.py set-source fn-x --source-doc README.md --source-evidence "原文"
    python mutate.py deprecate fn-x                   # 廃止（削除はしない）
    python mutate.py approve fn-x                     # approved にできる唯一の操作
    python mutate.py apply plan.json [--dry-run]      # 操作リストを一括適用
    python mutate.py --root <データディレクトリ> …

手で YAML を編集するときに壊れやすい規約 — ID の接頭辞と採番・連番属性
（func_id 等）・status: review での登録・source（出典）必須 — をツールが強制する。

- ID は metamodel.yaml の id_prefix に従う。--slug 省略時は「接頭辞+数字」の次番
- sequence 宣言（例 function: { attribute: func_id, format: "F-{:02d}" }）があれば自動採番
- add-item / add-relation の status は draft | review のみ。approved は approve でしか付かない
- add-item は source（--source-doc）が必須
- 既存レコードの編集はテキストを部分置換する（コメント・整形を保存する）
- すべての操作は適用後に全体を再検証し、新たな error が生まれたら巻き戻して失敗する
  （適用前から残っている error はそのまま報告される）

apply の plan.json（LLM や上位ツールが判断結果を構造化して渡す想定）:

    {"ops": [
      {"op": "add-item", "type": "function", "slug": "sync-check",
       "attrs": {"name": "同期チェック", "description": "…", "category": "仕様管理"},
       "source": {"doc": "specdb/sync_check.py", "location": {"section": "docstring"},
                  "evidence": "…"}},
      {"op": "add-relation", "type": "realizes", "from": "mod-specdb", "to": "fn-sync-check"},
      {"op": "set-attr", "ref": "mod-specdb", "attr": "path", "value": "specdb/"},
      {"op": "set-source", "ref": "fn-x", "source": {"doc": "README.md", "evidence": "…"}},
      {"op": "set-status", "ref": "fn-x", "status": "review"},
      {"op": "deprecate", "ref": "fn-old"},
      {"op": "approve", "ref": "realizes:sk-x->fn-y"}
    ]}

エンジン・diff 同様、特定のアイテム種別の知識は持たない。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

from engine import Store, parse_root

FOLD_THRESHOLD = 60   # これより長い文字列属性は >- の折りたたみで書く


class MutateError(Exception):
    pass


# ---------- YAML テキスト整形（追記用） ----------

def _scalar(v) -> str:
    """1 スカラー値を YAML 表記に（必要なときだけ引用する）。"""
    out = yaml.safe_dump(v, allow_unicode=True, default_flow_style=True,
                         width=10 ** 6).strip()
    return out[:-4].strip() if out.endswith("\n...") else out


def _flow(v) -> str:
    """dict / list を 1 行のフロースタイルに。"""
    return yaml.safe_dump(v, allow_unicode=True, default_flow_style=True,
                          width=10 ** 6, sort_keys=False).strip()


def _attr_lines(key: str, v, indent: int) -> list[str]:
    pad = " " * indent
    if isinstance(v, str) and (len(v) > FOLD_THRESHOLD or "\n" in v):
        return [f"{pad}{key}: >-", f"{pad}  {' '.join(v.split())}"]
    if isinstance(v, (dict, list)):
        return [f"{pad}{key}: {_flow(v)}"]
    return [f"{pad}{key}: {_scalar(v)}"]


def _source_keys(e: dict) -> list[str]:
    head = [k for k in ("doc", "location", "evidence") if k in e]
    return head + [k for k in e if k not in ("doc", "location", "evidence")]


def _source_lines(source, indent: int) -> list[str]:
    pad = " " * indent
    entries = source if isinstance(source, list) else [source]
    lines = [f"{pad}source:"]
    if len(entries) == 1:
        for k in _source_keys(entries[0]):
            lines += _attr_lines(k, entries[0][k], indent + 2)
        return lines
    for e in entries:
        body = []
        for k in _source_keys(e):
            body += _attr_lines(k, e[k], indent + 4)
        body[0] = f"{pad}  - " + body[0].lstrip()
        lines += body
    return lines


# ---------- レコードのテキスト位置特定（部分置換のため） ----------

def _blocks(text: str) -> list[tuple[int, int]]:
    """トップレベルのリスト要素（列 0 の '- ' で始まる塊）の (開始, 終了) 文字位置。"""
    starts = [m.start() for m in re.finditer(r"(?m)^- ", text)]
    return [(s, starts[i + 1] if i + 1 < len(starts) else len(text))
            for i, s in enumerate(starts)]


def _find_record(path: Path, pred) -> tuple[str, int, int, bool] | None:
    """pred(record) が真のレコードのテキスト範囲を返す (全文, 開始, 終了, リスト形式か)。"""
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        return None
    if isinstance(data, dict):                      # 1 アイテム = 1 ファイル形式
        return (text, 0, len(text), False) if pred(data) else None
    spans = _blocks(text)
    for rec, (s, e) in zip(data, spans):
        if isinstance(rec, dict) and pred(rec):
            return text, s, e, True
    return None


def _replace_status(block: str, new: str, is_list: bool) -> str:
    """レコードブロック内のトップレベル status を書き換える（無ければ挿入する）。"""
    if block.lstrip().startswith("- {"):            # 1 行フロースタイルの関係レコード
        if re.search(r"\bstatus:\s*[^,}\s]+", block):
            return re.sub(r"(\bstatus:\s*)[^,}\s]+", r"\g<1>" + new, block, count=1)
        return re.sub(r"\s*}", f", status: {new} }}", block, count=1)
    pad = "  " if is_list else ""
    pattern = re.compile(rf"(?m)^({re.escape(pad)}status:\s*)\S+")
    if pattern.search(block):
        return pattern.sub(rf"\g<1>{new}", block, count=1)
    # status 行が無い（draft 既定）→ 先頭行の直後に挿入する
    head, sep, rest = block.partition("\n")
    return f"{head}{sep}{pad}status: {new}\n{rest}"


def _replace_attr(block: str, key: str, value, is_list: bool) -> str:
    """レコードブロック内のトップレベル属性 key を新しい値に置き換える。
    ブロックスカラー（>- 等）の続き行も含めて置換する。無ければ status の前に挿入。"""
    pad = "  " if is_list else ""
    new_lines = "\n".join(_attr_lines(key, value, len(pad)))
    pattern = re.compile(
        rf"(?m)^{re.escape(pad)}{re.escape(key)}:[^\n]*(\n{re.escape(pad)}[ ]+[^\n]*)*")
    if pattern.search(block):
        return pattern.sub(lambda _: new_lines, block, count=1)
    status_line = re.compile(rf"(?m)^{re.escape(pad)}status:")
    m = status_line.search(block)
    if m:
        return block[:m.start()] + new_lines + "\n" + block[m.start():]
    return block.rstrip("\n") + "\n" + new_lines + "\n"


# ---------- エディタ本体 ----------

class Editor:
    """データルートに対する一連の変更。適用後に再検証し、新たな error があれば巻き戻す。"""

    def __init__(self, data_root: Path):
        self.root = data_root
        self.snapshots: dict[Path, str] = {}
        self.log: list[str] = []
        self.baseline = {str(p) for p in Store.load(data_root).problems
                         if p.level == "error"}

    # -- ファイル入出力（巻き戻し可能） --

    def _write(self, path: Path, text: str) -> None:
        if path not in self.snapshots:
            self.snapshots[path] = (path.read_text(encoding="utf-8")
                                    if path.exists() else None)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def rollback(self) -> None:
        for path, old in self.snapshots.items():
            if old is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(old, encoding="utf-8", newline="\n")

    def _store(self) -> Store:
        return Store.load(self.root)

    def _mm_type(self, t: str) -> dict:
        store = self._store()
        if t not in store.mm.item_types:
            raise MutateError(f"未知のアイテム種別 '{t}'")
        return store.mm.item_types[t]

    # -- 採番 --

    def _next_id(self, t: str, slug: str | None, explicit: str | None) -> str:
        store = self._store()
        prefix = store.mm.item_types[t].get("id_prefix")
        if explicit:
            if prefix and not explicit.startswith(prefix):
                raise MutateError(f"id '{explicit}' は接頭辞 '{prefix}' で始めること")
            iid = explicit
        elif slug:
            if not prefix:
                raise MutateError(f"種別 '{t}' に id_prefix が無い。--id で明示すること")
            iid = prefix + slug
        else:
            if not prefix:
                raise MutateError(f"種別 '{t}' に id_prefix が無い。--id で明示すること")
            digits = [m.group(1) for i in store.items_of(t)
                      if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", i.id))]
            if not digits:
                raise MutateError(
                    f"種別 '{t}' の既存 ID は連番形式でない。--slug で名前を付けること")
            width = max(len(d) for d in digits)   # ゼロ埋めの桁幅は既存 ID に合わせる
            iid = f"{prefix}{max(int(d) for d in digits) + 1:0{width}d}"
        if iid in store.items:
            raise MutateError(f"ID '{iid}' は既に存在する")
        return iid

    def _next_sequence(self, t: str, attrs: dict) -> None:
        """metamodel の sequence 宣言に従い連番属性を自動で埋める（指定済みなら何もしない）。"""
        store = self._store()
        seq = store.mm.item_types[t].get("sequence")
        if not seq or seq["attribute"] in attrs:
            return
        m = re.fullmatch(r"(.*)\{:0?(\d*)d\}(.*)", seq["format"])
        if not m:
            raise MutateError(f"sequence format '{seq['format']}' を解釈できない")
        pre, width, post = m.group(1), int(m.group(2) or 1), m.group(3)
        nums = [int(g.group(1)) for i in store.items_of(t)
                if (g := re.fullmatch(re.escape(pre) + r"(\d+)"
                                      + re.escape(post), str(i.attrs.get(seq["attribute"], ""))))]
        n = max(nums, default=0) + 1
        attrs[seq["attribute"]] = f"{pre}{n:0{width}d}{post}"

    # -- 操作 --

    def add_item(self, t: str, attrs: dict, source, status: str = "review",
                 slug: str | None = None, explicit_id: str | None = None,
                 file: str = "core.yaml") -> str:
        tdef = self._mm_type(t)
        if status not in ("draft", "review"):
            raise MutateError("add-item の status は draft | review のみ（approve は別操作）")
        if not source:
            raise MutateError("source（出典）は必須。--source-doc で指定する")
        iid = self._next_id(t, slug, explicit_id)
        attrs = dict(attrs)
        self._next_sequence(t, attrs)

        declared = list(tdef.get("attributes") or {})
        ordered = ([k for k in declared if k in attrs]
                   + [k for k in attrs if k not in declared])
        lines = [f"- id: {iid}"]
        for k in ordered:
            lines += _attr_lines(k, attrs[k], 2)
        lines.append(f"  status: {status}")
        lines += _source_lines(source, 2)

        path = self.root / "items" / t / file
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if old and yaml.safe_load(old) is not None and not isinstance(yaml.safe_load(old), list):
            raise MutateError(f"{path.name} はリスト形式でないため追記できない。--file で別名を指定する")
        text = (old.rstrip("\n") + "\n\n" if old.strip() else "") + "\n".join(lines) + "\n"
        self._write(path, text)
        self.log.append(f"add-item {iid} ({t}) -> {path.relative_to(self.root)}")
        return iid

    def _relation_file(self, rtype: str) -> Path:
        """関係レコードの追記先。その種別のレコードが最も多い既存ファイル、無ければ <種別>.yaml。"""
        rel_dir = self.root / "relations"
        best, best_n = None, 0
        for f in sorted(rel_dir.glob("*.yaml")):
            recs = yaml.safe_load(f.read_text(encoding="utf-8")) or []
            n = sum(1 for r in recs if isinstance(r, dict) and r.get("type") == rtype)
            if n > best_n:
                best, best_n = f, n
        return best or rel_dir / f"{rtype}.yaml"

    def add_relation(self, rtype: str, src: str, dst: str, attrs: dict | None = None,
                     source=None, status: str = "review",
                     file: str | None = None) -> None:
        store = self._store()
        if rtype not in store.mm.relation_types:
            raise MutateError(f"未知の関係種別 '{rtype}'")
        if status not in ("draft", "review"):
            raise MutateError("add-relation の status は draft | review のみ")
        for side, iid in (("from", src), ("to", dst)):
            item = store.items.get(iid)
            if item is None:
                raise MutateError(f"{side} のアイテム '{iid}' が存在しない")
            if item.type not in store.mm.endpoint_types(rtype, side):
                raise MutateError(
                    f"{side} の種別 '{item.type}' は関係 '{rtype}' に使えない")
        if any(r.type == rtype and r.src == src and r.dst == dst
               for r in store.relations):
            raise MutateError(f"関係 {rtype}:{src}->{dst} は既に存在する")

        path = (self.root / "relations" / file) if file else self._relation_file(rtype)
        parts = [f"type: {rtype}", f"from: {src}", f"to: {dst}"]
        parts += [f"{k}: {_scalar(v)}" for k, v in (attrs or {}).items()]
        parts.append(f"status: {status}")
        if source:
            lines = [f"- {p}" if i == 0 else f"  {p}"
                     for i, p in enumerate(parts)] + _source_lines(source, 2)
            record = "\n".join(lines)
        else:
            record = "- { " + ", ".join(parts) + " }"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        self._write(path, (old.rstrip("\n") + "\n" if old.strip() else "")
                    + record + "\n")
        self.log.append(f"add-relation {rtype}:{src}->{dst} -> {path.relative_to(self.root)}")

    def _locate_item(self, iid: str) -> tuple[Path, str, int, int, bool]:
        for f in sorted((self.root / "items").rglob("*.yaml")):
            found = _find_record(f, lambda r: r.get("id") == iid)
            if found:
                return (f, *found)
        raise MutateError(f"アイテム '{iid}' が items/ に見つからない")

    def _locate_relation(self, rtype: str, src: str, dst: str
                         ) -> tuple[Path, str, int, int, bool]:
        for f in sorted((self.root / "relations").rglob("*.yaml")):
            found = _find_record(f, lambda r: (r.get("type") == rtype
                                               and r.get("from") == src
                                               and r.get("to") == dst))
            if found:
                return (f, *found)
        raise MutateError(
            f"関係 {rtype}:{src}->{dst} が relations/ に見つからない"
            "（アイテム内の埋め込み記述なら、そのアイテムの YAML を直接編集する）")

    def _locate_ref(self, ref: str) -> tuple[Path, str, int, int, bool]:
        """アイテム ID または関係参照 (``rtype:from->to``) からレコードを特定する。

        set-status / approve と同じ参照文法を set-source でも受け付けるための共通口。
        """
        if re.match(r"^[\w-]+:.+->", ref):
            rtype, rest = ref.split(":", 1)
            src, dst = rest.split("->", 1)
            return self._locate_relation(rtype, src.strip(), dst.strip())
        return self._locate_item(ref)

    def set_status(self, ref: str, status: str, _via: str = "set-status") -> None:
        if status not in ("draft", "review", "approved", "deprecated"):
            raise MutateError(f"未知の status '{status}'")
        if status == "approved" and _via != "approve":
            raise MutateError("approved へは approve 操作でのみ上げられる（レビュー後）")
        path, text, s, e, is_list = self._locate_ref(ref)
        new_block = _replace_status(text[s:e], status, is_list)
        self._write(path, text[:s] + new_block + text[e:])
        self.log.append(f"{_via} {ref} -> {status}")

    def set_attr(self, iid: str, attr: str, value, to_review: bool = True) -> None:
        path, text, s, e, is_list = self._locate_item(iid)
        block = _replace_attr(text[s:e], attr, value, is_list)
        if to_review:
            block = _replace_status(block, "review", is_list)
        self._write(path, text[:s] + block + text[e:])
        self.log.append(f"set-attr {iid}.{attr}"
                        + (" (status: review)" if to_review else ""))

    def set_source(self, iid: str, source, to_review: bool = True) -> None:
        """出典を差し替える（文書の改稿で evidence が古くなったとき等）。

        ``iid`` は関係参照 (``rtype:from->to``) も受け付ける — 関係の evidence も
        文書の改稿で古くなるため、アイテムと同じ操作で差し替えられるようにする。
        """
        if not source:
            raise MutateError("source（出典）は必須")
        path, text, s, e, is_list = self._locate_ref(iid)
        block = text[s:e]
        pad = "  " if is_list else ""
        if block.lstrip().startswith("- {"):
            # 1 行フロースタイルのレコード（関係でよく使う）に block 形式の source を
            # そのまま差すと YAML が壊れる。先にブロック形式へ展開してから差す。
            head, _, rest = block.partition("\n")
            rec = yaml.safe_load(head)[0]
            lines: list[str] = []
            for k, v in rec.items():
                body = _attr_lines(k, v, len(pad))
                if not lines:
                    body[0] = "- " + body[0].lstrip()
                lines += body
            block = "\n".join(lines) + "\n" + rest
        new_lines = "\n".join(_source_lines(source, len(pad)))
        pattern = re.compile(
            rf"(?m)^{re.escape(pad)}source:[^\n]*(\n{re.escape(pad)}[ ]+[^\n]*)*")
        if pattern.search(block):
            block = pattern.sub(lambda _: new_lines, block, count=1)
        else:
            block = block.rstrip("\n") + "\n" + new_lines + "\n"
        if to_review:
            block = _replace_status(block, "review", is_list)
        self._write(path, text[:s] + block + text[e:])
        self.log.append(f"set-source {iid}"
                        + (" (status: review)" if to_review else ""))

    # -- 検証ゲート --

    def validate(self) -> tuple[list, list]:
        """(新たに生まれた error, 全 problems)。"""
        store = self._store()
        new = [p for p in store.problems
               if p.level == "error" and str(p) not in self.baseline]
        return new, store.problems


# ---------- plan の適用 ----------

def apply_plan(editor: Editor, plan: dict) -> None:
    ops = plan.get("ops")
    if not isinstance(ops, list):
        raise MutateError("plan は {\"ops\": [...]} の形にする")
    for i, op in enumerate(ops):
        kind = op.get("op")
        ref = op.get("ref") or op.get("id")
        try:
            if kind == "add-item":
                editor.add_item(op["type"], op.get("attrs") or {},
                                op.get("source"), op.get("status", "review"),
                                slug=op.get("slug"), explicit_id=op.get("id"),
                                file=op.get("file", "core.yaml"))
            elif kind == "add-relation":
                editor.add_relation(op["type"], op["from"], op["to"],
                                    op.get("attrs"), op.get("source"),
                                    op.get("status", "review"), op.get("file"))
            elif kind == "set-status":
                editor.set_status(ref, op["status"])
            elif kind == "set-attr":
                editor.set_attr(ref, op["attr"], op["value"],
                                to_review=op.get("to_review", True))
            elif kind == "set-source":
                editor.set_source(ref, op["source"],
                                  to_review=op.get("to_review", True))
            elif kind == "deprecate":
                editor.set_status(ref, "deprecated", _via="deprecate")
            elif kind == "approve":
                editor.set_status(ref, "approved", _via="approve")
            else:
                raise MutateError(f"未知の op '{kind}'")
        except KeyError as e:
            raise MutateError(f"ops[{i}] ({kind}): 必須キー {e} が無い") from None
        except MutateError as e:
            raise MutateError(f"ops[{i}] ({kind}): {e}") from None


# ---------- CLI ----------

def _parse_set(pairs: list[str]) -> dict:
    attrs = {}
    for p in pairs:
        if "=" not in p:
            raise MutateError(f"--set は key=value 形式（'{p}'）")
        k, v = p.split("=", 1)
        attrs[k.strip()] = yaml.safe_load(v) if v != "" else ""
    return attrs


def _build_source(args) -> dict | None:
    if not getattr(args, "source_doc", None):
        return None
    src: dict = {"doc": args.source_doc}
    if args.source_section:
        src["location"] = {"section": args.source_section}
    if args.source_evidence:
        src["evidence"] = args.source_evidence
    return src


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data_root, rest = parse_root(sys.argv[1:])

    ap = argparse.ArgumentParser(prog="mutate.py",
                                 description="仕様データへの追記・状態変更を機械的に行う")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add-item", help="アイテムを追加する（ID・連番は自動採番）")
    p.add_argument("type")
    p.add_argument("--slug", help="ID の接頭辞に続ける名前（例: sync-check → fn-sync-check）")
    p.add_argument("--id", dest="explicit_id", help="ID を明示する（接頭辞は強制される）")
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--status", default="review", choices=["draft", "review"])
    p.add_argument("--file", default="core.yaml", help="追記先ファイル名（既定: core.yaml）")
    p.add_argument("--source-doc", required=True)
    p.add_argument("--source-section")
    p.add_argument("--source-evidence")

    p = sub.add_parser("add-relation", help="関係を追加する")
    p.add_argument("type")
    p.add_argument("--from", dest="src", required=True)
    p.add_argument("--to", dest="dst", required=True)
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--status", default="review", choices=["draft", "review"])
    p.add_argument("--file", help="追記先ファイル名（既定: その種別が最も多いファイル）")
    p.add_argument("--source-doc")
    p.add_argument("--source-section")
    p.add_argument("--source-evidence")

    p = sub.add_parser("set-status", help="status を変える（approved は approve でのみ）")
    p.add_argument("ref", help="アイテム ID か 関係（realizes:sk-x->fn-y 形式）")
    p.add_argument("status", choices=["draft", "review", "deprecated"])

    p = sub.add_parser("set-attr", help="属性値を書き換える（status も review に戻る）")
    p.add_argument("ref")
    p.add_argument("attr")
    p.add_argument("value")
    p.add_argument("--keep-status", action="store_true",
                   help="status を review に戻さない（誤記修正など仕様に触れない変更用）")

    p = sub.add_parser("set-source", help="出典を差し替える（status も review に戻る）")
    p.add_argument("ref")
    p.add_argument("--source-doc", required=True)
    p.add_argument("--source-section")
    p.add_argument("--source-evidence")
    p.add_argument("--keep-status", action="store_true")

    p = sub.add_parser("deprecate", help="廃止する（アイテムは削除しない）")
    p.add_argument("ref")

    p = sub.add_parser("approve", help="レビュー済みのアイテム/関係を approved にする")
    p.add_argument("ref")

    p = sub.add_parser("apply", help="plan.json の操作リストを一括適用する")
    p.add_argument("plan")
    p.add_argument("--dry-run", action="store_true",
                   help="適用と検証だけ行い、最後に巻き戻す")

    args = ap.parse_args(rest)
    editor = Editor(data_root)
    dry_run = getattr(args, "dry_run", False)
    try:
        if args.cmd == "add-item":
            editor.add_item(args.type, _parse_set(args.set), _build_source(args),
                            args.status, slug=args.slug,
                            explicit_id=args.explicit_id, file=args.file)
        elif args.cmd == "add-relation":
            editor.add_relation(args.type, args.src, args.dst,
                                _parse_set(args.set), _build_source(args),
                                args.status, args.file)
        elif args.cmd == "set-status":
            editor.set_status(args.ref, args.status)
        elif args.cmd == "set-attr":
            editor.set_attr(args.ref, args.attr, yaml.safe_load(args.value),
                            to_review=not args.keep_status)
        elif args.cmd == "set-source":
            editor.set_source(args.ref, _build_source(args),
                              to_review=not args.keep_status)
        elif args.cmd == "deprecate":
            editor.set_status(args.ref, "deprecated", _via="deprecate")
        elif args.cmd == "approve":
            editor.set_status(args.ref, "approved", _via="approve")
        elif args.cmd == "apply":
            try:
                with open(args.plan, encoding="utf-8") as f:
                    plan = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                raise MutateError(f"plan を読めない: {e}") from None
            apply_plan(editor, plan)
    except MutateError as e:
        editor.rollback()
        print(f"error: {e}", file=sys.stderr)
        return 1

    new_errors, problems = editor.validate()
    for op in editor.log:
        print(f"適用: {op}")
    if new_errors:
        editor.rollback()
        print("この変更で新たな error が生まれるため巻き戻した:", file=sys.stderr)
        for p in new_errors:
            print(f"  {p}", file=sys.stderr)
        return 1
    errs = sum(1 for p in problems if p.level == "error")
    warns = len(problems) - errs
    print(f"検証: error {errs} 件 / warn {warns} 件")
    if dry_run:
        editor.rollback()
        print("--dry-run のため巻き戻した（上記は適用した場合の結果）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
