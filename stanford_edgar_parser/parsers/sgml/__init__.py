def __getattr__(name: str):
    if name == "parse_legacy_paper_filing":
        from ..plaintext import legacy_form_parsers

        return legacy_form_parsers.parse_legacy_paper_filing
    if name in {"parse_series_and_classes_sgml", "process_local_xbrl"}:
        from . import sgml_utils

        return getattr(sgml_utils, name)
    raise AttributeError(name)

__all__ = ["parse_legacy_paper_filing", "parse_series_and_classes_sgml", "process_local_xbrl"]
