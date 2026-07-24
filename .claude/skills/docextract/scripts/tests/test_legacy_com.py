"""旧 Office バイナリ形式 (.xls/.doc/.ppt) の COM 抽出器を検証する。

この経路は Microsoft Office (COM 自動化) と pywin32 を必須とする外部前提を持つ。
CI/サンドボックスには通常 Office も pywin32 も無いため、ここでは主に
**fail-closed 経路** (Office/pywin32 が無いとき「Office が必要」である旨を含む
明確なエラーで停止する) を検証する。実際の変換 (Office ありでの成功系) は
外部環境依存のため未評価 (docs/coverage.md の未評価サーフェス参照)。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import docextract
from docextract.extractors import OfficeUnavailableError, legacy_com
from docextract.extractors.base import ImageSaver


LEGACY_EXTS = [".xls", ".doc", ".ppt"]
APP_BY_EXT = {".xls": "Excel", ".doc": "Word", ".ppt": "PowerPoint"}


@pytest.fixture
def _no_pywin32(monkeypatch):
    """pywin32 が導入済みの環境でも「不在」を再現する。

    fail-closed 経路 (pywin32 が無いとき明確なエラーで止まる) の検証が、実行環境に
    pywin32 が入っているかどうかに左右されないようにする。``sys.modules`` に None を
    差し込むと ``import pythoncom`` / ``import win32com.client`` が ImportError になり、
    ``_require_win32com`` の未導入経路を決定論的に踏める。
    """
    for name in ("pythoncom", "win32com", "win32com.client"):
        monkeypatch.setitem(sys.modules, name, None)


def test_legacy_formats_are_registered():
    for ext in LEGACY_EXTS:
        assert ext in docextract.SUPPORTED_EXTENSIONS
        assert ext in docextract.available_extractors()


@pytest.mark.parametrize("ext", LEGACY_EXTS)
def test_fail_closed_message_names_office_and_pywin32(ext, tmp_path, _no_pywin32):
    """pywin32 不在時、Office 必須である旨と回避策を含むエラーで停止する。"""
    extractor = docextract.available_extractors()[ext]
    with pytest.raises(OfficeUnavailableError) as ei:
        extractor(Path("dummy" + ext), ImageSaver(tmp_path))
    msg = str(ei.value)
    assert "Office" in msg
    assert APP_BY_EXT[ext] in msg  # どの Office アプリが必要かを示す
    assert "pywin32" in msg  # 導入手段を示す
    assert ext in msg  # どの形式についての失敗かを示す
    # 利用できない環境向けの回避策 (新形式へ変換) を案内している
    assert ".docx" in msg or ".xlsx" in msg or ".pptx" in msg


@pytest.mark.parametrize("ext", LEGACY_EXTS)
def test_office_unavailable_is_runtime_error(ext, tmp_path, _no_pywin32):
    """OfficeUnavailableError は RuntimeError の一種 (CLI の失敗捕捉で拾える)。"""
    assert issubclass(OfficeUnavailableError, RuntimeError)
    extractor = docextract.available_extractors()[ext]
    with pytest.raises(RuntimeError):
        extractor(Path("dummy" + ext), ImageSaver(tmp_path))


def test_import_failure_message_names_install_command(tmp_path, _no_pywin32):
    """pywin32 不在時のメッセージは自動導入されない旨と実コマンドを案内する。"""
    extractor = docextract.available_extractors()[".xls"]
    with pytest.raises(OfficeUnavailableError) as ei:
        extractor(Path("dummy.xls"), ImageSaver(tmp_path))
    msg = str(ei.value)
    # 失敗ログを読んで手で調べ直さずに済むよう具体コマンドを出す
    assert "pip install" in msg and "pywin32" in msg
    assert "自動導入されない" in msg


def test_convert_failure_reports_office_detected_not_missing(monkeypatch, tmp_path):
    """COM 変換自体の失敗は「Office/pywin32 は検出済み」として切り分けて報告する。

    pywin32 が import できる前提に置き換えたうえで COM 変換が例外を投げる状況を
    模し、生の COM 例外ではなく OfficeUnavailableError になること、かつ
    「未導入」ではなく「検出済み」の文言で報告されることを見る (Excel 未導入への
    誤診を防ぐ)。
    """
    monkeypatch.setattr(legacy_com, "_require_win32com", lambda ext, app, action: None)

    def _boom(src, dst):
        raise OSError("COM サーバに接続できません")  # Open/SaveAs の失敗を模す

    monkeypatch.setitem(legacy_com._APP_SPEC["Excel"], "convert", _boom)
    extractor = docextract.available_extractors()[".xls"]
    with pytest.raises(OfficeUnavailableError) as ei:
        extractor(Path("dummy.xls"), ImageSaver(tmp_path))
    msg = str(ei.value)
    assert "COM サーバに接続できません" in msg  # 原因を握り潰さない
    assert "検出済み" in msg  # 未導入ではないと切り分けている
    # 変換失敗経路では「pywin32 を入れろ」の誤誘導をしない
    assert "自動導入されない" not in msg


def test_disable_irm_sets_permission_and_swallows_errors():
    """_disable_irm は Permission.Enabled=False を立て、非対応でも例外にしない。"""

    class _Perm:
        Enabled = True

    class _Doc:
        Permission = _Perm()

    doc = _Doc()
    legacy_com._disable_irm(doc)
    assert doc.Permission.Enabled is False

    class _NoPerm:
        @property
        def Permission(self):  # 非 IRM 文書 / 非対応を模す
            raise AttributeError("Permission not supported")

    legacy_com._disable_irm(_NoPerm())  # no-op として例外を出さない


def test_successful_conversion_delegates_and_relabels(monkeypatch, tmp_path):
    """変換成功時は既存抽出器へ委譲し、下流には旧形式のラベルを見せる。

    Office 無しでも通せるよう、変換を「.xls を .xlsx コピーにする」ダミーに
    差し替え、既存の extract_xlsx へ実際に委譲されることを確認する。
    """
    monkeypatch.setattr(legacy_com, "_require_win32com", lambda ext, app, action: None)

    # 本物の .xlsx を作り、変換ダミーがそれをコピーするようにする
    from openpyxl import Workbook

    real_xlsx = tmp_path / "seed.xlsx"
    wb = Workbook()
    ws = wb.active
    ws["A1"] = "旧Excel"
    wb.save(real_xlsx)

    def _fake_convert(src, dst):
        import shutil

        shutil.copyfile(real_xlsx, dst)

    monkeypatch.setitem(legacy_com._APP_SPEC["Excel"], "convert", _fake_convert)
    extractor = docextract.available_extractors()[".xls"]

    result = extractor(Path("legacy.xls"), ImageSaver(tmp_path / "out"))
    # 下流には旧形式 (.xls) を見せる (中間の xlsx ではない)
    assert result.file_type == "xls"
    assert result.source == "legacy.xls"
    assert "converted_via" in result.metadata
    # 委譲先 (extract_xlsx) が実際に中身を読んでいる
    # (孤立した単一セルはテキスト要素として出る)
    texts = [
        getattr(el, "content", None) for el in result.elements
    ] + [
        cell
        for el in result.elements
        if getattr(el, "rows", None)
        for row in el.rows
        for cell in row
    ]
    assert "旧Excel" in texts
