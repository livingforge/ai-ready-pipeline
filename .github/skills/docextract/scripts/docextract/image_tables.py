"""画像内の表の検出と構造復元 (すべて Apache-2.0 の OSS)。

パイプライン:
1. rapid_layout : 画像内のレイアウト解析で表領域 (bbox) を検出
2. rapid_table  : 検出した領域を切り出し、SLANet-plus で表構造を復元
                  (セルのテキストは内蔵の RapidOCR で認識)
3. 出力された HTML を行列 (rows) にパースして返す

依存パッケージが無い環境やモデル未ダウンロードで失敗した場合は
空リストを返し、抽出全体は止めない。
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

# 表領域として採用する検出スコアの下限
_MIN_SCORE = 0.5
# 切り出し時に bbox の周囲に付ける余白 (px)
_CROP_MARGIN = 8

_layout_engine = None
_table_engine = None


def is_available() -> bool:
    try:
        import rapid_layout  # noqa: F401
        import rapid_table  # noqa: F401
        return True
    except ImportError:
        return False


def detect_tables(
    path: str | Path, lang: str = "ja"
) -> list[tuple[list[list[str]], Optional[list[float]]]]:
    """画像内の表を検出し、(rows, 画像内 bbox) のリストを返す。"""
    try:
        from PIL import Image

        layout = _get_layout_engine()
        img = Image.open(path).convert("RGB")
        out = layout(img)
    except Exception:
        return []

    if out.boxes is None or len(out.boxes) == 0:
        return []

    tables: list[tuple[list[list[str]], Optional[list[float]]]] = []
    for box, cls, score in zip(out.boxes, out.class_names, out.scores):
        if cls != "table" or score < _MIN_SCORE:
            continue
        x0, y0, x1, y1 = (float(v) for v in box)
        crop = img.crop(
            (
                max(0, int(x0) - _CROP_MARGIN),
                max(0, int(y0) - _CROP_MARGIN),
                min(img.width, int(x1) + _CROP_MARGIN),
                min(img.height, int(y1) + _CROP_MARGIN),
            )
        )
        rows = _recognize_structure(crop, lang)
        if rows:
            tables.append((rows, [round(v, 1) for v in (x0, y0, x1, y1)]))
    return tables


def _get_layout_engine():
    global _layout_engine
    if _layout_engine is None:
        from rapid_layout import RapidLayout

        _layout_engine = RapidLayout()
    return _layout_engine


def _get_table_engine(lang: str):
    global _table_engine
    if _table_engine is None:
        from rapid_table import RapidTable
        from rapid_table.utils.typings import RapidTableInput

        from .ocr import _get_rapidocr_engine

        # RapidTable は use_ocr=True で生成すると内部に *もう1つ* RapidOCR
        # (det/cls/rec の3モデル) を読み込む。これは ocr_image が使うエンジンと
        # 同一言語の完全な重複で、常駐メモリを二重に消費する主因になる。
        # そこで use_ocr=False で構築して内部 OCR を作らせず、生成後に共有
        # インスタンスを注入したうえで OCR 経路だけ有効化する。これで RapidOCR
        # 1 セット分の常駐メモリを丸ごと削減できる (表セルの文字認識精度は
        # 同一エンジンを使うため不変)。
        engine = RapidTable(RapidTableInput(use_ocr=False))
        engine.ocr_engine = _get_rapidocr_engine(lang)
        engine.cfg.use_ocr = True
        _table_engine = engine
    return _table_engine


def _recognize_structure(crop, lang: str) -> list[list[str]]:
    try:
        engine = _get_table_engine(lang)
        out = engine(crop)
    except Exception:
        return []
    if not out.pred_htmls or not out.pred_htmls[0]:
        return []
    rows = _html_table_to_rows(out.pred_htmls[0])
    # 全セル空の表はノイズとして捨てる
    if not any(any(cell for cell in row) for row in rows):
        return []
    return rows


class _TableHTMLParser(HTMLParser):
    """rapid_table が出力する HTML テーブルを rows に変換する。"""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: Optional[list[str]] = None
        self._cell: Optional[list[str]] = None
        self._colspan = 1

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
            self._colspan = 1
            for k, v in attrs:
                if k == "colspan" and v and v.isdigit():
                    self._colspan = int(v)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            text = "".join(self._cell).strip()
            self._row.append(text)
            # colspan 分は空セルで埋めて列位置を保つ
            self._row.extend([""] * (self._colspan - 1))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _html_table_to_rows(html: str) -> list[list[str]]:
    parser = _TableHTMLParser()
    parser.feed(html)
    return parser.rows
