from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.parsers.html.preprocessing import to_compact_markdown
from stanford_edgar_parser.parsers.plaintext.plaintext_parser import parse_plaintext_filing
from stanford_edgar_parser.utils.bootstrap import re

_pre_tag_re = re.compile(r'(?is)<pre\b.*?>.*?</pre>')

def _extract_pre_blocks(text: str, stash,
                        ph_fmt="__PRE_BLOCK_{:03d}__") -> str:
    """Replace every <pre>…</pre> with a unique placeholder and store the block."""
    def _sub(m):
        idx = len(stash)
        stash.append(m.group(0))
        return ph_fmt.format(idx)
    return _pre_tag_re.sub(_sub, text)

def parse_legacy_13f_hr_txt(raw_text: str) -> str:
    """
    Router function to detect the 13F-HR text format and call the correct parser.
    """
    if ("----------------" in raw_text and "NAME OF ISSUER" in raw_text) or "x x" not in raw_text:
        print("--> Detected fixed-width format.")
        return parse_plaintext_filing(raw_text)
    else:
        print("--> Detected free-form (OCR-style) format.")
        return _parse_free_form_13f_hr(raw_text)

def _parse_free_form_13f_hr(raw_text: str) -> str:
    """
    Parses legacy 13F-HR filings that are in a free-form, OCR-like text format.
    """
    import re, pandas as pd

    CUSIP_RE = re.compile(r'\b([0-9A-Za-z]{8}[0-9])\b')
    NUM_RE = re.compile(r'(\d{1,3}(?:,\d{3})*)')
    TITLE_CANDIDATES = [
        "Common Stock","Common","ADR","SPON ADR","Spon ADR","Spons ADR",
        "Preferred","PRFD","Convertible","Convert","Convert Bond","ConvertBond",
        "Debenture","Notes"
    ]
    HEADERS = [
        'NAME OF ISSUER','TITLE OF CLASS','CUSIP','VALUE (x$1000)',
        'SHRS OR PRN AMT','SH/PRN','PUT/CALL','INVESTMENT DISCRETION','OTHER MANAGER',
        'VOTING AUTHORITY (SOLE)','VOTING AUTHORITY (SHARED)','VOTING AUTHORITY (NONE)'
    ]

    def smart_title_case(text: str) -> str:
        words = text.split()
        title_cased_words = []
        for word in words:
            if word.isupper() and len(word) > 1:
                title_cased_words.append(word)
            else:
                title_cased_words.append(word.capitalize())
        return ' '.join(title_cased_words)

    def _normalize_number_token(s: str) -> str:
        return s.replace(',', '')

    def _pick_title(text: str) -> str:
        up = text.upper()
        best = None
        for t in TITLE_CANDIDATES:
            if t.upper() in up:
                if not best or up.rfind(t.upper()) > up.rfind(best.upper()):
                    best = t
        return best or "Common"

    table_start = re.search(r'Form 13F Information Table', raw_text, re.I | re.S)
    if not table_start:
        return "<!-- Could not find 'Form 13F Information Table' marker -->"

    cover = raw_text[:table_start.start()]
    cover = "\n".join(l.strip() for l in cover.splitlines() if l.strip())
    cover = re.sub(r'FORM 13F\s+FORM 13F', '## FORM 13F (Some records may not parsed due to OCR errors in the original filing)', cover, flags=re.I)
    cover = re.sub(r'COVER PAGE', '\n\n### COVER PAGE', cover, flags=re.I)
    cover = re.sub(r'SUMMARY PAGE', '\n\n### SUMMARY PAGE', cover, flags=re.I)
    cover = re.sub(r'Name:', '\n\n**Name:**', cover, flags=re.I)
    cover = re.sub(r'Address:', '\n\n**Address:**', cover, flags=re.I)
    cover = re.sub(r'Person signing this report on behalf of Reporting Manager:',
                   '\n\n**Person signing this report on behalf of Reporting Manager:**',
                   cover, flags=re.I)

    blob = raw_text[table_start.end():]
    blob = re.sub(r'</?TABLE>|</?C>', ' ', blob, flags=re.I)
    blob = re.sub(r'\s+', ' ', blob).strip()
    blob = re.sub(r'(\d),\s+(\d{3})', r'\1,\2', blob)
    blob = re.sub(r'(\d{1,3}),\s+(\d)\s+(\d{3}\b)', r'\1 \2,\3', blob)

    matches = list(CUSIP_RE.finditer(blob))
    holdings = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i+1].start() if i+1 < len(matches) else len(blob)
        rec = blob[start:end].strip()

        cusip = m.group(1).upper()
        rest = rec[len(m.group(1)):].strip()

        nums = NUM_RE.findall(rest)
        value_tok = nums[0] if len(nums) >= 1 else '0'
        shares_tok = nums[1] if len(nums) >= 2 else '0'

        first_num_pos = rest.find(value_tok) if nums else -1

        issuer_title_text = rest
        if first_num_pos != -1:
            issuer_title_text = rest[:first_num_pos].strip()
        else:
            if ' x' in rest.lower():
                 issuer_title_text = rest.lower().split(' x')[0].strip()

        value_raw = _normalize_number_token(value_tok)
        shares_raw = _normalize_number_token(shares_tok)

        title = _pick_title(issuer_title_text)
        issuer_text = re.sub(re.escape(title) + r'$', '', issuer_title_text, flags=re.I).strip()
        issuer_text = re.sub(r'\bSpons?\b\s*A(?:DR)?\b$', '', issuer_text, flags=re.I).strip()
        issuer_text = re.sub(r'\s{2,}', ' ', issuer_text)

        issuer = smart_title_case(issuer_text)

        x_count = len(re.findall(r'\bx\b', rec, flags=re.I))
        inv_disc = "SOLE" if x_count >= 1 else "—"
        voting_sole = int(shares_raw) if (x_count >= 1 and shares_raw.isdigit() and int(shares_raw) > 0) else 0

        is_prn = any(k in title.upper() for k in ("CONVERT", "DEBENT", "NOTES"))
        row = {
            'NAME OF ISSUER': issuer or '—',
            'TITLE OF CLASS': 'ConvertBond' if 'CONVERT' in title.upper() else (title or 'Common'),
            'CUSIP': cusip,
            'VALUE (x$1000)': f"{int(value_raw):}" if value_raw.isdigit() and int(value_raw) > 0 else '—',
            'SHRS OR PRN AMT': f"{int(shares_raw):}" if shares_raw.isdigit() and int(shares_raw) > 0 else '—',
            'SH/PRN': 'PRN' if is_prn else 'SH',
            'PUT/CALL': '—',
            'INVESTMENT DISCRETION': inv_disc,
            'OTHER MANAGER': '—',
            'VOTING AUTHORITY (SOLE)': f"{voting_sole:,}" if voting_sole > 0 else '—',
            'VOTING AUTHORITY (SHARED)': '—',
            'VOTING AUTHORITY (NONE)': '—',
        }
        holdings.append(row)

    if not holdings:
        return f"{cover}\n\n<!-- No holdings data parsed from free-form 13F-HR -->"

    df = pd.DataFrame(holdings).reindex(columns=HEADERS).fillna('—')
    return f"{cover}\n\n{to_compact_markdown(df, index=False)}"

__all__ = [name for name in globals() if not name.startswith("__")]
