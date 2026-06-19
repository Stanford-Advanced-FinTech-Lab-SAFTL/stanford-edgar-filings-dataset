def __getattr__(name: str):
    if name in {"parse_legacy_13f_hr_txt", "parse_legacy_paper_filing"}:
        from . import legacy_form_parsers

        return getattr(legacy_form_parsers, name)
    if name == "parse_plaintext_filing":
        from . import plaintext_parser

        return plaintext_parser.parse_plaintext_filing
    raise AttributeError(name)

__all__ = ["parse_legacy_13f_hr_txt", "parse_legacy_paper_filing", "parse_plaintext_filing"]
