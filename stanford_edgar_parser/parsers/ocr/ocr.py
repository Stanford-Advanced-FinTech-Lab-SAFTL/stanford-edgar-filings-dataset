from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.parsers.ocr.rotate_auth import (
    OCR_API_URL,
    _has_mistral_api_keys,
    _mistral_no_keys_message,
    _record_mistral_key_success,
    _run_with_mistral_key_rotation,
    _summarize_ocr_usage,
)
from stanford_edgar_parser.parsers.ocr.ocr_utils import (
    _build_mistral_ocr_payload,
    _pick_text,
    get_signed_url_with_retry,
)
from stanford_edgar_parser.utils.bootstrap import (
    BeautifulSoup,
    Config,
    Optional,
    _load_sec_parser_env,
    imgkit,
    pathlib,
    random,
    re,
    requests,
    time,
)
from stanford_edgar_parser.utils.parse_stats import _log_current_filing_ocr

def _process_image_bytes_with_mistral(
    image_bytes: bytes,
    file_name: str,
    page_no: int,
    *,
    per_table_sleep_s: float,
    mistral_api_key: Optional[str] = None,
):
    """
    Processes a single rendered page image using Mistral OCR.
    This is an adaptation of your PDF processing logic for a single image.
    """
    try:
        def _run_page_ocr(*, client: Mistral, api_key: str, key_spec: Dict[str, Any]):
            up = client.files.upload(file={"file_name": f"page_{page_no}_{file_name}", "content": image_bytes}, purpose="ocr")
            if not up or not up.id:
                raise Exception("Image upload failed to return a valid ID.")

            signed_url = get_signed_url_with_retry(client, file_id=up.id)
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = _build_mistral_ocr_payload(signed_url)
            max_ocr_retries = 4
            ocr_delay = 2.0

            for attempt in range(max_ocr_retries):
                response = requests.post(OCR_API_URL, headers=headers, json=payload, timeout=600)

                if response.status_code in [429, 500, 502, 503, 504]:
                    if attempt < max_ocr_retries - 1:
                        print(f"OCR API error {response.status_code}. Retrying in {ocr_delay:.2f}s... (Attempt {attempt + 1}/{max_ocr_retries})")
                        time.sleep(ocr_delay)
                        ocr_delay *= 2
                        ocr_delay += random.uniform(0, 0.5)
                        continue

                response.raise_for_status()
                break

            ocr_data = response.json()
            usage = _summarize_ocr_usage(ocr_data, response.headers)
            if not ocr_data.get("pages"):
                print(f"[page {page_no}] OCR returned no content.")
                return "", usage, key_spec["env_name"]

            page_obj = ocr_data["pages"][0]
            return _pick_text(page_obj), usage, key_spec["env_name"]

        page_text, usage, used_env_name = _run_with_mistral_key_rotation(
            f"rendered page {page_no} for {file_name}",
            _run_page_ocr,
            explicit_api_key=mistral_api_key,
        )
        _record_mistral_key_success(used_env_name, usage=usage, explicit_api_key=mistral_api_key)
        return page_text

    except Exception as e:
        print(f"API processing for page {page_no} failed and was skipped. Error: {e}")
        return f"<!-- Error processing page {page_no}: {e} -->"

def parse_html_via_ocr(filepath: pathlib.Path) -> str:
    """
    High-quality OCR-based parser for positioned HTML. Renders each page to an
    image and uses Mistral OCR to extract text and tables.
    """
    _load_sec_parser_env()
    if Config.SKIP_OCR:
        _log_current_filing_ocr("html_image_ocr_skipped")
        return "<!-- HTML image OCR skipped by SEC_PARSER_SKIP_OCR. -->"
    if not _has_mistral_api_keys():
        print(f"{_mistral_no_keys_message()} Skipping OCR processing.")
        return "<!-- No Mistral API keys found. Positioned HTML was not processed. -->"

    _log_current_filing_ocr("html_image_ocr")
    html_content = filepath.read_text(encoding='utf-8', errors='replace')
    try:
        soup = BeautifulSoup(html_content, "lxml")
    except ValueError as e:
        if "not enough values to unpack" in str(e):
            print(f"[Warning] lxml parser crashed on malformed attributes. Falling back to html.parser.")
            soup = BeautifulSoup(html_content, "html.parser")
        else:
            raise

    pages = soup.find_all('div', id=re.compile(r'^pf\w+$'))
    if not pages:
        print("Could not find page containers (e.g., <div id='pf1'>). Treating document as a single page.")
        pages = [soup]

    md_parts = []
    
    options = {
        'format': 'png',
        'quality': '100',
        'width': '1224',
        'disable-smart-width': ''
    }

    print(f"Found {len(pages)} pages to process via OCR.")
    for i, page_soup in enumerate(pages, 1):
        print(f"--> Rendering and processing page {i}...")
        try:
            image_bytes = imgkit.from_string(str(page_soup), False, options=options)
            
            page_content = _process_image_bytes_with_mistral(
                image_bytes=image_bytes,
                file_name=filepath.name,
                page_no=i,
                per_table_sleep_s=Config.PER_TABLE_SLEEP_SECONDS,
                mistral_api_key=None,
            )
            md_parts.append(page_content)

        except Exception as e:
            error_msg = f"Failed to render or process page {i}: {e}"
            print(error_msg)
            md_parts.append(f"<!-- {error_msg} -->")

    return "\n\n------\n\n".join(md_parts)

__all__ = [name for name in globals() if not name.startswith("__")]
