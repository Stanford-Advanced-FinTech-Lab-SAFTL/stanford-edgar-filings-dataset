from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.parsers.html.preprocessing import to_compact_markdown
from stanford_edgar_parser.parsers.ocr.ocr_utils import normalize_text_markup
from stanford_edgar_parser.utils.bootstrap import (
    BeautifulSoup,
    List,
    Optional,
    pathlib,
    pd,
    re,
)

def parse_series_and_classes_sgml(header_content: str) -> str:
    """
    Parses the <SERIES-AND-CLASSES-CONTRACTS-DATA> SGML block from a
    filing header into a structured Markdown output.
    """
    sgml_match = re.search(
        r"<SERIES-AND-CLASSES-CONTRACTS-DATA>(.*?)</SERIES-AND-CLASSES-CONTRACTS-DATA>",
        header_content,
        re.S | re.I
    )

    if not sgml_match:
        return ""

    sgml_content = sgml_match.group(1)
    md_parts = ["## Series and Classes Contracts Data"]

    series_blocks = re.split(r'<SERIES>', sgml_content, flags=re.I)[1:]
    if not series_blocks:
        return ""

    for series_block in series_blocks:
        series_name_match = re.search(r'<SERIES-NAME>\s*([^\n<]+)', series_block, re.I)
        series_id_match = re.search(r'<SERIES-ID>\s*([^\n<]+)', series_block, re.I)

        series_name = series_name_match.group(1).strip() if series_name_match else "—"
        series_id = series_id_match.group(1).strip() if series_id_match else "—"

        md_parts.append(f"\n### {series_name} (Series ID: {series_id})")

        class_records = []
        class_contract_blocks = re.findall(
            r'<CLASS-CONTRACT>(.*?)(?=<CLASS-CONTRACT>|<SERIES>|$)',
            series_block,
            re.S | re.I
        )

        for class_block in class_contract_blocks:
            id_match = re.search(r'<CLASS-CONTRACT-ID>\s*([^\n<]+)', class_block, re.I)
            name_match = re.search(r'<CLASS-CONTRACT-NAME>\s*([^\n<]+)', class_block, re.I)
            ticker_match = re.search(r'<CLASS-CONTRACT-TICKER-SYMBOL>\s*([^\n<]+)', class_block, re.I)

            record = {
                'Class ID': id_match.group(1).strip() if id_match else "—",
                'Class Name': name_match.group(1).strip() if name_match else "—",
                'Ticker Symbol': ticker_match.group(1).strip() if ticker_match else "—",
            }
            class_records.append(record)

        if class_records:
            df = pd.DataFrame(class_records)
            md_parts.append(to_compact_markdown(df, index=False))

    return "\n\n".join(md_parts)

def parse_legacy_paper_filing(raw_text: str, form_type: str) -> str:
    """
    Parses various legacy plain-text paper filings (ADV series, MSDW, etc.).
    All data is contained within the <SEC-HEADER> or <IMS-HEADER> block, and
    it also captures any text from the <DOCUMENT> block.
    """
    form_titles = {
        "ADV": "FORM ADV: Uniform Application for Investment Adviser Registration",
        "ADV/A": "FORM ADV/A: Amendment to Form ADV",
        "ADV-E": "FORM ADV-E: Certificate of Accounting of Client Securities and Funds",
        "ADV-H-T": "FORM ADV-H-T: Application for a Temporary Hardship Exemption",
        "ADV-H-C": "FORM ADV-H-C: Application for a Continuing Hardship Exemption",
        "ADV-NR": "FORM ADV-NR: Appointment of Agent for Service of Process by Non-Resident Adviser",
        "ADVW": "FORM ADVW: Notice of Withdrawal from Registration as Investment Adviser",
        "ADVCO": "FORM ADVCO: Correction to an ADV Filing",
        "MSDW": "FORM MSDW: Notice of Withdrawal from Registration as a Municipal Securities Dealer"
    }
    title = form_titles.get(form_type, f"Form {form_type}")

    md_parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        f"## {title}\n"
    ]

    header_match = re.search(r"<(?:SEC|IMS)-HEADER>(.*?)</(?:SEC|IMS)-HEADER>", raw_text, re.S | re.I)
    if not header_match:
        md_parts.append(f"<!-- HEADER for {form_type} not found -->")
    else:
        header_content = header_match.group(1).strip()
        for line in header_content.splitlines():
            line = line.strip()
            if not line:
                continue

            if not line.startswith('\t') and line.endswith(':'):
                section_name = line.rstrip(':').strip()
                if section_name in ["FILER", "COMPANY DATA", "FILING VALUES", "BUSINESS ADDRESS", "MAIL ADDRESS", "FORMER COMPANY"]:
                    md_parts.append(f"\n### {section_name.title()}")
                continue

            if ':' in line:
                key, value = line.split(':', 1)
                key = key.strip()
                value = value.strip()
                if key and value:
                    md_parts.append(f"**{key}:** {value}")

    doc_match = re.search(r"<DOCUMENT>(.*?)</DOCUMENT>", raw_text, re.S | re.I)
    if doc_match:
        doc_content = doc_match.group(1)
        text_match = re.search(r"<TEXT>(.*?)</TEXT>", doc_content, re.S | re.I)
        if text_match:
            doc_text = text_match.group(1).strip()
            if doc_text:
                md_parts.append("\n### Document Note")
                blockquote_lines = [f"> {line.strip()}" for line in doc_text.splitlines() if line.strip()]
                md_parts.append("\n".join(blockquote_lines))

    return "\n".join(md_parts)

def _extract_class_name_map_from_header_content(header_content: str) -> dict:
    if not header_content:
        return {}
    try:
        header_soup = BeautifulSoup(header_content, 'lxml')
    except ValueError as e:
        if "not enough values to unpack" in str(e):
            print("[Warning] lxml crashed while parsing header contract data. Falling back to html.parser.")
            header_soup = BeautifulSoup(header_content, 'html.parser')
        else:
            raise

    class_name_map = {}
    if (scd := header_soup.find('series-and-classes-contracts-data')):
        for series in scd.find_all('series'):
            for class_contract in series.find_all('class-contract'):
                class_id_tag = class_contract.find('class-contract-id')
                class_name_tag = class_contract.find('class-contract-name')
                if class_id_tag and class_name_tag:
                    id_text_node = class_id_tag.find(string=True, recursive=False)
                    name_text_node = class_name_tag.find(string=True, recursive=False)
                    if id_text_node and name_text_node:
                        class_id = id_text_node.strip()
                        class_name = name_text_node.strip()
                        if class_id:
                            class_name_map[class_id] = class_name
    return class_name_map

def _iter_document_blocks(raw_bytes: bytes):
    for match in re.finditer(rb"<DOCUMENT>(.*?)</DOCUMENT>", raw_bytes, re.S | re.I):
        yield match.group(1)

def _iter_document_blocks_from_file(filepath: pathlib.Path, chunk_size: int = 8 * 1024 * 1024):
    start_re = re.compile(rb"<DOCUMENT>", re.I)
    end_re = re.compile(rb"</DOCUMENT>", re.I)
    overlap = 64
    buffer = b""
    in_document = False
    doc_parts: List[bytes] = []

    with filepath.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            buffer += chunk
            while True:
                if not in_document:
                    start_match = start_re.search(buffer)
                    if not start_match:
                        buffer = buffer[-overlap:]
                        break
                    buffer = buffer[start_match.end():]
                    doc_parts = []
                    in_document = True

                end_match = end_re.search(buffer)
                if end_match:
                    doc_parts.append(buffer[:end_match.start()])
                    yield b"".join(doc_parts)
                    buffer = buffer[end_match.end():]
                    doc_parts = []
                    in_document = False
                    continue

                if len(buffer) > overlap:
                    doc_parts.append(buffer[:-overlap])
                    buffer = buffer[-overlap:]
                break

def _first_document_block_from_file(filepath: pathlib.Path) -> Optional[bytes]:
    return next(_iter_document_blocks_from_file(filepath), None)

def _read_prefix_until_any(
    filepath: pathlib.Path,
    markers: List[bytes],
    chunk_size: int = 1024 * 1024,
    max_bytes: int = 64 * 1024 * 1024,
) -> bytes:
    buffer = b""
    marker_res = [re.compile(re.escape(marker), re.I) for marker in markers if marker]
    with filepath.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return buffer
            buffer += chunk
            earliest_end: Optional[int] = None
            for marker_re in marker_res:
                match = marker_re.search(buffer)
                if match and (earliest_end is None or match.end() < earliest_end):
                    earliest_end = match.end()
            if earliest_end is not None:
                return buffer[:earliest_end]
            if len(buffer) >= max_bytes:
                return buffer

def _file_contains_bytes(filepath: pathlib.Path, needle: bytes, chunk_size: int = 4 * 1024 * 1024) -> bool:
    if not needle:
        return False
    needle_lower = needle.lower()
    overlap = max(0, len(needle) - 1)
    tail = b""
    with filepath.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return False
            haystack = tail + chunk
            if needle_lower in haystack.lower():
                return True
            tail = haystack[-overlap:] if overlap else b""

def _extract_xml_blobs_from_body_bytes(body_bytes: bytes) -> List[str]:
    return [
        normalize_text_markup(match.group(1))
        for match in re.finditer(rb"<XML>(.*?)</XML>", body_bytes, re.S | re.I)
    ]

def _body_bytes_without_xml(body_bytes: bytes) -> bytes:
    if not re.search(rb"<XML", body_bytes, re.I):
        return body_bytes
    return re.sub(rb"<XML>.*?</XML>", b"", body_bytes, flags=re.S | re.I)

def _iter_pdf_attachment_texts_from_file(
    filepath: pathlib.Path,
    skip_types: set[bytes],
    xbrl_ex_re: re.Pattern,
):
    for doc_bytes in _iter_document_blocks_from_file(filepath):
        m = re.search(rb"<TYPE>\s*([^\s<]+)", doc_bytes, re.I)
        doc_type_bytes = m.group(1).upper() if m else b""
        if doc_type_bytes in skip_types or xbrl_ex_re.match(doc_type_bytes):
            continue
        text_match = re.search(rb"<TEXT>(.*?)</TEXT>", doc_bytes, re.S | re.I)
        body_bytes = text_match.group(1) if text_match else doc_bytes
        if not body_bytes.strip():
            desc_match = re.search(rb"<DESCRIPTION>\s*(.*?)\s*<", doc_bytes, re.S | re.I)
            if desc_match:
                body_bytes = desc_match.group(1)
        if re.search(rb"<PDF>", body_bytes, re.I):
            yield body_bytes.decode('latin-1', errors='replace')

__all__ = [name for name in globals() if not name.startswith("__")]
