"""数値ガード等の既定パラメータを設定ファイル (config.json) で一元管理する。

LLM/エージェントに渡す標準出力は、ホスト側 (Claude Code は Bash 出力 30,000 字で
中央切り詰め、GitHub Copilot はコンテキスト枯渇で切り詰め) で**黙って欠落**しうる。
これを避けるための数値ガードと各コマンドの既定値を、コードにハードコードせず
``<home>/config.json`` (:func:`docextract.paths.config_path`) で一元管理する。
``doctypes.json`` / ``item_types.json`` と同じく**利用者が編集できる**。

キー:
    ceiling_chars       stdout (--json) の上限。超過は拒否し絞り方を案内する
                        (0 で無効化)。既定 30000 = Claude Code の Bash 出力既定に一致。
    text_max_chars      ``text`` の既定最大文字数 (0 で全文)。
    prep_max_chars      ``prep`` が返す本文抜粋の最大文字数。
    search_max_hits     ``search`` が返す最大ヒット数。
    list_preview_chars  ``list`` / ``query`` のスリム射影 preview の短縮長。
    fact_evidence_chars ``facts`` 一覧の evidence (原文抜粋) の短縮長。
    preview_chars       登録時に result.json から作る preview の長さ。

優先順位は **CLI フラグ (明示) > config.json > 組み込み既定 DEFAULTS**。config.json が
無い/一部欠落/不正でも、その分だけ DEFAULTS にフォールバックする (fail-open。
設定ミスでツールが止まらないようにする)。値は 0 以上の整数のみ受け付け、範囲外や
型違いは警告して既定にフォールバックする。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import paths

# 組み込み既定値。config.json が無い/欠落してもここへフォールバックする。
# ceiling_chars=30000 は Claude Code の Bash 出力既定 (BASH_MAX_OUTPUT_LENGTH) に一致。
DEFAULTS: dict[str, int] = {
    "ceiling_chars": 30000,
    "text_max_chars": 20000,
    "prep_max_chars": 8000,
    "search_max_hits": 50,
    "list_preview_chars": 200,
    "fact_evidence_chars": 200,
    "preview_chars": 600,
}


def _valid(value: object) -> bool:
    """設定値として受け付ける条件: 0 以上の整数 (bool は除外)。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def load(path: str | Path | None = None) -> dict[str, int]:
    """設定を読み込み、``DEFAULTS`` に既知キーだけ重ねた dict を返す。

    ``path`` 省略時は :func:`docextract.paths.config_path` (env ``DOCEXTRACT_HOME``
    準拠)。ファイルが無ければ既定をそのまま返す。壊れた JSON・非オブジェクト・
    不正値は既定にフォールバックし、理由を stderr に出す (黙って既定に戻さない)。
    未知キーは無視する。
    """
    p = Path(path) if path else paths.config_path()
    values = dict(DEFAULTS)
    if not p.exists():
        return values
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"[config] {p} を読めません ({e})。組み込み既定を使います。",
            file=sys.stderr,
        )
        return values
    if not isinstance(raw, dict):
        print(
            f"[config] {p} の形式が不正です (オブジェクトが必要)。組み込み既定を使います。",
            file=sys.stderr,
        )
        return values
    for key, val in raw.items():
        if key not in DEFAULTS:
            continue  # 未知キーは無視 (将来キー追加やコメント代わりの記述を許容)
        if _valid(val):
            values[key] = val
        else:
            print(
                f'[config] "{key}" の値が不正です (0 以上の整数が必要): {val!r}。'
                f" 既定 {DEFAULTS[key]} を使います。",
                file=sys.stderr,
            )
    return values


def write_defaults(path: str | Path | None = None, *, overwrite: bool = False) -> bool:
    """既定値の config.json を書き出す。書いたら True、既存を残したら False。

    ``overwrite=False`` (既定) では既存ファイルを上書きしない (利用者の編集を守る)。
    ``init`` から呼び、初回だけ既定を敷く用途。
    """
    p = Path(path) if path else paths.config_path()
    if p.exists() and not overwrite:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(DEFAULTS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return True
