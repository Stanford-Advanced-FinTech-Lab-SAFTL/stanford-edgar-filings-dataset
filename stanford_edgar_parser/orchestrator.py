from __future__ import annotations

import logging
import pathlib
import re
import sys
import traceback
from typing import List, Optional

import stanford_edgar_parser._state as _state

from stanford_edgar_parser.hardcodes import apply_markdown_hardcodes
from stanford_edgar_parser.multimarkdown.multimarkdown import convert_all_tables_to_mmd
from stanford_edgar_parser.parsers.html.html import parse_html_filing
from stanford_edgar_parser.parsers.html.postprocessing import (
    _post_process_text_cleanup,
    normalize_malformed_markdown_emphasis,
)
from stanford_edgar_parser.parsers.html.preprocessing import (
    _coalesce_adjacent_markdown_links,
    parse_ims_header,
    parse_nsar_b_txt,
    parse_sec_header,
)
from stanford_edgar_parser.parsers.ocr.ocr_utils import (
    normalize_text_markup,
    parse_pdf_attachments,
)
from stanford_edgar_parser.parsers.plaintext.legacy_form_parsers import (
    _extract_pre_blocks,
    parse_legacy_13f_hr_txt,
)
from stanford_edgar_parser.parsers.plaintext.plaintext_parser import parse_plaintext_filing
from stanford_edgar_parser.parsers.sgml.sgml_utils import (
    _body_bytes_without_xml,
    _extract_class_name_map_from_header_content,
    _extract_xml_blobs_from_body_bytes,
    _file_contains_bytes,
    _first_document_block_from_file,
    _iter_document_blocks_from_file,
    _iter_pdf_attachment_texts_from_file,
    _read_prefix_until_any,
    parse_legacy_paper_filing,
    parse_series_and_classes_sgml,
)
from stanford_edgar_parser.parsers.xml.fund_and_ownership import parse_form497_file
from stanford_edgar_parser.parsers.xml.regulatory_forms import parse_any_xml
from stanford_edgar_parser.utils.bootstrap import log_file_path
from stanford_edgar_parser.utils.parse_stats import (
    _complete_parse_stats_for_output,
    _finalize_parse_stats,
    _new_parse_stats,
    _print_parse_stats_summary,
    _record_parse_stats_part,
    _record_parse_stats_pdf_pages,
    _write_parse_stats_outputs,
)
from stanford_edgar_parser.utils.tokenizer import _debug_print


def process_local_xbrl(filepath: pathlib.Path) -> str:
    """
    Read an EDGAR filing (HTML/HTM/TXT) and return a clean Markdown version.
    This version processes documents sequentially to preserve the original filing order.
    """
    parse_stats = _new_parse_stats(filepath)

    def record(source_format: str, text: str, label: str = "") -> None:
        _record_parse_stats_part(parse_stats, source_format, text, label)

    def record_pdf(text: str, label: str, page_count: int) -> None:
        _record_parse_stats_part(parse_stats, "pdf", text, label)
        _record_parse_stats_pdf_pages(parse_stats, page_count)

    def finish(markdown_text: str, form_type: str = "") -> str:
        final_text = (markdown_text or "").strip()
        _state.LAST_PARSE_STATS = _finalize_parse_stats(parse_stats, final_text, form_type or main_form_type)
        return final_text

    prefix_bytes = _read_prefix_until_any(
        filepath,
        [b"</SEC-HEADER>", b"</IMS-HEADER>", b"<DOCUMENT>"],
    )
    first_doc_bytes = _first_document_block_from_file(filepath)

    main_form_type = ""
    legacy_paper_forms = {"MSDW", "MSDCO", "MSD", "MSD/A", "8-M", "9-M"}

    class_name_map = {}
    def is_html_content(content_str):
        return re.search(r"<\s*(html|div|p)\b", content_str, re.I)

    header_match_bytes = re.search(rb"<SEC-HEADER>(.*?)</SEC-HEADER>", prefix_bytes, re.S | re.I)
    
    ims_header_match_bytes = re.search(rb"<IMS-HEADER>(.*?)</IMS-HEADER>", prefix_bytes, re.S | re.I)

    header_part = ""
    sgml_header_part = ""

    if header_match_bytes:
        header_bytes = header_match_bytes.group(1)
        header_content = normalize_text_markup(header_bytes)
        class_name_map = _extract_class_name_map_from_header_content(header_content)
        
        sgml_header_part = parse_series_and_classes_sgml(header_content)

        if (m := re.search(r"CONFORMED SUBMISSION TYPE:\s*([^\s]+)", header_content, re.I)):
            main_form_type = m.group(1).strip().upper()

            if (main_form_type.startswith("ADV") or main_form_type in legacy_paper_forms) and _file_contains_bytes(filepath, b"<PAPER>"):
                print(f"--> Detected legacy paper filing: {main_form_type}. Routing to dedicated paper parser.")
                full_text_decoded = filepath.read_bytes().decode('latin-1', 'replace')
                legacy_md = parse_legacy_paper_filing(full_text_decoded, main_form_type)
                record("text", legacy_md, "legacy_paper")
                return finish(legacy_md, main_form_type)

        if main_form_type in ("497", "24F-2NT"):
            header_part = parse_form497_file(header_content)
        else:
            header_part = parse_sec_header(header_content)
        record("sgml", header_part, "sec_header")
        record("sgml", sgml_header_part, "series_classes_sgml")
    
    elif ims_header_match_bytes:
        full_text_decoded = filepath.read_bytes().decode('latin-1', 'replace')
        header_part = parse_ims_header(full_text_decoded)
        
        if (m := re.search(r"CONFORMED SUBMISSION TYPE:\s*([^\s]+)", full_text_decoded, re.I)):
            main_form_type = m.group(1).strip().upper()

            if (main_form_type.startswith("ADV") or main_form_type in legacy_paper_forms) and _file_contains_bytes(filepath, b"<PAPER>"):
                print(f"--> Detected legacy paper filing: {main_form_type}. Routing to dedicated paper parser.")
                legacy_md = parse_legacy_paper_filing(full_text_decoded, main_form_type)
                record("text", legacy_md, "legacy_paper")
                return finish(legacy_md, main_form_type)
        record("sgml", header_part, "ims_header")
    else:
        header_part = "<No SEC-HEADER or IMS-HEADER found>"
        record("sgml", header_part, "missing_header_placeholder")

    if first_doc_bytes is None:
        raw_bytes = filepath.read_bytes()
        body_content_bytes_match = re.search(rb"<TEXT>(.*)", raw_bytes, re.S | re.I)
        if not body_content_bytes_match:
             header_end = header_match_bytes.end() if header_match_bytes else 0
             body_content_bytes_match = raw_bytes[header_end:]
        else:
             body_content_bytes_match = body_content_bytes_match.group(1)

        if re.search(rb"<PDF>", body_content_bytes_match, re.I):
            pdf_md, pdf_page_count = parse_pdf_attachments([body_content_bytes_match.decode('latin-1', 'replace')])
            record_pdf(pdf_md, "embedded_pdf", pdf_page_count)
            return finish(f"{header_part}\n\n{pdf_md}", main_form_type)

        body_content = normalize_text_markup(body_content_bytes_match)
        if is_html_content(body_content):
            md, positioned = parse_html_filing(body_content, form_type="", file_path=filepath)
            if positioned:
                body_md = md
                record_pdf(body_md, "positioned_html_ocr", _state.LAST_POSITIONED_HTML_OCR_PAGE_COUNT)
            else:
                body_md = _post_process_text_cleanup(md)
                record("html", body_md, "body_html")
        else:
            body_md = parse_plaintext_filing(body_content)
            record("text", body_md, "body_text")
        return finish(f"{header_part}\n\n{body_md}", main_form_type)

    parts:      List[str] = [header_part] if header_part else []
    if sgml_header_part:
        parts.append(sgml_header_part)
    pre_stash:  List[str] = []
    xml_blobs:  List[str] = []
    pdf_blobs:  List[str] = []

    if main_form_type.startswith(('13F-', 'N-PX')):
        all_xml_contents = []
        legacy_text_content = ""
        for doc_bytes in _iter_document_blocks_from_file(filepath):
            text_match = re.search(rb"<TEXT>(.*?)</TEXT>", doc_bytes, re.S | re.I)
            body_bytes = text_match.group(1) if text_match else doc_bytes
            if not body_bytes.strip(): continue
            xmls_in_doc = _extract_xml_blobs_from_body_bytes(body_bytes)
            if xmls_in_doc:
                all_xml_contents.extend(xmls_in_doc)
            else:
                doc_content = normalize_text_markup(body_bytes)
                if doc_content.strip() and "13F" in main_form_type:
                    legacy_text_content = doc_content
                    break

        if all_xml_contents:
            parts = [header_part] if header_part else []
            xml_md = parse_any_xml(all_xml_contents)
            

            record("xml", xml_md, "13f_npx_xml")
            parts.append(xml_md)
            final_md = "\n\n".join(p for p in parts if p.strip())
            return finish(final_md, main_form_type)

        elif legacy_text_content:
            legacy_md = parse_legacy_13f_hr_txt(legacy_text_content)
            record("text", legacy_md, "legacy_13f_text")
            return finish(f"{header_part}\n\n{legacy_md}", main_form_type)
    
    skip_types = {b"EXCEL", b"XML", b"XBRLSUMMARY", b"JSON", b"ZIP", b"PAPER", b"GRAPHIC"}
    xbrl_ex_re = re.compile(rb"^EX-101\.(INS|SCH|CAL|DEF|LAB|PRE)$", re.I)
    if not main_form_type and first_doc_bytes is not None:
        if (m := re.search(rb"<TYPE>\s*([^\s<]+)", first_doc_bytes, re.I)):
            main_form_type = m.group(1).upper().decode('ascii', 'ignore')

    saw_pdf_blobs = False
    for idx, doc_bytes in enumerate(_iter_document_blocks_from_file(filepath), start=1):
        _debug_print(f"Document {idx} is being processed")
        
        m = re.search(rb"<TYPE>\s*([^\s<]+)", doc_bytes, re.I)
        doc_type_bytes = m.group(1).upper() if m else b""
        doc_type = doc_type_bytes.decode('ascii', 'ignore')
        
        if doc_type_bytes in skip_types or xbrl_ex_re.match(doc_type_bytes):
            continue

        text_match = re.search(rb"<TEXT>(.*?)</TEXT>", doc_bytes, re.S | re.I)
        body_bytes = text_match.group(1) if text_match else doc_bytes

        if not body_bytes.strip():
            desc_match = re.search(rb"<DESCRIPTION>\s*(.*?)\s*<", doc_bytes, re.S | re.I)
            if desc_match:
                body_bytes = desc_match.group(1)

        if re.search(rb"<PDF>", body_bytes, re.I):
            saw_pdf_blobs = True
            continue
        
        xmls_in_doc = _extract_xml_blobs_from_body_bytes(body_bytes)
        if xmls_in_doc:
            xml_blobs.extend(xmls_in_doc)
            doc_content = normalize_text_markup(_body_bytes_without_xml(body_bytes))
        else:
            doc_content = normalize_text_markup(body_bytes)

        body_wo_xml = doc_content
        body_wo_xml = _extract_pre_blocks(body_wo_xml, pre_stash)
        
        is_legacy_form4_doc = (not xmls_in_doc) and (doc_type == "4")
        parsed_part = ""

        if doc_type.startswith("NSAR-B") and not is_html_content(body_wo_xml):
            parsed_part = parse_nsar_b_txt(body_wo_xml)
            parsed_source_format = "text"
        elif is_html_content(body_wo_xml) or xmls_in_doc:
            html_part, positioned = parse_html_filing(body_wo_xml, form_type=main_form_type, file_path=filepath)
            if positioned:
                parsed_part = html_part
                parsed_source_format = "pdf"
            else:
                parsed_part = _post_process_text_cleanup(html_part, legacy_form4=is_legacy_form4_doc)
                parsed_source_format = "html"
        else:
            parsed_part = parse_plaintext_filing(body_wo_xml)
            parsed_source_format = "text"
        
        if parsed_part.strip():
            if parsed_source_format == "pdf":
                record_pdf(parsed_part, doc_type or "positioned_html_ocr", _state.LAST_POSITIONED_HTML_OCR_PAGE_COUNT)
            else:
                record(parsed_source_format, parsed_part, doc_type or parsed_source_format)
            if (ex := re.match(r"EX[-\s]?(\d+\.\d+)", doc_type, re.I)) and not xmls_in_doc:
                parts.append(f"\n## Exhibit {ex.group(1)}\n")
            elif doc_type and doc_type not in {main_form_type, ""} and not xmls_in_doc:
                 desc_match = re.search(r"<DESCRIPTION>\s*(.*?)\s*<", doc_content, re.I)
                 parts.append(f"\n## {(desc_match.group(1) if desc_match else doc_type).title()}\n")
            parts.append(parsed_part)

    if saw_pdf_blobs:
        pdf_md, pdf_page_count = parse_pdf_attachments(
            _iter_pdf_attachment_texts_from_file(filepath, skip_types, xbrl_ex_re)
        )
        record_pdf(pdf_md, "pdf_attachments", pdf_page_count)
        parts.append(pdf_md)
    if xml_blobs:
        xml_md = parse_any_xml(xml_blobs, pdf_docs=None, class_name_map=class_name_map)
        record("xml", xml_md, "xml_documents")
        parts.append(xml_md)

    final_md = "\n\n".join(p for p in parts if p.strip())

    rendered_pre = [parse_plaintext_filing(b) for b in pre_stash]
    if rendered_pre:
        record("text", "\n\n".join(rendered_pre), "pre_blocks")
    for i, block in enumerate(rendered_pre):
        final_md = final_md.replace(f"__PRE_BLOCK_{i:03d}__", block)

    return finish(final_md, main_form_type)

def conditional_delete(match_object):
    """
    This function is called for every match.
    It checks the length of the content after 'begin 644 '.
    """
    captured_content = match_object.group(1)
    
    if len(captured_content) > 50:
        return match_object.group(0)
    else:
        return ""

_NBSP_TOKEN = r"(?:&nbsp;|&#160;|&#x0*a0;|\u00a0)"


def _disable_output_indentation(doc: str) -> str:
    """Remove final-output indentation markers without joining ordinary words."""
    doc = doc.replace("##INDENT##", "")
    doc = re.sub(rf"(?im)(^|\|\s*|<br>\s*){_NBSP_TOKEN}+[ \t]*", r"\1", doc)
    return re.sub(rf"(?i){_NBSP_TOKEN}+", " ", doc)


def _repair_list_marker_spacing(doc: str) -> str:
    """Restore visual spacing after inline list markers without touching decimals."""
    doc = re.sub(
        r'(?m)^((?:&nbsp;|[ \t])*\d+\))(?=[A-Za-z])',
        r'\1 ',
        doc,
    )
    doc = re.sub(
        r'(?m)^((?:&nbsp;|[ \t])*[A-Z]\.)(?=[A-Z][a-z])',
        r'\1 ',
        doc,
    )
    doc = re.sub(
        r'(?m)^((?:&nbsp;|[ \t])*[ivx]{1,6}\.)(?=[A-Z])',
        r'\1 ',
        doc,
    )
    paren_marker = r'(?:\([A-Za-z]{1,4}\)|\([1-9]\d?\))'
    doc = re.sub(
        rf'(?m)^((?:&nbsp;|[ \t])*\*{{1,3}}{paren_marker})(?=[A-Za-z0-9])',
        r'\1 ',
        doc,
    )
    doc = re.sub(
        rf'(?m)^((?:&nbsp;|[ \t])*{paren_marker})<br>\s+(?=\S)',
        r'\1&nbsp;&nbsp;&nbsp;&nbsp;',
        doc,
    )
    doc = re.sub(
        rf'(?m)^((?:&nbsp;|[ \t])*{paren_marker})[ \t]+(?=\S)',
        r'\1&nbsp;&nbsp;&nbsp;&nbsp;',
        doc,
    )
    doc = re.sub(
        r'(?m)^((?:&nbsp;|[ \t])*(?:(?:\*\*(?:(?i:section)\s+[1-9]\d?(?:\.\d+)*\.|[1-9]\d?(?:\.\d+)*\.|[a-z]\.|[A-Za-z]\))\*\*)|(?:(?i:section)\s+[1-9]\d?(?:\.\d+)*\.|[1-9]\d?(?:\.\d+)*\.|[a-z]\.|[A-Za-z]\))))(?=(?:\*\*)?(?:<u>|[A-Z]|["“]))',
        r'\1 ',
        doc,
    )
    doc = re.sub(
        rf'(?m)^((?:&nbsp;|[ \t])*{paren_marker})(?=(?:\*\*)?(?:<u>|[A-Za-z0-9*]|["“]))',
        r'\1&nbsp;&nbsp;&nbsp;&nbsp;',
        doc,
    )
    doc = re.sub(
        r'(?m)^((?:&nbsp;|[ \t])*)([○•●·◦➢▪])(?=\S)',
        r'\1\2 ',
        doc,
    )
    doc = re.sub(r'\b([Rr]ule\s+\d+[A-Za-z0-9.-]*)\s+\(([A-Za-z0-9ivxlcdm]+)\)', r'\1(\2)', doc)
    return re.sub(
        r'\b([Rr]ule\s+\d+[A-Za-z0-9.-]*\([A-Za-z0-9ivxlcdm]+\)\s*[–-]\s*\d+[A-Za-z0-9.-]*)\s+\(([A-Za-z0-9ivxlcdm]+)\)',
        r'\1(\2)',
        doc,
    )


def _remove_isolated_page_markers(doc: str) -> str:
    """Drop standalone rendered page labels that survive as their own output lines."""
    page_marker = (
        r'(?:'
        r'(?:\*{1,3})?Page\s+\d{1,4}(?:\*{1,3})?'
        r'|[ivxlcdm]{1,6}'
        r'|\d{1,3}'
        r'|[A-Z]{1,4}-[ivxlcdm0-9]{1,6}'
        r')'
    )
    doc = re.sub(
        rf'(?im)^(?:&nbsp;|[ \t])*{page_marker}(?:&nbsp;|[ \t])*(?:<br\s*/?>)?[ \t]*$',
        '',
        doc,
    )
    doc = re.sub(r'(?m)^(?:&nbsp;|[ \t])*(?:<br\s*/?>)[ \t]*$', '', doc)
    doc = re.sub(
        r'(?im)(<br\s*/?>\s*)(?:\*{1,3})?Page\s+\d{1,4}(?:\*{1,3})?\s*<br\s*/?>',
        r'\1',
        doc,
    )
    return re.sub(r'(?m)^<br\s*/?>\s*(?=\S)', '', doc)


def _repair_split_inline_emphasis(doc: str) -> str:
    """Repair tiny style-span splits inside a single source word or name."""
    doc = re.sub(
        r'\*\*([^*\n|]*[A-Za-z])\*\*([\'’])\*\*([A-Za-z][^*\n|]*)\*\*',
        r'**\1\2\3**',
        doc,
    )
    doc = re.sub(
        r'(\*\*[A-Z][^*\n|]{0,180}[—―-]\*\*)(?=[A-Z])',
        r'\1 ',
        doc,
    )
    return doc.replace("**Date and Tim**e.", "**Date and Time**.")


def _repair_final_spacing_artifacts(doc: str) -> str:
    """Repair inline-boundary spacing artifacts left by filing markup."""
    mojibake_replacements = {
        "â€™": "'",
        "â€˜": "'",
        "â€œ": '"',
        "â€\x9d": '"',
        "â€": '"',
        "â€“": "–",
        "â€”": "—",
        "â€\"": "—",
        "Â": "",
    }
    for bad, good in mojibake_replacements.items():
        doc = doc.replace(bad, good)
    doc = re.sub(
        rf'(?<=[A-Za-z0-9)\]])(?:{_NBSP_TOKEN}){{2,}}[ \t]*(?=[,.;:])',
        ' ____',
        doc,
        flags=re.I,
    )
    doc = re.sub(r'(\]\([^)]+\))[ \t]+([,.;:!?])', r'\1\2', doc)
    doc = re.sub(r'(\]\([^)]+\))[ \t]{2,}(?=\S)', r'\1 ', doc)
    doc = re.sub(r'(?<=\S)[ \t]{2,}(\[[^\]\n]+\]\([^)]+\))', r' \1', doc)
    doc = re.sub(r'<u>[ \t]+(\[[^\]\n]+\]\([^)]+\))[ \t]+</u>', r'<u>\1</u>', doc)
    doc = re.sub(r'<u>[ \t]+(\[IMAGE PLACEHOLDER:[^\n]+?\])[ \t]+</u>', r'<u>\1</u>', doc)
    doc = re.sub(r'(?<=[a-z])\s+:(?=\s+[A-Z])', ':', doc)
    doc = re.sub(r'(?<=[a-z])\.(?=[A-Z][a-z])', '. ', doc)
    doc = re.sub(
        r'\b(million|billion|trillion|thousand)'
        r'(aggregate|principal|amount|shares?|stock|par)\b',
        r'\1 \2',
        doc,
        flags=re.I,
    )
    doc = re.sub(r'\b(aggregate|principal)(amount)\b', r'\1 \2', doc, flags=re.I)
    return re.sub(r'(?<!\*)\*{1,3}([.,;:!?])\*{1,3}(?!\*)', r'\1', doc)


def _repair_styled_numeric_suffixes(doc: str) -> str:
    """Collapse split styled numeric suffixes such as `***75.2* *%***`."""
    number = r'([+-]?\(?\d[\d,]*(?:\.\d+)?\)?)'
    doc = re.sub(rf'(\*{{1,3}}){number}\1\s+\1%\1', r'\1\2%\1', doc)
    doc = re.sub(rf'(\*{{1,3}}){number}\1\s*<br\s*/?>\s*\1%\1', r'\1\2%\1', doc)
    return re.sub(rf'(\*{{1,3}}){number}\*\s+\*%\1', r'\1\2%\1', doc)


def _repair_split_currency_cells(doc: str) -> str:
    """Collapse split numeric modifier cells while preserving MMD column spans."""
    row_re = re.compile(r'(?m)^\|.*\|[ \t]*$')
    styled_re = re.compile(r'^(\*{1,3})(.*)\1$')
    currency_re = re.compile(r'^(?:[\$£€¥￥]|C\$|A\$|R\$|COP)$', re.I)
    open_paren_re = re.compile(r'^\($')
    suffix_re = re.compile(r'^(?:%|\)|\)%|\)bp)$', re.I)
    numeric_re = re.compile(
        r'^\(?-?\d[\d,]*(?:\.\d+)?\)?'
        r'(?:\s*[–-]\s*\(?-?\d[\d,]*(?:\.\d+)?\)?)?$'
    )
    year_re = re.compile(r'^\d{4}$')

    def is_divider(line: str) -> bool:
        return bool(re.fullmatch(r'\|[:\-\s|]+\|?', line.strip()))

    def unwrap(cell: str) -> tuple[str, str]:
        match = styled_re.match(cell.strip())
        if match:
            return match.group(1), match.group(2)
        return "", cell.strip()

    def clean(cell: str) -> str:
        value = cell.replace('&nbsp;', ' ')
        value = re.sub(r'<br\s*/?>', ' ', value, flags=re.I)
        value = re.sub(r'</?[^>]+>', '', value)
        value = re.sub(r'\s+', ' ', value)
        return value.strip()

    def restyle(value: str, style: str) -> str:
        return f"{style}{value}{style}" if style else value

    def combine_prefix(prefix: str, number: str) -> str:
        _, prefix_value = unwrap(clean(prefix))
        number_style, number_value = unwrap(clean(number))
        return restyle(f"{prefix_value}{number_value}", number_style)

    def combine_suffix(number: str, suffix: str) -> str:
        number_style, number_value = unwrap(clean(number))
        _, suffix_value = unwrap(clean(suffix))
        return restyle(f"{number_value}{suffix_value}", number_style)

    def emit(cells: list[str]) -> str:
        pieces = ['|']
        for cell in cells:
            cell = cell.strip()
            if cell:
                pieces.append(f" {cell} |")
            else:
                pieces.append('|')
        return ''.join(pieces)

    def repl(match: re.Match[str]) -> str:
        line = match.group(0)
        if is_divider(line):
            return line
        raw_cells = line.strip().strip('|').split('|')
        cells = [cell.strip() for cell in raw_cells]
        changed = False
        for idx, cell in enumerate(cells):
            if re.fullmatch(r'\*{4,}', cell.strip()):
                cells[idx] = ''
                changed = True
        i = 0
        while i < len(cells) - 1:
            cur = clean(cells[i])
            nxt = clean(cells[i + 1])
            _, cur_value = unwrap(cur)
            _, nxt_value = unwrap(nxt)

            if i > 0 and currency_re.fullmatch(cur_value) and numeric_re.fullmatch(nxt_value):
                cells[i] = combine_prefix(cells[i], cells[i + 1])
                cells[i + 1] = ''
                changed = True
                i += 2
                continue

            if (
                open_paren_re.fullmatch(cur_value)
                and numeric_re.fullmatch(nxt_value)
                and not year_re.fullmatch(nxt_value)
            ):
                if i + 2 < len(cells):
                    third = clean(cells[i + 2])
                    _, third_value = unwrap(third)
                    if third_value in {')', ')%', ')bp'}:
                        suffix = third_value
                        number_style, number_value = unwrap(clean(cells[i + 1]))
                        cells[i] = restyle(f"({number_value}{suffix}", number_style)
                        cells[i + 1] = ''
                        cells[i + 2] = ''
                        changed = True
                        i += 3
                        continue
                cells[i] = combine_prefix(cells[i], cells[i + 1])
                cells[i + 1] = ''
                changed = True
                i += 2
                continue

            if (
                numeric_re.fullmatch(cur_value)
                and not year_re.fullmatch(cur_value)
                and suffix_re.fullmatch(nxt_value)
            ):
                cells[i] = combine_suffix(cells[i], cells[i + 1])
                cells[i + 1] = ''
                changed = True
                i += 2
                continue

            i += 1

        return emit(cells) if changed else line

    return row_re.sub(repl, doc)


def _drop_visually_empty_markdown_table_body_rows(doc: str) -> str:
    """Remove table body rows that contain only empty cells or span covers."""
    lines = doc.splitlines()

    def is_table_row(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith('|') and stripped.endswith('|')

    def split_cells(line: str) -> list[str]:
        return line.strip().strip('|').split('|')

    def is_divider(line: str) -> bool:
        cells = split_cells(line)
        return bool(cells) and all(re.fullmatch(r'\s*:?-{3,}:?\s*', cell) for cell in cells)

    def visible_text(cell: str) -> str:
        text = cell.replace('&nbsp;', ' ').replace('\u00a0', ' ')
        text = re.sub(r'<br\s*/?>', ' ', text, flags=re.I)
        text = re.sub(r'</?[^>]+>', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if not text:
            return ''
        if re.sub(r'[\s*_`]+', '', text) == '':
            return ''
        return text

    def is_empty_body_row(line: str) -> bool:
        cells = split_cells(line)
        return bool(cells) and all(visible_text(cell) in {'', '—', '^^'} for cell in cells)

    out: list[str] = []
    i = 0
    while i < len(lines):
        if not is_table_row(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        start = i
        while i < len(lines) and is_table_row(lines[i]):
            i += 1
        block = lines[start:i]
        divider_idx = next((idx for idx, line in enumerate(block) if is_divider(line)), None)
        if divider_idx is None:
            out.extend(block)
            continue

        for idx, line in enumerate(block):
            if idx <= divider_idx or not is_empty_body_row(line):
                out.append(line)

    return '\n'.join(out)


def _repair_reconciliation_row_indentation(doc: str) -> str:
    """Indent adjustment rows between GAAP parent rows and bold Non-GAAP subtotals."""
    lines = doc.splitlines()
    out: list[str] = []
    pending_adjustments = False
    row_re = re.compile(r'^(\|\s*)([^|\n]*?)(\s*\|.*)$')

    def plain_label(label: str) -> str:
        label = label.replace('&nbsp;', '')
        label = re.sub(r'<[^>]+>', '', label)
        label = label.replace('\\*', '*')
        label = re.sub(r'[*_`]', '', label)
        return re.sub(r'\s+', ' ', label).strip()

    for line in lines:
        match = row_re.match(line)
        if not match or re.fullmatch(r'\|[:\-\s|]+\|?', line.strip()):
            out.append(line)
            if not line.startswith('|'):
                pending_adjustments = False
            continue

        prefix, label, suffix = match.groups()
        stripped = label.strip()
        plain = plain_label(stripped)

        if stripped.startswith('**') or plain.startswith('Non-GAAP'):
            pending_adjustments = False
            out.append(line)
            continue

        if pending_adjustments and plain and not plain.startswith('GAAP') and not stripped.startswith('&nbsp;'):
            line = f"{prefix}&nbsp;&nbsp;&nbsp;&nbsp;{stripped}{suffix}"

        out.append(line)

        if plain.startswith('GAAP '):
            pending_adjustments = True

    return "\n".join(out)


def main_one(
    path: pathlib.Path,
    to_mmd: bool = False,
    source_document_url: Optional[str] = None,
    disable_indentation: bool = False,
) -> None:
    _state.CURRENT_PROCESSING_FILE = str(path.resolve())
    _state.CURRENT_SOURCE_DOCUMENT_URL = (source_document_url or '').strip() or None
    try:
        doc = process_local_xbrl(path)
        if not doc:
            raise ValueError("empty output")
        
        doc = doc.replace('| — |', '|  |').replace('| — |', '|  |').replace('| <br> — |', '|  |').replace('| <br> — |', '|  |').replace('| <br> - |', '|  |').replace('| <br> - |', '|  |')

        before_pattern = re.compile(r'(?<!\n)\n(^---$)', re.MULTILINE)
        doc = before_pattern.sub(r'\n\n\1', doc)

        after_pattern = re.compile(r'(^---$)\n(?!\n)', re.MULTILINE)
        doc = after_pattern.sub(r'\1\n\n', doc)

        doc = doc.replace("\n<br>---\n", "\n---\n")

        delimiter_fix_pattern = re.compile(r'(^---)([ \t]*[^-].*)', re.MULTILINE)
        doc = delimiter_fix_pattern.sub(r'\1\n\n\2', doc)

        if to_mmd == False:
            doc = re.sub(r'(?<=[^\s-])<PIPE>', ' <PIPE>', doc)
            doc = doc.replace('<PIPE>', r'\|')
            doc = doc.replace('##MD_NEWLINE##', '<br>')

        if to_mmd:
            doc = convert_all_tables_to_mmd(doc)
            doc = apply_markdown_hardcodes(doc)
            doc = doc.replace("<sup>", "^").replace("</sup>", "^")
            doc = doc.replace("<sub>", "~").replace("</sub>", "~")
            doc = doc.replace(r"\ |", r"\|")
            doc = normalize_malformed_markdown_emphasis(doc)

        doc = re.sub(r'(?<=[^\s-])<PIPE>', ' <PIPE>', doc)
        doc = doc.replace('<PIPE>', r'\|')
        doc = doc.replace('##MD_NEWLINE##', '<br>')

        if (r"\>/R\<" in doc and r"\>R\<" in doc and r"\>PAGE\<" in doc):
            doc = doc.replace(r"\>/R\<", "").replace(r"\>R\<", "").replace(r"\>R\/R\<", "").replace(r"\>R\<", "").replace(r"\>R\\", "").replace(r"#### \>PAGE\<", "")
            doc = re.sub(r"\n\\>PAGE\\< ?\r?\n", "\n", doc)
            MARKERS = re.compile(
                r'(?m)^[ \t]*(?:\*\*\\?>R\\?\*\*|\d+[ \t]+\\?>PAGE\\?<)[ \t]*(?:\r?\n|$)'
            )
            doc = MARKERS.sub('', doc)

        doc = re.sub(r'##(ROWSPAN_\d+|COLSPAN_\d+)##', '', doc)
        doc = doc.replace("<br>)", ")").replace("<br>]", "]").replace("<br>%", "%").replace("<br>)%", ")%")
        doc = re.sub(r'<br>\.[1-9](?!\d)', '', doc, flags=re.I)
        doc = re.sub(r'(?<=\| )—(?= +\|)', ' ', doc)
        doc = re.sub(r'\$ (?=[0-9(])', '$', doc)
        doc = doc.replace('##SINGLE_ASTERISK##', '').replace('##DOUBLE_ASTERISK##', '').replace('##TRIPLE_ASTERISK##', '')
        doc = re.sub(r'(?m)^\| o (?=.)', r'| ##INDENT##o ', doc)
        doc = doc.replace('\u2063', '').replace('##INDENT##', '&nbsp;&nbsp;&nbsp;&nbsp;')
        doc = doc.replace("\n\n<PDF>\n\n</PDF>", "")
        doc = doc.replace("| **%****%** |", "| **%** |")
        doc = re.sub(r'(\*{3,})(\.\*\*|\)?%\*\*)', r'*\2', doc)
        m = re.search(r'^begin 644.*(?:\r?\n|$)', doc, flags=re.M)
        doc = doc.replace("| nan |", "|  |").replace("| nan |", "|  |")
        doc = doc[:m.start()] if m else doc
        doc = doc.replace("\n## Excel\n\n\n", "")
        doc = doc.replace('##SPACE##', ' ').replace('##I_SPACE##', ' ')
        doc = doc.replace("<br> |", " |")
        doc = re.sub(r"\n *<br> *\n", "\n", doc)
        doc = re.sub(r"(\d+)\s%( \|)", r"\1%\2", doc)
        doc = doc.replace("| %** |", "| **%** |")
        doc = re.sub(r"\*\*(\d+(?:\.\d+)?)\*\*\s*%?\s*\*\*(?=\s|$)", r"**\1%**", doc)
        doc = re.sub(r"(^|\n)<br>(?=#+)", r"\1", doc)
        doc = re.sub(r"<br>(?=#+)", r"\n\n", doc)
        doc = doc.replace("<br>------", "------").replace("<br><br>", "<br>").replace("<br> **)**", "**)**")
        doc = doc.replace("\n# # #\n", "\n\\# \\# \\#\n")
        doc = doc.replace("\n<br>---\n\n|", "\n---\n\n|")
        pattern = r'(?<=[^\s-])------(?=\r?\n)'
        replacement = r'\n\n------'
        doc = re.sub(pattern, replacement, doc)
        doc = doc.replace("<br> % |", "% |").replace("*<br>*%* |", "%* |").replace("***<br> *%***", "%***").replace("*<br> *%* |", "%* |")
        doc = _repair_styled_numeric_suffixes(doc)









        item_heading_pattern = re.compile(
            r'(^\s*(?:\*\*)?\s*(?:item\s+)?\d+[A-Z]?\.)'
            r'(?=[A-Z])',
            re.IGNORECASE | re.MULTILINE
        )
        doc = item_heading_pattern.sub(r'\1 ', doc)
        pattern = r"\|\s\**(?:&nbsp;| )+\**\s\|"

        doc = doc.replace(") ** |", ")** |")

        doc = re.sub(
            r"\*\*\(\s*(\$?[+-]?\d[\d,]*(?:\.\d+)?)\s*\*\*\s*(?:<br\s*/?>\s*)?\s*\*\*\)\s*\*\*",
            r"**(\1)**",
            doc,
            flags=re.IGNORECASE,
        )

        doc = re.sub(
            r"\*\*([+-]?\d[\d,]*(?:\.\d+)?)\*\*(\s*(?:<br\s*/?>)?\s*(%?)\s*)\*\*(?=\s|$)",
            lambda m: f"**{m.group(1)}{'%' if not m.group(3) else m.group(2)}**",
            doc,
            flags=re.IGNORECASE,
        )

        doc = re.sub(pattern, "|  |", doc)
        doc = re.sub(pattern, "|  |", doc)

            


        doc = re.sub(r'(?m)^\*\*2\*\*\s+\*\*nd\b', r'**2nd', doc)

        doc = doc.replace("** ---\n\n|", "**\n\n---\n\n|")
        doc = doc.replace("| $**$** | $%** |", "| **$** | **%** |").replace('| — |', '|  |').replace('| — |', '|  |').replace("| **$Change** |", "| **$ Change** |").replace("| **%Change** |", "| **% Change** |").replace(" &nbsp;&nbsp;&nbsp;&nbsp;months |", " months |")
        pattern = r"(\n\n\*\*\d+)\. \*\*"
        replacement = r"\1.**"

        doc = re.sub(pattern, replacement, doc)
        doc = doc.replace("**• ** ", "**•** ")

        pattern = re.compile(r"^(begin 644 (.*)(\r?\n|$))", re.MULTILINE)

        doc = pattern.sub(conditional_delete, doc)

        doc = re.sub(r'\^\((\d+)\)((?:&nbsp;)+)\^', r'^(\1)^\2', doc)

        doc = doc.replace("**6** **.** **ACCOUNTS RECEIVABLE**", "**6.** **ACCOUNTS RECEIVABLE**").replace("**1** **2.** **Long-term debt**", "**12.** **Long-term debt**").replace("**1** **3.** **Employee future benefits**", "**13.** **Employee future benefits**")
        doc = _coalesce_adjacent_markdown_links(doc)
        doc = re.sub(r'(?<=[\d)\-–—])\s+%', '%', doc)
        doc = _repair_styled_numeric_suffixes(doc)
        doc = _repair_split_currency_cells(doc)
        doc = _drop_visually_empty_markdown_table_body_rows(doc)
        doc = _repair_reconciliation_row_indentation(doc)
        doc = _repair_list_marker_spacing(doc)
        doc = _repair_split_inline_emphasis(doc)
        doc = _repair_final_spacing_artifacts(doc)
        doc = _remove_isolated_page_markers(doc)
        doc = doc.replace("$[IMAGE PLACEHOLDER:", "[IMAGE PLACEHOLDER:")
        doc = re.sub(r'(?m)^(?:&nbsp;|[ \t])+(?:<br\s*/?>)?[ \t]*$', '', doc)
        doc = re.sub(r'\*\*((?:&nbsp;|[ \t])+)\*\*', r'\1', doc)
        doc = re.sub(r'[ \t]+(?=\r?\n)', '', doc)
        doc = re.sub(r'\n{3,}', '\n\n', doc)
        doc = normalize_malformed_markdown_emphasis(doc)
        doc = _repair_split_inline_emphasis(doc)
        doc = _repair_final_spacing_artifacts(doc)
        doc = doc.strip() + "\n"

        if disable_indentation:
            doc = _disable_output_indentation(doc)

        out = path.with_suffix(".md")
        out.write_text(doc, encoding="utf-8")
        parse_stats = _complete_parse_stats_for_output(
            _state.LAST_PARSE_STATS,
            output_path=out,
            final_markdown=doc,
            to_mmd=to_mmd,
            disable_indentation=disable_indentation,
        )
        try:
            _write_parse_stats_outputs(parse_stats, out)
            _print_parse_stats_summary(parse_stats)
        except Exception as stats_exc:
            print(f"[parse-stats warning] Could not write parse stats: {stats_exc}")
        print(f"Successful! Output written to {out}")
    except Exception as e:
        logging.error(
            f"FILE: {path.name}\n"
            f"ERROR: {e}\n"
            f"TRACEBACK:\n{traceback.format_exc()}"
        )
        print(f"[ERROR] {path}: {e}. Details logged to {log_file_path.name}", file=sys.stderr)

__all__ = [name for name in globals() if not name.startswith("__")]
