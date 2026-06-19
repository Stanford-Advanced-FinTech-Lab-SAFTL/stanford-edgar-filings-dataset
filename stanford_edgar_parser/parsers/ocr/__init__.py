def __getattr__(name: str):
    if name in {"get_mistral_key_status_snapshot", "reset_mistral_key_status"}:
        from . import rotate_auth

        return getattr(rotate_auth, name)
    if name == "parse_pdf_attachments":
        from . import ocr_utils

        return ocr_utils.parse_pdf_attachments
    if name == "parse_html_via_ocr":
        from . import ocr

        return ocr.parse_html_via_ocr
    raise AttributeError(name)

__all__ = [
    "get_mistral_key_status_snapshot",
    "parse_html_via_ocr",
    "parse_pdf_attachments",
    "reset_mistral_key_status",
]
