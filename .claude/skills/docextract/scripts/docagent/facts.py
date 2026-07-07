"""抽出ファクト (仕様・要件項目) の集約 JSON ストア。

docextract の抽出結果から、システム開発の後工程 (現状把握・設計・仕様の洗い出し)
で機械的に使える**構造化された事実**を項目単位で蓄える。各項目は必ず出典
(``doc_id`` + ``location`` + ``evidence``) を持ち、「この要件はどの資料の
どこから来たか」を後工程で辿れるようにする。

各ファクトは他ファクトへの**参照 (``refs``)** を持てる。参照は「このアイテムが
別のアイテムを実現/詳細化/制約する」といった工程間トレースを、原文の散文ではなく
機械可読な形で残すためのもの。抽出時点では specdb の item ID がまだ無いため、参照先は
資料上の**自然キー** (``F-02`` / ``SCR-03`` / 物理名など) で指す (``to_ref``)。
参照の起点は常にそのファクト自身 (from 側) で、``rel`` は関係種別 (specdb の関係型
``realizes`` / ``refines`` 等)。後工程 (spec-designer) は ``to_ref`` を specdb の
アイテムへ決定的に解決して関係を起こす。

集約ストア (docagent の ``library.json``) が「文書ごとの分類・要約」を持つのに
対し、こちらは「文書を横断した仕様・要件の項目」を持つ。両者は別ファイル
(``facts.json`` / ``library.json``) だが、同じ ``run_docagent.py`` から操作する。

- 操作:  add / remove
- 参照:  get / query / stats / export
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from docextract import paths as _paths

from .store import DocAgentError, _resolve_term

SCHEMA_VERSION = 1

DEFAULT_STORE = _paths.facts_path()
DEFAULT_ITEM_TYPES = _paths.item_types_path()
DEFAULT_REL_TYPES = _paths.rel_types_path()

# 既定の種別定義 (パッケージ同梱)。コードにハードコードしない。実行時は
# store/item_types.json (利用者が各自編集できる) が存在すればそちらを優先。
PACKAGED_ITEM_TYPES = Path(__file__).resolve().parent / "item_types.json"
# 参照 (refs) の関係種別も同様に同梱デフォルト + 利用者編集 (store/rel_types.json)。
PACKAGED_REL_TYPES = Path(__file__).resolve().parent / "rel_types.json"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_terms_file(path: Path, key: str) -> list[str] | None:
    """統制語彙ファイル (item_types.json / rel_types.json) を読む共通処理。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    terms = data.get(key) if isinstance(data, dict) else data
    return list(terms) if terms else None


def default_item_types() -> list[str]:
    """パッケージ同梱の item_types.json から既定の種別一覧を読む。"""
    its = _read_terms_file(PACKAGED_ITEM_TYPES, "item_types")
    if its is None:
        raise DocAgentError(
            f"既定の種別定義が読めません: {PACKAGED_ITEM_TYPES}。"
            " docagent パッケージに item_types.json が同梱されているか確認してください"
        )
    return its


def default_rel_types() -> list[str]:
    """パッケージ同梱の rel_types.json から既定の関係種別一覧を読む。"""
    rts = _read_terms_file(PACKAGED_REL_TYPES, "rel_types")
    if rts is None:
        raise DocAgentError(
            f"既定の関係種別定義が読めません: {PACKAGED_REL_TYPES}。"
            " docagent パッケージに rel_types.json が同梱されているか確認してください"
        )
    return rts


def _load_item_types(path: Path) -> list[str]:
    its = _read_terms_file(path, "item_types")
    return its if its is not None else default_item_types()


def _load_rel_types(path: Path) -> list[str]:
    rts = _read_terms_file(path, "rel_types")
    return rts if rts is not None else default_rel_types()


@dataclass
class FactStore:
    """抽出ファクトの集約 JSON ストア本体。"""

    path: Path
    item_types_path: Path | None = None
    rel_types_path: Path | None = None
    version: int = SCHEMA_VERSION
    item_types: list[str] = field(default_factory=default_item_types)
    rel_types: list[str] = field(default_factory=default_rel_types)
    items: list[dict[str, Any]] = field(default_factory=list)

    # ── 入出力 ────────────────────────────────────────────────
    @classmethod
    def load(
        cls,
        path: str | Path,
        item_types_path: str | Path | None = None,
        rel_types_path: str | Path | None = None,
    ) -> "FactStore":
        path = Path(path)
        it_path = (
            Path(item_types_path)
            if item_types_path
            else path.parent / "item_types.json"
        )
        rt_path = (
            Path(rel_types_path)
            if rel_types_path
            else path.parent / "rel_types.json"
        )
        item_types = _load_item_types(it_path)
        rel_types = _load_rel_types(rt_path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            store = cls(
                path=path,
                item_types_path=it_path,
                rel_types_path=rt_path,
                version=data.get("version", SCHEMA_VERSION),
                item_types=data.get("item_types") or item_types,
                rel_types=data.get("rel_types") or rel_types,
                items=data.get("items", []),
            )
            if it_path.exists():
                store.item_types = item_types
            if rt_path.exists():
                store.rel_types = rel_types
            return store
        return cls(
            path=path,
            item_types_path=it_path,
            rel_types_path=rt_path,
            item_types=item_types,
            rel_types=rel_types,
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "item_types": self.item_types,
            "rel_types": self.rel_types,
            "items": self.items,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ── 参照 ──────────────────────────────────────────────────
    def get(self, fact_id: str) -> dict[str, Any]:
        for it in self.items:
            if it["id"] == fact_id:
                return it
        raise DocAgentError(
            f"ファクト ID '{fact_id}' は存在しません。"
            f" 一覧: python -m docagent facts"
        )

    def query(
        self,
        doc_id: str | None = None,
        type: str | None = None,
        text: str | None = None,
    ) -> list[dict[str, Any]]:
        results = self.items
        if doc_id:
            results = [it for it in results if it.get("doc_id") == doc_id]
        if type:
            # 種別も表記揺れを吸収して絞り込む (未知なら DocAgentError)。
            resolved = _resolve_term(type, self.item_types, label="種別")
            results = [it for it in results if it.get("type") == resolved]
        if text:
            t = text.lower()
            results = [it for it in results if t in _searchable(it)]
        return list(results)

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        by_doc: dict[str, int] = {}
        for it in self.items:
            by_type[it.get("type", "?")] = by_type.get(it.get("type", "?"), 0) + 1
            by_doc[it.get("doc_id", "?")] = by_doc.get(it.get("doc_id", "?"), 0) + 1
        return {"total": len(self.items), "by_type": by_type, "by_doc": by_doc}

    def export(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "item_types": self.item_types,
            "rel_types": self.rel_types,
            "items": self.items,
        }

    # ── 操作 ──────────────────────────────────────────────────
    def _next_id(self) -> str:
        nums = [
            int(it["id"][1:])
            for it in self.items
            if isinstance(it.get("id"), str) and it["id"][1:].isdigit()
        ]
        return f"f{(max(nums) + 1) if nums else 1:04d}"

    def _normalize_refs(
        self, refs: Iterable[dict[str, Any]] | None, force: bool = False
    ) -> list[dict[str, Any]]:
        """参照 (refs) を検証・正規化する。

        各参照は ``rel`` (関係種別) と ``to_ref`` (参照先の自然キー) が必須。任意で
        ``note`` を持てる。``rel`` は関係種別タクソノミーへ正規化する (未知なら拒否、
        ``force`` 時は正規化のみ)。起点は常にこのファクト自身のため from は持たない。
        """
        out: list[dict[str, Any]] = []
        for raw in refs or []:
            if not isinstance(raw, dict):
                raise DocAgentError(
                    f"ref は rel/to_ref を持つオブジェクトで指定してください: {raw!r}"
                )
            rel = (raw.get("rel") or "").strip()
            to_ref = (raw.get("to_ref") or "").strip()
            if not rel or not to_ref:
                raise DocAgentError(
                    "ref には rel (関係種別) と to_ref (参照先の自然キー) が必須です。"
                    f" 指定: {raw!r}"
                )
            resolved = _resolve_term(rel, self.rel_types, force=force, label="関係種別")
            ref: dict[str, Any] = {"rel": resolved, "to_ref": to_ref}
            note = (raw.get("note") or "").strip()
            if note:
                ref["note"] = note
            out.append(ref)
        return out

    def add(
        self,
        doc_id: str,
        type: str,
        statement: str,
        evidence: str | None = None,
        location: dict[str, Any] | None = None,
        refs: Iterable[dict[str, Any]] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """ファクトを1件追加する。``type`` はタクソノミーへ正規化して許可。

        keywords / confidence は持たない — 後工程が実際に使わない付帯情報で、
        LLM に出力させるだけコンテキストと出力トークンの無駄になるため廃止した
        (名寄せは statement の正規化包含 + bigram Jaccard で足りる)。
        """
        if not doc_id:
            raise DocAgentError("doc_id は必須です (どの文書から抽出したか)。")
        if not (statement or "").strip():
            raise DocAgentError("statement は必須です (抽出した事実の本文)。")
        resolved = _resolve_term(type, self.item_types, force=force, label="種別")
        item = {
            "id": self._next_id(),
            "doc_id": doc_id,
            "type": resolved,
            "statement": statement.strip(),
            "evidence": (evidence or "").strip() or None,
            "location": location or {},
            "refs": self._normalize_refs(refs, force=force),
            "added_at": _now(),
        }
        self.items.append(item)
        return item

    def remove(self, fact_id: str) -> dict[str, Any]:
        item = self.get(fact_id)
        self.items.remove(item)
        return item

    def merge(self, shard_paths: Iterable[str | Path]) -> dict[str, Any]:
        """並列抽出したシャード facts.json 群を、この主ストアへ束ねる。

        ``fact-add`` は 1 ストアの read-modify-write のため並列で競合する。並列時は
        各 spec-extractor に自分専用のシャード (``--facts …/facts.<doc>.json``) を書かせ、
        完了後にこのメソッドで統合する（順序非依存・データ競合ゼロ）。

        - **ID は取り込み側で振り直す**（シャード間の連番衝突を避ける）。
        - 出典・``refs`` 等はそのまま保持する。
        - 種別語彙 (``item_types`` / ``rel_types``) はシャード側の追加も失わないよう和集合。
        - 同一 (``doc_id``, ``type``, ``statement``) のファクトはスキップ（シャードの
          二重取り込みに対して冪等。意味的な重複統合は spec-reconcile の役割）。
        """
        seen = {(it.get("doc_id"), it.get("type"), it.get("statement")) for it in self.items}
        added = 0
        skipped = 0
        it_before = len(self.item_types)
        rel_before = len(self.rel_types)
        for sp in shard_paths:
            sp = Path(sp)
            if not sp.exists():
                raise DocAgentError(f"統合するシャードが見つかりません: {sp}")
            other = FactStore.load(sp)
            for t in other.item_types:
                self.add_item_type(t)
            for r in other.rel_types:
                self.add_rel_type(r)
            for it in other.items:
                key = (it.get("doc_id"), it.get("type"), it.get("statement"))
                if key in seen:
                    skipped += 1
                    continue
                seen.add(key)
                new = dict(it)
                new["id"] = self._next_id()
                self.items.append(new)
                added += 1
        return {
            "added": added,
            "skipped": skipped,
            "item_types_added": len(self.item_types) - it_before,
            "rel_types_added": len(self.rel_types) - rel_before,
            "total": len(self.items),
        }

    # ── 種別管理 ──────────────────────────────────────────────
    def add_item_type(self, name: str) -> None:
        if name not in self.item_types:
            self.item_types.append(name)

    def remove_item_type(self, name: str) -> None:
        if name in self.item_types:
            self.item_types.remove(name)

    def save_item_types(self) -> None:
        if self.item_types_path:
            self.item_types_path.parent.mkdir(parents=True, exist_ok=True)
            self.item_types_path.write_text(
                json.dumps({"item_types": self.item_types}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )

    # ── 関係種別管理 ──────────────────────────────────────────
    def add_rel_type(self, name: str) -> None:
        if name not in self.rel_types:
            self.rel_types.append(name)

    def remove_rel_type(self, name: str) -> None:
        if name in self.rel_types:
            self.rel_types.remove(name)

    def save_rel_types(self) -> None:
        if self.rel_types_path:
            self.rel_types_path.parent.mkdir(parents=True, exist_ok=True)
            self.rel_types_path.write_text(
                json.dumps({"rel_types": self.rel_types}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )


def _searchable(item: dict[str, Any]) -> str:
    parts = [
        item.get("statement", ""),
        item.get("evidence") or "",
        " ".join(
            f"{r.get('rel','')} {r.get('to_ref','')}" for r in item.get("refs", [])
        ),
        item.get("doc_id", ""),
    ]
    return " ".join(parts).lower()
