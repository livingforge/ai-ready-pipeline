"""xlsx 抽出器の end-to-end テスト。表領域の検出・空セル除去の挙動が中心。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docextract.extractors import extract_xlsx
from docextract.extractors.base import ImageSaver


def _extract(path, out_dir):
    return extract_xlsx(path, ImageSaver(out_dir)).to_dict()


def _tables(data):
    return [e for e in data["elements"] if e["type"] == "table"]


def test_basic_grid_and_number_stringified(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"S1": [["x", 5], ["y", 10]]})
    data = _extract(src, tmp_path / "out")
    t = _tables(data)[0]
    assert t["location"]["sheet"] == "S1"
    assert t["location"]["range"] == "A1:B2"
    # 数値は str 化される
    assert t["rows"] == [["x", "5"], ["y", "10"]]


def test_none_cell_in_data_column_becomes_empty_string(tmp_path, make_xlsx):
    # 列内に他のデータがある None セルは "" として保持される
    src = make_xlsx(sheets={"S": [["a", None, "c"], ["x", "y", "z"]]})
    data = _extract(src, tmp_path / "out")
    assert _tables(data)[0]["rows"] == [["a", "", "c"], ["x", "y", "z"]]


def test_whitespace_only_cell_treated_as_empty(tmp_path, make_xlsx):
    # 空白のみのセルは空とみなされ、末尾の空列と同様に除去される
    src = make_xlsx(sheets={"S": [["a", "   "], ["b", "\t"]]})
    data = _extract(src, tmp_path / "out")
    t = _tables(data)[0]
    assert t["n_cols"] == 1
    assert t["rows"] == [["a"], ["b"]]


def test_trailing_empty_rows_removed(tmp_path, make_xlsx):
    # 末尾に空行 -> 除去され n_rows は実データ分のみ
    src = make_xlsx(sheets={"S": [["a", "b"], [None, None], [None, None]]})
    data = _extract(src, tmp_path / "out")
    t = _tables(data)[0]
    assert t["n_rows"] == 1
    assert t["rows"] == [["a", "b"]]


def test_trailing_empty_columns_removed(tmp_path, make_xlsx):
    # 末尾に空列 -> 除去
    src = make_xlsx(sheets={"S": [["a", None], ["b", None]]})
    data = _extract(src, tmp_path / "out")
    t = _tables(data)[0]
    assert t["n_cols"] == 1
    assert t["rows"] == [["a"], ["b"]]


def test_leading_empty_rows_and_columns_removed(tmp_path, make_xlsx):
    # データが C3 から始まる -> 先頭の空行・空列は含めない
    src = make_xlsx(
        sheets={
            "S": [
                [None, None, None, None],
                [None, None, None, None],
                [None, None, "a", "b"],
                [None, None, "c", "d"],
            ]
        }
    )
    data = _extract(src, tmp_path / "out")
    t = _tables(data)[0]
    assert t["location"]["range"] == "C3:D4"
    assert t["rows"] == [["a", "b"], ["c", "d"]]


def test_middle_empty_row_removed_but_table_stays_one(tmp_path, make_xlsx):
    # 空行 1 本のスペーサーでは表は分割されず、空行自体は除去される
    src = make_xlsx(sheets={"S": [["a", "b"], [None, None], ["c", "d"]]})
    data = _extract(src, tmp_path / "out")
    tables = _tables(data)
    assert len(tables) == 1
    assert tables[0]["rows"] == [["a", "b"], ["c", "d"]]


def test_tables_split_by_two_or_more_empty_rows(tmp_path, make_xlsx):
    # 空行 2 本以上で離れたセル群は別の表として分割される
    src = make_xlsx(
        sheets={
            "S": [
                ["a", "b"],
                [None, None],
                [None, None],
                ["c", "d"],
            ]
        }
    )
    data = _extract(src, tmp_path / "out")
    tables = _tables(data)
    assert len(tables) == 2
    assert tables[0]["rows"] == [["a", "b"]]
    assert tables[0]["location"]["range"] == "A1:B1"
    assert tables[1]["rows"] == [["c", "d"]]
    assert tables[1]["location"]["range"] == "A4:B4"


def test_tables_split_by_empty_columns(tmp_path, make_xlsx):
    # 横並びの表も空列 2 本以上で分割される
    src = make_xlsx(
        sheets={
            "S": [
                ["a", None, None, "x"],
                ["b", None, None, "y"],
            ]
        }
    )
    data = _extract(src, tmp_path / "out")
    tables = _tables(data)
    assert len(tables) == 2
    assert tables[0]["rows"] == [["a"], ["b"]]
    assert tables[1]["rows"] == [["x"], ["y"]]
    assert tables[1]["location"]["range"] == "D1:D2"


def test_fully_empty_sheet_yields_no_table(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"Empty": [[None, None], [None, None]]})
    data = _extract(src, tmp_path / "out")
    assert _tables(data) == []


def test_multiple_sheets_each_become_table(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"A": [["1"]], "B": [["2"]]})
    data = _extract(src, tmp_path / "out")
    tables = _tables(data)
    sheets = {t["location"]["sheet"] for t in tables}
    assert sheets == {"A", "B"}


def test_image_extracted_with_anchor(tmp_path, make_xlsx, png_file):
    src = make_xlsx(sheets={"S": [["x"]]}, image=("S", png_file, "C3"))
    out = tmp_path / "out"
    data = _extract(src, out)
    images = [e for e in data["elements"] if e["type"] == "image"]
    assert len(images) == 1
    img = images[0]
    assert img["location"]["sheet"] == "S"
    assert img["location"]["anchor"] == "C3"
    assert (out / img["file"]).exists()


def test_image_anchor_beyond_column_z(tmp_path, make_xlsx, png_file):
    # AA1 相当のアンカー -> 2 文字列への変換 (26 進の桁上がり) を検証
    src = make_xlsx(sheets={"S": [["x"]]}, image=("S", png_file, "AA1"))
    data = _extract(src, tmp_path / "out")
    img = [e for e in data["elements"] if e["type"] == "image"][0]
    assert img["location"]["anchor"] == "AA1"


def test_metadata_includes_sheet_names(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"First": [["a"]], "Second": [["b"]]})
    data = _extract(src, tmp_path / "out")
    assert data["metadata"]["sheets"] == ["First", "Second"]
