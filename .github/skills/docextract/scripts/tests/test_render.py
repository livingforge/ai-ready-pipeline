"""render.py (Excel COM で範囲を PNG 化) のオーケストレーションを検証する。

Excel を実起動せず、_acquire シームにフェイク COM グラフを注入して
「CopyPicture→Paste→(図形数で検証)→Export、失敗は再試行、最後に必ず後始末」
という流れだけを確かめる (legacy_com が convert を差し替えるのと同じ流儀)。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from docextract import render
from docextract.extractors.legacy_com import (
    OfficeUnavailableError,
    Win32ComUnavailableError,
)


# ── フェイク COM グラフ ──────────────────────────────────────────
class _Fill:
    Visible = True


class _Format:
    def __init__(self):
        self.Fill = _Fill()


class _ChartArea:
    def __init__(self):
        self.Format = _Format()


class _Shapes(list):
    @property
    def Count(self):
        return len(self)


class _Chart:
    def __init__(self, paste_succeeds_on: int):
        # paste_succeeds_on: 何回目の Paste で図形が生えるか (0 = 永遠に空)
        self.ChartArea = _ChartArea()
        self.Shapes = _Shapes()
        self._paste_on = paste_succeeds_on
        self._attempt = 0
        self.exports: list[str] = []

    def Paste(self):
        self._attempt += 1
        if self._paste_on and self._attempt >= self._paste_on:
            self.Shapes.append(object())

    def Export(self, path, fmt):
        Path(path).write_bytes(b"\x89PNG\r\n fake")
        self.exports.append(path)


class _ChartObject:
    def __init__(self, chart):
        self.Chart = chart
        self.deleted = 0

    def Delete(self):
        self.deleted += 1


class _ChartObjects:
    def __init__(self, chart):
        self._chart = chart
        self.added: list[_ChartObject] = []

    def Add(self, left, top, width, height):
        co = _ChartObject(self._chart)
        self.added.append(co)
        return co


class _Range:
    def __init__(self):
        self.Width = 100.0
        self.Height = 50.0
        self.Left = 0.0
        self.Top = 0.0
        self.copies = 0

    def CopyPicture(self, appearance, fmt):
        self.copies += 1


class _Worksheet:
    def __init__(self, chart):
        self._chart = chart
        self._range = _Range()
        self._chart_objects = _ChartObjects(chart)

    def Range(self, a1):
        return self._range

    def ChartObjects(self):
        return self._chart_objects


class _Workbook:
    def __init__(self, ws):
        self._ws = ws
        self.closed = 0

    def Worksheets(self, name):
        return self._ws

    def Close(self, SaveChanges=True):
        self.closed += 1


class _Workbooks:
    def __init__(self, ws):
        self._ws = ws
        self.opened: list[str] = []

    def Open(self, path, ReadOnly=True):
        self.opened.append(path)
        return _Workbook(self._ws)


class _App:
    def __init__(self, ws):
        self.Workbooks = _Workbooks(ws)
        self.Visible = True
        self.DisplayAlerts = True
        self.quit = 0

    def Quit(self):
        self.quit += 1


class _PyC:
    def __init__(self):
        self.uninit = 0

    def PumpWaitingMessages(self):
        pass

    def CoUninitialize(self):
        self.uninit += 1


def _fake_acquire(chart):
    ws = _Worksheet(chart)
    app = _App(ws)
    pyc = _PyC()
    holder = {"app": app, "pyc": pyc, "ws": ws}

    def acquire():
        return pyc, app

    return acquire, holder


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # 再試行の待ちを潰してテストを速く保つ。
    monkeypatch.setattr(render.time, "sleep", lambda *_a, **_k: None)


# ── テスト ───────────────────────────────────────────────────────
def test_render_happy_path_exports_and_cleans_up(tmp_path):
    chart = _Chart(paste_succeeds_on=1)
    acquire, holder = _fake_acquire(chart)
    out = tmp_path / "sub" / "diagram.png"
    result = render.render_range_png(
        tmp_path / "book.xlsx", "S", "B2:L8", out, _acquire=acquire
    )
    assert Path(result) == out.resolve()
    assert out.exists()  # 親ディレクトリごと作られ、Export が書く
    assert holder["ws"]._range.copies == 1
    assert chart.exports == [str(out.resolve())]
    # 背景透明化を試みている (重なりでの空撮回避)
    assert chart.ChartArea.Format.Fill.Visible is False
    # 後始末: チャート破棄・ブック close・Quit・CoUninitialize
    assert holder["ws"]._chart_objects.added[0].deleted == 1
    assert holder["app"].quit == 1
    assert holder["pyc"].uninit == 1


def test_render_retries_until_paste_lands(tmp_path):
    # 3 回目の Paste で図形が生える → それまでは「空」で再試行する。
    chart = _Chart(paste_succeeds_on=3)
    acquire, holder = _fake_acquire(chart)
    out = tmp_path / "d.png"
    render.render_range_png(tmp_path / "b.xlsx", "S", "A1:C3", out, _acquire=acquire)
    assert holder["ws"]._range.copies == 3  # 空撮のたびに撮り直す
    assert out.exists()


def test_render_raises_when_paste_never_lands(tmp_path):
    # 何度試しても空 (クリップボード未反映) → 空 PNG を書かずにエラー。
    chart = _Chart(paste_succeeds_on=0)
    acquire, holder = _fake_acquire(chart)
    out = tmp_path / "never.png"
    with pytest.raises(OfficeUnavailableError):
        render.render_range_png(
            tmp_path / "b.xlsx", "S", "A1:C3", out, retries=3, _acquire=acquire
        )
    assert not out.exists()  # 空撮を成果物として残さない
    assert holder["app"].quit == 1  # 失敗しても後始末はする


def test_render_raises_when_win32com_unavailable(tmp_path, monkeypatch):
    # 既定経路 (_acquire 省略) は win32com が無ければ Win32ComUnavailableError。
    def _raise(ext, app, action):
        raise Win32ComUnavailableError("pywin32 が無い")

    monkeypatch.setattr(render, "_require_win32com", _raise)
    with pytest.raises(Win32ComUnavailableError):
        render.render_range_png(tmp_path / "b.xlsx", "S", "A1:C3", tmp_path / "o.png")
