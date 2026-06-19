# Agent Guide

This repository contains the Stanford EDGAR Filings Dataset parser. Preserve filing semantics and visual layout; do not add local phrase hardcodes to make a showcase file look better.

## Core Commands

- Parse a filing to layout-faithful MultiMarkdown:
  `python -m stanford_edgar_parser path/to/filing.txt --to_mmd`
- Parse without final indentation markers:
  `python -m stanford_edgar_parser path/to/filing.txt --to_mmd --disable-indentation`
- Render parsed Markdown:
  `node multimarkdown.js path/to/parsed.md > /tmp/sefd.html`
  `node html-to-pdf.mjs /tmp/sefd.html path/to/rendered.pdf`
- Check showcase table artifacts:
  `python tools/check_showcase_tables.py examples`
- Build side-by-side raw-vs-parsed review snippets:
  `python tools/review_snippet.py <example-dir-or-accession> "<needle text>"`
- Start the MCP server:
  `python -m stanford_edgar_parser.mcp_server`

The project also includes `.mcp.json` for MCP clients that support repo-local server config.

## Parser Rules

- Fix root causes in HTML/XML/SGML/table reconstruction code, not one-off output strings.
- Keep existing encoding/mojibake repairs unless the user explicitly asks to remove them.
- Preserve raw filer text when the source itself contains a typo or missing space.
- Preserve table structure, signs, currency symbols, percent signs, row/col spans, superscripts, subscripts, links, and indentation.
- Treat empty span-cover rows as removable only when they carry no visible content.
- Do not commit scratch review artifacts unless explicitly asked. Local review images and temporary HTML belong outside the published examples.

## Review Bar

For examples or parser changes, compare parsed output against the raw browser view, not just against Markdown. Inspect tables for silent dropped columns, detached modifiers (`$`, `%`, `)`, `bp`), row/column-span drift, list indentation, image placeholders, and malformed Markdown emphasis.

Use `tools/review_snippet.py` to isolate suspicious raw HTML blocks and the corresponding parsed Markdown. If a discrepancy appears, identify whether it is in the original source, a renderer artifact, or a parser bug before changing code.
