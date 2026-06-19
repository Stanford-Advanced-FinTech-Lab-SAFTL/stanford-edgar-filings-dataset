from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.multimarkdown.multimarkdown import (
    ORDER_II_FORM3,
    ORDER_I_FORM3,
    SEC_COUNTRY_CODES,
    reorder,
)
from stanford_edgar_parser.parsers.html.table_cleaning import df_to_markdown, md_table_2row_header
from stanford_edgar_parser.parsers.xml.ownership import (
    _dollarize_if_number,
    format_footnotes_in_text,
    get_value_with_footnote,
)
from stanford_edgar_parser.utils.bootstrap import (
    BeautifulSoup,
    html,
    itertools,
    np,
    pd,
    re,
    textwrap,
)

def parse_sec_header(raw_text: str) -> str:
    from stanford_edgar_parser.parsers.html.preprocessing import parse_sec_header as _impl

    return _impl(raw_text)


def to_compact_markdown(df: pd.DataFrame, **kwargs) -> str:
    from stanford_edgar_parser.parsers.html.preprocessing import to_compact_markdown as _impl

    return _impl(df, **kwargs)


def parse_schedule13g_xml(xml: BeautifulSoup) -> str:
    """
    Parses a Schedule 13G filing into structured Markdown, creating a
    valid table structure and correctly rendering all items, checkboxes, and comments for all filers.
    """
    submission = xml.find('edgarSubmission')
    if not submission:
        return "<!-- <edgarSubmission> tag not found in SCHEDULE 13G/A filing -->"

    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def get_multiline_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        if not found or not found.text: return "—"
        lines = [line.strip() for line in found.text.strip().split('\n') if line.strip()]
        return "\n".join(lines)

    header = submission.find('headerData')
    form_data = submission.find('formData')
    cover_page = form_data.find('coverPageHeader')
    issuer_info = cover_page.find('issuerInfo')
    items = form_data.find('items')

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## SCHEDULE 13G\n\n"
        "### Under the Securities Exchange Act of 1934\n"
    ]
    
    amendment_no = get_text(cover_page, 'amendmentNo')
    if amendment_no and amendment_no != "—":
        parts.append(f"**(Amendment No. {amendment_no})**\n")

    parts.append(f"**Issuer:** {get_text(issuer_info, 'issuerName')}")
    parts.append(f"**Title of Class of Securities:** {get_text(cover_page, 'securitiesClassTitle')}")
    parts.append(f"**CUSIP Number:** {get_text(issuer_info, 'issuerCusip')}")
    parts.append(f"**Date of Event Which Requires Filing of this Statement:** {get_text(cover_page, 'eventDateRequiresFilingThisStatement')}")
    
    parts.append("\n**Check the appropriate box to designate the rule pursuant to which this Schedule is filed:**\n")
    filed_rules_nodes = cover_page.find_all(re.compile('^designateRulePursuantThisScheduleFiled$', re.I))
    filed_rules = {node.text.strip() for node in filed_rules_nodes}
    all_rules = ["Rule 13d-1(b)", "Rule 13d-1(c)", "Rule 13d-1(d)"]
    for rule in all_rules:
        checkbox = '[x]' if rule in filed_rules else '[ ]'
        parts.append(f"- {checkbox} {rule}")

    for reporting_person in form_data.find_all('coverPageHeaderReportingPersonDetails'):
        parts.append("\n---\n")
        
        reporting_person_name = get_text(reporting_person, 'reportingPersonName')
        citizenship = get_text(reporting_person, 'citizenshipOrOrganization')
        voting_power = reporting_person.find('reportingPersonBeneficiallyOwnedNumberOfShares')
        sole_voting = get_text(voting_power, 'soleVotingPower')
        shared_voting = get_text(voting_power, 'sharedVotingPower')
        sole_dispositive = get_text(voting_power, 'soleDispositivePower')
        shared_dispositive = get_text(voting_power, 'sharedDispositivePower')
        aggregate_amount = get_text(reporting_person, 'reportingPersonBeneficiallyOwnedAggregateNumberOfShares')
        
        is_aggregate_excluded = get_text(reporting_person, 'aggregateAmountExcludesCertainSharesFlag').upper() == 'Y'
        checkbox_10_val = '[x]' if is_aggregate_excluded else '[ ]'
        
        percent_11_val = get_text(reporting_person, 'classPercent').replace(' ', '')
        
        person_type_nodes = reporting_person.find_all('typeOfReportingPerson')
        person_type = ", ".join(node.text for node in person_type_nodes)
        
        shares_block_text = "Number of<br>Shares<br>Beneficially<br>Owned by<br>Each<br>Reporting<br>Person<br>With##ROWSPAN_1##"

        table_content_1 = f"Names of Reporting Persons<br>{reporting_person_name}##COLSPAN_1##"
        
        group_membership_text = ""
        member_group_node = reporting_person.find('memberGroup')
        if member_group_node:
            status = member_group_node.text.strip().lower()
            checkbox_a = '[x]' if status == 'a' else '[ ]'
            checkbox_b = '[x]' if status == 'b' else '[ ]'
            group_membership_text = f"(a) {checkbox_a} (b) {checkbox_b}"
        else:
             group_membership_text = f"(a) [ ] (b) [ ]"

        table_content_2 = f"Check the Appropriate Box if a Member of a Group (See Instructions)<br>{group_membership_text}##COLSPAN_2##"
        table_content_3 = "SEC Use Only##COLSPAN_3##"
        table_content_4 = f"Citizenship or Place of Organization<br>{citizenship}##COLSPAN_4##"
        table_content_9 = f"Aggregate Amount Beneficially Owned by Each Reporting Person<br>{aggregate_amount}##COLSPAN_9##"
        table_content_10 = f"Check if the Aggregate Amount in Row (9) Excludes Certain Shares (See Instructions) {checkbox_10_val}##COLSPAN_10##"
        table_content_11 = f"Percent of Class Represented by Amount in Row (9)<br>{percent_11_val}%##COLSPAN_11##"
        table_content_12 = f"Type of Reporting Person (See Instructions)<br>{person_type}##COLSPAN_12##"

        header_row = f"| 1. | {table_content_1} | {table_content_1} |"
        separator_row = "|:---|:---|:---|:---|"
        body_rows = [
            f"| 2. | {table_content_2} | {table_content_2} |",
            f"| 3. | {table_content_3} | {table_content_3} |",
            f"| 4. | {table_content_4} | {table_content_4} |",
            f"| {shares_block_text} | 5. | Sole Voting Power<br>{sole_voting} |",
            f"| {shares_block_text} | 6. | Shared Voting Power<br>{shared_voting} |",
            f"| {shares_block_text} | 7. | Sole Dispositive Power<br>{sole_dispositive} |",
            f"| {shares_block_text} | 8. | Shared Dispositive Power<br>{shared_dispositive} |",
            f"| 9. | {table_content_9} | {table_content_9} |",
            f"| 10. | {table_content_10} | {table_content_10} |",
            f"| 11. | {table_content_11} | {table_content_11} |",
            f"| 12. | {table_content_12} | {table_content_12} |"
        ]
        table_md = [header_row, separator_row] + body_rows
        parts.append("\n".join(table_md))
        
        comment_text = get_text(reporting_person, 'comments')
        if comment_text and comment_text != "—":
            parts.append(f"\n**Comment for Type of Reporting Person:** {comment_text}")

    parts.append("\n---\n")
    
    item1 = items.find('item1')
    item2 = items.find('item2')
    item3 = items.find('item3')
    item4 = items.find('item4')
    item5 = items.find('item5')
    item6 = items.find('item6')
    item7 = items.find('item7')
    item8 = items.find('item8')
    item9 = items.find('item9')
    item10 = items.find('item10')

    parts.append(f"**Item 1(a). Name of Issuer:**\n{get_text(item1, 'issuerName')}\n")
    parts.append(f"**Item 1(b). Address of Issuer's Principal Executive Offices:**\n{get_text(item1, 'issuerPrincipalExecutiveOfficeAddress')}\n")
    
    parts.append(f"**Item 2(a). Name of Person Filing:**\n{get_text(item2, 'filingPersonName')}\n")
    parts.append(f"**Item 2(b). Address of Principal Business Office:**\n{get_text(item2, 'principalBusinessOfficeOrResidenceAddress')}\n")
    parts.append(f"**Item 2(c). Citizenship:**\n{get_text(item2, 'citizenship')}\n")
    parts.append(f"**Item 2(d). Title of Class of Securities:**\n{get_text(cover_page, 'securitiesClassTitle')}\n")
    parts.append(f"**Item 2(e). CUSIP Number:**\n{get_text(issuer_info, 'issuerCusip')}\n")

    parts.append("**Item 3. If this statement is filed pursuant to §§ 240.13d-1(b) or 240.13d-2(b) or (c), check whether the person filing is a:**\n")
    
    filer_type_codes = {node.text for node in (item3.find_all('typeOfPersonFiling') if item3 else [])}

    filer_type_map = {
        "BK": "(b)", "BD": "(a)", "IC": "(d)", "IA": "(e)", 
        "HC": "(g)", "EP": "(f)", "SA": "(h)", "CP": "(i)", "CO": "(k)"
    }
    
    item3_options = {
        "(a)": "Broker or dealer registered under section 15 of the Act (15 U.S.C. 78o).",
        "(b)": "Bank as defined in section 3(a)(6) of the Act (15 U.S.C. 78c).",
        "(c)": "Insurance company as defined in section 3(a)(19) of the Act (15 U.S.C. 78c).",
        "(d)": "Investment company registered under section 8 of the Investment Company Act of 1940 (15 U.S.C. 80a-8).",
        "(e)": "An investment adviser in accordance with § 240.13d-1(b)(1)(ii)(E);",
        "(f)": "An employee benefit plan or endowment fund in accordance with § 240.13d-1(b)(1)(ii)(F);",
        "(g)": "A parent holding company or control person in accordance with § 240.13d-1(b)(1)(ii)(G);",
        "(h)": "A savings associations as defined in Section 3(b) of the Federal Deposit Insurance Act (12 U.S.C. 1813);",
        "(i)": "A church plan that is excluded from the definition of an investment company under section 3(c)(14) of the Investment Company Act of 1940 (15 U.S.C. 80a-3);",
        "(j)": "A non-U.S. institution in accordance with § 240.13d-1(b)(1)(ii)(J), if filing as a non-U.S. institution in accordance with § 240.13d-1(b)(1)(ii)(J), please specify the type of institution:",
        "(k)": "Group, in accordance with Rule 240.13d-1(b)(1)(ii)(K)."
    }

    checked_item_letters = {filer_type_map.get(code) for code in filer_type_codes}

    for letter, text in item3_options.items():
        checkbox = '[x]' if letter in checked_item_letters else '[ ]'
        parts.append(f"{letter} {checkbox} {text}")
    
    parts.append(f"\n\n**Item 4. Ownership:**")
    
    amount_owned_text = get_multiline_text(item4, 'amountBeneficiallyOwned')
    parts.append(f"\n**(a) Amount beneficially owned:**\n\n{amount_owned_text}")
    
    percent_val_item4 = get_multiline_text(item4, 'classPercent')
    parts.append(f"\n**(b) Percent of class:**\n\n{percent_val_item4}")
    
    breakdown = item4.find('numberOfSharesPersonHas') if item4 else None
    if breakdown:
        parts.append("\n\n**(c) Number of shares as to which the person has:**")
        parts.append(f"\n**(i) Sole power to vote or to direct the vote:**\n\n{get_multiline_text(breakdown, 'solePowerOrDirectToVote')}")
        parts.append(f"\n**(ii) Shared power to vote or to direct the vote:**\n\n{get_multiline_text(breakdown, 'sharedPowerOrDirectToVote')}")
        parts.append(f"\n**(iii) Sole power to dispose or to direct the disposition of:**\n\n{get_multiline_text(breakdown, 'solePowerOrDirectToDispose')}")
        parts.append(f"\n**(iv) Shared power to dispose or to direct the disposition of:**\n\n{get_multiline_text(breakdown, 'sharedPowerOrDirectToDispose')}\n")
    
    parts.append(f"**Item 5. Ownership of Five Percent or Less of a Class.**\n")
    is_not_applicable_5 = get_text(item5, 'notApplicableFlag').upper() == 'Y'
    checkbox_5 = '[x]' if is_not_applicable_5 else '[ ]'
    parts.append(f"{checkbox_5} If this statement is being filed to report the fact that as of the date hereof the reporting person has ceased to be the beneficial owner of more than five percent of the class of securities, check the following.\n")

    parts.append(f"**Item 6. Ownership of More than 5 Percent on Behalf of Another Person.**\n")
    if get_text(item6, 'notApplicableFlag').upper() != 'Y':
        item6_text = get_text(item6, 'ownershipMoreThan5PercentOnBehalfOfAnotherPerson')
        parts.append(f"{item6_text}\n")
    else:
        parts.append("Not Applicable\n")

    parts.append(f"**Item 7. Identification and Classification of the Subsidiary**\n")
    if get_text(item7, 'notApplicableFlag').upper() != 'Y':
        item7_text = get_text(item7, 'subsidiaryIdentificationAndClassification')
        parts.append(f"{item7_text}\n")
    else:
        parts.append("Not Applicable\n")

    parts.append(f"**Item 8. Identification and Classification of Members of the Group**\n")
    if get_text(item8, 'notApplicableFlag').upper() != 'Y':
        item8_text = get_text(item8, 'identificationAndClassificationOfGroupMembers')
        parts.append(f"{item8_text}\n")
    else:
        parts.append("Not Applicable\n")
    
    parts.append(f"**Item 9. Notice of Dissolution of Group**\n")
    if get_text(item9, 'notApplicableFlag').upper() != 'Y':
        item9_text = get_text(item9, 'dissolutionOfGroupNotice')
        parts.append(f"{item9_text}\n")
    else:
        parts.append("Not Applicable\n")
             
    parts.append(f"\n**Item 10. Certification:**")
    parts.append(f"{get_text(item10, 'certifications')}\n")

    parts.append("\n### SIGNATURE\n")
    
    parts.append("After reasonable inquiry and to the best of my knowledge and belief, I certify that the information set forth in this statement is true, complete and correct.\n")
    
    for sig_info in form_data.find_all('signatureInformation'):
        sig_details = sig_info.find('signatureDetails')
        
        reporting_person = get_text(sig_info, 'reportingPersonName')
        if reporting_person and reporting_person != "—":
            parts.append(f"\n**{reporting_person}**")
            
        parts.append(f"**Date:** {get_text(sig_details, 'date')}")
        parts.append(f"**By:** {get_text(sig_details, 'signature')}")
        parts.append(f"**Name & Title:** {get_text(sig_details, 'title')}")

    return "\n\n".join(parts)

def _form3_header_details_block(xml: BeautifulSoup, owner_node, footnotes_map: dict) -> str:
    """Creates the header block for a Form 3 as a Markdown table."""
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

    event_date_val = get_value_with_footnote(xml, r'periodOfReport', footnotes_map)
    box2_html = f"**2. Date of Event Requiring Statement (Month/Day/Year)**<br><br>{event_date_val or ' '}"

    issuer_name = get_value_with_footnote(issuer_node, r"issuerName", footnotes_map)
    issuer_symbol = get_value_with_footnote(issuer_node, r"issuerTradingSymbol", footnotes_map)
    box3_html = f"**3. Issuer Name and Ticker or Trading Symbol**<br><br>{issuer_name} [ {issuer_symbol} ]"

    def is_checked(node, tag_name):
        tag = node.find(re.compile(tag_name, re.I))
        return tag and tag.text.strip().lower() in ("1", "true", "x")
    title = get_value_with_footnote(rel_node, r"officerTitle", footnotes_map) or " "
    box4_html = (
        "**4. Relationship of Reporting Person(s) to Issuer**<br>"
        "(Check all applicable)<br><br>"
        f"[{'X' if is_checked(rel_node, 'isDirector') else ' '}] Director   [{'X' if is_checked(rel_node, 'isTenPercentOwner') else ' '}] 10% Owner<br>"
        f"[{'X' if is_checked(rel_node, 'isOfficer') else ' '}] Officer (give title below)   [{'X' if is_checked(rel_node, 'isOther') else ' '}] Other (specify below)<br><br>"
        f"_{title}_"
    )

    amendment_date = get_value_with_footnote(xml, r'amendmentDate', footnotes_map)
    box5_html = f"**5. If Amendment, Date of Original Filed (Month/Day/Year)**<br><br>{amendment_date or ' '}"

    is_single = len(xml.find_all("reportingOwner")) == 1
    box6_html = (
        "**6. Individual or Joint/Group Filing (Check Applicable Line)**<br><br>"
        f"[{'X' if is_single else ' '}] Form filed by One Reporting Person<br>"
        f"[{' ' if is_single else 'X'}] Form filed by More than One Reporting Person"
    )

    header = "| | | |\n|:---|:---|:---|"
    row1 = f"| {box1_html} | {box3_html} | {box5_html} |"
    row2 = f"| {box2_html} | {box4_html} | {box6_html} |"

    return f"\n\n---\n{header}\n{row1}\n{row2}\n\n---\n"

def parse_form3_xml(soup: BeautifulSoup) -> str:
    """
    Parses an XML-based Form 3 (Initial Statement of Beneficial Ownership)
    into structured Markdown.
    """
    xml = soup

    fn_map, fn_txt = {}, []
    if (fsec := xml.find("footnotes")):
        fns = fsec.find_all("footnote")
        fn_map = {f["id"]: f"({i+1})" for i, f in enumerate(fns)}
        fn_txt = [f"({i+1}) {f.text.strip()}" for i, f in enumerate(fns)]

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM 3\n\n"
        "### INITIAL STATEMENT OF BENEFICIAL OWNERSHIP OF SECURITIES\n",
        "[ ] Check this box if no longer subject to Section 16. Form 4 or Form 5 obligations may continue. See Instruction 1(b)."
    ]

    for owner_node in xml.find_all("reportingOwner"):
        parts.append(f"\n{_form3_header_details_block(xml, owner_node, fn_map)}\n")

    t1_tag = xml.find(re.compile(r"nonDerivativeTable", re.I))
    if t1_tag and t1_tag.get_text(strip=True):
        rows_data = []
        for r in t1_tag.find_all(re.compile(r"nonDerivativeHolding", re.I)):
            row = {}
            post = r.find(re.compile(r'postTransactionAmounts', re.I))
            ownership = r.find(re.compile(r'ownershipNature', re.I))

            row["1. Title of Security"] = get_value_with_footnote(r, r'securityTitle', fn_map)
            row["2. Amount of Securities Beneficially Owned"] = get_value_with_footnote(post, r'sharesOwnedFollowingTransaction', fn_map)
            row["3. Ownership Form"] = get_value_with_footnote(ownership, r'directOrIndirectOwnership', fn_map)
            row["4. Nature of Indirect Beneficial Ownership"] = get_value_with_footnote(ownership, r'natureOfOwnership', fn_map)
            rows_data.append(row)

        df1 = pd.DataFrame(rows_data).fillna('')
        df1 = df1.applymap(format_footnotes_in_text)
        parts.append("\n## Table I - Non-Derivative Securities Beneficially Owned\n\n---\n")
        parts.append(md_table_2row_header(reorder(df1, ORDER_I_FORM3)))
        parts.append("---\n")
    else:
        df1 = pd.DataFrame([[''] * len(ORDER_I_FORM3)], columns=ORDER_I_FORM3)
        df1 = df1.replace(r'^\s*$', np.nan, regex=True).dropna(how='all')
        df1 = md_table_2row_header(df1)
        parts.extend([
            "\n## Table I - Non-Derivative Securities Beneficially Owned\n\n---\n",
            df1,
            "\n---"
        ])

    t2_tag = xml.find(re.compile(r'^derivativeTable$', re.I))
    if t2_tag and t2_tag.get_text(strip=True):
        rows_data_2 = []
        for r in t2_tag.find_all(re.compile(r'^derivativeHolding$', re.I)):
            row = {}
            underlying = r.find(re.compile(r'underlyingSecurity', re.I))
            ownership = r.find(re.compile(r'ownershipNature', re.I))
            
            row["1. Title of Derivative Security##ROWSPAN_1##<br>1. Title of Derivative Security##ROWSPAN_1##"] = get_value_with_footnote(r, r'securityTitle', fn_map)
            row["2. Date Exercisable and Expiration Date (Month/Day/Year)##COLSPAN_1##<br>Date Exercisable"] = get_value_with_footnote(r, r'exerciseDate', fn_map)
            row["2. Date Exercisable and Expiration Date (Month/Day/Year)##COLSPAN_1##<br>Expiration Date"] = get_value_with_footnote(r, r'expirationDate', fn_map)
            row["3. Title and Amount of Underlying Securities##COLSPAN_2##<br>Title"] = get_value_with_footnote(underlying, r'underlyingSecurityTitle', fn_map)
            row["3. Title and Amount of Underlying Securities##COLSPAN_2##<br>Amount or Number of Shares"] = get_value_with_footnote(underlying, r'underlyingSecurityShares', fn_map)
            row["4. Conversion or Exercise Price##ROWSPAN_2##<br>4. Conversion or Exercise Price##ROWSPAN_2##"] = _dollarize_if_number(get_value_with_footnote(r, r'conversionOrExercisePrice', fn_map))
            row["5. Ownership Form##ROWSPAN_3##<br>5. Ownership Form##ROWSPAN_3##"] = get_value_with_footnote(ownership, r'directOrIndirectOwnership', fn_map)
            row["6. Nature of Indirect Beneficial Ownership##ROWSPAN_4##<br>6. Nature of Indirect Beneficial Ownership##ROWSPAN_4##"] = get_value_with_footnote(ownership, r'natureOfOwnership', fn_map)

            rows_data_2.append(row)

        df2 = pd.DataFrame(rows_data_2).fillna('')
        df2 = df2.applymap(format_footnotes_in_text)
        parts.append("\n## Table II - Derivative Securities Beneficially Owned\n\n---\n")
        parts.append(md_table_2row_header(reorder(df2, ORDER_II_FORM3)))
        parts.append("---\n")
    else:
        parts.append("\n## Table II - Derivative Securities Beneficially Owned\n")
        
        parts.append("\n---\n")
        
        placeholder_df = pd.DataFrame([['—'] * len(ORDER_II_FORM3)], columns=ORDER_II_FORM3)
        parts.append(md_table_2row_header(placeholder_df))

        parts.append("\n---")

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

    return "\n\n".join(parts)

def parse_form_d_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form D into structured Markdown, accurately
    rendering all sections to mimic the visual layout of the original form.
    This version dynamically handles legacy exemption rules and correctly
    displays amendment information.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return html.unescape(found.text.strip()) if found and found.text else "—"

    def safe_format_dollar(value_str: str) -> str:
        if not value_str or value_str == "—": return "—"
        try:
            return f"${int(value_str):}"
        except (ValueError, TypeError):
            return value_str
    
    def get_boolean_checkbox(node, tag):
        val = get_text(node, tag).lower()
        return '[x] Yes [ ] No' if val == 'true' else '[ ] Yes [x] No'

    ENTITY_TYPES = [
        "Corporation", "Limited Partnership", "Limited Liability Company",
        "General Partnership", "Business Trust", "Other"
    ]
    YEAR_OF_INC_OPTIONS = [
        "Over Five Years Ago", "Within Last Five Years (Specify Year)", "Yet to Be Formed"
    ]
    INDUSTRY_GROUPS_FULL = [
        ("Agriculture", "Health Care", "Retailing"),
        ("Banking & Financial Services", "Biotechnology", "Restaurants"),
        ("  Commercial Banking", "Health Insurance", "Technology"),
        ("  Insurance", "Hospitals & Physicians", "  Computers"),
        ("  Investing", "Pharmaceuticals", "  Telecommunications"),
        ("  Investment Banking", "Other Health Care", "  Other Technology"),
        ("  Pooled Investment Fund", "Manufacturing", "Travel"),
        ("    Hedge Fund", "Real Estate", "  Airlines & Airports"),
        ("    Private Equity Fund", "  Commercial", "  Lodging & Conventions"),
        ("    Venture Capital Fund", "  Construction", "  Tourism & Travel Services"),
        ("    Other Investment Fund", "  REITS & Finance", "  Other Travel"),
        ("  *Is the issuer registered as an investment company?*", "  Residential", "Other"),
        ("  Other Banking & Financial Services", "  Other Real Estate", None),
        ("Business Services", None, None),
        ("Energy", None, None),
        ("  Coal Mining", None, None),
        ("  Electric Utilities", None, None),
        ("  Energy Conservation", None, None),
        ("  Environmental Services", None, None),
        ("  Oil & Gas", None, None),
        ("  Other Energy", None, None)
    ]
    REVENUE_RANGES = [
        "No Revenues", "$1 - $1,000,000", "$1,000,001 - $5,000,000",
        "$5,000,001 - $25,000,000", "$25,000,001 - $100,000,000",
        "Over $100,000,000", "Decline to Disclose", "Not Applicable"
    ]
    AGGREGATE_NAV_RANGES = [
        "No Aggregate Net Asset Value", "$1 - $5,000,000", "$5,000,001 - $25,000,000",
        "$25,000,001 - $50,000,000", "$50,000,001 - $100,000,000",
        "Over $100,000,000", "Decline to Disclose", "Not Applicable"
    ]
    SECURITY_TYPES = [
        "Equity", "Debt", "Option, Warrant or Other Right to Acquire Another Security",
        "Security to be Acquired Upon Exercise of Option, Warrant or Other Right to Acquire Security",
        "Pooled Investment Fund Interests", "Tenant-in-Common Securities",
        "Mineral Property Securities", "Other"
    ]
    
    FEDERAL_EXEMPTIONS_GROUPS_CORRECT = [
        [
            ("04a", "Rule 504(b)(1) (not (i), (ii) or (iii))"),
            ("04.1", "Rule 504(b)(1)(i)"),
            ("04.2", "Rule 504(b)(1)(ii)"),
            ("04d", "Rule 504(b)(1)(iii)"),
            ("06b", "Rule 506(b)"),
            ("06c", "Rule 506(c)"),
            ("4a5", "Securities Act Section 4(5)"),
        ],
        [
            ("3c", "Investment Company Act Section 3(c)"),
            ("3c.1", "  Section 3(c)(1)"),
            ("3c.2", "  Section 3(c)(2)"),
            ("3c.3", "  Section 3(c)(3)"),
            ("3c.4", "  Section 3(c)(4)"),
            ("3c.5", "  Section 3(c)(5)"),
            ("3c.6", "  Section 3(c)(6)"),
            ("3c.7", "  Section 3(c)(7)"),
        ],
        [
            (None, ""), (None, ""),
            ("3c.9", "  Section 3(c)(9)"),
            ("3c.10", "  Section 3(c)(10)"),
            ("3c.11", "  Section 3(c)(11)"),
            ("3c.12", "  Section 3(c)(12)"),
            ("3c.13", "  Section 3(c)(13)"),
            ("3c.14", "  Section 3(c)(14)"),
        ]
    ]

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM D\n\n"
        "### Notice of Exempt Offering of Securities\n"
    ]

    offering = xml.find('offeringData')

    type_of_filing_node = offering.find('typeOfFiling')
    if type_of_filing_node:
        amendment_node = type_of_filing_node.find('newOrAmendment')
        if amendment_node and get_text(amendment_node, 'isAmendment').lower() == 'true':
            prev_accession = get_text(amendment_node, 'previousAccessionNumber')
            if prev_accession != "—":
                parts.append(f"**Notice of Amendment** (Previous Accession Number: {prev_accession})\n")
    
    parts.append("### 1. Issuer's Identity\n")
    issuer = xml.find('primaryIssuer')
    parts.append(f"**CIK (Filer ID Number):** {get_text(issuer, 'cik')}")
    parts.append(f"**Name of Issuer:** {get_text(issuer, 'entityName')}")
    parts.append(f"**Jurisdiction of Incorporation/Organization:** {get_text(issuer, 'jurisdictionOfInc')}")
    previous_name = get_text(issuer.find('edgarPreviousNameList'), 'previousName')
    
    if previous_name == "—":
        previous_name = get_text(issuer.find('issuerPreviousNameList'), 'value')
        
    parts.append(f"**Previous Names:** {previous_name}")
    
    yoi_node = issuer.find('yearOfInc')
    is_over_five = get_text(yoi_node, 'overFiveYears').lower() == 'true'
    is_within_five = get_text(yoi_node, 'withinFiveYears').lower() == 'true'
    is_yet_to_be_formed = get_text(yoi_node, 'yetToBeFormed').lower() == 'true'
    year_value = get_text(yoi_node, 'value')
    
    parts.append("\n**Year of Incorporation/Organization**")
    
    checkbox_over_five = '[x]' if is_over_five else '[ ]'
    parts.append(f"- {checkbox_over_five} Over Five Years Ago")
    
    checkbox_within_five = '[x]' if is_within_five else '[ ]'
    specify_year_text = f" {year_value}" if is_within_five and year_value != "—" else ""
    parts.append(f"- {checkbox_within_five} Within Last Five Years (Specify Year){specify_year_text}")
    
    checkbox_yet_to_be = '[x]' if is_yet_to_be_formed else '[ ]'
    parts.append(f"- {checkbox_yet_to_be} Yet to Be Formed")

    entity_val = get_text(issuer, 'entityType')
    entity_other_desc = get_text(issuer, 'entityTypeOtherDesc')

    parts.append("\n**Entity Type**")
    for option in ENTITY_TYPES:
        is_checked = (option == entity_val)
        
        if option == "Other" and is_checked:
            specify_text = f" ({entity_other_desc})" if entity_other_desc and entity_other_desc != "—" else ""
            parts.append(f"- [x] {option}{specify_text}")
        else:
            checkbox = '[x]' if is_checked else '[ ]'
            parts.append(f"- {checkbox} {option}")

    parts.append("\n### 2. Principal Place of Business and Contact Information\n")
    addr = issuer.find('issuerAddress')
    
    contact_info = {
        "Name of Issuer": get_text(issuer, 'entityName'),
        "Street Address 1": get_text(addr, 'street1'),
        "Street Address 2": get_text(addr, 'street2'),
        "City": get_text(addr, 'city'),
        "State/Province/Country": get_text(addr, 'stateOrCountry'),
        "ZIP/Postal Code": get_text(addr, 'zipCode'),
        "Phone Number of Issuer": get_text(issuer, 'issuerPhoneNumber')
    }

    for key, value in contact_info.items():
        if value and value != "—":
            parts.append(f"**{key}:** {value}")

    related_persons = xml.find_all('relatedPersonInfo')
    if related_persons:
        parts.append("\n### 3. Related Persons\n")
        person_data = []
        for p in related_persons:
            name_node = p.find('relatedPersonName')
            addr_node = p.find('relatedPersonAddress')
            rels = [r.text for r in p.select('relatedPersonRelationshipList > relationship')]

            first_name = get_text(name_node, 'firstName')
            middle_name = get_text(name_node, 'middleName')
            full_first_name = f"{first_name} {middle_name}".strip() if middle_name and middle_name != "—" else first_name

            relationship_str = (
                f"{'[x]' if 'Executive Officer' in rels else '[ ]'} Executive Officer<br>"
                f"{'[x]' if 'Director' in rels else '[ ]'} Director<br>"
                f"{'[x]' if 'Promoter' in rels else '[ ]'} Promoter"
            )

            current_person = {
                "Last Name": get_text(name_node, 'lastName'),
                "First Name": full_first_name,
                "Street Address 1": get_text(addr_node, 'street1'),
                "City": get_text(addr_node, 'city'),
                "State": get_text(addr_node, 'stateOrCountry'),
                "ZIP/Postal Code": get_text(addr_node, 'zipCode'),
                "Relationship": relationship_str,
                "Clarification of Response": get_text(p, 'relationshipClarification') or "—"
            }
            person_data.append(current_person)

        if person_data:
            person_df = pd.DataFrame(person_data)

            column_order = [
                "Last Name", "First Name", "Street Address 1", "City", "State", 
                "ZIP/Postal Code", "Relationship", "Clarification of Response"
            ]
            
            person_df = person_df.reindex(columns=column_order)
            parts.append(to_compact_markdown(person_df, index=False))
    
    parts.append("\n### 4. Industry Group\n")
    
    industry_group_node = offering.find('industryGroup')
    industry_val = get_text(industry_group_node, 'industryGroupType')
    
    investment_fund_info_node = industry_group_node.find('investmentFundInfo')
    investment_fund_type_val = get_text(investment_fund_info_node, 'investmentFundType')
    
    yes_box, no_box = '[ ]', '[ ]'
    if investment_fund_info_node:
        is_investment_co_val = get_text(investment_fund_info_node, 'is40Act')
        if is_investment_co_val != "—":
            is_investment_co = is_investment_co_val.lower() in ['true', 'y']
            yes_box = '[x]' if is_investment_co else '[ ]'
            no_box = '[ ]' if is_investment_co else '[x]'

    table_rows = ["| | | |", "|:---|:---|:---|"]
    for row_tuple in INDUSTRY_GROUPS_FULL:
        cells = []
        for item in row_tuple:
            if item is None:
                cells.append("")
                continue

            item_text = item.strip()
            
            if "*" in item_text:
                question_text = item_text.replace('*','')
                cells.append(f"  {question_text} <br>  {yes_box} Yes {no_box} No")
                continue

            is_checked = (item_text.replace('&', 'and') == industry_val) or (item_text == investment_fund_type_val)
            
            checkbox = '[x]' if is_checked else '[ ]'
            
            indentation = " " * (len(item) - len(item.lstrip(' ')))
            cells.append(f"{indentation}{checkbox} {item_text}")

        table_rows.append(f"| {' | '.join(cells)} |")
        
    parts.append("\n".join(table_rows))

    parts.append("\n### 5. Issuer Size\n")
    
    issuer_size_node = offering.find('issuerSize')
    
    revenue_val = get_text(issuer_size_node, 'revenueRange')
    nav_val = get_text(issuer_size_node, 'aggregateNetAssetValueRange')
    
    table_rows = ["| **Revenue Range** | **OR** | **Aggregate Net Asset Value Range** |", "|:---|:---:|:---|"]
    for rev_option, nav_option in itertools.zip_longest(REVENUE_RANGES, AGGREGATE_NAV_RANGES, fillvalue=""):
        rev_cell = f"[{'x' if rev_option == revenue_val else ' '}] {rev_option}" if rev_option else ""
        nav_cell = f"[{'x' if nav_option == nav_val else ' '}] {nav_option}" if nav_option else ""
        table_rows.append(f"| {rev_cell} | | {nav_cell} |")
        
    parts.append("\n".join(table_rows))

    parts.append("\n### 6. Federal Exemption(s) and Exclusion(s) Claimed (select all that apply)\n")
    
    LEGACY_RULE_MAP = {
        "05": "Rule 505",
        "06": "Rule 506"
    }
    selected_exemptions = {item.text.strip().lower() for item in offering.select('federalExemptionsExclusions > item')}
    all_modern_codes = {code.lower() for group in FEDERAL_EXEMPTIONS_GROUPS_CORRECT for code, _ in group if code}

    exemptions_table_rows = ["| | | |", "|:---|:---|:---|"]
    for row_tuple in itertools.zip_longest(*FEDERAL_EXEMPTIONS_GROUPS_CORRECT, fillvalue=(None, "")):
        cells = []
        for code, text in row_tuple:
            if code is None:
                cells.append(text)
            else:
                is_checked = code.lower() in selected_exemptions
                checkbox = '[x]' if is_checked else '[ ]'
                indent = " " * (len(text) - len(text.lstrip(' ')))
                cells.append(f"{indent}{checkbox} {text.lstrip(' ')}")
        exemptions_table_rows.append(f"| {' | '.join(cells)} |")
    parts.append("\n".join(exemptions_table_rows))

    legacy_selected_codes = {code for code in selected_exemptions if code not in all_modern_codes and code in LEGACY_RULE_MAP}
    if legacy_selected_codes:
        parts.append("\n**Legacy Exemptions Claimed:**")
        for code in sorted(legacy_selected_codes):
            rule_name = LEGACY_RULE_MAP[code]
            parts.append(f"- [x] {rule_name}")

    parts.append("\n### 7. Type of Filing\n")
    is_amend = get_text(type_of_filing_node.find('newOrAmendment'), 'isAmendment').lower() == 'true'
    is_new = not is_amend
    first_sale_date = get_text(type_of_filing_node.find('dateOfFirstSale'), 'value')
    filing_md = f"**New Notice:** {'[x]' if is_new else '[ ]'} **Date of First Sale:** {first_sale_date}\n\n**Amendment:** {'[x]' if is_amend else '[ ]'}"
    parts.append(filing_md)

    parts.append("\n### 8. Duration of Offering\n")
    parts.append(f"Does the issuer intend this offering to last more than one year? {get_boolean_checkbox(offering.find('durationOfOffering'), 'moreThanOneYear')}")
    
    parts.append("\n### 9. Type(s) of Securities Offered\n")
    
    SECURITY_TYPE_MAP = {
        "Equity": "isEquityType",
        "Debt": "isDebtType",
        "Option, Warrant or Other Right to Acquire Another Security": "isOptionToAcquireType",
        "Security to be Acquired Upon Exercise of Option, Warrant or Other Right to Acquire Security": "isSecurityToBeAcquiredType",
        "Pooled Investment Fund Interests": "isPooledInvestmentFundType",
        "Tenant-in-Common Securities": "isTenantInCommonType",
        "Mineral Property Securities": "isMineralPropertyType",
        "Other": "isOtherType"
    }
    
    types_node = offering.find('typesOfSecuritiesOffered')
    securities_md = []
    
    for display_text in SECURITY_TYPES:
        xml_tag = SECURITY_TYPE_MAP.get(display_text)
        is_checked = False
        if xml_tag and types_node:
            is_checked = get_text(types_node, xml_tag).lower() in ['true', 'y']
        
        checkbox = '[x]' if is_checked else '[ ]'
        line = f"- {checkbox} {display_text}"
        
        if display_text == "Other" and is_checked:
            other_desc = get_text(types_node, 'descriptionOfOtherType')
            if other_desc and other_desc != "—":
                line += f" ({other_desc})"
                
        securities_md.append(line)
        
    parts.append("\n".join(securities_md))

    parts.append("\n### 10. Business Combination Transaction\n")
    
    biz_combo_node = offering.find('businessCombinationTransaction')
    bus_combo_md = f"Is this offering being made in connection with a business combination transaction, such as a merger, acquisition or exchange offer? {get_boolean_checkbox(biz_combo_node, 'isBusinessCombinationTransaction')}"
    parts.append(bus_combo_md)
    
    clarification = get_text(biz_combo_node, 'clarificationOfResponse')
    if clarification != "—":
        parts.append(f"**Clarification of Response:** {clarification}")

    min_inv = get_text(offering, 'minimumInvestmentAccepted')
    parts.append(f"\n### 11. Minimum Investment\n**Minimum investment accepted from any outside investor:** {safe_format_dollar(min_inv)} USD")
    
    parts.append("\n### 12. Sales Compensation\n")
    recipients = offering.find_all('recipient')
    if recipients:
        comp_data = []
        for r in recipients:
            addr_node = r.find('recipientAddress')
            states = ", ".join([s.text for s in r.select('statesOfSolicitationList > value')])
            is_foreign = get_text(r, 'foreignSolicitation').lower() == 'true'
            
            comp_data.append({
                "Recipient Name": get_text(r, 'recipientName'), 
                "Recipient CRD Number": get_text(r, 'recipientCRDNumber'),
                "Associated BD Name": get_text(r, 'associatedBDName'), 
                "Associated BD CRD Number": get_text(r, 'associatedBDCRDNumber'),
                "Street 1": get_text(addr_node, 'street1'), 
                "Street 2": get_text(addr_node, 'street2'),
                "City": get_text(addr_node, 'city'), 
                "State": get_text(addr_node, 'stateOrCountry'), 
                "ZIP Code": get_text(addr_node, 'zipCode'),
                "States of Solicitation": states,
                "Foreign Solicitation": '[x]' if is_foreign else '[ ]'
            })
        df = pd.DataFrame(comp_data)
        all_cols = ["Recipient Name", "Recipient CRD Number", "Associated BD Name", "Associated BD CRD Number", "Street 1", "Street 2", "City", "State", "ZIP Code", "States of Solicitation", "Foreign Solicitation"]
        df = df.reindex(columns=all_cols, fill_value="—")
        parts.append(to_compact_markdown(df, index=False))
    else:
        none_compensation_md = [
            "**Recipient:** — **Recipient CRD Number:** [x] None",
            "**(Associated) Broker or Dealer:** — **(Associated) Broker or Dealer CRD Number:** [x] None",
        ]
        parts.append("\n\n".join(none_compensation_md))

    parts.append("\n### 13. Offering and Sales Amounts\n")
    sales = offering.find('offeringSalesAmounts')

    def format_sales_amount(label: str, value: str) -> str:
        if value.strip().lower() == 'indefinite':
            return f"**{label}:** USD or [x] Indefinite"
        else:
            try:
                formatted_value = f"${int(value):,}"
                return f"**{label}:** {formatted_value} USD"
            except (ValueError, TypeError):
                return f"**{label}:** {value} USD"
    
    parts.append(format_sales_amount("Total Offering Amount", get_text(sales, 'totalOfferingAmount')))
    parts.append(format_sales_amount("Total Amount Sold", get_text(sales, 'totalAmountSold')))
    parts.append(format_sales_amount("Total Remaining to be Sold", get_text(sales, 'totalRemaining')))
    if (clarification := get_text(sales, 'clarificationOfResponse')) != "—":
        parts.append(f"**Clarification of Response:** {clarification}")

    parts.append("\n### 14. Investors\n")
    investors_node = offering.find('investors')
    parts.append(f"Select if securities in the offering have been or may be sold to persons who do not qualify as accredited investors, and enter the number of such non-accredited investors who already have invested in the offering: {get_boolean_checkbox(investors_node, 'hasNonAccreditedInvestors')}")
    
    num_non_accredited = get_text(investors_node, 'numberNonAccreditedInvestors')
    if num_non_accredited != "—":
        parts.append(f"**Number of such non-accredited investors:** {num_non_accredited}")

    parts.append(f"**Total Number of Investors Already Invested:** {get_text(investors_node, 'totalNumberAlreadyInvested')}")
    
    parts.append("\n### 15. Sales Commissions & Finder's Fees Expenses\n")
    
    instructional_text = "Provide separately the amounts of sales commissions and finders fees expenses, if any. If the amount of an expenditure is not known, provide an estimate and check the box next to the amount."
    parts.append(instructional_text)

    fees_node = offering.find('salesCommissionsFindersFees')
    
    def format_fee_line(label: str, fee_type_node) -> str:
        if not fee_type_node: return f"**{label}** $0 USD"
        amount = safe_format_dollar(get_text(fee_type_node, 'dollarAmount'))
        is_estimate = get_text(fee_type_node, 'isEstimate').lower() in ['true', 'y']
        
        if is_estimate:
            return f"**{label}** {amount} USD [x] Estimate"
        else:
            return f"**{label}** {amount} USD"

    parts.append(format_fee_line("Sales Commissions", fees_node.find('salesCommissions')))
    parts.append(format_fee_line("Finders' Fees", fees_node.find('findersFees')))
    
    if (clarification := get_text(fees_node, 'clarificationOfResponse')) != "—":
        parts.append(f"\n**Clarification of Response (if Necessary):** {clarification}")

    parts.append("\n### 16. Use of Proceeds\n")
    proceeds_node = offering.find('useOfProceeds')
    if proceeds_node:
        gross_proceeds_node = proceeds_node.find('grossProceedsUsed')
        amount_str = safe_format_dollar(get_text(gross_proceeds_node, 'dollarAmount'))
        is_estimate = get_text(gross_proceeds_node, 'isEstimate').lower() in ['true', 'y']
        
        estimate_text = " [x] Estimate" if is_estimate else ""
        
        parts.append(f"Provide the amount of the gross proceeds of the offering that has been or is proposed to be used for payments to any of the persons required to be named as executive officers, directors or promoters: **{amount_str} USD{estimate_text}**")
        if (clarification := get_text(proceeds_node, 'clarificationOfResponse')) != "—":
            parts.append(f"**Clarification of Response (if necessary):** {clarification}")
    else:
         parts.append("Provide the amount of the gross proceeds...: —")

    parts.append("\n### Signature and Submission\n")
    parts.append("Please verify the information you have entered and review the Terms of Submission below before signing and clicking SUBMIT below to file this notice.")
    
    terms_of_submission = [
        "**Terms of Submission**",
        "In submitting this notice, each issuer named above is:",
        "* Notifying the SEC and/or each State in which this notice is filed of the offering of securities described and undertaking to furnish them, upon written request, in the accordance with applicable law, the information furnished to offerees.",
        textwrap.fill("* Irrevocably appointing each of the Secretary of the SEC and, the Securities Administrator or other legally designated officer of the State in which the issuer maintains its principal place of business and any State in which this notice is filed, as its agents for service of process, and agreeing that these persons may accept service on its behalf, of any notice, process or pleading, and further agreeing that such service may be made by registered or certified mail, in any Federal or state action, administrative proceeding, or arbitration brought against the issuer in any place subject to the jurisdiction of the United States, if the action, proceeding or arbitration (a) arises out of any activity in connection with the offering of securities that is the subject of this notice, and (b) is founded, directly or indirectly, upon the provisions of: (i) the Securities Act of 1933, the Securities Exchange Act of 1934, the Trust Indenture Act of 1939, the Investment Company Act of 1940, or the Investment Advisers Act of 1940, or any rule or regulation under any of these statutes, or (ii) the laws of the State in which the issuer maintains its principal place of business or any State in which this notice is filed.", width=120),
        "* Certifying that, if the issuer is claiming a Regulation D exemption for the offering, the issuer is not disqualified from relying on Rule 504 or Rule 506 for one of the reasons stated in Rule 504(b)(3) or Rule 506(d)."
    ]
    parts.extend(terms_of_submission)
    
    parts.append("\nEach issuer identified above has read this notice, knows the contents to be true, and has duly caused this notice to be signed on its behalf by the undersigned duly authorized person.")
    parts.append("For signature, type in the signer's name or other letters or characters adopted or authorized as the signer's signature.")
    
    sig = offering.find('signatureBlock').find('signature')
    sig_df = pd.DataFrame([{
        "Issuer": get_text(sig, 'issuerName'),
        "Signature": get_text(sig, 'signatureName'),
        "Name of Signer": get_text(sig, 'nameOfSigner'),
        "Title": get_text(sig, 'signatureTitle'),
        "Date": get_text(sig, 'signatureDate'),
    }])
    parts.append(to_compact_markdown(sig_df, index=False))

    parts.append("\n*Persons who respond to the collection of information contained in this form are not required to respond unless the form displays a currently valid OMB number.*")
    
    return "\n\n".join(parts)

def parse_form_n_mfp2_xml(xml: BeautifulSoup, class_name_map: dict = None) -> str:
    """
    Parses an XML-based Form N-MFP2 (Monthly Schedule of Portfolio Holdings
    of Money Market Funds) into a comprehensive, structured Markdown document.
    This version captures all available data points for maximum detail.
    """
    def get_text(node, tag, strip_ns=True):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I)) or node.find(re.compile(f'(?:\\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_val(value_str: str, type_hint: str = 'string') -> str:
        """Robustly formats values based on their intended type."""
        if not value_str or value_str.lower() in ('—', 'n/a', 'na'): return "—"
        try:
            val_float = float(value_str.replace(',', ''))
            if type_hint == 'dollar': return f"${val_float:.2f}"
            if type_hint == 'percent': return f"{val_float * 100:.2f}%"
            if type_hint == 'shares': return f"{val_float:.4f}"
            if type_hint == 'yield': return f"{val_float * 100:.4f}%"
            if type_hint == 'number': return f"{val_float:.2f}"
        except (ValueError, TypeError):
            pass

        if value_str.upper() == 'Y': return "Yes"
        if value_str.upper() == 'N': return "No"
        return value_str

    parts = ["# Form N-MFP2: Monthly Schedule of Portfolio Holdings"]
    
    filer_info_section = []
    header_data = xml.find('headerData')
    if header_data:
        submission_type = get_text(header_data, 'submissionType')
        filer_info_section.append(f"**Submission Type:** {submission_type}")
        
        filer_creds = header_data.find('filerCredentials')
        if filer_creds:
            filer_info_section.append(f"**CIK:** {get_text(filer_creds, 'cik')}")
            filer_info_section.append(f"**CCC:** {get_text(filer_creds, 'ccc')}")
            
    parts.append("## N-MFP: Filer Information\n" + "\n".join(filer_info_section))

    form_data = xml.find('formData')
    gen_info = form_data.find('generalInfo')
    
    filing_info_section = [f"### General Information"]
    gen_data = {
        "Report for (YYYY-MM-DD)": get_text(gen_info, 'reportDate'),
        "CIK Number of Registrant": get_text(gen_info, 'cik'),
        "LEI of Registrant": get_text(gen_info, 'registrantLEIId'),
        "EDGAR Series Identifier": get_text(gen_info, 'seriesId'),
        "Total number of share classes in the series": get_text(gen_info, 'totalShareClassesInSeries'),
        "Is this the fund's final filing on Form N- MFP?": format_val(get_text(gen_info, 'finalFilingFlag')),
        "Has the fund acquired or merged with another fund during the reporting period?": format_val(get_text(gen_info, 'fundAcqrdOrMrgdWthAnthrFlag')),
    }
    for key, val in gen_data.items():
        filing_info_section.append(f"**{key}:** {val}")
    parts.append("\n\n".join(filing_info_section))

    series_info = form_data.find('seriesLevelInfo')
    parts.append("\n## Part A: Series-Level Information about the Fund")

    service_providers = []
    
    adviser_node = series_info.find("adviser")
    if adviser_node and adviser_node.get_text(strip=True):
        service_providers.append({"Item": "A.2", "Role": "Investment Adviser", "Details": get_text(adviser_node, 'adviserName'), "File/CIK Number": get_text(adviser_node, 'adviserFileNumber')})

    sub_adviser_node = series_info.find("subAdviser")
    if sub_adviser_node and sub_adviser_node.get_text(strip=True):
        service_providers.append({"Item": "A.3", "Role": "Sub-Adviser", "Details": get_text(sub_adviser_node, 'adviserName'), "File/CIK Number": get_text(sub_adviser_node, 'adviserFileNumber')})
        
    accountant_node = series_info.find('indpPubAccountant')
    if accountant_node and accountant_node.get_text(strip=True):
        acc_details = f"{get_text(accountant_node, 'name')}<br>City: {get_text(accountant_node, 'city')}<br>State: {get_text(accountant_node, 'stateCountry')}"
        service_providers.append({"Item": "A.4", "Role": "Independent Public Accountant", "Details": acc_details, "File/CIK Number": "—"})

    admin_node = series_info.find('administrator')
    if admin_node and admin_node.get_text(strip=True):
        service_providers.append({"Item": "A.5", "Role": "Administrator", "Details": get_text(admin_node, 'administratorName'), "File/CIK Number": "—"})

    transfer_agent_node = series_info.find('transferAgent')
    if transfer_agent_node and transfer_agent_node.get_text(strip=True):
        ta_details = f"{get_text(transfer_agent_node, 'name')}<br>CIK: {get_text(transfer_agent_node, 'cik')}"
        service_providers.append({"Item": "A.6", "Role": "Transfer Agent", "Details": ta_details, "File/CIK Number": get_text(transfer_agent_node, 'fileNumber')})

    if service_providers:
        parts.append("\n### Service Providers\n" + to_compact_markdown(pd.DataFrame(service_providers), index=False))

    fund_chars = {
        "A.1 - Securities Act File Number": get_text(series_info, 'securitiesActFileNumber'),
        "A.7 - Is this a Feeder Fund?": format_val(get_text(series_info, 'feederFundFlag')),
        "A.8 - Is this a Master Fund?": format_val(get_text(series_info, 'masterFundFlag')),
        "A.9 - Is this series primarily used to fund insurance company separate accounts?": format_val(get_text(series_info, 'seriesFundInsuCmpnySepAccntFlag')),
        "A.10 - Money Market Fund Category": get_text(series_info, 'moneyMarketFundCategory'),
        "A.10.a - Is this fund an exempt retail fund?": format_val(get_text(series_info, 'fundExemptRetailFlag')),
        "A.11 - WAM": f"{get_text(series_info, 'averagePortfolioMaturity')} days",
        "A.12 - WAL": f"{get_text(series_info, 'averageLifeMaturity')} days",
        "Does the fund apply liquidity fees?": format_val(get_text(series_info, 'liquidityFeeFundApplyFlag')),
        "Total Value of Portfolio Securities": format_val(get_text(series_info, 'totalValuePortfolioSecurities'), 'dollar'),
        "Amortized Cost of Portfolio Securities": format_val(get_text(series_info, 'amortizedCostPortfolioSecurities'), 'dollar'),
        "Cash": format_val(get_text(series_info, 'cash'), 'dollar'),
        "Total Other Assets": format_val(get_text(series_info, 'totalValueOtherAssets'), 'dollar'),
        "Total Liabilities": format_val(get_text(series_info, 'totalValueLiabilities'), 'dollar'),
        "Net Assets of Series": format_val(get_text(series_info, 'netAssetOfSeries'), 'dollar'),
        "Number of Shares Outstanding (Series)": format_val(get_text(series_info, 'numberOfSharesOutstanding'), 'number'),
        "Stable Price Per Share": format_val(get_text(series_info, 'stablePricePerShare'), 'dollar'),
        "7-Day Gross Yield": format_val(get_text(series_info, 'sevenDayGrossYield'), 'yield')
    }
    parts.append("\n### Fund Characteristics & Assets")
    for key, val in fund_chars.items():
        if val not in ("—", " days"): parts.append(f"- **{key}:** {val}")

    liquid_data = []
    daily_assets_node = series_info.find('totalValueDailyLiquidAssets')
    if daily_assets_node:
        for i in range(1, 6):
            day_tag = f'fridayDay{i}'
            if get_text(daily_assets_node, day_tag) != "—":
                 liquid_data.append({
                    "Period": f"Friday, Week {i}",
                    "Daily Liquid Assets ($)": format_val(get_text(series_info.find('totalValueDailyLiquidAssets'), f'fridayDay{i}'), 'dollar'),
                    "Weekly Liquid Assets ($)": format_val(get_text(series_info.find('totalValueWeeklyLiquidAssets'), f'fridayWeek{i}'), 'dollar'),
                    "Daily Liquid Assets (%)": format_val(get_text(series_info.find('percentageDailyLiquidAssets'), f'fridayDay{i}'), 'percent'),
                    "Weekly Liquid Assets (%)": format_val(get_text(series_info.find('percentageWeeklyLiquidAssets'), f'fridayWeek{i}'), 'percent'),
                })

    if liquid_data:
        parts.append("\n### A.13 - Weekly Liquid Assets\n" + to_compact_markdown(pd.DataFrame(liquid_data), index=False))
        
    series_level_nav_data = []
    nav_node = series_info.find('netAssetValue')
    if nav_node:
        for week_node in nav_node.find_all(re.compile(r'^(?:\w+:)?fridayWeek\d+$', re.I)):
            if week_node.text.strip():
                week_number_match = re.search(r'(\d+)$', week_node.name)
                week_number = week_number_match.group(1) if week_number_match else '?'
                series_level_nav_data.append({
                    "Period": f"Friday, Week {week_number}",
                    "Net Asset Value Per Share": format_val(week_node.text, 'shares')
                })

    if series_level_nav_data:
        parts.append("\n### A.23 - Weekly Net Asset Value Per Share (Series-Level)\n" + to_compact_markdown(pd.DataFrame(series_level_nav_data), index=False))

    class_level_nodes = form_data.find_all('classLevelInfo')
    if class_level_nodes:
        parts.append("\n## Part B: Class-Level Information about the Fund")
        
        if class_name_map is None:
            class_name_map = {}

        for i, node in enumerate(class_level_nodes):
            class_id = get_text(node, 'classesId')
            class_name = class_name_map.get(class_id, f"Unknown Class ({class_id})")
            
            parts.append(f"\n### Class: {class_name}")
            
            class_details = {
                "B.2 - Minimum Initial Investment": format_val(get_text(node, 'minInitialInvestment'), 'dollar'),
                "B.3 - Net Assets of Class": format_val(get_text(node, 'netAssetsOfClass'), 'dollar'),
                "B.4 - Shares Outstanding": format_val(get_text(node, 'numberOfSharesOutstanding'), 'number'),
                "B.7.7 - 7-Day Net Yield": format_val(get_text(node, 'sevenDayNetYield'), 'yield'),
                "B.8 - Person Paying for Fund Expenses?": format_val(get_text(node, 'personPayForFundFlag')),
            }
            
            if get_text(node, 'personPayForFundFlag').upper() == 'Y':
                class_details["Expense Reimbursement/Waiver Description"] = get_text(node, 'nameOfPersonDescExpensePay')

            for key, val in class_details.items():
                if val != "—": parts.append(f"- **{key}:** {val}")

            weekly_flow_data = []
            nav_per_share_node = node.find('netAssetPerShare')
            
            for week_node in node.find_all(re.compile(r'^(?:\w+:)?fridayWeek\d+$', re.I), recursive=False):
                week_number_match = re.search(r'(\d+)$', week_node.name)
                if not week_number_match:
                    continue
                week_number = week_number_match.group(1)

                subs = get_text(week_node, 'weeklyGrossSubscriptions')
                reds = get_text(week_node, 'weeklyGrossRedemptions')
                
                nav_per_share = "—"
                if nav_per_share_node:
                    nav_week_tag = nav_per_share_node.find(re.compile(rf'^(?:\w+:)?fridayWeek{week_number}$', re.I))
                    if nav_week_tag:
                        nav_per_share = nav_week_tag.text.strip()

                try: subs_val = float(subs) 
                except (ValueError, TypeError): subs_val = 0.0
                try: reds_val = float(reds)
                except (ValueError, TypeError): reds_val = 0.0
                    
                if subs_val > 0 or reds_val > 0 or nav_per_share != "—":
                    weekly_flow_data.append({
                        "Period": f"Week {week_number}",
                        "B.5 - Net Asset Value Per Share": format_val(nav_per_share, 'shares'),
                        "B.6 - Gross Subscriptions ($)": format_val(subs, 'dollar'),
                        "B.6 - Gross Redemptions ($)": format_val(reds, 'dollar'),
                    })

            if weekly_flow_data:
                 parts.append("\n**Weekly Flows and NAV**\n" + to_compact_markdown(pd.DataFrame(weekly_flow_data), index=False))

            total_node = node.find("totalForTheMonthReported")
            if total_node:
                parts.append("\n**Total for the month reported:**")
                parts.append(f"- **Total Gross Subscriptions:** {format_val(get_text(total_node, 'weeklyGrossSubscriptions'), 'dollar')}")
                parts.append(f"- **Total Gross Redemptions:** {format_val(get_text(total_node, 'weeklyGrossRedemptions'), 'dollar')}")

    securities_nodes = form_data.find_all('scheduleOfPortfolioSecuritiesInfo')
    if securities_nodes:
        parts.append("\n## Part C: Schedule of Portfolio Securities")
        for i, node in enumerate(securities_nodes):
            parts.append(f"\n### Security {i+1}: {get_text(node, 'nameOfIssuer')}")
            
            security_details = [
                f"**C.1 - Title:** {get_text(node, 'titleOfIssuer')}",
                f"**C.6 - Investment Category:** {get_text(node, 'investmentCategory')}",
            ]
            
            id_data = { "C.3 - CUSIP": get_text(node, 'CUSIPMember'), "C.4 - ISIN": get_text(node, 'ISINId'), "C.3 - LEI": get_text(node, 'LEIID'), "C.5 - Other ID": get_text(node, 'otherUniqueId')}
            id_str = ", ".join([f"{k}: {v}" for k, v in id_data.items() if v != "—"])
            if id_str: security_details.append(f"**Identifiers:** {id_str}")

            security_details.extend([
                f"**C.18 - Value (incl. sponsor support):** {format_val(get_text(node, 'includingValueOfAnySponsorSupport'), 'dollar')}",
                f"**C.18.a - Value (excl. sponsor support):** {format_val(get_text(node, 'excludingValueOfAnySponsorSupport'), 'dollar')}",
                f"**C.19 - Percentage of Net Assets:** {format_val(get_text(node, 'percentageOfMoneyMarketFundNetAssets'), 'percent')}",
                f"**C.17 - Yield as of Reporting Date:** {format_val(get_text(node, 'yieldOfTheSecurityAsOfReportingDate'), 'yield')}",
                f"**C.11 - Maturity Date (WAM):** {get_text(node, 'investmentMaturityDateWAM')}",
                f"**C.12 - Maturity Date (WAL):** {get_text(node, 'investmentMaturityDateWAL')}",
                f"**C.13 - Final Legal Maturity Date:** {get_text(node, 'finalLegalInvestmentMaturityDate')}",
            ])
            
            ratings = [f"{get_text(n, 'nameOfNRSRO')}: {get_text(n, 'rating')}" for n in node.find_all('NRSRO')]
            if ratings: security_details.append(f"**C.10 - Ratings:** {'; '.join(ratings)}")

            flags = {
                "C.9 Eligible Security?": format_val(get_text(node, 'securityEligibilityFlag')),
                "C.14 Has Demand Feature?": format_val(get_text(node, 'securityDemandFeatureFlag')),
                "C.15 Has Guarantee?": format_val(get_text(node, 'securityGuaranteeFlag')),
                "C.16 Has Enhancement?": format_val(get_text(node, 'securityEnhancementsFlag')),
                "C.22 Is an Illiquid Security?": format_val(get_text(node, 'illiquidSecurityFlag')),
                "C.20 Is a Daily Liquid Asset?": format_val(get_text(node, 'dailyLiquidAssetSecurityFlag')),
                "C.21 Is a Weekly Liquid Asset?": format_val(get_text(node, 'weeklyLiquidAssetSecurityFlag')),
                "C.23 Categorized at Level 3?": format_val(get_text(node, 'securityCategorizedAtLevel3Flag')),
            }
            flag_str = ", ".join([f"{k} {v}" for k, v in flags.items() if v != "—"])
            if flag_str: security_details.append(f"**Characteristics:** {flag_str}")

            parts.append("\n".join(f"- {item}" for item in security_details))
            
            demand_feature_node = node.find('demandFeature')
            if demand_feature_node and demand_feature_node.get_text(strip=True):
                parts.append("\n**C.14.a - Demand Feature Details:**")
                feature_details = {
                    "Issuer": get_text(demand_feature_node, 'identityOfDemandFeatureIssuer'),
                    "Amount Provided": get_text(demand_feature_node, 'amountProvidedByDemandFeatureIssuer'),
                    "Remaining Period": f"{get_text(demand_feature_node, 'remainingPeriodDemandFeature')} days",
                    "Is Conditional?": format_val(get_text(demand_feature_node, 'demandFeatureConditionalFlag')),
                }
                for key, val in feature_details.items():
                    if val != "—": parts.append(f"- **{key}:** {val}")
                
                ratings = [f"{get_text(n, 'nameOfNRSRO')}: {get_text(n, 'rating')}" for n in demand_feature_node.find_all('demandFeatureRatingOrNRSRO')]
                if ratings: parts.append(f"- **Ratings:** {'; '.join(ratings)}")

            guarantor_node = node.find('guarantor')
            if guarantor_node and guarantor_node.get_text(strip=True):
                parts.append("\n**C.15.a - Guarantor Details:**")
                guarantor_details = {
                    "Identity of Guarantor": get_text(guarantor_node, 'identityOfTheGuarantor'),
                    "Amount Provided": get_text(guarantor_node, 'amountProvidedByGuarantor'),
                }
                for key, val in guarantor_details.items():
                    if val != "—": parts.append(f"- **{key}:** {val}")

                ratings = [f"{get_text(n, 'nameOfNRSRO')}: {get_text(n, 'rating')}" for n in guarantor_node.find_all('guarantorRatingOrNRSRO')]
                if ratings: parts.append(f"- **Ratings:** {'; '.join(ratings)}")

            enhancement_node = node.find('enhancementProvider')
            if enhancement_node and enhancement_node.get_text(strip=True):
                parts.append("\n**C.16.a - Enhancement Details:**")
                enhancement_details = {
                    "Identity of Provider": get_text(enhancement_node, 'identityOfTheEnhancementProvider'),
                    "Type of Enhancement": get_text(enhancement_node, 'typeOfEnhancement'),
                    "Amount Provided": get_text(enhancement_node, 'amountProvidedByEnhancement'),
                }
                for key, val in enhancement_details.items():
                    if val != "—": parts.append(f"- **{key}:** {val}")

                ratings = [f"{get_text(n, 'nameOfNRSRO')}: {get_text(n, 'rating')}" for n in enhancement_node.find_all('enhancementRatingOrNRSRO')]
                if ratings: parts.append(f"- **Ratings:** {'; '.join(ratings)}")
            
            repo_node = node.find('repurchaseAgreement')
            if repo_node and repo_node.get_text(strip=True):
                parts.append("\n**C.8 - Repurchase Agreement Details:**")
                parts.append(f"- **Is Open?:** {format_val(get_text(repo_node, 'repurchaseAgreementOpenFlag'))}")
                collateral_issuers = repo_node.find_all('collateralIssuers')
                if collateral_issuers:
                    collateral_data = []
                    for issuer in collateral_issuers:
                        coupon_yield_str = get_text(issuer, 'couponOrYield')
                        try:
                            coupon_yield_formatted = f"{float(coupon_yield_str):.4f}%"
                        except (ValueError, TypeError):
                            coupon_yield_formatted = coupon_yield_str

                        collateral_data.append({
                            "Issuer Name": get_text(issuer, 'nameOfCollateralIssuer'),
                            "Maturity Date": get_text(issuer.find('maturityDate'), 'date'),
                            "Coupon/Yield": coupon_yield_formatted,
                            "Principal Amount": format_val(get_text(issuer, 'principalAmountToTheNearestCent'), 'dollar'),
                            "Collateral Value": format_val(get_text(issuer, 'valueOfCollateralToTheNearestCent'), 'dollar'),
                            "Category": get_text(issuer, 'ctgryInvestmentsRprsntsCollateral'),
                        })
                    parts.append("\n**Collateral:**\n" + to_compact_markdown(pd.DataFrame(collateral_data), index=False))

    sig = form_data.find('signature')
    if sig:
        parts.append("\n## N-MFP: Signatures")
        parts.append(f"**Registrant:** {get_text(sig, 'registrant')}")
        parts.append(f"**Date:** {get_text(sig, 'signatureDate')}")
        parts.append(f"**By:** {get_text(sig, 'signature')}")
        parts.append(f"**Name of Signing Officer:** {get_text(sig, 'nameOfSigningOfficer')}")
        parts.append(f"**Title of Signing Officer:** {get_text(sig, 'titleOfSigningOfficer')}")

    return "\n\n".join(parts)

def parse_form497_file(header_content: str) -> str:
    """
    Top-level parser for Form 497 content. It parses the general metadata
    and the specific series/class SGML data from within the header.
    """
    header_part = parse_sec_header(header_content)
    
    sgml_part = parse_form497_sgml(header_content)
    return f"{header_part}\n\n{sgml_part}".strip()

def parse_form497_sgml(header_content: str) -> str:
    """
    Parses the SGML content from within a Form 497 header using a robust
    regex-based approach to handle the malformed/unclosed tags correctly.
    """
    sgml_match = re.search(
        r"<SERIES-AND-CLASSES-CONTRACTS-DATA>(.*?)</SERIES-AND-CLASSES-CONTRACTS-DATA>",
        header_content,
        re.S | re.I
    )

    if not sgml_match:
        return "NO SERIES-AND-CLASSES-CONTRACTS-DATA BLOCK FOUND"

    sgml_content = sgml_match.group(1)

    md_parts = ["## Series and Classes Contracts Data"]

    series_blocks = re.split(r'<SERIES>', sgml_content, flags=re.I)[1:]

    if not series_blocks:
        return "<!-- Data block found, but no <SERIES> sections were detected inside. -->"

    for series_block in series_blocks:
        series_name_match = re.search(r'<SERIES-NAME>\s*([^\n<]+)', series_block, re.I)
        series_id_match = re.search(r'<SERIES-ID>\s*([^\n<]+)', series_block, re.I)

        series_name = series_name_match.group(1).strip() if series_name_match else "—"
        series_id = series_id_match.group(1).strip() if series_id_match else "—"

        md_parts.append(f"\n### {series_name} (Series ID: {series_id})")

        class_records = []
        
        class_contract_blocks = re.findall(
            r'<CLASS-CONTRACT>(.*?)(?=<CLASS-CONTRACT>|<SERIES>|$)', 
            series_block, 
            re.S | re.I
        )

        for class_block in class_contract_blocks:
            id_match = re.search(r'<CLASS-CONTRACT-ID>\s*([^\n<]+)', class_block, re.I)
            name_match = re.search(r'<CLASS-CONTRACT-NAME>\s*([^\n<]+)', class_block, re.I)
            ticker_match = re.search(r'<CLASS-CONTRACT-TICKER-SYMBOL>\s*([^\n<]+)', class_block, re.I)

            record = {
                'Class ID': id_match.group(1).strip() if id_match else "—",
                'Class Name': name_match.group(1).strip() if name_match else "—",
                'Ticker Symbol': ticker_match.group(1).strip() if ticker_match else "—",
            }
            class_records.append(record)

        if class_records:
            df = pd.DataFrame(class_records, columns=['Class Name', 'Ticker Symbol', 'Class ID'])
            md_table = df_to_markdown(df, is_clean=True)
            md_parts.append("---\n")
            md_parts.append(md_table)
        
        md_parts.append("\n---")

    final_md = "\n".join(md_parts).strip()
        
    return final_md

def parse_form_n_cen_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form N-CEN into a structured Markdown document.
    """
    def get_text(node, tag, strip_ns=True):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I)) or node.find(re.compile(f'(?:\\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_val(value_str: str, type_hint: str = 'string') -> str:
        """Robustly formats values based on their intended type."""
        if not value_str or value_str.lower() in ('—', 'n/a', 'na'): return "—"
        try:
            val_float = float(value_str.replace(',', ''))
            if type_hint == 'dollar': return f"${val_float:,.2f}"
            if type_hint == 'percent': return f"{val_float:.2f}%"
            if type_hint == 'shares': return f"{val_float:.4f}"
            if type_hint == 'number': return f"{val_float:,.2f}"
        except (ValueError, TypeError):
            pass

        if value_str.upper() == 'Y' or value_str.lower() == 'true': return "Yes"
        if value_str.upper() == 'N' or value_str.lower() == 'false': return "No"
        return value_str
        
    ORGANIZATION_TYPES = {
        "N-1A": "a. Open-end management investment company registered under the Act on Form N-1A",
        "N-2": "b. Closed-end management investment company registered under the Act on Form N-2",
        "N-3": "c. Separate account offering variable annuity contracts which is registered under the Act as a management investment company on Form N-3",
        "N-4": "d. Separate account offering variable annuity contracts which is registered under the Act as a unit investment trust on Form N-4",
        "N-5": "e. Small business investment company registered under the Act on Form N-5",
        "N-6": "f. Separate account offering variable insurance contracts which is registered under the Act as a unit investment trust on Form N-6",
        "N-8B-2": "g. Unit investment trust registered under the Act on Form N-8B-2"
    }
    
    FUND_TYPES_MAP = [
        ("Exchange-Traded Fund or Exchange-Traded Managed Fund or offers a Class that itself is an Exchange-Traded Fund or Exchange-Traded Managed Fund", None),
        ("Exchange-Traded Fund", "i."),
        ("Exchange-Traded Managed Fund", "ii."),
        ("Index Fund", "b."),
        ("Seeks to achieve performance results that are a multiple of a benchmark, the inverse of a benchmark, or a multiple of the inverse of a benchmark", "c."),
        ("Interval Fund", "d."),
        ("Fund of Funds", "e."),
        ("Master-Feeder Fund", "f."),
        ("Money Market Fund", "g."),
        ("Target Date Fund", "h."),
        ("Underlying fund to a variable annuity or variable life insurance contract", "i."),
        ("N/A", None)
    ]

    parts = ["# Form N-CEN: Annual Report for Registered Investment Companies"]
    
    header_data = xml.find('headerData')
    if header_data:
        filer_info_section = [f"**Submission Type:** {get_text(header_data, 'submissionType')}"]
        filer_creds = header_data.find('filer')
        if filer_creds:
            filer_info_section.append(f"**CIK:** {get_text(filer_creds, 'cik')}")
        parts.append("## N-CEN: Filer Information\n" + "\n".join(filer_info_section))

    parts.append("\n## N-CEN: Series/Class (Contract) Information")
    
    form_data = xml.find('formData')
    
    if form_data and (sc_info_form := form_data.find('seriesClass')):
        all_flag_node = sc_info_form.find('rptIncludeAllSeriesFlag')
        if all_flag_node is not None:
             include_all = all_flag_node.text.lower() == 'true'
             checkbox = '[x]' if include_all else '[ ]'
             parts.append(f"**Report includes all Series and Classes?:** {checkbox}")

    if header_data and (sc_info_header := header_data.find('seriesClass')):
        records = sc_info_header.find_all('rptSeriesClassInfo')
        
        series_records = []
        class_records = []
        for record in records:
            series_id = get_text(record, 'seriesId')
            if series_id != "—":
                series_records.append(series_id)
            for class_info in record.find_all('classInfo'):
                class_id = get_text(class_info, 'classId')
                if class_id != "—":
                    class_records.append(class_id)
        
        if series_records:
            for i, s_id in enumerate(series_records, 1):
                parts.append(f"\n**Series ID Record:{i}**\n- **Series ID:** {s_id}")

        if class_records:
             for i, c_id in enumerate(class_records, 1):
                parts.append(f"\n**Class ID Record:{i}**\n- **Class ID:** {c_id}")

    if not form_data:
        return "\n\n".join(parts)

    gen_info = form_data.find('generalInfo')
    if gen_info:
        filing_info_section = [f"### N-CEN: Part A: General Information"]
        report_period = gen_info.get('reportEndingPeriod', '—')
        is_lt_12 = gen_info.get('isReportPeriodLt12', '—')
        
        gen_data = {
            "Item A.1.a - Report for period ending": report_period,
            "Item A.1.b - Does this report cover a period of less than 12 months?": format_val(is_lt_12),
        }
        for key, val in gen_data.items():
            filing_info_section.append(f"**{key}:** {val}")
        parts.append("\n\n".join(filing_info_section))

    reg_info = form_data.find('registrantInfo')

    websites_node = reg_info.find('websites')
    website_node = websites_node.find('website') if websites_node else None

    if reg_info:
        parts.append("\n## N-CEN: Part B: Information About the Registrant")
        
        b1_b2_details = {
            "Item B.1.a - Full name of Registrant": get_text(reg_info, 'registrantFullName'),
            "Item B.1.b - Investment Company Act file number": get_text(reg_info, 'investmentCompFileNo'),
            "Item B.1.c - CIK": get_text(reg_info, 'registrantCik'),
            "Item B.1.d - LEI": get_text(reg_info, 'registrantLei'),
            "Item B.2.a - Street 1": get_text(reg_info, 'registrantstreet1'),
            "Item B.2.a - Street 2": get_text(reg_info, 'registrantstreet2'),
            "Item B.2.b - City": get_text(reg_info, 'registrantcity'),
            "Item B.2.c - State": get_text(reg_info, 'registrantstate').replace('US-', ''),
            "Item B.2.e - Zip Code": get_text(reg_info, 'registrantzipCode'),
            "Item B.2.f - Telephone": get_text(reg_info, 'registrantphoneNumber'),
            "Item B.2.g - Public Website": website_node.get('webpage', '—') if website_node else '—',
        }
        for key, val in b1_b2_details.items():
            if val and val.strip() != "—": parts.append(f"- **{key}:** {val}")

        locations = reg_info.find_all('locationBooksRecord')
        if locations:
            parts.append("\n### Item B.3 - Location of books and records")
            
            for i, loc in enumerate(locations, 1):
                parts.append(f"\n**Location books Record: {i}**")
                
                state_country_node = loc.find('officeStateCountry')
                
                location_details = {
                    "a. Name of person (e.g., a custodian of records)": get_text(loc, 'officeName'),
                    "b. Street 1": get_text(loc, 'officeAddress1'),
                    "Street 2": get_text(loc, 'officeAddress2'),
                    "c. City": get_text(loc, 'officeCity'),
                    "d. State, if applicable": (state_country_node.get('officeState', '—') if state_country_node else '—').replace('US-', ''),
                    "e. Foreign country, if applicable": state_country_node.get('officeCountry', '—') if state_country_node else '—',
                    "f. Zip code and zip code extension, or foreign postal code": get_text(loc, 'officeRecordsZipCode'),
                    "g. Telephone number": get_text(loc, 'officePhone'),
                    "h. Briefly describe the books and records kept at this location": get_text(loc, 'booksRecordsDesc'),
                }
                
                for key, val in location_details.items():
                    if val and val.strip() != "—":
                        key_formatted = key.replace("   ", "   ")
                        parts.append(f"- **{key_formatted}:** {val}")

        family_inv_comp_node = reg_info.find('registrantFamilyInvComp')

        b4_b5_details = {
            "Item B.4.a - Is this the first filing by the Registrant?": format_val(get_text(reg_info, 'isRegistrantFirstFiling')),
            "Item B.4.b - Is this the last filing by the Registrant?": format_val(get_text(reg_info, 'isRegistrantLastFiling')),
            
            "Item B.5.a - Is the Registrant part of a family of investment companies?":
                format_val(family_inv_comp_node.get('isRegistrantFamilyInvComp') if family_inv_comp_node else "—"),
            "Item B.5.a.i - Full name of family of investment companies":
                family_inv_comp_node.get('familyInvCompFullName', '—') if family_inv_comp_node else "—",
        }
        for key, val in b4_b5_details.items():
            if val and val.strip() != "—": parts.append(f"- **{key}:** {val}")
        
        parts.append("\n### Item B.6 - Organization")
        classification_type = get_text(reg_info, 'registrantClassificationType')
        for code, description in ORGANIZATION_TYPES.items():
            checkbox = '[x]' if code == classification_type else '[ ]'
            parts.append(f"- {checkbox} {description}")
        parts.append(f"- **Item B.6.i - Total number of Series:** {get_text(reg_info, 'totalSeries')}")

        parts.append(f"- **Item B.7 - Is the Registrant the issuer of a class of securities registered under the Securities Act?:** {format_val(get_text(reg_info, 'isSecuritiesActRegistration'))}")

        directors = reg_info.find_all('director')
        if directors:
            parts.append("\n### Item B.8 - Directors")
            dir_data = []
            for d in directors:
                file_nums = ", ".join(fn.get('fileNumber') for fn in d.find_all('fileNumberInfo'))
                dir_data.append({
                    "Name": get_text(d, 'directorName'),
                    "Is Interested Person?": format_val(get_text(d, 'isDirectorInterestedPerson')),
                    "Other Investment Company File Numbers": file_nums if file_nums else "N/A"
                })
            parts.append(to_compact_markdown(pd.DataFrame(dir_data), index=False))
        
        chief_compliance_officers = reg_info.find_all('chiefComplianceOfficer')
        parts.append("\n### Item B.9. Chief compliance officer.")

        if chief_compliance_officers:
            for i, cco in enumerate(chief_compliance_officers, 1):
                parts.append(f"\n**Chief compliance officer Record: {i}**")
                
                state_country_node = cco.find('ccoStateCountry')

                cco_details = {
                    "a. Full Name": get_text(cco, 'ccoName'),
                    "b. CRD Number, if any": get_text(cco, 'crdNumber'),
                    "c. Street Address 1": get_text(cco, 'ccoStreet1'),
                    "   Street Address 2": get_text(cco, 'ccoStreet2'),
                    "d. City": get_text(cco, 'ccoCity'),
                    "e. State, if applicable": (state_country_node.get('ccoState', '—') if state_country_node else '—').replace('US-', ''),
                    "f. Foreign country, if applicable": state_country_node.get('ccoCountry', '—') if state_country_node else '—',
                    "g. Zip code": get_text(cco, 'ccoZipCode'),
                    "h. Telephone number": get_text(cco, 'ccoPhone'),
                    "i. Has the chief compliance officer changed since the last filing?": format_val(get_text(cco, 'isCcoChangedSinceLastFiling')),
                }

                for key, val in cco_details.items():
                    if val and val.strip() != "—":
                        key_formatted = key.replace("   ", "   ")
                        parts.append(f"- **{key_formatted}:** {val}")

                employers = cco.find_all('ccoEmployer')
                if employers:
                    parts.append("\nIf the chief compliance officer is compensated or employed by any person other than the Registrant, provide:")
                    for j, emp in enumerate(employers, 1):
                        parts.append(f"**CCO employer Record: {j}**")
                        parts.append(f"- **i. Name of the person:** {get_text(emp, 'ccoEmployerName')}")
                        parts.append(f"- **ii. Person’s IRS Employer Identification Number:** {get_text(emp, 'ccoEmployerId')}")
        else:
            parts.append("No Chief Compliance Officer reported.")

        parts.append("\n### Item B.10. Matters for security holder vote.")
        
        submitted_matter_val = get_text(reg_info, 'isRegistrantSubmittedMatter')
        parts.append(f"- **Were any matters submitted by the Registrant for its security holders’ vote during the reporting period?** {format_val(submitted_matter_val)}")
        
        security_matter_node = reg_info.find('securityMatterSeriesInfo')
        if security_matter_node:
            series_infos = security_matter_node.find_all('seriesInfo')
            if series_infos:
                series_data = [{"Series Name": s.get('seriesName'), "Series ID": s.get('seriesId')} for s in series_infos]
                parts.append(to_compact_markdown(pd.DataFrame(series_data), index=False))

        covered_by_insurance_node = reg_info.find('coveredByInsurancePolicy')

        b11_b15_details = {
            "Item B.11.a - Have there been any material legal proceedings?": format_val(get_text(reg_info, 'isPreviousLegalProceeding')),
            "Item B.11.b - Has any proceeding previously reported been terminated?": format_val(get_text(reg_info, 'isPreviousProceedingTerminated')),
            "Item B.12.a - Were any claims with respect to the Registrant filed under a fidelity bond?": format_val(get_text(reg_info, 'isClaimFiled')),
            
            "Item B.13.a - Are the Registrant's officers or directors covered under any insurance policy?": \
                format_val(covered_by_insurance_node.get('isCoveredByInsurancePolicy') if covered_by_insurance_node else "—"),
            "Item B.13.a.i - If yes, were any claims filed under the policy during the reporting period?": \
                format_val(covered_by_insurance_node.get('isClaimFiledDuringPeriod') if covered_by_insurance_node else "—"),
                
            "Item B.14 - Did an affiliated person provide any form of financial support to the Registrant?": format_val(get_text(reg_info, 'isFinancialSupportDuringPeriod')),
            
            "Item B.15.a - Did the Registrant rely on any exemptive orders from the Commission?": format_val(get_text(reg_info, 'isExemptionFromAct')),
        }

        for key, val in b11_b15_details.items():
             if val and val.strip() != "—": parts.append(f"- **{key}:** {val}")
        
        release_numbers = reg_info.find_all('releaseNumberInfo')
        if release_numbers:
            release_list = [f"  - {rn.get('releaseNumber')}" for rn in release_numbers]
            parts.append("- **Item B.15.a.i - Release numbers:**\n" + "\n".join(release_list))

        underwriters = reg_info.find_all('principalUnderwriter')
        parts.append("\n### Item B.16. Principal underwriters.")
        if underwriters:
            for i, uw in enumerate(underwriters, 1):
                parts.append(f"\n**Principal underwriter Record: {i}**")

                state_country_node = uw.find('principalUnderWriterStateCountry')

                uw_details = {
                    "i. Full name": get_text(uw, 'principalUnderwriterName'),
                    "ii. SEC file number": get_text(uw, 'principalUnderwriterFileNumber'),
                    "iii. CRD number": get_text(uw, 'principalUnderwriterCrdNumber'),
                    "iv. LEI, if any": get_text(uw, 'principalUnderwriterLei'),
                    "v. State, if applicable": (state_country_node.get('principalUnderWriterState', '—') if state_country_node else '—').replace('US-', ''),
                    "vi. Foreign country, if applicable": state_country_node.get('principalUnderWriterCountry', '—') if state_country_node else '—',
                    "vii. Is the principal underwriter an affiliated person...?": format_val(get_text(uw, 'isPrincipalUnderwriterAffiliatedWithRegistrant')),
                }
                for key, val in uw_details.items():
                    if val and val.strip() != "—":
                        parts.append(f"- **{key}:** {val}")

            parts.append(f"- **b. Have any principal underwriters been hired or terminated during the reporting period?** {format_val(get_text(reg_info, 'isUnderwriterHiredOrTerminated'))}")
        else:
            parts.append("No Principal Underwriters reported.")

        accountants = reg_info.find_all('publicAccountant')
        parts.append("\n### Item B.17. Independent public accountant.")
        if accountants:
            for i, acc in enumerate(accountants, 1):
                parts.append(f"\n**Public accountant Record: {i}**")
                
                state_country_node = acc.find('publicAccountantStateCountry')

                acc_details = {
                    "a. Full Name": get_text(acc, 'publicAccountantName'),
                    "b. PCAOB Number": get_text(acc, 'pcaobNumber'),
                    "c. LEI, if any": get_text(acc, 'publicAccountantLei'),
                    "d. State, if applicable": (state_country_node.get('publicAccountantState', '—') if state_country_node else '—').replace('US-', ''),
                    "e. Foreign country, if applicable": state_country_node.get('publicAccountantCountry', '—') if state_country_node else '—',
                }
                for key, val in acc_details.items():
                    if val and val.strip() != "—":
                        parts.append(f"- **{key}:** {val}")

            parts.append(f"- **f. Has the independent public accountant changed since the last filing?** {format_val(get_text(reg_info, 'isPublicAccountantChanged'))}")
        else:
            parts.append("No Independent Public Accountants reported.")
            
        b18_b23_details = {
            "Item B.18 - Did an independent public accountant's report on internal control note any material weaknesses?": format_val(get_text(reg_info, 'isMaterialWeakness')),
            "Item B.19 - Did an independent public accountant issue an opinion other than an unqualified opinion?": format_val(get_text(reg_info, 'isOpinionOffered')),
            "Item B.20 - Have there been material changes in the method of valuation?": format_val(get_text(reg_info, 'isMaterialChange')),
            "Item B.21 - Have there been any changes in accounting principles or practices?": format_val(get_text(reg_info, 'isAccountingPrincipleChange')),
            "Item B.22.a - Were any payments made to shareholders as a result of an error in calculating NAV?": format_val(get_text(reg_info, 'isPaymentErrorInNetAssetValue')),
            "Item B.23 - Did the Registrant pay any dividend or make any distribution required to be accompanied by a written statement?": format_val(get_text(reg_info, 'isPaymentDividend')),
        }
        for key, val in b18_b23_details.items():
            if val and val.strip() != "—": parts.append(f"- **{key}:** {val}")

    series_questions = form_data.find_all('managementInvestmentQuestion')
    if series_questions:
        parts.append("\n## Part C: Additional Questions for Management Investment Companies")
        for i, s in enumerate(series_questions, 1):
            parts.append(f"\n### Management Investment Record: {i} - {get_text(s, 'mgmtInvFundName')}")

            parts.append("\n**Item C.1. Background information.**")
            c1_details = {
                "a. Full Name of the Fund": get_text(s, 'mgmtInvFundName'),
                "b. Series identification number, if any": get_text(s, 'mgmtInvSeriesId'),
                "c. LEI": get_text(s, 'mgmtInvLei'),
                "d. Is this the first filing on this form by the Fund?": format_val(get_text(s, 'isFirstFilingByFund')),
            }
            for key, val in c1_details.items():
                if val and val.strip() != "—":
                    parts.append(f"- **{key}:** {val}")

            parts.append("\n**Item C.2. Classes of open-end management investment companies.**")
            c2_details = {
                "a. How many Classes of shares of the Fund (if any) are authorized?": get_text(s, 'numAuthorizedClass'),
                "b. How many new Classes of shares of the Fund were added during the reporting period?": get_text(s, 'numAddedClass'),
                "c. How many Classes of shares of the Fund were terminated during the reporting period?": get_text(s, 'numTerminatedClass'),
            }
            for key, val in c2_details.items():
                if val and val.strip() != "—":
                    parts.append(f"- **{key}:** {val}")
            
            outstanding_classes = s.find_all('sharesOutstanding')
            if outstanding_classes:
                parts.append("\n**d. For each Class with shares outstanding, provide the information requested below:**")
                class_data = []
                for j, c in enumerate(outstanding_classes, 1):
                    class_data.append({
                        "Shares Outstanding Record": j,
                        "i. Full name of Class": c.get('sharesOutstandingClassName'), 
                        "ii. Class identification number, if any": c.get('sharesOutstandingClassId'), 
                        "iii. Ticker symbol, if any": c.get('sharesOutstandingTickerSymbol')
                    })
                parts.append(to_compact_markdown(pd.DataFrame(class_data), index=False))

            fund_type_tags = s.find_all('fundType')
            if fund_type_tags:
                parts.append("\n**Item C.3. Type of fund.**")
                selected_types = {ft.text.strip() for ft in fund_type_tags}
                
                for type_desc, prefix in FUND_TYPES_MAP:
                    checkbox = '[x]' if type_desc in selected_types else '[ ]'
                    indent = "  " if prefix and prefix.startswith('i') else ""
                    prefix_str = f"{prefix} " if prefix else ""
                    parts.append(f"- {indent}{checkbox} {prefix_str}{type_desc}")

            parts.append(f"\n**Item C.4 - Does the Fund seek to operate as a 'non-diversified company'?** {format_val(get_text(s, 'isNonDiversifiedCompany'))}")
            parts.append(f"**Item C.5 - Does the fund invest in a controlled foreign corporation?** {format_val(get_text(s, 'isForeignSubsidiary'))}")
            
            parts.append(f"\n**Item C.6. Securities lending.**")
            parts.append(f"- **a. Is the Fund authorized to engage in securities lending transactions?** {format_val(get_text(s, 'isFundSecuritiesLending'))}")
            
            fund_lend_securities_node = s.find('fundLendSecurities')
            if fund_lend_securities_node:
                parts.append(f"- **b. Did the Fund lend any of its securities during the reporting period?** {format_val(fund_lend_securities_node.get('didFundLendSecurities'))}")
                
                parts.append(f"  - **i. If yes, during the reporting period, did any borrower fail to return the loaned securities by the contractual deadline with the result that:**")
                parts.append(f"    - **1. The Fund (or it securities lending agent) liquidated collateral pledged to secure the loaned securities?** {format_val(get_text(fund_lend_securities_node, 'isFundLiquidated'))}")
                parts.append(f"    - **2. The Fund was otherwise adversely impacted?** {format_val(get_text(fund_lend_securities_node, 'isFundAdverselyImpacted'))}")

            security_lending_agents = s.find_all('securityLending')
            if security_lending_agents:
                parts.append("\n**c. Provide the information requested below about each securities lending agent, if any, retained by the Fund:**")
                for k, agent in enumerate(security_lending_agents, 1):
                    parts.append(f"\n**Securities Lending Record: {k}**")
                    parts.append(f"- **i. Full name of securities lending agent:** {get_text(agent, 'securitiesAgentName')}")
                    parts.append(f"- **ii. LEI, if any:** {get_text(agent, 'securitiesAgentLei')}")
                    parts.append(f"- **iii. Is the securities lending agent an affiliated person...?** {format_val(get_text(agent, 'isSecuritiesAgentAffiliated'))}")
                    
                    indemnity_node = agent.find('securityAgentIdemnity')
                    if indemnity_node:
                        parts.append(f"- **iv. Does the securities lending agent... indemnify the Fund against borrower default?** {format_val(indemnity_node.get('isSecurityAgentIdemnity'))}")
                        
                        idemnity_providers = indemnity_node.find_all('idemnityProvider')
                        if idemnity_providers:
                            parts.append("- **v. If the entity providing the indemnification is not the securities lending agent, provide the following information:**")
                            for m, provider in enumerate(idemnity_providers, 1):
                                parts.append(f"  **Idemnity Providers Record: {m}**")
                                parts.append(f"  - **1. Name of person providing indemnification:** {get_text(provider, 'idemnityProviderName')}")
                                parts.append(f"  - **2. LEI, if any:** {get_text(provider, 'idemnityProviderLei')}")
                        
                        parts.append(f"- **vi. Did the Fund exercise its indemnification rights during the reporting period?** {format_val(get_text(indemnity_node, 'didIndemnificationRights'))}")

            collateral_managers = s.find_all('collateralManager')
            if collateral_managers:
                 parts.append("\n**d. If a person providing cash collateral management services to the Fund in connection with the Fund's securities lending activities does not also serve as securities lending agent, provide the following information about each cash collateral manager:**")
                 for k, manager in enumerate(collateral_managers, 1):
                    parts.append(f"\n**Collateral Managers Record: {k}**")
                    parts.append(f"- **i. Full name of cash collateral manager:** {manager.get('collateralManagerName', '—')}")
                    parts.append(f"- **ii. LEI, if any:** {manager.get('collateralManagerLei', '—')}")
                    parts.append(f"- **iii. Is the cash collateral manager an affiliated person, or an affiliated person of an affiliated person, of a securities lending agent retained by the Fund??** {format_val(manager.get('isCollateralManagerAffliliated'))}")
                    parts.append(f"- **iv. Is the cash collateral manager an affiliated person of the Fund?** {format_val(manager.get('isCollateralManagerAffliliatedWithFund'))}")
            
            payment_types_node = s.find('paymentToAgentManagers')
            if payment_types_node:
                parts.append("\n**e. Types of payments made to one or more securities lending agents and cash collateral managers (check all that apply):**")
                selected_payments = {p.text.strip() for p in payment_types_node.find_all('paymentToAgentManagerType')}
                all_payment_types = [
                    "Revenue sharing split", "Fee-based revenue split (other than administrative fee)", "Administrative fee",
                    "Cash collateral reinvestment fee", "Indemnification fee", "Other", "N/A"
                ]
                for p_type in all_payment_types:
                    checkbox = '[x]' if p_type in selected_payments else '[ ]'
                    parts.append(f"- {checkbox} {p_type}")

            parts.append(f"\n- **f. Provide the monthly average of the value of portfolio securities on loan during the reporting period:** {format_val(get_text(s, 'avgPortfolioSecuritiesValue'), 'dollar')}")
            parts.append(f"- **g. Provide the net income from securities lending activities:** {format_val(get_text(s, 'netIncomeSecuritiesLending'), 'dollar')}")

            rely_on_rule_node = s.find('relyOnRuleTypes')
            if rely_on_rule_node:
                parts.append("\n**Item C.7. Reliance on certain statutory exemption and rules.**")
                rules = [rule.text.strip() for rule in rely_on_rule_node.find_all('relyOnRuleType')]
                if rules:
                    parts.append("Did the Fund rely on the following rules?")
                    for rule in rules:
                        parts.append(f"- {rule}")
                else:
                    parts.append("No reliance on statutory exemptions or rules reported.")
            
            parts.append("\n**Item C.8. Expense limitations.**")
            c8_details = {
                "a. Did the Fund have an expense limitation arrangement?": format_val(get_text(s, 'isExpenseLimitationInPlace')),
                "b. Were any expenses reduced or waived?": format_val(get_text(s, 'isExpenseReducedOrWaived')),
                "c. Are the fees waived subject to recoupment?": format_val(get_text(s, 'isFeesWaivedRecoupable')),
                "d. Were any expenses previously waived recouped during the period?": format_val(get_text(s, 'isExpenseWaivedRecoupable')),
            }
            for key, val in c8_details.items():
                 if val and val.strip() != "—":
                    parts.append(f"- **{key}** {val}")

            advisers = s.find_all('investmentAdviser')
            parts.append("\n**Item C.9. Investment advisers.**")
            if advisers:
                for i, adviser in enumerate(advisers, 1):
                    parts.append(f"\n**Investment Advisers Record: {i}**")
                    
                    state_country_node = adviser.find('investmentAdviserStateCountry')

                    adviser_details = {
                        "i. Full name": get_text(adviser, 'investmentAdviserName'),
                        "ii. SEC file number": get_text(adviser, 'investmentAdviserFileNo'),
                        "iii. CRD number": get_text(adviser, 'investmentAdviserCrdNo'),
                        "iv. LEI, if any": get_text(adviser, 'investmentAdviserLei'),
                        "v. State, if applicable": (state_country_node.get('investmentAdviserState', '—') if state_country_node else '—').replace('US-', ''),
                        "vi. Foreign country, if applicable": state_country_node.get('investmentAdviserCountry', '—') if state_country_node else '—',
                        "vii. Was the investment adviser hired during the reporting period?": format_val(get_text(adviser, 'isInvestmentAdviserHired')),
                    }
                    for key, val in adviser_details.items():
                        if val and val.strip() != "—":
                            parts.append(f"- **{key}:** {val}")
            else:
                parts.append("No Investment Advisers reported.")
            
            sub_advisers = s.find_all('subAdviser')
            if sub_advisers:
                parts.append("\n**Item C.9.b. Sub-advisers.**")
                for i, adviser in enumerate(sub_advisers, 1):
                    parts.append(f"\n**Sub-adviser Record: {i}**")
                    sub_adviser_details = {
                        "i. Full name": get_text(adviser, 'subAdviserName'),
                        "ii. SEC file number": get_text(adviser, 'subAdviserFileNo'),
                        "iii. CRD number": get_text(adviser, 'subAdviserCrdNo'),
                        "iv. LEI, if any": get_text(adviser, 'subAdviserLei'),
                        "v. Is the sub-adviser an affiliated person?": format_val(get_text(adviser, 'isSubAdviserAffiliated')),
                        "vi. Foreign country, if applicable": get_text(adviser, 'subAdviserCountry'),
                        "vii. Was the sub-adviser hired during the reporting period?": format_val(get_text(adviser, 'isSubAdviserHired')),
                    }
                    for key, val in sub_adviser_details.items():
                        if val and val.strip() != "—":
                            parts.append(f"- **{key}:** {val}")

            transfer_agents = s.find_all('transferAgent')
            parts.append("\n**Item C.10. Transfer agents.**")
            if transfer_agents:
                for i, agent in enumerate(transfer_agents, 1):
                    parts.append(f"\n**Transfer Agents Record: {i}**")

                    state_country_node = agent.find('transferAgentStateCountry')

                    agent_details = {
                        "i. Full name": get_text(agent, 'transferAgentName'),
                        "ii. SEC file number": get_text(agent, 'transferAgentFileNo'),
                        "iii. LEI, if any": get_text(agent, 'transferAgentLei'),
                        "iv. State, if applicable": (state_country_node.get('transferAgentState', '—') if state_country_node else '—').replace('US-', ''),
                        "v. Foreign country, if applicable": state_country_node.get('transferAgentCountry', '—') if state_country_node else '—',
                        "vi. Is the transfer agent an affiliated person of the Fund or its investment adviser(s)?": format_val(get_text(agent, 'isTransferAgentAffiliated')),
                        "vii. Is the transfer agent a sub-transfer agent?": format_val(get_text(agent, 'isTransferAgentSubAgent')),
                    }
                    for key, val in agent_details.items():
                        if val and val.strip() != "—":
                            parts.append(f"- **{key}:** {val}")
                
                parts.append(f"- **b. Has a transfer agent been hired or terminated during the reporting period?** {format_val(get_text(s, 'isTransferAgentHiredOrTerminated'))}")
            else:
                parts.append("No Transfer Agents reported.")

            pricing_services = s.find_all('pricingService')
            parts.append("\n**Item C.11. Pricing services.**")
            if pricing_services:
                for i, service in enumerate(pricing_services, 1):
                    parts.append(f"\n**Pricing Services Record: {i}**")
                    
                    state_country_node = service.find('pricingServiceStateCountry')

                    service_details = {
                        "i. Full name": get_text(service, 'pricingServiceName'),
                        "ii. LEI, if any, or provide and describe other identifying number": get_text(service, 'pricingServiceLei'),
                        "Description of other identifying number": get_text(service, 'pricingServiceIdNumberDesc'),
                        "iii. State, if applicable": (state_country_node.get('pricingServiceState', '—') if state_country_node else '—').replace('US-', ''),
                        "iv. Foreign country, if applicable": state_country_node.get('pricingServiceCountry', '—') if state_country_node else '—',
                        "v. Is the pricing service an affiliated person of the Fund or its investment adviser(s)?": format_val(get_text(service, 'isPricingServiceAffiliated')),
                    }
                    for key, val in service_details.items():
                        if val and val.strip() != "—":
                            key_formatted = key.replace("  ", "  ")
                            parts.append(f"- **{key_formatted}:** {val}")

                parts.append(f"- **b. Was a pricing service hired or terminated during the reporting period?** {format_val(get_text(s, 'isPricingServiceHiredOrTerminated'))}")
            else:
                parts.append("No Pricing Services reported.")
            
            custodians = s.find_all('custodian')
            parts.append("\n**Item C.12. Custodians.**")
            if custodians:
                parts.append("\n**a. Provide the following information about each person that provided custodial services to the Fund during the reporting period:**")
                for i, custodian in enumerate(custodians, 1):
                    parts.append(f"\n**Custodians Record: {i}**")
                    
                    state_country_node = custodian.find('custodianStateCountry')

                    custodian_details = {
                        "i. Full name": get_text(custodian, 'custodianName'),
                        "ii. LEI, if any": get_text(custodian, 'custodianLei'),
                        "iii. State, if applicable": (state_country_node.get('custodianState', '—') if state_country_node else '—').replace('US-', ''),
                        "iv. Foreign country, if applicable": state_country_node.get('custodianCountry', '—') if state_country_node else '—',
                        "v. Is the custodian an affiliated person of the Fund or its investment adviser(s)?": format_val(get_text(custodian, 'isCustodianAffiliated')),
                        "vi. Is the custodian a sub-custodian?": format_val(get_text(custodian, 'isSubCustodian')),
                        "vii. With respect to the custodian, check below to indicate the type of custody": get_text(custodian, 'custodyType'),
                    }
                    
                    for key, val in custodian_details.items():
                        if val and val.strip() != "—":
                            parts.append(f"- **{key}:** {val}")

                parts.append(f"\n- **b. Was a custodian hired or terminated during the reporting period?** {format_val(get_text(s, 'isCustodianHiredOrTerminated'))}")
            else:
                parts.append("No Custodians reported.")
                
            shareholder_agents = s.find_all('shareholderServicingAgent')
            parts.append("\n**Item C.13 - Shareholder Servicing Agents**")
            if shareholder_agents:
                for i, sa in enumerate(shareholder_agents, 1):
                    parts.append(f"\n**Shareholder Servicing Agents Record: {i}**")
                    
                    state_country_node = sa.find('shareholderServiceAgentStateCountry')
                    
                    sa_details = {
                        "i. Full name": get_text(sa, 'shareholderServiceAgentName'),
                        "ii. LEI, if any": get_text(sa, 'shareholderServiceAgentLei'),
                        
                        "iii. State, if applicable": (state_country_node.get('shareholderServiceAgentState', '—') if state_country_node else '—').replace('US-', ''),
                        "iv. Foreign country, if applicable": state_country_node.get('shareholderServiceAgentCountry', '—') if state_country_node else '—',
                        
                        "v. Is the shareholder servicing agent an affiliated person?": format_val(get_text(sa, 'isShareholderServiceAgentAffiliated')),
                        "vi. Is the shareholder servicing agent a sub-shareholder servicing agent?": format_val(get_text(sa, 'isShareholderServiceAgentSubshare')),
                    }
                    for key, val in sa_details.items():
                        if val and val.strip() != "—":
                            parts.append(f"- **{key}:** {val}")
                
                parts.append(f"- **b. Has a shareholder servicing agent been hired or terminated during the reporting period?** {format_val(get_text(s, 'isShareholderServiceHiredTerminated'))}")

            else:
                parts.append("No Shareholder Servicing Agents reported.")

            admins = s.find_all('admin')
            parts.append("\n**Item C.14. Administrators.**")
            if admins:
                parts.append("\n**a. Provide the following information about each administrator of the Fund:**")
                for i, admin in enumerate(admins, 1):
                    parts.append(f"\n**Administrators Record: {i}**")
                    
                    state_country_node = admin.find('adminStateCountry')

                    admin_details = {
                        "i. Full name": get_text(admin, 'adminName'),
                        "ii. LEI, if any, or other identifying number": get_text(admin, 'adminLei'),
                        "iii. State, if applicable": (state_country_node.get('adminState', '—') if state_country_node else '—').replace('US-', ''),
                        "iv. Foreign country, if applicable": state_country_node.get('adminCountry', '—') if state_country_node else '—',
                        "v. Is the administrator an affiliated person of the Fund or its investment adviser(s)?": format_val(get_text(admin, 'isAdminAffiliated')),
                        "vi. Is the administrator a sub-administrator?": format_val(get_text(admin, 'isAdminSubAdmin')),
                    }
                    
                    for key, val in admin_details.items():
                        if val and val.strip() != "—":
                            parts.append(f"- **{key}:** {val}")

                parts.append(f"\n- **b. Has a third-party administrator been hired or terminated during the reporting period?** {format_val(get_text(s, 'isAdminHiredOrTerminated'))}")
            else:
                parts.append("No Administrators reported.")
                
            affiliated_brokers = s.find_all('brokerDealer')
            parts.append("\n**Item C.15 - Affiliated broker-dealers.**")
            if affiliated_brokers:
                for i, ab in enumerate(affiliated_brokers, 1):
                    parts.append(f"\n**Broker Dealers Record: {i}**")

                    state_country_node = ab.find('brokerDealerStateCountry')

                    ab_details = {
                        "a. Full name": get_text(ab, 'brokerDealerName'),
                        "b. SEC file number": get_text(ab, 'brokerDealerFileNo'),
                        "c. CRD number": get_text(ab, 'brokerDealerCrdNo'),
                        "d. LEI, if any": get_text(ab, 'brokerDealerLei'),

                        "e. State, if applicable": (state_country_node.get('brokerDealerState', '—') if state_country_node else '—').replace('US-', ''),
                        "f. Foreign country, if applicable": state_country_node.get('brokerDealerCountry', '—') if state_country_node else '—',
                        
                        "g. Total commissions paid to the affiliated broker-dealer for the reporting period:": format_val(get_text(ab, 'brokerDealerCommission'), 'dollar'),
                    }
                    for key, val in ab_details.items():
                        if val and val.strip() != "—":
                            parts.append(f"- **{key}:** {val}")
            else:
                parts.append("No Affiliated Broker-Dealers reported.")

            brokers = s.find_all('broker')
            parts.append("\n**Item C.16. Brokers.**")
            
            if brokers:
                parts.append("\n**a. For each of the ten brokers that received the largest dollar amount of brokerage commissions...**")
                
                for i, broker in enumerate(brokers, 1):
                    parts.append(f"\n**Brokers Record: {i}**")
                    
                    state_country_node = broker.find('brokerStateCountry')

                    broker_details = {
                        "i. Full name of broker": get_text(broker, 'brokerName'),
                        "ii. SEC file number": get_text(broker, 'brokerFileNo'),
                        "iii. CRD number": get_text(broker, 'brokerCrdNo'),
                        "iv. LEI, if any": get_text(broker, 'brokerLei'),
                        "v. State, if applicable": (state_country_node.get('brokerState', '—') if state_country_node else '—').replace('US-', ''),
                        "vi. Foreign country, if applicable": state_country_node.get('brokerCountry', '—') if state_country_node else '—',
                        "vii. Gross commissions paid by the Fund for the reporting period": format_val(get_text(broker, 'grossCommission'), 'dollar'),
                    }
                    
                    for key, val in broker_details.items():
                        if val and val.strip() != "—":
                            parts.append(f"- **{key}:** {val}")

                aggregate_commission = get_text(s, 'aggregateCommission')
                if aggregate_commission and aggregate_commission.strip() != "—":
                    parts.append(f"\n**Aggregate Commission:** {format_val(aggregate_commission, 'dollar')}")
            else:
                parts.append("No Brokers reported.")

            principal_transactions = s.find_all('principalTransaction')
            if principal_transactions:
                parts.append("\n**Item C.17.a. Principal transaction counterparties.**")
                principal_data = []
                for pt in principal_transactions:
                    state_country_node = pt.find('principalStateCountry')
                    principal_data.append({
                        "Name": get_text(pt, 'principalName'),
                        "SEC file number": get_text(pt, 'principalFileNo'),
                        "CRD number": get_text(pt, 'principalCrdNo'),
                        "LEI": get_text(pt, 'principalLei'),
                        "State": (state_country_node.get('principalState', '—') if state_country_node else '—').replace('US-', ''),
                        "Country": state_country_node.get('principalCountry', '—') if state_country_node else '—',
                        "Total Purchase/Sale ($)": format_val(get_text(pt, 'principalTotalPurchaseSale'), 'dollar'),
                    })
                if principal_data:
                    df = pd.DataFrame(principal_data)
                    parts.append(to_compact_markdown(df, index=False))
                
            c17_c19_details = {
                "Item C.17.b - Aggregate value of principal purchase/sale transactions": format_val(get_text(s, 'principalAggregatePurchase'), 'dollar'),
                "Item C.18 - Did the Fund pay commissions for 'brokerage and research services'?": format_val(get_text(s, 'isBrokerageResearchPayment')),
                "Item C.19.a - Fund's monthly average net assets": format_val(get_text(s, 'mnthlyAvgNetAssets'), 'dollar'),
                "Item C.19.b - Money market fund's daily average net assets": format_val(get_text(s, 'dailyAvgNetAssets'), 'dollar'),
            }
            for key, val in c17_c19_details.items():
                if val and val.strip() != "—":
                    parts.append(f"- **{key}:** {val}")

            parts.append("\n**Item C.20. Lines of credit, interfund lending and interfund borrowing.**")
            line_of_credit_node = s.find('lineOfCredit')
            if line_of_credit_node:
                parts.append(f"- **a. Does the Fund have available a line of credit?** {format_val(line_of_credit_node.get('hasLineOfCredit'))}")
                
                credit_details = line_of_credit_node.find_all('lineOfCreditDetail')
                if credit_details:
                    parts.append("\n**If yes, for each line of credit, provide the information requested below:**")
                    for j, detail in enumerate(credit_details, 1):
                        parts.append(f"\n**Line of Credit details Record: {j}**")
                        parts.append(f"- **i. Is the line of credit a committed or uncommitted line of credit?** {get_text(detail, 'isCreditLineCommitted')}")
                        parts.append(f"- **ii. What size is the line of credit?** {format_val(get_text(detail, 'lineOfCreditSize'), 'dollar')}")
                        
                        institutions = detail.find_all('lineOfCreditInstitution')
                        if institutions:
                            parts.append("\n- **iii. With which institution(s) is the line of credit?**")
                            for k, inst in enumerate(institutions, 1):
                                parts.append(f"  - **Line Institutions Record: {k} Name of institution:** {inst.get('creditInstitutionName', '—')}")

                        shared_credit_node = detail.find('sharedCreditType')
                        if shared_credit_node:
                            credit_type = shared_credit_node.get('creditType', '—')
                            parts.append(f"\n- **iv. Is the line of credit just for the Fund, or is it shared among multiple funds?** {credit_type}")
                            
                            credit_users = shared_credit_node.find_all('creditUser')
                            if credit_users:
                                parts.append("\n  - **1. If shared, list the names of other funds that may use the line of credit:**")
                                user_data = []
                                for user in credit_users:
                                    user_data.append({
                                        "Name of fund": user.get('fundName', '—'),
                                        "SEC File number": user.get('secFileNo', '—')
                                    })
                                if user_data:
                                    parts.append(to_compact_markdown(pd.DataFrame(user_data), index=False))

                        parts.append(f"\n- **v. Did the Fund draw on the line of credit this period?** {format_val(get_text(detail, 'isCreditLineUsed'))}")

            parts.append(f"\n- **b. Did the Fund engage in interfund lending?** {format_val(get_text(s, 'isInterfundLending'))}")
            parts.append(f"- **c. Did the Fund engage in interfund borrowing?** {format_val(get_text(s, 'isInterfundBorrowing'))}")

            swing_pricing_val = get_text(s, 'isSwingPricing')
            if swing_pricing_val and swing_pricing_val != "—":
                parts.append("\n**Item C.21. Swing pricing.**")
                parts.append("- **a. Did the Fund (if not a Money Market Fund, Exchange-Traded Fund, or Exchange-Traded Managed Fund) engage in swing pricing?** " + format_val(swing_pricing_val))

    etf_info = form_data.find('exchangeSeriesInfo')
    if etf_info:
        parts.append("\n## Part E: Additional Questions for ETFs and ETMFs")
        for etf in etf_info.find_all('exchangeTradedFund'):
            parts.append(f"\n### {get_text(etf, 'fundName')}")
            
            exchange_node = etf.find('securityExchange')
            if exchange_node:
                parts.append("\n**Item E.1 - Exchange**")
                parts.append(f"- **Exchange:** {exchange_node.get('fundExchange', '—')}")
                parts.append(f"- **Ticker:** {exchange_node.get('fundsTickerSymbol', '—')}")

            auth_parts = etf.find_all('authorizedParticipant')
            if auth_parts:
                parts.append("\n**Item E.2 - Authorized Participants**")
                ap_data = [{"Name": ap.get('authorizedParticipantName', '—'), 
                            "Purchase Value": format_val(ap.get('authorizedParticipantPurchaseValue', '—'), 'dollar'), 
                            "Redeem Value": format_val(ap.get('authorizedParticipantRedeemValue', '—'), 'dollar')} for ap in auth_parts]
                parts.append(to_compact_markdown(pd.DataFrame(ap_data), index=False))

            parts.append("\n**Item E.3 - Creation Units**")
            e3_details = {
                "a. Number of Fund shares required to form a creation unit": format_val(get_text(etf, 'creationUnitNumOfShares'), 'number'),
                "b.i. Average percentage of value composed of cash (purchased)": format_val(get_text(etf, 'averagePercentagePurchased'), 'percent'),
                "c.i. Average percentage of value composed of cash (redeemed)": format_val(get_text(etf, 'averagePercentageRedeemed'), 'percent'),
                "d.i.2. Average transaction fee (dollars for one or more units, purchased)": format_val(get_text(etf, 'creationUnitTransactionFeeManyUnits'), 'dollar'),
                "d.i.3. Average transaction fee (percentage of value, purchased)": format_val(get_text(etf, 'creationUnitTransactionFeePercentagePerUnit'), 'percent'),
            }
            for key, val in e3_details.items():
                if val and val.strip() != "—": parts.append(f"- **{key}:** {val}")

            parts.append(f"- **Item E.5 - Is the Fund an 'In-Kind Exchange-Traded Fund'?** {format_val(get_text(etf, 'isInKindETF'))}")

    attachments_node = form_data.find('attachmentsTab')
    parts.append("\n## N-CEN: Part G: Attachments")
    parts.append("**Item G.1a. Attachments.**")
    parts.append("Attachments applicable to all Registrants. All Registrants shall file the following attachments, as applicable, with the current report. Indicate the attachments filed with the current report by checking the applicable items below:")

    attachment_map = {
        'isLegalProceedings': "i. Legal proceedings",
        'isFinancialSupport': "ii. Provision of financial support",
        'isIPAReportInternalControl': "iii. Independent public accountant's report on internal control (management investment companies other than small business investment companies only)",
        'isChangeAccountPrinciple': "iv. Change in accounting principles and practices",
        'isExemptiveOrder': "v. Information required to be filed pursuant to exemptive orders",
        'isOther': "vi. Other information required to be included as an attachment pursuant to Commission rules and regulations"
    }

    for tag_name, text in attachment_map.items():
        is_checked = False
        if attachments_node:
            is_checked = get_text(attachments_node, tag_name).lower() in ['y', 'true']
        
        checkbox = '[x]' if is_checked else '[ ]'
        parts.append(f"- {checkbox} {text}")

    signature = form_data.find('signature')
    if signature:
        parts.append("\n## N-CEN: Signature")
        parts.append("Pursuant to the requirements of the Investment Company Act of 1940, the Registrant has duly caused this report to a be signed on its behalf by the undersigned hereunto duly authorized.")
        sig_details = {
            "Registrant": signature.get('registrantSignedName', '—'),
            "Date": signature.get('signedDate', '—'),
            "Signature": signature.get('signature', '—'),
            "Title": signature.get('title', '—'),
        }
        for key, val in sig_details.items():
            if val and val != "—":
                parts.append(f"**{key}:** {val}")

    return "\n\n".join(parts)

def parse_form_c_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form C into structured Markdown, accurately
    rendering all sections to mimic the visual layout of the original form.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^(?:\\w+:)?{tag}$', re.I))
        return html.unescape(found.text.strip()) if found and found.text else "—"

    def safe_format_dollar(value_str: str) -> str:
        if not value_str or value_str == "—": return "—"
        try:
            is_negative = value_str.startswith('(') and value_str.endswith(')')
            if is_negative:
                value_str = '-' + value_str.strip('()')
            
            val = float(value_str)
            return f"${val:,.2f}"
        except (ValueError, TypeError):
            return value_str

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM C\n\n"
        "### UNDER THE SECURITIES ACT OF 1933\n"
    ]

    issuer_info = xml.find('issuerInformation')
    offering_info = xml.find('offeringInformation')
    annual_report_info = xml.find('annualReportDisclosureRequirements')
    signature_info = xml.find('signatureInfo')

    if issuer_info:
        parts.append("### Issuer Information\n")
        
        is_amendment = get_text(issuer_info, 'isAmendment').lower() in ['true', 'y']
        if is_amendment:
            parts.append(f"**Is this an amendment?** Yes")
            nature_of_amendment = get_text(issuer_info, 'natureOfAmendment')
            if nature_of_amendment != "—":
                parts.append(f"**Nature of Amendment:** {nature_of_amendment}")
        
        address_node = issuer_info.find('issuerAddress')
        
        address_parts = [
            get_text(address_node, 'street1'),
            get_text(address_node, 'city'),
            get_text(address_node, 'stateOrCountry'),
            get_text(address_node, 'zipCode')
        ]
        physical_address_str = ", ".join(part for part in address_parts if part and part != "—")

        issuer_details = {
            "Name of Issuer": get_text(issuer_info.find('issuerInfo'), 'nameOfIssuer'),
            "Legal Status": get_text(issuer_info.find('legalStatus'), 'legalStatusForm'),
            "Jurisdiction of Incorporation/Organization": get_text(issuer_info.find('legalStatus'), 'jurisdictionOrganization'),
            "Date of Organization": get_text(issuer_info.find('legalStatus'), 'dateIncorporation'),
            "Physical Address": physical_address_str or "—",
            "Issuer Website": get_text(issuer_info.find('issuerInfo'), 'issuerWebsite'),
            "Is there a Co-Issuer?": "Yes" if get_text(issuer_info, 'isCoIssuer') == 'Y' else "No",
            "Intermediary Name": get_text(issuer_info, 'companyName'),
            "Intermediary CIK": get_text(issuer_info, 'commissionCik'),
            "Intermediary File Number": get_text(issuer_info, 'commissionFileNumber'),
            "Intermediary CRD Number": get_text(issuer_info, 'crdNumber'),
        }
        for key, val in issuer_details.items():
            if val and val.strip() != "—":
                parts.append(f"**{key}:** {val}")

    if offering_info:
        parts.append("\n### Offering Information\n")
        offering_details = {
            "Compensation to Intermediary": get_text(offering_info, 'compensationAmount'),
            "Financial Interest in Issuer": get_text(offering_info, 'financialInterest'),
            "Type of Security Offered": get_text(offering_info, 'securityOfferedType'),
            "Other Description of Security": get_text(offering_info, 'securityOfferedOtherDesc'),
            "Number of Securities Offered": get_text(offering_info, 'noOfSecurityOffered'),
            "Price per Security": safe_format_dollar(get_text(offering_info, 'price')),
            "Method for Determining Price": get_text(offering_info, 'priceDeterminationMethod'),
            "Target Offering Amount": safe_format_dollar(get_text(offering_info, 'offeringAmount')),
            "Oversubscription Accepted": "Yes" if get_text(offering_info, 'overSubscriptionAccepted') == 'Y' else "No",
            "Oversubscription Allocation Type": get_text(offering_info, 'overSubscriptionAllocationType'),
            "Description of Oversubscription": get_text(offering_info, 'descOverSubscription'),
            "Maximum Offering Amount": safe_format_dollar(get_text(offering_info, 'maximumOfferingAmount')),
            "Deadline to Reach Target Amount": get_text(offering_info, 'deadlineDate'),
        }
        for key, val in offering_details.items():
            if val and val.strip() != "—":
                parts.append(f"**{key}:** {val}")

    if annual_report_info:
        parts.append("\n### Annual Report Disclosure Requirements\n")
        financials = {
            "Current Number of Employees": get_text(annual_report_info, 'currentEmployees'),
            "Total Assets (Most Recent Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'totalAssetMostRecentFiscalYear')),
            "Total Assets (Prior Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'totalAssetPriorFiscalYear')),
            "Cash & Cash Equivalents (Most Recent Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'cashEquiMostRecentFiscalYear')),
            "Cash & Cash Equivalents (Prior Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'cashEquiPriorFiscalYear')),
            "Accounts Receivable (Most Recent Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'actReceivedMostRecentFiscalYear')),
            "Accounts Receivable (Prior Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'actReceivedPriorFiscalYear')),
            "Short-Term Debt (Most Recent Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'shortTermDebtMostRecentFiscalYear')),
            "Short-Term Debt (Prior Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'shortTermDebtPriorFiscalYear')),
            "Long-Term Debt (Most Recent Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'longTermDebtMostRecentFiscalYear')),
            "Long-Term Debt (Prior Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'longTermDebtPriorFiscalYear')),
            "Revenues/Sales (Most Recent Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'revenueMostRecentFiscalYear')),
            "Revenues/Sales (Prior Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'revenuePriorFiscalYear')),
            "Cost of Goods Sold (Most Recent Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'costGoodsSoldMostRecentFiscalYear')),
            "Cost of Goods Sold (Prior Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'costGoodsSoldPriorFiscalYear')),
            "Taxes Paid (Most Recent Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'taxPaidMostRecentFiscalYear')),
            "Taxes Paid (Prior Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'taxPaidPriorFiscalYear')),
            "Net Income (Most Recent Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'netIncomeMostRecentFiscalYear')),
            "Net Income (Prior Fiscal Year)": safe_format_dollar(get_text(annual_report_info, 'netIncomePriorFiscalYear')),
        }
        for key, val in financials.items():
            if val and val.strip() not in ["—", "$—"]:
                parts.append(f"**{key}:** {val}")

        jurisdiction_nodes = annual_report_info.find_all('issueJurisdictionSecuritiesOffering')
        if jurisdiction_nodes:
            jurisdiction_codes = [node.text for node in jurisdiction_nodes]
            jurisdiction_names = [SEC_COUNTRY_CODES.get(code, code) for code in jurisdiction_codes]
            parts.append("\n**Jurisdictions Offered:**")
            parts.append(", ".join(jurisdiction_names))


    if signature_info:
        parts.append("\n### Signatures\n")
        issuer_signature = signature_info.find('issuerSignature')
        if issuer_signature:
            parts.append(f"**Issuer:** {get_text(issuer_signature, 'issuer')}")
            parts.append(f"**Signature:** {get_text(issuer_signature, 'issuerSignature')}")
            parts.append(f"**Title:** {get_text(issuer_signature, 'issuerTitle')}")

        for person in signature_info.find_all('signaturePerson'):
            parts.append("\n---")
            parts.append(f"**Signature:** {get_text(person, 'personSignature')}")
            parts.append(f"**Title:** {get_text(person, 'personTitle')}")
            parts.append(f"**Date:** {get_text(person, 'signatureDate')}")

    return "\n\n".join(parts)

def parse_nport_p_xml(xml: BeautifulSoup, class_name_map: dict = None) -> str:
    """
    Parses an XML-based Form NPORT-P into a structured Markdown document,
    capturing detailed fund information, monthly returns, and comprehensive
    data for each portfolio security, including derivatives. This version is
    updated to capture all fields from Part A and other sections.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_val(value_str: str, type_hint: str = 'string') -> str:
        if not value_str or value_str.lower() in ('—', 'n/a', 'na'): return "—"
        
        if value_str.upper() == 'Y': return "Yes"
        if value_str.upper() == 'N': return "No"
        
        try:
            val_float = float(value_str.replace(',', ''))
            if type_hint == 'dollar': return f"${val_float:.2f}"
            if type_hint == 'percent': return f"{val_float:.2f}%"
            if type_hint == 'shares': return f"{val_float:.4f}"
            if type_hint == 'number': return f"{val_float:.0f}"
        except (ValueError, TypeError):
            pass
            
        return value_str

    if class_name_map is None:
        class_name_map = {}

    parts = ["## Form NPORT-P: Monthly Portfolio Investments Report"]

    gen_info = xml.find('genInfo')
    if gen_info:
        parts.append("\n### NPORT-P: Part A: General Information")
        
        parts.append("\n**Item A.1. Information about the Registrant.**")
        registrant_info = {
            "a. Name of Registrant": get_text(gen_info, 'regName'),
            "b. Investment Company Act file number": get_text(gen_info, 'regFileNumber'),
            "c. CIK number of Registrant": get_text(gen_info, 'regCik'),
            "d. LEI of Registrant": get_text(gen_info, 'regLei'),
        }
        for key, val in registrant_info.items():
            if val != "—": parts.append(f"- **{key}:** {val}")

        reg_addr_node = gen_info.find('regStateConditional')
        registrant_address = {
            "Street Address 1": get_text(gen_info, 'regStreet1'),
            "City": get_text(gen_info, 'regCity'),
            "State": reg_addr_node['regState'].replace('US-', '') if reg_addr_node and reg_addr_node.has_attr('regState') else '—',
            "Foreign country": reg_addr_node['regCountry'] if reg_addr_node and reg_addr_node.has_attr('regCountry') else '—',
            "Zip / Postal Code": get_text(gen_info, 'regZipOrPostalCode'),
            "Telephone number": get_text(gen_info, 'regPhone'),
        }
        parts.append("- **e. Address and telephone number of Registrant.**")
        for key, val in registrant_address.items():
             if val and val.strip() != "—": parts.append(f"  - **{key}:** {val}")

        parts.append("\n**Item A.2. Information about the Series.**")
        series_info = {
            "a. Name of Series": get_text(gen_info, 'seriesName'),
            "b. EDGAR series identifier (if any)": get_text(gen_info, 'seriesId'),
            "c. LEI of Series": get_text(gen_info, 'seriesLei'),
        }
        for key, val in series_info.items():
            if val != "—": parts.append(f"- **{key}:** {val}")

        parts.append("\n**Item A.3. Reporting period.**")
        reporting_info = {
            "a. Date of fiscal year-end": get_text(gen_info, 'repPdEnd'),
            "b. Date as of which information is reported": get_text(gen_info, 'repPdDate'),
        }
        for key, val in reporting_info.items():
            if val != "—": parts.append(f"- **{key}:** {val}")
        
        parts.append("\n**Item A.4. Final filing**")
        final_filing = format_val(get_text(gen_info, 'isFinalFiling'))
        parts.append(f"Does the Fund anticipate that this will be its final filing on Form N-PORT? **{final_filing}**")

    fund_info = xml.find('fundInfo')
    if fund_info:
        parts.append("\n### Fund Information")
        fund_data = {
            "Total Assets": format_val(get_text(fund_info, 'totAssets'), 'dollar'),
            "Total Liabilities": format_val(get_text(fund_info, 'totLiabs'), 'dollar'),
            "Net Assets": format_val(get_text(fund_info, 'netAssets'), 'dollar'),
            "Assets Attributable to Miscellaneous Securities": format_val(get_text(fund_info, 'assetsAttrMiscSec'), 'dollar'),
            "Amount of Assets Invested in Other Investment Companies": format_val(get_text(fund_info, 'assetsInvested'), 'dollar'),
            "Delayed Delivery Securities": format_val(get_text(fund_info, 'delayDeliv'), 'dollar'),
            "Stand-by Commitments": format_val(get_text(fund_info, 'standByCommit'), 'dollar'),
            "Cash Not Reported": format_val(get_text(fund_info, 'cshNotRptdInCorD'), 'dollar'),
        }
        for key, val in fund_data.items():
            if val and val != "—" and val != "$0.00":
                parts.append(f"**{key}:** {val}")
        
        if (cur_metric := fund_info.find('curMetric')):
            parts.append("\n**Currency Risk Metrics (dv01):**")
            risk_data = {
                "3-Month": cur_metric.get('period3Mon'), "1-Year": cur_metric.get('period1Yr'),
                "5-Year": cur_metric.get('period5Yr'), "10-Year": cur_metric.get('period10Yr'),
                "30-Year": cur_metric.get('period30Yr')
            }
            parts.append("- " + " | ".join([f"**{k}:** {v}" for k, v in risk_data.items() if v]))

        if (invst_grade := fund_info.find('creditSprdRiskInvstGrade')):
            parts.append("\n**Credit Spread Risk - Investment Grade (dv01):**")
            risk_data = {
                "3-Month": invst_grade.get('period3Mon'), "1-Year": invst_grade.get('period1Yr'),
                "5-Year": invst_grade.get('period5Yr'), "10-Year": invst_grade.get('period10Yr'),
                "30-Year": invst_grade.get('period30Yr')
            }
            parts.append("- " + " | ".join([f"**{k}:** {v}" for k, v in risk_data.items() if v]))

        if (non_invst_grade := fund_info.find('creditSprdRiskNonInvstGrade')):
            parts.append("\n**Credit Spread Risk - Non-Investment Grade (dv01):**")
            risk_data = {
                "3-Month": non_invst_grade.get('period3Mon'), "1-Year": non_invst_grade.get('period1Yr'),
                "5-Year": non_invst_grade.get('period5Yr'), "10-Year": non_invst_grade.get('period10Yr'),
                "30-Year": non_invst_grade.get('period30Yr')
            }
            parts.append("- " + " | ".join([f"**{k}:** {v}" for k, v in risk_data.items() if v]))
        
        return_info = fund_info.find('returnInfo')
        if return_info:
            parts.append("\n**Monthly Return Information**")
            returns_data = []
            for monthly_return in return_info.find_all('monthlyTotReturn'):
                class_id = monthly_return.get('classId', 'N/A')
                class_name = class_name_map.get(class_id, f"Class ID {class_id}")
                returns_data.append({
                    "Class": class_name,
                    "Month 1 Return (%)": format_val(monthly_return.get('rtn1'), 'percent'),
                    "Month 2 Return (%)": format_val(monthly_return.get('rtn2'), 'percent'),
                    "Month 3 Return (%)": format_val(monthly_return.get('rtn3'), 'percent')
                })
            if returns_data:
                parts.append(to_compact_markdown(pd.DataFrame(returns_data), index=False))

            other_return_data = []
            for i in range(1, 4):
                other_mon_node = return_info.find(f'othMon{i}')
                if other_mon_node:
                    other_return_data.append({ "Period": f"Month {i}", "Net Realized Gain/Loss": format_val(other_mon_node.get('netRealizedGain'), 'dollar'), "Net Unrealized Appreciation/Depreciation": format_val(other_mon_node.get('netUnrealizedAppr'), 'dollar') })
            if other_return_data:
                parts.append("\n**Monthly Gains & Losses**")
                parts.append(to_compact_markdown(pd.DataFrame(other_return_data), index=False))
        
        var_info = fund_info.find('varInfo')
        if var_info and (designated_info := var_info.find('fundsDesignatedInfo')):
            parts.append("\n**Designated Index Information**")
            parts.append(f"- **Index Name:** {get_text(designated_info, 'nameDesignatedIndex')}")
            parts.append(f"- **Index Identifier:** {get_text(designated_info, 'indexIdentifier')}")

    class_level_nodes = xml.find_all('classLevelInfo')
    if class_level_nodes:
        parts.append("\n### NPORT-P: Part B: Information About the Series")
        for node in class_level_nodes:
            class_id = get_text(node, 'classId')
            class_name = class_name_map.get(class_id, f"Class ID {class_id}")
            parts.append(f"\n#### Class: {class_name} ({class_id})")
            
            parts.append("\n**Item B.2. Assets and Liabilities**")
            class_assets = {
                "Total Assets": format_val(get_text(node, 'totAssets'), 'dollar'),
                "Total Liabilities": format_val(get_text(node, 'totLiabs'), 'dollar'),
                "Net Assets": format_val(get_text(node, 'netAssets'), 'dollar'),
            }
            for key, val in class_assets.items():
                if val and val != "—":
                    parts.append(f"- **{key}:** {val}")
            
            nav_per_share = format_val(get_text(node, 'netAssetValuePerShare'), 'shares')
            if nav_per_share != "—":
                parts.append(f"**Item B.3. Net asset value per share:** {nav_per_share}")

            counterparty_nodes = node.find_all('securityLendingCounterparty')
            if counterparty_nodes:
                parts.append("\n**Item B.4. Securities Lending Counterparties**")
                counterparty_data = []
                for cp_node in counterparty_nodes:
                    counterparty_data.append({
                        "Counterparty Name": get_text(cp_node, 'counterpartyName'),
                        "Value of Securities on Loan": format_val(get_text(cp_node, 'valLoaned'), 'dollar')
                    })
                if counterparty_data:
                    parts.append(to_compact_markdown(pd.DataFrame(counterparty_data), index=False))

            parts.append("\n**Item B.5. Monthly Shareholder Flow Activity**")
            flow_data = []
            for i in range(1, 4):
                flow_node = node.find(f'mon{i}Flow')
                if flow_node:
                    flow_data.append({
                        "Period": f"Month {i}",
                        "Sales": format_val(flow_node.get('sales'), 'dollar'),
                        "Reinvestments": format_val(flow_node.get('reinvestment'), 'dollar'),
                        "Redemptions": format_val(flow_node.get('redemption'), 'dollar'),
                    })
            if flow_data:
                parts.append(to_compact_markdown(pd.DataFrame(flow_data), index=False))

            liq_info = node.find('highlyLiquidInvst')
            if liq_info:
                parts.append("\n**Item B.6. Highly Liquid Investment Minimum**")
                parts.append(f"- **Did the Fund meet the 30% minimum for at least one business day?** {format_val(get_text(liq_info, 'isFundMeet30PctDay1LiqAsset'))}")
                parts.append(f"- **Did the Fund meet the 10% minimum for at least one business day?** {format_val(get_text(liq_info, 'isFundMeet10PctWklyLiqAsset'))}")
    
    investments = xml.find_all('invstOrSec')
    if investments:
        parts.append("\n### Schedule of Portfolio Investments")
        investment_data = []
        for item in investments:
            ids = []
            if (cusip := get_text(item, 'cusip')) != "—": ids.append(f"CUSIP: {cusip}")
            if (lei := get_text(item, 'lei')) != "—": ids.append(f"LEI: {lei}")
            
            id_node = item.find('identifiers')
            if id_node:
                if (isin := get_text(id_node, 'isin')) != "—": ids.append(f"ISIN: {isin}")
                if (ticker := get_text(id_node, 'ticker')) != "—": ids.append(f"Ticker: {ticker}")
            id_str = "<br>".join(ids) if ids else "—"

            lending_info = "—"
            if (lending_node := item.find('securityLending')):
                is_loaned = format_val(get_text(lending_node, 'isLoanByFund'))
                lending_info = f"On Loan: {is_loaned}"

            debt_sec = item.find('debtSec')
            maturity_dt, coupon_kind, annualized_rt = "—", "—", "—"
            if debt_sec:
                maturity_dt = get_text(debt_sec, 'maturityDt')
                coupon_kind = get_text(debt_sec, 'couponKind')
                annualized_rt = format_val(get_text(debt_sec, 'annualizedRt'), 'percent')

            record = {
                "Name": get_text(item, 'name'),
                "Title": get_text(item, 'title'),
                "Identifiers": id_str,
                "Payoff Profile": get_text(item, 'payoffProfile'),
                "Asset Category": get_text(item, 'assetCat'),
                "Issuer Category": get_text(item, 'issuerCat'),
                "Country": get_text(item, 'invCountry'),
                "Balance": format_val(get_text(item, 'balance'), 'number'),
                "Units": get_text(item, 'units'),
                "Value (USD)": format_val(get_text(item, 'valUSD'), 'dollar'),
                "% of Net Assets": format_val(get_text(item, 'pctVal'), 'percent'),
                "Maturity Date": maturity_dt,
                "Coupon Type": coupon_kind,
                "Annualized Rate (%)": annualized_rt,
                "Restricted?": format_val(get_text(item, 'isRestrictedSec')),
                "Fair Value Level": get_text(item, 'fairValLevel'),
                "Lending Status": lending_info,
            }
            investment_data.append(record)
        
        df = pd.DataFrame(investment_data)
        if not df.empty:
            column_order = [
                "Name", "Title", "Identifiers", "Payoff Profile", "Asset Category", "Issuer Category", "Country", 
                "Balance", "Units", "Value (USD)", "% of Net Assets", "Maturity Date", "Coupon Type",
                "Annualized Rate (%)", "Restricted?", "Fair Value Level", "Lending Status"
            ]
            df = df.reindex(columns=column_order, fill_value="—").fillna("—")
            parts.append(to_compact_markdown(df, index=False))
            
    signature_node = xml.find('signature')
    if signature_node:
        parts.append("\n### Signature")
        signature_data = {
            "Date Signed": get_text(signature_node, 'dateSigned'),
            "Name of Applicant": get_text(signature_node, 'nameOfApplicant'),
            "Signature": get_text(signature_node, 'signature'),
            "Name of Signer": get_text(signature_node, 'signerName'),
            "Title": get_text(signature_node, 'title'),
        }
        for key, val in signature_data.items():
            if val != "—":
                parts.append(f"**{key}:** {val}")

    return "\n\n".join(parts)

def parse_form1a_xml(xml: BeautifulSoup) -> str:
    """
    Parses the XML of a Form 1-A filing into a structured and comprehensive
    Markdown document, mirroring the sections of the official form.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^(?:\\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_dollar(value_str: str) -> str:
        if not value_str or value_str == "—": return "—"
        try:
            val = float(value_str)
            return f"${val:.2f}"
        except (ValueError, TypeError):
            return value_str
            
    def format_number(value_str: str) -> str:
        if not value_str or value_str == "—": return "—"
        try:
            return f"{int(float(value_str)):}"
        except (ValueError, TypeError):
            return value_str
    
    def format_bool(value_str: str, yes_char='Y', no_char='N') -> str:
        if not value_str or value_str == "—": return "—"
        s = value_str.strip().upper()
        if s == yes_char or s == 'TRUE':
            return "Yes"
        if s == no_char or s == 'FALSE':
            return "No"
        return "—"

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM 1-A\n\n"
        "### REGULATION A OFFERING STATEMENT\n"
        "### UNDER THE SECURITIES ACT OF 1933\n"
    ]

    header = xml.find('headerData')
    form_data = xml.find('formData')
    
    parts.append("### Item 1. Issuer Information")
    emp_info = form_data.find('employeesInfo')
    issuer_info = form_data.find('issuerInfo')
    
    issuer_details_md = [
        f"**Exact name of issuer:** {get_text(emp_info, 'issuerName')}",
        f"**Jurisdiction of Incorporation/Organization:** {get_text(emp_info, 'jurisdictionOrganization')}",
        f"**Year of Incorporation:** {get_text(emp_info, 'yearIncorporation')}",
        f"**CIK:** {get_text(emp_info, 'cik')}",
        f"**I.R.S. Employer Identification Number:** {get_text(emp_info, 'irsNum')}",
        f"**Primary Standard Industrial Classification Code:** {get_text(emp_info, 'sicCode')}",
        f"**Total number of full-time employees:** {format_number(get_text(emp_info, 'fullTimeEmployees'))}",
        f"**Total number of part-time employees:** {format_number(get_text(emp_info, 'partTimeEmployees'))}",
        f"**Address of Principal Executive Offices:** {get_text(issuer_info, 'street1')}, {get_text(issuer_info, 'street2')}, {get_text(issuer_info, 'city')}, {get_text(issuer_info, 'stateOrCountry')} {get_text(issuer_info, 'zipCode')}",
        f"**Company Phone:** {get_text(issuer_info, 'phoneNumber')}",
        f"**Person to contact:** {get_text(issuer_info, 'connectionName')}",
    ]
    parts.extend(issuer_details_md)

    parts.append("\n### Financial Statements")
    financial_data = {
        "Balance Sheet Information": {
            "Cash and Cash Equivalents": format_dollar(get_text(issuer_info, 'cashEquivalents')),
            "Investment Securities": format_dollar(get_text(issuer_info, 'investmentSecurities')),
            "Accounts and Notes Receivable": format_dollar(get_text(issuer_info, 'accountsReceivable')),
            "Property, Plant and Equipment (PP&E)": format_dollar(get_text(issuer_info, 'propertyPlantEquipment')),
            "Total Assets": format_dollar(get_text(issuer_info, 'totalAssets')),
            "Accounts Payable and Accrued Liabilities": format_dollar(get_text(issuer_info, 'accountsPayable')),
            "Long-Term Debt": format_dollar(get_text(issuer_info, 'longTermDebt')),
            "Total Liabilities": format_dollar(get_text(issuer_info, 'totalLiabilities')),
            "Total Stockholders' Equity": format_dollar(get_text(issuer_info, 'totalStockholderEquity')),
            "Total Liabilities and Equity": format_dollar(get_text(issuer_info, 'totalLiabilitiesAndEquity'))
        },
        "Statement of Comprehensive Income Information": {
            "Total Revenues": format_dollar(get_text(issuer_info, 'totalRevenues')),
            "Costs and Expenses Applicable to Revenues": format_dollar(get_text(issuer_info, 'costAndExpensesApplToRevenues')),
            "Depreciation and Amortization": format_dollar(get_text(issuer_info, 'depreciationAndAmortization')),
            "Net Income": format_dollar(get_text(issuer_info, 'netIncome')),
            "Earnings Per Share - Basic": get_text(issuer_info, 'earningsPerShareBasic'),
            "Earnings Per Share - Diluted": get_text(issuer_info, 'earningsPerShareDiluted')
        },
        "Auditor Information": {
            "Name of Auditor": get_text(issuer_info, 'nameAuditor')
        }
    }
    
    for section_title, data_dict in financial_data.items():
        parts.append(f"\n**{section_title}**\n")
        table_data = {"Metric": list(data_dict.keys()), "Amount": list(data_dict.values())}
        parts.append(to_compact_markdown(pd.DataFrame(table_data), index=False))

    parts.append("\n### Outstanding Securities")
    common = form_data.find('commonEquity')
    preferred = form_data.find('preferredEquity')
    debt = form_data.find('debtSecurities')
    securities_data = [
        {"Class": get_text(common, 'commonEquityClassName'), "Outstanding": format_number(get_text(common, 'outstandingCommonEquity')), "CUSIP": get_text(common, 'commonCusipEquity'), "Publicly Traded": get_text(common, 'publiclyTradedCommonEquity')},
        {"Class": get_text(preferred, 'preferredEquityClassName'), "Outstanding": format_number(get_text(preferred, 'outstandingPreferredEquity')), "CUSIP": get_text(preferred, 'preferredCusipEquity'), "Publicly Traded": get_text(preferred, 'publiclyTradedPreferredEquity')},
        {"Class": get_text(debt, 'debtSecuritiesClassName'), "Outstanding": format_number(get_text(debt, 'outstandingDebtSecurities')), "CUSIP": get_text(debt, 'cusipDebtSecurities'), "Publicly Traded": get_text(debt, 'publiclyTradedDebtSecurities')}
    ]
    securities_df = pd.DataFrame([s for s in securities_data if s['Class'] != 'None'])
    if not securities_df.empty:
        parts.append(to_compact_markdown(securities_df, index=False))

    parts.append(f"\n### Item 2. Issuer Eligibility\n- [x] The issuer certifies that all of the statements in this part are true.")
    parts.append(f"\n### Item 3. Application of Rule 262\n- [x] The issuer certifies that it is not disqualified and has not been involved in any disqualifying event.")

    summary_info = form_data.find('summaryInfo')
    parts.append("\n### Item 4. Summary Information Regarding the Offering")
    offering_flags_md = [
        f"**Tier:** {get_text(summary_info, 'indicateTier1Tier2Offering')}",
        f"**Financial Statement Status:** {get_text(summary_info, 'financialStatementAuditStatus')}",
        f"**Type of Securities Offered:** {get_text(summary_info, 'securitiesOfferedTypes')}",
        f"**Is this a delayed or continuous offering?** {format_bool(get_text(summary_info, 'offerDelayedContinuousFlag'))}",
        f"**Was or is the offering to take place within one year after qualification?** {format_bool(get_text(summary_info, 'offeringYearFlag'))}",
        f"**Was or is the offering to commence within two days after qualification?** {format_bool(get_text(summary_info, 'offeringAfterQualifFlag'))}",
        f"**Is this a best efforts offering?** {format_bool(get_text(summary_info, 'offeringBestEffortsFlag'))}",
        f"**Was there any solicitation of interest?** {format_bool(get_text(summary_info, 'solicitationProposedOfferingFlag'))}",
        f"**Are there any resale securities by affiliates of the issuer?** {format_bool(get_text(summary_info, 'resaleSecuritiesAffiliatesFlag'))}"
    ]
    parts.extend(offering_flags_md)

    offering_amounts_data = {
        "Description": ["Number of securities offered", "Number of securities outstanding", "Price per security", "Issuer's aggregate offering price", "Aggregate offering price of securities held by security holders", "Aggregate price of securities offered concurrently", "Total aggregate offering price"],
        "Amount": [
            format_number(get_text(summary_info, 'securitiesOffered')),
            format_number(get_text(summary_info, 'outstandingSecurities')),
            format_dollar(get_text(summary_info, 'pricePerSecurity')),
            format_dollar(get_text(summary_info, 'issuerAggregateOffering')),
            format_dollar(get_text(summary_info, 'securityHolderAggegate')),
            format_dollar(get_text(summary_info, 'qualificationOfferingAggregate')),
            format_dollar(get_text(summary_info, 'totalAggregateOffering'))
        ]
    }
    parts.append("\n**Offering Amounts**")
    parts.append(to_compact_markdown(pd.DataFrame(offering_amounts_data), index=False))

    fees_data = {
        "Service Provider": ["Auditor", "Legal", "Promoters"],
        "Name": [get_text(summary_info, 'auditorServiceProviderName'), get_text(summary_info, 'legalServiceProviderName'), get_text(summary_info, 'promotersServiceProviderName')],
        "Fees": [format_dollar(get_text(summary_info, 'auditorFees')), format_dollar(get_text(summary_info, 'legalFees')), format_dollar(get_text(summary_info, 'promotersFees'))]
    }
    parts.append("\n**Anticipated Fees**")
    parts.append(to_compact_markdown(pd.DataFrame(fees_data), index=False))
    parts.append(f"**Estimated Net Proceeds to the Issuer:** {format_dollar(get_text(summary_info, 'estimatedNetAmount'))}")

    jur_info = form_data.find('juridictionSecuritiesOffered')
    if jur_info:
        parts.append("\n### Item 5. Jurisdictions in Which Securities are to be Offered")
        is_none = get_text(jur_info, 'jurisdictionsOfSecOfferedNone').lower() == 'true'
        if is_none:
            parts.append("- All States and Territories")
        else:
            states = [j.text for j in jur_info.find_all('issueJuridicationSecuritiesOffering')]
            parts.append(", ".join(states))

    securities_issued = form_data.find('securitiesIssued')
    unregistered_act = form_data.find('unregisteredSecuritiesAct')
    if securities_issued:
        parts.append("\n### Item 6. Unregistered Securities Issued or Sold Within One Year")
        unreg_details = [
            f"**Name of Such Issuer:** {get_text(securities_issued, 'securitiesIssuerName')}",
            f"**Title of Securities Issued:** {get_text(securities_issued, 'securitiesIssuerTitle')}",
            f"**Total Amount of Securities Issued:** {format_number(get_text(securities_issued, 'securitiesIssuedTotalAmount'))}",
            f"**Amount of such securities sold by principal security holders:** {format_number(get_text(securities_issued, 'securitiesPrincipalHolderAmount'))}",
            f"**Aggregate consideration:** {get_text(securities_issued, 'securitiesIssuedAggregateAmount')}",
            f"**Basis for aggregate consideration:** {get_text(securities_issued, 'aggregateConsiderationBasis')}",
            f"**Securities Act Exemption:** {get_text(unregistered_act, 'securitiesActExcemption')}"
        ]
        parts.extend(unreg_details)

    return "\n\n".join(parts)

__all__ = [name for name in globals() if not name.startswith("__")]
