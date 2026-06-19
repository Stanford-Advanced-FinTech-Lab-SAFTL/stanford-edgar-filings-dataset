---
name: stanford-edgar-parser
description: Parse, render, inspect, and debug SEC EDGAR filings with the Stanford EDGAR Filings Dataset parser. Use when Codex is asked to convert local SEC filing TXT/HTML/SGML/XML/PDF-containing submissions into layout-faithful MultiMarkdown, review parser output against raw browser layout, diagnose table/indentation/link/OCR artifacts, run showcase checks, or use the repo's MCP server.
---

# Stanford EDGAR Parser

Use this skill inside a Stanford EDGAR parser repo clone or in an environment
where the `stanford-edgar-parser` Python package is installed.

## Quick Workflow

1. Parse local filings with:
   `python -m stanford_edgar_parser path/to/filing.txt --to_mmd`
   or, from an installed package:
   `stanford-edgar-parser path/to/filing.txt --to_mmd`
2. Use `--disable-indentation` only when the caller wants final `&nbsp;` indentation markers removed.
3. Render Markdown when visual QA matters:
   `node multimarkdown.js path/to/parsed.md > /tmp/sefd.html`
   `node html-to-pdf.mjs /tmp/sefd.html path/to/rendered.pdf`
4. Run showcase checks before accepting examples or parser changes:
   `python tools/check_showcase_tables.py examples`
5. For suspicious output, compare raw HTML and parsed Markdown with:
   `python tools/review_snippet.py <example-dir-or-accession> "<needle text>"`

## Debugging Rules

- Fix source-level reconstruction logic, not local phrase hardcodes.
- Preserve filer text if the raw source itself has a typo, missing space, or odd punctuation.
- Never assume rendered Markdown alone is correct; compare against the raw browser view.
- Watch especially for detached `$`, `%`, `)`, `bp`, lost negative parentheses, dropped columns, row/col span drift, malformed emphasis, broken same-URL links, and missing indentation in lists/tables.
- Keep scratch review artifacts out of published `examples/` unless explicitly requested.

## MCP

Start the local MCP server with:
`python -m stanford_edgar_parser.mcp_server`

or, from an installed package:
`stanford-edgar-mcp`

It exposes parser-oriented tools for parsing filings, rendering Markdown to PDF, running showcase checks, and generating raw-vs-parsed review snippets.

Package installs always expose `parse_filing`. Repo-only render/review tools are exposed only when `multimarkdown.js`, `html-to-pdf.mjs`, and `tools/` are present.

The repo includes `.mcp.json` for clients that support project-local MCP configuration.

## Reference

Read `references/review.md` when doing a meticulous parser-output review or preparing showcase examples.
