import os


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


class Config:

    HTML_TIMEOUT_LIMIT = 15 # overall timeout for parsing a single HTML document (in minutes)
    PDF_TIMEOUT_LIMIT = 12 # timeout for processing all PDF attachments within a filing (in minutes)

    SKIP_OCR = _env_flag("SEC_PARSER_SKIP_OCR")

    OCR_MODEL = os.getenv("MISTRAL_OCR_MODEL", "mistral-ocr-latest") # model used for OCR on PDFs and rendered page images

    # Legacy settings for separate OCR-bench utilities. sec_parser no longer
    # calls FireRed/Qwen for table-specific second-pass transcription.
    FIRERED_MODEL_LOCAL_DIR = os.getenv("FIRERED_MODEL_LOCAL_DIR", "").strip() or None
    FIRERED_MODEL_CACHE_DIR = os.getenv("FIRERED_MODEL_CACHE_DIR", "").strip() or None
    FIRERED_MODEL_REVISION = os.getenv("FIRERED_MODEL_REVISION", "").strip() or None
    FIRERED_LOCAL_FILES_ONLY = _env_flag("FIRERED_LOCAL_FILES_ONLY")
    FIRERED_DEVICE = os.getenv("FIRERED_DEVICE", "auto").strip().lower() or "auto"
    FIRERED_DEVICE_MAP = os.getenv("FIRERED_DEVICE_MAP", "auto").strip() or "auto"
    FIRERED_MAX_NEW_TOKENS = _env_int("FIRERED_MAX_NEW_TOKENS", 4096)
    FIRERED_MAX_IMAGE_PIXELS = _env_int("FIRERED_MAX_IMAGE_PIXELS", 0)
    FIRERED_MPS_AUTO_MAX_IMAGE_PIXELS = _env_int("FIRERED_MPS_AUTO_MAX_IMAGE_PIXELS", 2000000)

    PER_TABLE_SLEEP_SECONDS = 0.375 # legacy delay for old second-pass table OCR utilities
    API_MAX_RETRIES = 5 # max number of retries for failed OCR API calls
    API_INITIAL_DELAY_SECONDS = 0.5 # initial delay before the first API retry; increases exponentially

    PDF_BATCH_SIZE = 10 # number of PDF pages to process in a single batch to the OCR API (10 was found to be the most efficient on my machine)
    IMAGE_RENDER_ZOOM = 3 # zoom factor for rendering PDF pages to images for OCR (probably wouldn't touch this)
