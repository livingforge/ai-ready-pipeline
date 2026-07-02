"""コマンドラインインターフェース。

例:
    python -m docextract report.docx -o out
    python -m docextract docs\\*.pdf slides.pptx
    python -m docextract --dir 資料フォルダ            # フォルダ内の対応ファイルを一括
    python -m docextract --dir 資料フォルダ -r          # サブフォルダも再帰的に
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from . import SUPPORTED_EXTENSIONS, extract


def _scan_dir(directory: Path, recursive: bool) -> list[Path]:
    """フォルダ内の対応形式ファイル (docx/xlsx/xlsm/pptx/pdf) を集める。

    ``recursive=True`` ならサブフォルダも辿る。一時ファイル (``~$`` で始まる
    Office のロックファイル等) は除外する。結果はパス順にソートして返す。
    """
    supported = {ext.lower() for ext in SUPPORTED_EXTENSIONS}
    it = directory.rglob("*") if recursive else directory.glob("*")
    found = [
        p
        for p in it
        if p.is_file() and p.suffix.lower() in supported and not p.name.startswith("~$")
    ]
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docextract",
        description=(
            "Office 文書 (docx/xlsx/pptx) と PDF からテキスト・表・画像を"
            "抽出して JSON 形式で出力します。"
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help=(
            f"入力ファイルまたはフォルダ (対応形式: {', '.join(SUPPORTED_EXTENSIONS)})。"
            "ワイルドカード可。フォルダを渡すと中の対応ファイルを一括処理"
        ),
    )
    parser.add_argument(
        "-d",
        "--dir",
        action="append",
        default=[],
        metavar="FOLDER",
        help="指定フォルダ内の対応ファイル (docx/xlsx/pptx/pdf) をすべて処理する (複数指定可)",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="--dir やフォルダ指定でサブフォルダも再帰的に走査する",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="output",
        help="出力先ディレクトリ (既定: output)",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="画像内テキストの OCR を無効化する",
    )
    parser.add_argument(
        "--ocr-lang",
        default="ja",
        help="OCR の言語 (既定: ja)",
    )
    parser.add_argument(
        "--ocr-backend",
        choices=["auto", "rapidocr", "windows"],
        default="auto",
        help="OCR バックエンド (既定: auto = rapidocr 優先、なければ Windows OCR)",
    )
    parser.add_argument(
        "--no-image-tables",
        action="store_true",
        help="画像内の表検出 (rapid_layout + rapid_table) を無効化する",
    )
    # Windows コンソール (cp932) でも日本語を安全に出力する
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    args = parser.parse_args(argv)

    if not args.inputs and not args.dir:
        parser.error("入力ファイルまたは --dir <フォルダ> を1つ以上指定してください")

    # 収集したファイル (重複は解決済みパスで排除し、指定順を保つ)
    files: list[Path] = []
    seen: set[Path] = set()

    def add_file(path: Path) -> None:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            files.append(path)

    failed = 0

    # Windows のシェルはワイルドカードを展開しないため自前で展開する。
    # 位置引数がフォルダなら中の対応ファイルを走査する。
    for pattern in args.inputs:
        p = Path(pattern)
        if p.is_dir():
            for f in _scan_dir(p, args.recursive):
                add_file(f)
            continue
        matched = [Path(m) for m in glob.glob(pattern)]
        if matched:
            for m in matched:
                if m.is_dir():
                    for f in _scan_dir(m, args.recursive):
                        add_file(f)
                else:
                    add_file(m)
        else:
            add_file(p)  # 存在しなければ後段の extract が明確なエラーを出す

    # --dir で明示指定されたフォルダを走査する
    for d in args.dir:
        dp = Path(d)
        if not dp.is_dir():
            print(f"[NG] フォルダが見つかりません: {dp}", file=sys.stderr)
            failed += 1
            continue
        matched = _scan_dir(dp, args.recursive)
        if not matched:
            scope = "（サブフォルダ含む）" if args.recursive else ""
            print(f"[--] 対応ファイルが見つかりません{scope}: {dp}")
        for f in matched:
            add_file(f)

    if not files:
        print("処理対象のファイルがありませんでした。", file=sys.stderr)
        return 1
    for path in files:
        try:
            data = extract(
                path,
                output_dir=args.output_dir,
                ocr=not args.no_ocr,
                ocr_lang=args.ocr_lang,
                ocr_backend=args.ocr_backend,
                image_tables=not args.no_image_tables,
            )
        except Exception as e:
            print(f"[NG] {path}: {e}", file=sys.stderr)
            failed += 1
            continue
        summary = ", ".join(f"{k}={v}" for k, v in data["summary"].items()) or "抽出なし"
        out = Path(args.output_dir) / f"{path.stem}_{path.suffix.lstrip('.').lower()}" / "result.json"
        print(f"[OK] {path} -> {out} ({summary})")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
