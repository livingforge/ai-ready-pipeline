---
name: docextract
description: Extract text, tables, and images from Office documents (docx/xlsx/pptx) and PDF into structured JSON, with OCR for image-embedded text and reconstruction of tables pasted as pictures. Use when asked to "parse / extract / convert / 解析 / 抽出 / 構造化" the contents of Word, Excel, PowerPoint, or PDF files. Requires Python 3.10+.
---

# docextract

Parse Office documents (Word / Excel / PowerPoint) and PDF into structured JSON
of **text, tables, and images**. What sets it apart is capturing content that
exists only as pixels:

- Text inside images / screenshots → **OCR** (RapidOCR), attached as `ocr_text`
- Tables pasted as pictures → **detected and reconstructed** (rapid_layout +
  rapid_table) into ordinary `table` elements (2-D arrays)

All dependencies are OSS cleared for commercial use (MIT / BSD / Apache-2.0);
see [package-meta/docextract/dependencies.md](../../package-meta/docextract/dependencies.md).

## Setup (gated on first run)

Set up the environment once with the dedicated command (or delegate to the
@skill-setup agent). It builds the shared `.venv` at the project root with
[uv](https://docs.astral.sh/uv/), installs the requirements of both skills, and
installs the venv commands `specdb` / `docextract`. It is idempotent, and
`--check` reports the current state without changing anything. As a fallback,
the first run of `extract` / `docagent` also triggers the same bootstrap.

Because bootstrap can run a **remote installer** and download **hundreds of MB**, these
high-risk steps go through an **approval gate** and are **safe-by-default (opt-in, fail-closed)**:

- If `uv` is missing, dependencies aren't installed, or a model must be fetched, the
  launcher prints the exact command + download size and **stops unless approved**.
- Approve by setting `DOCEXTRACT_AUTOINSTALL=1` for that run, or by answering the
  interactive `y/N` prompt on a TTY. Non-interactive runs without opt-in fail closed.
- `DOCEXTRACT_NO_UV_AUTOINSTALL=1` hard-disables auto-install (takes precedence).
- OCR / table-detection models (tens of MB) download into `.venv` on first extraction.
- For fully offline use, run once online (approved) to warm the cache, or use
  `--ocr-backend windows` (Windows only, built-in OCR).

```bash
python .github/skills/docextract setup --check   # state only, changes nothing, no approval needed
# approved one-off setup (bash; PowerShell: set $env:DOCEXTRACT_AUTOINSTALL=1 first)
DOCEXTRACT_AUTOINSTALL=1 python .github/skills/docextract setup
```

## Usage

Commands below use the console script `docextract` (installed into the shared
venv by the bootstrap above; call it as `.venv/Scripts/docextract` if the venv
is not activated). It works from any directory inside the project. Before the
venv exists, use `python .github/skills/docextract extract ...` instead — same interface.

```bash
docextract extract <files...> -o <output-dir>
docextract extract --dir <folder> -o <output-dir>     # batch a folder
docextract extract --dir <folder> -r -o <output-dir>  # recurse
```

- Formats: `.docx` `.xlsx` `.xlsm` `.pptx` `.pdf` (wildcards ok). Legacy
  `.xls` `.doc` `.ppt` also work **on Windows with Microsoft Office installed**
  (converted via Office COM automation; see Limitations)
- Each input yields `<output-dir>/<id>/` containing `result.json` and `images/`, where
  `<id>` embeds a hash of the file's absolute path so same-named files in different
  folders never collide. A manifest `<output-dir>/index.json` indexes all extractions by id.
- `-d/--dir` (repeatable) batches every supported file in a folder; `-r` recurses.
  Office temp files (`~$…`) are skipped
- Other flags: `--no-ocr`, `--no-image-tables`, `--ocr-lang ja`,
  `--ocr-backend auto|rapidocr|windows`
- **Feeding stdout to an LLM?** Add `--quiet --json-summary` so stdout collapses to a
  single machine-readable line `{run_id, succeeded, failed, output_dir, index, log_path,
  ids, failures, duplicates}` instead of one `[OK]` line per file. The extracted content
  lives in `index.json` → each `result.json`; read only the docs you need, on demand.
  See [docs/usage.md](docs/usage.md#llm--エージェントに渡すとき--標準出力をレシートにする).

Work with extracted results through the same launcher:
`docextract docagent <subcommand>`. Summarize registered documents with an LLM
via `docsummary <subcommand>` (alias: `docextract summarize`) — see the
**docsummary** skill for target selection and API-key (.env) setup.

Python API:

```python
import sys; sys.path.insert(0, r".github/skills/docextract/scripts")
from docextract import extract
data = extract("report.docx", output_dir="out")   # returns a dict, also writes result.json
```

## Output

`elements` lists the document's contents in reading order. Three types:

| type | content | key fields |
|------|---------|-----------|
| `text` | paragraphs, headings, text boxes | `content`, `style`, `location` |
| `table` | tables (2-D array) | `rows`, `n_rows`, `n_cols`, `location` |
| `image` | reference to an extracted image | `file`, `ocr_text`, `width`, `height`, `location` |

`location` is format-specific: docx=`order`, xlsx=`sheet`, pptx=`slide`,
pdf=`page`+`bbox`. Tables detected inside images add `from_image` and
`bbox_in_image`. `summary` holds per-type counts; `metadata` holds title,
author, etc.

Full schema: [docs/output-schema.md](docs/output-schema.md). CLI reference, OCR
backends, self-test, and troubleshooting: [docs/usage.md](docs/usage.md).

## Limitations (surface these to the user)

- PDF table detection is ruling-based (pdfplumber); borderless tables may be missed
- Image tables recover row/column structure, but merged cells are padded with
  empty strings across the span
- Legacy formats (`.doc` `.xls` `.ppt`) are handled **only on Windows with the
  matching Microsoft Office app installed** — they are converted to OOXML via
  Office COM automation (needs `pywin32`). Without Office/pywin32 they fail
  closed with a clear "Microsoft Office is required" error naming the app; in
  that case convert to `.docx`/`.xlsx`/`.pptx` first
- OCR is imperfect; note that hard-to-read images may yield noisy text
