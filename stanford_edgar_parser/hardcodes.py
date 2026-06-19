import re
from typing import List, Sequence


_STACKED_HEADER_COLLAPSES = [
    {
        "name": "net_unrealized_appreciation_depreciation",
        "rows": [
            ["", "Net Unrealized", ""],
            ["", "Appreciation", ""],
            ["", "(Depreciation)", "Net Unrealized"],
            ["", "as a % of", "Appreciation"],
            ["", "Trust Capital", "(Depreciation)"],
        ],
        "replacement": [
            "",
            "**Net Unrealized<br>Appreciation<br>(Depreciation)<br>as a % of<br>Trust Capital**",
            "**Net Unrealized<br>Appreciation<br>(Depreciation)**",
        ],
    },
]


def _split_markdown_row(line: str) -> List[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _normalize_match_cell(cell: str) -> str:
    cell = re.sub(r"</?u>", "", cell, flags=re.IGNORECASE)
    cell = re.sub(r"(?i)<br\s*/?>", " ", cell)
    cell = cell.replace("**", "")
    cell = cell.replace("&nbsp;", " ")
    cell = re.sub(r"\s+", " ", cell)
    if cell.strip().lower() == "nan":
        return ""
    return cell.strip()


def _row_matches(line: str, expected_cells: Sequence[str]) -> bool:
    cells = _split_markdown_row(line)
    if cells is None or len(cells) != len(expected_cells):
        return False
    return all(
        _normalize_match_cell(actual) == expected
        for actual, expected in zip(cells, expected_cells)
    )


def _format_markdown_row(cells: Sequence[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def apply_markdown_hardcodes(markdown_text: str) -> str:
    """
    Apply narrow markdown-level hardcoded fixes for recurring parser edge cases.
    These are intentionally exact-pattern transforms so they do not affect
    unrelated tables.
    """
    lines = markdown_text.splitlines()
    rewritten: List[str] = []
    i = 0

    while i < len(lines):
        matched = False

        for hardcode in _STACKED_HEADER_COLLAPSES:
            expected_rows = hardcode["rows"]
            span = len(expected_rows)
            if i + span > len(lines):
                continue

            if all(_row_matches(lines[i + offset], expected_rows[offset]) for offset in range(span)):
                rewritten.append(_format_markdown_row(hardcode["replacement"]))
                i += span
                matched = True
                break

        if matched:
            continue

        rewritten.append(lines[i])
        i += 1

    return "\n".join(rewritten)
