"""出力先ディレクトリの解決を一元管理するモジュール。

docextract の抽出結果 (``output/``) と docagent の集約ストア (``store/``) は、
既定でプロジェクト直下の**単一ディレクトリ** ``.docextract/`` 配下にまとめる。
ホストプロジェクトが既に持つ ``output`` / ``store`` と衝突しないよう、
ドット始まりの固有名を1つだけ作る方針 (``.pytest_cache`` 等と同じ発想)。

    .docextract/
      config.json        <- 数値ガード等の既定パラメータ (利用者が編集できる)
      output/            <- docextract の抽出結果 (<id>/result.json)
        index.json       <- 抽出マニフェスト (id で索引する台帳)
      store/
        library.json     <- docagent の集約 JSON (文書ごとの文書種別)
        doctypes.json    <- 文書種別の定義 (利用者が編集できる)
        facts.json       <- 抽出ファクト (仕様・要件項目、出典付き)
        item_types.json  <- ファクト種別の定義 (利用者が編集できる)
        summaries.json          <- docsummary の要約メタデータ (docsummary/store.py が解決)
        summary_guide.md        <- 要約の観点ガイドの上書き (任意。既定は docsummary 同梱)
        summary_categories.json <- 要約カテゴリー定義の上書き (任意。既定は docsummary 同梱)
      summaries/         <- docsummary の要約 Markdown (<doc_id>.md)

配置換えは環境変数 ``DOCEXTRACT_HOME`` で行う (docextract / docagent 共通の
唯一のつまみ)。個別の上書きは docextract の ``--output-dir`` /
docagent の ``--store`` でも従来どおり可能。

env は呼び出しごとに読む (import 時に固定しない) ので、テストや呼び出し側が
``DOCEXTRACT_HOME`` を設定すれば即座に反映される。
"""

from __future__ import annotations

import os
from pathlib import Path

# データ基点を差し替える環境変数と、その既定値。
ENV_HOME = "DOCEXTRACT_HOME"
DEFAULT_HOME = ".docextract"


def home_dir() -> Path:
    """データ基点ディレクトリ (env ``DOCEXTRACT_HOME``、既定 ``.docextract``)。"""
    return Path(os.environ.get(ENV_HOME) or DEFAULT_HOME)


def config_path() -> Path:
    """数値ガード等の既定パラメータを管理する設定ファイル (``<home>/config.json``)。

    LLM/エージェントへ渡す stdout の上限 (``ceiling_chars``) や各コマンドの既定
    (``text_max_chars`` 等) をここで一元管理し、利用者が編集できる。読み込みと
    既定値は :mod:`docextract.config` が担う。
    """
    return home_dir() / "config.json"


def output_dir() -> Path:
    """docextract の抽出結果ルート (``<home>/output``)。"""
    return home_dir() / "output"


def manifest_path() -> Path:
    """抽出マニフェスト (``<home>/output/index.json``)。

    抽出済み文書を ID で索引する台帳。個別の出力先を ``--output-dir`` で
    変えた場合は、その出力先直下の ``index.json`` を使う (extract() が明示的に
    パスを渡す)。
    """
    return output_dir() / "index.json"


def store_dir() -> Path:
    """docagent の集約ストア用ディレクトリ (``<home>/store``)。"""
    return home_dir() / "store"


def store_path() -> Path:
    """集約 JSON ファイル (``<home>/store/library.json``)。"""
    return store_dir() / "library.json"


def doctypes_path() -> Path:
    """文書種別の定義ファイル (``<home>/store/doctypes.json``)。"""
    return store_dir() / "doctypes.json"


def facts_path() -> Path:
    """抽出ファクト (仕様・要件項目) の集約 JSON (``<home>/store/facts.json``)。"""
    return store_dir() / "facts.json"


def item_types_path() -> Path:
    """ファクトの種別定義ファイル (``<home>/store/item_types.json``)。"""
    return store_dir() / "item_types.json"


def rel_types_path() -> Path:
    """ファクト間参照 (refs) の関係種別定義ファイル (``<home>/store/rel_types.json``)。"""
    return store_dir() / "rel_types.json"


def context_path() -> Path:
    """ブロック作業キュー (``<home>/store/context.json``)。

    context-set が確定した抽出対象ブロックの割り当て・進捗 (pending/claimed/done)
    を保持する。:mod:`docagent.context` が読み書きする。
    """
    return store_dir() / "context.json"
