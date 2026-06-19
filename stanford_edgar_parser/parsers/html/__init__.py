def __getattr__(name: str):
    if name == "parse_html_filing":
        from . import html

        return html.parse_html_filing
    if name == "parse_html_via_pdf_render":
        from . import preprocessing

        return preprocessing.parse_html_via_pdf_render
    if name == "parse_html_via_ocr":
        from ..ocr import ocr

        return ocr.parse_html_via_ocr
    raise AttributeError(name)

__all__ = ["parse_html_filing", "parse_html_via_ocr", "parse_html_via_pdf_render"]
