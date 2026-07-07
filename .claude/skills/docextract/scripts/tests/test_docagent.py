"""docagent データ操作 API のユニットテスト。

フィクスチャ (result.json 相当) はテスト内で生成するため、docextract の実行も
ネットワークも不要。一時ディレクトリ上のストアに対して操作する。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docagent import Library, DocAgentError
from docagent import cli
from docagent.store import PACKAGED_DOCTYPES, default_doctypes

DEFAULT_DOCTYPES = default_doctypes()


def _fixture_id(source: str) -> str:
    """テスト用の読みやすい ID。実運用では docextract がパスハッシュ入り ID を
    result.json に書き込むが、docagent はその値をそのまま使うだけなので、
    ここでは可読性優先で ``<stem>_<ext>`` を採用する。"""
    p = Path(source)
    stem = p.stem or p.name
    ext = p.suffix.lstrip(".").lower()
    base = f"{stem}_{ext}" if ext else stem
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in base)


def make_result(source: str, texts, tables=None, ocr=None) -> dict:
    elements = []
    for i, t in enumerate(texts, 1):
        elements.append({"type": "text", "content": t, "style": "Normal", "location": {"order": i}})
    for tb in tables or []:
        elements.append({"type": "table", "n_rows": len(tb), "n_cols": len(tb[0]), "rows": tb, "location": {}})
    for o in ocr or []:
        elements.append({"type": "image", "file": "images/x.png", "ocr_text": o, "location": {}})
    summary = {"text": len(texts)}
    if tables:
        summary["table"] = len(tables)
    if ocr:
        summary["image"] = len(ocr)
    return {
        "id": _fixture_id(source),
        "source": source,
        "source_abspath": f"/fixtures/{source}",
        "content_hash": "0" * 64,
        "file_type": Path(source).suffix.lstrip(".").lower(),
        "metadata": {"title": None, "author": "tester"},
        "summary": summary,
        "elements": elements,
    }


class DocAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / "store" / "library.json"
        self.dts = self.root / "store" / "doctypes.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_result(self, name: str, **kw) -> Path:
        p = self.root / f"{name}_result.json"
        p.write_text(json.dumps(make_result(name, **kw), ensure_ascii=False), encoding="utf-8")
        return p

    def _lib(self) -> Library:
        return Library.load(self.store, self.dts)

    # ── 取り込み ──
    def test_add_uses_id_from_result(self):
        rp = self._write_result("report.docx", texts=["月次売上の報告です。"])
        lib = self._lib()
        entry = lib.add_from_result(rp)
        lib.save()
        # ID は result.json の id をそのまま採用する (再計算しない)
        self.assertEqual(entry["id"], "report_docx")
        self.assertEqual(entry["source_abspath"], "/fixtures/report.docx")
        self.assertEqual(entry["file_type"], "docx")
        self.assertIsNone(entry["doctype"])  # 文書種別は未設定で登録
        self.assertIn("月次売上", entry["preview"])
        self.assertTrue(self.store.exists())

    def test_add_rejects_result_without_id(self):
        rp = self.root / "legacy_result.json"
        payload = make_result("legacy.docx", texts=["x"])
        del payload["id"]
        rp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        lib = self._lib()
        with self.assertRaises(DocAgentError) as cm:
            lib.add_from_result(rp)
        self.assertIn("id", str(cm.exception))

    def test_add_duplicate_requires_overwrite(self):
        rp = self._write_result("report.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        with self.assertRaises(DocAgentError):
            lib.add_from_result(rp)
        # overwrite は既存の文書種別を保持する
        lib.set_doctype("report_docx", "基本設計")
        entry = lib.add_from_result(rp, overwrite=True)
        self.assertEqual(entry["doctype"], "基本設計")

    # ── 文書種別の設定 ──
    def test_set_doctype(self):
        rp = self._write_result("report.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        self.assertIsNone(lib.get("report_docx")["doctype"])
        lib.set_doctype("report_docx", "議事録")
        self.assertEqual(lib.get("report_docx")["doctype"], "議事録")

    def test_reject_unknown_doctype(self):
        rp = self._write_result("report.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        with self.assertRaises(DocAgentError):
            lib.set_doctype("report_docx", "存在しない種別")
        # force で任意種別を許可
        lib.set_doctype("report_docx", "臨時種別", force=True)
        self.assertEqual(lib.get("report_docx")["doctype"], "臨時種別")

    # ── 文書種別名の表記揺れ吸収 ──
    def test_set_doctype_normalizes_variants(self):
        rp = self._write_result("a.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        # 囲み記号・前後空白を剥がす
        lib.set_doctype("a_docx", " 『議事録』 ")
        self.assertEqual(lib.get("a_docx")["doctype"], "議事録")
        # 全角スラッシュ・区切り揺れ (NFKC + ゆるい一致)
        lib.set_doctype("a_docx", "画面／帳票")
        self.assertEqual(lib.get("a_docx")["doctype"], "画面・帳票")
        # 一意な前方一致 (途中まで)
        lib.set_doctype("a_docx", "基本")
        self.assertEqual(lib.get("a_docx")["doctype"], "基本設計")

    def test_set_doctype_still_rejects_far_input(self):
        rp = self._write_result("a.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        with self.assertRaises(DocAgentError):
            lib.set_doctype("a_docx", "全く無関係な名称です")
        # force なら正規化のみで任意種別を許可 (囲みは剥がす)
        lib.set_doctype("a_docx", "『臨時種別』", force=True)
        self.assertEqual(lib.get("a_docx")["doctype"], "臨時種別")

    # ── 参照系 ──
    def test_query_and_stats(self):
        lib = self._lib()
        lib.add_from_result(self._write_result("a.docx", texts=["契約条項について。"]))
        lib.add_from_result(self._write_result("b.pdf", texts=["会議の決定事項。"]))
        lib.set_doctype("a_docx", "要件定義")
        lib.set_doctype("b_pdf", "議事録")
        lib.save()

        fresh = self._lib()
        self.assertEqual(len(fresh.query(doctype="要件定義")), 1)
        self.assertEqual(len(fresh.query(text="契約条項")), 1)  # preview へのマッチ
        stats = fresh.stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["by_doctype"]["要件定義"], 1)
        self.assertEqual(stats["by_doctype"]["議事録"], 1)

    def test_stats_counts_unclassified(self):
        lib = self._lib()
        lib.add_from_result(self._write_result("a.docx", texts=["x"]))
        self.assertEqual(lib.stats()["by_doctype"]["未分類"], 1)

    def test_remove(self):
        lib = self._lib()
        lib.add_from_result(self._write_result("a.docx", texts=["x"]))
        lib.remove("a_docx")
        self.assertEqual(len(lib.documents), 0)
        with self.assertRaises(DocAgentError):
            lib.get("a_docx")

    def test_get_missing_raises(self):
        with self.assertRaises(DocAgentError):
            self._lib().get("nope")

    # ── 文書種別の定義 ──
    def test_doctypes_from_file(self):
        self.dts.parent.mkdir(parents=True, exist_ok=True)
        self.dts.write_text(json.dumps({"doctypes": ["X", "Y"]}, ensure_ascii=False), encoding="utf-8")
        lib = self._lib()
        self.assertEqual(lib.doctypes, ["X", "Y"])
        lib.add_from_result(self._write_result("a.docx", texts=["x"]))
        lib.set_doctype("a_docx", "X")  # ファイル定義の種別は通る
        with self.assertRaises(DocAgentError):
            lib.set_doctype("a_docx", "要件定義")  # 既定でもファイルに無ければ拒否

    def test_default_doctypes_when_no_file(self):
        lib = self._lib()
        self.assertEqual(lib.doctypes, DEFAULT_DOCTYPES)

    def test_default_doctypes_come_from_packaged_json(self):
        # 既定の文書種別はコードではなくパッケージ同梱の doctypes.json が定義元
        data = json.loads(PACKAGED_DOCTYPES.read_text(encoding="utf-8-sig"))
        self.assertEqual(DEFAULT_DOCTYPES, data["doctypes"])
        self.assertIn("その他", DEFAULT_DOCTYPES)

    # ── preview は上限で切られる ──
    def test_preview_truncated(self):
        long_text = "あ" * 2000
        rp = self._write_result("big.docx", texts=[long_text])
        lib = self._lib()
        entry = lib.add_from_result(rp)
        self.assertLessEqual(len(entry["preview"]), 600)

    # ── text (本文テキストのみの軽量ビュー) ──
    def test_extract_text(self):
        rp = self._write_result(
            "mix.docx",
            texts=["第一段落。", "第二段落。"],
            tables=[[["品名", "数量"], ["りんご", "3"]]],
            ocr=["画像内の文字"],
        )
        lib = self._lib()
        lib.add_from_result(rp)
        out = lib.extract_text("mix_docx")
        self.assertEqual(out["id"], "mix_docx")
        self.assertFalse(out["truncated"])
        self.assertIn("第一段落。", out["text"])
        self.assertIn("品名 | 数量", out["text"])
        self.assertIn("りんご | 3", out["text"])
        self.assertIn("[画像OCR] 画像内の文字", out["text"])
        self.assertNotIn("location", out["text"])  # レイアウト情報は落ちる

    def test_extract_text_max_chars(self):
        rp = self._write_result("big.docx", texts=["あ" * 2000])
        lib = self._lib()
        lib.add_from_result(rp)
        out = lib.extract_text("big_docx", max_chars=100)
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["text"]), 100)
        self.assertEqual(out["total_chars"], 2000)

    def test_extract_text_missing_result(self):
        rp = self._write_result("gone.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        rp.unlink()
        with self.assertRaises(DocAgentError):
            lib.extract_text("gone_docx")

    # ── prep (取り込み準備の複合操作) ──
    def test_prep_registers_from_path(self):
        rp = self._write_result("report.docx", texts=["月次売上の報告です。"])
        lib = self._lib()
        out = lib.prep(str(rp))
        self.assertEqual(out["id"], "report_docx")
        self.assertIsNone(out["doctype"])
        self.assertFalse(out["already_classified"])
        self.assertEqual(out["doctypes"], DEFAULT_DOCTYPES)
        self.assertIn("月次売上", out["text"])
        self.assertIn("set-doctype report_docx", out["next_action"])
        self.assertTrue(self.store.exists())  # 登録時はストアも保存される

    def test_prep_by_id_preserves_doctype(self):
        rp = self._write_result("report.docx", texts=["x"])
        lib = self._lib()
        lib.add_from_result(rp)
        lib.set_doctype("report_docx", "基本設計")
        lib.save()

        out = self._lib().prep("report_docx")
        self.assertTrue(out["already_classified"])
        self.assertEqual(out["doctype"], "基本設計")

        # パスで再 prep しても文書種別は保持される
        out2 = self._lib().prep(str(rp))
        self.assertTrue(out2["already_classified"])
        self.assertEqual(out2["doctype"], "基本設計")

    def test_prep_max_chars(self):
        rp = self._write_result("big.docx", texts=["あ" * 2000])
        out = self._lib().prep(str(rp), max_chars=100)
        self.assertTrue(out["text_truncated"])
        self.assertEqual(len(out["text"]), 100)

    def test_prep_unknown_target_raises(self):
        with self.assertRaises(DocAgentError):
            self._lib().prep("nope")

    def test_prep_missing_result_falls_back_to_preview(self):
        rp = self._write_result("gone.docx", texts=["中身のテキスト"])
        lib = self._lib()
        lib.add_from_result(rp)
        lib.save()
        rp.unlink()
        out = self._lib().prep("gone_docx")
        self.assertIsNone(out["text"])
        self.assertIn("中身のテキスト", out["preview"])

    # ── 取り込みガード (元ファイルの直接渡し・壊れた JSON を弾く) ──
    def test_add_rejects_raw_office_file(self):
        raw = self.root / "売上.xlsx"
        raw.write_bytes(b"PK\x03\x04not-a-real-xlsx")
        lib = self._lib()
        with self.assertRaises(DocAgentError) as cm:
            lib.add_from_result(raw)
        self.assertIn("docextract", str(cm.exception))

    def test_prep_rejects_raw_office_file(self):
        raw = self.root / "資料.pdf"
        raw.write_bytes(b"%PDF-1.7 binary")
        with self.assertRaises(DocAgentError) as cm:
            self._lib().prep(str(raw))
        self.assertIn("docextract", str(cm.exception))

    def test_add_rejects_invalid_json(self):
        broken = self.root / "broken_result.json"
        broken.write_text("{ this is not valid json", encoding="utf-8")
        lib = self._lib()
        with self.assertRaises(DocAgentError):
            lib.add_from_result(broken)

    def test_add_rejects_json_without_elements(self):
        wrong = self.root / "wrong_result.json"
        wrong.write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        lib = self._lib()
        with self.assertRaises(DocAgentError) as cm:
            lib.add_from_result(wrong)
        self.assertIn("elements", str(cm.exception))

    # ── CLI: set 系の自動登録 (前段 prep/add のスキップを補完) ──
    def test_resolve_target_auto_registers_path(self):
        rp = self._write_result("c.docx", texts=["中身"])
        lib = self._lib()
        doc_id, auto = cli._resolve_target(lib, str(rp))
        self.assertEqual(doc_id, "c_docx")
        self.assertTrue(auto)
        self.assertIsNotNone(lib.find("c_docx"))
        # 2 回目は登録済みなので自動登録しない
        doc_id2, auto2 = cli._resolve_target(lib, "c_docx")
        self.assertEqual((doc_id2, auto2), ("c_docx", False))

    def test_resolve_target_unregistered_id_passthrough(self):
        lib = self._lib()
        self.assertEqual(cli._resolve_target(lib, "nope"), ("nope", False))

    # ── 集約 export ──
    def test_export_shape(self):
        lib = self._lib()
        lib.add_from_result(self._write_result("a.docx", texts=["x"]))
        data = lib.export()
        self.assertEqual(set(data), {"version", "doctypes", "documents"})
        self.assertEqual(len(data["documents"]), 1)


if __name__ == "__main__":
    unittest.main()
