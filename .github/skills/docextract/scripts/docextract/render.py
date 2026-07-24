"""Excel の指定セル範囲を PNG 画像にレンダリングする (Excel COM)。

ベクタ図形やセルの罫線・塗りで描かれた構成図は、テキスト化すると関係
(接続・向き・ゾーン) が失われる。fact 抽出が「本文から関係を確定できない」と
申告したブロックについて、その図面領域を**見た目そのまま画像化**して LLM の
視覚入力へ回すためのプリミティブ (render-then-see の render 部)。

範囲の特定は呼び出し側の責務: xlsx 抽出器が算出する図面領域 dbbox (全図形・
コネクタの外接矩形) や、セル領域の矩形を A1 レンジ (例 "B2:L8") にして渡す。
ここは「A1 レンジ → PNG」だけを担う。

Excel COM を使うため Windows + Microsoft Excel + pywin32 が前提。要件・エラー型は
legacy_com と共通 (使えない環境では「Office が必要」を含む明確なエラーで停止)。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .extractors.legacy_com import (
    OfficeUnavailableError,
    _com_conversion_error,
    _require_win32com,
)

# Excel enum: Range.CopyPicture(Appearance, Format)
_XL_SCREEN = 1  # xlScreen — 画面の見た目でコピー
_XL_BITMAP = 2  # xlBitmap — ラスタ (図形・罫線・塗りを見た目どおり取り込む)
_ACTION = "図面領域の画像化"


def _acquire_excel() -> tuple[object, object]:
    """Excel COM を初期化して (pythoncom, Excel.Application) を返す。

    テストが差し替えられるよう、実際の COM 取得はこの 1 関数に閉じ込める
    (legacy_com が convert を差し替えるのと同じ流儀。Excel を起動しない
    ユニットテストは ``_acquire`` にフェイクを渡す)。
    """
    _require_win32com(".xlsx", "Excel", _ACTION)
    import pythoncom
    import win32com.client as com

    pythoncom.CoInitialize()
    app = com.DispatchEx("Excel.Application")
    app.Visible = False
    app.DisplayAlerts = False
    return pythoncom, app


def render_range_png(
    xlsx_path: str | Path,
    sheet: str,
    cell_range: str | None,
    out_png: str | Path,
    retries: int = 5,
    _acquire: Callable[[], tuple[object, object]] | None = None,
) -> str:
    """xlsx の 1 シートの A1 レンジを PNG に書き出し、出力パス (絶対) を返す。

    ``cell_range`` を省略 (None/空) すると、そのシートの使用範囲 (UsedRange)
    全体を撮る (図面領域が特定できないときのフォールバック)。

    範囲を CopyPicture (ビットマップ) し、範囲と同じ大きさの一時チャートへ
    貼り付けて PNG へ Export する。図形・罫線・塗り・矢印などテキスト化で
    失われる情報を、見た目そのままキャプチャできる。

    クリップボード経由は準備待ちが要ることがあるため、CopyPicture→Paste→
    Export を ``retries`` 回まで再試行する。
    """
    src_abs = str(Path(xlsx_path).resolve())
    out_abs = str(Path(out_png).resolve())
    Path(out_abs).parent.mkdir(parents=True, exist_ok=True)

    pyc, app = (_acquire or _acquire_excel)()
    try:
        wb = app.Workbooks.Open(src_abs, ReadOnly=True)
        try:
            ws = wb.Worksheets(sheet)
            # 範囲未指定なら使用範囲全体 (図面領域を特定できないときの保険)。
            rng = ws.Range(cell_range) if cell_range else ws.UsedRange
            # 一時チャートを範囲と同じ大きさで用意する。Excel の Paste は「生成直後
            # で選択中のチャート」でないと空振りするため、範囲に重ねて (0,0) に置く。
            # 一方 CopyPicture(xlScreen) は画面の見た目 = 重なった浮動物ごと撮るので、
            # そのままだと空のチャートが被写体になり空撮になる。そこで**チャート背景を
            # 透明化**し、重なっていても範囲を透かしてコピーできるようにする
            # (貼り付けた画像は不透明で全面を覆うため Export 結果に透明は出ない)。
            cobj = ws.ChartObjects().Add(0, 0, rng.Width, rng.Height)
            try:
                chart = cobj.Chart
                try:
                    chart.ChartArea.Format.Fill.Visible = False
                except Exception:  # noqa: BLE001 版差で不可なら白背景のまま続行
                    pass
                last: object | None = None
                for _ in range(max(1, retries)):
                    # 前回の空 Paste 残骸を消してから試行する。
                    try:
                        for s in list(chart.Shapes):
                            s.Delete()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        rng.CopyPicture(_XL_SCREEN, _XL_BITMAP)
                        pyc.PumpWaitingMessages()
                        time.sleep(0.2)  # クリップボードへの反映待ち
                        chart.Paste()
                        pyc.PumpWaitingMessages()
                        # 貼り付け成否は図形数で検証する。クリップボード未反映だと
                        # Paste は無反応でも例外を出さず、Export は空 PNG を書いて
                        # しまう (ファイル存在だけでは空撮を検知できない)。
                        if int(chart.Shapes.Count) < 1:
                            raise RuntimeError("貼り付けが空 (クリップボード未反映)")
                        chart.Export(out_abs, "PNG")
                        break
                    except Exception as e:  # noqa: BLE001 クリップボード競合等は再試行
                        last = e
                        time.sleep(0.3)
                else:
                    raise _com_conversion_error(
                        ".xlsx", "Excel", _ACTION,
                        cause=last or "PNG が生成されませんでした",
                    )
            finally:
                cobj.Delete()
        finally:
            wb.Close(SaveChanges=False)
    except OfficeUnavailableError:
        raise
    except Exception as e:  # pywin32 は取得済み。COM 操作自体の失敗。
        raise _com_conversion_error(".xlsx", "Excel", _ACTION, cause=e) from e
    finally:
        try:
            app.Quit()
        finally:
            pyc.CoUninitialize()
    return out_abs


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(
        prog="python -m docextract.render",
        description="Excel の指定セル範囲を PNG にレンダリングする (Excel COM)",
    )
    p.add_argument("xlsx", help="対象の .xlsx パス")
    p.add_argument("--sheet", required=True, help="シート名")
    p.add_argument("--range", required=True, dest="cell_range", help='A1 レンジ (例 "B2:L8")')
    p.add_argument("-o", "--out", required=True, help="出力 PNG パス")
    args = p.parse_args(argv)
    try:
        out = render_range_png(args.xlsx, args.sheet, args.cell_range, args.out)
    except OfficeUnavailableError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    print(f"レンダリングしました: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
