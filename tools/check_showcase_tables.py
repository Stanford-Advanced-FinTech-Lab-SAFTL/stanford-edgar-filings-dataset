#!/usr/bin/env python3
"""Static checks for silent table-fragment bugs in showcase parsed Markdown."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys


CURRENCY_RE = re.compile(r"^(?:\*{1,3})?(?:[\$£€¥￥]|C\$|A\$|R\$|COP)(?:\*{1,3})?$")
OPEN_PAREN_RE = re.compile(r"^(?:\*{1,3})?\((?:\*{1,3})?$")
SUFFIX_RE = re.compile(r"^(?:\*{1,3})?(?:%|\)|\)%|\)bp)(?:\*{1,3})?$", re.I)
NUMERIC_RE = re.compile(
    r"^(?:\*{1,3})?[\$£€¥￥]?\(?-?\d[\d,]*(?:\.\d+)?(?:\s*(?:years?|months?))?(?:\*{1,3})?$",
    re.I,
)
NUMERIC_WITH_STYLE_RE = re.compile(r"\d")
SPACED_RULE_CITATION_RE = re.compile(
    r"\b[Rr]ule\s+\d+[A-Za-z0-9.-]*\s+\([A-Za-z0-9ivxlcdm]+\)"
    r"|\b[Rr]ule\s+\d+[A-Za-z0-9.-]*\([A-Za-z0-9ivxlcdm]+\)\s*[–-]\s*"
    r"\d+[A-Za-z0-9.-]*\s+\([A-Za-z0-9ivxlcdm]+\)"
)
JOINED_NUMERIC_LIST_MARKER_RE = re.compile(r"^(?:&nbsp;|[ \t])*\d+\)(?=[A-Za-z])")
JOINED_ALPHA_LIST_MARKER_RE = re.compile(r"^(?:&nbsp;|[ \t])*[A-Z]\.(?=[A-Z][a-z])")
SPLIT_APOSTROPHE_EMPHASIS_RE = re.compile(r"\*\*[^*\n|]*[A-Za-z]\*\*[\'’]\*\*[A-Za-z][^*\n|]*\*\*")


def split_markdown_row(line: str) -> list[str]:
    row = line.strip()
    if not row.startswith("|"):
        return []
    row = row.strip("|")
    return [cell.strip() for cell in row.split("|")]


def strip_cell(cell: str) -> str:
    text = cell.replace("&nbsp;", " ")
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"</?[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_divider(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def is_numeric_cell(cell: str) -> bool:
    text = strip_cell(cell)
    if NUMERIC_RE.fullmatch(text):
        return True
    return bool(NUMERIC_WITH_STYLE_RE.search(text)) and not re.search(r"[A-Za-z]{3,}", text)


def is_year_header(cell: str) -> bool:
    return bool(re.fullmatch(r"\*{0,3}\d{4}\*{0,3}", strip_cell(cell)))


def find_issues(path: pathlib.Path) -> list[str]:
    issues: list[str] = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    for line_no, line in enumerate(lines, start=1):
        emphasis_probe = re.sub(r"\\\*", "", line)
        if (
            "****" in emphasis_probe
            or re.search(r"(?<!\*)\*%\*\*\*(?!\*)", emphasis_probe)
            or re.search(r"\*[.,:;]\*{2,3}", emphasis_probe)
            or re.search(r"\*\*[,.():;]\*\*", emphasis_probe)
        ):
            issues.append(f"{path}:{line_no}: malformed Markdown emphasis marker run")
        if SPACED_RULE_CITATION_RE.search(strip_cell(line)):
            issues.append(f"{path}:{line_no}: spaced Rule citation, e.g. Rule 13 (a)")
        if JOINED_NUMERIC_LIST_MARKER_RE.search(line):
            issues.append(f"{path}:{line_no}: joined numeric list marker, e.g. 1)Text")
        if JOINED_ALPHA_LIST_MARKER_RE.search(line):
            issues.append(f"{path}:{line_no}: joined alphabetic list marker, e.g. A.Text")
        if SPLIT_APOSTROPHE_EMPHASIS_RE.search(line):
            issues.append(f"{path}:{line_no}: split Markdown emphasis across apostrophe")
        if "**Date and Tim**e" in line:
            issues.append(f"{path}:{line_no}: split Markdown emphasis inside Date and Time")

        cells = split_markdown_row(line)
        if not cells or is_divider(cells):
            continue

        clean = [strip_cell(cell) for cell in cells]
        for idx, cell in enumerate(clean[:-1]):
            nxt = clean[idx + 1]

            if idx > 0 and CURRENCY_RE.fullmatch(cell) and is_numeric_cell(nxt):
                issues.append(
                    f"{path}:{line_no}: split currency prefix at cells {idx + 1}-{idx + 2}: {cell!r} before {nxt!r}"
                )
            if OPEN_PAREN_RE.fullmatch(cell) and is_numeric_cell(nxt):
                issues.append(
                    f"{path}:{line_no}: split opening parenthesis at cells {idx + 1}-{idx + 2}: {cell!r} before {nxt!r}"
                )
            if is_numeric_cell(cell) and SUFFIX_RE.fullmatch(nxt) and not is_year_header(cell):
                issues.append(
                    f"{path}:{line_no}: split numeric suffix at cells {idx + 1}-{idx + 2}: {cell!r} before {nxt!r}"
                )

        joined_line = strip_cell(line)
        if re.search(r"\d(?:\.\d+)?\s+%", joined_line):
            issues.append(f"{path}:{line_no}: numeric percent has a visible space before %")
        if re.search(r"\(\s+\d|\d\s+\)", joined_line):
            issues.append(f"{path}:{line_no}: parenthesized number has visible internal spacing")

    return issues


def collect_paths(args: argparse.Namespace) -> list[pathlib.Path]:
    paths: list[pathlib.Path] = []
    for raw in args.paths:
        path = pathlib.Path(raw)
        if path.is_dir():
            paths.extend(sorted(path.glob("*/parsed.md")))
            if (path / "parsed.md").exists():
                paths.append(path / "parsed.md")
        else:
            paths.append(path)
    return sorted(set(paths))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", default=["examples"], help="parsed.md files, example dirs, or examples root")
    args = parser.parse_args(argv)

    issues: list[str] = []
    for path in collect_paths(args):
        if path.name != "parsed.md":
            continue
        issues.extend(find_issues(path))

    if issues:
        print("\n".join(issues))
        return 1

    print("No detached numeric modifier issues found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
