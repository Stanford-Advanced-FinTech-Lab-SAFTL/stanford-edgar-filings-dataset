"""Stanford EDGAR filings parser package."""

from . import _state
from . import api as _sec_parser

__version__ = "0.1.2"

clean_financial_df = _sec_parser.clean_financial_df
convert_all_tables_to_mmd = _sec_parser.convert_all_tables_to_mmd
df_to_markdown = _sec_parser.df_to_markdown
df_to_multimarkdown = _sec_parser.df_to_multimarkdown
estimate_parser_tokens = _sec_parser.estimate_parser_tokens
main_one = _sec_parser.main_one
normalize_text_markup = _sec_parser.normalize_text_markup
parse_any_xml = _sec_parser.parse_any_xml
parse_html_filing = _sec_parser.parse_html_filing
parse_pdf_attachments = _sec_parser.parse_pdf_attachments
parse_plaintext_filing = _sec_parser.parse_plaintext_filing
process_local_xbrl = _sec_parser.process_local_xbrl


def __getattr__(name: str):
    if hasattr(_state, name):
        return getattr(_state, name)
    return getattr(_sec_parser, name)

__all__ = [
    "LAST_PARSE_STATS",
    "__version__",
    "clean_financial_df",
    "convert_all_tables_to_mmd",
    "df_to_markdown",
    "df_to_multimarkdown",
    "estimate_parser_tokens",
    "main_one",
    "normalize_text_markup",
    "parse_any_xml",
    "parse_html_filing",
    "parse_pdf_attachments",
    "parse_plaintext_filing",
    "process_local_xbrl",
]
