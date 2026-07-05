"""要約の保存と対象選択 — summaries.json + summaries/<doc_id>.md を管理する。

正本の考え方:

- 要約本文は ``<home>/summaries/<doc_id>.md`` (人が読む Markdown)
- メタデータ (どの文書を・どの内容ハッシュで・どのモデルが要約したか) は
  ``<home>/store/summaries.json``

「未要約 (pending)」の判定はメタデータで行う:

- library.json に登録済みだが summaries.json に記録が無い → 未要約
- 記録はあるが文書の content_hash か「要約仕様」のハッシュが変わった → 陳腐化 (stale)

要約の出力構造はツールが固定する (パース済み文書情報に付加する固定フォーマット)
ため、利用者が定義するのは**出力フォーマットではなく**次の 2 つ:

- **要約の観点** ``templates/summary_guide.md`` — どの観点で内容を拾うか
- **カテゴリー** ``templates/summary_categories.json`` — 要約をどの既定カテゴリーへ
  分類するか (doctypes と同じ統制語彙。LLM が付与し :func:`_resolve_term` で正規化)

いずれもパッケージ同梱の既定があり、``<home>/store/summary_guide.md`` /
``<home>/store/summary_categories.json`` を置けばプロジェクト側で上書きできる
(doctypes.json と同じ「同梱既定 + 利用者編集ファイル優先」の方式)。観点・カテゴリー
のどちらを変えても要約は作り直すべきなので、両者を束ねた「要約仕様ハッシュ」
(:func:`spec_hash`) で鮮度を判定する。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docextract import paths as _paths

SCHEMA_VERSION = 1

_TEMPLATES = Path(__file__).resolve().parent / "templates"
PACKAGED_GUIDE = _TEMPLATES / "summary_guide.md"
PACKAGED_CATEGORIES = _TEMPLATES / "summary_categories.json"

# カテゴリー未付与 / 語彙外を表す表示名 (タクソノミー外の一時値)。
UNCATEGORIZED = "未分類"


class DocSummaryError(Exception):
    """docsummary 由来のユーザー向けエラー。"""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def summaries_store_path() -> Path:
    """要約メタデータの既定保存先 (``<home>/store/summaries.json``)。"""
    return _paths.store_dir() / "summaries.json"


def summaries_dir() -> Path:
    """要約 Markdown の既定出力先 (``<home>/summaries``)。"""
    return _paths.home_dir() / "summaries"


def guide_override_path() -> Path:
    """利用者が編集できる観点ガイドの上書きファイル (``<home>/store/summary_guide.md``)。"""
    return _paths.store_dir() / "summary_guide.md"


def categories_override_path() -> Path:
    """利用者が編集できるカテゴリー定義 (``<home>/store/summary_categories.json``)。"""
    return _paths.store_dir() / "summary_categories.json"


def resolve_guide(override: str | Path | None = None) -> tuple[Path, str]:
    """有効な要約観点ガイドの (パス, 本文) を返す。

    優先順位: 明示指定 (--guide-file) > ``<home>/store/summary_guide.md``
    (利用者の上書き) > パッケージ同梱の既定。
    """
    candidates = [Path(override)] if override else [guide_override_path(), PACKAGED_GUIDE]
    for cand in candidates:
        if cand.is_file():
            return cand, cand.read_text(encoding="utf-8-sig")
    raise DocSummaryError(
        f"要約の観点ガイドが見つかりません: {candidates[0]}。"
        f" 同梱の既定 ({PACKAGED_GUIDE}) が失われていないか確認してください"
    )


def _read_categories_file(path: Path) -> list[str] | None:
    """categories 定義を読み、カテゴリー一覧を返す。無効・不存在なら None。

    ``{"categories": [...]}`` でも素の配列でも受け付ける (doctypes.json と同じ)。
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    cats = data.get("categories") if isinstance(data, dict) else data
    return [str(c) for c in cats] if cats else None


def resolve_categories(override: str | Path | None = None) -> tuple[Path, list[str]]:
    """有効なカテゴリー定義の (パス, 一覧) を返す。

    優先順位: 明示指定 (--categories-file) > ``<home>/store/summary_categories.json``
    (利用者の上書き) > パッケージ同梱の既定。
    """
    candidates = ([Path(override)] if override
                  else [categories_override_path(), PACKAGED_CATEGORIES])
    for cand in candidates:
        cats = _read_categories_file(cand)
        if cats is not None:
            return cand, cats
    raise DocSummaryError(
        f"カテゴリー定義が見つかりません: {candidates[0]}。"
        f" 同梱の既定 ({PACKAGED_CATEGORIES}) が失われていないか確認してください"
    )


def spec_hash(guide_text: str, categories: list[str]) -> str:
    """観点ガイドとカテゴリーを束ねた「要約仕様ハッシュ」。

    どちらを変えても要約は作り直すべきなので、両者から 1 つのハッシュを作り、
    鮮度判定 (:meth:`SummaryStore.status_of`) に使う。
    """
    payload = guide_text + "\n\x00categories\x00\n" + "\n".join(categories)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class SummaryStore:
    """summaries.json の読み書き。"""

    path: Path
    version: int = SCHEMA_VERSION
    summaries: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "SummaryStore":
        p = Path(path) if path else summaries_store_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            return cls(path=p, version=data.get("version", SCHEMA_VERSION),
                       summaries=data.get("summaries", []))
        return cls(path=p)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": self.version, "summaries": self.summaries}
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def find(self, doc_id: str) -> dict[str, Any] | None:
        for s in self.summaries:
            if s["doc_id"] == doc_id:
                return s
        return None

    def upsert(self, entry: dict[str, Any]) -> dict[str, Any]:
        existing = self.find(entry["doc_id"])
        if existing:
            entry["created_at"] = existing.get("created_at", entry["updated_at"])
            self.summaries[self.summaries.index(existing)] = entry
        else:
            entry["created_at"] = entry["updated_at"]
            self.summaries.append(entry)
        return entry

    def remove(self, doc_id: str) -> dict[str, Any] | None:
        existing = self.find(doc_id)
        if existing:
            self.summaries.remove(existing)
        return existing

    # ── 状態判定 ──────────────────────────────────────────────
    def status_of(self, doc: dict[str, Any], spec: str) -> str:
        """文書 1 件の要約状態: 'none' (未要約) / 'stale' (陳腐化) / 'fresh'。

        ``spec`` は :func:`spec_hash` の値 (観点ガイド + カテゴリー)。文書の内容
        (content_hash) か要約仕様のどちらかが変われば陳腐化とみなす。
        """
        entry = self.find(doc["id"])
        if entry is None:
            return "none"
        if doc.get("content_hash") and entry.get("content_hash") != doc.get("content_hash"):
            return "stale"
        if entry.get("spec_hash") != spec:
            return "stale"
        return "fresh"


def _is_under(child: str | None, folder: Path) -> bool:
    """source_abspath が folder 配下か (大文字小文字は OS 差を吸収して比較)。"""
    if not child:
        return False
    try:
        c = Path(child).resolve()
        f = folder.resolve()
    except OSError:
        return False
    try:
        c.relative_to(f)
        return True
    except ValueError:
        # Windows のドライブ文字・大文字小文字揺れを casefold で再試行する。
        return str(c).casefold().startswith(str(f).casefold().rstrip("\\/") + "\\") or \
            str(c).casefold().startswith(str(f).casefold().rstrip("\\/") + "/")


def select_targets(documents: list[dict[str, Any]], store: SummaryStore,
                   spec: str, ids: list[str] | None = None,
                   folder: str | Path | None = None,
                   pending: bool = False, all_docs: bool = False,
                   force: bool = False) -> list[dict[str, Any]]:
    """要約対象の文書を選ぶ。返り値は library.json の文書 dict のリスト。

    - ids:     文書 ID の明示指定 (未登録 ID はエラー)
    - folder:  元ファイル (source_abspath) がフォルダ配下にある登録文書
    - pending: 未要約 + 陳腐化した文書
    - all_docs: 全登録文書

    ids / folder / all_docs で選んだ場合、``force=False`` なら fresh な文書は
    スキップする (再要約したいときは --force)。pending は定義上 fresh を含まない。
    """
    selected: list[dict[str, Any]] = []
    if ids:
        by_id = {d["id"]: d for d in documents}
        for doc_id in ids:
            if doc_id not in by_id:
                raise DocSummaryError(
                    f"ID '{doc_id}' の文書は登録されていません。"
                    " 登録済み文書の一覧: docsummary list"
                )
            selected.append(by_id[doc_id])
    elif folder:
        f = Path(folder)
        if not f.is_dir():
            raise DocSummaryError(f"--dir に指定されたフォルダがありません: {f}")
        selected = [d for d in documents if _is_under(d.get("source_abspath"), f)]
        if not selected:
            raise DocSummaryError(
                f"フォルダ {f} 配下の元ファイルを持つ登録文書がありません。"
                " 先に抽出・索引化してください:"
                f" docextract extract --dir {f} -r → docextract docagent sync"
            )
    elif pending or all_docs:
        selected = list(documents)
    else:
        raise DocSummaryError(
            "対象が指定されていません。文書 ID を渡すか、"
            " --dir <フォルダ> / --pending (未要約) / --all を指定してください"
        )

    if pending or not force:
        # 未要約・陳腐化のみ残す (--force で fresh も再要約。pending は定義上
        # force に関係なく fresh を含まない)。
        selected = [d for d in selected
                    if store.status_of(d, spec) in ("none", "stale")]
    return selected
