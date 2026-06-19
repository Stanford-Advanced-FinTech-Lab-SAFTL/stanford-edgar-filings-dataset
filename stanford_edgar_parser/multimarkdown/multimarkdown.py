from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.utils.bootstrap import pd, re, time

ORDER_I = [
    "1. Title of Security##ROWSPAN_1##<br>1. Title of Security##ROWSPAN_1##",
    "2. Transaction Date##ROWSPAN_2##<br>2. Transaction Date##ROWSPAN_2##",
    "2A. Deemed Execution Date##ROWSPAN_3##<br>2A. Deemed Execution Date##ROWSPAN_3##",
    "3. Transaction Code (V)##COLSPAN_1##<br>Code",
    "3. Transaction Code (V)##COLSPAN_1##<br>V",
    "4. Securities Acquired (A) or Disposed of (D)##COLSPAN_2##<br>Amount",
    "4. Securities Acquired (A) or Disposed of (D)##COLSPAN_2##<br>(A) or (D)",
    "4. Securities Acquired (A) or Disposed of (D)##COLSPAN_2##<br>Price",
    "5. Amount of Securities Beneficially Owned##ROWSPAN_4##<br>5. Amount of Securities Beneficially Owned##ROWSPAN_4##",
    "6. Ownership Form##ROWSPAN_5##<br>6. Ownership Form##ROWSPAN_5##",
    "7. Nature of Indirect Beneficial Ownership##ROWSPAN_6##<br>7. Nature of Indirect Beneficial Ownership##ROWSPAN_6##",
]

ORDER_II = [
    "1. Title of Derivative Security##ROWSPAN_7##<br>1. Title of Derivative Security##ROWSPAN_7##",
    "2. Conversion or Exercise Price##ROWSPAN_8##<br>2. Conversion or Exercise Price##ROWSPAN_8##",
    "3. Transaction Date##ROWSPAN_9##<br>3. Transaction Date##ROWSPAN_9##",
    "3A. Deemed Execution Date##ROWSPAN_10##<br>3A. Deemed Execution Date##ROWSPAN_10##",
    "4. Transaction Code (V)##COLSPAN_3##<br>Code",
    "4. Transaction Code (V)##COLSPAN_3##<br>V",
    "5. Number of Derivative Securities Acquired (A) or Disposed of (D)##COLSPAN_4##<br>(A)",
    "5. Number of Derivative Securities Acquired (A) or Disposed of (D)##COLSPAN_4##<br>(D)",
    "6. Date Exercisable and Expiration Date##COLSPAN_5##<br>Date Exercisable",
    "6. Date Exercisable and Expiration Date##COLSPAN_5##<br>Expiration Date",
    "7. Title and Amount of Underlying Securities##COLSPAN_6##<br>Title",
    "7. Title and Amount of Underlying Securities##COLSPAN_6##<br>Amount or Number of Shares",
    "8. Price of Derivative Security##ROWSPAN_11##<br>8. Price of Derivative Security##ROWSPAN_11##",
    "9. Number of Derivative Securities Beneficially Owned##ROWSPAN_12##<br>9. Number of Derivative Securities Beneficially Owned##ROWSPAN_12##",
    "10. Ownership Form##ROWSPAN_13##<br>10. Ownership Form##ROWSPAN_13##",
    "11. Nature of Indirect Beneficial Ownership##ROWSPAN_14##<br>11. Nature of Indirect Beneficial Ownership##ROWSPAN_14##",
]

ORDER_I_FORM3 = [
    "1. Title of Security",
    "2. Amount of Securities Beneficially Owned",
    "3. Ownership Form",
    "4. Nature of Indirect Beneficial Ownership",
]

ORDER_II_FORM3 = [
    "1. Title of Derivative Security##ROWSPAN_1##<br>1. Title of Derivative Security##ROWSPAN_1##",
    "2. Date Exercisable and Expiration Date (Month/Day/Year)##COLSPAN_1##<br>Date Exercisable",
    "2. Date Exercisable and Expiration Date (Month/Day/Year)##COLSPAN_1##<br>Expiration Date",
    "3. Title and Amount of Underlying Securities##COLSPAN_2##<br>Title",
    "3. Title and Amount of Underlying Securities##COLSPAN_2##<br>Amount or Number of Shares",
    "4. Conversion or Exercise Price##ROWSPAN_2##<br>4. Conversion or Exercise Price##ROWSPAN_2##",
    "5. Ownership Form##ROWSPAN_3##<br>5. Ownership Form##ROWSPAN_3##",
    "6. Nature of Indirect Beneficial Ownership##ROWSPAN_4##<br>6. Nature of Indirect Beneficial Ownership##ROWSPAN_4##",
]

SEC_COUNTRY_CODES = {
    'B9': 'ANTIGUA AND BARBUDA',
    'E9': 'CAYMAN ISLANDS',
    'F4': 'CHINA',
    'K3': 'HONG KONG',
    'AL': 'ALABAMA', 'AK': 'ALASKA', 'AZ': 'ARIZONA', 'AR': 'ARKANSAS', 'CA': 'CALIFORNIA',
    'CO': 'COLORADO', 'CT': 'CONNECTICUT', 'DE': 'DELAWARE', 'DC': 'DISTRICT OF COLUMBIA',
    'FL': 'FLORIDA', 'GA': 'GEORGIA', 'HI': 'HAWAII', 'ID': 'IDAHO', 'IL': 'ILLINOIS',
    'IN': 'INDIANA', 'IA': 'IOWA', 'KS': 'KANSAS', 'KY': 'KENTUCKY', 'LA': 'LOUISIANA',
    'ME': 'MAINE', 'MD': 'MARYLAND', 'MA': 'MASSACHUSETTS', 'MI': 'MICHIGAN',
    'MN': 'MINNESOTA', 'MS': 'MISSISSIPPI', 'MO': 'MISSOURI', 'MT': 'MONTANA',
    'NE': 'NEBRASKA', 'NV': 'NEVADA', 'NH': 'NEW HAMPSHIRE', 'NJ': 'NEW JERSEY',
    'NM': 'NEW MEXICO', 'NY': 'NEW YORK', 'NC': 'NORTH CAROLINA', 'ND': 'NORTH DAKOTA',
    'OH': 'OHIO', 'OK': 'OKLAHOMA', 'OR': 'OREGON', 'PA': 'PENNSYLVANIA',
    'RI': 'RHODE ISLAND', 'SC': 'SOUTH CAROLINA', 'SD': 'SOUTH DAKOTA',
    'TN': 'TENNESSEE', 'TX': 'TEXAS', 'UT': 'UTAH', 'VT': 'VERMONT', 'VA': 'VIRGINIA',
    'WA': 'WASHINGTON', 'WV': 'WEST VIRGINIA', 'WI': 'WISCONSIN', 'WY': 'WYOMING',
    'A0': 'ALBERTA', 'A1': 'BRITISH COLUMBIA', 'A2': 'MANITOBA', 'A3': 'NEW BRUNSWICK',
    'A4': 'NEWFOUNDLAND', 'A5': 'NOVA SCOTIA', 'A6': 'ONTARIO', 'A7': 'PRINCE EDWARD ISLAND',
    'A8': 'QUEBEC', 'A9': 'SASKATCHEWAN', 'B0': 'YUKON TERRITORY',
    'D4': 'GERMANY', 'G6': 'NETHERLANDS', 'H2': 'SWITZERLAND', 'L8': 'UNITED KINGDOM',
    'Z4': 'ISRAEL'
}

ITEM_HEADING = re.compile(
    r'^\s*Item[\s\u00A0]+\d+[A-Za-z]?\.[^.]*\s*$', re.I
)
SUBITEM_HEAD  = re.compile(r'^\s*\([A-Za-z0-9]+\)\s+.+$')

_sup_re = re.compile(r'<sup>(.*?)</sup>', re.I | re.S)

PART_HEADING  = re.compile(r'^\s*PART\s+[IVXLC]+\b.*\s*$', re.I)

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

def check_timeout(start_time: float, time_limit_s: int, stage_name: str):
    """
    Checks if the elapsed time has exceeded the limit.
    Returns a placeholder string on timeout, otherwise returns None.
    """
    if time.time() - start_time > time_limit_s:
        minutes = time_limit_s // 60
        print(f"[timeout] {stage_name} exceeded {minutes} minutes. Stopping.")
        return "\n\n<!-- PARSING OF THIS DOCUMENT EXCEEDED 15 MINUTES - CONTENT TRUNCATED -->\n\n"
    return None

def _sup_to_caret(txt: str) -> str:
    """<sup>foo</sup> → ^foo^  (markdown-it-sup)."""
    if not isinstance(txt, str):
        return str(txt)
    return _sup_re.sub(r'^\1^', txt)

def _normalize_markdown_emphasis(txt: str) -> str:
    txt = re.sub(r'(?<!\*)\*%\*\*\*(?!\*)', r'***%***', txt)
    txt = re.sub(
        r'(?m)^(\s*)\*\*\*([^*\n]*?)\*\*\*\*([^*\n]+)\*\s*$',
        lambda m: f"{m.group(1)}***{m.group(2)}{m.group(3)}***",
        txt,
    )
    txt = re.sub(
        r'(?<!\*)\*("?)\*\*\*\*([A-Za-z][^*\n]{0,80}?)\*\*\*\*',
        r'*\1**\2**',
        txt,
    )
    txt = re.sub(r'\*\*([,.:;])\*\*', r'\1', txt)
    txt = re.sub(r'\*\*\(\*\*([^*\n]+?)\*\*\)\*\*', r'**(\1)**', txt)
    txt = re.sub(r'(\*\*\*[^*|\n]+?)\*([.,:;])\*\*', r'\1\2***', txt)
    txt = re.sub(r'(\*\*[^*|\n]+?)\*([.,:;])\*\*\*', r'\1\2**', txt)
    return txt

def df_to_multimarkdown(df: pd.DataFrame) -> str:
    """
    Ultra-compact MultiMarkdown table with intelligent merging for both
    rowspan and colspan based on unique identifiers. This version correctly
    handles complex nested and overlapping spans.
    """
    if df.empty:
        return ""

    from stanford_edgar_parser.parsers.html.table_cleaning import drop_tag_only_rows_cols

    df = drop_tag_only_rows_cols(df, cols_only=True)

    COLSPAN_MARKER = "##__COLSPAN__##"
    ROWSPAN_MARKER = "^^"

    proc = df.copy().astype(str).fillna('')

    def _plain_cell(value: object, *, drop_leading_currency: bool = False) -> str:
        text = str(value)
        text = re.sub(r'##(?:ROWSPAN|COLSPAN)_\w+##', '', text)
        text = re.sub(r'##(?:BOLD|ITALIC|U)_(?:START|END)_\d+##', '', text)
        text = re.sub(r'##LINK_START_\d+__[^#]+##|##LINK_END_\d+##', '', text)
        text = re.sub(r'</?[^>]+>', '', text)
        text = text.replace('&nbsp;', ' ').replace('\u00a0', ' ')
        text = text.replace('**', '').replace('*', '')
        text = re.sub(r'\s+', ' ', text).strip()
        if text.lower() == 'nan':
            return ''
        if drop_leading_currency:
            text = re.sub(r'^\s*[$£€]\s*', '', text).strip()
        return text

    def _normalize_shifted_header_spans(frame: pd.DataFrame) -> pd.DataFrame:
        """
        Filing-agent spacer grids sometimes put a period header over the right
        edge of an expanded value span. If the body columns are duplicate
        colspan expansions, shift the header to the left edge before MMD
        serialization.
        """
        if frame.shape[0] < 3 or frame.shape[1] < 3:
            return frame

        out = frame.copy()
        max_header_rows = min(3, out.shape[0] - 1)

        for col_idx in range(out.shape[1] - 1):
            header_rows = []
            for row_idx in range(max_header_rows):
                left_header = _plain_cell(out.iat[row_idx, col_idx])
                right_header = _plain_cell(out.iat[row_idx, col_idx + 1])
                if not left_header and right_header and right_header != COLSPAN_MARKER:
                    header_rows.append(row_idx)

            if not header_rows:
                continue

            first_body_row = max(header_rows) + 1
            duplicate_rows = []
            comparable_rows = 0

            for row_idx in range(first_body_row, out.shape[0]):
                left = _plain_cell(out.iat[row_idx, col_idx], drop_leading_currency=True)
                right = _plain_cell(out.iat[row_idx, col_idx + 1], drop_leading_currency=True)
                if not left and not right:
                    continue
                if not left or not right:
                    continue
                comparable_rows += 1
                if left == right:
                    duplicate_rows.append(row_idx)

            if not duplicate_rows:
                continue
            if comparable_rows and len(duplicate_rows) / comparable_rows < 0.6:
                continue

            for row_idx in header_rows:
                out.iat[row_idx, col_idx] = out.iat[row_idx, col_idx + 1]
                out.iat[row_idx, col_idx + 1] = COLSPAN_MARKER

            for row_idx in duplicate_rows:
                out.iat[row_idx, col_idx + 1] = COLSPAN_MARKER

        return out

    def _normalize_sparse_currency_prefix_spans(frame: pd.DataFrame) -> pd.DataFrame:
        """
        Some filing-agent tables reserve a narrow prefix column for currency
        symbols and leave that prefix blank for non-currency rows in the same
        value group. Emit the whole value from the left edge of the group so
        MultiMarkdown renders a single value cell instead of a detached "$".
        """
        if frame.shape[0] < 2 or frame.shape[1] < 2:
            return frame

        out = frame.copy()
        currency_symbols = {'$', '£', '€', '¥', '￥', 'C$', 'A$', 'R$', 'COP'}

        for col_idx in range(1, out.shape[1] - 1):
            merge_rows = []
            saw_currency = False

            for row_idx in range(out.shape[0]):
                left_raw = out.iat[row_idx, col_idx]
                right_raw = out.iat[row_idx, col_idx + 1]
                left = _plain_cell(left_raw)
                right = _plain_cell(right_raw)

                if not right or right == _plain_cell(COLSPAN_MARKER):
                    continue
                if left and left not in currency_symbols:
                    continue
                if left in currency_symbols:
                    saw_currency = True
                merge_rows.append(row_idx)

            if not saw_currency or not merge_rows:
                continue

            for row_idx in merge_rows:
                left = _plain_cell(out.iat[row_idx, col_idx])
                right_raw = str(out.iat[row_idx, col_idx + 1]).strip()
                right_plain = _plain_cell(right_raw)
                if not right_plain:
                    continue

                if left in currency_symbols:
                    if right_plain.startswith(left):
                        merged = right_raw
                    else:
                        merged = f"{left}{right_raw}"
                else:
                    merged = right_raw

                out.iat[row_idx, col_idx] = merged.strip()
                out.iat[row_idx, col_idx + 1] = COLSPAN_MARKER

        return out

    proc = _normalize_sparse_currency_prefix_spans(proc)
    proc = _normalize_shifted_header_spans(proc)
    span_ids = pd.DataFrame(index=df.index, columns=df.columns, dtype=object)

    rowspan_pattern = re.compile(r'##ROWSPAN_(\w+)##')
    colspan_pattern = re.compile(r'##COLSPAN_(\w+)##')

    for r in range(proc.shape[0]):
        for c in range(proc.shape[1]):
            cell_text = proc.iat[r, c]
            row_match = rowspan_pattern.search(cell_text)
            col_match = colspan_pattern.search(cell_text)
            span_ids.iat[r, c] = {
                'row': row_match.group(1) if row_match else None,
                'col': col_match.group(1) if col_match else None,
            }
            proc.iat[r, c] = rowspan_pattern.sub('', colspan_pattern.sub('', cell_text)).strip()

    visited = set()
    for r_start in range(proc.shape[0]):
        for c_start in range(proc.shape[1]):
            if (r_start, c_start) in visited:
                continue
            
            start_ids = span_ids.iat[r_start, c_start]
            r_id = start_ids.get('row')
            c_id = start_ids.get('col')

            r_end, c_end = r_start, c_start
            if r_id:
                for r in range(r_start + 1, proc.shape[0]):
                    if span_ids.iat[r, c_start].get('row') == r_id:
                        r_end = r
                    else: break
            if c_id:
                for c in range(c_start + 1, proc.shape[1]):
                    if span_ids.iat[r_start, c].get('col') == c_id:
                        c_end = c
                    else: break
            
            longest_content = ""
            for r_iter in range(r_start, r_end + 1):
                for c_iter in range(c_start, c_end + 1):
                    current_content = str(proc.iat[r_iter, c_iter])
                    if len(current_content.strip()) > len(longest_content.strip()):
                        longest_content = current_content

            proc.iat[r_start, c_start] = longest_content

            for r_block in range(r_start, r_end + 1):
                for c_block in range(c_start, c_end + 1):
                    if (r_block, c_block) in visited:
                        continue
                    
                    visited.add((r_block, c_block))
                    
                    if (r_block, c_block) == (r_start, c_start):
                        continue

                    if r_block > r_start:
                        if c_block == c_start:
                            proc.iat[r_block, c_block] = ROWSPAN_MARKER
                        else:
                            proc.iat[r_block, c_block] = COLSPAN_MARKER
                    elif c_block > c_start:
                        proc.iat[r_block, c_block] = COLSPAN_MARKER

    if not proc.empty:
        def has_visible_cell(cell: object) -> bool:
            text = str(cell).strip()
            return text.lower() not in ('', 'nan', 'none', '—', ROWSPAN_MARKER, COLSPAN_MARKER)

        keep_mask = proc.apply(lambda row: any(has_visible_cell(cell) for cell in row), axis=1)
        proc = proc[keep_mask].reset_index(drop=True)

    def row_md(series: pd.Series) -> str:
        """Renders a pandas Series into a clean MultiMarkdown table row."""
        cells = series.fillna('').tolist()
        out = ["|"]
        i = 0
        while i < len(cells):
            cell_raw = cells[i]
            
            j = i
            while j + 1 < len(cells) and cells[j + 1] == COLSPAN_MARKER:
                j += 1
            
            col_span = j - i + 1
            cell_text = _normalize_markdown_emphasis(_sup_to_caret(cell_raw))

            out.append(f" {cell_text} ")
            out.append("|" * col_span)
            
            i = j + 1
        return "".join(out)

    header_cells = [_normalize_markdown_emphasis(_sup_to_caret(str(c))) for c in df.columns]
    header = "| " + " | ".join(header_cells) + " |"
    divider = "|" + "|".join(["---"] * len(df.columns)) + "|"
    body = [row_md(proc.iloc[r]) for r in range(len(proc))]

    return "\n".join([header, divider, *body])

def _parse_md_table_to_df(table_str: str) -> pd.DataFrame:
    """
    Parses a standard Markdown table string back into a DataFrame,
    correctly handling multi-line headers by skipping the separator line.
    """
    lines = table_str.strip().split('\n')
    
    rows_of_cells = []
    for line in lines:
        if not line.strip().startswith('|'):
            continue
        
        cells = [cell for cell in line.strip().strip('|').split('|')]
        
        if len(cells) > 0 and all(re.fullmatch(r'[:\-\s]*', c) for c in cells) and '-' in line:
            continue
            
        rows_of_cells.append(cells)

    if not rows_of_cells or len(rows_of_cells) < 1:
        return pd.DataFrame()

    header = rows_of_cells[0]
    num_cols = len(header)
    data = []
    
    for row in rows_of_cells[1:]:
        if len(row) < num_cols:
            row.extend([''] * (num_cols - len(row)))
        data.append(row[:num_cols])

    return pd.DataFrame(data, columns=header)

def convert_all_tables_to_mmd(markdown_content: str) -> str:
    """
    Finds all standard Markdown tables wrapped with '---' delimiters in the
    final output and converts them to MultiMarkdown format, preserving the
    delimiters.
    """
    table_pattern = re.compile(
        r'^\s*---\s*$'
        r'([\s\S]*?)'
        r'^\s*---\s*$',
        re.MULTILINE
    )

    def _uniquify(cols):
        seen = {}
        out = []
        for c in ["" if c is None else str(c) for c in cols]:
            n = seen.get(c, 0)
            out.append(f"{c}__{n}" if n else c)
            seen[c] = n + 1
        return out

    def replacer(match):
        md_table_str = match.group(1)
        df = _parse_md_table_to_df(md_table_str)

        if df.empty:
            return match.group(0)

        if any(str(c).strip() for c in df.columns):
            orig_cols = ["" if c is None else str(c) for c in df.columns]
            uniq_cols = _uniquify(orig_cols)

            df = df.copy()
            df.columns = uniq_cols
            header = pd.DataFrame([orig_cols], columns=uniq_cols)

            df = pd.concat([header, df], ignore_index=True, sort=False)

            df.columns = [''] * df.shape[1]
        
        mmd_table = df_to_multimarkdown(df)
        return f"\n---\n\n{mmd_table}\n\n---\n"
    return table_pattern.sub(replacer, markdown_content)

def reorder(df: pd.DataFrame, order) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [' '.join(map(str, tup)).strip() for tup in df.columns]

    cols_in_order = [c for c in order if c in df.columns]
    extras        = [c for c in df.columns if c not in cols_in_order]

    return df[cols_in_order + extras]

__all__ = [name for name in globals() if not name.startswith("__")]
