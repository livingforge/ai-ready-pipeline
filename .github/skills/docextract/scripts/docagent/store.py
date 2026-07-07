"""集約 JSON ストア (ライブラリ) の読み書きと操作を担う中核モジュール。

docextract が各文書ごとに出力する ``result.json`` を取り込み、**文書種別 (doctype)** を
付与して、**単一の集約 JSON** (既定 ``.docextract/store/library.json``) に束ねる。
文書種別は「その資料が要件定義書か・設計書か・議事録か」といった**現状把握のための
分類**で、doc-indexer が付ける。要約のような人間向け終端フォーマットは持たない
(仕様の中身は spec-extractor が facts.json に、横断検索は search が担う)。

- データ操作:  add / sync / set_doctype / remove
- データ参照:  get / list / query / stats / export / search

CLI (``docagent/cli.py``) と Python API の両方からこの ``Library`` を使う。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import get_close_matches
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from docextract import paths as _paths

SCHEMA_VERSION = 1

# 既定の保存先。docextract と同じ基点 (.docextract/、env DOCEXTRACT_HOME で変更可)
# 配下の store/ にまとめ、ホストプロジェクトの store/ と衝突しないようにする。
# CLI / API から個別に上書きも可能。
DEFAULT_STORE = _paths.store_path()
DEFAULT_DOCTYPES = _paths.doctypes_path()

# 既定の文書種別の定義ファイル (パッケージ同梱)。コードにはハードコードしない。
# 実行時は store/doctypes.json (利用者が各自編集できる) が存在すればそちらが優先。
PACKAGED_DOCTYPES = Path(__file__).resolve().parent / "doctypes.json"

# 種別が未設定の文書を集計で表す表示名 (タクソノミー外の一時値)。
UNCLASSIFIED = "未分類"


def _now() -> str:
    """ISO8601 (UTC) のタイムスタンプ文字列。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DocAgentError(Exception):
    """docagent 由来のユーザー向けエラー。"""


def default_doctypes() -> list[str]:
    """パッケージ同梱の doctypes.json から既定の文書種別を読む。"""
    dts = _read_doctypes_file(PACKAGED_DOCTYPES)
    if dts is None:
        raise DocAgentError(
            f"既定の文書種別定義が読めません: {PACKAGED_DOCTYPES}。"
            " docagent パッケージに doctypes.json が同梱されているか確認してください"
        )
    return dts


@dataclass
class Library:
    """集約 JSON ストア本体。

    ファイルとの入出力は :meth:`load` / :meth:`save` が担い、それ以外の操作は
    メモリ上の ``documents`` に対して行う。
    """

    path: Path
    doctypes_path: Path | None = None
    version: int = SCHEMA_VERSION
    doctypes: list[str] = field(default_factory=default_doctypes)
    documents: list[dict[str, Any]] = field(default_factory=list)
    # 登録時に result.json から作る preview の長さ。CLI が config.json の
    # preview_chars を反映して差し替える (既定 600)。
    preview_chars: int = 600

    # ── 入出力 ────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | Path, doctypes_path: str | Path | None = None) -> "Library":
        """ストアを読み込む。無ければ空のライブラリを返す (ファイルは作らない)。"""
        path = Path(path)
        dt_path = Path(doctypes_path) if doctypes_path else path.parent / "doctypes.json"
        doctypes = _load_doctypes(dt_path)

        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            lib = cls(
                path=path,
                doctypes_path=dt_path,
                version=data.get("version", SCHEMA_VERSION),
                doctypes=data.get("doctypes") or doctypes,
                documents=data.get("documents", []),
            )
            # doctypes.json が別に存在すればそちらを正とする。
            if dt_path.exists():
                lib.doctypes = doctypes
            return lib
        return cls(path=path, doctypes_path=dt_path, doctypes=doctypes)

    def save(self) -> None:
        """ストアを JSON として書き出す (親ディレクトリは自動作成)。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "doctypes": self.doctypes,
            "documents": self.documents,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ── 参照 ──────────────────────────────────────────────────
    def get(self, doc_id: str) -> dict[str, Any]:
        for d in self.documents:
            if d["id"] == doc_id:
                return d
        # エラー文に「次の一手」を含める: エージェントが自力で立ち直れるように。
        close = get_close_matches(doc_id, [d["id"] for d in self.documents], n=3, cutoff=0.5)
        hint = f" 似た ID: {', '.join(close)}。" if close else ""
        raise DocAgentError(
            f"ID '{doc_id}' の文書は登録されていません。{hint}"
            f" 登録済み ID の一覧: python -m docagent list"
        )

    def find(self, doc_id: str) -> dict[str, Any] | None:
        for d in self.documents:
            if d["id"] == doc_id:
                return d
        return None

    def query(
        self,
        doctype: str | None = None,
        text: str | None = None,
    ) -> list[dict[str, Any]]:
        """条件でフィルタして文書リストを返す。"""
        results = self.documents
        if doctype:
            results = [d for d in results if d.get("doctype") == doctype]
        if text:
            t = text.lower()
            results = [d for d in results if t in _searchable_text(d)]
        return list(results)

    def stats(self) -> dict[str, Any]:
        """文書種別別の件数などの集計。"""
        by_doctype: dict[str, int] = {}
        for d in self.documents:
            key = d.get("doctype") or UNCLASSIFIED
            by_doctype[key] = by_doctype.get(key, 0) + 1
        return {
            "total": len(self.documents),
            "by_doctype": by_doctype,
        }

    def export(self) -> dict[str, Any]:
        """集約 JSON 全体を dict で返す (データ参照)。"""
        return {
            "version": self.version,
            "doctypes": self.doctypes,
            "documents": self.documents,
        }

    def extract_text(
        self, doc_id: str, max_chars: int | None = None, offset: int = 0
    ) -> dict[str, Any]:
        """result.json から本文テキストだけを組み立てて返す (軽量ビュー)。

        座標・レイアウト等の JSON フィールドを落とし、テキスト全文・表の全行・
        画像 OCR をプレーンテキストに整形する。モデルに渡すトークン量を
        result.json 全体の Read より大幅に抑えるための参照系。

        ``offset`` から ``max_chars`` 文字だけを切り出す窓を返し (``max_chars`` が
        ``None`` なら残り全部)、``truncated`` / ``next_offset`` で「続きがあるか・
        次はどこから読むか」を必ず示す。これで巨大文書の全文を一度に stdout へ
        流さず、必要な分だけページングで受け取れる。
        """
        doc = self.get(doc_id)
        result_path = Path(doc.get("result_path") or "")
        if not result_path.exists():
            raise DocAgentError(
                f"result.json が見つかりません: {result_path}。"
                f" docextract で再抽出してから python -m docagent prep <result.json>"
                f" で登録し直してください"
            )
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        text = _render_text(result)
        total = len(text)
        offset = max(0, offset)
        window = text[offset:]
        if max_chars is not None:
            window = window[:max_chars]
        end = offset + len(window)
        truncated = end < total
        return {
            "id": doc_id,
            "source": doc.get("source"),
            "total_chars": total,
            "offset": offset,
            "returned_chars": len(window),
            "truncated": truncated,
            "next_offset": end if truncated else None,
            "text": window,
        }

    def prep(self, target: str, max_chars: int | None = 8000) -> dict[str, Any]:
        """取り込み準備を 1 コマンドに集約する (エージェントの手順を減らす複合操作)。

        ``target`` が result.json のパスなら登録 (既存なら抽出フィールドのみ更新し、
        文書種別は保持) してストアを保存、登録済み ID ならそのまま参照する。
        返り値に、文書種別の付与 (doc-indexer) や仕様抽出 (spec-extractor) に必要な
        材料 (種別候補・preview・本文抜粋・次の一手) を含める。
        """
        path = Path(target)
        if path.is_file():
            doc = self.add_from_result(path, overwrite=True)
            self.save()
        else:
            found = self.find(target)
            if found is None:
                raise DocAgentError(
                    f"'{target}' は登録済みの文書 ID でも result.json のパスでもありません。"
                    f" 登録済み ID の一覧: python -m docagent list"
                )
            doc = found

        payload: dict[str, Any] = {
            "id": doc["id"],
            "source": doc.get("source"),
            "file_type": doc.get("file_type"),
            "doctype": doc.get("doctype"),
            "already_classified": doc.get("doctype") is not None,
            "doctypes": list(self.doctypes),
            "metadata": doc.get("metadata", {}),
            "stats": doc.get("stats", {}),
            "preview": doc.get("preview", ""),
        }
        try:
            text = self.extract_text(doc["id"], max_chars=max_chars)
            payload["text"] = text["text"]
            payload["text_truncated"] = text["truncated"]
            payload["text_total_chars"] = text["total_chars"]
        except DocAgentError:
            # result.json が失われていても preview だけで判断できるようにする。
            payload["text"] = None
        payload["next_action"] = (
            "現状把握なら doctypes から 1 つ選んで文書種別を付与: "
            f'python -m docagent set-doctype {doc["id"]} "<種別>"。'
            " 仕様を洗い出すなら本文から出典付きファクトを抽出: "
            f"python -m docagent fact-add --doc {doc['id']} ..."
        )
        return payload

    # ── 操作 ──────────────────────────────────────────────────
    def add_from_result(self, result_path: str | Path, overwrite: bool = False) -> dict[str, Any]:
        """docextract の result.json を取り込み、文書エントリを登録する。

        文書種別は未設定 (``doctype=None``) で登録し、後から :meth:`set_doctype`
        で埋める。
        """
        result_path = Path(result_path)
        result = _load_result_json(result_path)

        # ID は docextract が result.json に書き込んだ値を正とする (再計算しない)。
        # これで「出力フォルダ名とストア ID の不一致」が構造的に起きない。
        doc_id = result.get("id")
        if not doc_id:
            raise DocAgentError(
                f"result.json に id がありません: {result_path}。"
                " id を含む新しい形式が必要です。docextract で再抽出してください:"
                f" python -m docextract <元ファイル>"
            )
        source = result.get("source", result_path.stem)
        existing = self.find(doc_id)
        if existing and not overwrite:
            raise DocAgentError(
                f"ID '{doc_id}' は既に登録済みです。上書き登録するには:"
                f" python -m docagent add {result_path} --overwrite"
                f" (文書種別は保持されます)。取り込み準備なら:"
                f" python -m docagent prep {result_path}"
            )

        entry = {
            "id": doc_id,
            "source": source,
            "source_abspath": result.get("source_abspath"),
            "content_hash": result.get("content_hash"),
            "file_type": result.get("file_type"),
            "result_path": str(result_path).replace("\\", "/"),
            "metadata": result.get("metadata", {}),
            "stats": result.get("summary", {}),
            "preview": _build_preview(result, self.preview_chars),
            "doctype": None,
            "added_at": existing["added_at"] if existing else _now(),
            "updated_at": _now(),
        }
        if existing:
            # 既存の文書種別は保持しつつ抽出由来のフィールドのみ更新。
            entry["doctype"] = existing.get("doctype")
            self.documents[self.documents.index(existing)] = entry
        else:
            self.documents.append(entry)
        return entry

    def sync_from_manifest(self, manifest_path: str | Path) -> dict[str, Any]:
        """抽出マニフェスト (output/index.json) の全文書を一括で登録/更新する。

        doc-indexer が「フォルダを抽出 → まとめて索引化」を1コマンドで行うための
        複合操作。既存の文書種別は :meth:`add_from_result` が保持する。
        result.json が失われている項目はスキップして報告する。
        返り値は ``{"added": [...], "updated": [...], "skipped": [...]}``。
        """
        manifest_path = Path(manifest_path)
        if not manifest_path.exists():
            raise DocAgentError(
                f"抽出マニフェストが見つかりません: {manifest_path}。"
                " 先に docextract で抽出してください: python -m docextract --dir <フォルダ> -r"
            )
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            raise DocAgentError(f"マニフェストを読み込めません: {manifest_path} ({e})") from e
        docs = (data or {}).get("documents", {})
        added, updated, skipped = [], [], []
        for doc_id, entry in docs.items():
            result_path = Path(entry.get("result_path", ""))
            if not result_path.exists():
                skipped.append(doc_id)
                continue
            existed = self.find(doc_id) is not None
            self.add_from_result(result_path, overwrite=True)
            (updated if existed else added).append(doc_id)
        return {"added": added, "updated": updated, "skipped": skipped}

    def search(
        self, term: str, doc_id: str | None = None, max_hits: int = 50
    ) -> list[dict[str, Any]]:
        """登録済み文書の result.json 本文を横断検索し、出典付きヒットを返す。

        doc-qa が「資料のどこに何が書いてあるか」を出典 (doc_id + location) 付きで
        答えるための接地 (grounding) 手段。各ヒットは
        ``{"doc_id", "source", "location", "kind", "score", "snippet"}``。座標情報を
        保ったまま、テキスト・表・画像 OCR の要素単位で一致を探す。

        照合はクエリ・本文の双方を :func:`_fold_text` (NFKC + 小文字化 + 空白除去)
        で畳んでから行うため、全角/半角・大文字小文字・要素内の改行や空白をまたぐ
        語も一致する。空白区切りの複数キーワードは **AND 条件** (すべて含む要素だけ
        ヒット)。結果は先着順ではなく関連度スコア順 (一致回数 + フレーズ一致ボーナス、
        OCR は減点) の上位 ``max_hits`` 件を返す。
        """
        keywords = [f for w in (term or "").split() if (f := _fold_text(w)[0])]
        if not keywords:
            return []
        phrase = "".join(keywords)
        hits: list[dict[str, Any]] = []
        for doc in self.documents:
            if doc_id and doc["id"] != doc_id:
                continue
            rp = Path(doc.get("result_path") or "")
            if not rp.exists():
                continue
            try:
                result = json.loads(rp.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            for el in result.get("elements", []):
                text, kind = _element_search_text(el)
                if not text:
                    continue
                folded, idx_map = _fold_text(text)
                if not all(k in folded for k in keywords):
                    continue
                # 一致回数を数える (1 語あたり上限 3: 巨大な表 1 枚が上位を独占しないように)。
                score = float(sum(min(folded.count(k), 3) for k in keywords))
                # snippet の中心は先頭キーワードの初出位置。フレーズ一致ならそちらを優先。
                anchor, anchor_len = folded.find(keywords[0]), len(keywords[0])
                if len(keywords) > 1:
                    p = folded.find(phrase)
                    if p >= 0:
                        score += 3.0
                        anchor, anchor_len = p, len(phrase)
                score *= _KIND_WEIGHTS.get(kind, 1.0)
                start = idx_map[anchor]
                end = idx_map[anchor + anchor_len - 1] + 1
                hits.append(
                    {
                        "doc_id": doc["id"],
                        "source": doc.get("source"),
                        "location": el.get("location", {}),
                        "kind": kind,
                        "score": round(score, 2),
                        "snippet": _snippet(text, start, end),
                    }
                )
        # スコア降順 (同点は文書の登録順・要素の出現順を保つ安定ソート)。
        hits.sort(key=lambda h: -h["score"])
        return hits[:max_hits]

    def set_doctype(self, doc_id: str, doctype: str, force: bool = False) -> dict[str, Any]:
        """文書種別を設定する。既定では定義内の値に正規化して許可。

        LLM は「設計書」「基本設計 」「『詳細設計』」のように表記が揺れた値を返す
        ことがある。厳密一致で弾く代わりに :func:`_resolve_term` で正式名へ寄せ、
        寄せられないときだけ拒否する（``force`` 時は正規化のみで任意許可）。
        """
        doc = self.get(doc_id)
        doc["doctype"] = _resolve_term(doctype, self.doctypes, force=force, label="文書種別")
        doc["updated_at"] = _now()
        return doc

    def remove(self, doc_id: str) -> dict[str, Any]:
        doc = self.get(doc_id)
        self.documents.remove(doc)
        return doc

    # ── 文書種別の管理 ────────────────────────────────────────
    def add_doctype(self, name: str) -> None:
        if name not in self.doctypes:
            self.doctypes.append(name)

    def remove_doctype(self, name: str) -> None:
        if name in self.doctypes:
            self.doctypes.remove(name)

    def save_doctypes(self) -> None:
        if self.doctypes_path:
            self.doctypes_path.parent.mkdir(parents=True, exist_ok=True)
            self.doctypes_path.write_text(
                json.dumps({"doctypes": self.doctypes}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


# 統制語彙の外側を囲みがちな引用符・括弧 (全角半角)。LLM が『基本設計』の
# ように装飾して返す揺れを剥がすため。
_TERM_WRAPPERS = "「」『』｢｣（）()【】[]〈〉《》\"'`"


def _normalize_term(raw: str) -> str:
    """統制語の表記揺れを畳む: NFKC・前後空白・外側の囲み記号を除去。"""
    s = unicodedata.normalize("NFKC", raw).strip()
    # 外側の囲み記号を内側が変わらなくなるまで繰り返し剥がす (『議事録』→ 議事録)。
    prev = None
    while prev != s and len(s) >= 2:
        prev = s
        s = s.strip(_TERM_WRAPPERS).strip()
    return s


def _loose_term(s: str) -> str:
    """比較用のゆるい形: 区切り (中黒・スラッシュ・空白) を除いた芯だけにする。"""
    return re.sub(r"[・/／\s]", "", s)


def _resolve_term(
    raw: str, terms: list[str], force: bool = False, label: str = "値"
) -> str:
    """入力語を統制語彙 (文書種別・ファクト種別など) 内の正式名へ解決する。

    表記揺れは吸収し、判断がつかないときだけ :class:`DocAgentError` で拒否する。
    解決は信頼度の高い順に試す:

    1. 正規化 (NFKC・空白・囲み記号除去) 後の完全一致
    2. 区切り記号を無視したゆるい一致 (見積／費用 ↔ 見積・費用)
    3. 一意な前方一致 (基本設計 → 基本設計書。候補が複数なら不採用)
    4. difflib による高信頼の近似一致 (cutoff=0.8)

    ``force`` 時は語彙に合わせず、正規化した値をそのまま採用する。``label`` は
    エラー文言に使う語彙の呼称 (例: 「文書種別」「種別」)。
    """
    norm = _normalize_term(raw)
    if force:
        return norm or raw
    # 1. 正規化後の完全一致
    for c in terms:
        if norm == _normalize_term(c):
            return c
    loose = _loose_term(norm)
    # 2. 区切りを無視したゆるい一致
    for c in terms:
        if loose and loose == _loose_term(_normalize_term(c)):
            return c
    # 3. 一意な前方一致 (どちらかがもう一方の接頭辞)
    prefix_hits = [
        c
        for c in terms
        if loose
        and (
            (lc := _loose_term(_normalize_term(c))).startswith(loose)
            or loose.startswith(lc)
        )
    ]
    if len(prefix_hits) == 1:
        return prefix_hits[0]
    # 4. 近似一致 (正規化した候補集合に対して)
    norm_map = {_normalize_term(c): c for c in terms}
    close = get_close_matches(norm, list(norm_map), n=1, cutoff=0.8)
    if close:
        return norm_map[close[0]]
    raise DocAgentError(_unknown_term_message(raw, terms, label))


def _unknown_term_message(raw: str, terms: list[str], label: str) -> str:
    """語彙外の値の拒否メッセージ (正しい選択肢を必ず添える)。"""
    close = get_close_matches(raw, terms, n=1, cutoff=0.4)
    hint = f" もしかして: {close[0]}。" if close else ""
    return (
        f"{label} '{raw}' は定義にありません。{hint}"
        f" この一覧から選び直してください: {', '.join(terms)}"
    )


# docextract が扱う元ファイルの拡張子。これらが result.json の代わりに直接
# 渡されるのは「抽出ステップを飛ばした」典型的な事故なので、専用の案内を出す。
_RAW_DOC_SUFFIXES = {
    ".docx", ".xlsx", ".xlsm", ".pptx", ".pdf", ".doc", ".xls", ".ppt",
}


def _load_result_json(result_path: Path) -> dict[str, Any]:
    """docextract の result.json を読み込む (取り込み系の共通入口)。

    元ファイルの直接渡し・壊れた JSON・想定外の形式を、生の JSONDecodeError では
    なく「次の一手」を添えた :class:`DocAgentError` に変換する。docextract を
    通さずに Excel 等をそのまま渡す事故を、その場で立ち直れる形で弾くための関門。
    """
    if not result_path.exists():
        raise DocAgentError(f"result.json が見つかりません: {result_path}")
    if result_path.suffix.lower() in _RAW_DOC_SUFFIXES:
        raise DocAgentError(
            f"result.json ではなく元ファイルが渡されました: {result_path}。"
            " docagent が受け取るのは docextract が出力した result.json です。"
            " 先に抽出してください: "
            f"python -m docextract {result_path}"
            " → 生成された .docextract/output/<id>/result.json を渡す"
        )
    try:
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DocAgentError(
            f"result.json として読み込めません: {result_path} ({e})。"
            " docextract が出力した result.json か確認してください"
            "（元ファイルを直接渡していませんか？ その場合は先に"
            " python -m docextract で抽出してください）"
        ) from e
    if not isinstance(result, dict) or "elements" not in result:
        raise DocAgentError(
            f"docextract の result.json 形式ではありません（'elements' がありません）: "
            f"{result_path}"
        )
    return result


def _load_doctypes(dt_path: Path) -> list[str]:
    dts = _read_doctypes_file(dt_path)
    return dts if dts is not None else default_doctypes()


def _read_doctypes_file(path: Path) -> list[str] | None:
    """doctypes.json を読んで文書種別一覧を返す。無効・不存在なら None。"""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    dts = data.get("doctypes") if isinstance(data, dict) else data
    return list(dts) if dts else None


def _searchable_text(doc: dict[str, Any]) -> str:
    parts = [
        doc.get("source", ""),
        doc.get("doctype") or "",
        doc.get("preview", ""),
        json.dumps(doc.get("metadata", {}), ensure_ascii=False),
    ]
    return " ".join(parts).lower()


def _element_search_text(el: dict[str, Any]) -> tuple[str, str]:
    """検索対象の要素を (テキスト, 種別ラベル) に整形する。非対象は ("", "")。"""
    t = el.get("type")
    if t == "text" and el.get("content"):
        return el["content"], "text"
    if t == "table" and el.get("rows"):
        return "\n".join(" | ".join(str(c) for c in row) for row in el["rows"]), "table"
    if t == "image" and el.get("ocr_text"):
        return el["ocr_text"], "image_ocr"
    return "", ""


# 要素種別ごとのスコア係数。画像 OCR は誤認識由来のノイズを含みやすいので、
# 本文・表と同点のときに上に来ないよう少しだけ割り引く。
_KIND_WEIGHTS = {"text": 1.0, "table": 1.0, "image_ocr": 0.7}


def _fold_text(text: str) -> tuple[str, list[int]]:
    """検索照合用にテキストを畳む: NFKC + 小文字化 + 空白 (改行含む) 除去。

    全角/半角 (ＣＳＶ↔CSV)・大文字小文字・抽出時に紛れ込む改行や空白の揺れを
    吸収するための正規化。畳んだ文字列と「畳んだ後の各文字 → 元テキストの
    インデックス」の対応表を返し、一致位置から元テキストの snippet を切り出せる
    ようにする。
    """
    chars: list[str] = []
    idx_map: list[int] = []
    for i, ch in enumerate(text):
        for c in unicodedata.normalize("NFKC", ch).lower():
            if c.isspace():
                continue
            chars.append(c)
            idx_map.append(i)
    return "".join(chars), idx_map


def _snippet(text: str, start: int, end: int, width: int = 160) -> str:
    """一致範囲 [start, end) の前後を切り出した抜粋 (前後は … で省略)。"""
    pad = max(0, (width - (end - start)) // 2)
    s0 = max(0, start - pad)
    e0 = min(len(text), end + pad)
    s = " ".join(text[s0:e0].split())
    return ("…" if s0 > 0 else "") + s + ("…" if e0 < len(text) else "")


def _render_text(result: dict[str, Any]) -> str:
    """elements をプレーンテキストへ整形する (text 全文・表の全行・画像 OCR)。"""
    return render_elements(result.get("elements", []))


def render_elements(elements: list[dict[str, Any]]) -> str:
    """要素リストをプレーンテキストへ整形する (context のブロック描画とも共用)。"""
    lines: list[str] = []
    for el in elements:
        t = el.get("type")
        if t == "text" and el.get("content"):
            lines.append(el["content"])
        elif t == "table" and el.get("rows"):
            lines.append("[表]")
            lines.extend(" | ".join(str(c) for c in row) for row in el["rows"])
        elif t == "image" and el.get("ocr_text"):
            lines.append("[画像OCR] " + el["ocr_text"])
    return "\n".join(lines)


def _build_preview(result: dict[str, Any], max_chars: int = 600) -> str:
    """result.json 先頭のテキスト・OCR を連結し、種別判定の手がかりになる抜粋を作る。"""
    chunks: list[str] = []
    for el in result.get("elements", []):
        if el.get("type") == "text" and el.get("content"):
            chunks.append(el["content"])
        elif el.get("type") == "table" and el.get("rows"):
            head = el["rows"][0] if el["rows"] else []
            chunks.append("[表] " + " | ".join(str(c) for c in head))
        elif el.get("type") == "image" and el.get("ocr_text"):
            chunks.append("[画像OCR] " + el["ocr_text"])
        joined = "\n".join(chunks)
        if len(joined) >= max_chars:
            return joined[:max_chars]
    return "\n".join(chunks)[:max_chars]
