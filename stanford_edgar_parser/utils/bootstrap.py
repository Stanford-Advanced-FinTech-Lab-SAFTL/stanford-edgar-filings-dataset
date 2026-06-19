#!/usr/bin/env python
from __future__ import annotations
import stanford_edgar_parser._state as _state

import sys
import re
import pathlib
import textwrap
import pandas as pd
import numpy as np
import math
import io
from bs4 import BeautifulSoup, Comment, Declaration, Doctype, NavigableString, ProcessingInstruction, Tag, UnicodeDammit
import traceback
import datetime
from typing import Any, Dict, List, Optional, Tuple
import argparse
from urllib.parse import quote, unquote, urljoin
import html
import itertools
import unicodedata
import binascii
import logging
import hashlib
import fcntl

if not hasattr(pd.DataFrame, "applymap") and hasattr(pd.DataFrame, "map"):
    def _dataframe_applymap(self, func, na_action=None, **kwargs):
        if kwargs:
            def _wrapped(value):
                return func(value, **kwargs)
            return self.map(_wrapped, na_action=na_action)
        return self.map(func, na_action=na_action)
    pd.DataFrame.applymap = _dataframe_applymap

from stanford_edgar_parser.hardcodes import apply_markdown_hardcodes

from collections import defaultdict
from statistics import median

import os, io, json, time, traceback, requests

from dotenv import load_dotenv
SEC_PARSER_DOTENV_PATH = pathlib.Path(__file__).resolve().parents[1] / ".env"


def _load_sec_parser_env() -> None:
    load_dotenv()
    if SEC_PARSER_DOTENV_PATH.exists():
        load_dotenv(SEC_PARSER_DOTENV_PATH, override=False)


_load_sec_parser_env()
from pydantic import BaseModel, Field, ConfigDict, ValidationError
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.errors import PdfReadError
from mistralai import Mistral, DocumentURLChunk
from mistralai.extra import response_format_from_pydantic_model

from stanford_edgar_parser.special_chars import WINGDINGS_MAP

import random
from mistralai.models.sdkerror import SDKError

import fitz
import base64

import imgkit
from playwright.sync_api import sync_playwright

from stanford_edgar_parser.config import Config

log_file_path = pathlib.Path(__file__).resolve().parents[1] / 'sec_parser_errors.log'
ocr_log_file_path = pathlib.Path(__file__).resolve().parents[2] / 'pdf_files.log'
parse_stats_log_file_path = pathlib.Path(__file__).resolve().parents[1] / 'sec_parser_parse_stats.jsonl'
parse_stats_summary_file_path = pathlib.Path(__file__).resolve().parents[1] / 'sec_parser_parse_stats_summary.json'

logger = logging.getLogger()
logger.setLevel(logging.ERROR)

handler = logging.FileHandler(log_file_path, mode='a', encoding='utf-8')
formatter = logging.Formatter('%(asctime)s\n%(message)s\n' + '-'*80, datefmt='%Y-%m-%d %H:%M:%S')

handler.setFormatter(formatter)
logger.addHandler(handler)

ocr_logger = logging.getLogger("ocr_tracker")
ocr_logger.setLevel(logging.INFO)
ocr_logger.propagate = False

ocr_handler = logging.FileHandler(ocr_log_file_path, mode='a', encoding='utf-8')
ocr_handler.setFormatter(formatter)
ocr_logger.addHandler(ocr_handler)

DEFAULT_FONT = 16.0

_state.CURRENT_PROCESSING_FILE = "Unknown"
_state.CURRENT_OCR_LOGGED_FILINGS = set()
_state.CURRENT_SOURCE_DOCUMENT_URL = None
_state.LAST_PARSE_STATS = None
_state.LAST_POSITIONED_HTML_OCR_PAGE_COUNT = 0
_TOKENIZER = None
_TOKENIZER_KEY = None
_TOKENIZER_KIND = None

__all__ = [name for name in globals() if not name.startswith("__")]
