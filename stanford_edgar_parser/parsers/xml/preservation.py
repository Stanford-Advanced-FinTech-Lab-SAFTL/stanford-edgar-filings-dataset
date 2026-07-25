"""Presentation-aware safety net for fields omitted by form-specific XML renderers."""

from __future__ import annotations

import html
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any, Iterable

from bs4 import BeautifulSoup, Tag


_EXCLUDED_NAMES = {
    "ccc",
    "contents",
    "documentmimeblock",
    "filercikccc",
    "filerccc",
    "nonpublicdocument",
    "password",
    "schemalocation",
    "schemaversion",
    "testorlive",
}
_STRUCTURAL_NAMES = {
    "edgarsubmission",
    "formdata",
    "headerdata",
}
_RATIO_FIELDS = {
    "percentagedailyliquidassets",
    "percentageweeklyliquidassets",
    "percentageofmoneymarketfundnetassets",
    "sevendaygrossyield",
    "sevendaygrossyieldvalue",
    "sevendaynetyield",
    "sevendaynetyieldvalue",
    "yieldofthesecurity",
    "yieldofthesecurityasofreportingdate",
}
_ACRONYMS = {
    "cik": "CIK",
    "crd": "CRD",
    "cusip": "CUSIP",
    "figi": "FIGI",
    "id": "ID",
    "isin": "ISIN",
    "lei": "LEI",
    "sec": "SEC",
    "usd": "USD",
}
_WORD_OVERRIDES = {
    "aggrmt": "Agreement",
    "appr": "Appreciation",
    "cat": "Category",
    "cd": "Code",
    "ctrld": "Controlled",
    "desc": "Description",
    "deriv": "Derivative",
    "dt": "Date",
    "exp": "Expiration",
    "intrst": "Interest",
    "invst": "Investment",
    "liabs": "Liabilities",
    "mon": "Month",
    "oth": "Other",
    "pctr": "Percentage",
    "pct": "Percentage",
    "pymnt": "Payment",
    "rt": "Rate",
    "tran": "Transaction",
    "val": "Value",
}


def _local_name(name: Any) -> str:
    return str(name or "").rsplit("}", 1)[-1].split(":", 1)[-1]


def _normalized(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = unicodedata.normalize("NFKC", text).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _compact(value: Any) -> str:
    return re.sub(r"[^\w]+", "", _normalized(value), flags=re.UNICODE)


def _informative(value: Any) -> bool:
    compact = _compact(value)
    return bool(compact) and compact not in {"live", "none", "null"}


def _decimal(value: Any) -> Decimal | None:
    text = html.unescape(str(value or "")).strip()
    if not re.fullmatch(r"\(?\s*[-+]?\$?\s*\d[\d,]*(?:\.\d+)?\s*%?\s*\)?", text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[$,%()\s]", "", text).replace(",", "")
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -number if negative and number > 0 else number


def _output_numbers(output: str) -> set[Decimal]:
    values = set()
    for token in re.findall(
        r"(?<![\w])\(?\s*[-+]?\$?\s*\d[\d,]*(?:\.\d+)?\s*%?\s*\)?(?![\w])",
        output,
    ):
        if (number := _decimal(token)) is not None:
            values.add(number)
    return values


def _humanize(name: str) -> str:
    name = _local_name(name)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    text = re.sub(r"[_\-]+", " ", text)
    words = []
    for word in text.split():
        lowered = word.casefold()
        month_match = re.fullmatch(r"mon(\d+)", lowered)
        if month_match:
            words.extend(("Month", month_match.group(1)))
            continue
        words.append(
            _ACRONYMS.get(
                lowered,
                _WORD_OVERRIDES.get(lowered, word.capitalize()),
            )
        )
    return " ".join(words)


def _meaningful_attribute(name: Any, value: Any) -> bool:
    raw_name = str(name or "")
    local = _local_name(raw_name).casefold()
    normalized = _normalized(value)
    return bool(
        normalized
        and _informative(value)
        and not raw_name.casefold().startswith("xmlns")
        and local not in _EXCLUDED_NAMES
        and "schema" not in local
        and not normalized.startswith(("http://www.sec.gov/", "https://www.sec.gov/"))
    )


def _value_preserved(
    field_name: str,
    value: str,
    normalized_output: str,
    compact_output: str,
    output_numbers: set[Decimal],
) -> bool:
    normalized = _normalized(value)
    normalized_field = field_name.casefold()
    label = _normalized(_humanize(field_name))
    if normalized and label and re.search(
        rf"{re.escape(label)}.{{0,16}}{re.escape(normalized)}(?=\W|$)",
        normalized_output,
    ):
        return True
    boolean_field = (
        normalized_field.startswith(("has", "is"))
        or normalized_field.endswith(("flag", "indicator"))
    )
    if boolean_field and normalized in {"1", "true", "y", "yes"}:
        return any(
            marker in normalized_output
            for marker in (f"{label} yes", f"{label} [x]", f"{label}: yes")
        )
    if boolean_field and normalized in {"0", "false", "n", "no"}:
        return any(
            marker in normalized_output
            for marker in (f"{label} no", f"{label} [ ]", f"{label}: no")
        )
    if len(normalized) >= 3 and normalized in normalized_output:
        return True
    compact = _compact(value)
    if len(compact) >= 4 and compact in compact_output:
        return True
    number = _decimal(value)
    if number is not None:
        if number in output_numbers:
            return True
        if field_name.casefold() in _RATIO_FIELDS and number * 100 in output_numbers:
            return True
    if normalized in {"true", "false"}:
        truth_words = ("yes", "[x]") if normalized == "true" else ("no", "[ ]")
        return any(f"{label} {word}" in normalized_output for word in truth_words)
    return False


def _direct_fields(node: Tag) -> list[tuple[str, str]]:
    fields = []
    for attribute, raw_value in node.attrs.items():
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        for value in values:
            if _meaningful_attribute(attribute, value):
                fields.append((_local_name(attribute), str(value).strip()))
    for child in node.find_all(recursive=False):
        if child.find(recursive=False):
            continue
        text = child.get_text(" ", strip=True)
        if (
            text
            and _informative(text)
            and _local_name(child.name).casefold() not in _EXCLUDED_NAMES
        ):
            fields.append((_local_name(child.name), text))
    return fields


def _indexed_name(node: Tag) -> str:
    name = _local_name(node.name)
    if not node.parent or not isinstance(node.parent, Tag):
        return _humanize(name)
    siblings = [
        sibling
        for sibling in node.parent.find_all(name=node.name, recursive=False)
        if isinstance(sibling, Tag)
    ]
    if len(siblings) <= 1:
        return _humanize(name)
    return f"{_humanize(name)} {siblings.index(node) + 1}"


def _context(node: Tag) -> str:
    ancestors = []
    current: Tag | None = node
    while current is not None and isinstance(current, Tag):
        name = _local_name(current.name)
        if name.casefold() not in _STRUCTURAL_NAMES and name != "[document]":
            ancestors.append(_indexed_name(current))
        current = current.parent if isinstance(current.parent, Tag) else None
    return " › ".join(reversed(ancestors[-4:])) or "Filing"


def _contextual_nodes(document: BeautifulSoup):
    """Yield nodes with stable repeated-record contexts in linear time."""

    def walk(parent: Tag, ancestors: tuple[str, ...]):
        children = [
            child
            for child in parent.find_all(recursive=False)
            if isinstance(child, Tag)
        ]
        totals = Counter(_local_name(child.name) for child in children)
        seen = Counter()
        for child in children:
            name = _local_name(child.name)
            seen[name] += 1
            label = _humanize(name)
            if totals[name] > 1:
                label = f"{label} {seen[name]}"
            lineage = ancestors
            if name.casefold() not in _STRUCTURAL_NAMES and name != "[document]":
                lineage = (*ancestors, label)
            yield child, " › ".join(lineage[-4:]) or "Filing"
            yield from walk(child, lineage)

    yield from walk(document, ())


def _escape_cell(value: str) -> str:
    value = html.escape(value, quote=False)
    value = re.sub(r"\r\n?|\n", "<br>", value)
    return value.replace("|", "&#124;")


def _coerce_documents(xml_input: Any) -> list[BeautifulSoup]:
    values: Iterable[Any]
    if isinstance(xml_input, (list, tuple)):
        values = xml_input
    else:
        values = [xml_input]
    documents = []
    for value in values:
        if isinstance(value, BeautifulSoup):
            documents.append(value)
        elif isinstance(value, Tag):
            documents.append(BeautifulSoup(str(value), "lxml-xml"))
        elif isinstance(value, (str, bytes)):
            documents.append(BeautifulSoup(value, "lxml-xml"))
    return documents


def append_unrendered_xml_fields(output: str, xml_input: Any) -> str:
    """Append contextual rows only for source field groups with an omitted value."""
    normalized_output = _normalized(output)
    compact_output = _compact(output)
    numbers = _output_numbers(output)
    missing_groups: list[tuple[str, list[tuple[str, str]]]] = []
    preservation_cache: dict[tuple[str, str], bool] = {}

    for document in _coerce_documents(xml_input):
        for node, context in _contextual_nodes(document):
            fields = _direct_fields(node)
            if not fields:
                continue
            missing = []
            for name, value in fields:
                key = (name, value)
                preserved = preservation_cache.get(key)
                if preserved is None:
                    preserved = _value_preserved(
                        name,
                        value,
                        normalized_output,
                        compact_output,
                        numbers,
                    )
                    preservation_cache[key] = preserved
                if not preserved:
                    missing.append((name, value))
            if missing:
                missing_groups.append((context, list(dict.fromkeys(missing))))

    if not missing_groups:
        return output

    deduplicated: dict[str, list[list[tuple[str, str]]]] = defaultdict(list)
    seen = set()
    for context, fields in missing_groups:
        key = (context, tuple(fields))
        if key in seen:
            continue
        seen.add(key)
        deduplicated[context].append(fields)

    lines = [
        output.rstrip(),
        "",
        "### Additional Filing Details",
        "",
        "The following source fields supplement the form-specific presentation above.",
        "",
        "| Context | Source fields |",
        "|:--|:--|",
    ]
    for context, groups in deduplicated.items():
        for fields in groups:
            details = "<br>".join(
                f"**{_escape_cell(_humanize(name))}:** {_escape_cell(value)}"
                for name, value in fields
            )
            lines.append(f"| {_escape_cell(context)} | {details} |")
    return "\n".join(lines).rstrip() + "\n"


def preserve_xml_fields(parser):
    """Decorate a form renderer with contextual preservation for omitted XML fields."""

    @wraps(parser)
    def wrapped(xml_input, *args, **kwargs):
        output = parser(xml_input, *args, **kwargs)
        if not isinstance(output, str):
            return output
        return append_unrendered_xml_fields(output, xml_input)

    return wrapped
