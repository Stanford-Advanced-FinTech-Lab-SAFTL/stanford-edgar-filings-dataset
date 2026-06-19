#!/usr/bin/env python3
"""Build a side-by-side raw-browser vs parsed-Markdown review snippet.

This is a local QA helper for showcase review. It writes scratch artifacts to
/tmp by default and should not put anything inside examples/.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable

from bs4 import BeautifulSoup, Tag


BLOCK_TAGS = {
    "table",
    "p",
    "div",
    "section",
    "article",
    "li",
    "ul",
    "ol",
    "blockquote",
}


@dataclass
class RawSnippet:
    html_fragment: str
    source_note: str


@dataclass
class ParsedSnippet:
    markdown: str
    source_note: str


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def resolve_example(root: pathlib.Path, example: str) -> pathlib.Path:
    candidate = pathlib.Path(example)
    if candidate.is_dir():
        return candidate
    candidate = root / "examples" / example
    if candidate.is_dir():
        return candidate
    raise SystemExit(f"Could not find example directory: {example}")


def contains_needle(tag: Tag, needle_norm: str) -> bool:
    return needle_norm in normalize_text(tag.get_text(" ", strip=True))


def find_smallest_tag(soup: BeautifulSoup, needle: str) -> Tag | None:
    needle_norm = normalize_text(needle)
    if not needle_norm:
        return None

    best: Tag | None = None
    best_len = sys.maxsize
    for tag in soup.find_all(True):
        if tag.name in {"script", "style", "head", "meta", "link"}:
            continue
        if not contains_needle(tag, needle_norm):
            continue
        text_len = len(tag.get_text(" ", strip=True))
        if text_len < best_len:
            best = tag
            best_len = text_len
    return best


def nearest_useful_block(tag: Tag | None) -> Tag | None:
    if tag is None:
        return None
    table = tag.find_parent("table")
    if table is not None:
        return table
    cur: Tag | None = tag
    while cur is not None and cur.name not in BLOCK_TAGS:
        parent = cur.parent
        cur = parent if isinstance(parent, Tag) else None
    return cur or tag


def trim_large_table(block: Tag, needle: str, row_context: int, max_chars: int) -> tuple[str, str]:
    block_html = str(block)
    if block.name != "table" or len(block_html) <= max_chars:
        return block_html, f"raw block: <{block.name}>"

    needle_norm = normalize_text(needle)
    rows = block.find_all("tr")
    hit_index = None
    for idx, row in enumerate(rows):
        if needle_norm in normalize_text(row.get_text(" ", strip=True)):
            hit_index = idx
            break

    if hit_index is None:
        return block_html[:max_chars], f"raw block: truncated <table> first {max_chars} chars"

    start = max(0, hit_index - row_context)
    end = min(len(rows), hit_index + row_context + 1)
    new_table = BeautifulSoup("<table></table>", "html.parser").table
    assert new_table is not None
    for attr, value in block.attrs.items():
        new_table[attr] = value
    for row in rows[start:end]:
        new_table.append(BeautifulSoup(str(row), "html.parser"))

    note = f"raw block: <table> rows {start + 1}-{end} of {len(rows)}"
    return str(new_table), note


def extract_raw_snippet(raw_text: str, needle: str, row_context: int, max_chars: int) -> RawSnippet:
    needle_norm = normalize_text(needle)
    raw_norm = normalize_text(raw_text)
    norm_idx = raw_norm.find(needle_norm)

    source_text = raw_text
    source_offset_note = "full raw source"
    if norm_idx >= 0 and len(raw_text) > max_chars:
        raw_idx = raw_text.lower().find(needle.lower())
        if raw_idx < 0:
            raw_idx = max(0, min(len(raw_text), norm_idx))
        start = max(0, raw_idx - max_chars // 2)
        end = min(len(raw_text), raw_idx + max_chars // 2)

        table_start = raw_text.rfind("<table", 0, raw_idx)
        table_end = raw_text.find("</table", raw_idx)
        if table_start >= 0 and table_end >= 0:
            close_end = raw_text.find(">", table_end)
            if close_end >= 0 and close_end - table_start <= max_chars * 2:
                start = table_start
                end = close_end + 1

        source_text = raw_text[start:end]
        source_offset_note = f"raw source chars {start}-{end}"

    soup = BeautifulSoup(source_text, "lxml")
    style_tags = "\n".join(str(tag) for tag in soup.find_all("style"))
    tag = find_smallest_tag(soup, needle)
    block = nearest_useful_block(tag)

    if block is None:
        idx = normalize_text(raw_text).find(normalize_text(needle))
        if idx < 0:
            fragment = f"<pre>{html.escape(raw_text[:max_chars])}</pre>"
            return RawSnippet(fragment, "raw fallback: needle not found, first source chunk")
        start = max(0, idx - max_chars // 2)
        end = min(len(raw_text), idx + max_chars // 2)
        fragment = f"<pre>{html.escape(raw_text[start:end])}</pre>"
        return RawSnippet(fragment, f"raw fallback: source chars {start}-{end}")

    fragment, note = trim_large_table(block, needle, row_context=row_context, max_chars=max_chars)
    if style_tags:
        fragment = f"{style_tags}\n{fragment}"
    return RawSnippet(fragment, f"{note}; {source_offset_note}")


def line_matches(line: str, needle_norm: str) -> bool:
    return needle_norm in normalize_text(line)


def table_bounds(lines: list[str], hit: int) -> tuple[int, int]:
    start = hit
    while start > 0 and lines[start - 1].lstrip().startswith("|"):
        start -= 1
    end = hit + 1
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    return start, end


def extract_parsed_snippet(parsed_text: str, needle: str, context_lines: int, max_lines: int) -> ParsedSnippet:
    lines = parsed_text.splitlines()
    needle_norm = normalize_text(needle)
    hit = next((i for i, line in enumerate(lines) if line_matches(line, needle_norm)), None)
    if hit is None:
        excerpt = "\n".join(lines[:max_lines])
        return ParsedSnippet(excerpt, "parsed fallback: needle not found, first markdown chunk")

    if lines[hit].lstrip().startswith("|"):
        start, end = table_bounds(lines, hit)
        note = f"parsed block: markdown table lines {start + 1}-{end}"
    else:
        start = max(0, hit - context_lines)
        end = min(len(lines), hit + context_lines + 1)
        note = f"parsed block: markdown lines {start + 1}-{end}"

    if end - start > max_lines:
        half = max_lines // 2
        start = max(0, hit - half)
        end = min(len(lines), start + max_lines)
        note += f" trimmed to {max_lines} lines around hit"

    return ParsedSnippet("\n".join(lines[start:end]), note)


def write_raw_html(path: pathlib.Path, fragment: str, title: str) -> None:
    path.write_text(
        f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ margin: 16px; font-family: Times New Roman, Times, serif; font-size: 14px; }}
table {{ border-collapse: collapse; max-width: 100%; }}
td, th {{ vertical-align: top; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
</style>
</head>
<body>
{fragment}
</body>
</html>
""",
        encoding="utf-8",
    )


def write_index(path: pathlib.Path, accession: str, needle: str, raw_note: str, parsed_note: str) -> None:
    path.write_text(
        f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>SEFD snippet review: {html.escape(accession)}</title>
<style>
body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
header {{ padding: 12px 16px; border-bottom: 1px solid #d0d7de; }}
.meta {{ color: #57606a; font-size: 13px; margin-top: 4px; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; height: calc(100vh - 86px); }}
.pane {{ min-width: 0; border-right: 1px solid #d0d7de; display: flex; flex-direction: column; }}
.pane:last-child {{ border-right: 0; }}
.label {{ padding: 8px 12px; background: #f6f8fa; border-bottom: 1px solid #d0d7de; font-size: 13px; font-weight: 600; }}
iframe {{ border: 0; width: 100%; flex: 1; }}
code {{ background: #f6f8fa; padding: 1px 4px; border-radius: 4px; }}
</style>
</head>
<body>
<header>
  <strong>{html.escape(accession)}</strong>
  <div class="meta">Needle: <code>{html.escape(needle)}</code></div>
  <div class="meta">Raw: {html.escape(raw_note)} | Parsed: {html.escape(parsed_note)}</div>
</header>
<main class="grid">
  <section class="pane">
    <div class="label">Raw HTML Browser View</div>
    <iframe src="raw_snippet.html"></iframe>
  </section>
  <section class="pane">
    <div class="label">Parsed Markdown Rendered View</div>
    <iframe src="parsed_snippet.html"></iframe>
  </section>
</main>
</body>
</html>
""",
        encoding="utf-8",
    )


def render_markdown(repo_root: pathlib.Path, md_path: pathlib.Path, html_path: pathlib.Path) -> None:
    renderer = repo_root / "multimarkdown.js"
    can_render = renderer.exists() and (repo_root / "node_modules" / "markdown-it").exists()
    if not can_render:
        body = f"<pre>{html.escape(md_path.read_text(encoding='utf-8', errors='replace'))}</pre>"
        html_path.write_text(
            f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ margin: 16px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
</style>
</head>
<body>
{body}
</body>
</html>
""",
            encoding="utf-8",
        )
        return

    result = subprocess.run(
        ["node", str(renderer), str(md_path)],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "markdown render failed")
    body = result.stdout
    html_path.write_text(
        f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ margin: 16px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 14px; }}
table {{ border-collapse: collapse; max-width: 100%; }}
td, th {{ border: 1px solid #d0d7de; padding: 3px 5px; vertical-align: top; }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
</style>
</head>
<body>
{body}
</body>
</html>
""",
        encoding="utf-8",
    )


def render_screenshot(
    renderer_root: pathlib.Path,
    html_path: pathlib.Path,
    png_path: pathlib.Path,
    width: int,
    height: int,
) -> bool:
    if not (renderer_root / "node_modules" / "puppeteer").exists():
        return False

    script = r"""
const puppeteer = require('puppeteer');
const path = require('path');
const [input, output, width, height] = process.argv.slice(-4);
(async () => {
  const browser = await puppeteer.launch({ headless: 'new' });
  const page = await browser.newPage();
  await page.setViewport({ width: Number(width), height: Number(height), deviceScaleFactor: 1 });
  await page.goto('file://' + path.resolve(input), { waitUntil: 'networkidle0' });
  await page.screenshot({ path: output, fullPage: true });
  await browser.close();
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""
    env = os.environ.copy()
    result = subprocess.run(
        ["node", "-e", script, str(html_path), str(png_path), str(width), str(height)],
        cwd=renderer_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        print(f"[screenshot warning] {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example", help="Accession number or path to example folder.")
    parser.add_argument("--needle", required=True, help="Distinctive text/value to locate in raw and parsed output.")
    parser.add_argument("--parsed-needle", help="Different text/value to locate in parsed.md.")
    parser.add_argument("--root", default=".", help="Repo root. Defaults to current directory.")
    parser.add_argument("--out", help="Output directory. Defaults to /tmp/sefd-review/<accession>-<slug>.")
    parser.add_argument("--row-context", type=int, default=4, help="Rows around a matched raw table row when trimming large tables.")
    parser.add_argument("--context-lines", type=int, default=20, help="Parsed Markdown context lines around a non-table hit.")
    parser.add_argument("--max-raw-chars", type=int, default=40000, help="Maximum raw HTML chars before table trimming/fallback.")
    parser.add_argument("--max-parsed-lines", type=int, default=120, help="Maximum parsed Markdown lines in the snippet.")
    parser.add_argument("--screenshot-width", type=int, default=1600, help="Viewport width for generated PNG review screenshots.")
    parser.add_argument("--screenshot-height", type=int, default=1200, help="Viewport height for generated PNG review screenshots.")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    example_dir = resolve_example(root, args.example)
    accession = example_dir.name
    raw_path = example_dir / "raw.txt"
    parsed_path = example_dir / "parsed.md"
    if not raw_path.exists() or not parsed_path.exists():
        raise SystemExit(f"{example_dir} must contain raw.txt and parsed.md")

    slug = re.sub(r"[^a-zA-Z0-9]+", "-", args.needle).strip("-")[:48] or "snippet"
    out_dir = pathlib.Path(args.out or f"/tmp/sefd-review/{accession}-{slug}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = extract_raw_snippet(read_text(raw_path), args.needle, args.row_context, args.max_raw_chars)
    parsed = extract_parsed_snippet(
        read_text(parsed_path),
        args.parsed_needle or args.needle,
        args.context_lines,
        args.max_parsed_lines,
    )

    raw_html_path = out_dir / "raw_snippet.html"
    parsed_md_path = out_dir / "parsed_snippet.md"
    parsed_html_path = out_dir / "parsed_snippet.html"
    index_path = out_dir / "index.html"
    source_path = out_dir / "raw_snippet_source.html"
    manifest_path = out_dir / "manifest.json"
    review_png_path = out_dir / "review.png"
    raw_png_path = out_dir / "raw_snippet.png"
    parsed_png_path = out_dir / "parsed_snippet.png"

    write_raw_html(raw_html_path, raw.html_fragment, f"raw snippet {accession}")
    parsed_md_path.write_text(parsed.markdown + "\n", encoding="utf-8")
    source_path.write_text(raw.html_fragment, encoding="utf-8")
    render_markdown(root, parsed_md_path, parsed_html_path)
    write_index(index_path, accession, args.needle, raw.source_note, parsed.source_note)

    screenshots = {}
    for key, html_file, png_file in (
        ("review_png", index_path, review_png_path),
        ("raw_png", raw_html_path, raw_png_path),
        ("parsed_png", parsed_html_path, parsed_png_path),
    ):
        if render_screenshot(root, html_file, png_file, args.screenshot_width, args.screenshot_height):
            screenshots[key] = str(png_file)

    manifest = {
        "accession": accession,
        "needle": args.needle,
        "parsed_needle": args.parsed_needle or args.needle,
        "raw_note": raw.source_note,
        "parsed_note": parsed.source_note,
        "index_html": str(index_path),
        "raw_html": str(raw_html_path),
        "raw_source_html": str(source_path),
        "parsed_markdown": str(parsed_md_path),
        "parsed_html": str(parsed_html_path),
        **screenshots,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(index_path)
    if screenshots:
        for key in sorted(screenshots):
            print(f"{key}: {screenshots[key]}")
    print(f"manifest: {manifest_path}")
    print(raw.source_note)
    print(parsed.source_note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
