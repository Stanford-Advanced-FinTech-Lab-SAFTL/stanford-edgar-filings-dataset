from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.parsers.html.html import clean_phone_numbers
from stanford_edgar_parser.parsers.html.preprocessing import _restore_markdown_links
from stanford_edgar_parser.utils.bootstrap import apply_markdown_hardcodes, re

BULLETS = "○•●·◦➢▪"
BOLD_SPLIT_RE = re.compile(r'\*\* +')

def _fix_paragraph_bold_runs(txt: str) -> str:
    out, last = [], 0

    for m in BOLD_SPLIT_RE.finditer(txt):
        i = m.start()

        window = txt[max(0, i-150):i]

        if window.count("**") >= 2:
            continue
        if re.search(rf"[{BULLETS}]\s*\*\*\s*$", window):
            continue

        out.append(txt[last:i].rstrip())
        out.append("\n\n**")
        last = m.end()

    out.append(txt[last:])
    return "".join(out)

def _convert_bullet_tables_to_lists(markdown_content: str) -> str:
    """
    Finds and converts two-column Markdown tables that are used to format
    bulleted lists into proper list items.
    
    e.g., | • | Some text... |  ->  • Some text...
    """
    table_pattern = re.compile(r'\n---\n\n(.*?)\n\n---\n', re.S)
    bullet_chars = {'○', '•', '●', '*', '·', '◦', '➢', '▪'}

    def replacer(match):
        md_table_str = match.group(1)
        lines = md_table_str.strip().split('\n')

        if len(lines) < 3:
            return match.group(0)

        header_cells = [cell.strip() for cell in lines[0].strip('|').split('|')]
        if any(header_cells):
            return match.group(0)

        separator_cells = [cell.strip() for cell in lines[1].strip('|').split('|')]
        if len(separator_cells) != 2:
            return match.group(0)

        list_items = []
        is_bullet_table = True
        for line in lines[2:]:
            data_cells = [cell.strip() for cell in line.strip('|').split('|')]
            if len(data_cells) != 2:
                is_bullet_table = False
                break

            bullet_part = data_cells[0]
            text_part = data_cells[1]

            if bullet_part not in bullet_chars:
                is_bullet_table = False
                break
            
            list_items.append(f"{bullet_part} {text_part}")

        if is_bullet_table and list_items:
            return "\n".join(list_items)
        
        return match.group(0)

    return table_pattern.sub(replacer, markdown_content)

def _format_footnote_lists(markdown_content: str) -> str:
    """
    Finds and formats footnotes that appear either as two-column tables
    or as simple numbered lines, converting the number into a superscript.
    """
    table_pattern = re.compile(r'\n---\n\n(.*?)\n\n---\n', re.S)

    def table_replacer(match):
        md_table_str = match.group(1)
        lines = md_table_str.strip().split('\n')

        if len(lines) != 3:
            return match.group(0)
        if any(cell.strip() for cell in lines[0].strip('|').split('|')):
            return match.group(0)
        if len(lines[1].strip('|').split('|')) != 2:
            return match.group(0)

        data_cells = [cell.strip() for cell in lines[2].strip('|').split('|')]
        if len(data_cells) != 2:
            return match.group(0)

        number_part, text_part = data_cells
        
        num_match = re.fullmatch(r'(?:<sup>)?\s*(\d{1,2})\s*(?:</sup>)?', number_part)
        
        if num_match and text_part:
            num_str = num_match.group(1)
            return f"<sup>{num_str}</sup> {text_part}"

        return match.group(0)

    content = table_pattern.sub(table_replacer, markdown_content)

    footnote_line_pattern = re.compile(r'^(\d{1,2})\.\s+(?=[A-Z])', re.MULTILINE)
    
    content = footnote_line_pattern.sub(r'<sup>\1</sup> ', content)
    
    return content

def _remove_page_numbers(markdown_content: str) -> str:
    """
    Removes page numbers from the document by targeting three patterns:
    1. Standalone integers on a line by themselves (e.g., "1", "A-1").
    2. Standalone integers surrounded by hyphens (e.g., "-1-").
    3. The above patterns when they are the sole content of a single-cell Markdown table.
    The number must be less than 500 to be considered a page number.
    This version also collapses the extra newlines left by the removal.
    """

    pattern_standalone = re.compile(
        r"(\n{2,}|^)"
        r"(?:<br\s*/?>)?"
        r"[ \t]*"
        r"(?:"
            r"(?:[A-Z]+-)?(\d{1,3})(?:\.)?"
            r"|"
            r"-\s*(\d{1,3})\s*-"
            r"|"
            r"page\s+(\d{1,3})(?:\.)?"
        r")"
        r"[ \t]*"
        r"(\n{2,}|$)",
        re.MULTILINE | re.IGNORECASE
    )

    def replacer_standalone(m):
        try:
            num_str = m.group(2) or m.group(3) or m.group(4)
            page_num = int(num_str)
            if page_num < 500:
                return "\n\n"
        except (ValueError, TypeError, IndexError):
            pass
        return m.group(0)

    content = pattern_standalone.sub(replacer_standalone, markdown_content)

    pattern_table = re.compile(r'\n---\n\n(.*?)\n\n---\n', re.S)

    def replacer_table(match):
        """Callback to check if a table block is just a page number."""
        table_content = match.group(1).strip()
        lines = table_content.split('\n')

        if len(lines) == 3:
            data_row = lines[2].strip()
            cell_match = re.fullmatch(
                r'\|\s*(?:(?:[A-Z]+-)?(\d{1,3})(?:\.)?|-\s*(\d{1,3})\s*-)\s*\|',
                data_row
            )
            if cell_match:
                try:
                    num_str = cell_match.group(1) or cell_match.group(2)
                    page_num = int(num_str)
                    if page_num < 500:
                        return "\n\n"
                except (ValueError, TypeError, IndexError):
                    pass
        
        return match.group(0)

    content = pattern_table.sub(replacer_table, content)
    
    return re.sub(r'\n{3,}', '\n\n', content)

BULLET_BOLD_SPLIT_RE = re.compile(
    rf"""(?x)
        (^[^\n]{{0,150}})      # 1) a look-back window ≤150 chars, captured
        \n+                    # 2) the offending newline(s)
        (\*\*[A-Za-z])         # 3) "**S"  (opening bold + letter)
    """,
    re.M,
)

def merge_adjacent_italics(s: str) -> str:
    pair = re.compile(r"##ITALIC_END_(\d+)####ITALIC_START_(\d+)##")
    while True:
        m = pair.search(s)
        if not m:
            break
        a, b = m.group(1), m.group(2)
        start_tag = f"##ITALIC_START_{a}##"
        idx = s.rfind(start_tag, 0, m.start())

        before, after = s[:m.start()], s[m.end():]

        if idx != -1:
            before = before[:idx] + f"##ITALIC_START_{b}##" + before[idx + len(start_tag):]

        if before and after and not before[-1].isspace() and not after[0].isspace():
            s = before + " " + after
        else:
            s = before + after
    return s

def merge_bracket_fragmented_underlines(s: str) -> str:
    """
    Repairs underline placeholder runs that split bracketed text into
    adjacent fragments, e.g.:
      ##U_START_a##[##U_END_a####U_START_b##Reserved##U_END_b##
    -> ##U_START_b##[Reserved##U_END_b##

    and the symmetric closing-bracket case.
    """
    while True:
        updated = s
        updated = re.sub(
            r'##U_START_(\d+)##\[\s*##U_END_\1##\s*##U_START_(\d+)##(.*?)##U_END_\2##',
            r'##U_START_\2##[\3##U_END_\2##',
            updated,
        )
        updated = re.sub(
            r'##U_START_(\d+)##(.*?)##U_END_\1##\s*##U_START_(\d+)##\]\s*##U_END_\3##',
            r'##U_START_\1##\2]##U_END_\1##',
            updated,
        )
        if updated == s:
            break
        s = updated
    return s

def collapse_redundant_bold_placeholders(s: str) -> str:
    """
    Collapses duplicated bold placeholder wrappers before they are restored
    to literal markdown asterisks.

    This is intentionally done at the placeholder stage so escaped literal
    asterisks in the source document are not touched.
    """
    while True:
        updated = s

        updated = re.sub(
            r"##BOLD_START_\d+##"
            r"((?:##(?:BOLD|ITALIC|U)_(?:START|END)_\d+##|\s)*)"
            r"(##BOLD_START_(\d+)##.*?##BOLD_END_\3##)"
            r"((?:##(?:BOLD|ITALIC|U)_(?:START|END)_\d+##|\s)*)"
            r"##BOLD_END_\d+##",
            r"\1\2\4",
            updated,
            flags=re.DOTALL,
        )

        updated = re.sub(
            r"##BOLD_START_\d+##(?=(?:\s|##NEWLINE##)*##BOLD_START_\d+##)",
            "",
            updated,
        )
        updated = re.sub(
            r"(##BOLD_END_\d+##)(?:\s|##NEWLINE##)*##BOLD_END_\d+##",
            r"\1",
            updated,
        )

        if updated == s:
            break
        s = updated
    return s

def normalize_malformed_markdown_emphasis(s: str) -> str:
    """
    Repair small emphasis-fragment artifacts created when nested source styles
    are split across adjacent spans before Markdown marker restoration.
    """
    s = re.sub(r'(?m)^\*((?:&nbsp;|[ \t])+)\*[ \t]*$', r'\1', s)
    s = re.sub(
        r'(?m)^\*((?:&nbsp;|[ \t])+)\*{4}([^*\n]+?)\*{3}[ \t]*$',
        r'\1***\2***',
        s,
    )
    s = re.sub(r'(?<!\*)\*%\*\*\*(?!\*)', r'***%***', s)
    s = re.sub(
        r'(?m)^(\s*)\*\*\*([^*\n]*?)\*\*\*\*([^*\n]+)\*\s*$',
        lambda m: f"{m.group(1)}***{m.group(2)}{m.group(3)}***",
        s,
    )
    s = re.sub(
        r'(?<!\*)\*("?)\*\*\*\*([A-Za-z][^*\n]{0,80}?)\*\*\*\*',
        r'*\1**\2**',
        s,
    )
    s = re.sub(
        r'(?<!\*)\*{4}([A-Za-z][^*\n]{0,160}?[A-Za-z])\*{4}(?!\*)',
        r'**\1**',
        s,
    )
    s = re.sub(r'\*\*([,.:;])\*\*', r'\1', s)
    s = re.sub(r'\*\*\(\*\*([^*\n]+?)\*\*\)\*\*', r'**(\1)**', s)
    s = re.sub(r'\*\*([^*\n]{0,160}?\()\*\*([^*\n]+?)\*\*\)\*\*', r'**\1\2)**', s)
    s = re.sub(
        r'\*\*([^*\n|]+?)\*\*((?:\^|\~)[^~^\n|]+(?:\^|\~))\*\*([^A-Za-z*\n|]*?)\*\*',
        r'**\1\2\3**',
        s,
    )
    s = re.sub(r'(\*\*\*[^*|\n]+?)\*([.,:;])\*\*', r'\1\2***', s)
    s = re.sub(r'(\*\*[^*|\n]+?)\*([.,:;])\*\*\*', r'\1\2**', s)
    s = re.sub(r'(\*{3}[^*\n]+?)\*+\.\*+', r'\1***.', s)
    s = re.sub(r'(?<=\w)\*{4}(?=\s*<br\s*/?>)', '', s)
    s = re.sub(r'(?m)^[ \t]*\*{4}[ \t]*$', '', s)
    return s

def _post_process_text_cleanup(markdown_text: str, legacy_form4 = False) -> str:
    """
    Final-stage clean-up for Markdown pulled from iXBRL/EDGAR filings.

    Returns a tidy Markdown string with:
      • mojibake fixed
      • hidden metadata lines removed
      • common word-splits repaired
      • normalised spacing & punctuation
    """
    if not markdown_text:
        return ""
        
    pattern = re.compile(r'(##BOLD_START_\d+##)(\s+)##BOLD_START_(\d+)##\((##BOLD_END_\3##)')

    replacement = r'\2\1('

    markdown_text = pattern.sub(replacement, markdown_text)

    pattern = re.compile(
        r'(##BOLD_START_(\d+)##)'
        r'##BOLD_START_(\d+)##'
        r'(\)?%|\))'
        r'##BOLD_END_\3##'
        r'##BOLD_END_\2##'
    )

    replacement = r'\1\4##BOLD_END_\2##'

    markdown_text = pattern.sub(replacement, markdown_text)

    markdown_text = markdown_text.replace("<sup></sup>", "").replace("##SUP####/SUP##", "").replace("syste m,", "system,")

    pattern = r"(##BOLD_START_\d+##)\n\n"
    
    replacement = r"\n\n\1"
    
    markdown_text = re.sub(pattern, replacement, markdown_text)

    pattern = re.compile(r'\s+(##(?:BOLD|ITALIC|U)_END_\d+##)\s+([,.:;!?])')
    markdown_text = pattern.sub(r'\1\2', markdown_text)

    markdown_text = re.sub(
        r"##BOLD_START_\d+##(?:##(?:BOLD|ITALIC)_(?:START|END)_\d+##)*(##BOLD_START_(\d+)##.*?##BOLD_END_\2##)(?:##(?:BOLD|ITALIC)_(?:START|END)_\d+##)*##BOLD_END_\d+##",
        r"\1", markdown_text, flags=re.DOTALL)

    markdown_text = re.sub(
        r"##ITALIC_START_\d+##(?:##(?:BOLD|ITALIC)_(?:START|END)_\d+##)*(##ITALIC_START_(\d+)##.*?##ITALIC_END_\2##)(?:##(?:BOLD|ITALIC)_(?:START|END)_\d+##)*##ITALIC_END_\d+##",
        r"\1", markdown_text, flags=re.DOTALL)
    
    markdown_text = re.sub(r"##BOLD_START_\d+##(##BOLD_START_\d+##)", r"\1", markdown_text)
    markdown_text = re.sub(r"(##BOLD_END_\d+##)##BOLD_END_\d+##", r"\1", markdown_text)

    markdown_text = re.sub(r"##ITALIC_START_\d+##(##ITALIC_START_\d+##)", r"\1", markdown_text)
    markdown_text = re.sub(r"(##ITALIC_END_\d+##)##ITALIC_END_\d+##", r"\1", markdown_text)
    
    markdown_text = markdown_text.replace("<sup> </sup>", " ").replace("<sup> ", " <sup>").replace(" </sup>", "</sup> ")

    markdown_text = re.sub(
        r'##NEWLINE##(?=(##BOLD_START_\d+##\)##BOLD_END_\d+##))',
        r'',
        markdown_text
    )

    markdown_text = merge_adjacent_italics(markdown_text)

    markdown_text = re.sub(r"##ITALIC_START_\d+##([○•●·◦➢])##ITALIC_END_\d+####ITALIC_START_(\d+)##", r"##ITALIC_START_\2##\1", markdown_text)

    pattern = r"(##BOLD_START_(\d+)####NEWLINE##)"
    replacement = r"##NEWLINE####BOLD_START_\2##"
                    
    markdown_text = markdown_text.replace("<sup>##NEWLINE##", "<sup>").replace("##NEWLINE##</sup>", "</sup>")
    
    _SWAP = re.compile(r'##NEWLINE##\s*((?:##(?:ITALIC|BOLD|U)_END_\d+##\s*)+)')

    markdown_text = _SWAP.sub(r'\1##NEWLINE## ', markdown_text)

    wrap_start = r'(?:##(?:ITALIC|BOLD)_START_\d+##)*'
    wrap_end   = r'(?:##(?:ITALIC|BOLD)_END_\d+##)*'

    roman_dot  = r'(?i:[ivxlcdm]+)\.'
    indent     = r'(?:##INDENT##)*'
    pair_paren = r'\([a-zA-Z]\)\([a-zA-Z]\)'

    marker_core = rf'(?:{pair_paren}|\*|\•|\d+\.\d[\d\.]*|\d+\.(?!\d)|\([a-zA-Z]\)|\((?i:[ivxlcdm]+)\)|\(\d+\)|{roman_dot}|[a-zA-Z]\.)'
    
    marker      = rf'{indent}{marker_core}'

    pattern = re.compile(
        rf'(?m)^({wrap_start}{marker}{wrap_end})[ \t]*\r?\n(?:[ \t]*\r?\n)*(?=\S)'
    )

    markdown_text = pattern.sub(r'\1 ', markdown_text)

    pattern = re.compile(
        r'##BOLD_END_(\d+)##(?: )?'
        r'<sup>##BOLD_START_\d+##'
        r'(.*?)'
        r'##BOLD_END_\d+##</sup>',
        re.DOTALL
    )

    markdown_text = pattern.sub(r'<sup>\2</sup>##BOLD_END_\1##', markdown_text)

    pattern = re.compile(
        r'##BOLD_END_(\d+)##(?: )?'
        r'##SUB####BOLD_START_\d+##'
        r'(.*?)'
        r'##BOLD_END_\d+####/SUB##',
        re.DOTALL
    )

    markdown_text = pattern.sub(r'##SUB##\2##/SUB####BOLD_END_\1##', markdown_text)

    markdown_text = re.sub(r'(##BOLD_START_\d+##•##BOLD_END_\d+##) (##BOLD_START_\d+##•##BOLD_END_\d+##)(\s?)', r'\1\n\n\2\3', markdown_text)

    markdown_text = re.sub(
        r'##BOLD_START_(\d+)##\s*\(\s*##BOLD_END_\1##\s*##BOLD_START_(\d+)##(.*?)##BOLD_END_\2##',
        r'##BOLD_START_\2##(\3##BOLD_END_\2##',
        markdown_text
    )
    
    markdown_text = re.sub(
        r'##BOLD_END_(\d+)##(?:##NEWLINE##\s*)?\s*##BOLD_START_\d+##(\s?(?:\)%|\)|%|,))(\s*)##BOLD_END_\d+##',
        r'\2\3##BOLD_END_\1##',
        markdown_text,
    )

    markdown_text = re.sub(r' (##(?:BOLD|U|ITALIC)_END_\d+##)', r'\1 ', markdown_text)
    
    markdown_text = re.sub(r'(?m)^((?:##INDENT##|&nbsp;|[ \t])*)([○•●·◦➢▪])(?=\S)', r'\1\2 ', markdown_text)

    markdown_text = re.sub(
        r'\.##BOLD_END_(\d+)####U_START',
        r'.##BOLD_END_\1## ##U_START',
        markdown_text
    )

    markdown_text = re.sub(r'##ITALIC_START_(\d+)####I_SPACE####ITALIC_END_\1##', ' ', markdown_text)
    markdown_text = re.sub(r'(##ITALIC_START_\d+##)(##I_SPACE##)', r'\2\1', markdown_text)
    markdown_text = re.sub(r'(##I_SPACE##)(##ITALIC_END_\d+##)', r'\2\1', markdown_text)

    markdown_text = re.sub(r'(\d{1,2}\.\d+[A-Z]?\.?)(##ITALIC_START_\d+##)', r'\1 \2', markdown_text)

    markdown_text = re.sub(
        r'((?:##(?:BOLD|ITALIC|U)_END_\d+##[^\S\r\n]*)+)(?=\S)',
        lambda m: re.sub(r'[^\S\r\n]+', '', m.group(1)) + (' ' if re.search(r'[^\S\r\n]', m.group(1)) else ''),
        markdown_text
    )

    markdown_text = re.sub(
        r'(##(?:BOLD|ITALIC|U)_START_\d+##)\s',
        r' \1',
        markdown_text
    )


    markdown_text = markdown_text.replace("\u00a0", " ")
    mojibake = {
        "â\x80\x94": "—",
        "â\x80\x93": "–",
        "â\x80\x99": "'",
        "â\x80\x98": "'",
        "â\x80\x9c": '"',
        "â\x80\x9d": '"',
        "â\x80¦": "...",
        "â�™": "'",
        "â�œ": '"',
        "â�d": '"',
        "â� ": '"',
        "â�”": "—",
        "â�“": "–",
        "â�‰": " ",
        "â�¦": "...",
        "”": '"',
        "“": '"',
        "’": "'",
        "‘": "'",
    }
    for bad, good in mojibake.items():
        markdown_text = markdown_text.replace(bad, good)
    

    markdown_text = clean_phone_numbers(markdown_text)


    junk_patterns = [
        re.compile(r'^\s*(?:<\??\s*)?xml\s+version\s*=\s*[\'"]\s*1\.0\s*[\'"].*$', re.IGNORECASE | re.MULTILINE),
        re.compile(
            r"^\s*\*{0,2}000\d{7}[^\n]*(?:Q[1-4]|FY|10-K|10-Q)[^\n]*false\*{0,2}\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        re.compile(r"^.*XBRL Document Created with.*$", re.MULTILINE),
        
        re.compile(r'^.*(?:Created by|Powered by|Unique Code|Generated At).*$', re.IGNORECASE | re.MULTILINE),

        re.compile(r"^false\d{4}FY\d+.*http://fasb\.org.*$", re.IGNORECASE | re.MULTILINE),
        re.compile(
            r"^(?!\s*\|).*\[(?:Member|Axis|Domain|Line Items|Abstract|Table|Text Block|"
            r"Policy Text Block|Extensible Enumeration|Flag|Roll Forward)\].*$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ]

    for pat in junk_patterns:
        markdown_text = pat.sub("", markdown_text)
    
    split_fixes = {
        r"##NEWLINE##": "<br>"
    }
    for bad_re, good in split_fixes.items():
        markdown_text = re.sub(bad_re, good, markdown_text, flags=re.IGNORECASE)

    markdown_text = re.sub(r'(<br>[ \t]*){2,}', '<br>', markdown_text, flags=re.IGNORECASE)


    table_pattern = re.compile(r'\n---\n\n(.*?)\n\n---\n', re.S)

    def remove_empty_tables_replacer(match):
        table_content = match.group(1)
        content_check = re.sub(r'[|\-:\s—–]', '', table_content)
        if not content_check:
            return ''
        return match.group(0)

    markdown_text = table_pattern.sub(remove_empty_tables_replacer, markdown_text)

    markdown_text = re.sub(
        r'(##BOLD_END_\d+##)'
        r'((?:<br>|##NEWLINE##|##COLSPAN_\d+##|\s)*)'
        r'(##BOLD_START_\d+##)'
        r'(\)?%|\))'
        r'(##BOLD_END_\d+##)',
        r' \4\1\2',
        markdown_text
    )

    markdown_text = re.sub(r"[ \t]+", " ", markdown_text)

    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text)
    markdown_text = re.sub(r"(?<![A-Za-z])([$�£�])(?![ \t]*\r?\n\r?\n)\s+(?![A-Za-z])", r"\1", markdown_text)
    markdown_text = re.sub(r"\(\s+", "(", markdown_text)
    markdown_text = re.sub(r"\s+\)", ")", markdown_text)
    markdown_text = re.sub(r"\s+([®™©])", r"\1", markdown_text)

    month_day_spacing_pattern = re.compile(
        r'\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(\d)',
        re.IGNORECASE
    )
    markdown_text = month_day_spacing_pattern.sub(r'\1 \2', markdown_text)

    date_comma_spacing_pattern = re.compile(r'(\d,)(\d{4})\b')
    markdown_text = date_comma_spacing_pattern.sub(r'\1 \2', markdown_text)

    markdown_text = markdown_text.replace("<br>)", ")").replace("<br>%", "%").replace("$<br>", "$").replace("##BOL<br>D", "##BOLD")

    link_pattern = re.compile(r'^\s*link\d+\s+".*?"\s*\n?', re.MULTILINE)
    
    markdown_text = link_pattern.sub("", markdown_text)

    markdown_text = markdown_text.replace("•<br>", "• ").replace("●<br>", "● ").replace("·<br>", "· ").replace("◦<br>", "◦ ").replace("➢<br>", "➢ ")

    BULLET_RUN_RE = re.compile(
        r'((?:^|(?:##INDENT##)+)\s*[○•●·◦➢▪])'
        r'(?:\s*(?:<br>\s*|\n\s*)){2,}',
        re.MULTILINE
    )

    markdown_text = BULLET_RUN_RE.sub(r'\1 ', markdown_text)

    markdown_text = _fix_paragraph_bold_runs(markdown_text)

    CHECK_RUN_RE = re.compile(
        r'([✓✔✘])'
        r'(?:\s*<br>\s*|\s*\n\s*)+'
    )
    markdown_text = CHECK_RUN_RE.sub(r'\1 ', markdown_text)

    markdown_text = re.sub(
        r'(##BOLD_START_\d+##TABLE OF CONTENTS##BOLD_END_\d+##)|\bTABLE OF CONTENTS\b',
        lambda m: m.group(1) or '**TABLE OF CONTENTS**',
        markdown_text,
    )
    
    markdown_text = markdown_text.replace("##SUP##", "<sup>").replace("##/SUP##", "</sup>").replace("##SUP##", "").replace("##/SUP##", "")
    markdown_text = markdown_text.replace("##SUB##", "<sub>").replace("##/SUB##", "</sub>").replace("##SUB##", "").replace("##/SUB##", "")


    markdown_text = re.sub(r'##BOLD_START_\d+##(?=\s*<br>##BOLD_START)', '', markdown_text)

    markdown_text = re.sub(r'(##BOLD_END_\d+##)(##BOLD_START_\d+##)', r'\1 \2', markdown_text)

    markdown_text = re.sub(r'(\d+)\s+(%##BOLD_END_)', r'\1\2', markdown_text)

    markdown_text = re.sub(r'(##BOLD_START_\d+##)\s+', r' \1', markdown_text)
    markdown_text = re.sub(r'\s+(##BOLD_END_\d+##)', r'\1', markdown_text)
    markdown_text = collapse_redundant_bold_placeholders(markdown_text)

    markdown_text = re.sub(r'(?<=\S)##BOLD_START_\d+##', r'**', markdown_text)
    markdown_text = re.sub(r'##BOLD_START_\d+##', r'**', markdown_text)

    markdown_text = re.sub(r'##BOLD_END_\d+##(?=\S)', r'**', markdown_text)
    markdown_text = re.sub(r'##BOLD_END_\d+##', r'**', markdown_text)

    markdown_text = re.sub(r'(##ITALIC_START_\d+##)\s+', r'\1', markdown_text)
    markdown_text = re.sub(r'\s+(##ITALIC_END_\d+##)', r'\1', markdown_text)
    markdown_text = re.sub(r'(?<=\S)##ITALIC_START_\d+##', r'*', markdown_text)
    markdown_text = re.sub(r'##ITALIC_START_\d+##', r'*', markdown_text)
    markdown_text = re.sub(r'##ITALIC_END_\d+##(?=\S)', r'*', markdown_text)
    markdown_text = re.sub(r'##ITALIC_END_\d+##', r'*', markdown_text)

    markdown_text = merge_bracket_fragmented_underlines(markdown_text)

    markdown_text = re.sub(r'(##U_START_\d+##)\s+', r'\1', markdown_text)
    markdown_text = re.sub(r'\s+(##U_END_\d+##)', r'\1', markdown_text)
    markdown_text = re.sub(r'(?<=\S)##U_START_\d+##', r'<u>', markdown_text)
    markdown_text = re.sub(r'##U_START_\d+##', r'<u>', markdown_text)
    markdown_text = re.sub(r'##U_END_\d+##(?=\S)', r'</u>', markdown_text)
    markdown_text = re.sub(r'##U_END_\d+##', r'</u>', markdown_text)
    markdown_text = _restore_markdown_links(markdown_text)

    markdown_text = markdown_text.replace("$ **", "**$").replace("** <br> **)**", ")**")

    pattern = r'^(Table of Contents)\s*•\s*(.+)$'
    replacement = r'\1\n\n• \2'

    markdown_text = re.sub(pattern,
                        replacement,
                        markdown_text,
                        flags=re.IGNORECASE | re.MULTILINE)
    
    markdown_text = re.sub(r'(?<!\*)\bTable of Contents\b(?!\*)', '**Table of Contents**', markdown_text)
    markdown_text = markdown_text.replace("****Table of Contents****", "**Table of Contents**")

    markdown_text = markdown_text.replace(",**\n\n**", ", ")

    markdown_text = markdown_text.replace("_ <u>", "_<u>").replace("</u> _", "</u>_")

    pattern = r'Unnamed:\s*\d+(?:_level_\d+)?\s'
    markdown_text = re.sub(pattern, '', markdown_text)

    markdown_text = re.sub(r"<BORDER>\.\d+", "", markdown_text)
    markdown_text = re.sub(r"<br>\.\d+(?![%\d])", "", markdown_text)

    markdown_text = markdown_text.replace("<BORDER>", "")

    if legacy_form4:
        bad_table2_row2 = "| 1. Title of Derivative Security<br> (Instr. 3)<br> | 2. Conver-<br> sion or<br> Exercise<br> Price of<br> Deri-<br> vative<br> Security<br> | 3. Transaction Date<br>(Month/<br>Day/<br>Year)<br> | 3A. Deemed Execution Date, if any <br>(Month/<br>Day/<br>Year)<br> | Code<br> | V<br> A<br> D DE<br> ED<br> Title<br> Amount or Number of Shares<br>| 8. Price<br> of<br> Derivative<br> Security<br> (Instr.5)<br> | 9. Number of<br> Derivative<br> Securities<br> Beneficially<br> Owned<br> Following<br> Reported<br> Transaction(s)<br> (Instr.4) | 10. Owner-<br>ship<br>Form of<br>Deriv-<br>ative<br>Securities:<br>Direct (D)<br>or<br>Indirect (I)<br>(Instr.4) | 11. Nature of<br> Indirect<br> Beneficial<br> Ownership<br> (Instr.4) | | | | | |"
        fixed_table2_row2 = "| 1. Title of Derivative Security<br> (Instr. 3)<br> | 2. Conver-<br> sion or<br> Exercise<br> Price of<br> Deri-<br> vative<br> Security<br> | 3. Transaction Date<br>(Month/<br>Day/<br>Year)<br> | 3A. Deemed Execution Date, if any <br>(Month/<br>Day/<br>Year)<br> | Code<br> | V | A | D | DE | ED | Title | Amount or Number of Shares | 8. Price<br> of<br> Derivative<br> Security<br> (Instr.5)<br> | 9. Number of<br> Derivative<br> Securities<br> Beneficially<br> Owned<br> Following<br> Reported<br> Transaction(s)<br> (Instr.4) | 10. Owner-<br>ship<br>Form of<br>Deriv-<br>ative<br>Securities:<br>Direct (D)<br>or<br>Indirect (I)<br>(Instr.4) | 11. Nature of<br> Indirect<br> Beneficial<br> Ownership<br> (Instr.4) |"
        markdown_text = markdown_text.replace(bad_table2_row2, fixed_table2_row2)

    markdown_text = _convert_bullet_tables_to_lists(markdown_text)

    footnote_spacing_pattern = re.compile(
        r'(?<=[^\n])'
        r'\n'
        r'(<sup>\d+</sup>.*)', 
        re.MULTILINE
    )
    
    markdown_text = footnote_spacing_pattern.sub(r'\n\n\1', markdown_text)

    markdown_text = markdown_text.replace("**\n•", "**\n\n•").replace("** 1. ", "**\n\n1. ")
    markdown_text = markdown_text.replace(" ##COLSPAN", "##COLSPAN").replace(" ##ROWSPAN", "##ROWSPAN")
    markdown_text = markdown_text.replace("</su<br>p>", "</sup><br>").replace("<sup> ", "<sup>").replace(" </sup>", "</sup>").replace("0.%", "0.0%").replace("<BORD<br>ER>", "")
    markdown_text = re.sub(r'SPAN##[1-9](?!\d)', 'SPAN##', markdown_text)
    markdown_text = re.sub(
        r'(?m)^QuickLinks(?: -- Click here to rapidly navigate through this document)?\r?\n\n',
        '',
        markdown_text
    )
    markdown_text = _remove_page_numbers(markdown_text)
    markdown_text = re.sub(r'(?m)^[ \t]*<sup>(?:100|[1-9]?\d)</sup>\s+QuickLinks[ \t]*\r?$', '', markdown_text)
    markdown_text = markdown_text.replace(' " *', ' "*').replace('* " ', '*" ').replace(' (" *', ' ()"*').replace('* ") ', '*") ').replace("**(** ", "**(**")
    markdown_text = markdown_text.replace("##TRIPLE_ASTERISK##", "\\*\\*\\*")
    markdown_text = markdown_text.replace("##DOUBLE_ASTERISK##", "\\*\\*")
    markdown_text = markdown_text.replace("##SINGLE_ASTERISK##", "\\*")


    markdown_text = re.sub(
        r'(?m)^((?:&nbsp;|[ \t])*(?:(?:\*\*(?:(?i:section)\s+[1-9]\d?(?:\.\d+)*\.|[1-9]\d?(?:\.\d+)*\.|[a-z]\.|[A-Za-z]\))\*\*)|(?:(?i:section)\s+[1-9]\d?(?:\.\d+)*\.|[1-9]\d?(?:\.\d+)*\.|[a-z]\.|[A-Za-z]\))))(?=(?:\*\*)?(?:<u>|[A-Z]|["“]))',
        r'\1 ',
        markdown_text,
    )
    markdown_text = re.sub(
        r'(?m)^((?:##INDENT##|&nbsp;|[ \t])*(?:\([A-Za-z]\)|\((?i:[ivxlcdm]+)\)|\([1-9]\d?\)))(?=(?:\*\*)?(?:<u>|[A-Za-z0-9*]|["“]))',
        r'\1&nbsp;&nbsp;&nbsp;&nbsp;',
        markdown_text,
    )
    markdown_text = re.sub(
        r'\b([Rr]ule\s+\d+[A-Za-z0-9.-]*)\s+\(([A-Za-z0-9ivxlcdm]+)\)',
        r'\1(\2)',
        markdown_text,
    )
    markdown_text = re.sub(
        r'\b([Rr]ule\s+\d+[A-Za-z0-9.-]*\([A-Za-z0-9ivxlcdm]+\)\s*[–-]\s*\d+[A-Za-z0-9.-]*)\s+\(([A-Za-z0-9ivxlcdm]+)\)',
        r'\1(\2)',
        markdown_text,
    )


    markdown_text = re.sub(r'(<sup>\s*\d+\s*</sup>)\s*<br\s*/?>\s+(?=[A-Za-z0-9])', r'\1 ', markdown_text, flags=re.I)
    markdown_text = re.sub(
        r'<sup>\s*(\((?:\d+|[A-Za-z]+))\s*</sup>\s*<br\s*/?>\s*<sup>\s*(\))\s*</sup>',
        r'<sup>\1\2</sup>',
        markdown_text,
        flags=re.I,
    )

    pattern = r'(?m)^((?:##INDENT##|&nbsp;|[ \t])*)([○•●·◦➢▪])(?=\S)'

    markdown_text = re.sub(pattern, r'\1\2 ', markdown_text)
    markdown_text = re.sub(r'(?:\n\n------){2,}', '\n\n------', markdown_text)

    pattern = r'([○•●·◦➢])\s*<br\s*/?>\s*'
    markdown_text = re.sub(pattern, r'\1 ', markdown_text)

    markdown_text = markdown_text.replace("<u> </u>", " ").replace("<u></u>", "")

    markdown_text = re.sub(r"<u>##COLSPAN_\d+##</u>", "", markdown_text)
    markdown_text = re.sub(r'(?m)^(?:##INDENT##|&nbsp;|[ \t])+(?:<br\s*/?>)?[ \t]*$', '', markdown_text)
    markdown_text = re.sub(r'\*\*((?:&nbsp;|[ \t])+)\*\*', r'\1', markdown_text)
    markdown_text = re.sub(r'[ \t]+(?=\r?\n)', '', markdown_text)
    markdown_text = re.sub(r'\n{3,}', '\n\n', markdown_text)
    markdown_text = normalize_malformed_markdown_emphasis(markdown_text)

    pattern = r'(?m)^(<sup>\(\d+\)</sup>)(?!\s)(\S)'
    markdown_text = re.sub(pattern, r'\1 \2', markdown_text)

    markdown_text = apply_markdown_hardcodes(markdown_text)

    return markdown_text.strip()

__all__ = [name for name in globals() if not name.startswith("__")]
