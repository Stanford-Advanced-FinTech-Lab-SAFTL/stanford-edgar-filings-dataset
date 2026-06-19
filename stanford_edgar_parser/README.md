# Stanford EDGAR Parser

Layout-faithful SEC filing parser used by the Stanford EDGAR Filings Dataset.
It converts raw EDGAR TXT/HTML/SGML/XML submissions into Markdown or
MultiMarkdown while preserving financial-table structure, indentation, links,
inline formatting, and filing metadata where possible.

## Install

From PyPI, after release:

```bash
pip install stanford-edgar-parser
```

Until then, install directly from GitHub:

```bash
pip install "stanford-edgar-parser @ git+https://github.com/Stanford-Advanced-FinTech-Lab-SAFTL/stanford-edgar-filings-dataset.git"
```

For local development from a clone:

```bash
pip install -e .
```

## Layout

- `runtime.py`: backward-compatible re-export shim
- `orchestrator.py`: local filing orchestration and final output cleanup
- `utils/`: imports, tokenizer helpers, parse statistics, and shared setup
- `multimarkdown/`: MultiMarkdown table conversion
- `parsers/html/`: HTML preprocessing, table cleanup, parser, and postprocessing
- `parsers/ocr/`: Mistral OCR key rotation, PDF/image OCR, and OCR utilities
- `parsers/plaintext/`: plaintext and legacy text-form parsers
- `parsers/sgml/`: SGML document-block utilities
- `parsers/xml/`: XML filing-form parsers
- `sec_parser.py`: compatibility shim for old `python stanford_edgar_parser/sec_parser.py` usage
- `__main__.py`: `python -m stanford_edgar_parser` command-line entrypoint

The original implementation remains untouched at `sec_parser/sec_parser.py`.
The equivalence tests in `tests/parser_equivalence/` verify the split-module
coverage and compare parser outputs bit-for-bit.

## Usage

```bash
python -m stanford_edgar_parser path/to/filing.txt
python -m stanford_edgar_parser path/to/filing.txt --to_mmd
stanford-edgar-parser path/to/filing.txt --to_mmd
```

```python
from stanford_edgar_parser import main_one, parse_html_filing
```

## Agent Skill Install

Install bundled Codex and Claude skill files:

```bash
stanford-edgar-install-skill
```

Or from Python:

```python
from stanford_edgar_parser.ai import install_skill

install_skill()
```

Use `--overwrite` if you want to replace an existing installed skill.

## MCP

After package install, expose the parser as an MCP server with:

```toml
[mcp_servers.stanford_edgar_parser]
command = "uvx"
args = ["--from", "stanford-edgar-parser", "stanford-edgar-mcp"]
startup_timeout_sec = 120
```

Before the PyPI release, use the GitHub package source:

```toml
[mcp_servers.stanford_edgar_parser]
command = "uvx"
args = [
  "--from",
  "stanford-edgar-parser @ git+https://github.com/Stanford-Advanced-FinTech-Lab-SAFTL/stanford-edgar-filings-dataset.git",
  "stanford-edgar-mcp"
]
startup_timeout_sec = 120
```

The package-installed MCP server always exposes `parse_filing`. Repo-local
rendering and review tools are exposed when the full clone includes
`multimarkdown.js`, `html-to-pdf.mjs`, and `tools/`.
