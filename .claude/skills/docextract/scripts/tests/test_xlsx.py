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
    src = make_xlsx(sheets={"A": [["1", "x"]], "B": [["2", "y"]]})
    data = _extract(src, tmp_path / "out")
    tables = _tables(data)
    sheets = {t["location"]["sheet"] for t in tables}
    assert sheets == {"A", "B"}


def test_isolated_single_cell_becomes_text(tmp_path, make_xlsx):
    # 孤立セル (1x1 の領域) は表ではなくテキスト要素として出る
    src = make_xlsx(sheets={"S": [["基本設計書"]]})
    data = _extract(src, tmp_path / "out")
    assert _tables(data) == []
    texts = [e for e in data["elements"] if e["type"] == "text"]
    assert len(texts) == 1
    assert texts[0]["content"] == "基本設計書"
    assert texts[0]["location"] == {"sheet": "S", "cell": "A1"}


def test_isolated_cells_and_table_mix(tmp_path, make_xlsx):
    # 表紙風レイアウト: 離れたタイトルセル 2 つ + 通常の表が共存する
    src = make_xlsx(
        sheets={
            "S": [
                ["タイトル"],
                [None],
                [None],
                [None, None, "注記"],
                [None],
                [None],
                ["a", "b"],
                ["c", "d"],
            ]
        }
    )
    data = _extract(src, tmp_path / "out")
    texts = [e for e in data["elements"] if e["type"] == "text"]
    assert {(t["content"], t["location"]["cell"]) for t in texts} == {
        ("タイトル", "A1"),
        ("注記", "C4"),
    }
    tables = _tables(data)
    assert len(tables) == 1
    assert tables[0]["rows"] == [["a", "b"], ["c", "d"]]


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


def test_vertical_merge_filled_down(tmp_path, make_xlsx):
    # 幅 1 の縦結合 (分類列の「同上」) は値が全行に展開される
    src = make_xlsx(
        sheets={"S": [["画面表示", "1"], [None, "2"], [None, "3"]]},
        merges={"S": ["A1:A3"]},
    )
    data = _extract(src, tmp_path / "out")
    assert _tables(data)[0]["rows"] == [
        ["画面表示", "1"],
        ["画面表示", "2"],
        ["画面表示", "3"],
    ]


def test_horizontal_merge_keeps_value_only_top_left(tmp_path, make_xlsx):
    # 横結合 (タイトル等) は展開せず左上セルのまま
    src = make_xlsx(
        sheets={"S": [["タイトル", None, None], ["a", "b", "c"]]},
        merges={"S": ["A1:C1"]},
    )
    data = _extract(src, tmp_path / "out")
    assert _tables(data)[0]["rows"] == [["タイトル", "", ""], ["a", "b", "c"]]


def test_block_merge_keeps_value_only_top_left(tmp_path, make_xlsx):
    # 面結合 (複数行 x 複数列、方眼紙の文章など) も展開しない
    src = make_xlsx(
        sheets={"S": [["本文", None, "x"], [None, None, "y"]]},
        merges={"S": ["A1:B2"]},
    )
    data = _extract(src, tmp_path / "out")
    assert _tables(data)[0]["rows"] == [["本文", "x"], ["", "y"]]


def test_metadata_includes_sheet_names(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"First": [["a"]], "Second": [["b"]]})
    data = _extract(src, tmp_path / "out")
    assert data["metadata"]["sheets"] == ["First", "Second"]


def _shapes(data):
    return [
        e
        for e in data["elements"]
        if e["type"] == "text" and e.get("style") == "shape"
    ]


def test_autoshape_text_extracted_as_shape_text(tmp_path, make_xlsx):
    # 図形 (ネットワーク構成図のノード) 内テキストが救出される
    src = make_xlsx(
        sheets={"構成図": [["システム構成図"]]},
        shapes={
            "構成図": [
                {"name": "Node-Web", "text": "Webサーバ\n192.168.1.10", "cell": (1, 4)},
                {"name": "Node-DB", "text": "DBサーバ\n192.168.3.30", "cell": (9, 4)},
            ]
        },
    )
    data = _extract(src, tmp_path / "out")
    shapes = _shapes(data)
    contents = {s["content"] for s in shapes}
    assert contents == {
        "Webサーバ\n192.168.1.10",
        "DBサーバ\n192.168.3.30",
    }
    web = next(s for s in shapes if s["content"].startswith("Web"))
    assert web["location"]["sheet"] == "構成図"
    assert web["location"]["shape_name"] == "Node-Web"
    assert web["location"]["cell"] == "B5"  # col=1,row=4 -> 0始まり -> B5


def test_connector_shape_excluded(tmp_path, make_xlsx):
    # コネクタ (テキストなしの接続線) は抽出対象外
    src = make_xlsx(
        sheets={"S": [["図"]]},
        shapes={
            "S": [
                {"name": "N1", "text": "ノード1", "cell": (0, 3)},
                {"connector": True, "name": "C1"},
                {"name": "N2", "text": "ノード2", "cell": (5, 3)},
            ]
        },
    )
    data = _extract(src, tmp_path / "out")
    contents = {s["content"] for s in _shapes(data)}
    assert contents == {"ノード1", "ノード2"}


def test_empty_text_shape_ignored(tmp_path, make_xlsx):
    # 空白のみのテキスト図形は要素化しない
    src = make_xlsx(
        sheets={"S": [["図"]]},
        shapes={"S": [{"name": "blank", "text": "   ", "cell": (0, 2)}]},
    )
    data = _extract(src, tmp_path / "out")
    assert _shapes(data) == []


def test_shapes_scoped_per_sheet(tmp_path, make_xlsx):
    # 図形が正しいシートに割り当てられる
    src = make_xlsx(
        sheets={"A": [["a"]], "B": [["b"]]},
        shapes={
            "A": [{"name": "sa", "text": "図A", "cell": (0, 3)}],
            "B": [{"name": "sb", "text": "図B", "cell": (0, 3)}],
        },
    )
    data = _extract(src, tmp_path / "out")
    by_sheet = {s["location"]["sheet"]: s["content"] for s in _shapes(data)}
    assert by_sheet == {"A": "図A", "B": "図B"}


def test_workbook_without_drawings_has_no_shape_text(tmp_path, make_xlsx):
    # 図形のないブックは従来どおり shape テキストを出さない (回帰防止)
    src = make_xlsx(sheets={"S": [["a", "b"], ["c", "d"]]})
    data = _extract(src, tmp_path / "out")
    assert _shapes(data) == []
    assert data.get("degraded") is None


def _topology(data):
    tabs = [
        e
        for e in data["elements"]
        if e["type"] == "table"
        and (e.get("location") or {}).get("kind") == "diagram_topology"
    ]
    if not tabs:
        return None
    rows = tabs[0]["rows"]
    assert rows[0] == ["接続元", "接続先"]  # ヘッダ
    return [tuple(r) for r in rows[1:]]


def test_connector_topology_reconstructed_geometrically(tmp_path, make_xlsx):
    # コネクタ端点をノードに幾何スナップして接続関係を復元する
    src = make_xlsx(
        sheets={"S": [["図"]]},
        shapes={
            "S": [
                # A: col0-2, B: col5-7 (いずれも row4-6)
                {"name": "A", "text": "ノードA", "cell": (0, 4)},
                {"name": "B", "text": "ノードB", "cell": (5, 4)},
                # コネクタ: A の右端(2,5) → B の左端(5,5)
                {"connector": True, "cell": (2, 5), "to_cell": (5, 5)},
            ]
        },
    )
    data = _extract(src, tmp_path / "out")
    assert _topology(data) == [("ノードA", "ノードB")]


def test_connector_topology_uses_explicit_connection(tmp_path, make_xlsx):
    # <a:stCxn>/<a:endCxn> の接続先 id があれば幾何より優先して使う。
    # コネクタは幾何的には両ノードから離れた位置に置くが、id で正しく解決される。
    src = make_xlsx(
        sheets={"S": [["図"]]},
        shapes={
            # 図形 id は 2,3,4,... の順 (この並びで id=2:A, id=3:B)
            "S": [
                {"name": "A", "text": "始点", "cell": (0, 0)},
                {"name": "B", "text": "終点", "cell": (20, 20)},
                # 遠く離れた位置のコネクタでも id で A->B に解決される
                {"connector": True, "cell": (40, 40), "to_cell": (41, 41),
                 "st": 2, "end": 3},
            ]
        },
    )
    data = _extract(src, tmp_path / "out")
    assert _topology(data) == [("始点", "終点")]


def test_far_connector_not_snapped(tmp_path, make_xlsx):
    # どのノードからも離れたコネクタ (明示接続なし) は誤接続せず棄却
    src = make_xlsx(
        sheets={"S": [["図"]]},
        shapes={
            "S": [
                {"name": "A", "text": "ノードA", "cell": (0, 0)},
                {"connector": True, "cell": (50, 50), "to_cell": (52, 50)},
            ]
        },
    )
    data = _extract(src, tmp_path / "out")
    assert _topology(data) is None


def test_no_topology_table_without_connectors(tmp_path, make_xlsx):
    # ノードだけでコネクタが無ければトポロジ表は出さない
    src = make_xlsx(
        sheets={"S": [["図"]]},
        shapes={"S": [{"name": "A", "text": "孤立ノード", "cell": (0, 4)}]},
    )
    data = _extract(src, tmp_path / "out")
    assert _topology(data) is None
    assert {s["content"] for s in _shapes(data)} == {"孤立ノード"}


def test_node_carries_shape_id(tmp_path, make_xlsx):
    # ノードのテキスト要素に shape_id が付き、トポロジと突き合わせられる
    src = make_xlsx(
        sheets={"S": [["図"]]},
        shapes={"S": [{"name": "A", "text": "ノードA", "cell": (0, 4)}]},
    )
    data = _extract(src, tmp_path / "out")
    node = _shapes(data)[0]
    assert node["location"]["shape_id"] == "2"


def _diagram_heading(data):
    return [
        e
        for e in data["elements"]
        if e["type"] == "text" and e.get("style") == "diagram"
    ]


def test_diagram_group_bundles_inner_cell_and_shapes_in_order(tmp_path, make_xlsx):
    # 図面領域の内側にあるセル注記が、図形と同じ構成図グループに畳み込まれ、
    # シート冒頭のタイトル(図面外)は版面順でグループより前に来る。
    src = make_xlsx(
        sheets={
            "構成図": [
                ["ネットワーク構成図"],           # A1: 図面外のタイトル
                [None, None, None],
                [None, None, None],
                [None, None, "凡例: Web-DB 接続"],  # C4: 図面内のセル注記 (col2,row3)
            ]
        },
        shapes={
            "構成図": [
                # rect: (1,1)->(3,3) と (1,5)->(3,7)。dbbox=(1,1,3,7)
                {"name": "Web", "text": "Webサーバ", "cell": (1, 1)},
                {"name": "DB", "text": "DBサーバ", "cell": (1, 5)},
            ]
        },
    )
    data = _extract(src, tmp_path / "out")
    els = data["elements"]

    # 図面外のタイトルが先頭 (版面順で図面グループより前)
    assert els[0]["content"] == "ネットワーク構成図"
    # 直後に構成図グループの見出し
    assert els[1].get("style") == "diagram"
    assert els[1]["location"]["range"] == "B2:D8"  # dbbox=(1,1,3,7)
    heading_i = 1

    # 図面内のセル注記は畳み込まれ、図形テキストではない(style!=shape)
    note = next(e for e in els if (e.get("content") or "").startswith("凡例"))
    assert note.get("style") != "shape"
    note_i = els.index(note)

    # 見出しの後にセル注記→図形の順で連続する
    shape_is = [i for i, e in enumerate(els) if e.get("style") == "shape"]
    assert heading_i < note_i < min(shape_is)
    assert {e["content"] for e in _shapes(data)} == {"Webサーバ", "DBサーバ"}
    # 畳み込んだセルは独立した表として二重に出ない
    assert _tables(data) == []


def test_no_diagram_heading_without_shapes(tmp_path, make_xlsx):
    # 図形の無いシートには構成図の見出しを出さない (回帰防止)
    src = make_xlsx(sheets={"S": [["a", "b"], ["c", "d"]]})
    data = _extract(src, tmp_path / "out")
    assert _diagram_heading(data) == []


def test_elements_ordered_by_position_across_types(tmp_path, make_xlsx, png_file):
    # 画像と表が版面の (行,列) 順に並ぶ (型順ではない)
    src = make_xlsx(
        sheets={"S": [["表1"], [None], [None], [None], [None], ["表2下"]]},
        image=("S", png_file, "A3"),  # 表1(row0) と 表2(row5) の間 (row2)
    )
    data = _extract(src, tmp_path / "out")
    order = [
        e.get("content") or e["type"]
        for e in data["elements"]
    ]
    assert order == ["表1", "image", "表2下"]
