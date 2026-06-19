from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.parsers.ocr.rotate_auth import (
    CP1252_CTRL_TO_UNICODE,
    OCR_API_URL,
    PUNCT_CANON,
    _has_mistral_api_keys,
    _mistral_no_keys_message,
    _print_mistral_monthly_usage,
    _record_mistral_key_success,
    _run_with_mistral_key_rotation,
    _summarize_ocr_usage,
)
from stanford_edgar_parser.utils.bootstrap import (
    Any,
    BeautifulSoup,
    Config,
    Dict,
    List,
    NavigableString,
    Optional,
    PdfReader,
    PdfWriter,
    SDKError,
    Tuple,
    UnicodeDammit,
    _load_sec_parser_env,
    base64,
    binascii,
    fitz,
    html,
    io,
    logging,
    np,
    os,
    random,
    re,
    requests,
    time,
    traceback,
    unicodedata,
)
from stanford_edgar_parser.utils.parse_stats import _log_current_filing_ocr

OCR_MODEL = Config.OCR_MODEL

MISTRAL_OCR_HTML_TABLE_PROMPT = (
    "Extract the document content. Return prose and non-table text as Markdown. "
    "For every table, return real HTML table markup instead of Markdown tables. "
    "Use only plain table structure tags such as <table>, <tr>, <th>, and <td>, "
    "with colspan and rowspan when needed to preserve merged cells. "
    "Do not wrap HTML tables in markdown code fences. Preserve visible table text, "
    "row order, column order, punctuation, signs, and numeric formatting."
)


def _mistral_ocr_table_format() -> str:
    value = os.getenv("MISTRAL_OCR_TABLE_FORMAT", "html").strip().lower()
    if value in {"html", "markdown"}:
        return value
    return "html"


def _build_mistral_ocr_payload(signed_url: str) -> dict:
    table_format = _mistral_ocr_table_format()
    payload = {
        "model": OCR_MODEL,
        "document": {"document_url": signed_url},
        "table_format": table_format,
    }
    if table_format == "html":
        payload["document_annotation_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "ocr_markdown_with_html_tables",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                    },
                    "required": ["content"],
                    "additionalProperties": False,
                },
            },
        }
        payload["document_annotation_prompt"] = MISTRAL_OCR_HTML_TABLE_PROMPT
    return payload


def _post_mistral_ocr_with_retry(
    *,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    operation_label: str,
    timeout_s: int = 600,
):
    max_retries = max(1, int(getattr(Config, "API_MAX_RETRIES", 4) or 4))
    delay = max(0.1, float(getattr(Config, "API_INITIAL_DELAY_SECONDS", 2.0) or 2.0))
    transient_statuses = {408, 409, 425, 429, 500, 502, 503, 504}
    last_exc: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        response = None
        try:
            response = requests.post(OCR_API_URL, headers=headers, json=payload, timeout=timeout_s)
            if response.status_code not in transient_statuses:
                response.raise_for_status()
                return response
            response.raise_for_status()
        except Exception as exc:
            last_exc = exc
            status_code = getattr(response, "status_code", None)
            is_transient = status_code in transient_statuses or isinstance(
                exc,
                (
                    requests.Timeout,
                    requests.ConnectionError,
                ),
            )
            if not is_transient or attempt >= max_retries:
                raise
            retry_after = None
            if response is not None:
                retry_after_raw = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_raw) if retry_after_raw else None
                except (TypeError, ValueError):
                    retry_after = None
            sleep_s = max(delay, retry_after or 0.0) + random.uniform(0, 0.5)
            print(
                f"OCR API transient error during {operation_label}: {exc}. "
                f"Retrying in {sleep_s:.2f}s... (Attempt {attempt}/{max_retries})"
            )
            time.sleep(sleep_s)
            delay = min(delay * 2, 60.0)

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"OCR API request failed during {operation_label}")


def _decode_uu_block_bytes(text: str) -> bytes:
    lines = text.splitlines()
    begin_idx = next((i for i, line in enumerate(lines) if re.match(r"^begin\s+\d{3}\s+[^\n]+$", line.strip())), None)
    if begin_idx is None:
        raise ValueError("Could not find a valid 'begin' line in the uuencoded block.")

    decoded = bytearray()
    found_end = False

    for line in lines[begin_idx + 1:]:
        uu_line = line.rstrip("\r\n")
        if uu_line == "end":
            found_end = True
            break
        if uu_line == "":
            continue
        try:
            decoded.extend(binascii.a2b_uu(uu_line.encode("latin-1")))
        except binascii.Error as e:
            if "Trailing garbage" not in str(e):
                raise ValueError(f"Failed to decode uuencoded block: {e}") from e

            recovered = None
            for end in range(len(uu_line) - 1, 0, -1):
                try:
                    candidate = binascii.a2b_uu(uu_line[:end].encode("latin-1"))
                    recovered = candidate
                    break
                except binascii.Error:
                    continue

            if recovered is None:
                raise ValueError(f"Failed to decode uuencoded block: {e}") from e

            decoded.extend(recovered)

    if not found_end:
        raise ValueError("Could not find the terminating 'end' line in the uuencoded block.")

    return bytes(decoded)

def _extract_uu_block(text: str) -> Tuple[bytes, str]:
    """Finds and decodes the first uuencoded block."""
    fname_match = re.search(r"begin\s+\d{3}\s+([^\n]+)", text)
    if not fname_match:
        raise ValueError("Could not find a valid 'begin' line in the uuencoded block.")
    filename = fname_match.group(1).strip()
    decoded_bytes = _decode_uu_block_bytes(text)
    if not decoded_bytes:
        raise ValueError("UU decoding produced no data.")
    if decoded_bytes.startswith(b"%PDF-") and b"%%EOF" not in decoded_bytes[-1024:]:
        decoded_bytes += b"\n%%EOF\n"
    return decoded_bytes, filename

def _slice_pdf_bytes(pdf_bytes: bytes, first_page: int, last_page: Optional[int] = None) -> bytes:
    """Extracts a page range from a PDF bytes object."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    num_pages = len(reader.pages)
    start_idx = first_page - 1
    end_idx = num_pages if last_page is None else min(last_page, num_pages)
    for i in range(start_idx, end_idx):
        writer.add_page(reader.pages[i])
    with io.BytesIO() as buf:
        writer.write(buf)
        return buf.getvalue()

def is_page_nearly_blank(page: fitz.Page, threshold: float = 3.5) -> bool:
    """Checks if a page is visually blank by analyzing pixel standard deviation."""
    pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY, alpha=False)
    img_data = np.frombuffer(pix.samples, dtype=np.uint8)
    if img_data.size < 100: return True
    std_dev = np.std(img_data)
    return std_dev < threshold

def realign_fixed_width_table(text: str) -> str:
    """Parses and perfectly reformats a poorly-aligned fixed-width table."""
    lines = text.strip().split('\n')
    if len(lines) < 2: return text
    separator_index = next((i for i, line in enumerate(lines) if '--' in line and len(line.replace('-', '').replace(' ', '')) < 5), -1)
    if separator_index == -1: return text
    separator_line = lines[separator_index]
    boundaries = []
    in_dash = False
    for i, char in enumerate(separator_line):
        if char == '-' and not in_dash:
            in_dash = True
            start = i
        elif char == ' ' and in_dash:
            in_dash = False
            boundaries.append((start, i))
    if in_dash: boundaries.append((start, len(separator_line)))
    if not boundaries: return text
    rows = [[line[s:e].strip() for s, e in boundaries] for line in lines if line.strip() != separator_line.strip() and any(line[s:e].strip() for s, e in boundaries)]
    if not rows: return text
    widths = [max(len(cell) for cell in col) for col in zip(*rows)]
    realigned = []
    header, body = rows[0], rows[1:]
    realigned.append('  '.join(h.ljust(w) for h, w in zip(header, widths)))
    realigned.append('  '.join('-' * w for w in widths))
    for row in body:
        full_row = row + [''] * (len(widths) - len(row))
        realigned.append('  '.join([full_row[0].ljust(widths[0])] + [c.rjust(w) for c, w in zip(full_row[1:], widths[1:])]))
    return '\n'.join(realigned)

def _ascii_text(s: str) -> str:
    s = _normalize_ocr_text(s or "")
    s = s.translate(PUNCT_CANON)
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2022", "*").replace("\u00b7", "*")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).strip()

def is_numeric_like(s: str) -> bool:
    s = _ascii_text(s)
    if s in {"", "-"}:
        return False
    s = s.replace(",", "").replace("$", "").replace("%", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        float(s)
        return True
    except ValueError:
        return False

def table_to_fixed_width(table) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [_ascii_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)

    if not rows:
        return ""

    col_count = max(len(r) for r in rows)
    rows = [r + [""] * (col_count - len(r)) for r in rows]

    aligns = []
    for j in range(col_count):
        if j == 0:
            aligns.append("left")
            continue

        col_vals = [r[j] for r in rows[1:] if r[j].strip()]
        numeric_count = sum(is_numeric_like(v) for v in col_vals)
        aligns.append("right" if col_vals and numeric_count >= len(col_vals) / 2 else "left")

    widths = [max(len(r[j]) for r in rows) for j in range(col_count)]

    def fmt(row):
        out = []
        for j, cell in enumerate(row):
            if aligns[j] == "right":
                out.append(cell.rjust(widths[j]))
            else:
                out.append(cell.ljust(widths[j]))
        return "  ".join(out).rstrip()

    lines = []
    for i, row in enumerate(rows):
        lines.append(fmt(row))
        if i == 0:
            lines.append("  ".join("-" * w for w in widths).rstrip())

    return "\n".join(lines)


def is_numeric_like(s: str) -> bool:
    from stanford_edgar_parser.parsers.html.preprocessing import is_numeric_like as _impl

    return _impl(s)


def _html_to_fixed_width_ascii(html_fragment: str) -> str:
    html_fragment = (html_fragment or "").strip()
    if html_fragment.startswith("```"):
        html_fragment = re.sub(r"^```(?:html)?\s*", "", html_fragment, flags=re.I)
        html_fragment = re.sub(r"\s*```$", "", html_fragment)

    soup = BeautifulSoup(html_fragment, "html.parser")
    root = soup.body if soup.body else soup
    output_parts: List[str] = []

    def append_text(text: str):
        text = _ascii_text(text)
        if not text:
            return
        m = re.match(r"^\*\*(.+?)\*\*$", text)
        if m:
            output_parts.append(m.group(1).strip().upper())
        else:
            output_parts.append(text)

    def walk(nodes):
        for node in nodes:
            if isinstance(node, NavigableString):
                append_text(str(node))
                continue

            node_name = getattr(node, "name", None)
            if node_name == "table":
                fixed = table_to_fixed_width(node)
                if fixed.strip():
                    output_parts.append(fixed)
                continue
            if node_name == "br":
                continue
            if getattr(node, "contents", None):
                walk(node.contents)
            else:
                append_text(node.get_text(" ", strip=True))

    walk(root.contents)
    return "\n\n".join(part for part in output_parts if part.strip())

_DASH_TRANSLATE = str.maketrans({
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2015": "-",
    "\u2212": "-", "\uFE58": "-", "\uFE63": "-", "\uFF0D": "-"
})

def _normalize_ocr_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("\u00A0", " ").replace("\ufeff", "").replace("\u200b", "").replace("\u200d", "")
    s = s.translate(_DASH_TRANSLATE)
    if "\\n" in s: s = s.replace(r"\n", "\n")
    return s

_CELL_SEP_RE = re.compile(r'^\s*:?\s*-{2,}\s*:?\s*$')
_MISTRAL_HTML_TABLE_FRAGMENT_RE = re.compile(r"<table\b[\s\S]*?</table>", re.I)

def _is_separator_line(line: str) -> bool:
    line = _normalize_ocr_text(line)
    if '|' not in line: return False
    core = line.strip()
    if not core.startswith('|'): return False
    parts = [p.strip() for p in core.strip('|').split('|')]
    if not parts or any(p == '' for p in parts): return False
    return all(_CELL_SEP_RE.match(p or '') for p in parts)

def find_md_table_blocks(text: str) -> List[Tuple[int,int]]:
    text = _normalize_ocr_text(text)
    lines = text.splitlines()
    blocks: List[Tuple[int,int]] = []
    i, n = 0, len(lines)
    while i < n - 1:
        if '|' in lines[i].lstrip() and i + 1 < n and _is_separator_line(lines[i+1]):
            j, has_body = i + 2, False
            while j < n and '|' in lines[j].lstrip() and lines[j].strip() != '':
                has_body = True
                j += 1
            if has_body:
                blocks.append((i, j-1))
                i = j
                continue
        i += 1
    return blocks

def slice_text_by_blocks(text: str, blocks: List[Tuple[int,int]]) -> List[str]:
    text = _normalize_ocr_text(text)
    lines = text.splitlines()
    return ["\n".join(lines[s:e+1]) for s, e in blocks]

def replace_blocks_with(text: str, blocks: List[Tuple[int,int]], repl_texts: List[str]) -> str:
    text = _normalize_ocr_text(text)
    lines = text.splitlines()
    out, cur, k = [], 0, 0
    for s, e in blocks:
        out.extend(lines[cur:s])
        out.extend(repl_texts[k].splitlines())
        cur = e + 1
        k += 1
    out.extend(lines[cur:])
    return "\n".join(out)

def _inline_mistral_table_placeholders(page_obj, text_content: str) -> str:
    rendered = str(text_content or "")
    tables = page_obj.get("tables") if isinstance(page_obj, dict) else None
    if not isinstance(tables, list) or not tables:
        return rendered

    inlined_count = 0
    fallback_contents = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_id = _normalize_ocr_text(str(table.get("id") or "")).strip()
        table_content = _normalize_ocr_text(
            str(table.get("content") or table.get("html") or table.get("markdown") or "")
        ).strip()
        if not table_content:
            continue
        if table_id:
            placeholder = f"[{table_id}]({table_id})"
            if placeholder in rendered:
                rendered = rendered.replace(placeholder, table_content)
                inlined_count += 1
                continue
        fallback_contents.append(table_content)

    if inlined_count == 0 and fallback_contents:
        missing_contents = [
            content
            for content in fallback_contents
            if content not in rendered
        ]
        if missing_contents:
            if rendered.strip():
                rendered = rendered.rstrip() + "\n\n" + "\n\n".join(missing_contents)
            else:
                rendered = "\n\n".join(missing_contents)

    return rendered


def _mistral_table_cell_text(cell) -> str:
    text = _normalize_ocr_text(cell.get_text(" ", strip=True))
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("|", r"\|")
    return text


def _html_table_fragment_to_mmd(table_html: str) -> str:
    try:
        soup = BeautifulSoup(table_html, "html.parser")
    except Exception:
        return table_html

    table = soup.find("table")
    if table is None:
        return table_html

    rows = []
    pending_rowspans = {}
    for tr in table.find_all("tr"):
        row = []
        col = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while pending_rowspans.get(col, 0) > 0:
                row.append("")
                pending_rowspans[col] -= 1
                col += 1

            try:
                colspan = max(1, int(cell.get("colspan", 1)))
            except (TypeError, ValueError):
                colspan = 1
            try:
                rowspan = max(1, int(cell.get("rowspan", 1)))
            except (TypeError, ValueError):
                rowspan = 1

            row.append(_mistral_table_cell_text(cell))
            for _ in range(1, colspan):
                row.append("")

            if rowspan > 1:
                for offset in range(colspan):
                    pending_rowspans[col + offset] = max(pending_rowspans.get(col + offset, 0), rowspan - 1)
            col += colspan

        while pending_rowspans.get(col, 0) > 0:
            row.append("")
            pending_rowspans[col] -= 1
            col += 1

        if any(cell.strip() for cell in row):
            rows.append(row)

    if not rows:
        return table_html

    width = max(len(row) for row in rows)
    padded_rows = [row + [""] * (width - len(row)) for row in rows]

    def render_row(row) -> str:
        return "| " + " | ".join(cell or " " for cell in row) + " |"

    separator = "| " + " | ".join("---" for _ in range(width)) + " |"
    return "\n".join([render_row(padded_rows[0]), separator, *[render_row(row) for row in padded_rows[1:]]])


def _convert_mistral_html_tables_to_mmd(text: str) -> str:
    if not text or "<table" not in text.lower():
        return text

    def replace_table(match) -> str:
        rendered = _html_table_fragment_to_mmd(match.group(0))
        return f"\n\n{rendered}\n\n"

    converted = _MISTRAL_HTML_TABLE_FRAGMENT_RE.sub(replace_table, text)
    return re.sub(r"\n{3,}", "\n\n", converted).strip()


def _pick_text(page_obj) -> str:
    text_content = _normalize_ocr_text((page_obj.get("markdown") or page_obj.get("text") or "").strip())
    inlined_text = _inline_mistral_table_placeholders(page_obj, text_content)
    return _normalize_ocr_text(_convert_mistral_html_tables_to_mmd(inlined_text).strip())

def _full_page_data_uri(doc: fitz.Document, page_index0: int, zoom: float = Config.IMAGE_RENDER_ZOOM) -> str:
    page = doc.load_page(page_index0)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return f"data:image/png;base64,{base64.b64encode(pix.tobytes('png')).decode('utf-8')}"

def get_signed_url_with_retry(client, file_id, max_retries=Config.API_MAX_RETRIES, initial_delay=Config.API_INITIAL_DELAY_SECONDS):
    """
    Attempts to get a signed URL, retrying with exponential backoff if a 404 error occurs.
    """
    delay = initial_delay
    for attempt in range(max_retries):
        try:
            signed_url = client.files.get_signed_url(file_id=file_id).url
            return signed_url
        except SDKError as e:
            if e.status_code == 404 and attempt < max_retries - 1:
                print(f"File ID {file_id} not found yet. Retrying in {delay:.2f}s... (Attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
                delay *= 2
                delay += random.uniform(0, 0.1)
            else:
                raise e
    raise Exception(f"Failed to get signed URL for file {file_id} after {max_retries} attempts.")

def _process_pdf_bytes_with_fallback(
    pdf_bytes: bytes,
    file_name: str,
    *,
    batch_size: int,
    mistral_api_key: Optional[str],
    per_table_sleep_s: float,
    start_time: float,
    time_limit_s: int,
):
    """
    Main PDF processing workflow that takes bytes and returns processed content
    and a boolean indicating if a timeout occurred, plus the number of PDF pages
    successfully parsed through OCR.
    """
    _log_current_filing_ocr("pdf_or_rendered_html")
    _print_mistral_monthly_usage("before", file_name, explicit_api_key=mistral_api_key)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    n_total = doc.page_count
    
    results = []
    parsed_page_count = 0
    timed_out = False

    print(f"[init] processing '{file_name}' ({n_total} pages) in batches of {batch_size}…")
    p = 1
    while p <= n_total:
        if time.time() - start_time > time_limit_s:
            print(f"\n[timeout] Time limit of {time_limit_s // 60} minutes reached. Stopping processing for this document.")
            timed_out = True
            break

        q = min(p + batch_size - 1, n_total)
        print(f"[basic] pages {p}–{q} …")
        
        pages = []
        try:
            chunk_bytes = _slice_pdf_bytes(pdf_bytes, first_page=p, last_page=q)

            def _run_batch_ocr(*, client: Mistral, api_key: str, key_spec: Dict[str, Any]):
                up = client.files.upload(file={"file_name": f"chunk_{p}-{q}_{file_name}", "content": chunk_bytes}, purpose="ocr")

                if not up or not up.id:
                    raise Exception("File upload failed to return a valid ID.")

                signed_url = get_signed_url_with_retry(client, file_id=up.id)
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                payload = _build_mistral_ocr_payload(signed_url)
                response = _post_mistral_ocr_with_retry(
                    headers=headers,
                    payload=payload,
                    operation_label=f"pdf batch {p}-{q} for {file_name}",
                    timeout_s=600,
                )
                ocr_data = response.json()
                usage = _summarize_ocr_usage(ocr_data, response.headers)
                return ocr_data.get("pages", []), usage, key_spec["env_name"]

            pages, usage, used_env_name = _run_with_mistral_key_rotation(
                f"pdf batch {p}-{q} for {file_name}",
                _run_batch_ocr,
                explicit_api_key=mistral_api_key,
            )
            _record_mistral_key_success(used_env_name, usage=usage, explicit_api_key=mistral_api_key)

        except Exception as e:
            print(f"API processing for pages {p}-{q} failed and was skipped. Error: {e}")
            error_message = f"Could not repair '{file_name}'. The file may be severely corrupted. Error: {e}"
            logging.error(
                f"FILE: {file_name}\nERROR: {error_message}\nTRACEBACK:\n{traceback.format_exc()}"
            )

        if not pages:
            p = q + 1
            continue
        parsed_page_count += len(pages)

        for i, page_obj in enumerate(pages):
            page_no = p + i
            doc_page = doc.load_page(page_no - 1)
            if is_page_nearly_blank(doc_page):
                print(f"[page {page_no}] is nearly blank -> skipping.")
                continue

            text_basic = _pick_text(page_obj)
            results.append({"page": page_no, "content": text_basic, "source": "mistral-ocr"})
        
        p = q + 1

    _print_mistral_monthly_usage("after", file_name, explicit_api_key=mistral_api_key)
    return results, timed_out, parsed_page_count

def _try_repair_pdf_bytes(pdf_bytes: bytes, file_name: str) -> bytes:
    """
    Attempts to repair a potentially truncated or corrupted PDF byte stream.
    """
    if not pdf_bytes.startswith(b"%PDF-"):
        print(f"[warning] Data for '{file_name}' does not appear to be a PDF. Skipping repair.")
        return pdf_bytes

    if b"%%EOF" not in pdf_bytes[-1024:]:
        print(f"[info] PDF '{file_name}' appears truncated. Appending EOF marker for recovery.")
        pdf_bytes += b"\n%%EOF\n"

    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            if doc.needs_pass:
                 print(f"[warning] PDF '{file_name}' is password protected and cannot be repaired or processed.")
                 return None

            with io.BytesIO() as output_buffer:
                doc.save(output_buffer, garbage=4, clean=True, deflate=True)
                print(f"[success] Successfully repaired and rebuilt '{file_name}'.")
                return output_buffer.getvalue()

    except Exception as e:
        print(f"[error] Could not repair '{file_name}'. The file may be severely corrupted. Error: {e}")
        logging.error(
            f"FILE: {file_name}\n"
            f"ERROR: Could not process PDF attachment: {e}\n"
            f"TRACEBACK:\n{traceback.format_exc()}"
        )
        return None

    return pdf_bytes

def parse_pdf_attachments(pdf_blobs) -> tuple[str, int]:
    """
    Parses uu-encoded PDF attachments using a high-quality, in-memory workflow.
    This version includes an attempt to repair corrupted PDFs.
    """
    _load_sec_parser_env()
    if Config.SKIP_OCR:
        _log_current_filing_ocr("pdf_attachments_skipped")
        return "<!-- PDF OCR skipped by SEC_PARSER_SKIP_OCR. -->", 0
    if not _has_mistral_api_keys():
        print(f"{_mistral_no_keys_message()} Skipping PDF processing.")
        return "<!-- No Mistral API keys found. PDF attachments were not processed. -->", 0

    TIME_LIMIT_SECONDS = Config.PDF_TIMEOUT_LIMIT * 60
    start_time = time.time()
    timed_out = False
    total_parsed_page_count = 0

    md_parts = ["\n### Attached PDF Documents\n"]

    for i, pdf_data in enumerate(pdf_blobs, 1):
        if timed_out:
            break

        filename = "unknown.pdf"
        try:
            pdf_bytes, filename = _extract_uu_block(pdf_data)

            repaired_pdf_bytes = _try_repair_pdf_bytes(pdf_bytes, filename)

            if not repaired_pdf_bytes:
                md_parts.append(f"**Attachment {i}:** `{filename}` – Corrupted and could not be repaired.")
                continue

            if not repaired_pdf_bytes.startswith(b"%PDF-"):
                md_parts.append(f"**Attachment {i}:** `{filename}` – not a PDF.")
                continue

            md_parts.append(f"**Attachment {i}:** `{filename}`")

            page_results, timed_out_during_processing, parsed_page_count = _process_pdf_bytes_with_fallback(
                pdf_bytes=repaired_pdf_bytes,
                file_name=filename,
                batch_size=Config.PDF_BATCH_SIZE,
                mistral_api_key=None,
                per_table_sleep_s=Config.PER_TABLE_SLEEP_SECONDS,
                start_time=start_time,
                time_limit_s=TIME_LIMIT_SECONDS
            )
            total_parsed_page_count += int(parsed_page_count or 0)

            if timed_out_during_processing:
                timed_out = True

            attachment_content_parts = [res.get('content', '') for res in page_results if res.get('content')]

            if attachment_content_parts:
                md_parts.append("\n\n".join(attachment_content_parts))
            else:
                if not timed_out:
                    md_parts.append("_No text found in this document._")

        except Exception as e:
            md_parts.append(f"Could not process `{filename}`: {e}")
            logging.error(
                f"FILE: {filename}\n"
                f"ERROR: Could not process PDF attachment: {e}\n"
                f"TRACEBACK:\n{traceback.format_exc()}"
            )
            traceback.print_exc()

    if timed_out:
        md_parts.append("\n\n**Time limit hit – remaining pages or documents were skipped.**")

    return "\n\n".join(md_parts), total_parsed_page_count

_CP1252_CTRL_RE = re.compile(r'[\x80-\x9F]')
_FILL_BLANK_RE = re.compile(r'[ \t]*[\u2002\u2003\u2004\u2005\u2006]{1,}[ \t]*')


def _has_cp1252_ctrls(s: str) -> bool:
    return bool(_CP1252_CTRL_RE.search(s))


def _preserve_fill_in_blanks(text: str) -> str:
    """Preserve SEC fill-in blanks encoded as em/en spaces before Unicode folds them."""
    if not any(ch in text for ch in "\u2002\u2003\u2004\u2005\u2006"):
        return text

    def repl(match: re.Match[str]) -> str:
        start, end = match.span()
        source = match.string
        prev_char = source[start - 1] if start > 0 else ""
        next_char = source[end] if end < len(source) else ""

        if prev_char == ">" and next_char == "<":
            return match.group(0)
        if (not prev_char or prev_char in "\r\n>") and next_char.isalnum():
            return match.group(0)
        if prev_char.isdigit() and next_char.isupper():
            return " "

        leading = " " if prev_char and (prev_char.isalnum() or prev_char in ")]}") else ""
        trailing = " " if next_char and (next_char.isalnum() or next_char in "([") else ""
        return f"{leading}____{trailing}"

    return _FILL_BLANK_RE.sub(repl, text)


def normalize_text_markup(markup):
    if isinstance(markup, bytes):
        utf8_text = None
        try:
            utf8_text = markup.decode('utf-8')
        except UnicodeDecodeError:
            utf8_text = None

        if utf8_text is not None and not _has_cp1252_ctrls(utf8_text):
            text = utf8_text
        else:
            markup = UnicodeDammit.detwingle(markup)

            utf8_text = None
            try:
                utf8_text = markup.decode('utf-8')
            except UnicodeDecodeError:
                utf8_text = None

            ud = UnicodeDammit(markup, is_html=True, smart_quotes_to='unicode')
            text = ud.unicode_markup
            detected_encoding = (ud.original_encoding or '').lower()

            if utf8_text is not None and detected_encoding.startswith('mac_'):
                text = utf8_text
            elif (not text) or ('\uFFFD' in text) or _has_cp1252_ctrls(text):
                text = markup.decode('latin-1', errors='strict')
    else:
        text = str(markup)

    text = text.translate(CP1252_CTRL_TO_UNICODE)

    mojibake_map = {
        'â�”': '—',
        'â�“': '–',
        'â�™': "'",
        'â�œ': '"',
        'â�d': '"',
        'â�‰': ' ',
    }
    for bad, good in mojibake_map.items():
        text = text.replace(bad, good)

    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&#160;&#160;&#160;&#160;", "##INDENT##")

    text = html.unescape(text)
    text = _preserve_fill_in_blanks(text)

    text = (text
            .replace('\u00AD', '')
            .replace('\u00A0', ' ')
            .replace('\u2007', ' ')
            .replace('\u202F', ' ')
            .replace('\u2009', ' ')
            .replace('\u2014', '—')
            .replace('\u2013', '–'))

    text = text.translate(PUNCT_CANON)

    return unicodedata.normalize('NFC', text)

__all__ = [name for name in globals() if not name.startswith("__")]
