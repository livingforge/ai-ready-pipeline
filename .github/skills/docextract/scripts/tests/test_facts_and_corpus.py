"""FactStore (仕様・要件ファクト) と Library の sync / search のユニットテスト。

新設エージェント (doc-indexer / spec-extractor / doc-qa) が依存する
データ操作を、docextract の実行やネットワークなしで検証する。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docagent import (
    FactStore,
    Library,
    DocAgentError,
    default_item_types,
    default_rel_types,
)

ITEM_TYPES = default_item_types()


def _result(doc_id: str, source: str, elements: list) -> dict:
    return {
        "id": doc_id,
        "source": source,
        "source_abspath": f"/fixtures/{source}",
        "content_hash": "0" * 64,
        "file_type": Path(source).suffix.lstrip(".").lower(),
        "metadata": {},
        "summary": {},
        "elements": elements,
    }


class FactStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.facts = self.root / "store" / "facts.json"
        self.item_types = self.root / "store" / "item_types.json"

    def _fs(self) -> FactStore:
        return FactStore.load(self.facts, self.item_types)

    def test_add_assigns_sequential_ids_and_persists(self):
        fs = self._fs()
        a = fs.add("doc_a", "機能要件", "ユーザはCSV出力できる", evidence="CSV可", location={"page": 3})
        b = fs.add("doc_a", "データ項目", "顧客コードは8桁")
        fs.save()
        self.assertEqual(a["id"], "f0001")
        self.assertEqual(b["id"], "f0002")
        self.assertEqual(a["location"], {"page": 3})
        self.assertEqual(a["evidence"], "CSV可")
        # 再読込しても連番は継続する
        c = self._fs().add("doc_b", "用語", "SKU: 在庫管理単位")
        self.assertEqual(c["id"], "f0003")

    def test_type_normalized_and_unknown_rejected(self):
        fs = self._fs()
        # 表記揺れ (囲み記号) は吸収される
        item = fs.add("d", "「機能要件」", "何か")
        self.assertEqual(item["type"], "機能要件")
        with self.assertRaises(DocAgentError):
            fs.add("d", "存在しない種別", "何か")
        # force なら任意種別を許可
        forced = fs.add("d", "独自種別", "何か", force=True)
        self.assertEqual(forced["type"], "独自種別")

    def test_required_fields(self):
        fs = self._fs()
        with self.assertRaises(DocAgentError):
            fs.add("", "機能要件", "本文")  # doc_id 必須
        with self.assertRaises(DocAgentError):
            fs.add("d", "機能要件", "   ")  # statement 必須
        # keywords / confidence は廃止済み — アイテムに含まれない
        item = fs.add("d", "機能要件", "本文")
        self.assertNotIn("keywords", item)
        self.assertNotIn("confidence", item)

    def test_query_and_stats(self):
        fs = self._fs()
        fs.add("doc_a", "機能要件", "CSV出力")
        fs.add("doc_a", "非機能要件", "レスポンス3秒以内")
        fs.add("doc_b", "機能要件", "PDF出力")
        self.assertEqual(len(fs.query(doc_id="doc_a")), 2)
        self.assertEqual(len(fs.query(type="機能要件")), 2)
        self.assertEqual(len(fs.query(text="csv")), 1)  # 大文字小文字を無視
        s = fs.stats()
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["by_type"]["機能要件"], 2)
        self.assertEqual(s["by_doc"]["doc_a"], 2)

    def test_remove_and_export(self):
        fs = self._fs()
        fs.add("d", "機能要件", "A")
        fs.add("d", "機能要件", "B")
        fs.remove("f0001")
        self.assertEqual(len(fs.items), 1)
        data = fs.export()
        self.assertEqual(set(data), {"version", "item_types", "rel_types", "items"})
        with self.assertRaises(DocAgentError):
            fs.remove("f0999")  # 不明な ID

    def test_item_types_editable_and_file_wins(self):
        # store/item_types.json があればそれが正になる
        self.item_types.parent.mkdir(parents=True, exist_ok=True)
        self.item_types.write_text(json.dumps({"item_types": ["X", "Y"]}), encoding="utf-8")
        fs = self._fs()
        self.assertEqual(fs.item_types, ["X", "Y"])
        fs.add("d", "X", "本文")  # 定義内は通る
        with self.assertRaises(DocAgentError):
            fs.add("d", "機能要件", "本文")  # 既定でもファイルに無ければ拒否

    def test_default_item_types_from_packaged_json(self):
        self.assertIn("機能要件", ITEM_TYPES)
        self.assertIn("用語", ITEM_TYPES)

    # ── refs (ファクト間参照 / 工程間トレース) ──────────────────
    def test_refs_added_validated_and_persisted(self):
        fs = self._fs()
        item = fs.add(
            "doc_a", "メソッド", "register()は予約を登録する",
            refs=[
                {"rel": "realizes", "to_ref": "F-02"},
                {"rel": "refines", "to_ref": "SCR-03", "note": "画面遷移元"},
            ],
        )
        self.assertEqual(
            item["refs"],
            [
                {"rel": "realizes", "to_ref": "F-02"},
                {"rel": "refines", "to_ref": "SCR-03", "note": "画面遷移元"},
            ],
        )
        fs.save()
        # 再読込しても refs が残る
        reloaded = self._fs()
        self.assertEqual(reloaded.items[0]["refs"], item["refs"])

    def test_refs_default_empty_list(self):
        fs = self._fs()
        item = fs.add("d", "機能要件", "本文")
        self.assertEqual(item["refs"], [])

    def test_ref_rel_normalized_and_unknown_rejected(self):
        fs = self._fs()
        # 表記揺れ (前後空白) は関係種別タクソノミーへ寄せる
        item = fs.add("d", "メソッド", "本文", refs=[{"rel": " realizes ", "to_ref": "F-1"}])
        self.assertEqual(item["refs"][0]["rel"], "realizes")
        # 未知の関係種別は拒否
        with self.assertRaises(DocAgentError):
            fs.add("d", "メソッド", "本文", refs=[{"rel": "bogus", "to_ref": "F-1"}])
        # force なら任意の関係種別を許可
        forced = fs.add("d", "メソッド", "本文", refs=[{"rel": "画面遷移", "to_ref": "SCR-9"}], force=True)
        self.assertEqual(forced["refs"][0]["rel"], "画面遷移")

    def test_ref_requires_rel_and_to_ref(self):
        fs = self._fs()
        with self.assertRaises(DocAgentError):
            fs.add("d", "メソッド", "本文", refs=[{"rel": "realizes"}])  # to_ref 欠落
        with self.assertRaises(DocAgentError):
            fs.add("d", "メソッド", "本文", refs=[{"to_ref": "F-1"}])  # rel 欠落

    def test_refs_are_searchable(self):
        fs = self._fs()
        fs.add("d", "メソッド", "処理", refs=[{"rel": "realizes", "to_ref": "F-42"}])
        self.assertEqual(len(fs.query(text="F-42")), 1)

    def test_rel_types_editable_and_file_wins(self):
        rel_types = self.root / "store" / "rel_types.json"
        rel_types.parent.mkdir(parents=True, exist_ok=True)
        rel_types.write_text(json.dumps({"rel_types": ["links"]}), encoding="utf-8")
        fs = FactStore.load(self.facts, self.item_types, rel_types)
        self.assertEqual(fs.rel_types, ["links"])
        fs.add("d", "機能要件", "本文", refs=[{"rel": "links", "to_ref": "X"}])  # 定義内は通る
        with self.assertRaises(DocAgentError):
            fs.add("d", "機能要件", "本文", refs=[{"rel": "realizes", "to_ref": "X"}])

    def test_default_rel_types_from_packaged_json(self):
        self.assertIn("realizes", default_rel_types())
        self.assertIn("refines", default_rel_types())

    # ── merge (並列シャードの統合) ─────────────────────────────
    def _shard(self, name: str) -> FactStore:
        return FactStore.load(self.root / "shards" / f"{name}.json")

    def test_merge_renumbers_ids_and_preserves_fields(self):
        a = self._shard("a")
        a.add("doc1", "機能要件", "CSV出力できる")
        a.add("doc1", "メソッド", "register()", refs=[{"rel": "realizes", "to_ref": "F-01"}])
        a.save()
        b = self._shard("b")
        b.add("doc2", "データ項目", "顧客コードは8桁")
        b.save()

        fs = self._fs()
        result = fs.merge([a.path, b.path])
        fs.save()
        self.assertEqual(result["added"], 3)
        self.assertEqual(result["skipped"], 0)
        # ID は取り込み側で連番に振り直される（シャードの f0001 衝突を解消）
        self.assertEqual([it["id"] for it in fs.items], ["f0001", "f0002", "f0003"])
        # refs はそのまま保持
        method = next(it for it in fs.items if it["type"] == "メソッド")
        self.assertEqual(method["refs"], [{"rel": "realizes", "to_ref": "F-01"}])

    def test_merge_skips_exact_duplicates(self):
        a = self._shard("a")
        a.add("doc1", "機能要件", "同一の事実")
        a.save()
        b = self._shard("b")
        b.add("doc1", "機能要件", "同一の事実")  # doc+type+statement 一致
        b.add("doc2", "機能要件", "別の事実")
        b.save()

        fs = self._fs()
        r1 = fs.merge([a.path])
        r2 = fs.merge([b.path])   # 再取り込みでも重複は増えない（冪等）
        self.assertEqual(r1["added"], 1)
        self.assertEqual(r2["added"], 1)      # 別の事実だけ増える
        self.assertEqual(r2["skipped"], 1)    # 同一の事実はスキップ
        self.assertEqual(len(fs.items), 2)

    def test_merge_unions_vocabularies(self):
        a = self._shard("a")
        a.add_item_type("独自種別")
        a.add_rel_type("独自関係")
        a.add("doc1", "独自種別", "x", refs=[{"rel": "独自関係", "to_ref": "Y"}], force=True)
        a.save()

        fs = self._fs()
        result = fs.merge([a.path])
        self.assertGreaterEqual(result["item_types_added"], 1)
        self.assertGreaterEqual(result["rel_types_added"], 1)
        self.assertIn("独自種別", fs.item_types)
        self.assertIn("独自関係", fs.rel_types)

    def test_merge_missing_shard_raises(self):
        fs = self._fs()
        with self.assertRaises(DocAgentError):
            fs.merge([self.root / "shards" / "nope.json"])


class SyncTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.out = self.root / "output"
        self.store = self.root / "store" / "library.json"
        self.cats = self.root / "store" / "categories.json"

    def _write_result(self, doc_id: str, source: str, elements: list) -> Path:
        d = self.out / doc_id
        d.mkdir(parents=True, exist_ok=True)
        rp = d / "result.json"
        rp.write_text(json.dumps(_result(doc_id, source, elements), ensure_ascii=False), encoding="utf-8")
        return rp

    def _manifest(self, entries: dict) -> Path:
        mp = self.out / "index.json"
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps({"version": 1, "documents": entries}, ensure_ascii=False), encoding="utf-8")
        return mp

    def _lib(self) -> Library:
        return Library.load(self.store, self.cats)

    def test_sync_registers_all_and_reports(self):
        r1 = self._write_result("a_docx", "a.docx", [{"type": "text", "content": "本文A", "location": {"order": 1}}])
        r2 = self._write_result("b_pdf", "b.pdf", [{"type": "text", "content": "本文B", "location": {"page": 1}}])
        mp = self._manifest(
            {
                "a_docx": {"result_path": str(r1).replace("\\", "/")},
                "b_pdf": {"result_path": str(r2).replace("\\", "/")},
                "gone_docx": {"result_path": str(self.out / "gone_docx" / "result.json").replace("\\", "/")},
            }
        )
        lib = self._lib()
        report = lib.sync_from_manifest(mp)
        lib.save()
        self.assertEqual(sorted(report["added"]), ["a_docx", "b_pdf"])
        self.assertEqual(report["skipped"], ["gone_docx"])  # result.json が無い
        self.assertEqual(len(self._lib().documents), 2)

    def test_sync_preserves_doctype_and_marks_updated(self):
        r1 = self._write_result("a_docx", "a.docx", [{"type": "text", "content": "本文A", "location": {"order": 1}}])
        mp = self._manifest({"a_docx": {"result_path": str(r1).replace("\\", "/")}})
        lib = self._lib()
        lib.sync_from_manifest(mp)
        lib.set_doctype("a_docx", "議事録")
        lib.save()
        # 2 回目の sync では文書種別を保持し、updated として数える
        lib2 = self._lib()
        report = lib2.sync_from_manifest(mp)
        self.assertEqual(report["updated"], ["a_docx"])
        self.assertEqual(lib2.get("a_docx")["doctype"], "議事録")

    def test_sync_missing_manifest_raises(self):
        with self.assertRaises(DocAgentError):
            self._lib().sync_from_manifest(self.out / "nope.json")


class SearchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.out = self.root / "output"
        self.store = self.root / "store" / "library.json"
        self.cats = self.root / "store" / "categories.json"

    def _register(self, doc_id: str, source: str, elements: list) -> None:
        d = self.out / doc_id
        d.mkdir(parents=True, exist_ok=True)
        rp = d / "result.json"
        rp.write_text(json.dumps(_result(doc_id, source, elements), ensure_ascii=False), encoding="utf-8")
        lib = Library.load(self.store, self.cats)
        lib.add_from_result(rp)
        lib.save()

    def _lib(self) -> Library:
        return Library.load(self.store, self.cats)

    def test_search_returns_hits_with_location(self):
        self._register(
            "a_docx",
            "a.docx",
            [
                {"type": "text", "content": "月次売上はCSVで出力できる", "location": {"order": 2}},
                {"type": "table", "rows": [["項目", "値"], ["顧客コード", "8桁"]], "location": {"order": 3}},
                {"type": "image", "file": "images/x.png", "ocr_text": "図: 権限は3種類", "location": {"order": 4}},
            ],
        )
        lib = self._lib()
        # text 要素へのヒット (location と種別を保つ)
        hits = lib.search("CSV")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["doc_id"], "a_docx")
        self.assertEqual(hits[0]["location"], {"order": 2})
        self.assertEqual(hits[0]["kind"], "text")
        self.assertIn("CSV", hits[0]["snippet"])
        # 表セルへのヒット
        self.assertEqual(lib.search("顧客コード")[0]["kind"], "table")
        # 画像 OCR へのヒット
        self.assertEqual(lib.search("権限")[0]["kind"], "image_ocr")

    def test_search_doc_filter_and_case_insensitive(self):
        self._register("a_docx", "a.docx", [{"type": "text", "content": "Alpha 機能", "location": {"order": 1}}])
        self._register("b_pdf", "b.pdf", [{"type": "text", "content": "Alpha 画面", "location": {"page": 1}}])
        lib = self._lib()
        self.assertEqual(len(lib.search("alpha")), 2)  # 大文字小文字を無視
        self.assertEqual(len(lib.search("alpha", doc_id="b_pdf")), 1)

    def test_search_empty_term_and_max_hits(self):
        self._register(
            "a_docx",
            "a.docx",
            [{"type": "text", "content": f"行{i} キーワード", "location": {"order": i}} for i in range(10)],
        )
        lib = self._lib()
        self.assertEqual(lib.search(""), [])
        self.assertEqual(len(lib.search("キーワード", max_hits=3)), 3)

    def test_search_normalizes_width_and_whitespace(self):
        self._register(
            "a_docx",
            "a.docx",
            [{"type": "text", "content": "ＣＳＶ出力\nの条件を定める", "location": {"order": 1}}],
        )
        lib = self._lib()
        self.assertEqual(len(lib.search("csv")), 1)  # 全角英字 ↔ 半角
        self.assertEqual(len(lib.search("出力の条件")), 1)  # 要素内の改行をまたぐ一致
        self.assertIn("ＣＳＶ出力", lib.search("csv")[0]["snippet"])  # snippet は原文のまま

    def test_search_multi_keyword_is_and(self):
        self._register(
            "a_docx",
            "a.docx",
            [
                {"type": "text", "content": "ユーザの権限は3種類ある", "location": {"order": 1}},
                {"type": "text", "content": "ユーザ一覧画面の仕様", "location": {"order": 2}},
            ],
        )
        hits = self._lib().search("ユーザ 権限")
        self.assertEqual(len(hits), 1)  # 両語を含む要素だけ
        self.assertEqual(hits[0]["location"], {"order": 1})

    def test_search_ranks_by_relevance_not_registration_order(self):
        # 先に登録した文書の弱いヒットが、後の文書の強いヒットを押し出さないこと
        # (旧実装は登録順の先着 max_hits 件で打ち切っていた)。
        self._register(
            "a_docx",
            "a.docx",
            [{"type": "text", "content": f"承認 その{i}", "location": {"order": i}} for i in range(5)],
        )
        self._register(
            "b_pdf",
            "b.pdf",
            [{"type": "text", "content": "承認フロー: 承認は2段階。最終承認は部長。", "location": {"page": 1}}],
        )
        hits = self._lib().search("承認", max_hits=3)
        self.assertEqual(hits[0]["doc_id"], "b_pdf")  # 一致回数の多い要素が先頭
        self.assertEqual(len(hits), 3)

    def test_search_phrase_match_ranks_first(self):
        self._register(
            "a_docx",
            "a.docx",
            [
                {"type": "text", "content": "権限は管理者が各ユーザに付与する", "location": {"order": 1}},
                {"type": "text", "content": "ユーザ権限の一覧を示す", "location": {"order": 2}},
            ],
        )
        hits = self._lib().search("ユーザ 権限")
        self.assertEqual(len(hits), 2)  # AND はどちらも満たす
        self.assertEqual(hits[0]["location"], {"order": 2})  # 連続一致 (フレーズ) が上位


if __name__ == "__main__":
    unittest.main()
