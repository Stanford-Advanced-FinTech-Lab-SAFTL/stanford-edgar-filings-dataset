from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.parsers.ocr.rotate_auth import _has_mistral_api_keys, _mistral_no_keys_message
from stanford_edgar_parser.parsers.ocr.ocr_utils import _process_pdf_bytes_with_fallback
from stanford_edgar_parser.utils.bootstrap import (
    BeautifulSoup,
    Config,
    NavigableString,
    Tag,
    _load_sec_parser_env,
    defaultdict,
    io,
    math,
    pd,
    quote,
    re,
    sync_playwright,
    time,
    traceback,
    unquote,
    urljoin,
)
from stanford_edgar_parser.utils.parse_stats import _log_current_filing_ocr

def fix_inverted_bold_paragraphs(soup: BeautifulSoup):
    """
    Finds malformed `<b><p>...</p></b>` structures and corrects them to the
    standard `<p><b>...</b></p>` format, which can be processed correctly by
    downstream logic.
    """
    for b_tag in soup.find_all(['b', 'strong']):
        p_tag = b_tag.find('p')
        
        if p_tag and b_tag.get_text(strip=True) == p_tag.get_text(strip=True):
            content = p_tag.get_text(separator=' ', strip=True)
            
            new_p = soup.new_tag('p')
            new_b = soup.new_tag('b')
            new_b.string = content
            new_p.append(new_b)
            
            b_tag.replace_with(new_p)

def promote_bold_subheads(soup, max_words=15, max_len=120):
    """
    Finds block elements (p, div) that contain only a single bold element
    and promotes the block to a <h4> tag. This version consolidates the
    text to prevent downstream parsing errors.
    """
    for block_tag in soup.find_all(['p', 'div']):
        child_tags = block_tag.find_all(True, recursive=False)
        
        if len(child_tags) == 1 and child_tags[0].name in ['b', 'strong'] and \
           block_tag.get_text(strip=True) == child_tags[0].get_text(strip=True):
            
            bold_child = child_tags[0]
            
            text_for_checking = bold_child.get_text(strip=True)
            if 1 < len(text_for_checking) <= max_len and len(text_for_checking.split()) <= max_words:
                
                final_text = bold_child.get_text(separator=' ', strip=True)

                h4 = soup.new_tag('h4')
                h4.string = final_text
                
                block_tag.replace_with(h4)
                
WS_RE = re.compile(r'^[\s\u00A0\u2063]+|[\s\u00A0\u2063]+$')

def process_inline_tags(soup, tags, placeholder_prefix):
    """
    A non-destructive function that finds all specified HTML tags (e.g., ['b', 'strong'])
    and wraps them with unique text-based placeholders without destroying nested tags.
    This version also cleans newlines and <br> tags from within the tag's content.

    Args:
        soup: The BeautifulSoup object to modify.
        tags: A list of tag names to process (e.g., ['b', 'strong']).
        placeholder_prefix: The prefix for the placeholder (e.g., "BOLD", "ITALIC").
    """
    found_tags = list(soup.find_all(tags))

    for i, tag in enumerate(found_tags):
        if not tag.parent:
            continue

        if not tag.get_text(strip=True):
            tag.decompose()
            continue

        is_in_table = tag.find_parent('table')

        for descendant in list(tag.descendants):
            if descendant.name == 'br':
                if is_in_table:
                    descendant.replace_with(NavigableString('##NEWLINE##'))
                else:
                    descendant.replace_with(NavigableString(' '))
            elif isinstance(descendant, NavigableString):
                cleaned_text = str(descendant).replace('\n', ' ')
                descendant.replace_with(NavigableString(cleaned_text))

        start_placeholder = f"##{placeholder_prefix}_START_{i}##"
        end_placeholder = f"##{placeholder_prefix}_END_{i}##"

        tag.insert_before(NavigableString(start_placeholder))
        tag.insert_after(NavigableString(end_placeholder))

        tag.unwrap()


def process_anchor_tags(soup):
    """
    Preserve anchor href targets in text form so they survive read_html/table parsing.
    Anchors are restored to markdown links in _post_process_text_cleanup.
    Internal fragment-only anchors are unwrapped because SEC filings often use
    long generated IDs for table-of-contents links that add noise to training text.
    """
    found_tags = list(soup.find_all('a'))
    base_tag = soup.find('base', href=True)
    base_href = (base_tag.get('href') or '').strip() if base_tag else ''
    base_url = base_href or _state.CURRENT_SOURCE_DOCUMENT_URL

    for i, tag in enumerate(found_tags):
        if not tag.parent:
            continue

        href = (tag.get('href') or '').strip()
        if href.startswith('#'):
            tag.unwrap()
            continue
        if (
            href
            and base_url
            and not re.match(r'^[a-z][a-z0-9+.-]*:', href, flags=re.IGNORECASE)
        ):
            href = urljoin(base_url, href)
        if not tag.get_text(strip=True):
            tag.decompose()
            continue

        if not href or href.lower().startswith('javascript:'):
            tag.unwrap()
            continue

        is_in_table = tag.find_parent('table')

        for descendant in list(tag.descendants):
            if descendant.name == 'br':
                if is_in_table:
                    descendant.replace_with(NavigableString('##NEWLINE##'))
                else:
                    descendant.replace_with(NavigableString(' '))
            elif isinstance(descendant, NavigableString):
                cleaned_text = str(descendant).replace('\n', ' ')
                descendant.replace_with(NavigableString(cleaned_text))

        encoded_href = quote(href, safe="/:?&=%._-")
        start_placeholder = f"##LINK_START_{i}__{encoded_href}##"
        end_placeholder = f"##LINK_END_{i}##"

        tag.insert_before(NavigableString(start_placeholder))
        tag.insert_after(NavigableString(end_placeholder))
        tag.unwrap()


def _escape_markdown_link_label(label: str) -> str:
    return label.replace('\\', '\\\\').replace('[', r'\[').replace(']', r'\]')


def _repair_markdown_link_label_spacing(label: str) -> str:
    """Restore prose spacing lost when adjacent inline tags are concatenated."""
    label = re.sub(r'(?<=[A-Za-z0-9])(?=\([A-Za-z])', ' ', label)
    label = re.sub(r'(?<=\))(?=\([A-Za-z])', ' ', label)
    return label


def _link_text_joiner(left: str, separator: str, right: str) -> str:
    if separator:
        return separator
    if not left or not right:
        return ""

    left_char = left[-1]
    right_char = right[0]

    if left_char.isspace() or right_char.isspace():
        return ""
    if left_char.isdigit() and right_char.isdigit():
        return ""
    if left_char in "([{/":
        return ""
    if right_char == "(" and len(right) > 1 and right[1].isalpha():
        return " "
    if right_char in ".,;:)]}%/":
        return ""
    if left_char == "-" or right_char == "-":
        return ""
    if left_char.isalnum() and right_char.isalnum():
        return " "
    if left_char in ".,;:" and right_char.isalnum():
        return " "
    return ""


def _coalesce_adjacent_markdown_links(text: str) -> str:
    link_pair_pattern = re.compile(
        r'\[([^\]\n]*)\]\(([^()\n]+)\)([ \t]*)\[([^\]\n]*)\]\(\2\)'
    )

    def _replace(match: re.Match) -> str:
        left = match.group(1)
        href = match.group(2)
        separator = match.group(3)
        right = match.group(4)
        joiner = _link_text_joiner(left, separator, right)
        return f'[{left}{joiner}{right}]({href})'

    previous = None
    while text != previous:
        previous = text
        text = link_pair_pattern.sub(_replace, text)
    return text


def _restore_markdown_links(text: str) -> str:
    """
    Restore anchor placeholders to markdown links after inline formatting placeholders
    have already been converted back into markdown/HTML markup.
    """
    link_pattern = re.compile(
        r'##LINK_START_(\d+)__([^#]+)##(.*?)##LINK_END_\1##',
        re.DOTALL,
    )

    def _replace(match: re.Match) -> str:
        href = unquote(match.group(2))
        raw_label = match.group(3)
        if not raw_label.strip():
            return ''
        leading_ws = raw_label[:len(raw_label) - len(raw_label.lstrip())]
        trailing_ws = raw_label[len(raw_label.rstrip()):]
        label = _repair_markdown_link_label_spacing(raw_label.strip())
        label = _escape_markdown_link_label(label)
        return f'{leading_ws}[{label}]({href}){trailing_ws}'

    previous = None
    while text != previous:
        previous = text
        text = link_pattern.sub(_replace, text)
    return _coalesce_adjacent_markdown_links(text)

def _unsplit_numbers(text: str) -> str:
    """
    Collapse separators inside a number.
    '1 234' → '1234'     '1,234' → '1234'     '($ 1 234)' → '($1234)'
    """
    month_pattern = re.compile(
        r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|'
        r'May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|'
        r'Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b',
        re.IGNORECASE
    )

    def contains_month(s: str) -> bool:
        return bool(month_pattern.search(s))
    
    if isinstance(text, str) and contains_month(text):
        return text
    if isinstance(text, str):
        return re.sub(r'(?<=\d)[\s,]+(?=\d)', '', text)
    return text

def handle_list_like_table_with_indentation(table_element, output_list: list) -> bool:
    """
    Identifies tables used for indented lists. It uses a hybrid approach:
    1.  Calculates precise indentation from absolute CSS widths (pt, px) first.
    2.  If absolute widths are not found, it checks for a percentage-based 'width'
        attribute and applies a heuristic (level = width / 2) as a fallback.
    """
    STANDARD_INDENT_PT = 18.0
    LIST_MARKER_RE_TABLE = re.compile(
        r"""^\s*
        (?:
            [o□☒⌧♦⧫†‡-○•●·◦➢▪]          # Bullet characters
            |
            \((?:[a-z0-9ivxlcdm]+)\)  # Markers in parentheses, e.g., (a), (i), (1)
            |
            [a-z][\.\)]              # Markers with a period or parenthesis, e.g., a., a)
            |
            \d+\.                    # Numeric markers, e.g., 1.
            |
            [ivxlcdm]+\.              # Roman numeral markers, e.g., i., iv.
            |
            \d+\.\d[\d\.]*            # Multi-level numeric markers like 1.1 or 1.2.3
        )
        \s*$""",
        re.IGNORECASE | re.VERBOSE
    )

    rows = table_element.find_all('tr', recursive=False)
    if not rows:
        return False

    processed_items = []
    is_consistent_list_table = True

    for row in rows:
        if not row.get_text(strip=True):
            continue

        cells = row.find_all(['td', 'th'], recursive=False)

        first_content_cell = None
        first_content_cell_index = -1
        for i, cell in enumerate(cells):
            if cell.get_text(strip=True):
                first_content_cell = cell
                first_content_cell_index = i
                break

        if not first_content_cell:
            continue

        raw_cell_text = first_content_cell.get_text(strip=True)
        normalized_text = re.sub(r'##((?:BOLD|ITALIC|U)_(?:START|END)_\d+|(?:COLSPAN)_\d+)##', '', raw_cell_text)

        if not LIST_MARKER_RE_TABLE.fullmatch(normalized_text):
            is_consistent_list_table = False
            break

        marker_cell = first_content_cell
        marker_cell_index = first_content_cell_index
        
        content_fragments = []
        for i in range(marker_cell_index + 1, len(cells)):
            cell_text = cells[i].get_text(separator=' ', strip=True)
            if cell_text:
                content_fragments.append(cell_text)

        if not content_fragments:
            continue

        content_text = ' '.join(content_fragments)

        total_indent_pt = 0.0
        percentage_level = 0
        conversions = {'in': 72.0, 'pt': 1.0, 'px': 0.75}

        for i in range(marker_cell_index):
            spacer_cell = cells[i]
            if spacer_cell.get_text(strip=True):
                continue

            style_attr = spacer_cell.get('style', '')
            width_attr = spacer_cell.get('width', '')
            found_absolute_width = False

            for attr_str in [style_attr, width_attr]:
                abs_match = re.search(r'width\s*[:=]?\s*"?([\d\.]+)(in|pt|px)"?', attr_str, re.I)
                if abs_match:
                    try:
                        value = float(abs_match.group(1))
                        unit = (abs_match.group(2) or 'pt').lower()
                        total_indent_pt += value * conversions.get(unit, 1.0)
                        found_absolute_width = True
                        break
                    except (ValueError, TypeError):
                        pass
            if found_absolute_width:
                continue

            if spacer_cell.has_attr('width'):
                pct_match = re.search(r'([\d\.]+)%', spacer_cell['width'])
                if pct_match:
                    try:
                        pct_value = float(pct_match.group(1))
                        percentage_level += int(pct_value / 2)
                    except (ValueError, TypeError):
                        pass

        marker_indent_info = _calculate_effective_indent(marker_cell)
        total_indent_pt += marker_indent_info['indent']

        marker_width_attr = marker_cell.get('width', '') + marker_cell.get('style', '')
        marker_width_match = re.search(r'width\s*[:=]?\s*"?([\d\.]+)(pt|px)"?', marker_width_attr, re.I)
        if marker_width_match:
            try:
                value = float(marker_width_match.group(1))
                unit = (marker_width_match.group(2) or 'pt').lower()
                total_indent_pt += value * conversions.get(unit, 1.0)
            except (ValueError, TypeError):
                pass

        level = int(round(total_indent_pt / STANDARD_INDENT_PT))

        if level == 0 and percentage_level > 0:
            if pct_value >= 8:
                level = 3
            elif pct_value == 5:
                level = 2.5
            elif pct_value >= 4:
                level = 2
            elif pct_value == 3:
                level = 1.5
            elif pct_value > 0:
                level = 1

        marker_text = marker_cell.get_text(strip=True)
        n = max(0, math.floor(level))
        indent_prefix = "##INDENT##" * n + ('&nbsp;&nbsp;' if math.isclose(level - n, 0.5, abs_tol=1e-9) else '')
        
        full_line = f"{indent_prefix}{marker_text} {content_text}"
        processed_items.append(full_line)

    if is_consistent_list_table and processed_items:
        output_list.append("\n\n".join(processed_items) + "\n\n")
        return True

    return False
    
def defragment_bolds(soup: BeautifulSoup):
    """
    Finds and merges adjacent <b> or <strong> tags to fix fragmentation.
    For example, turns <b>Hello</b> <b>World</b> into <b>Hello World</b>.
    """
    for b_tag in soup.find_all(['b', 'strong']):
        while True:
            next_tag = b_tag.next_sibling
            
            if isinstance(next_tag, NavigableString) and next_tag.strip() == '':
                real_next_tag = next_tag.next_sibling
            else:
                real_next_tag = next_tag

            if real_next_tag and real_next_tag.name in ['b', 'strong']:
                if isinstance(next_tag, NavigableString) and next_tag.strip() == '':
                    b_tag.append(" ")
                
                b_tag.extend(real_next_tag.contents)
                
                real_next_tag.decompose()
                if isinstance(next_tag, NavigableString):
                    next_tag.extract()
            else:
                break

_INVISIBLE = re.compile(
    r'\b(?:none|hidden|0(?:px|pt|em|rem)?)\b|'
    r'\btransparent\b|rgba?\([^)]*,\s*0(?:\.0+)?\)|hsla?\([^)]*,\s*0(?:\.0+)?\)',
    re.I
)

def has_visible_border(style: str, side: str) -> bool:
    s = (style or '').lower()

    m = re.search(rf'border-{side}\s*:\s*([^;]+)', s)
    if m:
        return not _INVISIBLE.search(m.group(1))

    for prop in (f'border-{side}-width', f'border-{side}-style', f'border-{side}-color'):
        m = re.search(rf'{prop}\s*:\s*([^;]+)', s)
        if m and _INVISIBLE.search(m.group(1)):
            return False

    m = re.search(r'border\s*:\s*([^;]+)', s)
    if m:
        return not _INVISIBLE.search(m.group(1))

    return False

def tag_border_cells(table_element, soup):
    """
    Finds cells with top or bottom borders and tags them with distinct
    sentinels. It now also correctly handles <hr> tags by replacing
    them with line breaks.
    """
    for cell in table_element.find_all(['td', 'th']):
        style = cell.get('style', '').lower()
        hr_tags = cell.find_all('hr')

        if hr_tags:
            cell.append('<BORDER>')
            for hr in hr_tags:
                hr.replace_with(soup.new_tag('br'))

        if 'border-bottom: medium none' in style:
            continue
        if has_visible_border(style, 'bottom') and not hr_tags:
            cell.append('<BORDER>')
        if has_visible_border(style, 'top'):
            cell.append('<BORDER_TOP>')

BULLET_CHARS = {'○', '•', '●', '·', '◦', '➢', '▪'}

def merge_bullet_head_fragments(soup: BeautifulSoup) -> None:
    """
    If a block element contains only a bullet glyph and the *next*
    block starts with <b> / <strong>, merge them so that the bullet
    and the bold text end up in the same paragraph:

        <p>•</p><p><b>We …</b></p>   →   <p>• <b>We …</b></p>
    """
    for bullet_blk in soup.find_all(['p', 'div']):
        txt = bullet_blk.get_text(strip=True)
        if txt not in BULLET_CHARS:
            continue

        nxt = bullet_blk.find_next_sibling(lambda t: (
            t.name in {'p', 'div'} and t.get_text(strip=True)
        ))
        if not nxt:
            continue

        first_child = nxt.find(True, recursive=False)
        if first_child and first_child.name in {'b', 'strong'}:
            bullet_blk.string = bullet_blk.string or bullet_blk.new_string(txt)
            bullet_blk.append(" ")
            for node in list(nxt.contents):
                bullet_blk.append(node.extract())
            nxt.decompose()

def convert_styled_superscripts_to_placeholders(soup: BeautifulSoup):
    """
    Finds elements styled as superscripts and replaces them with a unique
    text-based placeholder. This version is safer and avoids converting large
    container elements.
    """
    vertical_offset_re = re.compile(
        r'(bottom|top)\s*:\s*(-?[\d.]+)(?:pt|px|em)', 
        re.IGNORECASE
    )

    for tag in soup.find_all(style=True):
        if tag.name not in ['span', 'font', 'p', 'i', 'b', 'strong', 'em', 'u']:
            continue
        
        style_attr = tag.get('style', '').lower().replace(' ', '')
        
        if 'position:relative' not in style_attr:
            continue

        match = vertical_offset_re.search(style_attr)
        if not match:
            continue

        prop, value_str = match.groups()
        value = float(value_str)

        is_styled_as_superscript = (prop == 'bottom' and value > 0) or \
                                   (prop == 'top' and value < 0)

        if is_styled_as_superscript:
            text_content = tag.get_text(strip=False)
            if text_content and len(text_content) < 50:
                placeholder = f"##SUP##{text_content}##/SUP##"
                tag.replace_with(NavigableString(placeholder))

def promote_styled_headings(soup: BeautifulSoup):
    """
    Finds block elements (div, p) that are visually styled as headings
    based on font-size, font-weight, and other CSS attributes, and
    replaces them with standard h1, h2, etc., tags.
    """
    font_size_re = re.compile(r'font-size\s*:\s*([\d\.]+)pt', re.IGNORECASE)

    for tag in soup.find_all(['div', 'p']):
        if tag.find_parent(['td', 'th']):
            continue
        style = tag.get('style', '').replace(' ', '').lower()
        if not style:
            continue

        font_size_match = font_size_re.search(style)
        font_size = float(font_size_match.group(1)) if font_size_match else 0

        is_bold = 'font-weight:bold' in style or 'font-weight:700' in style
        is_uppercase = 'text-transform:uppercase' in style

        heading_level = 0

        if font_size >= 20:
            heading_level = 2
        elif font_size > 16 and is_uppercase:
            heading_level = 2
        elif font_size >= 14 and is_bold:
            heading_level = 3
        elif font_size >= 12 and is_bold and is_uppercase:
             heading_level = 4
        elif is_bold:
            text_for_checking = tag.get_text(strip=True)
            if text_for_checking and len(text_for_checking.split()) < 25:
                heading_level = 4

        if heading_level > 0:
            if tag.find('table'):
                continue
            text_content = tag.get_text(separator=' ', strip=True)
            if text_content:
                new_heading_tag = soup.new_tag(f'h{heading_level}')
                new_heading_tag.string = text_content
                
                tag.replace_with(new_heading_tag)

def handle_sentence_fragment_table(table_element, output_list: list) -> bool:
    """
    Identifies tables used primarily for laying out sentence fragments (e.g.,
    fill-in-the-blank forms) and converts them into a single, flowing paragraph
    of Markdown instead of a multi-column table.
    
    Args:
        table_element: The BeautifulSoup object for the <table>.
        output_list: The list where Markdown chunks are being appended.

    Returns:
        True if the table was handled as a sentence fragment, False otherwise.
    """
    rows = table_element.find_all('tr', recursive=False)

    if not rows or len(rows) > 3:
        return False

    if len(rows) == 1:
        cells = rows[0].find_all(['td', 'th'], recursive=False)
        if len(cells) == 2:
            first_cell_text = cells[0].get_text(strip=True)
            if re.fullmatch(r'\(\s*\d+\s*\)', first_cell_text):
                return False
        
    if table_element.find('th'):
        return False

    total_text = table_element.get_text(strip=True)
    
    financial_indicators_re = re.compile(r'[$£�%]|Amount|Total|Percent|Instruction|Vote|/s/|Abstained|pence|Name of Witness|owned by|\b\([a-z]\)\b|\!\[|##BOLD_START', re.IGNORECASE)
    if financial_indicators_re.search(total_text):
        return False

    if len(total_text) < 10 or len(total_text) > 300:
        return False
        
    if len(rows) == 1 and len(rows[0].find_all(['td', 'th'], recursive=False)) <= 1:
        return False
    
    output_lines = []
    for row in rows:
        row_fragments = []
        for cell in row.find_all(['td', 'th']):
            cell_text = cell.get_text(separator='', strip=False)
            if not cell_text:
                continue
            
            style = cell.get('style', '').lower()
            if 'border-bottom' in style:
                row_fragments.append(f"<u>{cell_text}</u>")
            else:
                row_fragments.append(cell_text)
        
        line_text = ' '.join(row_fragments)
        if line_text:
            output_lines.append(line_text)

    if output_lines:
        full_paragraph = ' <br> '.join(output_lines)
        full_paragraph = re.sub(r'\s+', ' ', full_paragraph).strip()
        
        output_list.append(full_paragraph + "\n\n")
        return True

    return False

def parse_sec_header(raw_text: str) -> str:
    """
    Parses the <SEC-HEADER> block of a filing into structured Markdown.
    This version correctly handles multi-line, indented address blocks.
    """
    if not raw_text:
        return ""

    output = ["## Filing Summary"]
    
    raw_text = raw_text.replace('\r\n', '\n').strip()
    raw_text = re.sub(r'<\/?SEC-HEADER.*?>', '', raw_text).strip()
    lines = raw_text.split('\n')
    
    current_section = ""
    in_address_block = False

    for line in lines:
        line = line.rstrip()
        
        if not line.strip() or line.strip().startswith('<'):
            continue

        if not line.startswith('\t') and not line.startswith('    ') and ':' in line:
            in_address_block = False
            key, value = [s.strip() for s in line.split(':', 1)]
            output.append(f"**{key}**: {value}\n")
            current_section = key
            if "ADDRESS" in current_section.upper():
                in_address_block = True

        elif line.startswith('\t') or line.startswith('    '):
            key, value = [s.strip() for s in line.split(':', 1)]
            
            if in_address_block:
                output.append(f"- **{key}:** {value}")
            else:
                 if value:
                    output.append(f"- **{key}:** {value}")
                 else:
                    output.append(f"\n**{key}:**")
        
        elif not line.startswith('\t') and ':' not in line:
            in_address_block = False
            section_title = line.strip()
            if section_title:
                output.append(f"\n### {section_title.title()}")
                current_section = section_title
                if "ADDRESS" in current_section.upper():
                    in_address_block = True

    return "\n".join(output)

def parse_ims_header(raw_text: str) -> str:
    """
    Parses the <IMS-HEADER> block from a legacy filing into structured Markdown.
    """
    header_match = re.search(r"<IMS-HEADER>(.*?)</IMS-HEADER>", raw_text, re.S | re.I)
    if not header_match:
        return ""

    output = ["## Filing Summary"]
    content = header_match.group(1).strip()
    lines = content.split('\n')

    in_block = False
    for line in lines:
        line = line.rstrip()
        if not line.strip():
            continue

        if not line.startswith('\t') and ':' in line:
            key, value = [s.strip() for s in line.split(':', 1)]
            if value:
                output.append(f"**{key}**: {value}")
            else:
                output.append(f"\n### {key.title()}")
            in_block = False
        elif line.startswith('\t') and ':' in line:
            key, value = [s.strip() for s in line.split(':', 1)]
            output.append(f"- **{key}:** {value}")
        elif not line.startswith('\t') and line.strip().endswith(':'):
            output.append(f"\n### {line.strip().title()}")
            in_block = True
    
    return "\n\n".join(output)

def parse_nsar_b_txt(raw_text: str) -> str:
    """
    Parses both legacy single-series and multi-series plain text Form NSAR-B
    filings into structured Markdown. This robust version decodes the answer key
    on each line to correctly categorize all data.
    """

    NSAR_MAP = {
        '001A': "Registrant Name", '001B': "SEC File Number", '001C': "Telephone Number",
        '002A': "Street", '002B': "City", '002C': "State", '002D01': "Zip Code",
        '003': "Is Registrant a Small Business Investment Company?",
        '004': "Is Registrant a Unit Investment Trust?", '005': "Is Registrant a Separate Account?",
        '006': "Is Registrant a Non-diversified Company?", '007A': "Is Registrant a Series Company?",
        '019A': "Is registrant a series company?", '019B': "Number of series", '019C': "Family of investment companies name",
        '021': "Total Broker Commissions Paid ($000)", '024': "Is registrant a diversified investment company?",
        '071A': "Total income ($000)", '071B': "Total expenses ($000)", '071C': "Net investment income ($000)",
        '071D': "Net gains or (losses) ($000)", '074F': "Total Investments ($000)", '074N': "Total liabilities ($000)",
        '074T': "Net assets ($000)", '075A': "Number of shares outstanding", '075B': "Net asset value per share",
        '080C': "Fidelity Bond Coverage Amount ($)",
        '081A': "Fidelity bond in effect?", '081B': "Fidelity bond coverage amount ($000)",
        '082A': "Were any claims filed under fidelity bond?", '082B': "Amount of claims ($)",
        '083A': "Any uncollectible advisory fees?", '084A': "Any uncollectible underwriting commissions?",
        '085A': "Has registrant acquired another investment company?", '085B': "Has registrant been acquired by another?"
    }

    TABLE_SPECS = {
        '007': {"name": "Series Information", "cols": {'C01': "Series Number", 'C02': "Series Name", 'C03': "Is this the last filing for this series?"}},
        '008': {"name": "Investment Advisers", "cols": {'A': "Name", 'B': "Type", 'C': "File No.", 'D01': "City", 'D02': "State"}},
        '010': {"name": "Custodians", "cols": {'A': "Name", 'B': "File No.", 'C01': "City", 'C02': "State"}},
        '011': {"name": "Principal Underwriters", "cols": {'A': "Name", 'B': "File No.", 'C01': "City", 'C02': "State"}},
        '012': {"name": "Transfer Agents", "cols": {'A': "Name", 'B': "File No.", 'C01': "City", 'C02': "State"}},
        '013': {"name": "Independent Public Accountants", "cols": {'A': "Name", 'B01': "City", 'B02': "State"}},
        '014': {"name": "Brokers", "cols": {'A': "Name", 'B': "File No."}},
        '015': {"name": "Sub-Custodians", "cols": {'A': "Name", 'B': "Type", 'C01': "City", 'C02': "State", 'E01': "Holds Assets?"}},
        '020': {"name": "Top 10 Brokers by Commission", "cols": {'A': "Broker Name", 'B': "IRS No.", 'C': "Commissions Paid ($000)"}},
        '022': {"name": "Securities Depositories", "cols": {'A': "Depository Name", 'B': "IRS No.", 'C': "Value of Securities ($000)", 'D': "Amount of Deposits ($000)"}},
    }

    def _format_nsar_value(label: str, value: object) -> str:
        if pd.isna(value) or str(value).strip() in ("N/A", "", "—", "nan"): return "—"
        clean = str(value).strip().replace(",", "")
        if not clean: return "—"
        if "Telephone Number" in label and re.fullmatch(r"\d{10,11}", clean):
            digits = clean[-10:]
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        if "SEC File Number" in label and re.fullmatch(r"811-\d{1,5}", clean):
            prefix, num = clean.split("-")
            return f"{prefix}-{int(num):05d}"
        if "Net asset value per share" in label:
            try: return f"${float(clean):.2f}"
            except (ValueError, TypeError): return clean
        if "($000)" in label:
            try: return f"${int(float(clean)):}"
            except (ValueError, TypeError): return clean
        if "($)" in label:
             try: return f"${int(float(clean)):}"
             except (ValueError, TypeError): return clean
        if clean.upper() in ("Y", "N", "X"): return "Yes" if clean.upper() in ("Y", "X") else "No"
        if re.match(r'^-?[\d.]+$', clean):
            try: return f"{int(float(clean)):}"
            except (ValueError, TypeError): return clean
        return str(value).strip()

    pem_pattern = r'-----BEGIN PRIVACY-ENHANCED MESSAGE-----(.*?)-----END PRIVACY-ENHANCED MESSAGE-----'
    text_inside_pem = re.search(pem_pattern, raw_text, re.DOTALL)
    
    if not text_inside_pem:
        content = re.sub(r'<IMS-HEADER>.*?</IMS-HEADER>|<IMS-DOCUMENT>.*?</IMS-DOCUMENT>', '', raw_text, flags=re.DOTALL).strip()
    else:
        content = text_inside_pem.group(1)
        content = re.sub(r'<IMS-HEADER>.*?</IMS-HEADER>', '', content, flags=re.DOTALL)
        content = re.sub(r'<IMS-DOCUMENT>.*?\n|</IMS-DOCUMENT>', '', content, flags=re.DOTALL)
    
    lines = [line for line in content.strip().splitlines() if line.strip() and not line.strip().startswith(('<PAGE>', 'SIGNATURE', 'TITLE'))]
    if not lines: return ""
    
    registrant_data = {}
    series_data = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))

    answer_key_re = re.compile(
        r"^(?P<item>\d{3})"
        r"(?P<letter>[A-Z ]{2})"
        r"(?P<sub>\d{2})"
        r"(?P<series>\d{2})"
        r"(?P<rep>\d{2})\s+"
        r"(?P<value>.*\S)?$"
    )

    first_series_id = None

    for line in lines:
        match = answer_key_re.match(line)
        if not match: continue

        parts = match.groupdict()
        item, letter, series_id, rep_id, value = \
            parts['item'], parts['letter'].strip(), parts['series'], int(parts['rep']), parts.get('value')
        
        full_letter = letter + parts['sub'] if parts['sub'] != "00" else letter

        if item == '007' and letter == 'C' and first_series_id is None:
            first_series_id = parts['sub']

        if rep_id > 0:
            series_data[series_id][item][rep_id][full_letter] = value
        elif series_id == '00':
            FINANCIAL_ITEMS = {'024', '071', '072', '073', '074', '075', '076', '077'}
            if item in FINANCIAL_ITEMS and first_series_id:
                series_data[first_series_id][item][1][full_letter] = value
            else:
                registrant_data[item + full_letter] = value
        else:
            series_data[series_id][item][1][full_letter] = value

    md_parts = ["## Form NSAR-B: Semi-Annual Report for Registered Investment Companies"]
    md_parts.append("\n### General Information")
    for key in ['001A', '001B', '001C', '019A', '019B', '019C']:
         if key in registrant_data:
            md_parts.append(f"**{NSAR_MAP[key]}**: {_format_nsar_value(NSAR_MAP[key], registrant_data.get(key))}")
    
    addr_parts = [registrant_data.get(k) for k in ['002A', '002B', '002C', '002D01']]
    if any(p for p in addr_parts if p and _format_nsar_value('', p) != '—'):
        md_parts.append(f"**Address:** {', '.join(p for p in addr_parts if p and _format_nsar_value('', p) != '—')}")

    for key in ['003', '004', '005', '006', '007A']:
        if key in registrant_data: md_parts.append(f"**{NSAR_MAP[key]}**: {_format_nsar_value(NSAR_MAP[key], registrant_data[key])}")
    
    registrant_tables = series_data.get('00', {})
    for item_num, spec in TABLE_SPECS.items():
        if item_num in registrant_tables:
            table_rows = [row for rep_id, row in sorted(registrant_tables[item_num].items())]
            df_table = pd.DataFrame(table_rows).rename(columns=spec['cols'])
            expected_cols = list(spec['cols'].values())
            df_table = df_table.reindex(columns=expected_cols, fill_value="—")
            for col in df_table.columns: df_table[col] = df_table[col].apply(lambda x: _format_nsar_value(col, x))
            if not df_table.empty:
                md_parts.append(f"\n### {spec['name']}\n\n{to_compact_markdown(df_table, index=False)}")

    for series_id_str in sorted(series_data.keys()):
        if series_id_str == '00': continue
        
        header_sgml_match = re.search(rf"<SERIES-ID>S\d+?{series_id_str}</SERIES-ID>\s*<SERIES-NAME>([^<]+)", raw_text, re.I)
        series_name = header_sgml_match.group(1).strip() if header_sgml_match else f"Series {series_id_str}"
        
        series_items = series_data[series_id_str]
        
        if any(item in series_items for item in ['024', '071', '074', '075']):
            md_parts.append(f"\n### {series_name}")
            md_parts.append("\n**Financial Highlights**")
            for item_code in sorted(series_items.keys()):
                if item_code in NSAR_MAP:
                    value_dict = series_items[item_code].get(1, {})
                    value = next(iter(value_dict.values()), "—")
                    md_parts.append(f"**{NSAR_MAP[item_code]}**: {_format_nsar_value(NSAR_MAP[item_code], value)}")

    md_parts.append("\n### Registrant Totals & Other Information")
    for key in ['021', '080C', '081A', '081B', '082A', '082B', '083A', '084A', '085A', '085B']:
        if key in registrant_data: md_parts.append(f"**{NSAR_MAP.get(key, key)}**: {_format_nsar_value(NSAR_MAP.get(key, key), registrant_data[key])}")

    sig_match = re.search(r"SIGNATURE\s+([^\n]+)\nTITLE\s+([^\n]+)", content, re.S)
    if not sig_match: sig_match = re.search(r"SIGNATURE\s+(.*?)\s+TITLE\s+(.*)", content, re.S)
    if sig_match:
        signature, title = sig_match.groups()
        md_parts.append(f"\n---\n\n**Signature**: {signature.strip()}  \n**Title**: {title.strip()}")

    for exhibit_match in re.finditer(r"<DOCUMENT>.*?<TYPE>(EX-[^<\n]+)(.*?)</DOCUMENT>", raw_text, re.S | re.I):
        ex_type, ex_content_full = exhibit_match.groups()
        ex_type = ex_type.strip()
        text_match = re.search(r"<TEXT>(.*)", ex_content_full, re.S | re.I)
        text = text_match.group(1).strip() if text_match else ex_content_full.strip()
        if "<TABLE>" in text and "<LEGEND>" in text:
             md_parts.append(f"\n## {ex_type}\n\n```text\n{text}\n```")
        else:
             md_parts.append(f"\n## {ex_type}\n\n{text}")

    return "\n\n".join(p for p in md_parts if p and p.strip()).replace("</TEXT>", "")

def tag_spans_in_table_soup(table_soup: BeautifulSoup):
    """
    Finds cells with rowspan or colspan and appends a unique text-based
    placeholder to the cell's content for later parsing. This modifies
    the soup in place.
    """
    for cell in table_soup.find_all(['td', 'th']):
        rowspan = cell.get('rowspan')
        if rowspan:
            try:
                span_val = int(rowspan)
                if span_val > 1:
                    placeholder = table_soup.new_string(f"##ROWSPAN_{span_val}##")
                    cell.append(placeholder)
            except (ValueError, TypeError):
                continue

        colspan = cell.get('colspan')
        if colspan:
            try:
                span_val = int(colspan)
                if span_val > 1:
                    placeholder = table_soup.new_string(f"##COLSPAN_{span_val}##")
                    cell.append(placeholder)
            except (ValueError, TypeError):
                continue

def colspan_rowspan_tag(table_soup):
    """
    Finds cells with top or bottom borders and tags them with distinct
    sentinels for top and bottom borders.
    """
    for idx, cell in enumerate(table_soup.find_all(['td', 'th'])):
        colspan_tag = cell.get('colspan')
        rowspan_tag = cell.get('rowspan')

        if rowspan_tag:
            try:
                if int(rowspan_tag) > 1:
                    cell.append(f'##ROWSPAN_{idx}##')
            except (TypeError, ValueError):
                pass

        if colspan_tag:
            try:
                if int(colspan_tag) > 1:
                    cell.append(f'##COLSPAN_{idx}##')
            except (TypeError, ValueError):
                pass

def protect_special_chars_in_tables(soup: BeautifulSoup):
    """
    Finds and replaces special characters that might be misinterpreted as
    Markdown (*, **, ***) with text-based placeholders.

    This version is robust and applies the replacement to ALL text nodes
    in the document, regardless of whether they are in a table, paragraph,
    font tag, or any other element.
    """
    for text_node in soup.find_all(string=True):
        original_text = str(text_node)
        
        modified_text = original_text.replace('***', '##TRIPLE_ASTERISK##')
        modified_text = modified_text.replace('**', '##DOUBLE_ASTERISK##')
        modified_text = modified_text.replace('*', '##SINGLE_ASTERISK##')
        
        if original_text != modified_text:
            text_node.replace_with(modified_text)

def protect_numeric_list_items(html_content: str) -> str:
    """
    Finds numeric list items in table cells and protects them with a placeholder.
    """
    try:
        soup = BeautifulSoup(html_content, "lxml")
    except ValueError as e:
        if "not enough values to unpack" in str(e):
            print(f"[Warning] lxml parser crashed on malformed attributes. Falling back to html.parser.")
            soup = BeautifulSoup(html_content, "html.parser")
        else:
            raise
    for td in soup.find_all(['td', 'th']):
        cell_text = td.get_text(strip=True)
        if re.fullmatch(r'\s*\d+\.\s*', cell_text):
            td.string = f"##PROTECT_{cell_text}##"
    return str(soup)

def _calculate_effective_indent(tag) -> dict:
    """
    Parses the style attribute of a tag to calculate the effective left indentation
    and returns it along with the font size for normalization. This version
    approximates percentage-based indents.
    """
    style = tag.get('style', '').lower().strip()
    if not style:
        return {'indent': 0.0, 'font_size': None}

    prop_re = re.compile(r'([\w-]+)\s*:\s*([^;]+)')
    declarations = prop_re.findall(style)

    DEFAULT_CONTAINER_WIDTH_PT = 612.0

    conversions = {'in': 72.0, 'pt': 1.0, 'px': 0.75, 'em': 12.0}
    margin_left_pt = 0.0
    padding_left_pt = 0.0
    text_indent_pt = 0.0
    font_size_pt = None

    def parse_value(v_str):
        match = re.search(r'(-?\d*\.?\d+)(in|pt|px|em|%)?', v_str)
        if match:
            try:
                num = float(match.group(1))
                unit = match.group(2) if match.group(2) else 'pt'

                if unit == '%':
                    return (num / 100.0) * DEFAULT_CONTAINER_WIDTH_PT
                
                return num * conversions.get(unit, 1.0)
            except (ValueError, TypeError):
                return 0.0
        return 0.0

    for prop, val_str in declarations:
        if prop == 'font-size':
            font_size_pt = parse_value(val_str)
        elif prop == 'margin-left':
            margin_left_pt = parse_value(val_str)
        elif prop == 'padding-left':
            padding_left_pt = parse_value(val_str)
        elif prop == 'text-indent':
            text_indent_pt = parse_value(val_str)
        elif prop == 'margin':
            values = val_str.split()
            if len(values) == 1: margin_left_pt = parse_value(values[0])
            elif len(values) == 2: margin_left_pt = parse_value(values[1])
            elif len(values) >= 4: margin_left_pt = parse_value(values[3])
        elif prop == 'padding':
            values = val_str.split()
            if len(values) == 1: padding_left_pt = parse_value(values[0])
            elif len(values) == 2: padding_left_pt = parse_value(values[1])
            elif len(values) >= 4: padding_left_pt = parse_value(values[3])

    return {
        'indent': margin_left_pt + padding_left_pt + text_indent_pt,
        'font_size': font_size_pt
    }

def _normalize_list_indentation(soup: BeautifulSoup):
    """
    Finds all elements that look like list items, calculates their visual
    indentation from CSS, and standardizes them into <p> tags with an
    ##INDENT## placeholder. This version also merges fragmented bullet
    points where the bullet and its text are in separate adjacent tags.
    """
    LIST_MARKER_RE = re.compile(
        r"""(?ix) # Use case-insensitive and verbose flags
        ^ \s* (?: # Start of line, optional space, and main non-capturing group
            
            # Case 1: Simple bullet characters that can be immediately followed by non-space characters.
            [○•●·◦➢▪]
            
            | # OR
            
            # Case 2: More complex markers that MUST be followed by a separator.
            (?: # Group for the complex markers themselves
                \d+ \. \d [\d\.]*  # Multi-level numbers like 1.2.3
                | \d+ \. (?!\d)     # Numbers with a dot, NOT followed by another digit (e.g., "1.")
                | [a-z] [\.\)]      # Letters like a. or a)
                | [ivxlcdm]+ \.     # Roman numerals like i.
                | \( [a-z0-9]+ \)   # Parenthesized markers like (a) or (1)
            )
            # The required separator for complex markers (space, placeholder, or end-of-line)
            (?: \s+ | \#\# | $ ) 
            
        ) # End of the main non-capturing group
        """,
        re.I | re.VERBOSE
    )
    
    DEFAULT_FONT_SIZE_PT = 10.0
    STANDARD_INDENT_EM = 1.2

    potential_list_items = soup.find_all(['p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
    
    for tag in potential_list_items:
        
        
        children = tag.find_all(['font', 'span'], recursive=False)
        
        if len(children) > 1:
            first_child, second_child = children[0], children[1]
            
            is_bullet_fragment = (
                re.fullmatch(r'\s*[•●·◦➢▪]\s*', first_child.get_text()) and 
                second_child.get_text(strip=True)
            )
            
            if is_bullet_fragment:
                style = second_child.get('style', '')
                if 'padding-left' in style:
                    first_child.insert_after(NavigableString(" "))

        text = tag.get_text(separator=' ', strip=True)
        
        if not LIST_MARKER_RE.match(text):
            continue

        indent_info = _calculate_effective_indent(tag)
        indent_pt = indent_info['indent']
        
        font_size_pt = indent_info.get('font_size') or DEFAULT_FONT_SIZE_PT
        
        if font_size_pt > 0:
            indent_em = indent_pt / font_size_pt
            level = int(max(0, round(indent_em / STANDARD_INDENT_EM)))
        else:
            level = 0

        if level > 0:
            indent_prefix = soup.new_string('##INDENT##' * (level))
            tag.insert(0, indent_prefix)

        tag.name = 'p'
        tag.attrs = {}

def is_cell_truly_empty(cell_value):
    """
    Checks if a cell is empty after removing all custom tags,
    placeholders, and special whitespace.
    """
    if pd.isna(cell_value):
        return True
    text = str(cell_value)
    text = re.sub(r'##(SUP|/SUP|SUB|/SUB|BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##', '', text)
    text = text.replace('##NEWLINE##', '').replace('<BORDER>', '').replace('\u00A0', '').replace('\u2063', '').replace("—", "").replace("–", "").replace("-", "")
    if text == '':
        return True
    return not text.strip()

def is_numeric_like(s: str) -> bool:
    """
    Checks if a string, after removing all custom placeholders, HTML tags,
    and common financial formatting, can be interpreted as a number.
    This is a comprehensive check for use on raw DataFrame cells.
    """
    if not isinstance(s, str):
        return False

    cleaned_s = re.sub(r'##(SUP|/SUP|SUB|/SUB|BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##', '', s)
    
    cleaned_s = cleaned_s.replace('##NEWLINE##', '')
    cleaned_s = cleaned_s.replace('<BORDER>', '')
    cleaned_s = cleaned_s.replace('\u00A0', ' ')
    cleaned_s = cleaned_s.replace('\u2063', '')
    
    cleaned_s = re.sub(r'<.*?>', '', cleaned_s)
    
    cleaned_s = cleaned_s.replace('$', '').replace(',', '').replace('(', '').replace(')', '')

    cleaned_s = cleaned_s.strip().replace(' ', '')
    cleaned_s = re.sub(r'(?<=\d)%', '', cleaned_s)

    cleaned_s = cleaned_s.strip().replace(' ', '')

    if cleaned_s in ('—', '–', '-', ''):
        return True
    
    try:
        float(cleaned_s)
        return True
    except (ValueError, TypeError):
        return False
    
def pre_fix_document_structure(soup: BeautifulSoup):
    """
    Corrects severe structural issues in the HTML, such as improperly nested
    content within list items and stray <br> tags between list items. Also
    fixes table headers (<th>) that are not wrapped in a <tr>.
    """
    for table in soup.find_all("table"):
        stray_th_tags = table.find_all('th', recursive=False)
        if stray_th_tags:
            new_header_row = soup.new_tag("tr")
            for th in stray_th_tags:
                new_header_row.append(th.extract())
            table.insert(0, new_header_row)


    for list_tag in soup.find_all(['ol', 'ul']):
        for br in list_tag.find_all('br', recursive=False):
            br.decompose()

    return soup

def convert_styled_inline_divs_to_spans(soup: BeautifulSoup):
    """
    Finds all <div> tags styled with 'display:inline' and converts them
    into <span> tags. This preserves their styling information for later
    processing without treating them as block-level elements.
    """
    for tag in soup.find_all('div', style=True):
        if re.search(r'display\s*:\s*inline', tag.get('style', ''), re.IGNORECASE):
            tag.name = 'span'

def convert_vertical_align_superscripts(soup: BeautifulSoup):
    """
    Finds elements styled with `vertical-align: top` and a reduced font size,
    and replaces them with a text-based ##SUP## placeholder for consistent processing.
    """
    va_super_re = re.compile(r'vertical-align\s*:\s*(super|top)', re.IGNORECASE)

    for tag in soup.find_all(['div', 'span', 'font'], style=True):
        style = tag.get('style', '')
        
        is_vertically_aligned = va_super_re.search(style)
        has_small_font = 'font-size' in style.lower()

        if is_vertically_aligned and has_small_font:
            text_content = tag.get_text(strip=True)
            if text_content:
                placeholder_text = f"##SUP##{text_content}##/SUP##"
                
                tag.replace_with(NavigableString(placeholder_text))

def is_positioned_container(tag: Tag) -> bool:
    """
    Checks if a given BeautifulSoup tag is a container for positioned HTML content.
    """
    if tag.find('table'):
        return False

    POSITIONED_DIV_THRESHOLD = 1000 
    
    div_pattern = r'<div[^>]+style\s*=\s*".*?position\s*:\s*(?:absolute|relative).*?left\s*:.*?"'
    matches = re.findall(div_pattern, str(tag), re.IGNORECASE | re.DOTALL)
    
    return len(matches) > POSITIONED_DIV_THRESHOLD

def is_document_layout_positioned(soup: BeautifulSoup) -> bool:
    """
    Determines if an HTML document uses a positioned layout rather than a semantic one.
    
    This is determined by checking for a high number of absolutely positioned
    elements, which is a strong indicator of a document designed for visual
    rendering rather than semantic parsing. It also includes a fast-path
    check for known page-container patterns.
    """
    page_containers = soup.find_all('div', id=re.compile(r'^pf\w+$'))
    if len(page_containers) > 3:
        return True

    POSITIONED_ELEMENT_THRESHOLD = 1000 
    MAX_TABLES_THRESHOLD = 20

    positioned_style_re = re.compile(r'position\s*:\s*absolute', re.IGNORECASE)
    
    positioned_elements = soup.find_all(style=positioned_style_re)
    
    num_tables = len(soup.find_all('table'))

    if len(positioned_elements) > POSITIONED_ELEMENT_THRESHOLD and num_tables < MAX_TABLES_THRESHOLD:
        return True
        
    return False

def handle_width_indented_list_table(table_element, output_list: list) -> bool:
    """
    Deterministically handles list-like tables by measuring the width of the
    first spacer cell in the *first content row* to determine the indentation
    level for the entire table.

    Returns True if the table was handled, False otherwise.
    """
    table_text = table_element.get_text(strip=True)
    if '●' not in table_text and '○' not in table_text:
        return False

    rows = table_element.find_all('tr', recursive=False)
    if not rows:
        return False

    is_indented_list = False
    INDENTATION_THRESHOLD_PX = 100

    first_content_row = next((row for row in rows if row.get_text(strip=True)), None)
    
    if first_content_row:
        cells = first_content_row.find_all('td', recursive=False)
        if len(cells) == 3 and not cells[0].get_text(strip=True):
            spacer_cell = cells[0]
            width_attr = spacer_cell.get('style', '') + spacer_cell.get('width', '')
            width_match = re.search(r'width\s*:\s*([\d\.]+)', width_attr)
            if width_match:
                try:
                    indent_px = float(width_match.group(1))
                    if indent_px > INDENTATION_THRESHOLD_PX:
                        is_indented_list = True
                except ValueError:
                    pass

    list_items = []
    for row in rows:
        if not row.get_text(strip=True):
            continue

        cells = row.find_all('td', recursive=False)
        if len(cells) != 3 or cells[0].get_text(strip=True):
            continue

        _spacer, marker_cell, content_cell = cells
        marker_text = marker_cell.get_text(strip=True)
        content_text = re.sub(r'\s+', ' ', content_cell.get_text(separator=' ', strip=True)).strip()

        if marker_text or content_text:
            indent_prefix = "##INDENT##" if is_indented_list else ""
            list_items.append(f"{indent_prefix}{marker_text} {content_text}")

    if list_items:
        output_list.append("\n\n".join(list_items) + "\n\n")
        return True

    return False

def fix_inverted_bold_paragraphs(soup: BeautifulSoup):
    """
    Finds malformed `<b><p>...</p></b>` structures and corrects them to the
    standard `<p><b>...</b></p>` format, which can be processed correctly by
    downstream logic.
    """
    for b_tag in soup.find_all(['b', 'strong']):
        p_tag = b_tag.find('p')
        
        if p_tag and b_tag.get_text(strip=True) == p_tag.get_text(strip=True):
            content = p_tag.get_text(separator=' ', strip=True)
            
            new_p = soup.new_tag('p')
            new_b = soup.new_tag('b')
            new_b.string = content
            new_p.append(new_b)
            
            b_tag.replace_with(new_p)

def fix_malformed_inline_paragraphs(html_content: str) -> str:
    """
    Uses regex to iteratively fix malformed HTML where a <p> tag is incorrectly nested 
    inside an inline formatting tag (b, i, u, etc.). This runs on the raw string 
    before BeautifulSoup parsing to ensure the structure is valid.
    
    It repeatedly swaps tag pairs like <b><p> -> <p><b> and </p></b> -> </b></p>
    until the document structure is corrected.
    """
    open_pattern = re.compile(
        r'(<(?:b|i|u|strong|em)(?: [^>]*)?>\s*)(<p(?: [^>]*)?>)',
        re.IGNORECASE
    )
    close_pattern = re.compile(
        r'(</p>\s*)(</(?:b|i|u|strong|em)>)',
        re.IGNORECASE
    )
    
    while True:
        new_content = open_pattern.sub(r'\2\1', html_content)
        new_content = close_pattern.sub(r'\2\1', new_content)
        
        if new_content == html_content:
            break
        
        html_content = new_content
        
    return html_content

def _fix_escaped_malformed_font_tag(html_string: str) -> str:
    """
    Uses a targeted regex to fix a specific, known issue where a malformed
    '< FONT...' tag is incorrectly escaped by BeautifulSoup during initial parsing.
    """
    fixed_string = re.sub(r'&lt;\s+FONT', '<FONT', html_string, flags=re.IGNORECASE)
    
    fixed_string = fixed_string.replace('sans-serif&gt;', 'sans-serif>')
    
    return fixed_string

def defragment_adjacent_tags(soup: BeautifulSoup, tags_to_merge: list):
    """
    Robustly merges adjacent tags of the same type. This version is corrected
    to use an index-based while loop to prevent infinite loops and uses the
    correct .extract() method to prevent crashes.
    """
    for parent in soup.find_all(True):
        i = 0
        while i < len(parent.contents) - 1:
            current_node = parent.contents[i]

            if getattr(current_node, 'name', None) not in tags_to_merge:
                i += 1
                continue

            
            next_node = parent.contents[i + 1]
            whitespace_node = None
            real_next_node = next_node

            if isinstance(next_node, NavigableString) and not next_node.strip():
                whitespace_node = next_node
                if i + 2 < len(parent.contents):
                    real_next_node = parent.contents[i + 2]
                else:
                    i += 1
                    continue

            if getattr(real_next_node, 'name', None) in tags_to_merge:
                
                if whitespace_node:
                    current_node.append(NavigableString(" "))

                for child in list(real_next_node.contents):
                    current_node.append(child.extract())
                
                real_next_node.extract()
                if whitespace_node:
                    whitespace_node.extract()
                
            else:
                i += 1

def dedupe_adjacent_containers(lst):
    out = []
    for x in lst:
        if not out or x != out[-1]:
            out.append(x)
    return out

def parse_positioned_html_islands_via_ocr(soup: BeautifulSoup):
    """
    Finds "islands" of positioned HTML, converts each to a PDF, processes it via OCR,
    and returns the complete parsed markdown and a success flag. It de-duplicates
    page containers to prevent processing the same page multiple times.
    """
    _state.LAST_POSITIONED_HTML_OCR_PAGE_COUNT = 0
    all_containers = [div for div in soup.find_all("div") if is_positioned_container(div)]
    if not all_containers:
        return None, False
    if Config.SKIP_OCR:
        _log_current_filing_ocr("positioned_html_islands_skipped")
        return "<!-- Positioned-layout HTML islands skipped by SEC_PARSER_SKIP_OCR. -->", True

    top_level_containers = [c for c in all_containers if not any(p in all_containers for p in c.parents)]
    
    if not top_level_containers:
        return None, False
    
    print(f"--> Identified {len(top_level_containers)} unique positioned HTML island(s) to process via PDF conversion.")

    style_tags = soup.head.find_all('style') if soup.head else soup.find_all('style')
    css_styles = "\n".join(style.string for style in style_tags if style.string)
    if not _has_mistral_api_keys():
        print(f"[Error] {_mistral_no_keys_message()}")
        return None, False

    start_time = time.time()
    TIME_LIMIT_SECONDS = Config.PDF_TIMEOUT_LIMIT * 60
    md_parts = []
    parsed_page_count_total = 0

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        for i, container_div in enumerate(top_level_containers):
            print(f"--> Processing island {i + 1} of {len(top_level_containers)} (ID: {container_div.get('id')})...")
            try:
                temp_html = f"""
                <!DOCTYPE html><html><head><meta charset="UTF-8"><style>{css_styles}</style></head>
                <body>{str(container_div)}</body></html>
                """
                page.set_content(temp_html)
                pdf_bytes = page.pdf(format='A4')

                page_results, timed_out, _parsed_page_count = _process_pdf_bytes_with_fallback(
                    pdf_bytes=pdf_bytes,
                    file_name=f"html_island_{i+1}.pdf",
                    batch_size=Config.PDF_BATCH_SIZE,
                    mistral_api_key=None,
                    per_table_sleep_s=Config.PER_TABLE_SLEEP_SECONDS,
                    start_time=start_time,
                    time_limit_s=TIME_LIMIT_SECONDS
                )
                parsed_page_count_total += int(_parsed_page_count or 0)
                parsed_markdown = "\n\n".join(res.get('content', '') for res in page_results if res.get('content'))
                md_parts.append(parsed_markdown)
                print(f"--> Island {i + 1} successfully parsed.")

            except Exception as e:
                error_msg = f"Failed to process positioned HTML island {i + 1}: {e}"
                print(f"[Error] {error_msg}")
                traceback.print_exc()
                md_parts.append(f"<!-- {error_msg} -->")

        browser.close()

    final_markdown = "\n\n------\n\n".join(md_parts)
    _state.LAST_POSITIONED_HTML_OCR_PAGE_COUNT = parsed_page_count_total
    return final_markdown, True

def parse_html_via_pdf_render(html_content: str, file_name_for_logging: str) -> str:
    """
    High-quality OCR-based parser for any HTML document identified as having a
    positioned layout. Renders the entire document to a PDF in memory and uses
    Mistral's vision models to extract text and tables.
    """
    _state.LAST_POSITIONED_HTML_OCR_PAGE_COUNT = 0
    _load_sec_parser_env()
    if Config.SKIP_OCR:
        _log_current_filing_ocr("positioned_html_ocr_skipped")
        return "<!-- Positioned-layout HTML OCR skipped by SEC_PARSER_SKIP_OCR. -->"
    if not _has_mistral_api_keys():
        print(f"{_mistral_no_keys_message()} Skipping OCR processing.")
        return "<!-- No Mistral API keys found. Positioned HTML was not processed. -->"

    start_time = time.time()
    TIME_LIMIT_SECONDS = Config.PDF_TIMEOUT_LIMIT * 60
    
    print(f"--> Rendering full document '{file_name_for_logging}' to PDF for OCR processing...")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_content(html_content)
            pdf_bytes = page.pdf(format='A4', print_background=True)
            browser.close()

        page_results, timed_out, _parsed_page_count = _process_pdf_bytes_with_fallback(
            pdf_bytes=pdf_bytes,
            file_name=file_name_for_logging,
            batch_size=Config.PDF_BATCH_SIZE,
            mistral_api_key=None,
            per_table_sleep_s=Config.PER_TABLE_SLEEP_SECONDS,
            start_time=start_time,
            time_limit_s=TIME_LIMIT_SECONDS
        )
        _state.LAST_POSITIONED_HTML_OCR_PAGE_COUNT = int(_parsed_page_count or 0)
        
        if timed_out:
            print(f"[timeout] Processing for '{file_name_for_logging}' timed out.")

        parsed_markdown = "\n\n".join(res.get('content', '') for res in page_results if res.get('content'))
        return parsed_markdown, True

    except Exception as e:
        error_msg = f"Failed to render or process positioned HTML file '{file_name_for_logging}': {e}"
        print(f"[Error] {error_msg}")
        traceback.print_exc()
        return f"<!-- {error_msg} -->", True

def convert_margin_layout_to_table(soup: BeautifulSoup):
    """
    Finds consecutive divs that use font tags with large left margins for layout
    and converts the entire block into a single table. This handles cases where
    multi-column text is created with CSS margins instead of table tags.
    """
    potential_blocks = soup.find_all('div', style=re.compile(r'float:left'))
    
    i = 0
    while i < len(potential_blocks):
        start_block = potential_blocks[i]
        
        row_divs = [start_block]
        current = start_block
        while True:
            next_sibling = current.find_next_sibling('div')
            if next_sibling and next_sibling in potential_blocks:
                row_divs.append(next_sibling)
                current = next_sibling
            else:
                break
        
        table_rows_data = []
        is_target_pattern = True
        
        for row_div in row_divs:
            inner_div = row_div.find('div', recursive=False)
            if not inner_div:
                is_target_pattern = False
                break
                
            children = inner_div.find_all(['font', 'span'], recursive=False)
            if len(children) != 2:
                is_target_pattern = False
                break
            
            style = children[1].get('style', '')
            margin_match = re.search(r'margin-left\s*:\s*([\d\.]+)\s*pt', style, re.I)
            if not margin_match or float(margin_match.group(1)) < 100:
                is_target_pattern = False
                break
                
            col1_content = children[0].decode_contents()
            col2_content = children[1].decode_contents()
            table_rows_data.append((col1_content, col2_content))

        if is_target_pattern and table_rows_data:
            new_table = soup.new_tag('table')
            for col1_html, col2_html in table_rows_data:
                tr = soup.new_tag('tr')
                td1 = soup.new_tag('td')
                td2 = soup.new_tag('td')
                
                td1.extend(BeautifulSoup(col1_html, 'lxml').body.contents)
                td2.extend(BeautifulSoup(col2_html, 'lxml').body.contents)
                
                tr.append(td1)
                tr.append(td2)
                new_table.append(tr)
                
            start_block.replace_with(new_table)
            
            for old_div in row_divs[1:]:
                old_div.decompose()
            
            i += len(row_divs)
        else:
            i += 1

def to_compact_markdown(df: pd.DataFrame, **kwargs) -> str:
    """
    Converts a DataFrame to a token-efficient Markdown string. It uses minimal
    `---` separators but intelligently preserves the column alignment specified
    by the original (longer) separator line from pandas.
    """
    markdown_str = df.to_markdown(**kwargs)
    if not markdown_str:
        return ""

    lines = markdown_str.splitlines()
    if len(lines) < 2:
        return markdown_str

    original_separator = lines[1]
    
    separator_cells = original_separator.strip('|').split('|')
    new_separators = []
    for cell in separator_cells:
        cell = cell.strip()
        if cell.startswith(':') and cell.endswith(':'):
            new_separators.append(':---:')
        elif cell.endswith(':'):
            new_separators.append('---:')
        else:
            new_separators.append(':---')

    compact_separator = '|' + '|'.join(new_separators) + '|'
    
    lines[1] = compact_separator
    
    return "\n".join(lines)

def _flatten_redundant_nesting(soup: BeautifulSoup):
    """
    Iteratively simplifies deeply nested structures where a tag's only
    significant child is another tag of the same type. This version is
    corrected to NOT flatten nested font tags if it would destroy the
    critical 'face' attribute.
    """
    while True:
        changed = False
        for tag in soup.find_all(['div', 'span', 'font', 'b', 'strong', 'i', 'em']):
            
            significant_children = [
                child for child in tag.contents
                if (isinstance(child, NavigableString) and child.strip()) or isinstance(child, Tag)
            ]

            if len(significant_children) == 1:
                inner_child = significant_children[0]
                
                if isinstance(inner_child, Tag) and tag.name == inner_child.name:
                    
                    if tag.name == 'font':
                        if inner_child.has_attr('face') and not tag.has_attr('face'):
                            continue

                    tag.clear()
                    for grandchild in list(inner_child.contents):
                        tag.append(grandchild.extract())
                    
                    changed = True

        if not changed:
            break

def convert_div_table_to_html(div_table_soup: Tag) -> str:
    """Converts a div-based table structure into a standard HTML table string."""
    html_str = "<table>"
    rows = div_table_soup.find_all(lambda tag: tag.name == 'div' and 'display: table-row' in tag.get('style', ''))
    
    for row_div in rows:
        html_str += "<tr>"
        cells = row_div.find_all(lambda tag: tag.name == 'div' and 'display: table-cell' in tag.get('style', ''))
        for cell_div in cells:
            colspan = cell_div.get('colspan', '1')
            rowspan = cell_div.get('rowspan', '1')
            html_str += f"<td colspan='{colspan}' rowspan='{rowspan}'>{cell_div.decode_contents()}</td>"
        html_str += "</tr>"
        
    html_str += "</table>"
    return html_str

def convert_nested_div_table_to_placeholders(soup: BeautifulSoup):
    """
    Finds a div-based table nested inside a <td>, parses it to a Markdown
    table, replaces all pipes '|' with '<PIPE>' and all newlines '\n' with
    '##MD_NEWLINE##', and then replaces the original div with this new
    single-line string.
    """
    for div_table in soup.select('td > div[style*="display: table"]'):
        try:
            div_html = str(div_table)
            temp_soup = BeautifulSoup(div_html, 'lxml')
            
            if (table_tag := temp_soup.find('div', style=re.compile(r'display:\s*table'))):
                table_tag.name = 'table'
            for row in temp_soup.find_all('div', style=re.compile(r'display:\s*table-row')):
                row.name = 'tr'
            for cell in temp_soup.find_all('div', style=re.compile(r'display:\s*table-cell')):
                cell.name = 'td'

            df_list = pd.read_html(io.StringIO(str(temp_soup)), flavor="lxml", keep_default_na=False, header=0)
            if not df_list:
                continue
            
            df = df_list[0]
            
            df.dropna(how='all', axis=1, inplace=True)
            df.dropna(how='all', axis=0, inplace=True)
            df = df.reset_index(drop=True)

            if df.empty:
                continue

            md_table_string = to_compact_markdown(df, index=False)
            
            placeholder_string = md_table_string.replace('\n', '##MD_NEWLINE##').replace('|', '<PIPE>')
            
            div_table.replace_with(NavigableString(placeholder_string))

        except Exception as e:
            print(f"[Warning] Could not process nested div-table. Removing it. Error: {e}")
            div_table.decompose()

__all__ = [name for name in globals() if not name.startswith("__")]
