from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.multimarkdown.multimarkdown import ITEM_HEADING, check_timeout
from stanford_edgar_parser.parsers.html.preprocessing import (
    BULLET_CHARS,
    _calculate_effective_indent,
    _fix_escaped_malformed_font_tag,
    _flatten_redundant_nesting,
    _normalize_list_indentation,
    colspan_rowspan_tag,
    convert_margin_layout_to_table,
    convert_nested_div_table_to_placeholders,
    convert_styled_inline_divs_to_spans,
    convert_styled_superscripts_to_placeholders,
    convert_vertical_align_superscripts,
    defragment_adjacent_tags,
    fix_inverted_bold_paragraphs,
    fix_malformed_inline_paragraphs,
    handle_list_like_table_with_indentation,
    handle_sentence_fragment_table,
    handle_width_indented_list_table,
    is_document_layout_positioned,
    parse_html_via_pdf_render,
    parse_positioned_html_islands_via_ocr,
    pre_fix_document_structure,
    process_anchor_tags,
    process_inline_tags,
    promote_styled_headings,
    protect_numeric_list_items,
    protect_special_chars_in_tables,
    tag_border_cells,
)
from stanford_edgar_parser.parsers.html.table_cleaning import (
    clean_financial_df,
    convert_wingdings_boxes,
    df_to_markdown,
    drop_active_colspan_empty_cols,
    drop_tag_only_rows_cols,
    merge_whitespace_tags,
    normalize_dl_lists,
    remove_empty_bold_tags,
    unwrap_fragmenting_tags,
)
from stanford_edgar_parser.parsers.xml.regulatory_forms import parse_any_xml
from stanford_edgar_parser.utils.bootstrap import (
    BeautifulSoup,
    Config,
    Comment,
    Declaration,
    Doctype,
    NavigableString,
    Optional,
    ProcessingInstruction,
    io,
    np,
    pathlib,
    pd,
    re,
    textwrap,
    time,
)
from stanford_edgar_parser.utils.tokenizer import _debug_print

NON_VISIBLE_STRING_NODES = (Comment, Declaration, Doctype, ProcessingInstruction)


def _drop_non_visible_string_nodes(soup: BeautifulSoup) -> None:
    for node in soup.find_all(string=lambda s: isinstance(s, NON_VISIBLE_STRING_NODES)):
        node.extract()


def _format_image_placeholder(img) -> str:
    attrs = []
    for attr in ("src", "alt", "title", "width", "height"):
        value = img.get(attr)
        if not value:
            continue
        if isinstance(value, list):
            value = " ".join(str(part) for part in value)
        value = re.sub(r"\s+", " ", str(value)).strip().replace("`", "'")
        if value:
            attrs.append(f'{attr}="{value}"')
    tag = "<img" + (f" {' '.join(attrs)}" if attrs else "") + ">"
    return f"[IMAGE PLACEHOLDER: `{tag}`]"


def _apply_structural_table_indentation(table) -> None:
    """Preserve row-label indentation encoded as empty leading table cells."""
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 3:
            continue

        leading_span = 0
        target_idx = None
        target = None
        for idx, cell in enumerate(cells):
            text = cell.get_text(" ", strip=True).replace("\xa0", "").strip()
            if text:
                target_idx = idx
                target = cell
                break
            try:
                leading_span += max(1, int(cell.get("colspan", 1)))
            except (TypeError, ValueError):
                leading_span += 1

        if target is None or target_idx is None or leading_span <= 0:
            continue

        style = (target.get("style") or "").lower().replace(" ", "")
        if "text-align:left" not in style:
            continue

        later_has_value = any(
            cell.get_text(" ", strip=True).replace("\xa0", "").strip()
            for cell in cells[target_idx + 1 :]
        )
        if not later_has_value:
            continue

        target_text = target.get_text(" ", strip=True).replace("\xa0", "").strip()
        if re.fullmatch(r"[$%()0-9,.\-\s]+", target_text):
            continue
        if target_text.startswith("##INDENT##") or target_text.startswith("&nbsp;"):
            continue

        target.insert(0, NavigableString("##INDENT##"))


def parse_html_filing(html_content: str, form_type: str = "", file_path: Optional[pathlib.Path] = None) -> str:
    """
    Parses HTML content into Markdown using an intelligent text assembly method.
    """
    _state.LAST_POSITIONED_HTML_OCR_PAGE_COUNT = 0
    start_time = time.time()
    time_limit_s = Config.HTML_TIMEOUT_LIMIT * 60
    timed_out = False

    count = 0

    r_tag_pattern = re.compile(
        r'(?:<|\\>)\s*/?\s*\bR\b\s*(?:>|\\<|<)',
        re.IGNORECASE
    )
    html_content = r_tag_pattern.sub('', html_content)
    html_content = re.sub(r'</?ix:\w+.*?>', '', html_content, flags=re.IGNORECASE)

    indent_preservation_pattern = re.compile(
        r'((?:&nbsp;|\s)+)(<(?:b|strong|i|em|u)\b[^>]*>)',
        re.IGNORECASE
    )
    html_content = indent_preservation_pattern.sub(r'\2\1', html_content)

    html_content = re.sub(r'<sup\b[^>]*>', '##SUP##', html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'</sup\s*>', '##/SUP##', html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'<sub\b[^>]*>', '##SUB##', html_content, flags=re.IGNORECASE)
    html_content = re.sub(r'</sub\s*>', '##/SUB##', html_content, flags=re.IGNORECASE)
    html_content = html_content.replace("|", r"\|").replace("<br>", "##NEWLINE##").replace("<BR>", "##NEWLINE##")
    
    html_content = re.sub(r'<!--.*?-->', '', html_content, flags=re.DOTALL)

    html_content = fix_malformed_inline_paragraphs(html_content)

    u_tag_whitespace_pattern = re.compile(
        r'(<u\b[^>]*>)(\s* \s*)(</u>)',
        re.IGNORECASE
    )

    html_content = u_tag_whitespace_pattern.sub(r'\1##SPACE##\3', html_content)

    i_tag_whitespace_pattern = re.compile(
        r'(<i\b[^>]*>)(\s* \s*)(</i>)',
        re.IGNORECASE
    )

    html_content = i_tag_whitespace_pattern.sub(r'\1##I_SPACE##\3', html_content)
    
    try:
        soup = BeautifulSoup(html_content, "lxml")
    except ValueError as e:
        if "not enough values to unpack" in str(e):
            print(f"[Warning] lxml parser crashed on malformed attributes. Falling back to html.parser.")
            soup = BeautifulSoup(html_content, "html.parser")
        else:
            raise

    _drop_non_visible_string_nodes(soup)

    convert_nested_div_table_to_placeholders(soup)

    _flatten_redundant_nesting(soup)

    convert_margin_layout_to_table(soup)

    fix_inverted_bold_paragraphs(soup)

    if is_document_layout_positioned(soup):
        file_name = file_path.name if file_path else "unknown_file.html"
        print(f"--> Detected positioned layout for '{file_name}'. Routing to OCR-based parser.")
        return parse_html_via_pdf_render(html_content, file_name), True

    final_markdown_from_ocr, is_fully_processed = parse_positioned_html_islands_via_ocr(soup)

    if is_fully_processed:
        print("--> Document fully processed by island parser. Bypassing standard HTML parsing.")
        return final_markdown_from_ocr, True
    
    convert_wingdings_boxes(soup)

    convert_vertical_align_superscripts(soup)

    convert_styled_inline_divs_to_spans(soup)

    soup = pre_fix_document_structure(soup)

    unwrap_fragmenting_tags(soup)

    for block_tag in soup.find_all(['div', 'p']):
        
        first_text_node = block_tag.find(string=True)

        if first_text_node and block_tag.get_text(strip=True).startswith(first_text_node.strip()):
            text = str(first_text_node)
            leading_ws_match = re.match(r'^([\s\u00A0\u2003]+)', text)

            if leading_ws_match:
                ws_string = leading_ws_match.group(1)
                indent_level = 0
                for char in ws_string:
                    if char in ['\u00A0', ' ']:
                        indent_level += 0.5
                    elif char == '\u2003':
                        indent_level += 2

                final_indent_level = int(indent_level)
                if final_indent_level > 0:
                    indent_prefix = '##INDENT##' * final_indent_level
                    
                    block_tag.insert(0, NavigableString(indent_prefix))
                    
                    first_text_node.replace_with(text.lstrip(' \u00A0\u2003'))

    _normalize_list_indentation(soup)

    convert_styled_superscripts_to_placeholders(soup)

    promote_styled_headings(soup)

    _debug_print("→ stage 0 (raw):", len(soup.find_all("table")))
    timeout_check = check_timeout(start_time, time_limit_s, "HTML pre-processing Stage 0-1")
    if timeout_check is not None:
        return timeout_check, True

    text_nodes = soup.find_all(string=True)
    _debug_print(f"→ stage 0.1 (normalizing text nodes): {len(text_nodes):,}")
    for idx, text_node in enumerate(text_nodes, start=1):
        if idx % 100000 == 0:
            _debug_print(f"→ stage 0.1 progress: {idx:,}/{len(text_nodes):,} text nodes")
        s = str(text_node)
        s = s.replace('\u00A0', ' ')
        for z in ['\u200B', '\u200C', '\u200D', '\u2060', '\u2063', '\uFEFF']:
            s = s.replace(z, '')
        

        text_node.replace_with(s)

    title_tag = soup.find('title')
    title_text = title_tag.text if title_tag else ''
    is_form4 = 'Form 4' in title_text or 'form 4' in html_content[:1000].lower()
    is_legacy = bool(soup.find(string=re.compile(r'statement of changes in beneficial ownership', re.I)))
    is_modern_xml = bool(soup.find('ownershipDocument'))

    def _style_declares_bold(style: str) -> bool:
        if not style:
            return False

        style_lc = style.lower()
        style_compact = style_lc.replace(' ', '')

        if any(token in style_compact for token in (
            'font-weight:bold',
            'font-weight:700',
            'font-weight:800',
            'font-weight:900',
        )):
            return True

        font_decl_match = re.search(r'font\s*:\s*([^;]+)', style_lc)
        if font_decl_match and re.search(r'(^|[\s/])(?:bold|700|800|900)(?=$|[\s/])', font_decl_match.group(1)):
            return True

        return False

    styled_rows = soup.find_all('tr', style=True)
    _debug_print(f"→ stage 0.2 (propagating row styles): {len(styled_rows):,}")
    for idx, tr in enumerate(styled_rows, start=1):
        if idx % 25000 == 0:
            _debug_print(f"→ stage 0.2 progress: {idx:,}/{len(styled_rows):,} styled rows")
        row_style = tr.get('style', '')
        row_style_lc = row_style.lower().replace(' ', '')
        inherited_bits = []

        if _style_declares_bold(row_style):
            inherited_bits.append('font-weight:bold')
        if 'font-style:italic' in row_style_lc:
            inherited_bits.append('font-style:italic')
        if 'text-decoration:underline' in row_style_lc:
            inherited_bits.append('text-decoration:underline')

        if not inherited_bits:
            continue

        for cell in tr.find_all(['td', 'th'], recursive=False):
            cell_style = cell.get('style', '')
            cell_style_lc = cell_style.lower().replace(' ', '')
            additions = []

            for bit in inherited_bits:
                if bit.startswith('font-weight:'):
                    if 'font-weight:' not in cell_style_lc:
                        additions.append(bit)
                elif bit.startswith('font-style:'):
                    if 'font-style:' not in cell_style_lc:
                        additions.append(bit)
                elif bit.startswith('text-decoration:'):
                    if 'text-decoration:' not in cell_style_lc:
                        additions.append(bit)

            if additions:
                merged_style = cell_style.rstrip().rstrip(';')
                if merged_style:
                    merged_style += '; '
                merged_style += '; '.join(additions)
                cell['style'] = merged_style

    styled_tags = soup.find_all(['span', 'font', 'p', 'div', 'td', 'th'], style=True)
    _debug_print(f"→ stage 0.3 (normalizing styled tags): {len(styled_tags):,}")

    for idx, tag in enumerate(styled_tags, start=1):
        if idx % 50000 == 0:
            _debug_print(f"→ stage 0.3 progress: {idx:,}/{len(styled_tags):,} styled tags")
        raw_style_str = tag.get('style', '')
        style_str = raw_style_str.lower().replace(' ', '')

        is_bold = _style_declares_bold(raw_style_str)
        is_italic = 'font-style:italic' in style_str
        is_underline = 'text-decoration:underline' in style_str

        if not (is_bold or is_italic or is_underline):
            continue

        if is_bold and tag.find_parent(['b', 'strong']):
            is_bold = False

        if not (is_bold or is_italic or is_underline):
            if tag.name in ['span', 'font']:
                 tag.unwrap()
            continue
            
        inner_content_holder = soup.new_tag('div')
        for child in list(tag.contents):
            inner_content_holder.append(child.extract())

        if is_underline:
            new_u_tag = soup.new_tag('u')
            new_u_tag.extend(inner_content_holder.contents)
            inner_content_holder.clear()
            inner_content_holder.append(new_u_tag)

        if is_italic:
            new_i_tag = soup.new_tag('i')
            new_i_tag.extend(inner_content_holder.contents)
            inner_content_holder.clear()
            inner_content_holder.append(new_i_tag)

        if is_bold:
            new_b_tag = soup.new_tag('b')
            new_b_tag.extend(inner_content_holder.contents)
            inner_content_holder.clear()
            inner_content_holder.append(new_b_tag)

        tag.clear()
        tag.extend(inner_content_holder.contents)

        if tag.name in ['span', 'font']:
            tag.unwrap()

    _debug_print("→ stage 0.4 (merging whitespace and adjacent inline tags)")
    merge_whitespace_tags(soup)

    remove_empty_bold_tags(soup)


    defragment_adjacent_tags(soup, ['b', 'strong'])
    defragment_adjacent_tags(soup, ['i', 'em'])    
    defragment_adjacent_tags(soup, ['font'])

    _debug_print("→ stage 0.5 (processing inline markers and spans)")
    process_inline_tags(soup, ['b', 'strong'], "BOLD")
    process_inline_tags(soup, ['i', 'em'], "ITALIC")
    process_inline_tags(soup, ['u'], "U")
    process_anchor_tags(soup)

    _debug_print("→ stage 0.6 (tagging colspan/rowspan)")
    colspan_rowspan_tag(soup)

    _debug_print("→ stage 1 (after bold cleanup):", len(soup.find_all("table")))
    timeout_check = check_timeout(start_time, time_limit_s, "HTML pre-processing Stage 1-2")
    if timeout_check is not None:
        return timeout_check, True

    xml_tags = soup.find_all(re.compile(r'^xml$', re.I))
    if xml_tags:
        if soup.body is None or not soup.body.get_text(strip=True):
            return parse_any_xml([t.decode_contents() for t in xml_tags])
        
    _debug_print("→ stage 2 (after wingdings):", len(soup.find_all("table")))
    timeout_check = check_timeout(start_time, time_limit_s, "HTML pre-processing Stage 2-3")
    if timeout_check is not None:
        return timeout_check, True
    
    normalize_dl_lists(soup)
    protect_special_chars_in_tables(soup)

    _debug_print("→ stage 3 (after list-table→li):", len(soup.find_all("table")))
    timeout_check = check_timeout(start_time, time_limit_s, "HTML pre-processing Stage 3-4")
    if timeout_check is not None:
        return timeout_check, True
    
    for tag in soup.find_all(["head", "title", "meta", "base", "xml", "script", "style", "ix:header", "ix:resources"]):
        tag.decompose()
    _drop_non_visible_string_nodes(soup)

    _debug_print("→ stage 4 (after non-visible metadata/xml/script/style/ix):", len(soup.find_all("table")))
    timeout_check = check_timeout(start_time, time_limit_s, "HTML pre-processing Stage 4-5")
    if timeout_check is not None:
        return timeout_check, True
    
    for tag in soup.find_all(attrs={"style": re.compile(r'display:\s*none', re.I)}):
        tag.decompose()

    _debug_print("→ stage 5 (after display:none):", len(soup.find_all("table")))
    timeout_check = check_timeout(start_time, time_limit_s, "HTML pre-processing Stage (final)")
    if timeout_check is not None:
        return timeout_check, True
    
    total_tables = len(soup.find_all("table"))
    next_milestone_pct = 10

    for img in soup.find_all("img"):
        img.replace_with(NavigableString(f"\n\n{_format_image_placeholder(img)}\n\n"))

    sections_md, text_buf = [], []
    BLOCK_TAGS   = {"p", "div"}
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    pending: List[str] = []
    last_emitted = None

    def _emit_pending():
        nonlocal last_emitted
        if pending:
            unique_pending = [p for p in pending if p.lstrip().rstrip() != last_emitted]
            sections_md.extend(unique_pending)
            if unique_pending: last_emitted = unique_pending[-1].lstrip().rstrip()
            pending.clear()

    def flush(prefix: str = ""):

        raw_text = "".join(text_buf)
        
        txt = re.sub(r'\s+', ' ', raw_text).strip()
        
        if txt:
            _emit_pending()
                        
            final_text = re.sub(r'##SUP##(.*?)##/SUP##', r'<sup>\1</sup>', txt)

            if final_text.startswith(('http://', 'https://')):
                sections_md.append(prefix + final_text + "\n\n")
            else:
                sections_md.append(prefix + final_text + "\n\n")

        text_buf.clear()

    def queue(level: int, cand: str, el):
        tag = f"\n{'#' * level} {cand}\n"
        if not pending or pending[-1] != tag:
            pending.append(tag)
        el.clear()

    body_tag = soup.body
    body = body_tag

    if body_tag and body_tag.find(True):
        body = body_tag
    else:
        body = soup

    is_13f_filing = "13F" in form_type.upper()
    skipped_descendant_ids = set()

    def skip_descendants(el):
        if hasattr(el, "descendants"):
            skipped_descendant_ids.update(id(child) for child in el.descendants)

    for elem in list(body.descendants):

        if time.time() - start_time > time_limit_s:
            print(f"[timeout] HTML parsing exceeded {time_limit_s // 60} minutes. Stopping.")
            timed_out = True
            break

        if id(elem) in skipped_descendant_ids:
            continue
        
        if elem.name == 'p' and elem.has_attr('style'):
            style = elem.get('style', '').lower()
            if 'border-bottom' in style and not elem.get_text(strip=True):
                flush()
                sections_md.append("\n\n------\n\n")
                skip_descendants(elem)
                elem.clear()
                continue

        if elem.name == 'div':
            style = elem.get('style', '').lower()
            if 'page-break-after: always' in style and not elem.get_text(strip=True):
                flush()
                sections_md.append("\n\n------\n\n")
                skip_descendants(elem)
                elem.clear()
                continue

        if elem.name in HEADING_TAGS:
            flush()
            raw_cand = elem.get_text(separator=' ', strip=True)
            
            cand = re.sub(r'\s+', ' ', raw_cand).strip()

            if cand and cand != last_emitted:
                lvl = int(elem.name[1])
                tag = f"\n{'#' * lvl} {cand}\n"
                sections_md.append(tag)
                last_emitted = cand
            skip_descendants(elem)
            elem.clear()
            continue

        if elem.name == "li":
            flush()
            li_text = elem.get_text()

            if li_text:
                if li_text.lstrip().startswith(tuple(BULLET_CHARS)):
                    sections_md.append("\n" + li_text)
                else:
                    sections_md.append("* " + li_text)
            
            sections_md.append("\n\n")

            skip_descendants(elem)
            elem.clear()
            continue

        if elem.name in BLOCK_TAGS or elem.name == "br":
            flush()
            continue

        if elem.name == "hr":
            flush()
            sections_md.append("\n\n------\n\n")
            skip_descendants(elem)
            elem.clear()
            continue

        if elem.name == "pre":
            flush()
            pre_text = elem.get_text()
            if pre_text:
                sections_md.append(pre_text)
            skip_descendants(elem)
            elem.clear()
            continue

        if elem.name == "table":
            count += 1
            current_pct = (count / total_tables) * 100

            if current_pct >= next_milestone_pct and total_tables > 10:
                milestone_to_print = int(next_milestone_pct)
                print(f"-> Processing tables... {milestone_to_print}% complete ({count} of {total_tables})")
                
                next_milestone_pct += 10.0
            flush()
            skip_descendants(elem)

            if handle_width_indented_list_table(elem, sections_md):
                skip_descendants(elem)
                elem.clear()
                continue

            if handle_list_like_table_with_indentation(elem, sections_md):
                skip_descendants(elem)
                elem.clear()
                continue

            if handle_sentence_fragment_table(elem, sections_md):
                skip_descendants(elem)
                elem.clear()
                continue
            
            tag_border_cells(elem, soup)

            DEFAULT_FONT_SIZE_PT = 10.0
            STANDARD_INDENT_EM = 1.2

            for cell in elem.find_all(['td', 'th']):
                elements_to_check = [cell] + cell.find_all(['p', 'div', 'font'])
                
                max_indent_pt = 0.0
                font_size_pt = None

                for el in elements_to_check:
                    indent_info = _calculate_effective_indent(el)
                    if indent_info['indent'] > max_indent_pt:
                        max_indent_pt = indent_info['indent']
                        if indent_info['font_size']:
                            font_size_pt = indent_info['font_size']
                
                if font_size_pt is None:
                    for el in elements_to_check:
                        indent_info = _calculate_effective_indent(el)
                        if indent_info['font_size']:
                            font_size_pt = indent_info['font_size']
                            break

                effective_font_size = font_size_pt or DEFAULT_FONT_SIZE_PT

                if max_indent_pt > 0 and effective_font_size > 0:
                    indent_em = max_indent_pt / effective_font_size
                    ratio = indent_em / STANDARD_INDENT_EM
                    quantized_level = round(ratio * 4) / 4
                    full_indents = int(quantized_level)
                    remainder = quantized_level - full_indents
                    
                    indent_prefix = ""
                    if full_indents > 0:
                        indent_prefix += '##INDENT##' * full_indents
                    
                    if remainder >= 0.75:
                        indent_prefix += '&nbsp;&nbsp;&nbsp;'
                    elif remainder >= 0.5:
                        indent_prefix += '&nbsp;&nbsp;'

                    if indent_prefix:
                        cell.insert(0, NavigableString(indent_prefix))

            table_text = elem.get_text(separator=' ', strip=True)

            if ITEM_HEADING.match(table_text):
                queue(3, table_text, elem)
                continue
            
            for cell in elem.find_all(['td', 'th']):
                indent_level = 0
                text_indent_level = 0
                elements_to_check = [cell] + cell.find_all(['div', 'p'], recursive=False)
                
                for el in elements_to_check:
                    style = el.get('style', '')
                    if not style: continue
                    
                    pad_match = re.search(r'padding-left\s*:\s*([\d\.]+)(pt|px|em)', style)
                    margin_match = re.search(r'margin-left\s*:\s*([\d\.]+)(pt|px|em)', style)
                    
                    total_offset_pt = 0.0

                    if pad_match:
                        val, unit = float(pad_match.group(1)), pad_match.group(2)
                        if unit == 'em': total_offset_pt += val * 10.0
                        elif unit == 'px': total_offset_pt += val * 0.75
                        else: total_offset_pt += val

                    if margin_match:
                        val, unit = float(margin_match.group(1)), margin_match.group(2)
                        if unit == 'em': total_offset_pt += val * 10.0
                        elif unit == 'px': total_offset_pt += val * 0.75
                        else: total_offset_pt += val

                    if total_offset_pt > 0:
                        level = int(round(total_offset_pt / 5.0))
                        if level > 0:
                            indent_level = level
                            break

                first_visible_text = None
                for descendant in cell.descendants:
                    if not isinstance(descendant, NavigableString):
                        continue
                    descendant_text = str(descendant)
                    if descendant_text.strip(' \t\r\n\u00A0\u2003'):
                        first_visible_text = descendant_text
                        break

                if first_visible_text:
                    leading_ws_match = re.match(r'^([\s\u00A0\u2003]+)', first_visible_text)
                    if leading_ws_match:
                        ws_string = leading_ws_match.group(1)
                        ws_string = re.sub(r'[\r\n]+[ \t\f\v]*', '', ws_string)
                        indent_units = 0.0
                        for char in ws_string:
                            if char in ['\u00A0', ' ']:
                                indent_units += 0.5
                            elif char == '\u2003':
                                indent_units += 2.0
                        text_indent_level = int(indent_units)
                        indent_level = max(indent_level, text_indent_level)

                for br in cell.find_all('br'):
                    br.replace_with('##NEWLINE##')
                for p in cell.find_all(['p', 'div']):
                    p.append('##NEWLINE##')
                
                cell_text = re.sub(r'(?<=[A-Za-z0-9])-\s+', '- ', cell.get_text(strip=False).replace("**", "").replace("** ", ""))
                if text_indent_level > 0:
                    cell_text = re.sub(r'^[\s\u00A0\u2003]+', '', cell_text)
                if cell_text.strip() == "##NEWLINE##":
                    cell_text = ""
                
                IND = "\u2063"
                if text_indent_level > 0 and not re.match(r'^(?:##INDENT##|&nbsp;)+', cell_text):
                    cell_text = ('##INDENT##' * text_indent_level) + cell_text
                elif indent_level > 0:
                    cell_text = IND * indent_level + cell_text

                cell.clear()
                cell.string = cell_text

            for cell in elem.find_all(['td', 'th']):
                if cell.get('colspan'):
                    try:
                        if int(cell['colspan']) > 500:
                            del cell['colspan']
                    except (ValueError, TypeError):
                        del cell['colspan']

            for tr in elem.find_all('tr'):
                if not tr.find(['td', 'th']):
                    tr.decompose()

            _apply_structural_table_indentation(elem)

            table_html = str(elem)
            table_html = protect_numeric_list_items(table_html)

            table_html = _fix_escaped_malformed_font_tag(table_html)
            
            try:
                df_from_html = pd.read_html(io.StringIO(table_html), flavor="lxml", keep_default_na=False, na_values=[""])[0]

                df_from_html.replace(to_replace=r'##PROTECT_(.*?)##', value=r'\1', regex=True, inplace=True)
                            
                df_from_html = df_from_html.replace({'##VISUAL_BORDER##': '<BORDER>'}, regex=False)

                first_real_row_idx = 0
                for i, row in df_from_html.iterrows():
                    is_junk = all(
                        str(cell).strip() in ('', 'nan', '<BORDER>', '<BORDER_TOP>', 'NaN') or 'spacer.gif' in str(cell) 
                        for cell in row
                    )
                    if not is_junk:
                        first_real_row_idx = i
                        break
                        
                raw_df = df_from_html.iloc[first_real_row_idx:].reset_index(drop=True)
                
                raw_df = raw_df.replace(r'^\s*(?:&nbsp;)?\s*$', np.nan, regex=True)

                raw_df = (raw_df
                            .dropna(how='all')
                            .dropna(how='all', axis=1)
                            .reset_index(drop=True))

                if not raw_df.empty:
                    for r in range(1, len(raw_df)):
                        for c in range(len(raw_df.columns)):
                            if isinstance(raw_df.iat[r, c], str) and '<BORDER_TOP>' in raw_df.iat[r, c]:
                                above_cell = raw_df.iat[r - 1, c]
                                if pd.isna(above_cell):
                                    raw_df.iat[r - 1, c] = '<BORDER>'
                                else:
                                    raw_df.iat[r - 1, c] = str(above_cell) + '<BORDER>'

                raw_df = raw_df.replace({r'<BORDER_TOP>': '', r'<BORDER_BOTTOM>': '<BORDER>'}, regex=True)

                sup_replacer = lambda x: re.sub(r'##SUP##(.*?)##/SUP##', r'<sup>\1</sup>', str(x)) if '##SUP##' in str(x) else x

                raw_df = raw_df.applymap(sup_replacer)

                raw_df = drop_tag_only_rows_cols(raw_df).reset_index(drop=True)

                raw_df = (
                    raw_df
                    .replace(r' -(?=[A-Za-z])', ' - ', regex=True)
                    .replace(r'\s{2,}', ' ',    regex=True)
                )

                def normalize_for_comparison(val):
                    if isinstance(val, str):
                        text = val.replace('\u2063', '').replace('\u00A0', '')
                        return re.sub(r'\s+', ' ', text).strip()
                    
                    return val

                if not raw_df.empty and raw_df.shape[1] > 1:
                    cols_to_clean = raw_df.columns[1:]
                    raw_df[cols_to_clean] = raw_df[cols_to_clean].applymap(normalize_for_comparison)

                table_text = re.sub(r'##(BOLD_START_\d+|BOLD_END_\d+|U_START_\d+|U_END_\d+|ITALIC_START_\d+|ITALIC_END_\d+|ROWSPAN_\d+|COLSPAN_\d+|LINK_START_\d+__[^#]+|LINK_END_\d+)##', '', table_text)
                table_text = table_text.replace('##NEWLINE##', '').replace('<br>', '').strip()

                positives = (
                    (('$' in table_text or '£' in table_text or '�' in table_text or " ) " in table_text) and re.search(r'\d', table_text)) or
                    ('%' in table_text or re.search(r'\([\d,]+\)', table_text)) or
                    (len(table_text) > 300 and re.search(r'\d', table_text) and
                    "Part I" not in table_text and
                    "Name of each exchange on which registered" not in table_text and
                    "ITEM 1" not in table_text) or
                    any(k in table_text for k in ["Common stock", "Total", "By:", "Earnings", "##SUP", "##SUB", "marketing", "Period Ended", "Months Ended", "For <BORDER> Against"]) or
                    ((raw_df == ')').any().any())
                )

                exclusions = any(k in table_text for k in ["Emerging growth company", "Smaller reporting company", "[One-month LIBOR   +] __%", "⌧"])

                is_financial_table = positives and not exclusions

                if is_financial_table and not is_13f_filing and not (is_form4 and is_legacy):
                    df_to_render = clean_financial_df(raw_df)
                else:
                    df_to_render = raw_df
                    if "OO" in table_text and "CHECK" in table_text:
                        df_to_render = drop_active_colspan_empty_cols(df_to_render)

                md = df_to_markdown(df_to_render, disable_numparse=True, is_legacy_form4_table1=((is_form4 and is_legacy and "Table I" in df_to_render.to_string())), is_legacy_form4_table2=(is_form4 and is_legacy and "Table II" in df_to_render.to_string()))

                if md and not md.isspace():
                    _emit_pending()
                    
                    if "|:-" in md:
                        sections_md.append(f"\n---\n\n{md}\n\n---\n")
                    else:
                        sections_md.append(f"{md}\n\n")

            except (ValueError, IndexError):
                fallback_text = elem.get_text(separator=' ', strip=False)
                if fallback_text:
                    _emit_pending()
                    sections_md.append(textwrap.fill(fallback_text) + "\n")
            
            skip_descendants(elem)
            elem.clear()
            continue

        if isinstance(elem, NavigableString):
            if not elem.find_parent(HEADING_TAGS.union({"li", "table", "script", "style"})):
                text_buf.append(str(elem))

    flush()
    pending.clear()

    md = "".join(sections_md)
    md = re.sub(
        r"EX-[\d\.]+\s+\d+\s+[\w\.]+\.htm\s+EX-[\d\.]+\s+Document\s+"
        r"created\s+using\s+Wdesk.*?Document",
        "",
        md,
        flags=re.I,
    )

    md = re.sub(r'\n{3,}', '\n\n', md).strip()

    if timed_out:
        return md + "\n\n<PARSING HTML EXCEEDED 15 MINUTES - OUTPUT HAS BEEN TRUNCATED>", False
    else:
        return md, False

def clean_phone_numbers(text: str) -> str:
    """
    Removes newlines from within phone numbers and unifies formatting
    by removing any Markdown bold tags from the number components.
    """
    if not isinstance(text, str):
        return text

    phone_pattern = re.compile(r"""
        \*{0,2}
        (
            \(\s*\d{3}\s*\)
        )
        \*{0,2}

        \s*\n\s*

        \*{0,2}
        (
            \d{3}\s*[-]?\s*\d{4}
        )
        \*{0,2}
    """, re.VERBOSE)

    return phone_pattern.sub(r"\1 \2", text)

__all__ = [name for name in globals() if not name.startswith("__")]
