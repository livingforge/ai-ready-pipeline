"""models.py のデータモデル — 特に to_dict の境界挙動を検証する。"""

from __future__ import annotations

from docextract.models import (
    ExtractionResult,
    ImageElement,
    TableElement,
    TextElement,
)


class TestTextElement:
    def test_full(self):
        d = TextElement("hello", style="Heading 1", location={"order": 3}).to_dict()
        assert d == {
            "type": "text",
            "content": "hello",
            "style": "Heading 1",
            "location": {"order": 3},
        }

    def test_no_style_no_location(self):
        d = TextElement("plain").to_dict()
        assert d == {"type": "text", "content": "plain"}
        assert "style" not in d and "location" not in d

    def test_empty_style_omitted(self):
        # 空文字の style は falsy なので出力されない
        d = TextElement("x", style="").to_dict()
        assert "style" not in d

    def test_empty_location_omitted(self):
        d = TextElement("x", location={}).to_dict()
        assert "location" not in d

    def test_empty_content_preserved(self):
        # content が空文字でも content キー自体は必ず存在する
        d = TextElement("").to_dict()
        assert d["content"] == ""

    def test_unicode_and_newlines_preserved(self):
        content = "日本語\n改行\tタブ"
        assert TextElement(content).to_dict()["content"] == content


class TestTableElement:
    def test_basic_dimensions(self):
        d = TableElement([["a", "b", "c"], ["1", "2", "3"]]).to_dict()
        assert d["n_rows"] == 2
        assert d["n_cols"] == 3
        assert d["type"] == "table"

    def test_empty_rows(self):
        # 行が空 -> n_rows=0, n_cols=0 (default=0 が効く)
        d = TableElement([]).to_dict()
        assert d["n_rows"] == 0
        assert d["n_cols"] == 0
        assert d["rows"] == []

    def test_ragged_rows_ncols_is_max(self):
        # 不揃いの行 -> n_cols は最大列数
        d = TableElement([["a"], ["b", "c", "d"], ["e", "f"]]).to_dict()
        assert d["n_rows"] == 3
        assert d["n_cols"] == 3

    def test_row_of_empty_strings_counts_columns(self):
        d = TableElement([["", "", ""]]).to_dict()
        assert d["n_rows"] == 1
        assert d["n_cols"] == 3

    def test_location_omitted_when_empty(self):
        assert "location" not in TableElement([["a"]]).to_dict()


class TestImageElement:
    def test_full(self):
        # location はキーワードで渡す (フィールド順は width,height,ocr_text,location)
        d = ImageElement("images/i.png", "png", 60, 40, location={"order": 4}).to_dict()
        assert d == {
            "type": "image",
            "file": "images/i.png",
            "format": "png",
            "width": 60,
            "height": 40,
            "location": {"order": 4},
        }

    def test_with_ocr_text(self):
        d = ImageElement(
            "images/i.png", "png", 60, 40, ocr_text="図1: 売上推移", location={"order": 4}
        ).to_dict()
        assert d["ocr_text"] == "図1: 売上推移"
        assert d["location"] == {"order": 4}

    def test_empty_ocr_text_omitted(self):
        # 空文字の ocr_text は出力されない
        d = ImageElement("i.png", "png", ocr_text="").to_dict()
        assert "ocr_text" not in d

    def test_minimal(self):
        d = ImageElement("images/i.png", "jpg").to_dict()
        assert d == {"type": "image", "file": "images/i.png", "format": "jpg"}
        assert "width" not in d and "height" not in d

    def test_zero_dimensions_are_included(self):
        # 0 は None ではないので出力される (is not None 判定の境界)
        d = ImageElement("i.png", "png", width=0, height=0).to_dict()
        assert d["width"] == 0
        assert d["height"] == 0

    def test_only_width_present(self):
        d = ImageElement("i.png", "png", width=100).to_dict()
        assert d["width"] == 100
        assert "height" not in d


class TestExtractionResult:
    def test_summary_counts_by_type(self):
        res = ExtractionResult(source="s.docx", file_type="docx")
        res.elements = [
            TextElement("a"),
            TextElement("b"),
            TableElement([["x"]]),
            ImageElement("i.png", "png"),
        ]
        out = res.to_dict()
        assert out["summary"] == {"text": 2, "table": 1, "image": 1}
        assert out["source"] == "s.docx"
        assert out["file_type"] == "docx"

    def test_empty_elements_empty_summary(self):
        out = ExtractionResult(source="s", file_type="pdf").to_dict()
        assert out["summary"] == {}
        assert out["elements"] == []
        assert out["metadata"] == {}

    def test_element_order_preserved(self):
        res = ExtractionResult(source="s", file_type="docx")
        res.elements = [TextElement("first"), TableElement([["t"]]), TextElement("last")]
        types = [e["type"] for e in res.to_dict()["elements"]]
        assert types == ["text", "table", "text"]

    def test_metadata_passthrough(self):
        res = ExtractionResult(source="s", file_type="docx", metadata={"author": "me"})
        assert res.to_dict()["metadata"] == {"author": "me"}
