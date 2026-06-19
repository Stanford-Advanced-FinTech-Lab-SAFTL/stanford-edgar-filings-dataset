---
name: stanford-edgar-parser
description: Use the Stanford EDGAR Filings Dataset parser to parse SEC filings into layout-faithful MultiMarkdown, render and inspect examples, debug table/indentation/link/OCR issues, run showcase checks, and use the local MCP server.
---

# Stanford EDGAR Parser

Use this skill in the Stanford EDGAR parser repository or in an environment
where the `stanford-edgar-parser` Python package is installed.

## Commands

- Parse to MultiMarkdown:
  `python -m stanford_edgar_parser path/to/filing.txt --to_mmd`
  or `stanford-edgar-parser path/to/filing.txt --to_mmd`
- Remove final indentation markers:
  `python -m stanford_edgar_parser path/to/filing.txt --to_mmd --disable-indentation`
- Render:
  `node multimarkdown.js path/to/parsed.md > /tmp/sefd.html`
  `node html-to-pdf.mjs /tmp/sefd.html path/to/rendered.pdf`
- Static showcase checks:
  `python tools/check_showcase_tables.py examples`
- Raw-vs-parsed review:
  `python tools/review_snippet.py <example-dir-or-accession> "<needle text>"`
- MCP server:
  `python -m stanford_edgar_parser.mcp_server`
  or `stanford-edgar-mcp`

The repo includes `.mcp.json` for clients that support project-local MCP configuration.
Package installs always expose `parse_filing`; repo-only render/review tools are exposed only when helper scripts are present.

## Review Standard

Compare parser output to the raw browser view. Do not approve output just because rendered Markdown looks plausible.

Prioritize table fidelity: visible columns/rows, merged headers, indentation hierarchy, `$`, `%`, `)`, `bp`, accounting parentheses, superscripts, subscripts, same-target links, and image placeholders.

Fix parser root causes. Do not introduce phrase-specific showcase hardcodes.
