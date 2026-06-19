"""Mutable parser state shared by import-native parser modules."""

CURRENT_PROCESSING_FILE = "Unknown"
CURRENT_OCR_LOGGED_FILINGS = set()
CURRENT_SOURCE_DOCUMENT_URL = None
LAST_PARSE_STATS = None
LAST_POSITIONED_HTML_OCR_PAGE_COUNT = 0
