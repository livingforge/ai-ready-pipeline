"""cli.py — 終了コード・エラー報告・ワイルドカード展開・フォルダ一括を検証する。"""

from __future__ import annotations

import pytest

from docextract.cli import main


def test_success_returns_zero(tmp_path, make_docx, capsys):
    src = make_docx("a.docx", paragraphs=[("hello", None)])
    rc = main([str(src), "-o", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr()
    assert "[OK]" in out.out


def test_unsupported_file_reports_ng_and_returns_one(tmp_path, capsys):
    bad = tmp_path / "note.txt"
    bad.write_text("x", encoding="utf-8")
    rc = main([str(bad), "-o", str(tmp_path / "out")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "[NG]" in err


def test_missing_file_reports_ng(tmp_path, capsys):
    rc = main([str(tmp_path / "ghost.pdf"), "-o", str(tmp_path / "out")])
    assert rc == 1
    assert "[NG]" in capsys.readouterr().err


def test_partial_failure_still_returns_one(tmp_path, make_docx, capsys):
    good = make_docx("ok.docx", paragraphs=[("hi", None)])
    bad = tmp_path / "bad.txt"
    bad.write_text("x", encoding="utf-8")
    rc = main([str(good), str(bad), "-o", str(tmp_path / "out")])
    assert rc == 1  # 1 件でも失敗すれば非ゼロ
    captured = capsys.readouterr()
    assert "[OK]" in captured.out
    assert "[NG]" in captured.err


def test_wildcard_expansion(tmp_path, make_docx, capsys):
    make_docx("one.docx", paragraphs=[("1", None)])
    make_docx("two.docx", paragraphs=[("2", None)])
    pattern = str(tmp_path / "*.docx")
    rc = main([pattern, "-o", str(tmp_path / "out")])
    assert rc == 0
    assert capsys.readouterr().out.count("[OK]") == 2


def test_nonmatching_glob_falls_back_to_literal_path(tmp_path, capsys):
    # マッチしないパターンはそのままパスとして扱われ、存在しないので NG
    pattern = str(tmp_path / "nope_*.pdf")
    rc = main([pattern, "-o", str(tmp_path / "out")])
    assert rc == 1
    assert "[NG]" in capsys.readouterr().err


def test_multiple_files_all_ok(tmp_path, make_docx, make_xlsx, capsys):
    d = make_docx("a.docx", paragraphs=[("x", None)])
    x = make_xlsx("b.xlsx", sheets={"S": [["y"]]})
    rc = main([str(d), str(x), "-o", str(tmp_path / "out")])
    assert rc == 0
    assert capsys.readouterr().out.count("[OK]") == 2


def test_default_output_dir(tmp_path, make_docx, monkeypatch, capsys):
    # -o 省略時は cwd 直下の output/ を使う
    src = make_docx("a.docx", paragraphs=[("x", None)])
    monkeypatch.chdir(tmp_path)
    rc = main([str(src)])
    assert rc == 0
    assert (tmp_path / "output" / "a_docx" / "result.json").exists()


# --------------------------------------------------------------------------
# --dir / フォルダ一括
# --------------------------------------------------------------------------
def test_dir_option_processes_all_supported(tmp_path, make_docx, make_xlsx, make_pdf, capsys):
    src = tmp_path / "src"
    src.mkdir()
    make_docx("src/a.docx", paragraphs=[("x", None)])
    make_xlsx("src/b.xlsx", sheets={"S": [["y"]]})
    make_pdf("src/c.pdf", pages=[{"texts": [("z", (72, 72))]}])
    # 対応外は無視される
    (src / "note.txt").write_text("ignore", encoding="utf-8")

    rc = main(["--dir", str(src), "-o", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("[OK]") == 3
    assert "note.txt" not in out


def test_dir_skips_office_lock_files(tmp_path, make_docx, capsys):
    src = tmp_path / "src"
    src.mkdir()
    make_docx("src/real.docx", paragraphs=[("x", None)])
    (src / "~$real.docx").write_bytes(b"lock")  # Office 一時ファイル

    rc = main(["--dir", str(src), "-o", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("[OK]") == 1
    assert "~$" not in out


def test_dir_non_recursive_ignores_subfolders(tmp_path, make_docx, capsys):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    make_docx("src/top.docx", paragraphs=[("x", None)])
    make_docx("src/sub/deep.docx", paragraphs=[("y", None)])

    rc = main(["--dir", str(src), "-o", str(tmp_path / "out")])
    assert rc == 0
    assert capsys.readouterr().out.count("[OK]") == 1  # top のみ


def test_dir_recursive_includes_subfolders(tmp_path, make_docx, capsys):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    make_docx("src/top.docx", paragraphs=[("x", None)])
    make_docx("src/sub/deep.docx", paragraphs=[("y", None)])

    rc = main(["--dir", str(src), "-r", "-o", str(tmp_path / "out")])
    assert rc == 0
    assert capsys.readouterr().out.count("[OK]") == 2


def test_positional_directory_is_scanned(tmp_path, make_docx, capsys):
    src = tmp_path / "src"
    src.mkdir()
    make_docx("src/a.docx", paragraphs=[("x", None)])
    make_docx("src/b.docx", paragraphs=[("y", None)])

    rc = main([str(src), "-o", str(tmp_path / "out")])
    assert rc == 0
    assert capsys.readouterr().out.count("[OK]") == 2


def test_dir_and_files_deduplicated(tmp_path, make_docx, capsys):
    src = tmp_path / "src"
    src.mkdir()
    a = make_docx("src/a.docx", paragraphs=[("x", None)])
    # 同じファイルを個別指定 + フォルダ指定 → 1 回だけ処理
    rc = main([str(a), "--dir", str(src), "-o", str(tmp_path / "out")])
    assert rc == 0
    assert capsys.readouterr().out.count("[OK]") == 1


def test_missing_dir_reports_ng(tmp_path, capsys):
    rc = main(["--dir", str(tmp_path / "ghost"), "-o", str(tmp_path / "out")])
    assert rc == 1
    assert "[NG]" in capsys.readouterr().err


def test_empty_dir_reports_nothing_to_do(tmp_path, capsys):
    src = tmp_path / "empty"
    src.mkdir()
    rc = main(["--dir", str(src), "-o", str(tmp_path / "out")])
    assert rc == 1
    assert "対応ファイルが見つかりません" in capsys.readouterr().out


def test_no_inputs_errors_out(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
