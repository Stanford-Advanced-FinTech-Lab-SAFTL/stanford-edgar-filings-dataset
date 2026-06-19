from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.utils.bootstrap import (
    Any,
    Dict,
    Optional,
    defaultdict,
    json,
    ocr_logger,
    os,
    parse_stats_log_file_path,
    parse_stats_summary_file_path,
    pathlib,
    re,
)
from stanford_edgar_parser.utils.tokenizer import (
    _tokenizer_metadata,
    estimate_parser_tokens,
    normalize_form_type_for_stats,
)

def _pct(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((float(value) / float(total)) * 100.0, 4)


def _new_parse_stats(filepath: pathlib.Path) -> Dict[str, Any]:
    return {
        "input_path": str(filepath),
        "source_document_url": _state.CURRENT_SOURCE_DOCUMENT_URL,
        "accession_number": _current_filing_accession_number(),
        "tokenizer_info": _tokenizer_metadata(),
        "form_type": "",
        "form_category": "unknown",
        "format_token_counts": {bucket: 0 for bucket in ("sgml", "html", "xml", "pdf", "text", "other")},
        "format_char_counts": {bucket: 0 for bucket in ("sgml", "html", "xml", "pdf", "text", "other")},
        "format_section_counts": {bucket: 0 for bucket in ("sgml", "html", "xml", "pdf", "text", "other")},
        "pdf_page_count": 0,
        "parts": [],
    }


def _record_parse_stats_part(stats: Optional[Dict[str, Any]], source_format: str, text: str, label: str = "") -> None:
    if not stats or not text or not str(text).strip():
        return
    bucket = (source_format or "other").strip().lower()
    if bucket not in stats["format_token_counts"]:
        bucket = "other"
    token_count = estimate_parser_tokens(text)
    char_count = len(text)
    stats["format_token_counts"][bucket] += token_count
    stats["format_char_counts"][bucket] += char_count
    stats["format_section_counts"][bucket] += 1
    stats["parts"].append(
        {
            "source_format": bucket,
            "label": label or bucket,
            "token_count": token_count,
            "char_count": char_count,
        }
    )


def _record_parse_stats_pdf_pages(stats: Optional[Dict[str, Any]], page_count: int) -> None:
    if not stats:
        return
    stats["pdf_page_count"] = int(stats.get("pdf_page_count") or 0) + max(0, int(page_count or 0))


def _finalize_parse_stats(stats: Optional[Dict[str, Any]], document_text: str, form_type: str) -> Dict[str, Any]:
    stats = stats or _new_parse_stats(pathlib.Path(_state.CURRENT_PROCESSING_FILE or "unknown"))
    form_type = (form_type or stats.get("form_type") or "unknown").strip().upper() or "unknown"
    stats["form_type"] = form_type
    stats["form_category"] = normalize_form_type_for_stats(form_type)
    source_total = sum(int(v) for v in stats.get("format_token_counts", {}).values())
    stats["source_token_count"] = source_total
    stats["source_char_count"] = sum(int(v) for v in stats.get("format_char_counts", {}).values())
    stats["format_token_percentages"] = {
        key: _pct(int(value), source_total)
        for key, value in sorted(stats.get("format_token_counts", {}).items())
    }
    pdf_page_count = int(stats.get("pdf_page_count") or 0)
    pdf_output_token_count = int((stats.get("format_token_counts") or {}).get("pdf") or 0)
    stats["pdf_page_count"] = pdf_page_count
    stats["pdf_output_token_count"] = pdf_output_token_count
    stats["pdf_output_tokens_per_page"] = (
        round(float(pdf_output_token_count) / float(pdf_page_count), 4)
        if pdf_page_count > 0
        else None
    )
    stats["initial_document_token_count"] = estimate_parser_tokens(document_text or "")
    stats["initial_document_char_count"] = len(document_text or "")
    stats["tokenizer_info"] = _tokenizer_metadata()
    return stats


def _complete_parse_stats_for_output(
    stats: Optional[Dict[str, Any]],
    *,
    output_path: pathlib.Path,
    final_markdown: str,
    to_mmd: bool,
    disable_indentation: bool = False,
) -> Dict[str, Any]:
    stats = dict(stats or _new_parse_stats(pathlib.Path(_state.CURRENT_PROCESSING_FILE or "unknown")))
    stats["output_path"] = str(output_path)
    stats["to_mmd"] = bool(to_mmd)
    stats["disable_indentation"] = bool(disable_indentation)
    stats["final_token_count"] = estimate_parser_tokens(final_markdown or "")
    stats["final_char_count"] = len(final_markdown or "")
    stats["tokenizer_info"] = _tokenizer_metadata()
    return stats


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _write_parse_stats_outputs(stats: Dict[str, Any], output_path: pathlib.Path) -> None:
    sidecar_path = output_path.with_suffix(".parse_stats.json")
    sidecar_path.write_text(json.dumps(stats, indent=2, sort_keys=True), encoding="utf-8")

    # Per-filing sidecars are enough for normal/batch parsing. The global JSONL
    # accumulator can grow to many GB and summary refreshes can stall large runs.
    if _env_flag("SEC_PARSER_DISABLE_GLOBAL_STATS") or not _env_flag("SEC_PARSER_ENABLE_GLOBAL_STATS"):
        return

    with parse_stats_log_file_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(stats, sort_keys=True) + "\n")

    if _env_flag("SEC_PARSER_REFRESH_GLOBAL_STATS"):
        _refresh_parse_stats_summary()

_PARSE_STATS_SUMMARY_CACHE: Dict[str, Any] = {
    "path": "",
    "size": 0,
    "latest_by_input": {},
}

def _refresh_parse_stats_summary() -> None:
    latest_by_input: Dict[str, Dict[str, Any]]
    log_path = parse_stats_log_file_path
    cache_path = str(log_path)
    current_size = log_path.stat().st_size if log_path.exists() else 0
    cached_path = str(_PARSE_STATS_SUMMARY_CACHE.get("path") or "")
    cached_size = int(_PARSE_STATS_SUMMARY_CACHE.get("size") or 0)
    if cache_path == cached_path and current_size >= cached_size:
        latest_by_input = dict(_PARSE_STATS_SUMMARY_CACHE.get("latest_by_input") or {})
        read_from = cached_size
    else:
        latest_by_input = {}
        read_from = 0

    if log_path.exists():
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            if read_from:
                handle.seek(read_from)
            for line in handle:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                key = str(record.get("input_path") or record.get("accession_number") or record.get("output_path") or "")
                if key:
                    latest_by_input[key] = record
        _PARSE_STATS_SUMMARY_CACHE["path"] = cache_path
        _PARSE_STATS_SUMMARY_CACHE["size"] = current_size
        _PARSE_STATS_SUMMARY_CACHE["latest_by_input"] = dict(latest_by_input)
    else:
        _PARSE_STATS_SUMMARY_CACHE["path"] = cache_path
        _PARSE_STATS_SUMMARY_CACHE["size"] = 0
        _PARSE_STATS_SUMMARY_CACHE["latest_by_input"] = {}
    records = list(latest_by_input.values())
    total_filings = len(records)
    form_counts: Dict[str, int] = defaultdict(int)
    form_token_counts: Dict[str, int] = defaultdict(int)
    format_token_counts: Dict[str, int] = defaultdict(int)
    total_pdf_page_count = 0
    total_pdf_output_token_count = 0
    total_tokens = 0
    pdf_output_tokens_per_page_values: List[float] = []
    for record in records:
        form = str(record.get("form_category") or normalize_form_type_for_stats(str(record.get("form_type") or "")))
        tokens = int(record.get("final_token_count") or record.get("initial_document_token_count") or 0)
        form_counts[form] += 1
        form_token_counts[form] += tokens
        total_tokens += tokens
        pdf_page_count = int(record.get("pdf_page_count") or 0)
        pdf_output_token_count = int(
            record.get("pdf_output_token_count")
            or (record.get("format_token_counts") or {}).get("pdf")
            or 0
        )
        total_pdf_page_count += pdf_page_count
        total_pdf_output_token_count += pdf_output_token_count
        pdf_output_tokens_per_page = record.get("pdf_output_tokens_per_page")
        if pdf_output_tokens_per_page is None and pdf_page_count > 0:
            pdf_output_tokens_per_page = round(
                float(pdf_output_token_count) / float(pdf_page_count),
                4,
            )
        if pdf_output_tokens_per_page is not None:
            pdf_output_tokens_per_page_values.append(float(pdf_output_tokens_per_page))
        for bucket, value in (record.get("format_token_counts") or {}).items():
            format_token_counts[str(bucket)] += int(value or 0)
    median_pdf_output_tokens_per_page = None
    if pdf_output_tokens_per_page_values:
        sorted_values = sorted(pdf_output_tokens_per_page_values)
        midpoint = len(sorted_values) // 2
        median_pdf_output_tokens_per_page = (
            sorted_values[midpoint]
            if len(sorted_values) % 2 == 1
            else round((sorted_values[midpoint - 1] + sorted_values[midpoint]) / 2.0, 4)
        )
    summary = {
        "deduped_filing_count": total_filings,
        "total_final_token_count": total_tokens,
        "total_pdf_page_count": total_pdf_page_count,
        "total_pdf_output_token_count": total_pdf_output_token_count,
        "pdf_output_tokens_per_page": (
            round(float(total_pdf_output_token_count) / float(total_pdf_page_count), 4)
            if total_pdf_page_count > 0
            else None
        ),
        "median_pdf_output_tokens_per_page": median_pdf_output_tokens_per_page,
        "form_type_counts": dict(sorted(form_counts.items())),
        "form_type_count_percentages": {
            key: _pct(value, total_filings)
            for key, value in sorted(form_counts.items())
        },
        "form_type_token_percentages": {
            key: _pct(value, total_tokens)
            for key, value in sorted(form_token_counts.items())
        },
        "format_token_counts": dict(sorted(format_token_counts.items())),
        "format_token_percentages": {
            key: _pct(value, sum(format_token_counts.values()))
            for key, value in sorted(format_token_counts.items())
        },
    }
    parse_stats_summary_file_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def _print_parse_stats_summary(stats: Dict[str, Any]) -> None:
    final_tokens = int(stats.get("final_token_count") or stats.get("initial_document_token_count") or 0)
    form = stats.get("form_category") or stats.get("form_type") or "unknown"
    percentages = stats.get("format_token_percentages") or {}
    format_text = ", ".join(
        f"{bucket}={percentages.get(bucket, 0):.1f}%"
        for bucket in ("html", "xml", "sgml", "pdf", "text", "other")
        if percentages.get(bucket, 0) > 0
    ) or "none"
    print(f"[parse-stats] tokens={final_tokens:,} | form={form} | formats: {format_text}")


def _current_filing_accession_number() -> Optional[str]:
    current_path = _state.CURRENT_PROCESSING_FILE
    if not current_path or current_path == "Unknown":
        return None

    current_name = pathlib.Path(current_path).name
    match = re.search(r"\b\d{10}-\d{2}-\d{6}\b", current_name)
    if match:
        return match.group(0)

    stem = pathlib.Path(current_path).stem.strip()
    return stem or None

def _log_current_filing_ocr(ocr_reason: str) -> None:
    accession_number = _current_filing_accession_number()
    if not accession_number or accession_number in _state.CURRENT_OCR_LOGGED_FILINGS:
        return

    filing_name = pathlib.Path(_state.CURRENT_PROCESSING_FILE).name
    _state.CURRENT_OCR_LOGGED_FILINGS.add(accession_number)
    ocr_logger.info(
        f"ACCESSION: {accession_number}\n"
        f"FILING: {filing_name}\n"
        f"OCR_REASON: {ocr_reason}"
    )

__all__ = [name for name in globals() if not name.startswith("__")]
