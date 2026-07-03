"""Excel (.xlsx) の抽出器。

各シートから「表領域」を検出して抽出する。

Excel の使用範囲 (used range) は書式だけのセルや削除済みデータの残骸で
実データより大きく膨らむことが多く、そのまま 2 次元配列にすると大量の
空文字が混じる。そこで非空セルの連結成分 (connected components) から
表領域を検出し、領域ごとに 1 つの表として抽出する:

- 空白ギャップ 1 行/列以内で隣接するセルは同じ表とみなす
  (表中のスペーサー行で分断されないため)
- 2 行/列以上の空白で離れたセル群は別の表として分割する
- 各表の内部でも完全な空行・空列は除去する

シートに埋め込まれた画像も取り出す。
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook

from ..models import ExtractionResult, ImageElement, TableElement
from .base import ImageSaver

# 同じ表とみなす空白ギャップの許容幅 (行/列数)
_GAP = 1


def extract_xlsx(path: Path, saver: ImageSaver) -> ExtractionResult:
    # data_only=True で数式ではなくキャッシュされた計算結果を読む
    wb = load_workbook(str(path), data_only=True)
    result = ExtractionResult(source=path.name, file_type="xlsx")

    props = wb.properties
    result.metadata = {
        "title": props.title or None,
        "author": props.creator or None,
        "created": props.created.isoformat() if props.created else None,
        "modified": props.modified.isoformat() if props.modified else None,
        "sheets": wb.sheetnames,
    }

    for ws in wb.worksheets:
        grid = _sheet_to_grid(ws)
        for top, left, bottom, right in _find_table_regions(grid):
            rows = _region_to_rows(grid, top, left, bottom, right)
            cell_range = (
                f"{_col_letter(left + 1)}{top + 1}:"
                f"{_col_letter(right + 1)}{bottom + 1}"
            )
            result.elements.append(
                TableElement(
                    rows=rows,
                    location={"sheet": ws.title, "range": cell_range},
                )
            )
        for img in getattr(ws, "_images", []):
            try:
                data = img._data()
            except Exception:
                continue
            ext = getattr(img, "format", None) or "png"
            rel_path = saver.save(data, str(ext))
            result.elements.append(
                ImageElement(
                    file=rel_path,
                    format=str(ext).lower(),
                    location={"sheet": ws.title, "anchor": _anchor_cell(img)},
                )
            )
    return result


def _cell_to_str(v) -> str:
    """セル値を文字列化する。None と空白のみの値は空とみなす。"""
    if v is None:
        return ""
    s = str(v)
    return s if s.strip() else ""


def _sheet_to_grid(ws) -> list[list[str]]:
    return [[_cell_to_str(v) for v in row] for row in ws.iter_rows(values_only=True)]


def _find_table_regions(
    grid: list[list[str]],
) -> list[tuple[int, int, int, int]]:
    """非空セルの連結成分から表領域の外接矩形を求める。

    空白ギャップ _GAP 以内 (チェビシェフ距離 _GAP+1 以内) のセル同士を
    連結とみなして flood fill し、成分ごとの外接矩形を返す。
    戻り値は 0 始まりの (top, left, bottom, right) を位置順に並べたもの。
    """
    filled = {
        (r, c)
        for r, row in enumerate(grid)
        for c, v in enumerate(row)
        if v
    }
    reach = _GAP + 1
    offsets = [
        (dr, dc)
        for dr in range(-reach, reach + 1)
        for dc in range(-reach, reach + 1)
        if dr or dc
    ]
    regions: list[tuple[int, int, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for start in filled:
        if start in seen:
            continue
        seen.add(start)
        stack = [start]
        top, left = start
        bottom, right = start
        while stack:
            r, c = stack.pop()
            top = min(top, r)
            bottom = max(bottom, r)
            left = min(left, c)
            right = max(right, c)
            for dr, dc in offsets:
                nb = (r + dr, c + dc)
                if nb in filled and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        regions.append((top, left, bottom, right))
    return sorted(regions)


def _region_to_rows(
    grid: list[list[str]], top: int, left: int, bottom: int, right: int
) -> list[list[str]]:
    """外接矩形から行列を切り出し、内部の完全な空行・空列を除去する。"""
    rows = [row[left : right + 1] for row in grid[top : bottom + 1]]
    rows = [r for r in rows if any(r)]
    keep = [j for j in range(right - left + 1) if any(r[j] for r in rows)]
    return [[r[j] for j in keep] for r in rows]


def _col_letter(n: int) -> str:
    """1 始まりの列番号を A1 形式の列文字に変換する。"""
    col = ""
    while n:
        n, rem = divmod(n - 1, 26)
        col = chr(65 + rem) + col
    return col


def _anchor_cell(img) -> str | None:
    try:
        anc = img.anchor._from
        return f"{_col_letter(anc.col + 1)}{anc.row + 1}"
    except Exception:
        return None
