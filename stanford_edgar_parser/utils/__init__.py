def __getattr__(name: str):
    if name == "estimate_parser_tokens":
        from .tokenizer import estimate_parser_tokens

        return estimate_parser_tokens
    if name == "normalize_text_markup":
        from ..parsers.ocr.ocr_utils import normalize_text_markup

        return normalize_text_markup
    raise AttributeError(name)

__all__ = ["estimate_parser_tokens", "normalize_text_markup"]
