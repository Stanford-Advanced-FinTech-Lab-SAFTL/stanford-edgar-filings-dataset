from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.multimarkdown.multimarkdown import ORDER_I, ORDER_II, reorder
from stanford_edgar_parser.parsers.html.table_cleaning import md_table_2row_header
from stanford_edgar_parser.utils.bootstrap import BeautifulSoup, pd, re, textwrap

def _unsplit_numbers(text: str) -> str:
    from stanford_edgar_parser.parsers.html.preprocessing import _unsplit_numbers as _impl

    return _impl(text)


def _form4_header_details_block(xml: BeautifulSoup, owner_node: BeautifulSoup, footnotes_map: dict) -> str:
    """
    Creates the full header section for a Form 4 as a single, unified
    Markdown table to match the original form's visual layout.
    """
    issuer_node = xml.find("issuer")
    rel_node = owner_node.find("reportingOwnerRelationship")

    owner_name = get_value_with_footnote(owner_node, r"rptOwnerName", footnotes_map)
    addr_node = owner_node.find("reportingOwnerAddress")
    street1 = get_value_with_footnote(addr_node, "rptOwnerStreet1", footnotes_map) or ""
    street2 = get_value_with_footnote(addr_node, "rptOwnerStreet2", footnotes_map) or ""
    full_street = f"{street1}<br>{street2}" if street2 else street1
    city = get_value_with_footnote(addr_node, "rptOwnerCity", footnotes_map) or ""
    state = get_value_with_footnote(addr_node, "rptOwnerState", footnotes_map) or ""
    zip_code = get_value_with_footnote(addr_node, "rptOwnerZipCode", footnotes_map) or ""
    box1_html = (
        "**1. Name and Address of Reporting Person**<sup>*</sup><br><br>"
        f"{owner_name}<br><sub>(Last) (First) (Middle)</sub><br><br>"
        f"{full_street}<br><sub>(Street)</sub><br><br>"
        f"{city}, {state} {zip_code}<br><sub>(City) (State) (Zip)</sub>"
    )

    issuer_name = get_value_with_footnote(issuer_node, r"issuerName", footnotes_map)
    issuer_symbol = get_value_with_footnote(issuer_node, r"issuerTradingSymbol", footnotes_map)
    box2_html = f"**2. Issuer Name and Ticker or Trading Symbol**<br><br>{issuer_name} [ {issuer_symbol} ]"

    date_val = (get_value_with_footnote(xml, r'dateOfEarliestTransaction', footnotes_map) or 
                get_value_with_footnote(xml, r'periodOfReport', footnotes_map))
    box3_html = f"**3. Date of Earliest Transaction (Month/Day/Year)**<br><br>{date_val or ' '}"

    amendment_date = get_value_with_footnote(xml, r'amendmentDate', footnotes_map)
    box4_html = f"**4. If Amendment, Date of Original Filed (Month/Day/Year)**<br><br>{amendment_date or ' '}"

    def is_checked(node, tag_name):
        tag = node.find(re.compile(tag_name, re.I))
        return tag and tag.text.strip().lower() in ("1", "true", "x")
    
    title = (get_value_with_footnote(rel_node, r"officerTitle", footnotes_map) or
             get_value_with_footnote(rel_node, r"otherText", footnotes_map) or
             " ")
    
    box5_html = (
        "**5. Relationship of Reporting Person(s) to Issuer**<br>"
        "(Check all applicable)<br><br>"
        f"[{'X' if is_checked(rel_node, 'isDirector') else ' '}] Director [{'X' if is_checked(rel_node, 'isTenPercentOwner') else ' '}] 10% Owner<br>"
        f"[{'X' if is_checked(rel_node, 'isOfficer') else ' '}] Officer (give title below) [{'X' if is_checked(rel_node, 'isOther') else ' '}] Other (specify below)<br><br>"
        f"_{title}_"
    )

    is_single = len(xml.find_all("reportingOwner")) == 1
    box6_html = (
        "**6. Individual or Joint/Group Filing (Check Applicable Line)**<br><br>"
        f"[{'X' if is_single else ' '}] Form filed by One Reporting Person<br>"
        f"[{' ' if is_single else 'X'}] Form filed by More than One Reporting Person"
    )

    header = "| | | |\n|:---|:---|:---|"
    row1 = f"| {box1_html} | {box3_html} | {box5_html} |"
    row2 = f"| {box2_html} | {box4_html} | {box6_html} |"

    return f"{header}\n{row1}\n{row2}"

def get_value_with_footnote(node_to_search, tag_name_or_regex, fn_map):
    """
    Extracts a value from a tag and robustly associates it with footnotes,
    regardless of whether they are children or siblings of the value tag.
    """
    if not node_to_search:
        return ""
    
    value_node = node_to_search.find(re.compile(tag_name_or_regex, re.I))
    if not value_node:
        return ""

    temp_node = BeautifulSoup(str(value_node), 'lxml-xml').find()
    if temp_node:
        for fn_tag in temp_node.find_all('footnoteId'):
            fn_tag.decompose()
        val_container = temp_node.find('value') or temp_node
        val = val_container.get_text(separator=' ', strip=True)
    else:
        val = ""

    fid_nodes = value_node.find_all('footnoteId', recursive=False)
    
    if not fid_nodes:
        fid_nodes = value_node.find_next_siblings('footnoteId') + value_node.find_previous_siblings('footnoteId')
        
    if fid_nodes:
        fn_markers = "".join(fn_map.get(fid.get("id"), "") for fid in fid_nodes)
        if not val or val == "—":
            return fn_markers
        return f"{val}{fn_markers}"
        
    return val

def format_footnotes_in_text(text):
    """
    Finds all consecutive footnote markers like (1) or (2)(3) in a string
    and wraps them in <sup> tags.
    """
    if not isinstance(text, str):
        return text
    return re.sub(r'((?:\(\d+\))+)', r'<sup>\1</sup>', text)

_DOLLAR_RE = re.compile(r'^-?\d+(\.\d+)?$')
def _dollarize_if_number(val: str) -> str:
    """Add a leading ‘$’ when *val* is a plain number (no parens, no footnotes)."""
    if val == "":
        return val
    if val and _DOLLAR_RE.match(val):
        return f"${val}"
    elif val[0] == ".":
        return f"$0{val}"
    return val

def _is_voluntary(code: str, tl_value: str) -> bool:
    return (code == 'V') or bool(tl_value and tl_value.strip())

def parse_form4_xml(soup, doc_type="4") -> str:
    xml = soup

    not_subject_flag = xml.find('notSubjectToSection16')
    checkbox = f"[{'x' if not_subject_flag and not_subject_flag.text.strip() in ['1', 'true', 'Y'] else ' '}]"
    
    aff10b5_one_flag = xml.find('aff10b5One')
    checkbox2 = f"[{'x' if aff10b5_one_flag and aff10b5_one_flag.text.strip() in ['1', 'true', 'Y'] else ' '}]"
    checkbox2_text = "Check this box to indicate that a transaction was made pursuant to a contract, instruction or written plan for the purchase or sale of equity securities of the issuer that is intended to satisfy the affirmative defense conditions of Rule 10b5-1(c). See Instruction 10."


    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        f"## FORM {doc_type}\n\n"
        "### STATEMENT OF CHANGES IN BENEFICIAL OWNERSHIP\n",
        f"{checkbox} Check this box if no longer subject to Section 16. Form 4 or Form 5 obligations may continue. See Instruction 1(b).",
        f"\n{checkbox2} {checkbox2_text}"
    ]

    fn_map, fn_txt = {}, []
    if (fsec := xml.find("footnotes")):
        fns = fsec.find_all("footnote")
        valid_fns = [f for f in fns if f.has_attr('id')]
        fn_map = {f["id"]: f"({i+1})" for i, f in enumerate(valid_fns)}
        fn_txt = [f"({i+1}) {f.text.strip()}" for i, f in enumerate(valid_fns)]

    for owner_node in xml.find_all("reportingOwner"):
        parts.append(f"\n---\n{_form4_header_details_block(xml, owner_node, fn_map)}\n---")

    t1_tag = xml.find(re.compile(r"nonDerivativeTable", re.I))
    search_context_t1 = t1_tag if t1_tag else xml
    non_derivative_rows = search_context_t1.find_all(re.compile(r"nonDerivative(Transaction|Holding|Security)", re.I))

    if non_derivative_rows:
        rows_data = []
        for r in non_derivative_rows:
            row = {}
            amounts   = r.find(re.compile(r'transactionAmounts', re.I))
            coding    = r.find(re.compile(r'transactionCoding',  re.I))
            post      = r.find(re.compile(r'postTransactionAmounts', re.I))
            ownership = r.find(re.compile(r'ownershipNature', re.I))

            row["1. Title of Security##ROWSPAN_1##<br>1. Title of Security##ROWSPAN_1##"]          = get_value_with_footnote(r, r'securityTitle', fn_map)
            row["2. Transaction Date##ROWSPAN_2##<br>2. Transaction Date##ROWSPAN_2##"]           = get_value_with_footnote(r, r'transactionDate', fn_map)
            row["2A. Deemed Execution Date##ROWSPAN_3##<br>2A. Deemed Execution Date##ROWSPAN_3##"]     = get_value_with_footnote(r, r'deemedExecutionDate', fn_map)

            code          = get_value_with_footnote(coding, r'transactionCode', fn_map)
            tl_value      = get_value_with_footnote(r,      r'transactionTimeliness', fn_map)
            row["3. Transaction Code (V)##COLSPAN_1##<br>Code"] = code
            row["3. Transaction Code (V)##COLSPAN_1##<br>V"]    = "V" if _is_voluntary(code, tl_value) else ""

            price           = _dollarize_if_number(
                                  get_value_with_footnote(amounts, r'transactionPricePerShare', fn_map)
                              )
            shares          = get_value_with_footnote(amounts, r'transactionShares', fn_map)
            acq_disp_code   = get_value_with_footnote(amounts, r'transactionAcquiredDisposedCode', fn_map)

            row["4. Securities Acquired (A) or Disposed of (D)##COLSPAN_2##<br>Amount"]     = shares
            row["4. Securities Acquired (A) or Disposed of (D)##COLSPAN_2##<br>(A) or (D)"] = acq_disp_code
            row["4. Securities Acquired (A) or Disposed of (D)##COLSPAN_2##<br>Price"]      = price

            row["5. Amount of Securities Beneficially Owned##ROWSPAN_4##<br>5. Amount of Securities Beneficially Owned##ROWSPAN_4##"] = get_value_with_footnote(post, r'sharesOwnedFollowingTransaction', fn_map)
            row["6. Ownership Form##ROWSPAN_5##<br>6. Ownership Form##ROWSPAN_5##"]                          = get_value_with_footnote(ownership, r'directOrIndirectOwnership', fn_map)
            row["7. Nature of Indirect Beneficial Ownership##ROWSPAN_6##<br>7. Nature of Indirect Beneficial Ownership##ROWSPAN_6##"] = get_value_with_footnote(ownership, r'natureOfOwnership', fn_map)
            rows_data.append(row)

        df1 = pd.DataFrame(rows_data).fillna('')
        df1 = df1.applymap(_unsplit_numbers)
        df1 = df1.applymap(format_footnotes_in_text)
        parts.append("\n## Table I - Non-Derivative Securities\n")
        parts.append(f"---\n{md_table_2row_header(reorder(df1, ORDER_I))}\n---")
    else:
        parts.extend([
            "\n## Table I - Non-Derivative Securities\n\n---\n",
            md_table_2row_header(
                pd.DataFrame([['—'] * len(ORDER_I)], columns=ORDER_I)
            ),
            "---\n"
        ])

    t2_tag = xml.find(re.compile(r'^derivativeTable$', re.I))
    search_context_t2 = t2_tag if t2_tag else xml
    derivative_rows = search_context_t2.find_all(re.compile(r'^derivative(Transaction|Holding|Security)$', re.I))

    if derivative_rows:
        rows_data_2 = []
        for r in derivative_rows:
            row = {}
            amounts    = r.find(re.compile(r'transactionAmounts', re.I))
            coding     = r.find(re.compile(r'transactionCoding',  re.I))
            underlying = r.find(re.compile(r'underlyingSecurity', re.I))
            post       = r.find(re.compile(r'postTransactionAmounts', re.I))
            ownership  = r.find(re.compile(r'ownershipNature', re.I))

            row["1. Title of Derivative Security##ROWSPAN_7##<br>1. Title of Derivative Security##ROWSPAN_7##"]      = get_value_with_footnote(r, r'securityTitle', fn_map)
            row["2. Conversion or Exercise Price##ROWSPAN_8##<br>2. Conversion or Exercise Price##ROWSPAN_8##"]      = _dollarize_if_number(get_value_with_footnote(r, r'conversionOrExercisePrice', fn_map))
            row["3. Transaction Date##ROWSPAN_9##<br>3. Transaction Date##ROWSPAN_9##"]                  = get_value_with_footnote(r, r'transactionDate', fn_map)
            row["3A. Deemed Execution Date##ROWSPAN_10##<br>3A. Deemed Execution Date##ROWSPAN_10##"]            = get_value_with_footnote(r, r'deemedExecutionDate', fn_map)

            code          = get_value_with_footnote(coding, r'transactionCode', fn_map)
            tl_value      = get_value_with_footnote(r,      r'transactionTimeliness', fn_map)
            row["4. Transaction Code (V)##COLSPAN_3##<br>Code"] = code
            row["4. Transaction Code (V)##COLSPAN_3##<br>V"]    = "V" if _is_voluntary(code, tl_value) else ""

            shares = get_value_with_footnote(amounts, r'transactionShares', fn_map)
            acq_disp_code = get_value_with_footnote(amounts, r'transactionAcquiredDisposedCode', fn_map)
            
            row["5. Number of Derivative Securities Acquired (A) or Disposed of (D)##COLSPAN_4##<br>(A)"] = ""
            row["5. Number of Derivative Securities Acquired (A) or Disposed of (D)##COLSPAN_4##<br>(D)"] = ""
            
            if acq_disp_code.strip().upper() == 'A':
                row["5. Number of Derivative Securities Acquired (A) or Disposed of (D)##COLSPAN_4##<br>(A)"] = shares
            elif acq_disp_code.strip().upper() == 'D':
                row["5. Number of Derivative Securities Acquired (A) or Disposed of (D)##COLSPAN_4##<br>(D)"] = shares


            row["6. Date Exercisable and Expiration Date##COLSPAN_5##<br>Date Exercisable"] = get_value_with_footnote(r, r'exerciseDate', fn_map)
            row["6. Date Exercisable and Expiration Date##COLSPAN_5##<br>Expiration Date"]  = get_value_with_footnote(r, r'expirationDate', fn_map)

            row["7. Title and Amount of Underlying Securities##COLSPAN_6##<br>Title"]               = get_value_with_footnote(underlying, r'underlyingSecurityTitle', fn_map)
            row["7. Title and Amount of Underlying Securities##COLSPAN_6##<br>Amount or Number of Shares"] = get_value_with_footnote(underlying, r'underlyingSecurityShares', fn_map)
            
            price_value = (get_value_with_footnote(r, r'derivativeSecurityPrice', fn_map) or
                           get_value_with_footnote(amounts, r'transactionPricePerShare', fn_map) or
                           get_value_with_footnote(amounts, r'transactionValue', fn_map))

            row["8. Price of Derivative Security##ROWSPAN_11##<br>8. Price of Derivative Security##ROWSPAN_11##"] = _dollarize_if_number(price_value)

            row["9. Number of Derivative Securities Beneficially Owned##ROWSPAN_12##<br>9. Number of Derivative Securities Beneficially Owned##ROWSPAN_12##"] = get_value_with_footnote(post, r'sharesOwnedFollowingTransaction', fn_map)
            row["10. Ownership Form##ROWSPAN_13##<br>10. Ownership Form##ROWSPAN_13##"]                                    = get_value_with_footnote(ownership, r'directOrIndirectOwnership', fn_map)
            row["11. Nature of Indirect Beneficial Ownership##ROWSPAN_14##<br>11. Nature of Indirect Beneficial Ownership##ROWSPAN_14##"]           = get_value_with_footnote(ownership, r'natureOfOwnership', fn_map)
            rows_data_2.append(row)

        df2 = pd.DataFrame(rows_data_2).fillna('')
        df2 = df2.applymap(_unsplit_numbers)
        df2 = df2.applymap(format_footnotes_in_text)
        parts.append("## Table II - Derivative Securities\n\n---\n")
        parts.append(md_table_2row_header(reorder(df2, ORDER_II)))
        parts.append("---\n")
    else:
        parts.extend([
            "## Table II - Derivative Securities\n\n---\n",
            md_table_2row_header(
                pd.DataFrame([['—'] * len(ORDER_II)], columns=ORDER_II)
            ),
            "---\n"
        ])

    if fn_txt:
        parts.append("\n### Footnotes:")
        parts.extend(fn_txt)

    if (remarks_node := xml.find('remarks')) and (remarks_text := remarks_node.get_text(strip=True)):
        parts.append(f"\n**Remarks:**\n{remarks_text}")
        
    for sig in xml.find_all(re.compile(r"ownerSignature", re.I)):
        name = get_value_with_footnote(sig, r"signatureName", fn_map)
        date = get_value_with_footnote(sig, r"signatureDate", fn_map)
        parts.append(f"\n**Signature:** {name or '—'}  \n**Date:** {date or '—'}")

    boilerplate_footer = """
    ### Remarks:

    Reminder: Report on a separate line for each class of securities beneficially owned directly or indirectly.

    * If the form is filed by more than one reporting person, see Instruction 4 (b)(v).

    ** Intentional misstatements or omissions of facts constitute Federal Criminal Violations See 18 U.S.C. 1001 and 15 U.S.C. 78ff(a).

    Note: File three copies of this Form, one of which must be manually signed. If space is insufficient, see Instruction 6 for procedure.

    **Persons who respond to the collection of information contained in this form are not required to respond unless the form displays a currently valid OMB Number.**
    """
    parts.append(textwrap.dedent(boilerplate_footer).strip())
    
    return "\n\n".join(parts).replace(".0000", "")

__all__ = [name for name in globals() if not name.startswith("__")]
