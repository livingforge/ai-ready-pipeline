"""docagent — 抽出結果を単一の集約 JSON に束ねるデータ操作 API。

docextract が出力する ``result.json`` を取り込み、文書種別 (doctype) を付与して
``store/library.json`` に集約する。仕様・要件は出典付きファクトとして
``store/facts.json`` に蓄える。CLI (``python -m docagent``) と、``Library`` /
``FactStore`` を直接使う Python API の両方を提供する。
"""

from __future__ import annotations

from .facts import FactStore, default_item_types, default_rel_types
from .store import (
    DEFAULT_DOCTYPES,
    DEFAULT_STORE,
    PACKAGED_DOCTYPES,
    DocAgentError,
    Library,
    default_doctypes,
)

__all__ = [
    "Library",
    "FactStore",
    "DocAgentError",
    "default_doctypes",
    "default_item_types",
    "default_rel_types",
    "DEFAULT_STORE",
    "DEFAULT_DOCTYPES",
    "PACKAGED_DOCTYPES",
]
