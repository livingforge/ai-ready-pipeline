"""ブロック作業キュー (context queue) — 低トークン抽出プロトコルの中核。

仕様抽出のサブエージェントが「対象外ファイルの参照」「自作スクリプトでの集計」で
トークンを浪費しないよう、入出力を 2 コマンドに固定するためのデータ構造:

- **context-set** (オーケストレータ): 登録済み文書群を**ブロック**の作業キューへ確定する。
  ブロックはシート (xlsx) / ページ (pdf) / スライド (pptx) を最小単位に、同一文書内で
  ``block_max_chars`` を超えない範囲で結合する。単一ユニットが上限を超える場合は
  文境界 (句読点・改行) を優先して分割する。
- **context-get** (サブエージェント): 担当ブロックの本文と語彙 (item_types / rel_types)
  を 1 コマンドで受け取る。コンテキスト未設定・全ブロック処理済みはエラー。
- **context-send** (サブエージェント): 抽出結果 ``[{type, statement, refs?…}]`` だけを
  返す。出典 (``doc_id`` + ``location``) はブロック定義から **server-side で付与**する
  ため、エージェントは evidence や location をコンテキストへ往復させない。
  結果はブロック専用シャード (``shards/facts.<block_id>.json``) へ書かれ、既存の
  ``facts-merge`` で主ストアへ統合できる (1ブロック=1シャード。書き込み競合なし)。
- **context-check** (オーケストレータ): done でないブロックを列挙する。``facts-merge``
  前のバリア (ID が揃っているかの確認) に使う。

払い出しは**オーケストレータ割り当て型** (``--id`` 明示) を基本とする。キューを
自己サーブで pop する形は「次の未処理」自体が共有可変状態になり、並列時に同じ
ブロックを二重払い出ししうるため、引数なしの pop は**直列・単独実行専用**の糖衣。
状態は 3 値 ``{pending, claimed, done}`` で、claimed のまま残ったブロックは
context-check が未完として報告し、オーケストレータが再割り当てする
(context-send はシャードを作り直すため再実行は冪等)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .facts import CONFIDENCE_LEVELS, FactStore
from .store import DocAgentError, Library, _load_result_json, render_elements

SCHEMA_VERSION = 1

# ブロックの最小単位を与える location キー (優先順)。どれも無い要素は "body"
# (文書全体をひとつのユニットとみなし、文字数でのみ分割する。docx が該当)。
_UNIT_KEYS = ("sheet", "page", "slide")

# 分割時に優先する文境界。無ければハードカットする。
_SENTENCE_BREAKS = "。．！？!?\n"

_STATUSES = ("pending", "claimed", "done")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── ブロック構築 ─────────────────────────────────────────────
def _unit_of(el: dict[str, Any]) -> tuple[str, Any]:
    """要素が属するユニット (kind, value) を location から決める。"""
    loc = el.get("location") or {}
    for k in _UNIT_KEYS:
        if k in loc:
            return k, loc[k]
    return "body", None


def _unit_label(kind: str, value: Any) -> str:
    return "body" if kind == "body" else f"{kind}={value}"


def split_text(text: str, limit: int) -> list[str]:
    """``limit`` を超えるテキストを、文境界を優先して分割する。

    後方から最寄りの文境界 (句読点・改行) を探し、見つからない・早すぎる
    (半分より前) 場合はハードカットする。空文字は空リスト。
    """
    if limit <= 0 or len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind(c) for c in _SENTENCE_BREAKS)
        if cut < limit // 2:
            cut = limit - 1
        parts.append(rest[: cut + 1])
        rest = rest[cut + 1 :]
    if rest:
        parts.append(rest)
    return parts


def _block_location(
    units: list[tuple[str, Any]], part: tuple[int, int] | None
) -> dict[str, Any]:
    """ブロックを構成するユニット群から、ファクトへ付与する location を作る。"""
    loc: dict[str, Any] = {}
    kinds = {k for k, _ in units if k != "body"}
    if len(kinds) == 1:
        kind = kinds.pop()
        values = [v for k, v in units if k == kind]
        if len(values) == 1:
            loc[kind] = values[0]
        else:
            loc[kind + "s"] = values
    elif kinds:
        loc["units"] = [_unit_label(k, v) for k, v in units]
    if part:
        loc["part"] = part[0]
        loc["parts"] = part[1]
    return loc


def build_blocks(
    doc: dict[str, Any], elements: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """1 文書の要素列からブロック列を作る (結合・分割は同一文書内に限る)。"""
    # 出現順を保ってユニットごとに要素を束ねる。
    grouped: dict[tuple[str, Any], list[dict[str, Any]]] = {}
    for el in elements:
        grouped.setdefault(_unit_of(el), []).append(el)
    units = [
        (kind, value, render_elements(els)) for (kind, value), els in grouped.items()
    ]
    units = [(k, v, t) for k, v, t in units if t.strip()]

    blocks: list[dict[str, Any]] = []

    def flush(acc: list[tuple[str, Any, str]]) -> None:
        if not acc:
            return
        keys = [(k, v) for k, v, _ in acc]
        # 複数ユニットを結合したときは見出しで区切り、単独ユニットは本文のみ。
        if len(acc) == 1:
            text = acc[0][2]
        else:
            text = "\n\n".join(f"### {_unit_label(k, v)}\n{t}" for k, v, t in acc)
        blocks.append({"units": keys, "text": text, "part": None})

    acc: list[tuple[str, Any, str]] = []
    acc_len = 0
    for kind, value, text in units:
        if len(text) > limit:
            flush(acc)
            acc, acc_len = [], 0
            parts = split_text(text, limit)
            for i, p in enumerate(parts, 1):
                blocks.append(
                    {"units": [(kind, value)], "text": p, "part": (i, len(parts))}
                )
            continue
        if acc and acc_len + len(text) > limit:
            flush(acc)
            acc, acc_len = [], 0
        acc.append((kind, value, text))
        acc_len += len(text)
    flush(acc)

    out: list[dict[str, Any]] = []
    for n, b in enumerate(blocks, 1):
        out.append(
            {
                "id": f"{doc['id']}.b{n:02d}",
                "doc_id": doc["id"],
                "source": doc.get("source"),
                "units": [_unit_label(k, v) for k, v in b["units"]],
                "location": _block_location(b["units"], b["part"]),
                "chars": len(b["text"]),
                "status": "pending",
                "text": b["text"],
            }
        )
    return out


# ── キュー本体 ───────────────────────────────────────────────
@dataclass
class ContextQueue:
    """ブロック作業キュー (``store/context.json``)。

    本文テキストはキュー構築時のスナップショットとして各ブロックに保持する
    (result.json が走行中に差し替わっても get の内容が揺れない)。
    """

    path: Path
    version: int = SCHEMA_VERSION
    block_max_chars: int = 0
    created_at: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)

    # ── 入出力 ────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | Path) -> "ContextQueue":
        path = Path(path)
        if not path.exists():
            raise DocAgentError(
                f"コンテキストが未設定です ({path} がありません)。"
                " 先にオーケストレータが context-set --files/--folder/--docs で"
                " 対象を確定してください"
            )
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return cls(
            path=path,
            version=data.get("version", SCHEMA_VERSION),
            block_max_chars=data.get("block_max_chars", 0),
            created_at=data.get("created_at", ""),
            blocks=data.get("blocks", []),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "block_max_chars": self.block_max_chars,
            "created_at": self.created_at,
            "blocks": self.blocks,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ── 参照 ──────────────────────────────────────────────────
    def find(self, block_id: str) -> dict[str, Any]:
        for b in self.blocks:
            if b["id"] == block_id:
                return b
        known = ", ".join(b["id"] for b in self.blocks[:10])
        raise DocAgentError(
            f"ブロック ID '{block_id}' はキューにありません。"
            f" 既知の ID (先頭10件): {known}。一覧は context-check --json"
        )

    def shard_path(self, block_id: str) -> Path:
        return self.path.parent / "shards" / f"facts.{block_id}.json"

    def check(self) -> dict[str, Any]:
        """done でないブロックを列挙する (facts-merge 前のバリア)。"""
        by_status = {s: 0 for s in _STATUSES}
        incomplete = []
        shards = []
        for b in self.blocks:
            by_status[b["status"]] = by_status.get(b["status"], 0) + 1
            if b["status"] == "done":
                shards.append(str(self.shard_path(b["id"])).replace("\\", "/"))
            else:
                incomplete.append(
                    {"id": b["id"], "status": b["status"], "units": b["units"]}
                )
        return {
            "total": len(self.blocks),
            "by_status": by_status,
            "complete": not incomplete,
            "incomplete": incomplete,
            "shards": shards,
        }

    # ── 操作 ──────────────────────────────────────────────────
    @classmethod
    def build(
        cls,
        path: str | Path,
        lib: Library,
        docs: list[dict[str, Any]],
        block_max_chars: int,
        force: bool = False,
    ) -> tuple["ContextQueue", list[dict[str, str]]]:
        """文書群からキューを構築して保存する。返り値は (queue, skipped)。

        既存キューに未完 (pending/claimed) が残っている場合は、走行中の作業を
        黙って破棄しないよう ``force`` なしでは拒否する。result.json が失われた
        文書はスキップして理由を返す (部分失敗で全体を止めない)。
        """
        path = Path(path)
        if path.exists() and not force:
            existing = cls.load(path)
            state = existing.check()
            if not state["complete"] and state["total"] > 0:
                raise DocAgentError(
                    f"未完のコンテキストが残っています (未完 {len(state['incomplete'])} /"
                    f" 全 {state['total']} ブロック)。続行するなら context-get/context-send"
                    " で処理を進め、作り直すなら context-set --force を付けてください"
                )
        queue = cls(
            path=path, block_max_chars=block_max_chars, created_at=_now()
        )
        skipped: list[dict[str, str]] = []
        for doc in docs:
            result_path = Path(doc.get("result_path") or "")
            try:
                result = _load_result_json(result_path)
            except DocAgentError as e:
                skipped.append({"id": doc["id"], "reason": str(e)})
                continue
            queue.blocks.extend(
                build_blocks(doc, result.get("elements", []), block_max_chars)
            )
        if not queue.blocks:
            raise DocAgentError(
                "コンテキストにできるブロックが 0 件です (対象文書が無いか、"
                "本文が空です)。対象の指定 (--files/--folder/--docs) を見直してください"
            )
        queue.save()
        return queue, skipped

    def get(self, block_id: str | None = None) -> dict[str, Any]:
        """ブロックを 1 件払い出す (pending → claimed)。

        ``block_id`` 明示 (オーケストレータ割り当て型) が基本。claimed の再取得は
        許す (クラッシュ後の再開を冪等にする)。done は拒否。引数なしは次の pending
        を pop する糖衣で、**直列・単独実行専用** (並列では必ず --id を使う)。
        """
        if block_id is not None:
            block = self.find(block_id)
            if block["status"] == "done":
                raise DocAgentError(
                    f"ブロック '{block_id}' は処理済み (done) です。未完の一覧は"
                    " context-check --json。全体を作り直すなら context-set --force"
                )
        else:
            block = next((b for b in self.blocks if b["status"] == "pending"), None)
            if block is None:
                state = self.check()
                if state["complete"]:
                    raise DocAgentError(
                        "すべてのブロックが処理済みです。次の一手: context-check で"
                        " 確認し、facts-merge でシャードを主ストアへ統合してください"
                    )
                claimed = ", ".join(i["id"] for i in state["incomplete"])
                raise DocAgentError(
                    f"pending のブロックがありません (処理中 claimed: {claimed})。"
                    " 未完了のブロックを引き継ぐなら context-get --id <block_id>"
                )
        block["status"] = "claimed"
        block["claimed_at"] = _now()
        self.save()
        return block

    def send(
        self,
        block_id: str,
        items: list[dict[str, Any]],
        item_types_path: str | Path | None,
        rel_types_path: str | Path | None,
    ) -> dict[str, Any]:
        """抽出結果をブロック専用シャードへ保存し、ブロックを done にする。

        location はブロック定義から server-side で付与する。シャードは毎回
        作り直すため再送は冪等 (二重取り込みにならない)。語彙外の type や
        不正な refs はその項目だけ拒否し、有効分は受理する (全体を止めない)。
        """
        block = self.find(block_id)
        shard = self.shard_path(block_id)
        fs = FactStore.load(shard, item_types_path, rel_types_path)
        fs.items = []  # 再送を冪等にする (シャードはこのブロックの結果の全量)
        added: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for i, raw in enumerate(items):
            if not isinstance(raw, dict):
                rejected.append({"index": i, "reason": f"オブジェクトが必要です: {raw!r}"})
                continue
            try:
                item = fs.add(
                    doc_id=block["doc_id"],
                    type=raw.get("type") or "",
                    statement=raw.get("statement") or "",
                    evidence=None,
                    location=dict(block["location"]),
                    keywords=raw.get("keywords") or [],
                    confidence=_valid_confidence(raw.get("confidence")),
                    refs=raw.get("refs"),
                )
                item["block_id"] = block_id
                added.append(item)
            except DocAgentError as e:
                rejected.append(
                    {
                        "index": i,
                        "statement": (raw.get("statement") or "")[:80],
                        "reason": str(e),
                    }
                )
        fs.save()
        block["status"] = "done"
        block["done_at"] = _now()
        block["facts"] = len(added)
        self.save()
        by_type: dict[str, int] = {}
        for it in added:
            by_type[it["type"]] = by_type.get(it["type"], 0) + 1
        return {
            "id": block_id,
            "shard": str(shard).replace("\\", "/"),
            "added": len(added),
            "by_type": by_type,
            "rejected": rejected,
        }


def _valid_confidence(value: Any) -> str | None:
    """confidence の揺れを吸収する: 不正値は None に落とす (項目ごと拒否はしない)。"""
    return value if value in CONFIDENCE_LEVELS else None


# ── 対象文書の解決 (context-set の入力) ──────────────────────
def resolve_docs(
    lib: Library,
    files: Iterable[str] | None = None,
    folder: str | None = None,
    doc_ids: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """--files / --folder / --docs から登録済み文書を選ぶ (和集合・登録順)。

    ファイルは登録時の ``source_abspath`` (正規化済み絶対パス) か ``source``
    (ファイル名) で照合する。1 件も選べなければエラー (何が合わなかったかを返す)。
    """
    selected: dict[str, dict[str, Any]] = {}
    misses: list[str] = []

    def _match_file(f: str) -> list[dict[str, Any]]:
        target = str(Path(f).resolve()).replace("\\", "/").lower()
        name = Path(f).name.lower()
        hits = []
        for d in lib.documents:
            abspath = (d.get("source_abspath") or "").replace("\\", "/").lower()
            if abspath == target or (d.get("source") or "").lower() == name:
                hits.append(d)
        return hits

    for f in files or []:
        hits = _match_file(f)
        if hits:
            for d in hits:
                selected[d["id"]] = d
        else:
            misses.append(f)
    if folder:
        prefix = str(Path(folder).resolve()).replace("\\", "/").lower().rstrip("/") + "/"
        hits = [
            d
            for d in lib.documents
            if (d.get("source_abspath") or "").replace("\\", "/").lower().startswith(prefix)
        ]
        if hits:
            for d in hits:
                selected[d["id"]] = d
        else:
            misses.append(folder)
    for doc_id in doc_ids or []:
        selected[doc_id] = lib.get(doc_id)  # 未登録なら「次の一手」付きで失敗する

    if not selected:
        detail = f" 一致しなかった指定: {', '.join(misses)}。" if misses else ""
        raise DocAgentError(
            "対象の文書を選べませんでした。" + detail +
            " 登録済みの一覧は docextract docagent list。未登録なら先に"
            " @doc-indexer で抽出・登録してください"
        )
    return list(selected.values())
