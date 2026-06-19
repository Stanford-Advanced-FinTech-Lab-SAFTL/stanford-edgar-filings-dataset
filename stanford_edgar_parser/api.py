"""Explicit public API for the Stanford EDGAR parser."""

from stanford_edgar_parser.multimarkdown.multimarkdown import (
    convert_all_tables_to_mmd,
    df_to_multimarkdown,
)
from stanford_edgar_parser.orchestrator import main_one, process_local_xbrl
from stanford_edgar_parser.parsers.html.html import parse_html_filing
from stanford_edgar_parser.parsers.html.preprocessing import parse_html_via_pdf_render
from stanford_edgar_parser.parsers.html.table_cleaning import (
    clean_financial_df,
    df_to_markdown,
)
from stanford_edgar_parser.parsers.ocr.rotate_auth import (
    get_mistral_key_status_snapshot,
    reset_mistral_key_status,
)
from stanford_edgar_parser.parsers.ocr.ocr import parse_html_via_ocr
from stanford_edgar_parser.parsers.ocr.ocr_utils import (
    normalize_text_markup,
    parse_pdf_attachments,
)
from stanford_edgar_parser.parsers.plaintext.plaintext_parser import parse_plaintext_filing
from stanford_edgar_parser.parsers.xml.regulatory_forms import parse_any_xml
from stanford_edgar_parser.utils.tokenizer import (
    estimate_parser_tokens,
    normalize_form_type_for_stats,
)

__all__ = [
    "clean_financial_df",
    "convert_all_tables_to_mmd",
    "df_to_markdown",
    "df_to_multimarkdown",
    "estimate_parser_tokens",
    "get_mistral_key_status_snapshot",
    "main_one",
    "normalize_form_type_for_stats",
    "normalize_text_markup",
    "parse_any_xml",
    "parse_html_filing",
    "parse_html_via_ocr",
    "parse_html_via_pdf_render",
    "parse_pdf_attachments",
    "parse_plaintext_filing",
    "process_local_xbrl",
    "reset_mistral_key_status",
]
