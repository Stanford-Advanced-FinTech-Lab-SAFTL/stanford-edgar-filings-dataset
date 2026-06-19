from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.parsers.ocr.ocr_utils import normalize_text_markup
from stanford_edgar_parser.utils.bootstrap import (
    BeautifulSoup,
    NavigableString,
    Tag,
    WINGDINGS_MAP,
    html,
    logging,
    np,
    pd,
    re,
    traceback,
    unicodedata,
)

def is_cell_truly_empty(cell_value):
    from stanford_edgar_parser.parsers.html.preprocessing import is_cell_truly_empty as _impl

    return _impl(cell_value)


def is_numeric_like(s: str) -> bool:
    from stanford_edgar_parser.parsers.html.preprocessing import is_numeric_like as _impl

    return _impl(s)


def to_compact_markdown(df: pd.DataFrame, **kwargs) -> str:
    from stanford_edgar_parser.parsers.html.preprocessing import to_compact_markdown as _impl

    return _impl(df, **kwargs)


_PAREN_NUM_RE = re.compile(r'\((\$?)([\d][\d,]*)\)')

def _strip_commas_in_paren(val):
    if isinstance(val, str):
        return _PAREN_NUM_RE.sub(
            lambda m: f"({m.group(1)}{m.group(2).replace(',', '')})",
            val,
        )
    return val

def _collapse_newlines(s: str) -> str:
    """Turn hard new-lines inside a cell into a visible <br> (or a space)."""
    return re.sub(r'\s*\n\s*', '<br>', s)

def read_html(path) -> str:
    """Read an HTML file and return normalized Unicode text."""
    raw = path.read_bytes()
    return normalize_text_markup(raw)

def is_centered(element) -> bool:
    """
    Checks if a BeautifulSoup element is centered by checking its own attributes
    or those of its parent tags. This iterative version prevents recursion errors.
    """
    while element and hasattr(element, 'name'):
        if element.name == 'center':
            return True
        
        if element.get('align', '').lower() == 'center':
            return True
        
        style = element.get('style', '').lower()
        if 'text-align:center' in style.replace(' ', ''):
            return True
            
        element = element.parent
        
    return False

_JS_CSS_RE = re.compile(r'''
    ^\s*(?:var|function)\b
  | ^\s*[/]{2}
  | ^\s*/\*
  | ^\s*[.#][\w-]+\s*[{]
''', re.I | re.X)

def is_junk_text(text: str) -> bool:
    """Checks if a line of text is likely unwanted XBRL metadata."""
    if _JS_CSS_RE.match(text): return True
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text): return True
    if re.fullmatch(r'\d{6,10}', text): return True
    if text == 'us-': return True
    if re.fullmatch(r'([a-zA-Z0-9\-]+:[a-zA-Z0-9\.]+\s*)+', text): return True
    return False

_UNCLOSED_CELL_RE = re.compile(
    r'^\s*\$?\([\d,]+(?:\.\d+)?\s*$'
)

def _close_unclosed_paren(val):
    """Add a missing ')' only when the *whole* cell is an unclosed value."""
    if isinstance(val, str) and _UNCLOSED_CELL_RE.match(val):
        return val.rstrip() + ')'
    return val

def drop_pctless_dupes(df: pd.DataFrame) -> pd.DataFrame:
    """
    If two adjacent columns carry the *same* numeric info and the only
    difference is that one shows a “%”, keep the % column and drop the
    other.  Works even when negatives are shown as “(4)” / “(4)%”.
    """
    to_drop_indices = set()

    def _norm(col: pd.Series) -> pd.Series:
        return (col.fillna('')
                   .astype(str)
                   .str.replace(r'[()\s%]', '', regex=True)
                   .str.replace('—', ''))

    for i in range(len(df.columns) - 1):
        if i in to_drop_indices or (i + 1) in to_drop_indices:
            continue
        col1 = df.iloc[:, i]
        col2 = df.iloc[:, i + 1]

        if _norm(col1).equals(_norm(col2)):
            pct1 = col1.astype(str).str.contains('%').sum()
            pct2 = col2.astype(str).str.contains('%').sum()
            to_drop_indices.add(i if pct1 < pct2 else (i + 1))

    return df.iloc[:, [idx for idx in range(df.shape[1]) if idx not in to_drop_indices]]

DOLLAR_RE = re.compile(r'^\(\s*\$|\$\s*')

def drop_dollarless_dupes(df: pd.DataFrame) -> pd.DataFrame:
    """
    If two *adjacent* columns carry the same numeric information and the only
    difference is that one shows a leading “$”, keep the $-column and drop
    the other.  Handles negatives shown as “($4)” / “(4)”.
    """
    to_drop_indices = set()

    def _norm(col: pd.Series) -> pd.Series:
        return (col.fillna('')
                   .astype(str)
                   .str.replace(r'[()\s$,]', '', regex=True)
                   .str.replace('—', ''))

    for i in range(len(df.columns) - 1):
        if i in to_drop_indices or (i + 1) in to_drop_indices:
            continue
        col1 = df.iloc[:, i]
        col2 = df.iloc[:, i + 1]

        if _norm(col1).equals(_norm(col2)):
            d1 = col1.astype(str).str.match(DOLLAR_RE).sum()
            d2 = col2.astype(str).str.match(DOLLAR_RE).sum()
            to_drop_indices.add(i if d1 < d2 else (i + 1))

    return df.iloc[:, [idx for idx in range(df.shape[1]) if idx not in to_drop_indices]]

def _shift_colx_into_named(df: pd.DataFrame) -> pd.DataFrame:
    colx = [c for c in df.columns if str(c).startswith("Col ")]
    real = [c for c in df.columns if c not in colx and c != "Label"]

    if not colx:
        return df

    if not real:
        df.rename(columns={c: "" for c in colx}, inplace=True)
        return df

    for idx in df.index:
        stash = []
        for c in colx:
            val = df.at[idx, c]
            if pd.notna(val) and str(val).strip():
                stash.append(val)
                df.at[idx, c] = ""

        if not stash:
            continue

        for c in real:
            if not stash:
                break
            if pd.isna(df.at[idx, c]) or str(df.at[idx, c]).strip() == "":
                df.at[idx, c] = stash.pop(0)

    to_drop = []
    for c in colx:
        if df[c].replace("", np.nan).isna().all():
            to_drop.append(c)

    df = df.drop(columns=to_drop)

    df.rename(columns={c: "" for c in colx if c not in to_drop}, inplace=True)

    return df

_BLANK_RE = re.compile(r'^\s*$|^\s*[—–-]+\s*$')

def _is_blank(val) -> bool:
    """
    True if *val* is NaN, empty, whitespace or just dashes (even with
    stray spaces/NBSPs wrapped around).
    """
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return True
    return bool(_BLANK_RE.fullmatch(str(val)))

def drop_header_only_cols(df: pd.DataFrame) -> pd.DataFrame:
    """
    Remove any column whose *body* (rows 1…end) is completely blank.
    Works even with duplicate column names because we slice by position.
    """
    if df.shape[1] < 2 or df.shape[0] < 2:
        return df

    body = df.iloc[2:]
    keep = ~body.applymap(_is_blank).all(axis=0).to_numpy()
    return df.iloc[:, keep]

def _drop_header_and_empty_cols(df: pd.DataFrame,
                                header_rows: int = 3,
                                min_blank_rows: int = 10) -> pd.DataFrame:

    if df.shape[0] <= header_rows + min_blank_rows:
        return df
    
    if len(df.iloc[:header_rows + 1].replace('\u00A0', '', regex=False).replace(r'^[\s\u00A0]*[—–-]?[\s\u00A0]*$', np.nan, regex=True).to_string()) > 80:
        return df

    body = (
        df.iloc[header_rows:]
          .replace('\u00A0', '', regex=False)
          .replace(r'^[\s\u00A0]*[—–-]?[\s\u00A0]*$', np.nan, regex=True)
    )
    
    blank_counts = body.isna().sum(axis=0)

    drop_mask = (blank_counts == len(body)) & (blank_counts >= min_blank_rows)

    return df.loc[:, ~drop_mask]

def _late_drop_blank_header_subset_cols(df: pd.DataFrame,
                                        header_rows: int = 2,
                                        min_blank_rows: int = 3) -> pd.DataFrame:
    """
    Late cleanup for empty spacer columns whose header is just a split-out
    subset of an adjacent populated header column.

    This targets tables where symbol/header merges leave behind an empty body
    column carrying only partial header text, while preserving intentionally
    blank columns that have distinct headers.
    """
    if df.shape[1] < 3 or df.shape[0] <= header_rows + min_blank_rows:
        return df

    body = (
        df.iloc[header_rows:]
          .replace('\u00A0', '', regex=False)
          .replace(r'^[\s\u00A0]*[—–-]?[\s\u00A0]*$', np.nan, regex=True)
    )
    nonblank_counts = body.notna().sum(axis=0).to_numpy()

    header = df.iloc[:header_rows].copy()

    def _norm_header_cell(val) -> str:
        if not isinstance(val, str):
            return '' if val is None else str(val)
        text = normalize_for_symbol_check(val)
        text = text.replace('<br>', ' ').replace('##NEWLINE##', ' ')
        return re.sub(r'\s+', ' ', text).strip()

    norm_header = header.applymap(_norm_header_cell)
    keep_mask = np.ones(df.shape[1], dtype=bool)

    for i in range(1, df.shape[1] - 1):
        if nonblank_counts[i] != 0:
            continue
        if nonblank_counts[i - 1] == 0 and nonblank_counts[i + 1] == 0:
            continue

        sub_parts = [str(norm_header.iat[r, i]).strip() for r in range(header_rows)]
        if not any(sub_parts):
            continue

        for neighbor_idx in (i - 1, i + 1):
            if nonblank_counts[neighbor_idx] == 0:
                continue

            super_parts = [str(norm_header.iat[r, neighbor_idx]).strip() for r in range(header_rows)]
            matches = True
            compared = False

            for sub_part, super_part in zip(sub_parts, super_parts):
                if not sub_part:
                    continue
                compared = True
                if not super_part or sub_part not in super_part:
                    matches = False
                    break

            if matches and compared:
                keep_mask[i] = False
                break

    return df.loc[:, keep_mask]

_BLANK_RE = re.compile(r'^\s*$|^\s*[—–-]+\s*$')
def _is_blank(val) -> bool:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return True
    return bool(_BLANK_RE.fullmatch(str(val)))

def drop_adjacent_head_dupes(df: pd.DataFrame,
                             n_head: int = 3) -> pd.DataFrame:
    """
    Drops adjacent columns with duplicate headers, using a refined heuristic
    to preserve columns with significant and mostly distinct data.
    """
    if df.shape[1] < 2:
        return df

    FILL_THRESHOLD = 0.25
    MAX_OVERLAP_PCT = 0.25

    start_col = 0
    if df.shape[0] > n_head:
        first_col_body = df.iloc[n_head:, 0].dropna().astype(str)
        if not first_col_body.empty:
            cells_with_letters = first_col_body.str.contains(r'[a-zA-Z]').sum()
            if (cells_with_letters / len(first_col_body)) > 0.75:
                start_col = 1

    keep_mask = np.ones(df.shape[1], dtype=bool)

    head = (df.iloc[:n_head, :]
              .fillna('')
              .astype(str)
              .apply(lambda col: col.str.strip()))

    body = df.iloc[n_head:, :]
    body_len = len(body)

    for i in range(start_col, df.shape[1] - 1):
        if not keep_mask[i]:
            continue

        header_col_i = head.iloc[:, i]
        header_col_j = head.iloc[:, i + 1]

        if header_col_i.equals(header_col_j) and not header_col_i.apply(_is_blank).all():

            body_col_i = body.iloc[:, i]
            body_col_j = body.iloc[:, i + 1]
            
            if body_col_i.fillna('').equals(body_col_j.fillna('')):
                keep_mask[i + 1] = False
                continue

            if body_len > 0:
                nb_i = (~body_col_i.apply(_is_blank)).sum()
                nb_j = (~body_col_j.apply(_is_blank)).sum()
                
                vals_i = set(body_col_i.dropna().astype(str).str.strip())
                vals_j = set(body_col_j.dropna().astype(str).str.strip())
                vals_i.discard('')
                vals_j.discard('')

                shared_values_count = len(vals_i & vals_j)
                denom = max(1, min(nb_i, nb_j))
                overlap_ratio = shared_values_count / denom
                are_sufficiently_distinct = overlap_ratio < MAX_OVERLAP_PCT

                fill_pct_i = nb_i / body_len
                fill_pct_j = nb_j / body_len

                vals_i = set(body_col_i.dropna().astype(str).str.strip()); vals_i.discard('')
                vals_j = set(body_col_j.dropna().astype(str).str.strip()); vals_j.discard('')

                shared = len(vals_i & vals_j)
                overlap_i = shared / max(1, len(vals_i))
                overlap_j = shared / max(1, len(vals_j))

                if nb_i <= nb_j and overlap_i >= 0.90:
                    keep_mask[i] = False
                elif nb_j < nb_i and overlap_j >= 0.90:
                    keep_mask[i + 1] = False

    return df.loc[:, keep_mask]

def drop_visually_redundant_blank_cols(df: pd.DataFrame, header_rows: int = 2) -> pd.DataFrame:
    if df.shape[1] < 2 or df.shape[0] < header_rows:
        return df

    header = df.iloc[:header_rows]
    body = df.iloc[header_rows:]

    if body.empty:
        return df

    col_groups = {}
    for i in range(df.shape[1]):
        key = tuple(header.iloc[:, i].fillna('').astype(str).tolist())
        if key not in col_groups:
            col_groups[key] = []
        col_groups[key].append(i)

    indices_to_drop = []
    for header_key, indices in col_groups.items():
        if len(indices) < 2:
            continue

        header_has_visible_content = False
        for cell in header_key:
            cleaned_header = re.sub(
                r'##(?:ROWSPAN|COLSPAN)_\w+##|##NEWLINE##|##INDENT##|<BORDER(?:_TOP)?>|&nbsp;|<br\s*/?>',
                '',
                str(cell),
                flags=re.I,
            )
            cleaned_header = re.sub(r'[\u00A0\u200B-\u200D\u2060\u2063\uFEFF]+', '', cleaned_header)
            if cleaned_header.strip():
                header_has_visible_content = True
                break

        if not header_has_visible_content:
            continue

        blank_in_group = []
        non_blank_in_group = []
        
        for idx in indices:
            if body.iloc[:, idx].apply(_is_blank).all():
                blank_in_group.append(idx)
            else:
                non_blank_in_group.append(idx)

        if non_blank_in_group:
            indices_to_drop.extend(blank_in_group)
        elif blank_in_group:
            indices_to_drop.extend(blank_in_group[1:])

    if not indices_to_drop:
        return df

    return df.drop(df.columns[sorted(list(set(indices_to_drop)))], axis=1)

_LAYOUT_SCAFFOLD_RE = re.compile(
    r'##(?:ROWSPAN|COLSPAN)_\w+##|##NEWLINE##|##INDENT##|<BORDER(?:_TOP)?>|&nbsp;|'
    r'##(?:BOLD|ITALIC|U)_(?:START|END)_\d+##|##LINK_START_\d+__[^#]+##|##LINK_END_\d+##',
    re.I,
)

def _blank_layout_only_cell(cell):
    """
    Treat parser-introduced layout scaffolding as empty, while preserving
    real visible content such as superscripts, currency symbols, and text.
    """
    if not isinstance(cell, str):
        return cell

    cleaned = _LAYOUT_SCAFFOLD_RE.sub('', cell)
    cleaned = re.sub(r'(?i)\b(?:nan|none)\b', '', cleaned)
    cleaned = re.sub(r'[\u00A0\u200B-\u200D\u2060-\u206F\uFEFF]+', '', cleaned)
    cleaned = re.sub(r'\s+', '', cleaned)

    if cleaned == '':
        return ''

    return cell

_YEAR_TAIL_RE = re.compile(r"\s*years$", flags=re.IGNORECASE)

def _norm(s):
    s = str(s).strip()
    s = _YEAR_TAIL_RE.sub("", s)
    return s.strip()

def is_direct_subset(series_subset: pd.Series, series_superset: pd.Series) -> bool:
    non_blank_subset = ~series_subset.apply(_is_blank)
    non_blank_superset = ~series_superset.apply(_is_blank)

    if non_blank_subset.sum() >= non_blank_superset.sum():
        return False

    subset_values = series_subset[non_blank_subset].apply(_norm)
    corresponding_superset_values = series_superset[non_blank_subset].apply(_norm)

    are_equal = (subset_values == corresponding_superset_values).all()
    return are_equal


def drop_subset_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Iterates through a DataFrame and drops a column if it is a "direct subset"
    of an adjacent column.

    This is useful for removing spacer columns or redundant columns that only
    contain a subset of information already present in their neighbor.

    Args:
        df: The pandas DataFrame to clean.

    Returns:
        A new DataFrame with subset columns removed.
    """
    if df.shape[1] < 2:
        return df
    
    indices_to_drop = set()
    
    for i in range(df.shape[1] - 1):
        idx_left, idx_right = i, i + 1

        if idx_left in indices_to_drop or idx_right in indices_to_drop:
            continue

        col_left = df.iloc[:, idx_left]
        col_right = df.iloc[:, idx_right]

        if is_direct_subset(col_right, col_left):
            indices_to_drop.add(idx_right)
        elif is_direct_subset(col_left, col_right):
            indices_to_drop.add(idx_left)

    if not indices_to_drop:
        return df

    sorted_indices_to_drop = sorted(list(indices_to_drop), reverse=True)
    df_cleaned = df.drop(df.columns[sorted_indices_to_drop], axis=1)
    
    return df_cleaned

SUP_HTML = re.compile(r'<sup\b[^>]*>.*?</sup>', flags=re.I)
HTML_TAGS = re.compile(r'<[^>]+>')
ZERO_WIDTH = re.compile(r'[\u200B-\u200F\u202A-\u202E\u2060-\u206F\ufeff]')
THOUSANDS_SEP = re.compile(r'(?<=\d)[,·\u00A0\u202F](?=\d)')

def _clean_numeric_cell_value(val):
    if not isinstance(val, str):
        return val
    if val == "`":
        return ""

    core = re.sub(
        r'##(SUP|/SUP|SUB|/SUB|BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##',
        '', val
    ).replace('##NEWLINE##','').replace('##INDENT##','').replace('<BORDER>','').replace('&nbsp;','')

    core = re.sub(r'<sup\b[^>]*>.*?</sup>', '', core, flags=re.I)
    core = re.sub(r'<[^>]+>', '', core)
    core = ZERO_WIDTH.sub('', core)
    core = unicodedata.normalize('NFKC', core)

    core_stripped = core.strip()
    if not core_stripped:
        return val

    probe = re.sub(r'[,$()\s–—-]', '', core_stripped)
    is_numeric = probe.replace('.', '', 1).isdigit()

    if is_numeric:
        val_no_zw = ZERO_WIDTH.sub('', val)
        return THOUSANDS_SEP.sub('', val_no_zw)

    return val

def drop_exact_dup_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Drops columns whose name and contents exactly match a previous column."""
    seen = set()
    keep_idxs = []
    for idx, col in enumerate(df.columns):
        col_values = tuple(df.iloc[:, idx].tolist())
        key = (col, col_values)
        if key not in seen:
            seen.add(key)
            keep_idxs.append(idx)
    return df.iloc[:, keep_idxs]

def get_first_data_row_index(df):

    first_data_row_index = 0
    if not df.empty:
        first_col = df.iloc[:, 0]
        num_rows = len(first_col)

        first_non_empty_idx = num_rows
        for idx, val in first_col.items():
            if not is_cell_truly_empty(val):
                first_non_empty_idx = idx
                break

        first_italic_idx = len(df)
        for idx, row in df.iterrows():
            if any('##ITALIC_START_' in str(cell) for cell in row):
                first_italic_idx = idx
                break

        first_col_label_idx = min(first_non_empty_idx, first_italic_idx)

        if first_col_label_idx == num_rows:
            first_col_label_idx = 0

        header_end_idx = 0
        for idx, row in df.iterrows():
            
            is_header_row = row.apply(lambda x:
                is_cell_truly_empty(x) or
                '##BOLD_START' in str(x) and
                not is_numeric_like(str(x))
            ).all()

            if is_header_row:
                continue
            else:
                header_end_idx = idx
                break

        first_col_label_idx = 0 if len(df) <= first_col_label_idx else first_col_label_idx
        header_end_idx = 0 if len(df) <= header_end_idx else header_end_idx
        first_data_row_index = max(first_col_label_idx, header_end_idx)

        if first_data_row_index < len(df):
            row_to_check = df.iloc[first_data_row_index]

            all_cells_have_border = all("<BORDER>" in str(cell) for cell in row_to_check)

            all_other_cells_are_bold = False
            if df.shape[1] > 1:
                cells_except_first = row_to_check.iloc[1:]
                all_other_cells_are_bold = all("##BOLD_START" in str(cell) for cell in cells_except_first)
            
            next_index_is_valid = (first_data_row_index + 1) < len(df)

            if all_cells_have_border and all_other_cells_are_bold and next_index_is_valid:
                first_data_row_index += 1
        
        if first_data_row_index < len(df):
            row_to_check = df.iloc[first_data_row_index]

            if '##(in millions' in row_to_check.to_string() or "(In millions of Canadian dollars)" in row_to_check.to_string():
                
                if (first_data_row_index + 1) < len(df):
                    first_data_row_index += 1
                else:
                    first_data_row_index = 0
        
    return first_data_row_index

def should_flag_as_token_only(column_series: pd.Series) -> bool:
    """
    Determines if a column should be flagged as "token-only" for potential removal.
    This version is modified to NOT flag columns that only contain '%', as they
    are needed for merging.
    """
    non_empty_values = column_series.dropna()
    if non_empty_values.empty:
        return False

    ONLY_TOKS = {'(', ')', ')%', ')bp', ')##DOUBLE_ASTERISK##', 'months', 'years'}

    is_suspicious = non_empty_values.isin(ONLY_TOKS).any()

    if not is_suspicious:
        return False

    if non_empty_values.eq('%').any():
        numeric_percent_pattern = re.compile(r'^\s*[\d\.]+\s*%\s*$')
        has_numeric_percents = column_series.str.match(numeric_percent_pattern, na=False).any()

        if has_numeric_percents:
            return False

    return True

def _is_column_fully_ignorable(
    df: pd.DataFrame,
    col_to_check_idx: int,
    partner_col_idx: int,
    allow_dollar_sign: bool
) -> bool:
    """
    Helper function to determine if an entire column is redundant.
    This version uses a local "blank check" that does NOT treat dashes as blank.
    """
    _TRULY_BLANK_RE = re.compile(r'^\s*$')
    def _is_truly_blank_for_colspan(val) -> bool:
        """A local version of _is_blank that considers dashes as content."""
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return True
        return bool(_TRULY_BLANK_RE.fullmatch(str(val)))

    tag_re = re.compile(
        r'##(?:COLSPAN|ROWSPAN)_\d+##|##NEWLINE##|##INDENT##|<BORDER(?:_TOP)?>|&nbsp;|<br\s*/?>|##(?:ITALIC|BOLD|U)_(?:START|END)_\d+##|\b(?:nan)\b',
        re.I
    )
    colspan_re = re.compile(r'##COLSPAN_(\w+)##')

    _ZW_RE = re.compile(r'[\u200B-\u200D\u2060-\u206F\uFEFF]')
    def _normalize(s: str) -> str:
        s = '' if s is None else str(s)
        s = _ZW_RE.sub('', s)
        s = tag_re.sub('', s)
        return s.strip()

    for row_idx in range(len(df)):
        cell_to_check = str(df.iat[row_idx, col_to_check_idx])
        partner_cell = str(df.iat[row_idx, partner_col_idx])
        cleaned_cell = _normalize(cell_to_check)

        is_content_ignorable = _is_truly_blank_for_colspan(cleaned_cell) or \
                               (allow_dollar_sign and cleaned_cell == '$')

        partner_colspan_match = colspan_re.search(partner_cell)
        is_active_colspan_target = (
            partner_colspan_match and
            partner_colspan_match.group(0) in cell_to_check
        )

        if is_active_colspan_target:
            if not is_content_ignorable:
                cleaned_partner_cell = _normalize(partner_cell)
                if cleaned_cell != cleaned_partner_cell:
                    return False
            continue

        if not is_content_ignorable:
            return False

    return True

def drop_active_colspan_empty_cols(df: pd.DataFrame, allow_dollar_sign: bool = False) -> pd.DataFrame:
    """
    Finds and removes columns that are entirely redundant using a robust positional mask.
    A column is redundant if its cells are either targets of an active colspan
    from an adjacent column OR its content is blank (or optionally, just '$').
    This version correctly checks both left and right columns in a pair and is
    immune to non-standard column labels.
    """
    if df.shape[1] < 2:
        return df

    keep_mask = np.ones(df.shape[1], dtype=bool)
    colspan_re = re.compile(r'##COLSPAN_(\w+)##')

    for i in range(df.shape[1] - 1):
        left_col_idx = i
        right_col_idx = i + 1

        is_pair_linked = False
        for row_idx in range(len(df)):
            left_cell = str(df.iat[row_idx, left_col_idx])
            right_cell = str(df.iat[row_idx, right_col_idx])
            
            left_match = colspan_re.search(left_cell)
            if left_match and left_match.group(0) in right_cell:
                is_pair_linked = True
                break
            
            right_match = colspan_re.search(right_cell)
            if right_match and right_match.group(0) in left_cell:
                is_pair_linked = True
                break
        
        if not is_pair_linked:
            continue

        if keep_mask[right_col_idx] and _is_column_fully_ignorable(df, right_col_idx, left_col_idx, allow_dollar_sign):
            keep_mask[right_col_idx] = False
        
    return df.loc[:, keep_mask]

def normalize_for_symbol_check(val, remove_sups=True):
        """
        A helper to strip all placeholders and special tags for symbol checks.
        Includes a parameter to conditionally preserve superscript tags.
        """
        if not isinstance(val, str):
            return str(val)

        if remove_sups:
            placeholder_pattern = r'##(SUP|/SUP|SUB|/SUB|BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##'
        else:
            placeholder_pattern = r'##(?!SUP|/SUP|SUB|/SUB)(?:BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##'

        text = re.sub(placeholder_pattern, '', val)

        text = text.replace('##NEWLINE##', '').replace('<BORDER>', '').replace("##INDENT##", "").replace("&nbsp;", "")

        if remove_sups:
            text = text.replace("<sup>", "").replace("</sup>", "")

        return text.strip()

def is_sup_only_column(column_series: pd.Series) -> bool:
    sup_only_pattern = re.compile(r'^(?:\s*(?:<sup\b[^>]*>.*?</sup>|##SUP##.*?##/SUP##)\s*)+$')

    for cell_value in column_series.dropna():
        normalized_val = normalize_for_symbol_check(cell_value, remove_sups=False)

        if normalized_val and not sup_only_pattern.fullmatch(normalized_val):
            return False

    return any(normalize_for_symbol_check(v, remove_sups=False) for v in column_series.dropna())

def clean_financial_df(df_to_clean: pd.DataFrame) -> pd.DataFrame:
    df = df_to_clean.copy()
    preserved_header_rows = None

    keep = [True] + [not df.iloc[:, i].equals(df.iloc[:, i-1]) 
                 for i in range(1, df.shape[1])]
    df = df.loc[:, keep]

    df = drop_active_colspan_empty_cols(df)

    df = df.replace(r'<sup></sup>', '', regex=True)

    df = df.applymap(_clean_numeric_cell_value)

    df.dropna(how='all', inplace=True)
    df.reset_index(drop=True, inplace=True)

    if isinstance(df.columns, pd.MultiIndex):
        def _clean_multiindex_header_part(raw_part) -> str:
            clean = str(raw_part).strip()
            if not clean or re.fullmatch(r'Unnamed:\s*\d+(?:_level_\d+)?', clean):
                return ""
            clean = re.sub(r'##(?:ROWSPAN|COLSPAN)_\d+##', '', clean)
            clean = re.sub(r'\s*##NEWLINE##\s*', '##NEWLINE## ', clean)
            clean = re.sub(r'\s+', ' ', clean).strip()
            clean = clean.replace(' ##NEWLINE##', '##NEWLINE##').replace('##NEWLINE## ', '##NEWLINE## ')
            return clean.strip()

        def _extract_preserved_header_rows(columns: pd.MultiIndex) -> List[List[str]]:
            rows: List[List[str]] = []
            for level in range(columns.nlevels):
                header_row: List[str] = []
                for col_idx, col_tuple in enumerate(columns):
                    raw_text = str(col_tuple[level]).strip()
                    clean_text = _clean_multiindex_header_part(raw_text)
                    colspan_match = re.search(r'##COLSPAN_(\d+)##', raw_text)
                    has_colspan_marker = bool(colspan_match)
                    colspan_marker_id = colspan_match.group(1) if colspan_match else None
                    has_rowspan_marker = bool(re.search(r'##ROWSPAN_\d+##', raw_text))

                    if level > 0:
                        prev_raw_text = str(columns[col_idx][level - 1]).strip()
                        prev_clean_text = _clean_multiindex_header_part(prev_raw_text)
                        prev_has_rowspan = bool(re.search(r'##ROWSPAN_\d+##', prev_raw_text))
                    else:
                        prev_clean_text = ""
                        prev_has_rowspan = False

                    if (
                        level > 0
                        and clean_text
                        and clean_text == prev_clean_text
                        and (has_rowspan_marker or prev_has_rowspan)
                    ):
                        header_row.append("^^")
                    elif (
                        col_idx > 0
                        and clean_text
                        and has_colspan_marker
                        and clean_text == header_row[-1]
                    ):
                        prev_same_level_raw_text = str(columns[col_idx - 1][level]).strip()
                        prev_colspan_match = re.search(r'##COLSPAN_(\d+)##', prev_same_level_raw_text)
                        prev_colspan_marker_id = prev_colspan_match.group(1) if prev_colspan_match else None
                        if prev_colspan_marker_id == colspan_marker_id:
                            header_row.append("##__COLSPAN__##")
                        else:
                            header_row.append(clean_text)
                    elif not clean_text and has_colspan_marker:
                        header_row.append("##__COLSPAN__##")
                    else:
                        header_row.append(clean_text)
                rows.append(header_row)

            while rows and all(str(cell).strip() in {"", "##__COLSPAN__##"} for cell in rows[-1]):
                rows.pop()

            return rows

        def _flatten_multiindex_header(col_tuple) -> str:
            parts = []
            last_clean = None
            for raw_part in col_tuple:
                clean = _clean_multiindex_header_part(raw_part)
                if not clean:
                    continue
                if clean == last_clean:
                    continue
                parts.append(clean)
                last_clean = clean
            return ' '.join(parts).strip()

        if any('<BORDER>' in ' '.join(col) for col in list(df.columns)):
            hdr = df.columns.to_frame(index=False).T
            hdr.columns = range(df.shape[1])
            body = df.copy()
            body.columns = hdr.columns
            df = pd.concat([hdr, body], ignore_index=True)
        else:
            preserved_header_rows = _extract_preserved_header_rows(df.columns)
            df.columns = [_flatten_multiindex_header(col) for col in df.columns]

    if preserved_header_rows:
        df.attrs['preserved_header_rows'] = preserved_header_rows

    df = df.replace(to_replace=r'^Unnamed.*$', value='', regex=True)

    sup_re = re.compile(r'</?sup\b[^>]*>', re.I)

    def normalize_for_comparison(x):
        """
        Removes sup tags, invisible characters, replaces non-breaking spaces and <br> tags,
        strips whitespace, normalizes ampersand spacing, and treats dash-only 
        cells as empty for comparison.
        """
        if isinstance(x, str):
            x = re.sub(r'<br\s*/?>', ' ', x, flags=re.IGNORECASE)

            x = re.sub(r'[\u2060-\u206F]', '', x) 
            x = sup_re.sub('', x)
            x = x.replace('\u00A0', ' ')
            x = re.sub(r'\s*&\s*', ' & ', x)
            x = x.replace('##NEWLINE##', '')
            x = x.replace('<BORDER>', '')
            x = re.sub(r'##(SUP|/SUP|SUB|/SUB|BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##', '', x)
            x = x.replace("##INDENT##", "")

            x = re.sub(r'\s+', ' ', x)
            
            stripped_x = x.strip()

            if stripped_x in ('—', '-', '–'):
                return ''
            
            return stripped_x
        return x

    df_for_comparison = df.copy().applymap(normalize_for_comparison)
    
    df_for_comparison.columns = [normalize_for_comparison(c) for c in df_for_comparison.columns]

    def apply_cleaning_and_sync(original_df, comparison_df, cleaning_func, *args, **kwargs):
        comp_df_copy = comparison_df.copy()
        
        prefixed_cols = [f"{i}___{col}" for i, col in enumerate(comp_df_copy.columns)]
        comp_df_copy.columns = prefixed_cols
        cleaned_prefixed_df = cleaning_func(comp_df_copy, *args, **kwargs)
        
        kept_indices = [int(col.split('___')[0]) for col in cleaned_prefixed_df.columns]
        
        new_original_df = original_df.iloc[:, kept_indices]
        new_comparison_df = comparison_df.iloc[:, kept_indices]

        return new_original_df, new_comparison_df
    

    CLEAN_TAG  = re.compile(r'\s*(?:##NEWLINE##|<br\s*/?>)\s*', flags=re.I)
    ONLY_SYM   = re.compile(r'^\s*([\$%\(\)]|%\)|\)%|\)$)\s*(?:##NEWLINE##|<br\s*/?>)?\s*$', flags=re.I)

    def _strip_tag_only_symbols(val):
        if isinstance(val, str) and ONLY_SYM.fullmatch(val):
            return CLEAN_TAG.sub('', val).strip()
        return val

    df = df.applymap(_strip_tag_only_symbols)
    df_for_comparison = df_for_comparison.applymap(_strip_tag_only_symbols)

    dash_vals = ['–', '-', '—']
    is_dash = lambda s: s.astype(str).str.strip().isin(dash_vals)

    header_rows = 1
    if df.shape[0] > header_rows:
        i = 0
        current_columns = df.columns.tolist()
        while i < len(current_columns) - 2:
            body_df = df.iloc[header_rows:]
            col1 = df.iloc[:, i]
            col2 = df.iloc[:, i + 1]
            col3 = df.iloc[:, i + 2]
            body_col1 = body_df.iloc[:, i]
            body_col2 = body_df.iloc[:, i + 1]
            body_col3 = body_df.iloc[:, i + 2]

            c2_all_dashes = (not body_col2.dropna().empty) and is_dash(body_col2).all()
            has_adjacent_dashes = (
                (is_dash(body_col1) & is_dash(body_col2))
                | (is_dash(body_col2) & is_dash(body_col3))
            ).any()

            if c2_all_dashes and not has_adjacent_dashes:
                rows_to_merge = is_dash(col2) & ~is_dash(col1) & ~is_dash(col3)
                merged_values = (
                    col1.fillna('').astype(str) +
                    ' – ' +
                    col3.fillna('').astype(str)
                )
                row_mask = rows_to_merge.to_numpy()
                df.iloc[row_mask, i] = merged_values.loc[rows_to_merge].to_numpy()

                keep_mask = np.ones(df.shape[1], dtype=bool)
                keep_mask[[i + 1, i + 2]] = False
                df = df.loc[:, keep_mask]
                df_for_comparison = df_for_comparison.loc[:, keep_mask]
                current_columns = df.columns.tolist()
                continue

            i += 1

    def _clean_header_cell(cell):
        if pd.isna(cell): return cell
        cleaned_cell = str(cell).strip()
        return re.sub(r'^\s*(\d{4})\.0?\s*$', r'\1', cleaned_cell)

    if not df.empty and df.shape[0] > 0:
        df = df.astype(object)
        df.iloc[0] = df.iloc[0].apply(_clean_header_cell)
        
    if not df_for_comparison.empty and df_for_comparison.shape[0] > 0:
        df_for_comparison = df_for_comparison.astype(object)
        df_for_comparison.iloc[0] = df_for_comparison.iloc[0].apply(_clean_header_cell)

    mask = (df
        .replace(r'(##COLSPAN_\d+##|<BORDER>)', '', regex=True)
        .replace(r'[\s\u200b\u200c\u200d\u2060-\u2064\ufeff]+', '',
                 regex=True)
        .fillna('')
        .eq('')
        .all(axis=1))

    df = df[~mask].copy().reset_index(drop=True)
    df_for_comparison = df_for_comparison[~mask].reset_index(drop=True)

    def find_dest_col(df, row_idx, start_col_idx):
        """Return first non-blank column index scanning leftwards from start_col_idx."""
        col = start_col_idx
        while col >= 0:
            val = df.iat[row_idx, col]
            if not (pd.isna(val) or str(val).strip() == ''):
                return col
            col -= 1
        return start_col_idx
    

    df_to_string = df_for_comparison.to_string(index=False, header=False)

    first_data_row_index = get_first_data_row_index(df)
    first_data_row_index = first_data_row_index if first_data_row_index > 0 else 1

    if "Intended Award Value:" not in df_to_string and "UnderwritingDiscountsandCommissions" not in df_to_string or bool(re.search(r'\d', df_to_string)):
        right_merge_prefix_joiners = {
            '$': '',
            '£': '',
            '¥': '',
            '￥': '',
            '�': '',
            '(peso)': ' ',
        }
        current_cols = df.columns.tolist()
        i = 0
        while i < len(current_cols) - 1:
            col, nxt = current_cols[i], current_cols[i + 1]
            normalized_col = df[col].apply(normalize_for_symbol_check)
            normalized_col_lower = normalized_col.astype(str).str.lower()
            
            if normalized_col_lower.isin(right_merge_prefix_joiners).any():

                is_symbol_only_column_body = False
                
                col_body = normalized_col_lower.iloc[first_data_row_index:]

                non_empty_body_vals = col_body.replace(r'(?i)^nan$', np.nan, regex=True).dropna()
                non_empty_body_vals = non_empty_body_vals[non_empty_body_vals.astype(str).str.strip() != '']

                if not non_empty_body_vals.empty and non_empty_body_vals.isin(right_merge_prefix_joiners).all():
                    is_symbol_only_column_body = True
                
                if not is_symbol_only_column_body:
                    should_abort_merge = False
                    if i + 1 < len(current_cols):
                        nxt_col_name = current_cols[i + 1]
                        normalized_nxt_col = df[nxt_col_name].apply(normalize_for_symbol_check)

                        if normalized_nxt_col.str.contains('%', na=False).any():
                            currency_indices = normalized_col_lower[normalized_col_lower.isin(right_merge_prefix_joiners)].index
                            percent_indices = normalized_nxt_col[normalized_nxt_col.str.contains('%', na=False)].index
                            
                            if not currency_indices.empty and not percent_indices.empty:
                                if currency_indices.max() < percent_indices.min():
                                    should_abort_merge = True

                    if should_abort_merge:
                        i += 1
                        continue

                made_a_merge = False
                rows_to_clear = []
                for idx in df.index[normalized_col_lower.isin(right_merge_prefix_joiners)]:
                    prefix_symbol = normalized_col.loc[idx]
                    prefix_key = normalized_col_lower.loc[idx]
                    joiner = right_merge_prefix_joiners.get(prefix_key, '')
                    next_cell_raw_value = str(df.loc[idx, nxt])
                    next_cell_normalized = normalize_for_symbol_check(next_cell_raw_value)
                    
                    if next_cell_normalized.startswith(prefix_symbol):
                        continue

                    if not next_cell_normalized.startswith(prefix_symbol):
                        if next_cell_raw_value.strip() == "<BORDER>":
                            df.loc[idx, nxt] = f"{prefix_symbol}{joiner}—<BORDER>"
                        else:
                            df.loc[idx, nxt] = f"{prefix_symbol}{joiner}{next_cell_raw_value}".strip()
                        
                        rows_to_clear.append(idx)
                        made_a_merge = True

                if rows_to_clear:
                    df.loc[rows_to_clear, col] = ''
                
                if made_a_merge:
                    df.drop(columns=[col], inplace=True)
                    df_for_comparison.drop(columns=[df_for_comparison.columns[i]], inplace=True)
                    current_cols = df.columns.tolist()
                    continue
            i += 1

    first_data_row_index = get_first_data_row_index(df)
    first_data_row_index = first_data_row_index if first_data_row_index > 0 else 1

    current_cols = df.columns.tolist()
    df_for_comparison_cols = df_for_comparison.columns.tolist()
    df = df.replace(r'(?i)^nan$', '', regex=True).fillna('')
    i = 0

    is_annotation_col_re = re.compile(
        r"""^
        \s*
        ( # Start of a repeating group for one or more tokens
            (?:
                # Alternative 1: Simple symbols like ), %, )bp from your original regex
                \)|%|\)bp
                |
                # Alternative 2: The specific parenthesized footnote rule.
                # This explicitly matches numbers from 0-49 OR letters (like (a), (iv)).
                # It will NOT match (50).
                \(\s*(?:[1-4]?\d|[a-z]+)\s*\)
                |
                # Alternative 3: Digits followed by a letter, with an optional paren
                \)?\s*\d+[a-z]
                |
                # --- NEW, RESTRICTED ALTERNATIVE ---
                # Alternative 4: A single letter from 'a' through 'n' for footnotes.
                # This specific range prevents capturing common data markers like 'x' or 'o'.
                [a-n]
            )
            \s* # Allow optional whitespace between tokens
        )+ # End of the repeating group, must match at least once
        $""",
        re.VERBOSE | re.IGNORECASE
    )
    
    special_merge_symbols = {'%', '%#', ')', ')%', ')bp', ')##DOUBLE_ASTERISK##', 'months', '%]', ']%', '##SINGLE_ASTERISK##', ']', "§", "‡", "•]", "years"}

    contains_footnote_text_re = re.compile(r"[a-z]", re.IGNORECASE)

    is_ratio_table = 'ratio' in df.to_string(index=False, header=False).lower()

    while i < len(current_cols) - 1:
        col_to_check_idx = i + 1
        col_body_series = df.iloc[first_data_row_index:, col_to_check_idx]
        normalized_col_body = col_body_series.apply(normalize_for_symbol_check)

        candidate_eval_mask = ~col_body_series.astype(str).str.contains(
            r'##(?:COLSPAN|ROWSPAN)_\d+##',
            regex=True,
            na=False,
        )
        non_blank_normalized_body = (
            normalized_col_body[candidate_eval_mask]
            .replace('', np.nan)
            .dropna()
        )

        if non_blank_normalized_body.empty:
            i += 1
            continue

        contains_checkbox_current = normalized_col_body.str.contains(r'[☐☒]', na=False).any()
        contains_checkbox_next = False
        if i + 2 < len(current_cols):
            next_col_body = df.iloc[first_data_row_index:, i + 2]
            normalized_next_col_body = next_col_body.apply(normalize_for_symbol_check)
            contains_checkbox_next = normalized_next_col_body.str.contains(r'[☐☒]', na=False).any()

        is_candidate_for_merge = False
        colspan_re = re.compile(r'##COLSPAN_(\w+)##')
        is_potentially_symbol_col = True

        for r_idx, norm_val in non_blank_normalized_body.items():
            row_pos = df.index.get_loc(r_idx)
            if is_annotation_col_re.fullmatch(norm_val) or norm_val in special_merge_symbols:
                continue

            raw_cell_value = str(df.iat[row_pos, col_to_check_idx])
            has_sup_marker = '<sup>' in raw_cell_value or '##SUP##' in raw_cell_value
            if has_sup_marker and re.search(r'\d', norm_val):
                continue

            is_colspan_target_exception = False
            if i < len(current_cols):
                left_cell = df.iat[row_pos, i]
                current_cell = df.iat[row_pos, col_to_check_idx]
                match = colspan_re.search(str(left_cell))
                if match and match.group(0) in str(current_cell):
                    is_colspan_target_exception = True
            
            if not is_colspan_target_exception:
                is_potentially_symbol_col = False
                break

        if is_potentially_symbol_col:
            is_candidate_for_merge = True

        is_special_symbol_col = (
            not non_blank_normalized_body.empty
            and non_blank_normalized_body.isin(special_merge_symbols).all()
        )
        is_x_only_col = not non_blank_normalized_body.empty and (non_blank_normalized_body == 'x').all()
        should_merge_x_conditionally = is_ratio_table and is_x_only_col

        if (is_candidate_for_merge or is_special_symbol_col or should_merge_x_conditionally) and not contains_checkbox_current and not contains_checkbox_next:
            
            contains_numeric_data = False
            for r_idx, norm_val in non_blank_normalized_body.items():
                row_pos = df.index.get_loc(r_idx)
                if is_numeric_like(norm_val) and norm_val not in special_merge_symbols:
                    is_footnote_override = False
                    
                    raw_cell_value = str(df.iat[row_pos, col_to_check_idx])
                    
                    if '<sup>' in raw_cell_value or '##SUP##' in raw_cell_value or '<sub>' in raw_cell_value or '##SUB##' in raw_cell_value:
                        is_footnote_override = True
                    
                    if not is_footnote_override:
                        contains_numeric_data = True
                        break
            
            if contains_numeric_data:
                i += 1
                continue

            if first_data_row_index > 0:
                for h_idx in range(first_data_row_index):
                    raw_merge_val = df.iat[h_idx, i + 1]
                    if not pd.isna(raw_merge_val) and str(raw_merge_val).strip():
                        value_to_merge = str(raw_merge_val)
                        norm_cell_to_merge = normalize_for_symbol_check(value_to_merge)
                        is_header_a_symbol = is_annotation_col_re.fullmatch(norm_cell_to_merge) or norm_cell_to_merge in special_merge_symbols
                        if is_header_a_symbol:
                            target_cell_raw = df.iat[h_idx, i]
                            target_value = '' if pd.isna(target_cell_raw) else str(target_cell_raw)
                            if value_to_merge not in target_value:
                                df.iat[h_idx, i] = f"{target_value}{value_to_merge}".strip()

            has_footnote_text = non_blank_normalized_body.str.contains(contains_footnote_text_re).any()
            for r_idx in non_blank_normalized_body.index:
                row_pos = df.index.get_loc(r_idx)
                raw_merge_val = df.iat[row_pos, col_to_check_idx]
                value_to_merge = '' if pd.isna(raw_merge_val) else str(raw_merge_val)
                if has_footnote_text:
                    raw_target_val = df.iat[row_pos, i]
                    target_value = '' if pd.isna(raw_target_val) else str(raw_target_val)
                    if value_to_merge.strip() and value_to_merge not in target_value:
                        joiner = " "
                        df.iat[row_pos, i] = f"{target_value}{joiner}{value_to_merge}".strip()
                else:
                    dest_col_idx = find_dest_col(df, r_idx, i)
                    raw_target_val = df.iat[row_pos, dest_col_idx]
                    target_value = '' if pd.isna(raw_target_val) else str(raw_target_val)
                    if value_to_merge.strip() and value_to_merge not in target_value:
                        value_to_merge = value_to_merge.lstrip()
                        m = re.search(r'(##COLSPAN_\d+##(?:<BORDER>)?) ?$', target_value)
                        if m:
                            token = m.group(1)
                            base = target_value[: -len(m.group(0))].rstrip()
                            merged = f"{base}{value_to_merge}"
                            if not merged.endswith(token):
                                merged = f"{merged}{token}"
                            df.iat[row_pos, dest_col_idx] = merged.strip()
                        else:
                            df.iat[row_pos, dest_col_idx] = f"{target_value.rstrip()}{value_to_merge}".strip()

            keep_mask = np.ones(df.shape[1], dtype=bool)
            keep_mask[col_to_check_idx] = False
            df = df.loc[:, keep_mask]
            df_for_comparison = df_for_comparison.loc[:, keep_mask]
            current_cols = df.columns.tolist()
            df_for_comparison_cols = df_for_comparison.columns.tolist()
            continue

        i += 1
    
    has_paren = df.apply(
        lambda col: (
            col
            .apply(normalize_for_symbol_check)
            .replace('', np.nan)
            .dropna()
            .eq(')')
            .any()
        )
    ).astype(bool)


    df = df.loc[:, ~has_paren]
    df_for_comparison = df_for_comparison.loc[:, ~has_paren.values]

    df = df.applymap(_close_unclosed_paren)

    df_string = df.to_string()

    first_data_row_index = get_first_data_row_index(df)

    if len(df) > 2 and df.iloc[0, 0] != "x" and "•" not in df_string and "●" not in df_string and "☐" not in df_string and " %]" not in df_string and "##= ##" not in df_string and "▪" not in df_string and "□" not in df_string and "🗹" not in df_string and "☒" not in df_string:
        ONLY_TOKS = {'(', ')', ')%', ')bp', '%'}

        raw = df.astype('string')

        norm = raw.applymap(normalize_for_symbol_check).astype('string')
        norm_nonempty = norm.replace('', pd.NA)
        is_only_tokens = norm.apply(should_flag_as_token_only)

        clean = (raw
            .replace(r'##NEWLINE##', '', regex=True)
            .replace(r'##COLSPAN_\d+##', '', regex=True)
            .replace(r'##ROWSPAN_\d+##', '', regex=True)
            .replace(r'##BOLD_(?:START|END)_\d+##', '', regex=True)
            .replace(r'##ITALIC_(?:START|END)_\d+##', '', regex=True)
            .replace(r'##U_(?:START|END)_\d+##', '', regex=True)
            .replace('$$', '', regex=False)
            .applymap(lambda x: x.strip() if isinstance(x, str) else x)
        )

        clean = clean.replace(r'^\[\]$', '__keep__', regex=True)

        cells = clean.astype('string')
        has_letters  = cells.apply(lambda col: col.str.contains(r'[A-Za-z]', na=False)).any()
        has_numbers  = cells.apply(lambda col: col.str.contains(r'\d', na=False)).any()
        has_currency = cells.apply(lambda col: col.str.contains(r'[\$£¥￥]', na=False)).any()

        keep_mask = (has_letters | has_numbers | has_currency) & ~is_only_tokens
        df = df.loc[:, keep_mask]
        df_for_comparison = df_for_comparison.loc[:, keep_mask.values]

    layout_clean_start = get_first_data_row_index(df)
    if 0 <= layout_clean_start < len(df):
        df.iloc[layout_clean_start:] = df.iloc[layout_clean_start:].applymap(_blank_layout_only_cell)
    df_for_comparison = df.copy().applymap(normalize_for_comparison)
    
    df_for_comparison.columns = [normalize_for_comparison(c) for c in df_for_comparison.columns]

    df, df_for_comparison = apply_cleaning_and_sync(df, df_for_comparison, drop_adjacent_head_dupes, n_head=2)

    df, df_for_comparison = apply_cleaning_and_sync(df, df_for_comparison, drop_pctless_dupes)
    df, df_for_comparison = apply_cleaning_and_sync(df, df_for_comparison, drop_dollarless_dupes)

    df, df_for_comparison = apply_cleaning_and_sync(df, df_for_comparison, drop_subset_columns)
    
    df = _shift_colx_into_named(df)

    df, df_for_comparison = apply_cleaning_and_sync(df, df_for_comparison, drop_visually_redundant_blank_cols, header_rows=3)
    df, df_for_comparison = apply_cleaning_and_sync(df, df_for_comparison, drop_visually_redundant_blank_cols, header_rows=2)
    
    df, df_for_comparison = apply_cleaning_and_sync(df, df_for_comparison, _drop_header_and_empty_cols, header_rows=3, min_blank_rows=8)
    df, df_for_comparison = apply_cleaning_and_sync(df, df_for_comparison, _drop_header_and_empty_cols, header_rows=2, min_blank_rows=3)
    df, df_for_comparison = apply_cleaning_and_sync(df, df_for_comparison, _drop_header_and_empty_cols, header_rows=1, min_blank_rows=3)

    df_for_comparison = df.copy().applymap(normalize_for_comparison)
    df_for_comparison.columns = [normalize_for_comparison(c) for c in df_for_comparison.columns]
    df, df_for_comparison = apply_cleaning_and_sync(
        df,
        df_for_comparison,
        _late_drop_blank_header_subset_cols,
        header_rows=2,
        min_blank_rows=3,
    )

    df = df.applymap(lambda x: '—' if isinstance(x, str) and x.replace("\u2063", '').strip() == '' else x)

    df = df.replace(r'^\s*-+\s*$', np.nan, regex=True)

    df = df.dropna(how='all')

    df = drop_active_colspan_empty_cols(df, allow_dollar_sign=True)

    cols_to_drop = []
    for i in range(df.shape[1] - 1, 0, -1):
        col_name = df.columns[i]
        col_series = df[col_name]

        if is_sup_only_column(col_series):
            left_col_name = df.columns[i - 1]
            df[left_col_name] = df[left_col_name].astype(str) + col_series.fillna('').astype(str)
            cols_to_drop.append(col_name)

    if cols_to_drop:
        df.drop(columns=cols_to_drop, inplace=True)

    return df
    
def replace_checkbox_symbols(text: str) -> str:
    """
    Map any straggling checkbox, Wingdings, or stray-letter marker to a
    consistent Markdown format. This serves as a final cleanup pass on
    extracted text.
    """
    if not isinstance(text, str):
        return text

    s = text

    direct_map = {
        '☒': '[x]', '☑': '[x]', '✓': '[x]', '✔': '[x]',
        '☐': '[ ]', '❑': '[ ]', '❒': '[ ]', '❏': '[ ]', '❐': '[ ]',

        'þ': '[x]', 'ý': '[x]',
        '¨': '[ ]', 'ü': '[ ]',

        'X': '[x]', 'x': '[x]',
        'O': '[ ]', 'o': '[ ]',
    }

    core_symbol = re.sub(r'##(SUP|/SUP|BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##', '', s)
    core_symbol = core_symbol.replace('##NEWLINE##', '').replace('\u00A0', '').replace('\u2063', '').replace('<BORDER>', '')
    core_symbol = core_symbol.strip()

    if core_symbol in direct_map:
        markdown_checkbox = direct_map[core_symbol]
        return s.replace(core_symbol, markdown_checkbox)

    s = re.sub(r'\s+[xX]\s*$', ' [x]', s)
    s = re.sub(r'\s+[oO]\s*$', ' [ ]', s)

    return s

def convert_wingdings_boxes(soup: BeautifulSoup) -> None:
    """
    Turn Wingdings and Webdings symbols into their Unicode equivalents.
    This works for any tag whose inline style or face attribute contains
    the words "wingdings" or "webdings". It uses a comprehensive internal map.
    """
    for tag in soup.find_all(True):
        f_hint = (tag.get("style", "") + tag.get("face", "")).lower()
        
        font_family = None
        if "wingdings 3" in f_hint:
            font_family = "wingdings 3"
        elif "wingdings 2" in f_hint:
            font_family = "wingdings 2"
        elif "wingdings" in f_hint:
            font_family = "wingdings"
        elif "webdings" in f_hint:
            font_family = "webdings"
        else:
            continue

        char = tag.get_text(strip=True).replace('##NEWLINE##', '')
        if not char:
            continue
            
        translation_map = WINGDINGS_MAP.get(font_family, {})
        
        repl = translation_map.get(char)
        
        if repl is not None:
            tag.replace_with(NavigableString(repl))

def add_superscript(text):
    """
    Finds common footnote markers (*, **, (1), (a), etc.) at the end of a string
    and wraps them in <sup> tags, ignoring common non-footnote suffixes.
    This version is modified to ignore single asterisks.
    """
    if not isinstance(text, str):
        return text
    
    footnote_pattern = re.compile(r'^(.*?)(\s*)((?:\*+|\(\d+\)|\([a-zA-Z]+\))(?:\*+|\(\d+\)|\([a-zA-Z]+\))*)$')
    
    match = footnote_pattern.match(text)
    
    if match and match.group(3):
        base = match.group(1)
        space = match.group(2)
        marker = match.group(3)

        if marker == '*':
            return text

        return f"{base}{space}<sup>{marker}</sup>"
        
    return text

def remove_empty_bold_tags(soup: BeautifulSoup):
    """
    Finds and completely removes any <b> or <strong> tag that only
    contains whitespace or  . This prevents the creation of "** **" artifacts.
    """
    for tag in soup.find_all(['b', 'strong']):
        if not tag.get_text(strip=True):
            tag.decompose()

def merge_whitespace_tags(soup: BeautifulSoup):
    """
    Finds inline tags (b, i, u, span, etc.) that only contain whitespace,
    appends a single space to the preceding element, and removes the original tag.
    This prevents important spaces between elements from being lost during parsing.
    
    e.g., <i>Title.</i><b> </b><span>Content...</span>
    becomes: <i>Title. </i><span>Content...</span>
    """
    whitespace_tags = soup.find_all(['b', 'strong', 'i', 'em', 'u', 'span', 'font'])
    
    for tag in whitespace_tags:
        if not tag.get_text(strip=True) and tag.get_text():
            prev_element = tag.previous_sibling
            
            while isinstance(prev_element, NavigableString) and not prev_element.strip():
                prev_element = prev_element.previous_sibling

            if prev_element:
                if isinstance(prev_element, Tag):
                    prev_element.append(NavigableString(' '))
                elif isinstance(prev_element, NavigableString):
                    prev_element.replace_with(NavigableString(str(prev_element) + ' '))
                
                tag.decompose()

SENTINEL_RE = re.compile(
    r'(##(?:ROWSPAN|COLSPAN)_\d+##|##NEWLINE##|##INDENT##|<BORDER(?:_TOP)?>|&nbsp;|<br\s*/?>)',
    re.I
)

def drop_tag_only_rows_cols(
        df: pd.DataFrame,
        skip_rows: int = 0,
        cols_only: bool = False
) -> pd.DataFrame:
    """Remove columns (and, unless cols_only=True, rows) that become empty
    once tag-like markers are stripped.

    Args:
        df: Input DataFrame.
        skip_rows: Keep these top rows untouched when also dropping rows.
        cols_only: If True, drop only empty columns; leave all rows intact.

    Returns:
        A cleaned DataFrame.
    """
    def clean(col: pd.Series) -> pd.Series:
        return (col.fillna('')
              .astype(str)
              .str.replace(r'(?i)\b(?:nan|none)\b', '', regex=True)
              .str.replace(SENTINEL_RE, '', regex=True)
              .str.replace('[\u00A0\u200B-\u200D\u2060\u2063\uFEFF]', '', regex=True)
              .str.replace(r'\s+', '', regex=True)
              .str.replace('—', ''))

    body = df.iloc[skip_rows:]
    cleaned = body.apply(clean)

    keep_cols = ~cleaned.eq('').all(axis=0)

    if cols_only:
        return df.loc[:, keep_cols.values]

    cleaned_kept = cleaned.loc[:, keep_cols]
    keep_body_rows = ~cleaned_kept.eq('').all(axis=1)

    row_mask = np.r_[np.ones(skip_rows, dtype=bool), keep_body_rows.values]

    return df.loc[row_mask, keep_cols.values]

def df_to_markdown(df: pd.DataFrame, is_clean: bool = False, disable_numparse: bool = False, is_legacy_form4_table1 = False, is_legacy_form4_table2 = False) -> str:
    """
    Converts a DataFrame to a Markdown string. This version includes a special
    check to identify and reformat 2x2 "footnote tables" into a single line of text.
    """
    preserved_header_rows = df.attrs.get('preserved_header_rows')

    if df.shape[0] == 1 and df.shape[1] == 2:
        marker = str(df.columns[0]).strip()
        content = str(df.columns[1]).strip()
        
        is_footnote_in_header = (
            re.fullmatch(r'\(\s*\d+\s*\)', marker) and 
            all(_is_blank(val) for val in df.iloc[0])
        )

        if is_footnote_in_header:
            formatted_marker = add_superscript(marker)
            return f"{formatted_marker} {content}"

        marker = str(df.iloc[0, 0]).strip()
        content = str(df.iloc[0, 1]).strip()
        if re.fullmatch(r'\(\s*\d+\s*\)', marker):
             formatted_marker = add_superscript(marker)
             return f"{formatted_marker} {content}"

    if df.shape[0] == 2 and df.shape[1] == 2:
        is_row0_blank = all(_is_blank(val) for val in df.iloc[0])
        is_row1_blank = all(_is_blank(val) for val in df.iloc[1])

        content_row = -1
        if is_row0_blank and not is_row1_blank:
            content_row = 1
        elif is_row1_blank and not is_row0_blank:
            content_row = 0
        
        if content_row != -1:
            marker = str(df.iloc[content_row, 0]).strip()
            content = str(df.iloc[content_row, 1]).strip()
            if re.fullmatch(r'\(\s*\d+\s*\)', marker):
                formatted_marker = add_superscript(marker)
                return f"{formatted_marker} {content}"
    try:
        df = df.replace(r'^[–—-]\s*$', '', regex=True)
        df = (
            df.replace(r'^\s*$', np.nan, regex=True)
              .dropna(axis=1, how='all')
              .dropna(axis=0, how='all')
              .reset_index(drop=True)
        )
        if df.empty:
            return ""
        
        df = df.replace('nan', '', regex=False)

        df = drop_active_colspan_empty_cols(df, allow_dollar_sign=True)

        first_data_row_index = get_first_data_row_index(df)

        for col_idx in range(df.shape[1]):
            for row_idx in range(first_data_row_index):
                cell_content = str(df.iat[row_idx, col_idx])

                if '<BORDER>' in cell_content:
                    
                    start_merge_row = 0
                    for k in range(row_idx - 1, -1, -1):
                        if '<BORDER>' in str(df.iat[k, col_idx]):
                            start_merge_row = k + 1
                            break
                    
                    vals_to_merge = df.iloc[start_merge_row : row_idx + 1, col_idx].fillna('').astype(str).tolist()
                    
                    clear_start_row = start_merge_row

                    if vals_to_merge and '##ITALIC_START_' in vals_to_merge[0]:
                        vals_to_merge = vals_to_merge[1:]
                        clear_start_row += 1
                    
                    merged_text = '##NEWLINE##'.join(vals_to_merge)
                    span_tags = re.findall(r'##(?:ROWSPAN|COLSPAN)_\d+##', merged_text)

                    is_duplicated_span = False

                    if span_tags:
                        df_as_string = df.to_string()
                        for tag in span_tags:
                            if df_as_string.count(tag) > 1:
                                is_duplicated_span = True
                                break
                    
                    if (
                        not is_numeric_like(merged_text)
                        and not is_duplicated_span
                    ):
                        df.iat[row_idx, col_idx] = merged_text

                        for i in range(clear_start_row, row_idx):
                            df.iat[i, col_idx] = ''
        
        df = df.replace(
            to_replace=r"(<BORDER>)",
            value="",
            regex=True
        )
        
        ZERO_WIDTH = r'\u200B\u200C\u200D\u2060\u2063\uFEFF'

        blank_pattern = rf'^(?:[\s\r\n{ZERO_WIDTH}\u00A0]|##NEWLINE##)+$'

        df.replace(blank_pattern, np.nan, regex=True, inplace=True)

        df.dropna(axis=0, how='all', inplace=True)
        df.dropna(axis=1, how='all', inplace=True)
        if is_legacy_form4_table1:
            df = df.drop(columns=[
                col for col in df.columns 
                if df[col].iloc[1:].isna().all()
            ])

        table_as_string = df.to_string().lower()
        keywords = ["large accelerated filer", "emerging growth company", "accelerated filer", "non-accelerated filer"]

        if any(keyword in table_as_string for keyword in keywords):
            num_columns = df.shape[1]
            null_header_df = pd.DataFrame([[''] * num_columns])
            
            null_header_df.columns = df.columns
            
            df = pd.concat([null_header_df, df], ignore_index=True)

        df.columns = [
            re.sub(r'(##)\.[1-9]$', r'\1', col) if isinstance(col, str) else col
            for col in df.columns
        ]

        if len(df.columns) > 1:
            cols_to_drop = []
            for i in range(1, len(df.columns)):
                raw = df.iat[0, i]
                
                if pd.isna(raw):
                    continue

                if isinstance(raw, (int, np.integer)):
                    x = int(raw)
                else:
                    txt = str(raw)
                    txt = re.sub(r'##(SUP|/SUP|SUB|/SUB|BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##', '', txt)
                    txt = txt.replace('##NEWLINE##', '').replace('<br>', '').strip()

                    m = re.match(r'^(\d+)$', txt)
                    x = int(m.group(1)) if m else None

                if x is None or x >= 15:
                    continue
                
                if not df.iloc[:, i].iloc[1:].replace(r'^\s*$', np.nan, regex=True).isna().all():
                    continue

                left_col_index = i - 1
                left_val_raw = df.iat[0, left_col_index]
                left_val = '' if pd.isna(left_val_raw) else str(left_val_raw)
                
                df.iat[0, left_col_index] = f"{left_val.replace('##NEWLINE##', '').strip()}<sub>{x}</sub>"
                
                cols_to_drop.append(df.columns[i])

            if cols_to_drop:
                df.drop(columns=cols_to_drop, inplace=True)


        df = df.replace(r'^\$nan$', '$—', regex=True)
                    
        if not all(pd.isna(df.columns)):
            if preserved_header_rows:
                df.columns = [''] * df.shape[1]
            else:
                header = pd.DataFrame([df.columns], columns=df.columns)
                df = pd.concat([header, df], ignore_index=True)
                df.columns = [''] * df.shape[1]
    
        body = None
        if len(df) > 1:
            row0 = df.iloc[0].dropna()
            vals = pd.to_numeric(row0, errors="coerce")

            auto_like = (
                vals.notna().all()
                and len(vals) > 0
                and vals.is_unique
                and vals.is_monotonic_increasing
                and (vals < 200).all()
            )

            if auto_like and len(df) >= 2:
                if len(df):
                    new_header = [''] * len(df.columns)
                    body = df.iloc[1:].reset_index(drop=True)
                    body.columns = new_header
                else:
                    body = pd.DataFrame(columns=df.columns)
            else:
                body = df.copy()

        if body is None:
            return ""
        
        bullet_chars = {'○', '•', '●', '*', '·', '◦', '➢', '-', '▪'}

        if body.empty and len(body.columns) == 2:
            bullet = str(body.columns[0]).strip()
            if bullet in bullet_chars:
                text = str(body.columns[1]).strip()
                return f"{bullet} {text}"

        if body.empty and body.columns.empty:
            return ""



        def _clean_header(col):
            """Removes a trailing period from numeric headers like '2024.'."""
            s = str(col).strip()
            if re.fullmatch(r'\d+\.', s):
                return s[:-1]
            return s

        new_cols = [_clean_header(col) for col in body.columns]
        body.columns = new_cols

        IND = "\u2063"

        body = body.applymap(_strip_commas_in_paren)

        body_shape = body.shape
        if body_shape[0] > 2:
            body = drop_tag_only_rows_cols(body)

        body_string = body.to_string().lower()

        is_toc = 'item' in body_string and 'part' in body_string or ('governance' in body_string) or (" 1.0" in body_string) or ("page" in body_string) or ("financial statements" in body_string)
         
        if is_toc:
            body = body.map(lambda x: re.sub(r'\.0$', '', str(x)))

        def _clean_cell(val: object) -> str:
            s = html.unescape(str(val) if val is not None else "")

            s = re.sub(
                r'##(?:<sup>|</sup>|BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|'
                r'ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##', "", s
            )
            s = (s.replace("##NEWLINE##", "")
                .replace("<BORDER>", "")
                .replace("\u00A0", " ")
                .replace("\u2063", "##INDENT##"))
            s = re.sub(r'(?i)</?sup[^>]*>', "", s)
            s = re.sub(r'^\s*\^(.+?)\^\s*$', r'\1', s)
            s = re.sub(r'\s+', " ", s).strip()

            return s
        
        clean = body.applymap(_clean_cell)
        clean = clean.replace(r'^\s*$', "", regex=True)
        body = body[~(clean == "").all(axis=1)]

        list_marker_pattern = re.compile(
            r'^\s*(?:\[(?: |x)\]'
            r'|[ivxlcdm]+[.)]?'
            r'|\d+[.)]'
            r'|[a-z][.)]'
            r'|\([a-z0-9]+\)'
            r'|[-○•●·◦➢☐□☒⌧♦⧫▪]'
            r'|\#\#SINGLE_ASTERISK\#\#'
            r'|\#\#DOUBLE_ASTERISK\#\#'
            r'|\#\#TRIPLE_ASTERISK\#\#'
            r'|\#'
            r'|<sup>\s*\(?[a-z0-9]+\)?\s*(?:</sup>|<sup>)?'
            r')\s*$',
            re.IGNORECASE
        )

        def normalize_marker(s: str) -> str:
            s = html.unescape(str(s))
            s = re.sub(
                r'##(?:BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|'
                r'ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##',
                '',
                s
            )

            s = s.replace('##NEWLINE##', '')
            s = s.replace('<BORDER>', '')
            s = s.replace('\u00A0', ' ')
            s = s.replace('\u2063', '')
            s = s.replace('##INDENT##', '')
            s = s.replace('&nbsp;', '')
            s = s.replace("<sup>##SINGLE_ASTERISK##</sup>", "##SINGLE_ASTERISK##")

            s = re.sub(r'^\s*\^(.+?)\^\s*$', r'\1', s)
            s = re.sub(r'\s+', ' ', s).strip()
            return s
        
        if body.shape[1] == 3:
            col1 = body.iloc[:, 0].astype(str).str.strip()
            col2 = body.iloc[:, 1].astype(str).str.strip()

            is_col1_all_digits = col1.str.isdigit().all()
            is_col2_all_periods = (col2 == '.').all()

            if is_col1_all_digits and is_col2_all_periods:
                list_items = []
                for index, row in body.iterrows():
                    number = row.iloc[0]
                    content = str(row.iloc[2]).strip()
                    list_items.append(f"{number}. {content}")
                
                return "\n\n".join(list_items) + "\n\n"
                                
        if body.shape[1] == 2 and all(not str(c).strip() for c in body.columns):
            first_col = body.iloc[:, 0].astype(str).str.strip()
            non_empty_markers = first_col[first_col != '']

            if body.shape[0] == 1:
                pattern = re.compile(
                    r'^(?:'
                    r'Item\s+\d+\s?\.'
                    r'|'
                    r'##BOLD_START_(\d+)##\s*Item\s+\d+\s?\.'
                    r'\s*##BOLD_END_\1##'
                    r'|'
                    r'(?:[1-9]|1[0-5])\.0'
                    r')$'
                )

                def is_valid(s) -> bool:
                    return bool(pattern.fullmatch(str(s)))

                if is_valid(body.iloc[0,0]):
                    return f"{str(body.iloc[0, 0]).replace('.0', '.')} {str(body.iloc[0, 1])}"
                
            if not non_empty_markers.empty and non_empty_markers.map(normalize_marker).str.fullmatch(list_marker_pattern).all():
                ALPHA_RE   = re.compile(r'^\(?[a-z]\)?\.?$', re.IGNORECASE)
                ROMAN_RE   = re.compile(r'^\(?[ivxlcdm]+\)?\.?$', re.IGNORECASE)

                md_list_items = []
                for _, row in body.iterrows():
                    marker = str(row.iloc[0]).strip()
                    marker_norm = normalize_marker(marker)

                    content = str(row.iloc[1]).strip()
                    content = re.sub(r'\s*<(br)\s*/?>\s*$', '', content, flags=re.IGNORECASE).strip()
                    content = content.replace("##NEWLINE##", "")

                    
                    md_list_items.append(f"{marker} {content}".replace("##NEWLINE##", " ").replace('&nbsp;', ''))

                return "\n\n".join(md_list_items)
            
        if body.shape == (1, 1) and all(not str(c).strip() for c in body.columns):
            cell_content = str(body.iloc[0, 0]).strip()

            cell_content = cell_content.strip()
            cell_content = re.sub(r'\s+', ' ', cell_content)

            if cell_content:
                return cell_content

        if preserved_header_rows:
            normalized_header_rows: List[List[str]] = []
            target_width = body.shape[1]
            for row in preserved_header_rows:
                padded_row = list(row[:target_width])
                if len(padded_row) < target_width:
                    padded_row.extend([''] * (target_width - len(padded_row)))
                normalized_header_rows.append(padded_row)

            if normalized_header_rows:
                header_df = pd.DataFrame(normalized_header_rows, columns=body.columns)
                body = pd.concat([header_df, body], ignore_index=True)

        return to_compact_markdown(body, index=False, disable_numparse=disable_numparse)

    except Exception as e:
        error_message = f"[TABLE PARSE ERROR]: {e}"
        
        print(f"[TABLE PARSE ERROR in {_state.CURRENT_PROCESSING_FILE}]: {e}")
        logging.error(
            f"FILE: {_state.CURRENT_PROCESSING_FILE} (error in df_to_markdown)\n"
            f"ERROR: {error_message}\n"
            f"TRACEBACK:\n{traceback.format_exc()}"
        )
        return "<FAILED TO PARSE TABLE>"

def normalize_dl_lists(soup: BeautifulSoup):
    """
    Recursively finds all list-like <dl> tags and converts them to a
    flat sequence of <p> tags. By repeatedly finding and processing the
    deepest list first ("inside-out"), it ensures indentation levels
    are calculated correctly.
    """
    INDENT_CHAR = "##INDENT##"
    
    list_marker_re = re.compile(r'^\s*(?:[○•●·◦➢□☐☑☒🗷✓✔]|\d+\.\d*[A-Z]?|\d+\.|\([a-zA-Z0-9]+\))\s*$')

    while True:
        deepest_dl = None
        max_depth = -1

        for dl in soup.find_all('dl'):
            dt_tags = dl.find_all('dt', recursive=False)
            is_list_like = dt_tags and all(list_marker_re.match(dt.get_text(strip=True)) for dt in dt_tags)
            
            if not is_list_like:
                continue

            depth = len(list(dl.find_parents('dl')))
            if depth > max_depth:
                max_depth = depth
                deepest_dl = dl
        
        if deepest_dl is None:
            break

        level = max_depth
        new_paragraphs = []

        for child in list(deepest_dl.children):
            if child.name == 'dt':
                dd = child.find_next_sibling('dd')
                if dd and dd.previous_sibling is child:
                    new_p = soup.new_tag("p")
                    

                    indent_prefix = INDENT_CHAR * level
                    new_p.append(NavigableString(indent_prefix))
                    
                    for content_node in list(child.children):
                        new_p.append(content_node.extract())
                    
                    new_p.append(NavigableString(" "))
                    
                    for content_node in list(dd.children):
                        new_p.append(content_node.extract())
                    
                    new_paragraphs.append(new_p)

                    child.decompose()
                    dd.decompose()

        deepest_dl.replace_with(*new_paragraphs)
                    
def defragment_font_tags(soup: BeautifulSoup):
    """
    Merges adjacent <font> tags to prevent unwanted spaces from being
    inserted between word fragments during text extraction.
    e.g. <font>W</font><font>ord</font> becomes <font>Word</font>
    """
    for font_tag in soup.find_all('font'):
        if not font_tag.parent:
            continue

        while True:
            next_element = font_tag.next_sibling
            
            if isinstance(next_element, NavigableString) and not next_element.strip():
                whitespace_node = next_element
                next_element = whitespace_node.next_sibling
                whitespace_node.extract()

            if next_element and next_element.name == 'font':
                for child in list(next_element.contents):
                    font_tag.append(child.extract())
                
                next_element.decompose()
            else:
                break

def unwrap_fragmenting_tags(soup: BeautifulSoup):
    """
    Finds and merges <small> tags with adjacent text nodes to prevent
    unwanted spaces from being inserted between word fragments. This version
    manually rebuilds the parent's content to ensure reliable merging.
    """
    parents = {tag.parent for tag in soup.find_all('small') if tag.parent}

    for parent in parents:
        new_contents = []
        for child in list(parent.contents):
            if child.name == 'small':
                small_text = child.get_text()
                if new_contents and isinstance(new_contents[-1], NavigableString):
                    new_contents[-1] = NavigableString(str(new_contents[-1]) + small_text)
                else:
                    new_contents.append(NavigableString(small_text))
            elif isinstance(child, (Tag, NavigableString)):
                new_contents.append(child)

        parent.clear()
        for new_child in new_contents:
            parent.append(new_child)

def md_table_2row_header(df: pd.DataFrame) -> str:
    """
    Render `df` as markdown where the header row is blank and the
    original header text appears as the first row(s) of the body.
    """
    mains, subs, last_main = [], [], ""
    has_subs = any("<br>" in str(c) for c in df.columns)

    for c in df.columns:
        main, sub = (str(c).split("<br>", 1) if "<br>" in str(c) else (str(c), ""))
        main, sub = main.strip(), sub.strip()
        if not main:
            main = last_main
        else:
            last_main = main
        mains.append(main or " ")
        subs.append(sub  or " ")

    blank_header = "| " + " | ".join(" " for _ in mains) + " |"
    sep          = "| " + " | ".join("---" for _ in mains) + " |"

    body = ["| " + " | ".join(mains) + " |"]
    if has_subs:
        body.append("| " + " | ".join(subs) + " |")

    for _, row in df.iterrows():
        body.append("| " + " | ".join(
            map(str, row.replace({np.nan: '—'}).tolist())) + " |")

    return "\n".join([blank_header, sep, *body])

__all__ = [name for name in globals() if not name.startswith("__")]
