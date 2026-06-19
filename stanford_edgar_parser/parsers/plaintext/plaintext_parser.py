from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.utils.bootstrap import pd, re

def _finish_block(rows, header_hint):
    df = pd.DataFrame(rows)

    if header_hint and len(header_hint) == df.shape[1] - 1:
        df.columns = ["Label", *header_hint]
    else:
        df.columns = ["Label"] + [f"Col {i}" for i in range(1, df.shape[1])]

    for idx in df.index:
        row_vals = df.loc[idx].tolist()
        label, rest = row_vals[0], row_vals[1:]

        merged = []
        skip_next = False
        for i, v in enumerate(rest):
            if skip_next:
                skip_next = False
                continue
            if isinstance(v, str) and v.strip() == "$":
                j = i + 1
                while j < len(rest) and (rest[j] is None or str(rest[j]).strip() == ""):
                    j += 1
                merged.append("$" + ("" if j >= len(rest) else str(rest[j]).strip()))
                if j < len(rest):
                    rest[j] = ""
                skip_next = False
            else:
                merged.append(v)

        merged = [x for x in merged if not (isinstance(x, str) and x.strip() == "")]
        merged += [""] * (len(rest) - len(merged))

        df.loc[idx, df.columns[1:]] = merged

    df.columns = ["" if str(c).startswith("Col ") else c for c in df.columns]

    return df

DOT_ROW = re.compile(
    r"""^\s*
        (?P<label>[A-Za-z].*?)
        (?:\.{2,}|\s{2,})\s*
        (?P<nums>[$()\-0-9,.\s]+)
        \s*$
    """,
    re.X,
)

DOT_ROW = re.compile(
    r"""^\s*
        (?P<label>[A-Za-z].*?)
        (?:\.{2,}|\s{2,})\s*
        (?P<nums>[$()\-0-9,.\s]+)
        \s*$
    """,
    re.X,
)

HEADER_HINT_RE = re.compile(r"^\s*(\d{4}|[A-Za-z]{3,}).*\s{2,}")
SGML_TAG_RE = re.compile(r"<S>|<C>")

def parse_plaintext_filing(raw_text: str) -> str:
    pem_pattern = r'-----BEGIN PRIVACY-ENHANCED MESSAGE-----(.*?)-----END PRIVACY-ENHANCED MESSAGE-----'
    text_inside_pem = re.search(pem_pattern, raw_text, re.DOTALL)
    
    if not text_inside_pem:
        ims_pattern = r'<IMS-HEADER>.*?</IMS-HEADER>|<IMS-DOCUMENT>.*?</IMS-DOCUMENT>'
        content = re.sub(ims_pattern, '', raw_text, flags=re.DOTALL)
    else:
        content = text_inside_pem.group(1)
        header_pattern = r'<IMS-HEADER>.*?</IMS-HEADER>'
        content_no_header = re.sub(header_pattern, '', content, flags=re.DOTALL)
        doc_tags_pattern = r'<IMS-DOCUMENT>.*?\n|</IMS-DOCUMENT>'
        content = re.sub(doc_tags_pattern, '', content_no_header, flags=re.DOTALL)
    
    cleaned_text = re.sub(r'\n{3,}', '\n\n', content)

    cleaned_text = cleaned_text.replace("</TEXT>", "")

    spaced_text = f"\n```\n{cleaned_text}\n```\n"
    
    return spaced_text

__all__ = [name for name in globals() if not name.startswith("__")]
