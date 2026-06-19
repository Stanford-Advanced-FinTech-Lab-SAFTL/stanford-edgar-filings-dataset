# Parser Output Review

Use this checklist when reviewing parsed filings or showcase examples.

## Compare Against Source

- Inspect the raw browser view for the same section, table, or paragraph.
- If the raw source itself has a typo or missing space, preserve it unless the task is explicit normalization.
- If the browser visually joins split HTML fragments, the parser should usually reconstruct the same semantic value.

## Tables

- Check that every visible column and row appears.
- Check that merged headers remain grouped with MultiMarkdown `||` and `^^`.
- Check that currency/percent/parentheses modifiers attach to numbers: `$7700`, `75.0%`, `(200)`, `)bp`.
- Check that empty body rows are not artifacts from rowspan/colspan scaffolding.
- Check that numeric signs and accounting parentheses are not flipped or dropped.
- Check that row labels keep indentation levels where they carry hierarchy.

## Text And Lists

- Check numbered, alphabetic, and parenthesized list markers for a visible space or indentation after the marker.
- Check that paragraph indentation is preserved where it expresses hierarchy.
- Check that adjacent styled spans do not create malformed Markdown emphasis.
- Check that adjacent links with the same target preserve readable spacing.
- Check image placeholders are explicit and not confused with body text.

## Useful Commands

- `python tools/check_showcase_tables.py examples`
- `python tools/review_snippet.py examples/<accession> "<needle text>"`
- `python -m stanford_edgar_parser examples/<accession>/raw.txt --to_mmd`
- `node multimarkdown.js examples/<accession>/parsed.md > /tmp/sefd.html`
- `node html-to-pdf.mjs /tmp/sefd.html /tmp/sefd.pdf`
