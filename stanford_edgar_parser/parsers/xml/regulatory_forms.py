from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.multimarkdown.multimarkdown import SEC_COUNTRY_CODES
from stanford_edgar_parser.parsers.html.table_cleaning import _collapse_newlines, df_to_markdown, md_table_2row_header
from stanford_edgar_parser.parsers.ocr.ocr_utils import parse_pdf_attachments
from stanford_edgar_parser.parsers.xml.fund_and_ownership import (
    parse_form1a_xml,
    parse_form3_xml,
    parse_form_c_xml,
    parse_form_d_xml,
    parse_form_n_cen_xml,
    parse_form_n_mfp2_xml,
    parse_nport_p_xml,
    parse_schedule13g_xml,
)
from stanford_edgar_parser.parsers.xml.ownership import parse_form4_xml
from stanford_edgar_parser.utils.bootstrap import (
    BeautifulSoup,
    datetime,
    html,
    pd,
    re,
    textwrap,
)

def to_compact_markdown(df: pd.DataFrame, **kwargs) -> str:
    from stanford_edgar_parser.parsers.html.preprocessing import to_compact_markdown as _impl

    return _impl(df, **kwargs)


def parse_abs_ee_xml(xml: BeautifulSoup) -> str:
    """
    Parses the XML of a Form ABS-EE data file (EX-102) into a structured Markdown table.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_dollar(value_str: str) -> str:
        if not value_str or value_str == "—": return "—"
        try:
            return f"${float(value_str):.2f}"
        except (ValueError, TypeError):
            return value_str

    parts = ["## Exhibit 102: Asset Data File"]
    assets = xml.find_all(re.compile(r'^assets$', re.I))
    if not assets:
        return "<!-- No <assets> tags found in ABS-EE XML -->"

    asset_records = []
    for asset in assets:
        prop = asset.find(re.compile(r'^property$', re.I))
        if get_text(asset, 'assetNumber') == "—":
            continue
        record = {
            "Asset Number": get_text(asset, 'assetNumber'),
            "Originator": get_text(asset, 'originatorName'),
            "Origination Date": get_text(asset, 'originationDate'),
            "Original Loan Amount": format_dollar(get_text(asset, 'originalLoanAmount')),
            "Maturity Date": get_text(asset, 'maturityDate'),
            "Interest Rate (%)": get_text(asset, 'originalInterestRatePercentage'),
            "Property Name": get_text(prop, 'propertyName') if prop else "—",
            "Property Type": get_text(prop, 'propertyTypeCode') if prop else "—",
            "City": get_text(prop, 'propertyCity') if prop else "—",
            "State": get_text(prop, 'propertyState') if prop else "—",
        }
        asset_records.append(record)

    if asset_records:
        df = pd.DataFrame(asset_records)
        parts.append(to_compact_markdown(df, index=False))

    return "\n\n".join(parts)

def parse_abs_ee_comments_xml(xml: BeautifulSoup) -> str:
    """
    Parses the XML of an ABS-EE Asset Related Document (EX-103) into Markdown.
    """
    from bs4 import Comment
    import textwrap

    parts = ["## Exhibit 103: Asset Related Document"]
    
    comments = xml.find_all(string=lambda text: isinstance(text, Comment))
    
    if not comments:
        return "<!-- No comments found in ABS-EE EX-103 XML -->"

    for comment in comments:
        comment_text = comment.strip()
        
        if any(phrase in comment_text for phrase in ["Exhibit 103", "Ford Credit Auto Lease Trust", "Asset Related Document", "This asset related document provides narrative"]):
            continue
        
        if comment_text in ["Explanatory Narrative", "General Narrative", "Item-specific Narrative"]:
            parts.append(f"### {comment_text}")
        else:
            item_match = re.match(r"Item\s+([0-9a-zA-Z\(\)]+)\s*\.\s*(.*)", comment_text, re.DOTALL)
            if item_match:
                item_num = item_match.group(1).strip()
                item_text = item_match.group(2).strip()
                parts.append(f"- **Item {item_num}:** {item_text}")
            else:
                parts.append(textwrap.fill(comment_text, width=100))

    return "\n\n".join(parts)

def parse_schedule13d_xml(xml: BeautifulSoup) -> str:
    """
    Parses a Schedule 13D or 13D/A filing into structured Markdown,
    creating a detailed cover page table for each reporting person that
    matches the visual layout of the original form.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    submission = xml.find('edgarSubmission')
    if not submission:
        return "<!-- <edgarSubmission> tag not found in SCHEDULE 13D/A filing -->"

    cover_page = submission.find('coverPageHeader')
    issuer_info = cover_page.find('issuerInfo')
    form_data = submission.find('formData')

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## SCHEDULE 13D\n\n"
        "### Under the Securities Exchange Act of 1934\n"
    ]

    if (amendment_no := get_text(cover_page, 'amendmentNo')) != "—":
        parts.append(f"**(Amendment No. {amendment_no})**\n")

    parts.append(f"**{get_text(issuer_info, 'issuerName')}**")
    parts.append(f"*(Name of Issuer)*\n")
    parts.append(f"**{get_text(cover_page, 'securitiesClassTitle')}**")
    parts.append(f"*(Title of Class of Securities)*\n")
    parts.append(f"**{get_text(issuer_info, 'issuerCusip')}**")
    parts.append(f"*(CUSIP Number)*\n")

    auth_person_container = cover_page.find('authorizedPersons')
    notification_info = auth_person_container.find('notificationInfo') if auth_person_container else None

    if notification_info:
        contact_block = [f"**{get_text(notification_info, 'personName')}**"]
        
        address_node = notification_info.find(lambda tag: tag.name.endswith('personAddress'))
        
        if address_node:
            address_lines = []
            if (street1 := get_text(address_node, 'street1')) != "—":
                address_lines.append(street1)
            if (street2 := get_text(address_node, 'street2')) != "—":
                address_lines.append(street2)
            
            city_state_zip = []
            if (city := get_text(address_node, 'city')) != "—": city_state_zip.append(city)
            if (state := get_text(address_node, 'stateOrCountry')) != "—": city_state_zip.append(state)
            if (zip_code := get_text(address_node, 'zipCode')) != "—": city_state_zip.append(zip_code)
            
            if city_state_zip:
                address_lines.append(" ".join(city_state_zip))

            contact_block.extend(address_lines)

        if (phone_num := get_text(notification_info, 'personPhoneNum')) != "—":
            contact_block.append(phone_num)
        
        parts.append("<br>".join(contact_block))
        parts.append(f"*(Name, Address and Telephone Number of Person Authorized to Receive Notices and Communications)*\n")

    parts.append(f"**{get_text(cover_page, 'dateOfEvent')}**")
    parts.append(f"*(Date of Event Which Requires Filing of this Statement)*\n")

    for i, person in enumerate(form_data.find_all('reportingPersonInfo'), 1):
        
        cusip = get_text(issuer_info, 'issuerCusip')
        name = get_text(person, 'reportingPersonName')
        is_group_b = get_text(person, 'memberOfGroup') == 'b'
        group_checkboxes = "[ ] (a)<br>[x] (b)" if is_group_b else "[x] (a)<br>[ ] (b)"
        source_of_funds = get_text(person, 'fundType')
        is_legal_proc = get_text(person, 'legalProceedings') == 'Y'
        legal_proc_checkbox = '[x]' if is_legal_proc else '[ ]'
        citizenship_code = get_text(person, 'citizenshipOrOrganization')
        citizenship = SEC_COUNTRY_CODES.get(citizenship_code, citizenship_code)
        sole_voting = f"{int(float(get_text(person, 'soleVotingPower'))):.2f}" if get_text(person, 'soleVotingPower') not in ["—", "0.00"] else "0.00"
        shared_voting = f"{int(float(get_text(person, 'sharedVotingPower'))):.2f}" if get_text(person, 'sharedVotingPower') not in ["—", "0.00"] else "0.00"
        sole_dispositive = f"{int(float(get_text(person, 'soleDispositivePower'))):.2f}" if get_text(person, 'soleDispositivePower') not in ["—", "0.00"] else "0.00"
        shared_dispositive = f"{int(float(get_text(person, 'sharedDispositivePower'))):.2f}" if get_text(person, 'sharedDispositivePower') not in ["—", "0.00"] else "0.00"
        aggregate_owned = f"{int(float(get_text(person, 'aggregateAmountOwned'))):.2f}" if get_text(person, 'aggregateAmountOwned') not in ["—", "0.00"] else "0.00"
        is_exclude_shares = get_text(person, 'isAggregateExcludeShares') == 'Y'
        exclude_shares_checkbox = '[x]' if is_exclude_shares else '[ ]'
        percent_of_class = f"{get_text(person, 'percentOfClass')}%"
        person_type = get_text(person, 'typeOfReportingPerson')
        comment = get_text(person, 'commentContent')
        
        shares_block_text = f"Number of Shares<br>Beneficially Owned by<br>Each Reporting Person With:##ROWSPAN_1##"
        
        table_content = {
            'name': f"Name of reporting person<br>**{name}**##COLSPAN_2##",
            'group': f"Check the appropriate box if a member of a Group (See Instructions)<br>{group_checkboxes}##COLSPAN_3##",
            'sec_use': "SEC use only##COLSPAN_4##",
            'source_funds': f"Source of funds (See Instructions)<br>**{source_of_funds}**##COLSPAN_5##",
            'legal': f"Check if disclosure of legal proceedings is required pursuant to Items 2(d) or 2(e)<br>{legal_proc_checkbox}##COLSPAN_6##",
            'citizenship': f"Citizenship or place of organization<br>**{citizenship}**##COLSPAN_7##",
            'agg_owned': f"Aggregate amount beneficially owned by each reporting person<br>**{aggregate_owned}**##COLSPAN_8##",
            'exclude_shares': f"Check if the aggregate amount in Row (11) excludes certain shares (See Instructions)<br>{exclude_shares_checkbox}##COLSPAN_9##",
            'percent_class': f"Percent of class represented by amount in Row (11)<br>**{percent_of_class}**##COLSPAN_10##",
            'person_type': f"Type of Reporting Person (See Instructions)<br>**{person_type}**##COLSPAN_11##"
        }

        cusip_header = f"| **CUSIP No.** | **{cusip}** |"
        
        table_md = [
            "| | | | |",
            "|:--|:--|:--|:--|",
            f"| 1 | {table_content['name']} | {table_content['name']} | |",
            f"| 2 | {table_content['group']} | {table_content['group']} | |",
            f"| 3 | {table_content['sec_use']} | {table_content['sec_use']} | |",
            f"| 4 | {table_content['source_funds']} | {table_content['source_funds']} | |",
            f"| 5 | {table_content['legal']} | {table_content['legal']} | |",
            f"| 6 | {table_content['citizenship']} | {table_content['citizenship']} | |",
            f"| {shares_block_text} | 7 | Sole Voting Power<br>**{sole_voting}** |",
            f"| {shares_block_text} | 8 | Shared Voting Power<br>**{shared_voting}** |",
            f"| {shares_block_text} | 9 | Sole Dispositive Power<br>**{sole_dispositive}** |",
            f"| {shares_block_text} | 10 | Shared Dispositive Power<br>**{shared_dispositive}** |",
            f"| 11 | {table_content['agg_owned']} | {table_content['agg_owned']} | |",
            f"| 12 | {table_content['exclude_shares']} | {table_content['exclude_shares']} | |",
            f"| 13 | {table_content['percent_class']} | {table_content['percent_class']} | |",
            f"| 14 | {table_content['person_type']} | {table_content['person_type']} | |",
        ]

        parts.append(f"\n{cusip_header}\n---\n" + "\n".join(table_md) + "\n---")
        if comment != "—":
            parts.append(f"\n**Comment for Reporting Person:** {comment}")
            
    items = form_data.find('items1To7')
    if items:
        item1 = items.find('item1')
        if item1:
            parts.append("\n**Item 1. Security and Issuer**")
            
            sec_title = get_text(item1, 'securityTitle')
            if sec_title != "—":
                parts.append(f"**(a) Title of Class of Securities:**\n{sec_title}")
            
            issuer_name = get_text(item1, 'issuerName')
            if issuer_name != "—":
                parts.append(f"**(b) Name of Issuer:**\n{issuer_name}")

            address_node = item1.find('issuerPrincipalAddress')
            if address_node:
                street1 = get_text(address_node, 'street1')
                street2 = get_text(address_node, 'street2')
                city = get_text(address_node, 'city')
                state = get_text(address_node, 'stateOrCountry')
                zip_code = get_text(address_node, 'zipCode')
                
                address_parts = [p for p in [street1, street2, city, state, zip_code] if p and p != "—"]
                if address_parts:
                    full_address = ", ".join(address_parts)
                    parts.append(f"**(c) Address of Issuer's Principal Executive Offices:**\n{full_address}")

            if (comment := get_text(item1, 'commentText')) and comment != "—":
                parts.append(f"\n{comment}")
        
        item4 = items.find('item4')
        if item4 and (purpose := get_text(item4, 'transactionPurpose')) and purpose != "—":
            parts.append(f"\n**Item 4. Purpose of Transaction**\n\n{purpose}")

        item5 = items.find('item5')
        if item5:
            parts.append("\n**Item 5. Interest in Securities of the Issuer**")

            if (text_a := get_text(item5, 'percentageOfClassSecurities')):
                parts.append(f"\n**(a)**\n{text_a}")

            if (text_b := get_text(item5, 'numberOfShares')):
                parts.append(f"\n**(b)**\n{text_b}")

            if (transaction_text := get_text(item5, 'transactionDesc')):
                parts.append(f"\n**(c)**\n{transaction_text}")

        item6 = items.find('item6')
        if item6 and (contracts := get_text(item6, 'contractDescription')) and contracts != "—":
            parts.append(f"\n**Item 6. Contracts, Arrangements, Understandings or Relationships With Respect to Securities of the Issuer.**\n\n{contracts}")

    parts.append("\n### SIGNATURE\n")
    sig_info = form_data.find('signatureInfo')
    if sig_info:
        for signature in sig_info.find_all('signaturePerson'):
            details = signature.find('signatureDetails')
            parts.append(f"After reasonable inquiry and to the best of my knowledge and belief, I certify that the information set forth in this statement is true, complete and correct.\n")
            parts.append(f"**Reporting Person:** {get_text(signature, 'signatureReportingPerson')}\n")
            parts.append(f"**Signature:** {get_text(details, 'signature')}")
            parts.append(f"**Name/Title:** {get_text(details, 'title')}")
            parts.append(f"**Date:** {get_text(details, 'date')}\n")

    return "\n\n".join(parts)

def parse_form1k_xml(xml: BeautifulSoup) -> str:
    """
    Parses the XML of a Form 1-K filing into a structured Markdown document.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_bool(value_str: str, yes_char='Y', no_char='N') -> str:
        if not value_str or value_str == "—": return "—"
        s = value_str.strip().upper()
        if s == yes_char or s == 'TRUE':
            return "Yes"
        if s == no_char or s == 'FALSE':
            return "No"
        return "—"

    parts = ["## Form 1-K Filing Summary"]

    header_data = xml.find('headerData')
    form_data = xml.find('formData')

    if not form_data or not header_data:
        return "<!-- <formData> or <headerData> tag not found in 1-K XML -->"

    filer_info_node = header_data.find('filerInfo')
    filer_creds_node = filer_info_node.find('filer').find('issuerCredentials') if filer_info_node else None
    flags_node = filer_info_node.find('flags') if filer_info_node else None

    parts.append("\n### Filer Information")
    header_details = {
        "Issuer CIK": get_text(filer_creds_node, 'cik'),
        "Issuer CCC": get_text(filer_creds_node, 'ccc'),
        "Is filer a shell company?": format_bool(get_text(flags_node, 'shellCompanyFlag'), no_char='N'),
        "Is this filing by a successor company?": format_bool(get_text(flags_node, 'successorFilingFlag'), no_char='N'),
    }
    for key, val in header_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    parts.append("\n### Submission Contact Information")
    submission_details = {
        "Is this a LIVE or TEST Filing?": get_text(filer_info_node, 'liveTestFlag'),
        "Period": get_text(header_data, 'reportingPeriod'),
    }
    for key, val in submission_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")
    
    item1 = form_data.find('item1')
    item1_info = form_data.find('item1Info')
    item2 = form_data.find('item2')

    parts.append("\n### Item 1: Issuer Information (Tab 1 Notification)")
    issuer_details = {
        "Type of Report": get_text(item1, 'formIndication'),
        "Fiscal Year End": get_text(item1, 'fiscalYearEnd'),
        "Exact Name of Issuer": get_text(item1_info, 'issuerName'),
        "CIK": get_text(item1_info, 'cik'),
        "Jurisdiction of Incorporation": get_text(item1_info, 'jurisdictionOrganization'),
        "IRS Number": get_text(item1_info, 'irsNum'),
        "Address": f"{get_text(item1, 'street1')}, {get_text(item1, 'city')}, {get_text(item1, 'stateOrCountry')} {get_text(item1, 'zipCode')}",
        "Issuer Phone Number": get_text(item1, 'phoneNumber'),
        "Title of each class of securities issued pursuant to Regulation A": get_text(item1, 'issuedSecuritiesTitle'),
    }
    
    for key, val in issuer_details.items():
        if val and "—" not in val:
            parts.append(f"**{key}:** {val}")

    parts.append("\n### Item 2: Ongoing Reporting Requirements")
    is_compliant = get_text(item2, 'regArule257').lower() == 'true'
    compliance_text = "Yes" if is_compliant else "No"
    parts.append(f"**Is the issuer relying on the relief provided by Rule 257(d) for this filing?** {compliance_text}")

    return "\n\n".join(parts)

def parse_form1z_xml(xml: BeautifulSoup) -> str:
    """
    Parses the XML of a Form 1-Z (Exit Report) into a structured Markdown document.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM 1-Z\n\n"
        "### EXIT REPORT UNDER REGULATION A\n"
    ]

    header_data = xml.find('headerData')
    form_data = xml.find('formData')

    if not form_data or not header_data:
        return "<!-- <formData> or <headerData> tag not found in 1-Z XML -->"

    filer_info_node = header_data.find('filerInfo')
    filer_creds_node = filer_info_node.find('filer').find('issuerCredentials') if filer_info_node else None
    
    parts.append("### Filer Information")
    header_details = {
        "Issuer CIK": get_text(filer_creds_node, 'cik'),
        "Issuer CCC": get_text(filer_creds_node, 'ccc'),
        "Is this a LIVE or TEST Filing?": get_text(filer_info_node, 'liveTestFlag'),
    }
    for key, val in header_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    item1_node = form_data.find('item1')
    parts.append("\n### Item 1: Issuer Information")
    item1_details = {
        "Name of Issuer": get_text(item1_node, 'issuerName'),
        "Address": f"{get_text(item1_node, 'street1')}, {get_text(item1_node, 'city')}, {get_text(item1_node, 'stateOrCountry')} {get_text(item1_node, 'zipCode')}",
        "Telephone Number": get_text(item1_node, 'phone'),
        "Commission File Number": get_text(item1_node, 'commissionFileNumber'),
    }
    for key, val in item1_details.items():
        if val and "—" not in val:
            parts.append(f"**{key}:** {val}")

    cert_node = form_data.find('certificationSuspension')
    parts.append("\n### Part II: Certification of Suspension of Duty to File Reports")
    cert_details = {
        "Title of each class of securities": get_text(cert_node, 'securitiesClassTitle'),
        "File Number for the Regulation A offering statement": get_text(cert_node, 'certificationFileNumber'),
        "Approximate number of holders of record as of the certification date": get_text(cert_node, 'approxRecordHolders'),
    }
    for key, val in cert_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    sig_node = form_data.find('signatureTab')
    parts.append("\n### Signature")
    parts.append("Pursuant to the requirements of Regulation A, the issuer has duly caused this report to be signed on its behalf by the undersigned, thereunto duly authorized.")
    
    sig_details = {
        "CIK": get_text(sig_node, 'cik'),
        "Issuer": get_text(sig_node, 'regulationIssuerName1'),
        "By (Signature)": get_text(sig_node, 'signatureBy'),
        "Title": get_text(sig_node, 'title'),
        "Date": get_text(sig_node, 'date'),
    }
    
    for key, val in sig_details.items():
        if val and val != "—":
            parts.append(f"\n**{key}:** {val}")
    
    return "\n\n".join(parts)

def parse_form_ta1_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form TA-1 or TA-1/A into a structured Markdown document,
    handling variations in the XML schema over time and capturing all conditional details.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'^(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_bool(value_str: str) -> str:
        s = value_str.strip().upper()
        if s == 'Y' or s == 'TRUE': return "Yes"
        if s == 'N' or s == 'FALSE': return "No"
        return "—"

    def format_address(addr_node) -> str:
        if not addr_node: return "—"
        parts = [
            get_text(addr_node, 'street1'),
            get_text(addr_node, 'street2'),
            get_text(addr_node, 'city'),
            get_text(addr_node, 'stateOrCountry'),
            get_text(addr_node, 'zipCode'),
        ]
        return ", ".join(p for p in parts if p and p != "—")

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM TA-1\n\n"
        "### UNIFORM FORM OF APPLICATION FOR REGISTRATION AS A TRANSFER AGENT\n"
    ]

    submission_root = xml.find('edgarSubmission')
    if not submission_root:
        return "<!-- <edgarSubmission> tag not found in TA-1 XML -->"

    search_context = submission_root.find('formData') or submission_root

    registrant = search_context.find('registrant')
    independent = search_context.find('independentRegistrant')
    disciplinary = search_context.find('disciplinaryHistory') or search_context
    signature = search_context.find('signatureData') or search_context.find('signature')

    if not registrant:
        return "<!-- <registrant> tag not found in TA-1 XML -->"

    parts.append("### Registrant Information")
    
    reg_details = {
        "Appropriate regulatory agency": get_text(search_context, 'regulatoryAgency'),
        "Full name of Registrant": get_text(registrant, 'entityName'),
        "FINS Number": get_text(registrant, 'finsNumber'),
        "Address of principal office where transfer agent activities are performed": format_address(registrant.find('principalOfficeAddress')),
    }
    for key, val in reg_details.items():
        if val and "—" not in val:
            parts.append(f"**{key}:** {val}")

    is_mailing_different = format_bool(get_text(registrant, 'differentMailingAddress'))
    parts.append(f"**Is mailing address different from principal office address?:** {is_mailing_different}")
    if is_mailing_different == 'Yes':
        mailing_address = format_address(registrant.find('mailingAddress'))
        if mailing_address != "—":
            parts.append(f"**Mailing Address:** {mailing_address}")
            
    parts.append(f"**Telephone Number:** {get_text(registrant, 'telephoneNumber')}")

    conducts_other_business = format_bool(get_text(registrant, 'conductBusinessInOtherLocations'))
    parts.append(f"**Does registrant conduct business in other locations?:** {conducts_other_business}")
    if conducts_other_business == 'Yes':
        other_locations = registrant.find_all('otherBusinessLocation')
        if other_locations:
            for i, loc_node in enumerate(other_locations, 1):
                parts.append(f"**Other Business Location Address {i}:** {format_address(loc_node)}")

    remaining_reg_details = {
        "Is registrant a self-transfer agent?": format_bool(get_text(registrant, 'selfTransferAgent')),
        "Does registrant engage a service company to perform any of its transfer agent functions?": format_bool(get_text(registrant, 'engagedServiceCompany')),
    }
    for key, val in remaining_reg_details.items():
        if val and "—" not in val:
            parts.append(f"**{key}:** {val}")
            
    is_engaged_as_service_co = format_bool(get_text(registrant, 'engagedAsServiceCompany'))
    parts.append(f"**Is registrant engaged as a service company by a named transfer agent?:** {is_engaged_as_service_co}")
    if is_engaged_as_service_co == 'Yes':
        service_co_details = registrant.find_all('asServiceCompany')
        for i, co in enumerate(service_co_details, 1):
            parts.append(f"\n**Service Company Arrangement {i}:**")
            parts.append(f"- **Name:** {get_text(co, 'entityName')}")
            parts.append(f"- **File Number:** {get_text(co, 'fileNumber')}")
            parts.append(f"- **Address:** {format_address(co.find('asServiceCompanyAddress'))}")


    parts.append("\n### Ownership and Control Information")
    
    registrant_type_node = independent or registrant
    other_control_node = independent or search_context

    ind_details = {
        "Registrant Type": get_text(registrant_type_node, 'registrantType'),
        "Description (if Other)": get_text(registrant_type_node, 'registrantTypeDescription'),
    }
    for key, val in ind_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    other_control_mgmnt_node = other_control_node.find('otherControlManagement')
    has_other_control = format_bool(get_text(other_control_mgmnt_node, 'otherEntity'))
    parts.append(f"**Does any other person control the management or policies of the applicant?:** {has_other_control}")
    if has_other_control == 'Yes' and other_control_mgmnt_node:
        details_node = other_control_mgmnt_node.find('otherControlManagementDetails')
        if details_node:
            parts.append(f"- **Controlling Entity Name:** {get_text(details_node, 'entityName')}")
            parts.append(f"- **Agreement Description:** {get_text(details_node, 'agreementDescription')}")

    parts.append(f"**Does any other person directly or indirectly finance the applicant?:** {format_bool(get_text(other_control_node.find('otherControlFinance'), 'otherEntity'))}")

    control_persons = (independent and independent.find_all('soleProprietorshipOtherData')) or \
                      search_context.find_all('corporationPartnershipData')

    if control_persons:
        parts.append("\n**Control Affiliates Information:**")
        owner_data = []
        for person in control_persons:
            owner_data.append({
                "Entity Name": get_text(person, 'entityName'),
                "Relationship Start Date": get_text(person, 'relationshipStartDate'),
                "Title or Status": get_text(person, 'titleOrStatus'),
                "Ownership Code": get_text(person, 'ownershipCode'),
                "Control Person": format_bool(get_text(person, 'controlPerson')),
                "Authority Description": get_text(person, 'authorityDescription'),
                "Relationship End Date": get_text(person, 'relationshipEndDate')
            })
        df = pd.DataFrame(owner_data)
        df.dropna(axis=1, how='all', inplace=True)
        df = df.loc[:, (df != '—').any(axis=0)]
        parts.append(to_compact_markdown(df, index=False))

    parts.append("\n### Disciplinary History")
    
    DISCIPLINARY_QUESTIONS = {
        'felonyOrMisdemeanor': "Convicted/plead guilty to any felony or investment-related misdemeanor?",
        'otherFelony': "Convicted/plead guilty to any other felony?",
        'enjoinedInvestmentRelatedActivity': "Enjoined in connection with any investment-related activity?",
        'violationOfInvestmentRelatedRegulation': "Found to have violated any investment-related statute or regulation?",
        'falseStatementOrOmission': "Made a false statement or omission in a filing with the SEC?",
        'violationOfRegulations': "Found to have violated SRO rules or failed to supervise?",
        'authorizationDeniedOrSuspended': "Had authorization to act as a financial professional denied, suspended, or revoked?",
        'registrationDeniedOrSuspended': "Had a registration as a financial professional denied, suspended, or revoked?",
        'fsrFalseStatementOrOmission': "Federal/State agency found a false statement or omission?",
        'fsrViolationOfInvestmentRelatedRegulation': "Federal/State agency found a violation of investment-related regulations?",
        'fsrAuthorizationDeniedOrSuspended': "Federal/State agency denied, suspended, or revoked authorization?",
        'fsrFoundOrderAgainstApplicant': "Federal/State agency entered an order against the applicant?",
        'fsrRegistrationDeniedOrSuspended': "Federal/State agency denied, suspended, or revoked registration?",
        'fsrRevokedSuspendedLicense': "Federal/State agency revoked or suspended a license?",
        'sraFalseStatementOrOmission': "SRO found a false statement or omission?",
        'sraViolationOfRules': "SRO found a violation of its rules?",
        'sraAuthorizationDeniedOrSuspended': "SRO denied, suspended, or revoked authorization?",
        'sraRevokedSuspendedLicense': "SRO revoked or suspended a license?",
        'foreignAgency': "Subject of an order or finding by a foreign financial regulatory authority?",
        'subjectOfProceedings': "Currently the subject of any proceeding that could result in a 'yes' answer to any of the above?",
        'revokedBond': "Had a bond revoked for disorderly conduct, fraud, or dishonesty?",
        'unsatisfiedJudgementsOrLiens': "Have any unsatisfied judgments or liens against them?"
    }
    
    if disciplinary:
        for tag, question in DISCIPLINARY_QUESTIONS.items():
            question_node = disciplinary.find(lambda t: t.name.lower() == tag.lower())
            if question_node:
                answer = format_bool(get_text(question_node, 'involved'))
                parts.append(f"\n- **{question}:** {answer}")

                if answer == 'Yes':
                    details_tag_name = tag + "Details"
                    details_nodes = question_node.find_all(lambda t: t.name.lower() == details_tag_name.lower())
                    for i, detail_node in enumerate(details_nodes, 1):
                        parts.append(f"  - **Details #{i}:**")
                        detail_data = {
                            "Entity Name": get_text(detail_node, 'entityName'),
                            "Action Title": get_text(detail_node, 'actionTitle'),
                            "Action Date": get_text(detail_node, 'actionDate'),
                            "Court/Body Name and Location": get_text(detail_node, 'courtOrBodyNameAndLocation'),
                            "Action Description": get_text(detail_node, 'actionDescription'),
                            "Disposition": get_text(detail_node, 'dispositionOfProceeding')
                        }
                        for key, val in detail_data.items():
                            if val and val != "—":
                                parts.append(f"    - **{key}:** {val}")

    if signature:
        parts.append("\n### Signature")
        sig_details = {
            "Signature": get_text(signature, 'signatureName'),
            "Title": get_text(signature, 'signatureTitle'),
            "Date": get_text(signature, 'signatureDate'),
            "Phone Number": get_text(signature, 'signaturePhoneNumber'),
        }
        for key, val in sig_details.items():
            if val and val != "—":
                parts.append(f"**{key}:** {val}")

    return "\n\n".join(parts)

def parse_form_mai_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form MA-I or MA-I/A into a structured Markdown document,
    capturing all applicant, employment, and disciplinary history details.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'^(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_bool(value_str: str) -> str:
        s = value_str.strip().upper()
        if s == 'Y' or s == 'TRUE': return "Yes"
        if s == 'N' or s == 'FALSE': return "No"
        return "—"
    
    def format_name(name_node) -> str:
        if not name_node: return "—"
        first = get_text(name_node, 'firstName')
        middle = get_text(name_node, 'middleName')
        last = get_text(name_node, 'lastName')
        return " ".join(p for p in [first, middle, last] if p and p != "—")

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM MA-I\n\n"
        "### APPLICATION FOR MUNICIPAL ADVISOR REGISTRATION OF A NATURAL PERSON\n"
    ]

    header = xml.find('headerData')
    form_data = xml.find('formData')
    if not form_data or not header:
        return "<!-- <formData> or <headerData> not found in MA-I XML -->"

    filer_info = header.find('filerInfo')
    contact = filer_info.find('contact') if filer_info else None
    
    filer_node = header.find(re.compile(r'^(?:\w+:)?filer$', re.I))

    parts.append("### Filer and Contact Information")
    header_details = {
        "Filer CIK": get_text(filer_node, 'filerId'),
        "Contact Name": get_text(contact, 'name'),
        "Contact Phone": get_text(contact, 'phoneNumber'),
        "Contact Email": get_text(filer_info, 'contactEmail'),
        "Notification Emails": ", ".join(n.text for n in header.find_all('internetNotificationAddress')),
    }
    for key, val in header_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    parts.append("\n### Applicant Information")
    applicant_details = {
        "Is this an amendment?": format_bool(get_text(form_data, 'isAmendment')),
        "Is applicant a natural person?": format_bool(get_text(form_data, 'isIndividual')),
        "Full Name of Applicant": format_name(form_data.find('applicantName')),
        "Applicant CRD Number": get_text(form_data, 'applicantCrdNum'),
        "Associated with more than one advisory firm?": format_bool(get_text(form_data, 'hasMoreThanOneAdvisoryFirms')),
        "Number of advisory firms": get_text(form_data, 'noOfAdvisoryFirms'),
    }
    for key, val in applicant_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")
    
    offices = form_data.find_all('municipalAdvisorOffice')
    if offices:
        parts.append("\n### Municipal Advisor Firm Information")
        for i, office in enumerate(offices, 1):
            firm = office.find('municipalFirm')
            parts.append(f"\n**Firm #{i}**")
            firm_details = {
                "Firm Name": get_text(firm, 'municipalFirmName'),
                "Firm CIK": get_text(firm.find('municipalFiler'), 'filerId'),
                "Employment Start Date": get_text(firm, 'recentEmploymentCommencedDate'),
                "Independent Contractor Relationship?": format_bool(get_text(firm, 'isIndependentRelatioship')),
            }
            for key, val in firm_details.items():
                if val and val != "—":
                    parts.append(f"- **{key}:** {val}")
            
            reg_info = office.find('maRegistration')
            if reg_info:
                parts.append("\n  **Firm's Registration Information:**")
                reg_details = {
                    "Form MA Filing Date": get_text(reg_info.find('hasFiled'), 'filingDate'),
                    "EDGAR CIK No.": get_text(reg_info.find('hasFiled'), 'cik'),
                }
                for key, val in reg_details.items():
                    if val and val != "—":
                        parts.append(f"  - **{key}:** {val}")

            advisor_offices = office.find_all('advisorOffice')
            if advisor_offices:
                parts.append("\n  **Office Location Information:**")
                for j, adv_office in enumerate(advisor_offices, 1):
                    location_types = ", ".join(loc.text for loc in adv_office.find_all('locationInfo'))
                    parts.append(f"\n  - **Office #{j} ({location_types})**")
                    
                    addr_info_node = adv_office.find('addressInfo')
                    addr = addr_info_node.find('address') if addr_info_node else None

                    office_details = {
                        "Start Date": get_text(adv_office, 'startDate'),
                        "Address": f"{get_text(addr, 'street1')}, {get_text(addr, 'city')}, {get_text(addr, 'stateOrCountry')} {get_text(addr, 'zipCode')}"
                    }
                    for key, val in office_details.items():
                        if val and val.replace(",", "").replace(" ", "") != "—":
                            parts.append(f"    - **{key}:** {val}")

    other_names = form_data.find_all('otherName')
    if other_names:
        parts.append("\n### Other Names Used")
        for name in other_names:
            parts.append(f"- {format_name(name)}")

    emp_history = form_data.find('employmentHistory')
    if emp_history:
        parts.append("\n### Employment History")
        current = emp_history.find('currentEmployer')
        parts.append("\n**Current Employer**")
        
        current_addr_node = current.find('addressInfo') if current else None
        addr_parts = [
            get_text(current_addr_node, 'city'),
            get_text(current_addr_node, 'stateOrCountry'),
            get_text(current_addr_node, 'zipCode')
        ]
        current_address = ", ".join(p for p in addr_parts if p and p != "—")

        current_emp_details = {
            "Start Date": get_text(current, 'startDate'),
            "Employer Name": get_text(current, 'name'),
            "Address": current_address,
            "Position": get_text(current, 'positionDescription'),
            "Related to Municipal Advisor business?": format_bool(get_text(current, 'isRelatedToMunicipalAdvisor')),
            "Investment Related?": format_bool(get_text(current, 'isRelatedToInvestment')),
        }
        for key, val in current_emp_details.items():
            if val and val != "—":
                parts.append(f"- **{key}:** {val}")

        priors = emp_history.find_all('priorEmployer')
        if priors:
            parts.append("\n**Prior Employers**")
            for prior in priors:
                parts.append(f"\n- **{get_text(prior, 'name')}** ({get_text(prior, 'startDate')} - {get_text(prior, 'endDate')})")
                
                prior_addr_node = prior.find('addressInfo')
                prior_addr_parts = [
                    get_text(prior_addr_node, 'city'),
                    get_text(prior_addr_node, 'stateOrCountry'),
                    get_text(prior_addr_node, 'zipCode')
                ]
                prior_address = ", ".join(p for p in prior_addr_parts if p and p != "—")
                if prior_address:
                    parts.append(f"  - **Address:** {prior_address}")
                
                parts.append(f"  - **Position:** {get_text(prior, 'positionDescription')}")

    parts.append(f"\n**Engaged in other business?** {format_bool(get_text(form_data, 'isEngagedInOtherBusiness'))}")
    other_businesses = form_data.find_all('otherBusiness')
    if other_businesses:
        parts.append("\n### Other Business Information")
        for i, business in enumerate(other_businesses, 1):
            addr_node = business.find('addressInfo')
            address_str = ", ".join(p for p in [get_text(addr_node, 'street1'), get_text(addr_node, 'city'), get_text(addr_node, 'stateOrCountry'), get_text(addr_node, 'zipCode')] if p and p != "—")
            parts.append(f"\n**Business #{i}**")
            business_details = {
                "Start Date": get_text(business, 'startDate'),
                "Name": get_text(business, 'name'),
                "Address": address_str,
                "Related to Municipal Advisor business?": format_bool(get_text(business, 'isRelatedToMunicipalAdvisor')),
                "Investment Related?": format_bool(get_text(business, 'isRelatedToInvestment')),
                "Nature of Business": get_text(business, 'natureOfBusiness'),
                "Position": get_text(business, 'positionDescription'),
                "Approx. Hours/Month": get_text(business, 'approximateHoursOrMonths'),
                "Duties": get_text(business, 'dutiesDescription'),
            }
            for key, val in business_details.items():
                if val and val != "—":
                    parts.append(f"- **{key}:** {val}")

    questions_node = form_data.find('disclosureQuestions')
    if questions_node:
        parts.append("\n### Disclosure Questions")

        DISCLOSURE_QUESTION_MAP = {
            "Item 6A: Criminal Disclosure": {
                "criminalDisclosure": {
                    "isConvictedOfFelony": "(1)(a) Has the individual ever been convicted of any felony, or pled guilty or nolo contendere to any charge of a felony in a domestic, foreign, or military court?",
                    "isChargedWithFelony": "(1)(b) Has the individual ever been charged with any felony?",
                    "isOrgConvictedOfFelony": "(2)(a) Based upon activities that occurred while the individual exercised control over it, has an organization ever been convicted of any felony or pled guilty or nolo contendere in a domestic or foreign court to any charge of a felony?",
                    "isOrgChargedWithFelony": "(2)(b) Based upon activities that occurred while the individual exercised control over it, has an organization ever been charged with any felony?"
                }
            },
            "Item 6B: Criminal Disclosure (Misdemeanor)": {
                "criminalDisclosure": {
                    "isConvictedOfMisdemeanor": "(1)(a) Has the individual ever been convicted of any misdemeanor or pled guilty or nolo contendere to any charge of a misdemeanor involving: municipal advisory activities or a municipal advisor-related or investment-related business or any fraud, false statements or omissions, wrongful taking of property, bribery, perjury, forgery, counterfeiting, extortion, or a conspiracy to commit any of these offenses?",
                    "isChargedWithMisdemeanor": "(1)(b) Has the individual ever been charged with any misdemeanor of the kind described in 6B(1)(a)?",
                    "isOrgConvictedOfMisdemeanor": "(2)(a) Based upon activities that occurred while the individual exercised control over it, has an organization ever been convicted of any misdemeanor or pled guilty or nolo contendere to any charge of a misdemeanor of the kind specified in 6B(1)(a)?",
                    "isOrgChargedWithMisdemeanor": "(2)(b) Based upon activities that occurred while the individual exercised control over it, has an organization ever been charged with any misdemeanor of the kind specified in 6B(1)(a)?"
                }
            },
            "Item 6C: Regulatory Action Disclosure (SEC or CFTC)": {
                "regulatoryDisclosure": {
                    "isMadeFalseStatement": "(1) Has the SEC or the CFTC ever found the individual to have made a false statement or omission?",
                    "isViolatedRegulation": "(2) Has the SEC or the CFTC ever found the individual to have been involved in a violation of any SEC or CFTC regulation or statute?",
                    "isViolatedSecurityAct": "(3) Has the SEC or the CFTC ever found the individual to have been a cause of a denial, suspension, revocation, or restriction of the authorization of a municipal advisor-related business or investment-related business to operate?",
                    "isOrderAgainst": "(4) Has the SEC or the CFTC ever entered an order against the individual in connection with municipal advisor-related or investment-related activity?",
                    "isImposedPenalty": "(5) Has the SEC or the CFTC ever imposed a civil money penalty on the individual, or ordered the individual to cease and desist from any activity?",
                    "isWillFullyAided": "(6) Has the SEC or the CFTC ever found the individual to have willfully violated any provision of the specified Acts, or any rule or regulation under any of such Acts, or any of the rules of the MSRB, or found the individual to have been unable to comply with any provision of such Acts, rules or regulations?",
                    "isFailedToSupervise": "(7) Has the SEC or the CFTC ever found the individual to have willfully aided, abetted, counseled, commanded, induced, or procured the violation by any person of any provision of the specified Acts, or any rule or regulation under any of such Acts, or any of the rules of the MSRB?",
                    "isFailedResonably": "(8) Has the SEC or the CFTC ever found the individual to have failed reasonably to supervise another person subject to his or her supervision, with a view to preventing the violation of any provision of the specified Acts, or any rule or regulation under any of such Acts, or any of the rules of the MSRB?"
                }
            },
            "Item 6G: Investigation Disclosure": {
                "investigationDisclosure": {
                    "isInvestigated": "(1) Has the individual been notified, in writing, that he or she is currently the subject of any regulatory complaint or proceeding that could result in a 'Yes' answer to any part of 6C, D, or E?"
                }
            },
            "Item 6H: Civil Judicial Action Disclosure": {
                "civilDisclosure": {
                    "isEnjoined": "(1)(a) Has any domestic or foreign court ever enjoined the individual in connection with any municipal advisor-related or investment-related activity?",
                    "isFoundInViolationOfRegulation": "(1)(b) Has any domestic or foreign court ever found that the individual was involved in a violation of any municipal advisor-related or investment-related statute(s) or regulation(s)?",
                    "isDismissed": "(1)(c) Has any domestic or foreign court ever dismissed, pursuant to a settlement agreement, a municipal advisor-related or investment-related civil action brought against the individual by a domestic jurisdiction or foreign financial regulatory authority?",
                    "isNamedInCivilProceeding": "(2) Is the individual named in any currently pending civil proceeding that could result in a 'Yes' answer to any part of 6H(1)?"
                }
            },
            "Item 6I: Customer Complaint/Arbitration/Civil Litigation Disclosure": {
                "complaintDisclosure": {
                    "isComplaintSettled": "(1)(a) Has the individual ever been the subject of a municipal advisor-related or investment-related, customer-initiated written or oral complaint that alleged that he or she was involved in fraud, false statements, omissions, theft, embezzlement, wrongful taking of property, bribery, forgery, counterfeiting, extortion, or dishonest, unfair or unethical practices, which was settled?",
                    "isComplaintPending": "(1)(b) Has the individual ever been the subject of a municipal advisor-related or investment-related, customer-initiated written or oral complaint that alleged that he or she was involved in fraud, false statements, omissions, theft, embezzlement, wrongful taking of property, bribery, forgery, counterfeiting, extortion, or dishonest, unfair or unethical practices, which is still pending?",
                    "isFraudCasePending": "(2)(a) Has the individual ever been the subject of a municipal advisor-related or investment-related, customer-initiated arbitration or civil litigation that alleged that he or she was involved in fraud, false statements, omissions, theft, embezzlement, wrongful taking of property, bribery, forgery, counterfeiting, extortion, or dishonest, unfair or unethical practices, which is still pending?",
                    "isFraudCaseResultedAward": "(2)(b) Has the individual ever been the subject of a municipal advisor-related or investment-related, customer-initiated arbitration or civil litigation that alleged that he or she was involved in fraud, false statements, omissions, theft, embezzlement, wrongful taking of property, bribery, forgery, counterfeiting, extortion, or dishonest, unfair or unethical practices, which resulted in an arbitration award or civil judgment against the individual, regardless of amount?",
                    "isFraudCaseSettled": "(2)(c) Has the individual ever been the subject of a municipal advisor-related or investment-related, customer-initiated arbitration or civil litigation that alleged that he or she was involved in fraud, false statements, omissions, theft, embezzlement, wrongful taking of property, bribery, forgery, counterfeiting, extortion, or dishonest, unfair or unethical practices, which was settled?"
                }
            },
            "Item 6J: Termination Disclosure": {
                "terminationDisclosure": {
                    "isViloatedIndustryStandard": "(1) Has the individual ever voluntarily resigned, been discharged or permitted to resign after allegations were made that accused him or her of violating municipal advisor-related or investment-related statutes, regulations, rules, or industry standards of conduct?",
                    "isInvolvedInFraud": "(2) Has the individual ever voluntarily resigned, been discharged or permitted to resign after allegations were made that accused him or her of fraud or the wrongful taking of property?",
                    "isFailedToSupervise": "(3) Has the individual ever voluntarily resigned, been discharged or permitted to resign after allegations were made that accused him or her of failure to supervise in connection with municipal advisor-related or investment-related statutes, regulations, rules or industry standards of conduct?"
                }
            },
            "Item 6K: Financial Disclosure": {
                "financialDisclosure": {
                    "isCompromised": "(1) Within the past 10 years, has the individual made a compromise with creditors, filed a bankruptcy petition or been the subject of an involuntary bankruptcy petition?",
                    "isBankruptcyPetition": "(2) Based upon events that occurred while the individual exercised control over it, has an organization made a compromise with creditors, filed a bankruptcy petition or been the subject of an involuntary bankruptcy petition?",
                    "isTrusteeApointed": "(3) Based upon events that occurred while the individual exercised control over it, has a broker or dealer been the subject of an involuntary bankruptcy petition, had a trustee appointed, or had a direct payment procedure initiated under the Securities Investor Protection Act?",
                    "isBondRevoked": "(4) Has a bonding company ever denied, paid out on, or revoked a bond for the individual?"
                }
            },
            "Item 6M: Judgment/Lien Disclosure": {
                "judgmentLienDisclosure": {
                    "isLienAgainst": "Are there currently any unsatisfied judgments or liens against the individual?"
                }
            }
        }
        
        for item_title, sections in DISCLOSURE_QUESTION_MAP.items():
            parts.append(f"\n**{item_title}**")
            for section_tag, questions in sections.items():
                section_node = questions_node.find(section_tag)
                if section_node:
                    for question_tag, question_text in questions.items():
                        answer = format_bool(get_text(section_node, question_tag))
                        parts.append(f"- {question_text} **{answer}**")

    sig_info = form_data.find('signatureInfo')
    if sig_info:
        parts.append("\n### Signature")
        sig = sig_info.find('signature')
        sig_details = {
            "Date Signed": get_text(sig, 'dateSigned'),
            "Signature": get_text(sig, 'signature'),
            "Title": get_text(sig, 'title'),
        }
        for key, val in sig_details.items():
            if val and val != "—":
                parts.append(f"**{key}:** {val}")

    return "\n\n".join(parts)
    
def parse_form_x17a5_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form X-17A-5 (FOCUS Report) into a structured Markdown document.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_bool(value_str: str) -> str:
        s = value_str.strip().upper()
        if s == 'Y' or s == 'TRUE': return "Yes"
        if s == 'N' or s == 'FALSE': return "No"
        return "—"
        
    def format_address(addr_node) -> str:
        if not addr_node: return "—"
        parts = [
            get_text(addr_node, 'street1'),
            get_text(addr_node, 'street2'),
            get_text(addr_node, 'city'),
            get_text(addr_node, 'stateOrCountry'),
            get_text(addr_node, 'zipCode'),
        ]
        return ", ".join(p for p in parts if p and p != "—")

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM X-17A-5\n\n"
        "### ANNUAL AUDITED REPORT\n"
    ]

    header_data = xml.find('headerData')
    form_data = xml.find('formData')
    if not form_data or not header_data:
        return "<!-- <formData> or <headerData> tag not found in X-17A-5 XML -->"
    
    filer_info = header_data.find('filerInfo')
    filer_creds = filer_info.find('filerCredentials') if filer_info else None
    flags = filer_info.find('flags') if filer_info else None
    
    parts.append("### Filer Information")
    filer_details = {
        "Filer CIK": get_text(filer_creds, 'filerCik'),
        "Filer CCC": get_text(filer_creds, 'filerCcc'),
        "Is this a LIVE or TEST filing?": get_text(filer_info, 'liveTestFlag'),
        "Would you like a Return Copy?": format_bool(get_text(flags, 'returnCopyFlag')),
    }
    for key, val in filer_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    submission_info = form_data.find('submissionInformation')
    parts.append("\n### Submission Information")
    sub_details = {
        "Report Period Begin Date": get_text(submission_info, 'periodBegin'),
        "Report Period End Date": get_text(submission_info, 'periodEnd'),
        "Type of Registrant": get_text(submission_info, 'typeOfRegistrant'),
        "Any material weaknesses identified?": format_bool(get_text(submission_info, 'materialWeakness')),
        "Amendment Description": get_text(submission_info, 'amendmentDescription'),
    }
    for key, val in sub_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    registrant_info = form_data.find('registrantIdentification')
    parts.append("\n### Registrant Identification")
    reg_details = {
        "Name of Broker-Dealer": get_text(registrant_info, 'brokerDealerName'),
        "Business Address": format_address(registrant_info.find('businessAddress')),
        "Contact Person": get_text(registrant_info, 'contactPersonName'),
        "Contact Phone": get_text(registrant_info, 'contactPersonPhoneNumber'),
    }
    for key, val in reg_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    accountant_info = form_data.find('accountantIdentification')
    parts.append("\n### Independent Public Accountant Identification")
    acc_details = {
        "Accountant Name": get_text(accountant_info, 'accountantName'),
        "Accountant Address": format_address(accountant_info.find('accountantAddress')),
        "Accountant Type": get_text(accountant_info, 'accountantType'),
    }
    for key, val in acc_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    oath_info = form_data.find('oathSignature')
    parts.append("\n### OATH OR AFFIRMATION")
    parts.append(f"I, **{get_text(oath_info, 'signPersonName')}**, swear (or affirm) that, to the best of my knowledge and belief, the accompanying financial statements and supporting schedules pertaining to the firm of **{get_text(oath_info, 'entityName')}**, as of **{get_text(oath_info, 'signDate')}**, are true and correct.")
    
    oath_details = {
        "Signature": get_text(oath_info, 'signature'),
        "Title": get_text(oath_info, 'oathTitle'),
        "Notarized": format_bool(get_text(oath_info, 'confirmNotarizedFlag')),
    }
    for key, val in oath_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    return "\n\n".join(parts)

def parse_form_cfportal_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form CFPORTAL or CFPORTAL/A into a structured Markdown document.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'^(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_bool(value_str: str) -> str:
        s = value_str.strip().upper()
        if s == 'Y' or s == 'TRUE': return "Yes"
        if s == 'N' or s == 'FALSE': return "No"
        return "—"

    def format_address(addr_node) -> str:
        if not addr_node: return "—"
        parts = [
            get_text(addr_node, 'street1'),
            get_text(addr_node, 'street2'),
            get_text(addr_node, 'city'),
            get_text(addr_node, 'stateOrCountry'),
            get_text(addr_node, 'zipCode'),
        ]
        return ", ".join(p for p in parts if p and p != "—")

    def format_name(name_node) -> str:
        if not name_node: return "—"
        first = get_text(name_node, 'firstName')
        middle = get_text(name_node, 'middleName')
        last = get_text(name_node, 'lastName')
        return " ".join(p for p in [first, middle, last] if p and p != "—")
        
    OWNERSHIP_CODES = {
        'NA': "NA - less than 5%", 'A': "A - 5% but less than 10%",
        'B': "B - 10% but less than 25%", 'C': "C - 25% but less than 50%",
        'D': "D - 50% but less than 75%", 'E': "E - 75% or more",
        'G': "G - Other (general partner, trustee, or elected member)"
    }

    ENTITY_TYPE_CODES = {
        'DE': "DE (Domestic Entity)",
        'FE': "FE (Foreign Entity)",
        'NP': "NP (Natural Person)"
    }

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM CFPORTAL\n\n"
        "### FUNDING PORTAL REGISTRATION AND REPORTING\n"
    ]
    
    header = xml.find('headerData')
    form_data = xml.find('formData')
    if not form_data or not header:
        return "<!-- <formData> or <headerData> not found in CFPORTAL XML -->"

    filer_info = header.find('filerInfo')
    filer_creds = filer_info.find('filerCredentials') if filer_info else None
    contact_info = filer_info.find('contact') if filer_info else None
    
    parts.append("### Filer Information")
    filer_details = {
        "Filer CIK": get_text(filer_creds, 'filerCik'),
        "Filer CCC": get_text(filer_creds, 'filerCcc'),
        "Is this a LIVE or TEST Filing?": get_text(filer_info, 'liveTestFlag'),
    }
    for key, val in filer_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    if contact_info:
        parts.append("\n### Submission Contact Information")
        contact_details = {
            "Name": get_text(contact_info, 'name'),
            "Phone Number": get_text(contact_info, 'phoneNumber'),
            "E-mail Address": get_text(filer_info, 'contactEmail'),
        }
        for key, val in contact_details.items():
            if val and val != "—":
                parts.append(f"**{key}:** {val}")

    ident_info = form_data.find('identifyingInformation')
    parts.append("\n### Identifying Information")
    
    ident_details = {
        "Full Name of Funding Portal": get_text(ident_info, 'nameOfPortal'),
        "Amendment Explanation": get_text(ident_info, 'amendmentExplanation'),
        "Other Business Name(s)": get_text(ident_info.find('otherNamesAndWebsiteUrls'), 'otherNamesUsedPortal'),
        "Website URL(s)": get_text(ident_info.find('otherNamesAndWebsiteUrls'), 'webSiteOfPortal'),
        "Previous Website URL(s)": get_text(ident_info.find('prevNamesAndWebsiteUrls'), 'prevWebSiteUrls'),
        "IRS Employer ID No.": get_text(ident_info, 'irsEmployerIdNumber'),
        "Portal's Main Street Address": format_address(ident_info.find('portalAddress')),
    }
    for key, val in ident_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")

    mailing_address_is_different = get_text(ident_info, 'mailingAddressDifferent').lower() == 'true'
    if not mailing_address_is_different:
        parts.append("**Mailing Address:** Same as main address")
    else:
        parts.append(f"**Mailing Address:** {format_address(ident_info.find('portalMailingAddress'))}")

    ident_details_part2 = {
        "Contact Telephone": get_text(ident_info.find('portalContact'), 'portalContactPhone'),
        "Contact E-mail": get_text(ident_info.find('portalContact'), 'portalContactEmail'),
        "Contact Employee": f"{format_name(ident_info.find('contactEmployeeName'))}, {get_text(ident_info, 'contactEmployeeTitle')}",
        "Fiscal Year End": get_text(ident_info, 'fiscalYearEnd'),
        "Previously registered with Commission?": format_bool(get_text(ident_info, 'anyPreviousRegistrations')),
        "Registered with a foreign financial authority?": format_bool(get_text(ident_info, 'anyForeignRegistrations')),
    }
    for key, val in ident_details_part2.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")


    org_info = form_data.find('formOfOrganization')
    parts.append("\n### Form of Organization")
    org_details = {
        "Legal Status": get_text(org_info, 'legalStatusForm'),
        "State/Country of Formation": get_text(org_info, 'jurisdictionOrganization'),
        "Date of Formation": get_text(org_info, 'dateIncorporation'),
    }
    for key, val in org_details.items():
        parts.append(f"**{key}:** {val}")
    
    succ_info = form_data.find('successions')
    if succ_info:
        parts.append("\n### Successions")
        parts.append(f"**Is the applicant succeeding to the business of a currently registered funding portal?** {format_bool(get_text(succ_info, 'isSucceedingBusiness'))}")
        if get_text(succ_info, 'isSucceedingBusiness').upper() == 'Y':
            acq_info = succ_info.find('acquiredHistoryDetails')
            acq_details = {
                "Name of Acquired Funding Portal": get_text(acq_info, 'acquiredFundingPortal'),
                "Acquired Portal's SEC File No.": get_text(acq_info, 'acquiredPortalFileNumber'),
                "Brief description of the details of the succession": get_text(acq_info, 'acquiredDesc'),
            }
            for key, val in acq_details.items():
                if val and val != "—":
                    parts.append(f"**{key}:** {val}")

    ctrl_info = form_data.find('controlRelationships')
    if ctrl_info and ctrl_info.find_all('fullLegalNames'):
        parts.append("\n### Control Relationships")
        parts.append("Persons/entities that directly or indirectly control the applicant:")
        for name_node in ctrl_info.find_all('fullLegalNames'):
            parts.append(f"- {get_text(name_node, 'fullLegalName')}")

    disc_info = form_data.find('disclosureAnswers')
    if disc_info:
        parts.append("\n### Disclosure Information")
        DISCLOSURE_MAP = {
            'criminalDisclosure': "Criminal Disclosure", 'regulatoryActionDisclosure': "Regulatory Action Disclosure",
            'civilJudicialActionDisclosure': "Civil Judicial Disclosure", 'financialDisclosure': "Financial Disclosure"
        }
        for tag, title in DISCLOSURE_MAP.items():
            section = disc_info.find(tag)
            if section and any(node.text.upper() == 'Y' for node in section.find_all()):
                parts.append(f"**{title}:** Yes")
            else:
                parts.append(f"**{title}:** No")

    parts.append(f"\n**Does the applicant engage in any non-securities related business?** {format_bool(get_text(form_data.find('nonSecuritiesRelatedBusiness'), 'isEngagedInNonSecurities'))}")
    escrow_info = form_data.find('escrowArrangements')
    if escrow_info:
        parts.append("\n### Qualified Third Party Arrangements")
        third_party = escrow_info.find('investorFundsContacts')
        if third_party:
            parts.append(f"**Name of person:** {get_text(third_party, 'investorFundsContactName')}")
            parts.append(f"**Address:** {format_address(third_party.find('investorFundsAddress'))}")
            parts.append(f"**Phone Number:** {get_text(third_party, 'investorFundsContactPhone')}")
        parts.append(f"**Compensation Description:** {get_text(escrow_info, 'compensationDesc')}")

    exec_info = form_data.find('execution')
    parts.append("\n### Execution")
    exec_details = {
        "Date": get_text(exec_info, 'executionDate'),
        "Full Legal Name of Funding Portal": get_text(exec_info, 'fullLegalNameFundingPortal'),
        "By (Signature)": get_text(exec_info, 'personSignature'),
        "Title": get_text(exec_info, 'personTitle'),
    }
    for key, val in exec_details.items():
        parts.append(f"**{key}:** {val}")

    sched_a = form_data.find('scheduleA')
    if sched_a and sched_a.find_all('entityOrNaturalPerson'):
        parts.append("\n### FORM FUNDING PORTAL SCHEDULE A: Direct Owners and Executive Officers")
        sched_a_data = []
        for person in sched_a.find_all('entityOrNaturalPerson'):
            ownership_code = get_text(person, 'ownershipCode')
            ownership_desc = OWNERSHIP_CODES.get(ownership_code, ownership_code)
            entity_code = get_text(person, 'entityType')
            entity_desc = ENTITY_TYPE_CODES.get(entity_code, entity_code)
            sched_a_data.append({
                "Full Legal Name": get_text(person, 'fullLegalName'),
                "Entity Type": entity_desc,
                "Title or Status": get_text(person, 'titleStatus'),
                "Date Acquired": get_text(person, 'dateOfTitleStatusAcquired'),
                "Ownership Code": ownership_desc,
                "Control Person?": format_bool(get_text(person, 'controlPerson')),
                "CRD No.": get_text(person, 'crdNumber'),
                "IRS Tax No.": get_text(person, 'irsTaxNumber'),
                "IRS Employer ID No.": get_text(person, 'irsEmployerIdNumber'),
            })
        parts.append(to_compact_markdown(pd.DataFrame(sched_a_data), index=False))

    sched_b = form_data.find('scheduleB')
    if sched_b and sched_b.find_all('amendEntityOrNaturalPerson'):
        parts.append("\n### FORM FUNDING PORTAL SCHEDULE B: Amendments to Schedule A")
        sched_b_data = []
        for person in sched_b.find_all('amendEntityOrNaturalPerson'):
            ownership_code = get_text(person, 'ownershipCode')
            ownership_desc = OWNERSHIP_CODES.get(ownership_code, ownership_code)
            entity_code = get_text(person, 'entityType')
            entity_desc = ENTITY_TYPE_CODES.get(entity_code, entity_code)
            sched_b_data.append({
                "Full Legal Name": get_text(person, 'fullLegalName'),
                "Type of Amendment": get_text(person, 'typeOfAmendment'),
                "Entity Type": entity_desc,
                "Title or Status": get_text(person, 'titleStatus'),
                "Date Acquired": get_text(person, 'dateOfTitleStatusAcquired'),
                "Ownership Code": ownership_desc,
                "Control Person?": format_bool(get_text(person, 'controlPerson')),
                "CRD No.": get_text(person, 'crdNumber'),
                "IRS Tax No.": get_text(person, 'irsTaxNumber'),
                "IRS Employer ID No.": get_text(person, 'irsEmployerIdNumber'),
            })
        parts.append(to_compact_markdown(pd.DataFrame(sched_b_data), index=False))

    sched_c = form_data.find('scheduleC')
    if sched_c:
        parts.append("\n### FORM FUNDING PORTAL SCHEDULE C: Non-resident Funding Portals")
        agent = sched_c.find('agentForService')
        if agent:
            parts.append("\n**A. Agent for Service of Process:**")
            agent_details = {
                "Name of U.S. person designated as agent": get_text(agent, 'agentName'),
                "Address of U.S. person designated as agent": format_address(agent.find('agentAddress')),
            }
            for key, val in agent_details.items():
                if val and "—" not in val:
                    parts.append(f"- **{key}:** {val}")
        
        sig = sched_c.find('executionForNonResident')
        if sig:
            parts.append("\n**Execution for Non-Resident Funding Portals:**")
            sig_details = {
                "Signature": get_text(sig, 'signature'),
                "Printed Name": get_text(sig, 'printedName'),
                "Title": get_text(sig, 'title'),
                "Date": get_text(sig, 'date'),
            }
            for key, val in sig_details.items():
                if val and val != "—":
                    parts.append(f"**{key}:** {val}")

    return "\n\n".join(parts)

def parse_form_ta2_xml(xml: BeautifulSoup) -> str:
    """
    Parses a comprehensive XML-based Form TA-2 into a structured Markdown document,
    mirroring all sections of the official form. Defaults missing compliance
    checkboxes to NA.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_number(value_str: str) -> str:
        if not value_str or value_str == "—": return "—"
        try:
            return f"{int(float(value_str)):}"
        except (ValueError, TypeError):
            return value_str

    def format_dollar(value_str: str) -> str:
        if not value_str or value_str == "—": return "—"
        try:
            return f"${float(value_str):.2f}"
        except (ValueError, TypeError):
            return value_str
            
    def format_checkbox(value_str: str) -> str:
        val = value_str.strip().upper()
        if val in ('Y', 'YES'): 
            return "[X] Yes [ ] No [ ] NA"
        if val in ('N', 'NO'): 
            return "[ ] Yes [X] No [ ] NA"
        if val == 'NA': 
            return "[ ] Yes [ ] No [X] NA"
        return "[ ] Yes [ ] No [X] NA"

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM TA-2\n\n"
        "### FORM FOR REPORTING ACTIVITIES OF TRANSFER AGENTS\n"
    ]

    submission = xml.find('edgarSubmission')
    if not submission:
        return "<!-- <edgarSubmission> tag not found in TA-2 XML -->"

    header_data = submission.find('headerData')
    filer_node = submission.find('filer')
    registrant_node = submission.find('registrant')
    sc_data = submission.find('serviceCompanyData')
    sig_data = submission.find('signatureData')

    parts.append("### Registrant and Reporting Period Information")
    parts.append(f"**CIK:** {get_text(filer_node, 'cik')}")
    parts.append(f"**SEC File Number:** {get_text(filer_node, 'fileNumber')}")
    parts.append(f"**For the reporting period ended:** {get_text(submission, 'periodOfReport')}")

    parts.append("\n### Item 1. Full Name of Registrant")
    parts.append(get_text(header_data, 'entityName'))

    parts.append("\n### Item 2. Service Company Activities")
    
    engaged_service_co_node = submission.find('engagedServiceCompany')
    function_val = "NONE"
    if engaged_service_co_node:
        function_val = get_text(engaged_service_co_node, 'serviceCompany').upper()

    all_box = '[X]' if function_val == 'ALL' else '[ ]'
    some_box = '[X]' if function_val == 'SOME' else '[ ]'
    none_box = '[X]' if function_val == 'NONE' else '[ ]'

    parts.append(f"**a. During the reporting period, has the Registrant engaged a service company to perform any of its transfer agent functions?**")
    parts.append(f"{all_box} All")
    parts.append(f"{some_box} Some")
    parts.append(f"{none_box} None")

    if function_val in ('ALL', 'SOME') and engaged_service_co_node:
        transfer_agents = engaged_service_co_node.find_all('serviceCompanyTransferAgent')
        if transfer_agents:
            agent_details = []
            for agent in transfer_agents:
                agent_details.append(f"- **Name of Service Company:** {get_text(agent, 'entityName')}")
                agent_details.append(f"  **File Number:** {get_text(agent, 'fileNumber')}")
            if agent_details:
                parts.append("\n" + "\n".join(agent_details))

    engaged_as_service_co_node = submission.find('engagedAsServiceCompany')
    is_engaged_as_sc = get_text(engaged_as_service_co_node, 'registrantEngagedService').lower() == 'y'
    parts.append(f"**c. Is the Registrant engaged as a service company by a named transfer agent?** {'[X] Yes [ ] No' if is_engaged_as_sc else '[ ] Yes [X] No'}")

    parts.append("\n### Item 3. Regulatory and Amendment Information")
    parts.append(f"**a. Registrant's appropriate regulatory agency (ARA):** {get_text(submission, 'regulatoryAgency')}")
    
    amend_info = submission.find('registrantRegulatoryAgency')
    amend_filed_val = get_text(amend_info, 'amendmentFiled')
    amend_status = "Yes" if amend_filed_val == 'Y' else "No" if amend_filed_val == 'N' else "Not Applicable"
    parts.append(f"**b. During the reporting period, has the Registrant amended Form TA-1?** {amend_status}")

    if sc_data:
        parts.append("\n### Item 4. Transfer Agent Activities During the Reporting Period:")
        parts.append(f"**a. Number of items received for transfer:** {format_number(get_text(sc_data, 'numberItemsReceivedForTransfer'))}")
        parts.append(f"**b. Number of individual securityholder accounts for which the TA maintained master securityholder files:** {format_number(get_text(sc_data, 'numberMasterSecurityHolderFilings'))}")

        parts.append("\n### Item 5. Aggregate number of individual securityholder accounts, including accounts in the Direct Registration System (DRS), dividend reinvestment plans and/or direct purchase plans as of December 31:")
        parts.append(f"**a. Total number of individual securityholder accounts:** {format_number(get_text(sc_data, 'numberIndividualAccounts'))}")
        parts.append(f"**b. Number of individual securityholder dividend reinvestment plan and/or direct purchase plan accounts:** {format_number(get_text(sc_data, 'numberDivReinvDirPurPlanAccounts'))}")
        parts.append(f"**c. Number of individual securityholder DRS accounts:** {format_number(get_text(sc_data, 'numberDirectRegistSystemAccounts'))}")

        sh_accounts = sc_data.find('securityHolderAccounts')
        if sh_accounts:
            parts.append("\n**d. Approximate percentage of accounts by security type:**")
            account_data = {'Corporate Securities - Equity': f"{get_text(sh_accounts, 'equitySecurity')}%", 'Corporate Securities - Debt': f"{get_text(sh_accounts, 'debtSecurity')}%", 'Open-End Investment Company Securities': f"{get_text(sh_accounts, 'openEndInvestmentCompany')}%", 'Limited Partnership Securities': f"{get_text(sh_accounts, 'limitedPartnership')}%", 'Municipal Debt Securities': f"{get_text(sh_accounts, 'municipalDebt')}%", 'Other Securities': f"{get_text(sh_accounts, 'other')}%" }
            account_df = pd.DataFrame([account_data])
            parts.append(to_compact_markdown(account_df, index=False))

        parts.append("\n### Item 6. Number of securities issues for which Registrant acted:")
        sh_data = sc_data.find('securityHolderData')
        if sh_data:
            role_map = {
                'transMaintainMasterSecHolder': "6(a). Receives items for transfer and maintains the master securityholder files:",
                'transNotMaintMasterSecHolder': "6(b). Receives items for transfer but does not maintain the master securityholder files:",
                'notTransMaintMasterSecHolder': "6(c). Does not receive items for transfer but maintains the master securityholder files:"
            }

            issues_rows = []
            for tag_name, label in role_map.items():
                node = sh_data.find(tag_name)
                if node:
                    row_data = {
                        'label': label,
                        'equity': format_number(get_text(node, 'equitySecurity')),
                        'debt': format_number(get_text(node, 'debtSecurity')),
                        'openEnd': format_number(get_text(node, 'openEndInvestmentCompany')),
                        'limitedPartner': format_number(get_text(node, 'limitedPartnership')),
                        'municipal': format_number(get_text(node, 'municipalDebt')),
                        'other': format_number(get_text(node, 'other'))
                    }
                    issues_rows.append(row_data)

            if issues_rows:
                issues_df = pd.DataFrame(issues_rows)
                issues_df.columns = [
                    '',
                    'Corporate Securities##COLSPAN_1##<br>Equity',
                    'Corporate Securities##COLSPAN_1##<br>Debt',
                    'Open-End Investment Company Securities',
                    'Limited Partnership Securities',
                    'Municipal Debt Securities',
                    'Other Securities'
                ]

                parts.append("\n---\n")
                parts.append(md_table_2row_header(issues_df))
                parts.append("\n---\n")
        else:
            parts.append("Security holder data not reported in this filing.")

        parts.append("\n### Item 7. Scope of certain additional types of activities performed:")
        parts.append(f"**a. Number of issues for which dividend reinvestment plan and/or direct purchase plan services were provided:** {format_number(get_text(sh_data, 'dividendReinvDirectPurchasePlan'))}")
        parts.append(f"**b. Number of issues for which DRS services were provided:** {format_number(get_text(sh_data, 'directRegistrationSystem'))}")
        dividend_node = sh_data.find('dividendAndInterest') if sh_data else None
        parts.append(f"**c(i). Dividend disbursement and interest paying agent activities - Number of issues:** {format_number(get_text(dividend_node, 'numberIssues'))}")
        parts.append(f"**c(ii). Dividend disbursement and interest paying agent activities - Amount (in dollars):** {format_dollar(get_text(dividend_node, 'amountIssues'))}")

        parts.append("\n### Item 8. Aged record differences, existing for more than 30 days:")
        prior_agent_node = sh_data.find('priorAgent') if sh_data else None
        current_agent_node = sh_data.find('currentAgent') if sh_data else None
        
        aged_data = {
            'Prior Transfer Agent(s) (If applicable)': [
                format_number(get_text(prior_agent_node, 'numberIssues')),
                format_dollar(get_text(prior_agent_node, 'amountIssues'))
            ],
            'Current Transfer Agent': [
                format_number(get_text(current_agent_node, 'numberIssues')),
                format_dollar(get_text(current_agent_node, 'amountIssues'))
            ]
        }
        aged_df = pd.DataFrame(aged_data, index=['8(a)(i). Number of issues:', '8(a)(ii). Market value (in dollars):'])
        parts.append(to_compact_markdown(aged_df))
        
        num_filed = get_text(sh_data, 'numberFiled')
        parts.append(f"**8(b). Number of quarterly reports regarding buy-ins filed:** {format_number(num_filed)}")
        
        buy_in_compliance_val = "Y" if num_filed == "0" else "N"
        parts.append(f"**8(c). During the reporting period, did the Registrant file all quarterly reports regarding buy-ins?** {format_checkbox(buy_in_compliance_val)}")

        parts.append("\n### Item 9. Turnaround time for routine items:")
        turnaround_compliance_val = get_text(sh_data, 'alwaysCompliant')
        parts.append(f"**a. Has the Registrant always been in compliance with the turnaround time for routine items?** {format_checkbox(turnaround_compliance_val)}")
        
        if turnaround_compliance_val.upper() == 'N':
            parts.append(f"**Number of months not in compliance:** {format_number(get_text(sh_data, 'monthsNotInCompliance'))}")

        parts.append("\n### Item 10. Open-end investment company securities activities:")
        parts.append(f"**a. Total number of transactions processed:** {format_number(get_text(sh_data, 'total'))}")
        parts.append(f"**b. Number of transactions processed on a date other than date of receipt of order (as ofs):** {format_number(get_text(sh_data, 'totalOtherThanReceiptOrderDate'))}")

        db_search = sc_data.find('databaseSearches')
        if db_search:
            parts.append("\n### Item 11. Lost Securityholder Searches")
            parts.append(f"**a. Date of database search:** {get_text(db_search, 'databaseSearchDate')}")
            parts.append(f"**b. Number of lost securityholder accounts submitted for database search:** {format_number(get_text(db_search, 'numberLostAccountsSearched'))}")
            parts.append(f"**c. Number of addresses obtained from database search:** {format_number(get_text(db_search, 'numberAddressesFromSearch'))}")

        parts.append("\n### Item 12. Accounts Remitted to States")
        parts.append(f"**Number of securityholder accounts that have been remitted to states:** {format_number(get_text(sc_data, 'numberLostAccountsRemittedToStates'))}")

    if sig_data:
        parts.append("\n### SIGNATURE")
        sig_details = {
            "Signature of Official responsible for Form": get_text(sig_data, 'signatureName'),
            "Title of Signing Officer": get_text(sig_data, 'signatureTitle'),
            "Telephone Number": get_text(sig_data, 'signaturePhoneNumber'),
            "Date Signed (Month/Day/Year)": get_text(sig_data, 'signatureDate')
        }
        sig_df = pd.DataFrame(sig_details.items(), columns=['', ''])
        parts.append(to_compact_markdown(sig_df, index=False))

    return "\n\n".join(parts)

def parse_form_taw_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form TA-W (Notice of Withdrawal) into a structured Markdown document.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_address(addr_node) -> str:
        if not addr_node: return "—"
        parts = [
            get_text(addr_node, 'street1'),
            get_text(addr_node, 'street2'),
            get_text(addr_node, 'city'),
            get_text(addr_node, 'stateOrCountry'),
            get_text(addr_node, 'zipCode'),
        ]
        return ", ".join(p for p in parts if p and p != "—")

    def format_bool(value_str: str) -> str:
        s = value_str.strip().lower()
        if s == 'y' or s == 'true': return "Yes"
        if s == 'n' or s == 'false': return "No"
        return "—"

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM TA-W\n\n"
        "### NOTICE OF WITHDRAWAL FROM REGISTRATION AS TRANSFER AGENT\n"
    ]

    submission = xml.find('edgarSubmission')
    if not submission:
        return "<!-- <edgarSubmission> tag not found in TA-W XML -->"

    filer_node = submission.find('filer')
    registrant_node = submission.find('registrant')
    taw_details_node = submission.find('tawDetails')
    sig_data = submission.find('signatureData')

    parts.append("### Registrant Information")
    parts.append(f"**SEC File Number:** {get_text(filer_node, 'fileNumber')}")
    parts.append(f"**Full Name of Registrant:** {get_text(registrant_node, 'entityName')}")
    parts.append(f"**Address:** {format_address(registrant_node.find('businessAddress'))}")
    parts.append(f"**Reason for withdrawal:** {get_text(registrant_node, 'withdrawalDescription')}")
    parts.append(f"**Date ceased transfer agent functions:** {get_text(registrant_node, 'lastActionDate')}")
    parts.append(f"**Does registrant plan to re-register in the future?** {format_bool(get_text(registrant_node, 'futureTransferAgentFunctions'))}")
    
    parts.append("\n### Legal and Disciplinary History")
    parts.append(f"**Is the registrant subject to any proceedings?** {format_bool(get_text(registrant_node.find('subjectOfProceedings'), 'involved'))}")
    parts.append(f"**Does the registrant have any unsatisfied judgements or liens?** {format_bool(get_text(registrant_node.find('unsatisfiedJudgementsOrLiens'), 'involved'))}")

    if taw_details_node:
        successor_node = taw_details_node.find('tawSuccessorDetails')
        if successor_node:
            parts.append("\n### Successor Transfer Agent")
            parts.append(f"**Name:** {get_text(successor_node, 'entityName')}")
            parts.append(f"**Address:** {format_address(successor_node.find('tawSuccessorAddress'))}")
            parts.append(f"**Is successor registered with the SEC?** {format_bool(get_text(successor_node, 'successorRegistered'))}")

        custodian_node = taw_details_node.find('tawCustodians')
        if custodian_node:
            parts.append("\n### Custodian of Books and Records")
            parts.append(f"**Name:** {get_text(custodian_node, 'entityName')}")
            parts.append(f"**Address:** {format_address(custodian_node.find('tawCustodianAddress'))}")

    if sig_data:
        parts.append("\n### SIGNATURE")
        sig_details = {
            "Signature": get_text(sig_data, 'signatureName'),
            "Title": get_text(sig_data, 'signatureTitle'),
            "Telephone Number": get_text(sig_data, 'signaturePhoneNumber'),
            "Date": get_text(sig_data, 'signatureDate'),
        }
        for key, val in sig_details.items():
            if val != "—":
                parts.append(f"**{key}:** {val}")

    return "\n\n".join(parts)

def parse_form_24f2nt_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form 24F-2NT into a structured Markdown document.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'^(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_dollar(value_str: str) -> str:
        if not value_str or value_str == "—": return "—"
        is_negative = value_str.startswith('(') and value_str.endswith(')')
        if is_negative:
            value_str = '-' + value_str.strip('()')
        try:
            val = float(value_str)
            return f"${val:.2f}"
        except (ValueError, TypeError):
            return value_str

    parts = ["## FORM 24F-2NT: Annual Notice of Securities Sold Pursuant to Rule 24f-2"]

    header_data = xml.find('headerData')
    filer_info = header_data.find('filerInfo') if header_data else None
    filer_creds = filer_info.find('filer').find('issuerCredentials') if filer_info else None
    
    parts.append("\n### 24F-2NT: Filer Information")
    filer_details = {
        "Filer CIK": get_text(filer_creds, 'cik'),
        "Is this a LIVE or TEST Filing?": get_text(filer_info, 'liveTestFlag'),
        "Filer Investment Company Type": get_text(filer_info, 'investmentCompanyType'),
    }
    for key, val in filer_details.items():
        if val and val != "—": parts.append(f"**{key}:** {val}")

    for filing_info in xml.find_all('annualFilingInfo'):
        parts.append("\n### 24F-2NT: Annual Filing Information")

        item1 = filing_info.find('item1')
        issuer_addr = item1.find('addressOfIssuer') if item1 else None
        parts.append(f"**1. Name and address of issuer:**")
        parts.append(f"- **Name of Issuer:** {get_text(item1, 'nameOfIssuer')}")
        if issuer_addr:
            parts.append(f"- **Address:** {get_text(issuer_addr, 'street1')}, {get_text(issuer_addr, 'city')}, {get_text(issuer_addr, 'state')} {get_text(issuer_addr, 'zipCode')}")

        item2 = filing_info.find('item2')
        all_series_flag = get_text(item2.find('reportClassName'), 'rptIncludeAllFlag').lower() == 'true'
        parts.append(f"\n**2. The Name and EDGAR Identifier of each series or class of securities for which this Form is filed:**")
        parts.append(f"- [{'x' if all_series_flag else ' '}] Check box if the Form is being filed for all series and classes of the issuer.")

        item3 = filing_info.find('item3')
        parts.append(f"\n**3. Investment Company Act File Number:** {get_text(item3, 'investmentCompActFileNo')}")
        sec_act_nums = ", ".join([get_text(n, 'fileNumber') for n in item3.find_all('securitiesActFileNo')])
        parts.append(f"   **Securities Act File Number:** {sec_act_nums}")

        item4 = filing_info.find('item4')
        parts.append(f"\n**4(a). Last day of fiscal year for which this Form is filed:** {get_text(item4, 'lastDayOfFiscalYear')}")
        
        is_late = get_text(item4, 'isThisFormBeingFiledLate').lower() == 'true'
        is_last_time = get_text(item4, 'isThisTheLastTimeIssuerFilingThisForm').lower() == 'true'
        parts.append(f"**4(b). Check box if this Form is being filed late:** [{'x' if is_late else ' '}]")
        parts.append(f"**4(c). Check box if this is the last time the issuer will be filing this Form:** [{'x' if is_last_time else ' '}]")

        parts.append("\n**5. Calculation of registration fee:**")
        item5 = filing_info.find('item5')
        item6 = filing_info.find('item6')
        item7 = filing_info.find('item7')
        item8 = filing_info.find('item8')

        calc_data = [
            ("(i) Aggregate sale price of securities sold during the fiscal year:", format_dollar(get_text(item5, 'aggregateSalePriceOfSecuritiesSold'))),
            ("(ii) Aggregate price of securities redeemed or repurchased during the fiscal year:", format_dollar(get_text(item5, 'aggregatePriceOfSecuritiesRedeemedOrRepurchasedInFiscalYear'))),
            ("(iii) Aggregate price of securities redeemed or repurchased during any prior fiscal year:", format_dollar(get_text(item5, 'aggregatePriceOfSecuritiesRedeemedOrRepurchasedAnyPrior'))),
            ("(iv) Total available redemption credits [add Items 5(ii) and 5(iii)]:", format_dollar(get_text(item5, 'totalAvailableRedemptionCredits'))),
            ("(v) Net sales:", format_dollar(get_text(item5, 'netSales'))),
            ("(vi) Redemption credits available for use in future years:", format_dollar(get_text(item5, 'redemptionCreditsAvailableForUseInFutureYears'))),
            ("(vii) Multiplier for determining registration fee:", get_text(item5, 'multiplierForDeterminingRegistrationFee')),
            ("(viii) Registration fee due:", format_dollar(get_text(item5, 'registrationFeeDue'))),
            ("6(i). Amount of securities deducted:", format_dollar(get_text(item6, 'amountOfSecuritiesDeducted'))),
            ("6(ii). Number of shares or other units remaining unsold:", get_text(item6, 'numberOfSharesOrOtherUnitsRemainingUnsold')),
            ("7. Interest due -- if this Form is being filed more than 90 days after the end of the issuer's fiscal year:", format_dollar(get_text(item7, 'interestDue'))),
            ("8. Total of the amount of the registration fee due plus any interest due:", format_dollar(get_text(item8, 'totalOfRegistrationFeePlusAnyInterestDue'))),
        ]
        
        for label, value in calc_data:
            if value != "—":
                parts.append(f"- **{label}** {value}")

        notes = get_text(filing_info, 'explanatoryNotes')
        if notes != "—":
            parts.append(f"\n**Explanatory Notes (if any):**\n{notes}")

        signature = filing_info.find('signature')
        if signature:
            parts.append("\n**Signatures**")
            parts.append(f"**Name and Title:** {get_text(signature, 'nameAndTitle')}")
            parts.append(f"**Date:** {get_text(signature, 'signatureDate')}")
            parts.append(f"**Signature:** {get_text(signature, 'signature')}")

    return "\n\n".join(parts)

def parse_form_maw_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form MA-W (Notice of Withdrawal) into a structured Markdown document
    that accurately reflects the official form's layout.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'^(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_bool(value_str: str) -> str:
        s = value_str.strip().upper()
        if s == 'Y': return "Yes"
        if s == 'N': return "No"
        return "—"

    def format_name(name_node) -> str:
        if not name_node: return "—"
        first = get_text(name_node, 'firstName')
        middle = get_text(name_node, 'middleName')
        last = get_text(name_node, 'lastName')
        return " ".join(p for p in [first, middle, last] if p and p != "—")

    def format_address(addr_node) -> str:
        if not addr_node: return "—"
        parts = [
            get_text(addr_node, 'street1'),
            get_text(addr_node, 'street2'),
            get_text(addr_node, 'city'),
            get_text(addr_node, 'stateOrCountry'),
            get_text(addr_node, 'zipCode'),
        ]
        return ", ".join(p for p in parts if p and p and p != "—")

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM MA-W\n\n"
        "### NOTICE OF WITHDRAWAL FROM REGISTRATION AS A MUNICIPAL ADVISOR\n"
    ]

    header = xml.find('headerData')
    form_data = xml.find('formData')

    if not form_data:
        return "<!-- <formData> tag not found in MA-W XML -->"

    filer_id = get_text(header.find('filer'), 'filerId')
    filer_ccc = get_text(header.find('filer'), 'filerCcc')
    file_number = get_text(header.find('filer'), 'filerFileNumber')
    notification_email = get_text(header, 'internetNotificationAddress')

    parts.append("### Filer Information")
    parts.append(f"**Filer CIK:** {filer_id}")
    parts.append(f"**Filer CCC:** {filer_ccc}")
    parts.append(f"**File Number:** {file_number}")
    
    parts.append("\n### Notification Information")
    parts.append(f"**Notification Email Address:** {notification_email}")

    parts.append("\n### Item 1: Identifying Information")
    parts.append(f"**A. Full Legal Name:** {get_text(form_data, 'fullLegalName')}")
    parts.append(f"**B. SEC File Number:** {get_text(form_data, 'fileNumber')}")

    contact_person = form_data.find('contactPersonInfo')
    if contact_person:
        parts.append("\n### Item 2: Contact Person")
        name = format_name(contact_person.find('individualName'))
        address = format_address(contact_person.find('address'))
        parts.append(f"**Name, title, and contact information:**")
        parts.append(f"- **Name:** {name}")
        parts.append(f"- **Title:** {get_text(contact_person, 'title')}")
        parts.append(f"- **Address:** {address}")
        parts.append(f"- **Telephone Number:** {get_text(contact_person, 'phoneNumber')}")
        parts.append(f"- **Email Address:** {get_text(contact_person, 'email')}")

    parts.append("\n### Item 3: Money Owed to Clients")
    parts.append(f"**A. Has the registrant received any pre-paid municipal advisory fees for municipal advisory activities, including pre-paid services and subscription fees for publications, that have not been delivered?** {format_bool(get_text(form_data, 'isReceivedAnyPrepaidFee'))}")
    parts.append(f"**B. Borrowed any money from clients that has not been repaid?** {format_bool(get_text(form_data, 'isBorrowedNotRepaid'))}")

    parts.append("\n### Item 4: Advisory Contract Assignments")
    parts.append(f"**Has the registrant assigned any contracts to another person that engages in municipal advisory activities?** {format_bool(get_text(form_data, 'isAdvisoryContract'))}")

    parts.append("\n### Item 5: Judgments and Liens")
    parts.append(f"**Are there any unsatisfied judgments or liens against the registrant?** {format_bool(get_text(form_data, 'isUnsatisfiedJudgementsOrLiens'))}")

    books_records = form_data.find('booksAndRecords')
    if books_records:
        parts.append("\n### Item 6: Books and Records (from Schedule W1)")
        for location in books_records.find_all('personLocation'):
            person_info = location.find('personInfo')
            location_info = location.find('locationInfo')
            parts.append(f"\n**Person with Custody:**")
            parts.append(f"- **Name and business address:** {get_text(person_info, 'name')}, {format_address(person_info.find('address'))}")
            
            parts.append(f"\n**Location of Books and Records:**")
            parts.append(f"- **Name of Location, if any:** {get_text(location_info, 'nameAddressPhone.name') or 'Same as above'}")
            parts.append(f"- **Address:** {format_address(location_info.find('address'))}")
            parts.append(f"- **Briefly describe the books and records kept at this location:** {get_text(location_info, 'description')}")

    execution = form_data.find('execution')
    if execution:
        parts.append("\n### Execution")
        signer = execution.find('soleProprietor') or execution.find('municipalAdvisoryFirm')
        if signer:
            parts.append(f"**Signature:** {get_text(signer, 'signature')}")
            parts.append(f"**Date:** {get_text(signer, 'date')}")
            parts.append(f"**Printed Name:** {get_text(signer, 'signerName')}")
            parts.append(f"**Title:** {get_text(signer, 'title')}")
            
    return "\n\n".join(parts)

def parse_form_ma_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form MA or MA/A (for municipal advisory firms) into a
    structured Markdown document, capturing 100% of available fields.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'^(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_bool(value_str: str) -> str:
        s = value_str.strip().upper()
        if s == 'Y' or s == 'TRUE': return "Yes"
        if s == 'N' or s == 'FALSE': return "No"
        return "—"

    def format_name(name_node) -> str:
        if not name_node: return "—"
        first = get_text(name_node, 'firstName')
        middle = get_text(name_node, 'middleName')
        last = get_text(name_node, 'lastName')
        return " ".join(p for p in [first, middle, last] if p and p != "—")

    def format_address(addr_node) -> str:
        if not addr_node: return "—"
        parts = [
            get_text(addr_node, 'street1'),
            get_text(addr_node, 'street2'),
            get_text(addr_node, 'city'),
            get_text(addr_node, 'stateOrCountry'),
            get_text(addr_node, 'zipCode'),
        ]
        return ", ".join(p for p in parts if p and p.strip() != "—")

    OWNERSHIP_CODES = {
        'NA': "NA - less than 5%", 'A': "A - 5% but less than 10%",
        'B': "B - 10% but less than 25%", 'C': "C - 25% but less than 50%",
        'D': "D - 50% but less than 75%", 'E': "E - 75% or more",
        'F': "F - Other (general partner, trustee, or elected member)"
    }

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM MA: UNIFORM APPLICATION FOR MUNICIPAL ADVISOR REGISTRATION\n"
    ]

    header = xml.find('headerData')
    form_data = xml.find('formData')

    if not form_data or not header:
        return "<!-- <formData> or <headerData> not found in MA XML -->"

    filer_node = header.find('filer')
    filer_id = get_text(filer_node, 'filerId')
    filer_ccc = get_text(filer_node, 'filerCcc')
    contact_name = get_text(header.find('contact'), 'name')
    contact_phone = get_text(header.find('contact'), 'phoneNumber')
    contact_email = get_text(header, 'contactEmail')
    submission_type = get_text(header, 'submissionType')
    
    parts.append("### Filer and Contact Information")
    parts.append(f"**Filer CIK:** {filer_id}")
    parts.append(f"**Filer CCC:** {filer_ccc}")
    parts.append(f"**Contact Name:** {contact_name}")
    parts.append(f"**Contact Phone:** {contact_phone}")
    parts.append(f"**Contact Email:** {contact_email}")

    notifications = header.find_all('internetNotificationAddress')
    if notifications:
        emails = ", ".join(n.text for n in notifications)
        parts.append(f"**Notification Emails:** {emails}")

    parts.append("\n### Type of Filing")
    if submission_type == "MA":
        parts.append("**Selected Filing Type:** Initial Application")
    elif submission_type == "MA/A":
        parts.append("**Selected Filing Type:** Amendment")
    elif submission_type == "MA-A":
        parts.append("**Selected Filing Type:** Annual Update")

    parts.append("\n### Item 1: Identifying Information")
    controls_node = form_data.find('controls')
    item1_details = {
        "A. Full Legal Name of the Firm": get_text(form_data, 'firmName'),
        "   Organization CRD No.": get_text(form_data, 'firmCrdNumber'),
        "   Is applicant a Sole Proprietor?": format_bool(get_text(controls_node, 'isSolePropietor')),
        "   Has the municipal legal name changed since the last filing?": format_bool(get_text(controls_node, 'hasNameChange')),
        "B. Doing-Business-As (DBA) Name": get_text(form_data, 'dbaName'),
        "   Has the applicant had any previous DBA names?": format_bool(get_text(controls_node, 'hasPreviousDBAName')),
        "   Does the applicant have any additional DBA names?": format_bool(get_text(controls_node, 'hasAdditionalDBANames')),
        "C. IRS Employer Identification Number": get_text(form_data, 'irsNum'),
    }
    for key, val in item1_details.items():
        if val and val != "—":
            parts.append(f"**{key.replace('   ', '&nbsp;&nbsp;&nbsp;')}:** {val}")
    
    regs = form_data.find('registrations')
    if regs:
        parts.append("\n**D. Registrations:**")
        
        if (mat_reg := regs.find('maTregistration')):
            parts.append(f"- **Municipal Advisor (Temporary):** SEC File No: {get_text(mat_reg, 'fileNumber')}")

        base_regs = regs.find('baseRegistrations')
        if base_regs:
            reg_map = {
                "Municipal Advisor": base_regs.find('maRegistration'),
                "Broker-Dealer": base_regs.find('bdRegistration'),
                "SEC-Registered Investment Adviser": base_regs.find('iaRegistration'),
                "Other": base_regs.find('anotherRegistration')
            }
            for name, node in reg_map.items():
                if node and node.get_text(strip=True):
                    file_no = get_text(node, 'fileNumber')
                    crd_no = get_text(node, 'crdNumber')
                    desc = get_text(node, 'description')
                    reg_id = get_text(node, 'registrationId')
                    details = []
                    if file_no != "—": details.append(f"SEC File No: {file_no}")
                    if crd_no != "—": details.append(f"CRD No: {crd_no}")
                    if desc != "—": details.append(f"Description: {desc}")
                    if reg_id != "—": details.append(f"ID: {reg_id}")
                    parts.append(f"- **{name}:** {', '.join(details)}")
    
    additional_regs = form_data.find_all('additionalRegistration')
    if additional_regs:
        parts.append("\n**Additional Registrations:**")
        for reg in additional_regs:
            name_reg = reg.find('nameAndRegistration')
            if name_reg:
                parts.append(f"- **{get_text(name_reg, 'name')}:** {get_text(name_reg, 'registrationId')}")


    principal_address = format_address(form_data.find('principalOfficeAddress'))
    parts.append(f"\n**E. Principal Office and Place of Business:** {principal_address}")
    parts.append(f"**Telephone Number:** {get_text(form_data.find('principalOfficeAddress'), 'phoneNumber')}")

    additional_offices = form_data.find_all('additionalOffice')
    if additional_offices:
        parts.append("\n**Additional Offices of Employment:**")
        for i, office in enumerate(additional_offices, 1):
            office_info = office.find('officeInfo')
            address = format_address(office_info) if office_info else "Address Not Provided"
            parts.append(f"- **Office #{i} ({get_text(office, 'addDeleteAmend')}):** {address} | Phone: {get_text(office_info, 'phoneNumber')}")

    parts.append(f"\n**Mailing Address is Different from Principal Office:** {format_bool(get_text(form_data, 'mailingAddressDifferent'))}")
    if get_text(form_data, 'mailingAddressDifferent').upper() == 'Y':
        mailing_address = format_address(form_data.find('mailingAddress'))
        parts.append(f"**Mailing Address:** {mailing_address}")

    parts.append(f"\n**F. Website:** {get_text(form_data, 'primaryWebAddress')}")

    cco = form_data.find('cco')
    if cco:
        parts.append("\n**G. Chief Compliance Officer (CCO):**")
        cco_details = { "Name": format_name(cco.find('name')), "Titles": ", ".join(t.text for t in cco.find_all('title')), "Address": format_address(cco.find('address')), "Phone Number": get_text(cco, 'phoneNumber'), "Email": get_text(cco, 'email') }
        for key, val in cco_details.items():
            if val and val != "—": parts.append(f"**{key}:** {val}")

    affiliates = form_data.find_all('businessAffiliate')
    if affiliates:
        parts.append("\n**H. Business Affiliates:**")
        for aff in affiliates:
            parts.append(f"- **Name:** {get_text(aff, 'affiliateName')}")
            reg_info = aff.find('applicableRegistration')
            if reg_info:
                parts.append(f"  - **Issuing Agency:** {get_text(reg_info, 'issuingAgencyName')}")
                parts.append(f"  - **Jurisdiction:** {get_text(reg_info, 'jurisdiction')}")
                
    parts.append(f"**I. Location of Books and Records:** {format_bool(get_text(controls_node, 'hasBooksRecords'))}")

    parts.append("\n### Item 2: Form of Organization")
    form_org_node = form_data.find('formOfOrganization')
    org_type = "—"
    if form_org_node:
        if form_org_node.find('Corporation'): org_type = "Corporation"
        elif form_org_node.find('SoleProprietorship'): org_type = "Sole Proprietorship"
        elif form_org_node.find('LLP'): org_type = "Limited Liability Partnership (LLP)"
        elif form_org_node.find('Partnership'): org_type = "Partnership"
        elif form_org_node.find('LLC'): org_type = "Limited Liability Company (LLC)"
        elif form_org_node.find('LP'): org_type = "Limited Partnership (LP)"
        elif form_org_node.find('Other'): org_type = f"Other ({get_text(form_org_node, 'otherValue')})"

    org_details = {
        "A. Applicant's form of organization": org_type,
        "B. Month of Applicant's Annual Fiscal Year End": get_text(form_data, 'monthOfFiscalYearEnd'),
        "C. State, Other U.S. Jurisdiction, or Foreign Jurisdiction Under Which Applicant is Organized": get_text(form_data.find('organizedJurisdiction'), 'stateOrCountry'),
        "D. Date of Organization": get_text(form_data, 'dateOfOrganization'),
        "E. Is the applicant a public reporting company?": format_bool(get_text(controls_node, 'isSection12Or15ReportingCompany'))
    }
    for key, val in org_details.items():
        if val and val != "—": parts.append(f"**{key}:** {val}")

    parts.append("\n### Item 3: Successions")
    is_succeeding = format_bool(get_text(controls_node, 'isSucceedingApplicant'))
    parts.append(f"**Is the applicant succeeding to the business of a registered municipal advisor?** {is_succeeding}")
    if is_succeeding == "Yes":
        successions_node = form_data.find('successions')
        if successions_node:
            parts.append(f"**Date of Succession:** {get_text(successions_node, 'dateOfSuccession')}")
            succ_details = successions_node.find('succeedingApplicantDetails')
            if succ_details:
                parts.append(f"**Name of Predecessor:** {get_text(succ_details, 'name')}")
                parts.append(f"**CRD No. of Predecessor:** {get_text(succ_details, 'crdNumber')}")
                parts.append(f"**SEC File No. of Predecessor:** {get_text(succ_details, 'fileNumber')}")


    parts.append("\n### Item 4: Information About Applicant's Business")
    
    me_or_op_node = form_data.find('meOrOPCompensationTypes')
    solicitation_node = form_data.find('solicitationCompensationTypes')
    me_or_op_comp = ", ".join(c.text for c in me_or_op_node.find_all('compensationTypes')) if me_or_op_node else "—"
    solicitation_comp = ", ".join(c.text for c in solicitation_node.find_all('compensationTypes')) if solicitation_node else "—"
    
    biz_details = {
        "A. Number of Employees": get_text(form_data, 'numberOfEmployees'),
        "B. Municipal Advisory Activities - Employees": get_text(form_data, 'employeesEngagedInMAA'),
        "C. Registered Representatives - MAA Employees also registered reps of a broker-dealer": get_text(form_data, 'maaEmployeesRegBD'),
        "   MAA Employees also associated with an investment adviser": get_text(form_data, 'maaRegIA'),
        "D. Public Relations Company?": format_bool(get_text(form_data, 'isPrcApplicant')),
        "E. Soliciting on Behalf of an Affiliate - Number of firms": get_text(form_data, 'numberOfSolicitingFirms'),
        "F. Types of Clients - Number of clients served as municipal advisor": get_text(form_data, 'clientsServedAsMA'),
        "   Types of Clients": ", ".join(c.text for c in form_data.find_all('clientTypes')),
        "G. Solicitation of Municipal Entities and Obligated Persons - Municipal Entities": get_text(form_data, 'numberOfSolicitedME'),
        "   Obligated Persons": get_text(form_data, 'numberOfSolicitedOP'),
        "   Total Solicited": get_text(form_data, 'totalNumberOfSolicitedMEAndOP'),
        "H. Types of Persons Solicited": ", ".join(p.text for p in form_data.find_all('solicitationPersonTypes')),
        "I. Compensation Arrangements (Municipal Advisory)": me_or_op_comp,
        "J. Compensation Arrangements (Solicitation)": solicitation_comp,
        "K. Does the applicant receive compensation in the context of its municipal advisory business from other than its municipal entity or obligated person clients?": format_bool(get_text(controls_node, 'receiveCompensationForMAAFromOtherClients')),
        "L. Applicant Business Relating to Municipal Securities": ", ".join(a.text for a in form_data.find_all('engagedActivityType')),
    }
    for key, val in biz_details.items():
        if val and val != "—": parts.append(f"**{key.replace('   ', '&nbsp;&nbsp;&nbsp;')}:** {val}")

    parts.append("\n### Item 5: Other Business Activities")
    other_activities_node = form_data.find('otherActivities')
    if other_activities_node:
        activities_map = { "Broker-Dealer": other_activities_node.find('brokerDealers'), "Trust Company": other_activities_node.find('trustCompany'), "Insurance": other_activities_node.find('insurance'), "Investment Advisor": other_activities_node.find('investmentAdvisor'), }
        for activity, node in activities_map.items():
            if node:
                is_engaged = format_bool(get_text(node, 'isActivelyEngaged'))
                is_primary = format_bool(get_text(node, 'isPrimaryBusiness'))
                parts.append(f"- **{activity}:** Actively Engaged: {is_engaged}, Primary Business: {is_primary}")
    
    parts.append(f"**Is applicant engaged in any other non-municipal advisor business?** {format_bool(get_text(form_data, 'isEngagedInOtherNonMAABusiness'))}")

    parts.append("\n### Item 6: Financial Industry and Other Activities of Associated Persons")
    parts.append(f"**Types of associated persons:** {', '.join(t.text for t in form_data.find_all('fiaAPTypes'))}")
    parts.append(f"**Total Associated Persons:** {get_text(form_data, 'totalFIAAssociatedPersons')}")
            
    parts.append("\n### Item 7: Participation or Interest in Client Transactions")
    participation_node = form_data.find('participationInterestMACT')
    if participation_node:
        participation_map = {
            'mactBuySellFromClients': "Buy or sell municipal securities from or to municipal advisory clients for the firm's own account?",
            'mactBuySellRecommendToClients': "Buy or sell municipal securities from or to third-parties on behalf of clients?",
            'mactEnterDerivativesWithClients': "Enter into derivatives transactions with clients for the firm's own account?",
            'mactRecommendOwnedInterestToClients': "Recommend to clients to buy/sell securities in which the firm has a financial interest?",
            'mactRecommendToClientsServing': "Recommend to clients products/services of an affiliated person?",
            'mactRecommendToClientsHavingOtherSalesInterest': "Recommend to clients securities of an issuer with which the firm has other relationships?",
            'mactDiscAuthBuySellAsMAA': "Have discretionary authority to buy/sell municipal securities for clients?",
            'mactDiscAuthBuySell': "Have discretionary authority to buy/sell any other securities or investments for clients?",
            'mactDiscAuthDetermineBrokerDealer': "Have discretionary authority to determine the broker-dealer to be used for client transactions?",
            'mactDiscAuthDetermineCommissionToBrokerDealer': "Have discretionary authority to determine the commission paid to a broker-dealer?",
            'mactRecommendBrokerDealerToClient': "Recommend broker-dealers to clients?",
            'mactRecommendBrokerDealerToClientAreAP': "   If yes, are any of these broker-dealers an associated person of the applicant?",
            'mactCompensateForReferrals': "Compensate any person for client referrals?",
            'mactReceiveCompensationForReferrals': "Receive compensation from any person for client referrals?",
        }
        for tag, question in participation_map.items():
            parts.append(f"- **{question.replace('   ', '&nbsp;&nbsp;&nbsp;')}:** {format_bool(get_text(participation_node, tag))}")

    parts.append("\n### Item 8: Owners, Officers, and Other Control Persons")
    is_cp_for_policy = get_text(controls_node, 'isCPForApplicantPolicy')
    parts.append(f"**A. (2) Does any person not named in Item 1-A or Schedules A, B, or C, directly or indirectly, control the applicant's management or policies?** {format_bool(is_cp_for_policy)}")
    is_public_reporting_co = get_text(controls_node, 'isSection12Or15ReportingCompany')
    parts.append(f"\n**B. (1) Is any person in Schedule A, B, or C, or in Section 8-A of Schedule D a public reporting company?** {format_bool(is_public_reporting_co)}")

    parts.append("\n### Item 9: Disclosure Information")
    disclosure_node = form_data.find('disclosureAnswers')
    if disclosure_node:
        disclosure_map = {
            'Criminal': [('isConvictedOfFelony', 'Applicant/Advisory Affiliate Convicted/Pled Guilty to Felony?'), ('isChargedWithFelony', 'Applicant/Advisory Affiliate Charged with Felony?'), ('isOrgConvictedOfFelony', 'Organization Convicted/Pled Guilty to Felony?'), ('isOrgChargedWithFelony', 'Organization Charged with Felony?')],
            'Regulatory': [('isMadeFalseStatement', 'SEC/CFTC Found False Statement?'), ('isViolatedRegulation', 'SEC/CFTC Found Violation?'), ('isCauseOfDenial', 'SEC/CFTC Found Cause of Denial/Suspension?'), ('isOrderAgainst', 'SEC/CFTC Entered Order?'), ('isImposedPenalty', 'SEC/CFTC Imposed Civil Penalty?'), ('isUnEthical', 'SRO Found Unethical Conduct?'), ('isFoundInViolationOfRegulation', 'SRO Found Violation?'), ('isFoundInCauseOfDenial', 'SRO Found Cause of Denial/Suspension?'), ('isOrderAgainstActivity', 'SRO Barred/Suspended/Fined > $2,500?'), ('isDeniedLicense', 'SRO Denied/Suspended/Revoked Registration?'), ('isFoundMadeFalseStatement', 'Foreign Authority Found False Statement?'), ('isFoundInViolationOfRules', 'Foreign Authority Found Violation?'), ('isFoundInCauseOfSuspension', 'Foreign Authority Found Cause of Suspension?'), ('isDiscipliend', 'Foreign Authority Disciplined?'), ('isAuthorizedToActAttorney', 'Authorization to Act as Attorney/Accountant Revoked?'), ('isRegulatoryComplaint', 'Subject of a Regulatory Complaint?')],
            'Civil': [('isEnjoined', 'Enjoined in Connection with Municipal Advisory Activity?'), ('isFoundInViolationOfRegulation', 'Found to Have Violated Regulations?'), ('isDismissed', 'Civil Proceeding Dismissed Pursuant to Settlement?'), ('isNamedInCivilProceeding', 'Named in Civil Proceeding Alleging Violation?')]
        }
        for category, questions in disclosure_map.items():
            parts.append(f"\n**{category} Disclosure:**")
            cat_node = disclosure_node.find(f'{category.lower()}Disclosure')
            for tag, question in questions:
                parts.append(f"- **{question}:** {format_bool(get_text(cat_node, tag))}")
    
    parts.append("\n### Item 10: Small Businesses")
    parts.append(f"**Does the applicant have annual receipts of less than $7,000,000?** {format_bool(get_text(form_data, 'hasAnnualReceiptsLessThan7Million'))}")
    parts.append(f"**Is the applicant affiliated with a person that has annual receipts of more than $7,000,000?** {format_bool(get_text(form_data, 'isAffiliatedWithReceiptsMoreThan7Million'))}")

    schedule_a = form_data.find('scheduleA')
    if schedule_a:
        parts.append("\n### Schedule A: Direct Owners and Executive Officers")
        owner_data = []
        for business in schedule_a.find_all('business'):
            info = business.find('baseInformation')
            owner_data.append({ "Name": get_text(business, 'name'), "Title/Status": get_text(info, 'titleStatus'), "Date Acquired": get_text(info, 'statusAcquired'), "Ownership Code": OWNERSHIP_CODES.get(get_text(info, 'ownershipCode'), get_text(info, 'ownershipCode')), "Control Person?": format_bool(get_text(info, 'isControPerson')), "IRS Number": get_text(business, 'irsNum') })
        for person in schedule_a.find_all('person'):
            info = person.find('baseInformation')
            owner_data.append({ "Name": format_name(person.find('name')), "Title/Status": get_text(info, 'titleStatus'), "Date Acquired": get_text(info, 'statusAcquired'), "Ownership Code": OWNERSHIP_CODES.get(get_text(info, 'ownershipCode'), get_text(info, 'ownershipCode')), "Control Person?": format_bool(get_text(info, 'isControPerson')), "CRD Number": get_text(info, 'crdNumber') })
        if owner_data:
            df = pd.DataFrame(owner_data).fillna("—")
            parts.append(to_compact_markdown(df, index=False))

    schedule_b = form_data.find('scheduleB')
    if schedule_b:
        parts.append("\n### Schedule B: Indirect Owners")
        owner_data = []
        for business in schedule_b.find_all('business'):
            info = business.find('baseInfo')
            base = info.find('baseInformation')
            owner_data.append({ "Owning Entity": get_text(business, 'owningEntity'), "Name": get_text(info, 'name'), "Title/Status": get_text(base, 'titleStatus'), "Date Acquired": get_text(base, 'statusAcquired'), "Ownership Code": OWNERSHIP_CODES.get(get_text(base, 'ownershipCode'), get_text(base, 'ownershipCode')), "Control Person?": format_bool(get_text(base, 'isControPerson')), "IRS Number": get_text(info, 'irsNum') })
        for person in schedule_b.find_all('person'):
            info = person.find('baseInfo')
            base = info.find('baseInformation')
            owner_data.append({ "Owning Entity": get_text(person, 'owningEntity'), "Name": format_name(info.find('name')), "Title/Status": get_text(base, 'titleStatus'), "Date Acquired": get_text(base, 'statusAcquired'), "Ownership Code": OWNERSHIP_CODES.get(get_text(base, 'ownershipCode'), get_text(base, 'ownershipCode')), "Control Person?": format_bool(get_text(base, 'isControPerson')) })
        if owner_data:
            df = pd.DataFrame(owner_data).fillna("—")
            parts.append(to_compact_markdown(df, index=False))

    schedule_c = form_data.find('scheduleC')
    if schedule_c:
        parts.append("\n### Schedule C: Amendments to Schedules A and B")
        amendment_data = []
        for business in schedule_c.find_all(['directBusinesses', 'indirectBusinesses']):
            for biz_item in business.find_all('business'):
                info = biz_item.find('baseInformation')
                base_info_container = info.find('baseInfo') or info
                base = base_info_container.find('baseInformation')
                amendment_data.append({ "Type": get_text(biz_item, 'type'), "Ownership": "Direct" if business.name == 'directBusinesses' else "Indirect", "Owning Entity": get_text(info, 'owningEntity') or "—", "Name": get_text(base_info_container, 'name'), "Title/Status": get_text(base, 'titleStatus'), "Date Acquired": get_text(base, 'statusAcquired'), "Ownership Code": OWNERSHIP_CODES.get(get_text(base, 'ownershipCode'), get_text(base, 'ownershipCode')), "Control Person?": format_bool(get_text(base, 'isControPerson')), "IRS Number": get_text(base_info_container, 'irsNum')})
        for person in schedule_c.find_all(['directPersons', 'indirectPersons']):
            for person_item in person.find_all('person'):
                info = person_item.find('baseInformation')
                base_info_container = info.find('baseInfo') or info
                base = base_info_container.find('baseInformation')
                amendment_data.append({ "Type": get_text(person_item, 'type'), "Ownership": "Direct" if person.name == 'directPersons' else "Indirect", "Owning Entity": get_text(info, 'owningEntity') or "—", "Name": format_name(base_info_container.find('name')), "Title/Status": get_text(base, 'titleStatus'), "Date Acquired": get_text(base, 'statusAcquired'), "Ownership Code": OWNERSHIP_CODES.get(get_text(base, 'ownershipCode'), get_text(base, 'ownershipCode')), "Control Person?": format_bool(get_text(base, 'isControPerson')), "CRD Number": get_text(base, 'crdNumber')})
        if amendment_data:
            df = pd.DataFrame(amendment_data).fillna("—")
            parts.append(to_compact_markdown(df, index=False))
            
    drp_info = form_data.find('drpInfo')
    if drp_info:
        parts.append("\n### Disclosure Reporting Pages (DRPs)")
        for reg_drp in drp_info.find_all('regulatoryDrp'):
            drp_for_node = reg_drp.find('drpFor')
            drp_for = "Applicant"
            if drp_for_node and drp_for_node.find('applicantAndAP'):
                drp_for = "Applicant and Associated Person"
            elif drp_for_node and drp_for_node.find('associatedPerson'):
                drp_for = "Associated Person"

            questions = ", ".join(q.text for q in reg_drp.find_all('responseQuestion'))
            parts.append(f"\n**Regulatory DRP for: {drp_for} (Responding to Questions: {questions})**")
            
            applicant_info_node = reg_drp.find('applicantInfo')
            if applicant_info_node:
                filing = applicant_info_node.find('advBDU4Filing')
                parts.append(f"- **Filed On (Applicant):** Form ADV/BD/U4 for {get_text(filing, 'name')}")
                parts.append(f"- **CRD Number:** {get_text(filing, 'crdNumber')}")
                parts.append(f"- **Disclosure Number:** {get_text(filing, 'disclosureNumber')}")

            ap_info_node = reg_drp.find('apInfo')
            if ap_info_node:
                for ap in ap_info_node.find_all('associatedPerson'):
                    parts.append(f"\n  - **Associated Person:** {format_name(ap.find('naturalPersonInfo'))}")
                    parts.append(f"  - **CRD Number:** {get_text(ap, 'crdNumber')}")
                    ap_filing = ap.find('advBDU4Filing')
                    if ap_filing:
                         parts.append(f"  - **Filed On (AP):** Form ADV/BD/U4 for {get_text(ap_filing, 'name')}")
                         parts.append(f"  - **Disclosure Number:** {get_text(ap_filing, 'disclosureNumber')}")
    
    if form_data.find('civilDisclosureDrp') or form_data.find('criminalDisclosureDrp'):
         parts.append("\n*Additional Civil or Criminal DRPs may be present.*")

    exec_page = form_data.find('maExecutionPage')
    if exec_page:
        parts.append("\n### Execution Page")
        sig = exec_page.find('signature')
        exec_details = { "Signature": get_text(sig, 'signature'), "Signer Name": get_text(sig, 'signerName'), "Title": get_text(sig, 'title'), "Date": get_text(sig, 'date'), "CRD Number": get_text(exec_page, 'crdNumber') }
        for key, val in exec_details.items():
            if val and val != "—":
                parts.append(f"**{key}:** {val}")

    return "\n\n".join(parts)

def parse_legacy_n_mfp_xml(xml: BeautifulSoup, class_name_map: dict = None) -> str:
    """
    Parses an XML-based legacy Form N-MFP (pre-2016 schema) into a
    comprehensive, structured Markdown document.
    """
    def get_text(node, tag, strip_ns=True):
        if not node: return "—"
        found = node.find(re.compile(rf'(?:\w+:)?{tag}$', re.I), recursive=False)
        if not found:
            found = node.find(re.compile(rf'(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_val(value_str: str, type_hint: str = 'string') -> str:
        """Robustly formats values based on their intended type."""
        if not value_str or value_str.lower() in ('—', 'n/a', 'na'): return "—"
        try:
            val_float = float(value_str.replace(',', ''))
            if type_hint == 'dollar': return f"${val_float:.2f}"
            if type_hint == 'yield': return f"{val_float * 100:.4f}%"
            if type_hint == 'percent': return f"{val_float * 100:.4f}%"
            if type_hint == 'shares': return f"{val_float:.4f}"
            if type_hint == 'number': return f"{val_float:.2f}"
        except (ValueError, TypeError):
            pass

        if value_str.upper() == 'Y': return "Yes"
        if value_str.upper() == 'N': return "No"
        return value_str

    parts = ["# Form N-MFP: Monthly Schedule of Portfolio Holdings"]
    
    search_context = xml.find('edgarSubmission') or xml

    parts.append("## N-MFP: Filer Information")
    parts.append(f"**Submission Type:** {get_text(search_context, 'submissionType')}")
    parts.append(f"**Live/Test Flag:** {get_text(search_context, 'liveTestFlag')}")
    parts.append(f"**Is Electronic Copy of Paper Format:** {format_val(get_text(search_context, 'isThisElectronicCopyOfPaperFormat'))}")
    parts.append(f"**CIK:** {get_text(search_context, 'EntityCentralIndexKey')}")

    filing_info_section = ["### General Information"]
    gen_data = {
        "Report for (YYYY-MM-DD)": get_text(search_context, 'reportDate') or get_text(search_context, 'DocumentPeriodEndDate'),
        "EDGAR Series Identifier": get_text(search_context, 'seriesId'),
        "Total number of share classes in the series": get_text(search_context, 'totalClassesInSeries') or get_text(search_context, 'totalShareClassesInSeries'),
        "Is this the fund's final filing on Form N-MFP?": format_val(get_text(search_context, 'isThisFinalFiling') or get_text(search_context, 'finalFilingFlag')),
        "Is Fund Liquidating?": format_val(get_text(search_context, 'isFundLiquidating')),
        "Is Fund Merging/Being Acquired?": format_val(get_text(search_context, 'isFundMergingWithOrBeingAcquiredByAnotherFund') or get_text(search_context, 'fundAcqrdOrMrgdWthAnthrFlag')),
        "Has the fund acquired or merged with another fund during the reporting period?": format_val(get_text(search_context, 'hasFundAcquiredOrMergedWithAnotherFundSinceLastFiling')),
    }
    for key, val in gen_data.items():
        filing_info_section.append(f"**{key}:** {val}")
    parts.append("\n\n".join(filing_info_section))

    series_info = search_context.find('seriesLevelInfo') or search_context.find('seriesLevelInformation')
    if series_info:
        parts.append("\n## Part A: Series-Level Information about the Fund")

        service_providers = []
        adviser_node = series_info.find("adviser") or series_info.find("investmentAdviserList")
        if adviser_node:
            for node in adviser_node.find_all("adviser"):
                service_providers.append({"Item": "A.2", "Role": "Investment Adviser", "Details": get_text(node, 'adviserName'), "File/CIK Number": get_text(node, 'adviserFileNumber')})
        
        sub_adviser_list = series_info.find("subAdviserList")
        if sub_adviser_list:
            for sub_adviser_node in sub_adviser_list.find_all("subAdviser"):
                service_providers.append({"Item": "A.3", "Role": "Sub-Adviser", "Details": get_text(sub_adviser_node, 'adviserName'), "File/CIK Number": get_text(sub_adviser_node, 'adviserFileNumber')})

        admin_node = series_info.find('administrator') or series_info.find('administratorList')
        if admin_node and admin_node.get_text(strip=True):
             service_providers.append({"Item": "A.5", "Role": "Administrator", "Details": get_text(admin_node, 'administratorName') or admin_node.text.strip(), "File/CIK Number": "—"})
        
        accountant_node = series_info.find('indpPubAccountant') or series_info.find('independentPublicAccountant')
        if accountant_node and accountant_node.get_text(strip=True):
            acc_details = f"{get_text(accountant_node, 'name')}<br>City: {get_text(accountant_node, 'city')}<br>State: {get_text(accountant_node, 'stateCountry') or get_text(accountant_node, 'state')}"
            service_providers.append({"Item": "A.4", "Role": "Independent Public Accountant", "Details": acc_details, "File/CIK Number": "—"})

        transfer_agent_list = series_info.find('transferAgentList') or [series_info.find('transferAgent')]
        for ta_node in transfer_agent_list:
            if ta_node and ta_node.get_text(strip=True):
                ta_details = f"{get_text(ta_node, 'name')}<br>CIK: {get_text(ta_node, 'cik') or get_text(ta_node, 'EntityCentralIndexKey')}"
                service_providers.append({"Item": "A.6", "Role": "Transfer Agent", "Details": ta_details, "File/CIK Number": get_text(ta_node, 'fileNumber')})

        if service_providers:
            parts.append("\n### Service Providers\n" + to_compact_markdown(pd.DataFrame(service_providers), index=False))

        fund_chars = {
            "A.1 - Securities Act File Number": get_text(series_info, 'securitiesActFileNumber') or get_text(series_info, 'ContainedFileInformationFileNumber'),
            "A.7 - Is this a Feeder Fund?": format_val(get_text(series_info, 'feederFundFlag') or get_text(series_info, 'isThisFeederFund')),
            "A.8 - Is this a Master Fund?": format_val(get_text(series_info, 'masterFundFlag') or get_text(series_info, 'isThisMasterFund')),
            "A.9 - Is this series primarily used to fund insurance company separate accounts?": format_val(get_text(series_info, 'seriesFundInsuCmpnySepAccntFlag') or get_text(series_info, 'isThisSeriesPrimarilyUsedToFundInsuranceCompanySeperateAccounts')),
            "A.10 - Money Market Fund Category": get_text(series_info, 'moneyMarketFundCategory') or get_text(series_info, 'InvestmentTypeDomain'),
            "A.11 - WAM": f"{get_text(series_info, 'averagePortfolioMaturity') or get_text(series_info, 'dollarWeightedAveragePortfolioMaturity')} days",
            "A.12 - WAL": f"{get_text(series_info, 'averageLifeMaturity') or get_text(series_info, 'dollarWeightedAverageLifeMaturity')} days",
            "Total Value of Portfolio Securities": format_val(get_text(series_info, 'totalValuePortfolioSecurities'), 'dollar'),
            "Amortized Cost of Portfolio Securities": format_val(get_text(series_info, 'amortizedCostPortfolioSecurities') or get_text(series_info, 'AvailableForSaleSecuritiesAmortizedCost'), 'dollar'),
            "Cash": format_val(get_text(series_info, 'cash'), 'dollar'),
            "Total Other Assets": format_val(get_text(series_info, 'totalValueOtherAssets') or get_text(series_info, 'OtherAssets'), 'dollar'),
            "Total Liabilities": format_val(get_text(series_info, 'totalValueLiabilities') or get_text(series_info, 'Liabilities'), 'dollar'),
            "Net Assets of Series": format_val(get_text(series_info, 'netAssetOfSeries') or get_text(series_info, 'AssetsNet'), 'dollar'),
            "Number of Shares Outstanding (Series)": format_val(get_text(series_info, 'numberOfSharesOutstanding'), 'number'),
            "Stable Price Per Share": format_val(get_text(series_info, 'stablePricePerShare'), 'dollar'),
            "7-Day Gross Yield": format_val(get_text(series_info, 'sevenDayGrossYield') or get_text(series_info, 'MoneyMarketSevenDayYield'), 'yield')
        }
        parts.append("\n### Fund Characteristics & Assets")
        
        master_fund_node = series_info.find('masterFund')
        if get_text(series_info, 'feederFundFlag').upper() == 'Y' and master_fund_node:
            parts.append("\n**Master Fund Information:**")
            parts.append(f"- **CIK:** {get_text(master_fund_node, 'cik') or get_text(master_fund_node, 'EntityCentralIndexKey')}")
            parts.append(f"- **Name:** {get_text(master_fund_node, 'entityName') or get_text(master_fund_node, 'EntityRegistrantName')}")
            parts.append(f"- **Series ID:** {get_text(master_fund_node, 'seriesId') or get_text(master_fund_node, 'seriesIdentifier')}")
        
        for key, val in fund_chars.items():
            if val not in ("—", " days"): parts.append(f"- **{key}:** {val}")
            
        liquid_data = []
        dla_node = series_info.find('totalValueDailyLiquidAssets')
        wla_node = series_info.find('totalValueWeeklyLiquidAssets')
        pdla_node = series_info.find('percentageDailyLiquidAssets')
        pwla_node = series_info.find('percentageWeeklyLiquidAssets')
        
        if dla_node or wla_node or pdla_node or pwla_node:
            for i in range(1, 6):
                week_tag = f'fridayWeek{i}'
                if get_text(dla_node, week_tag) != "—" or get_text(wla_node, week_tag) != "—" or \
                   get_text(pdla_node, week_tag) != "—" or get_text(pwla_node, week_tag) != "—":
                    
                    liquid_data.append({
                        "Period": f"Friday, Week {i}",
                        "Daily Liquid Assets ($)": format_val(get_text(dla_node, week_tag), 'dollar'),
                        "Weekly Liquid Assets ($)": format_val(get_text(wla_node, week_tag), 'dollar'),
                        "Daily Liquid Assets (%)": format_val(get_text(pdla_node, week_tag), 'percent'),
                        "Weekly Liquid Assets (%)": format_val(get_text(pwla_node, week_tag), 'percent'),
                    })

        if liquid_data:
            parts.append("\n### Weekly Liquid Assets\n" + to_compact_markdown(pd.DataFrame(liquid_data), index=False))

        series_shadow_price_node = series_info.find('seriesShadowPrice')
        if series_shadow_price_node:
            parts.append("\n**Series Shadow Price:**")
            parts.append(f"- **NAV Per Share (incl. support):** {format_val(get_text(series_shadow_price_node, 'netValuePerShareIncludingCapitalSupportAgreement'), 'shares')} (as of {get_text(series_shadow_price_node, 'dateCalculatedFornetValuePerShareIncludingCapitalSupportAgreement')})")
            parts.append(f"- **NAV Per Share (excl. support):** {format_val(get_text(series_shadow_price_node, 'netValuePerShareExcludingCapitalSupportAgreement'), 'shares')} (as of {get_text(series_shadow_price_node, 'dateCalculatedFornetValuePerShareExcludingCapitalSupportAgreement')})")

    class_level_nodes = search_context.find_all('classLevelInformation') or search_context.find_all('classLevelInfo')
    if class_level_nodes:
        parts.append("\n## Part B: Class-Level Information about the Fund")
        
        if class_name_map is None:
            class_name_map = {}

        for i, node in enumerate(class_level_nodes):
            class_id = get_text(node, 'classId') or get_text(node, 'classesId')
            
            class_name = class_name_map.get(class_id.upper(), f"Unknown Class ({class_id})")
            parts.append(f"\n### Class: {class_name}")
            
            class_details = {
                "B.2 - Minimum Initial Investment": format_val(get_text(node, 'minInitialInvestment'), 'dollar'),
                "B.3 - Net Assets of Class": format_val(get_text(node, 'netAssetsOfClass'), 'dollar'),
                "B.4 - Shares Outstanding": format_val(get_text(node, 'numberOfSharesOutstanding'), 'number'),
                "B.4 - Net Asset Value Per Share": format_val(get_text(node, 'netAssetValuePerShare'), 'number'),
                "B.7.7 - 7-Day Net Yield": format_val(get_text(node, 'sevenDayNetYield'), 'yield'),
                "Person Paying for Fund Expenses?": format_val(get_text(node, 'personPayForFundFlag')),
                "Expense Reimbursement/Waiver Description": get_text(node, 'nameOfPersonDescExpensePay')
            }
            for key, val in class_details.items():
                if val != "—": parts.append(f"- **{key}:** {val}")
            
            weekly_flows = []
            flow_nodes = node.find_all(re.compile(r'^(?:\w+:)?fridayWeek\d+$', re.I), recursive=False)
            
            for week_node in flow_nodes:
                week_number_match = re.search(r'(\d+)$', week_node.name)
                if not week_number_match:
                    continue
                
                week_number_str = week_number_match.group(1)

                subs = get_text(week_node, 'weeklyGrossSubscriptions')
                reds = get_text(week_node, 'weeklyGrossRedemptions')
                
                if (subs and subs != "0.00") or (reds and reds != "0.00"):
                    weekly_flows.append({
                        "Period": f"Week {week_number_str}",
                        "Gross Subscriptions ($)": format_val(subs, 'dollar'),
                        "Gross Redemptions ($)": format_val(reds, 'dollar'),
                    })

            if weekly_flows:
                weekly_flows.sort(key=lambda x: int(re.search(r'\d+', x['Period']).group()))
                parts.append("\n**Weekly Flows:**")
                parts.append(to_compact_markdown(pd.DataFrame(weekly_flows), index=False))

            total_node = node.find("totalForTheMonthReported")
            if total_node:
                parts.append("\n**Monthly Shareholder Flow Activity:**")
                parts.append(f"- **Gross Subscriptions for month:** {format_val(get_text(total_node, 'weeklyGrossSubscriptions'), 'dollar')}")
                parts.append(f"- **Gross Redemptions for month:** {format_val(get_text(total_node, 'grossRedemptions'), 'dollar')}")
            else:
                 parts.append(f"- **Net flow for month:** {format_val(get_text(node, 'netShareholderFlowActivityForMonthEnded'), 'dollar')}")

            class_shadow_price_node = node.find('classShadowPrice')
            if class_shadow_price_node:
                incl_node = class_shadow_price_node.find('netAssetValuePerShareIncludingCapitalSupportAgreement')
                excl_node = class_shadow_price_node.find('netAssetValuePerShareExcludingCapitalSupportAgreement')
                parts.append("\n**Class Shadow Price:**")
                parts.append(f"- **NAV Per Share (incl. support):** {format_val(get_text(incl_node, 'value'), 'shares')} (as of {get_text(incl_node, 'dateAsOfWhichValueWasCalculated')})")
                parts.append(f"- **NAV Per Share (excl. support):** {format_val(get_text(excl_node, 'value'), 'shares')} (as of {get_text(excl_node, 'dateAsOfWhichValueWasCalculated')})")

    securities_nodes = search_context.find_all('scheduleOfPortfolioSecuritiesInfo') or search_context.find_all('scheduleOfPortfolioSecurities')
    if securities_nodes:
        parts.append("\n## Part C: Schedule of Portfolio Securities")
        
        for i, node in enumerate(securities_nodes):
            parts.append(f"\n### Security {i+1}: {get_text(node, 'nameOfIssuer') or get_text(node, 'InvestmentIssuer')}")
            
            security_details = [
                f"**C.1 - Title:** {get_text(node, 'titleOfIssuer') or get_text(node, 'InvestmentTitle')}",
                f"**C.6 - Investment Category:** {get_text(node, 'investmentCategory') or get_text(node, 'InvestmentTypeDomain')}",
            ]
            
            id_data = {
                "C.3 - CUSIP": get_text(node, 'CUSIPMember'), 
                "C.4 - ISIN": get_text(node, 'ISINId'), 
                "C.3 - LEI": get_text(node, 'LEIID'),
                "CIK": get_text(node, 'cik'),
                "C.5 - Other ID": get_text(node, 'otherUniqueId')
            }
            id_str = ", ".join([f"{k}: {v}" for k, v in id_data.items() if v != "—"])
            if id_str: security_details.append(f"**Identifiers:** {id_str}")

            rating_node = node.find('designatedNrsro')
            rating_str = get_text(node, 'securityRated') or get_text(node, 'rating')
            if rating_node and get_text(rating_node, 'nameOfDesignatedNRSRO') != 'N/A':
                 rating_str += f" ({get_text(rating_node, 'nameOfDesignatedNRSRO')}: {get_text(rating_node, 'creditRatingDesignatedNRSRO')})"
            if rating_str != "—":
                security_details.append(f"**Rating:** {rating_str}")

            security_details.extend([
                f"**C.18 - Value (incl. sponsor support):** {format_val(get_text(node, 'includingValueOfAnySponsorSupport'), 'dollar')}",
                f"**C.18.a - Value (excl. sponsor support):** {format_val(get_text(node, 'excludingValueOfAnySponsorSupport') or get_text(node, 'valueOfSecurityExcludingValueOfCapitalSupportAgreement'), 'dollar')}",
                f"**Principal Amount:** {format_val(get_text(node, 'InvestmentOwnedBalancePrincipalAmount'), 'dollar')}",
                f"**Amortized Cost:** {format_val(get_text(node, 'AvailableForSaleSecuritiesAmortizedCost'), 'dollar')}",
                f"**Fair Value:** {format_val(get_text(node, 'InvestmentOwnedAtFairValue'), 'dollar')}",
                f"**C.19 - Percentage of Net Assets:** {format_val(get_text(node, 'percentageOfMoneyMarketFundNetAssets') or get_text(node, 'InvestmentOwnedPercentOfNetAssets'), 'percent')}",
                f"**C.17 - Yield as of Reporting Date:** {format_val(get_text(node, 'yieldOfTheSecurityAsOfReportingDate'), 'yield')}",
                f"**C.11 - Maturity Date (WAM):** {get_text(node, 'investmentMaturityDateWAM') or get_text(node, 'InvestmentMaturityDate')}",
                f"**C.12 - Maturity Date (WAL):** {get_text(node, 'investmentMaturityDateWAL') or get_text(node, 'InvestmentMaturityDate')}",
                f"**C.13 - Final Legal Maturity Date:** {get_text(node, 'finalLegalInvestmentMaturityDate')}",
            ])
            
            flags = {
                "C.14 - Has Demand Feature?": format_val(get_text(node, 'securityDemandFeatureFlag') or get_text(node, 'doesSecurityHaveDemandFeature')),
                "C.15 - Has Guarantee?": format_val(get_text(node, 'securityGuaranteeFlag') or get_text(node, 'doesSecurityHaveGuarantee')),
                "C.16 - Has Enhancement?": format_val(get_text(node, 'securityEnhancementsFlag') or get_text(node, 'doesSecurityHaveEnhancementsOnWhichFundRelying')),
                "C.22 - Is an Illiquid Security?": format_val(get_text(node, 'illiquidSecurityFlag') or get_text(node, 'isThisIlliquidSecurity')),
                "C.20 - Is a Daily Liquid Asset?": format_val(get_text(node, 'dailyLiquidAssetSecurityFlag')),
                "C.21 - Is a Weekly Liquid Asset?": format_val(get_text(node, 'weeklyLiquidAssetSecurityFlag')),
                "C.23 - Categorized at Level 3?": format_val(get_text(node, 'securityCategorizedAtLevel3Flag')),
            }
            flag_str = ", ".join([f"{k.split('-')[0].strip()} {k.split('-')[1].strip()} {v}" for k, v in flags.items() if v != "—"])
            if flag_str: security_details.append(f"**Characteristics:** {flag_str}")

            parts.append("\n".join(f"- {item}" for item in security_details))
            
            guarantor_node = node.find('guarantor')
            if guarantor_node and guarantor_node.get_text(strip=True):
                parts.append("\n**C.15.a - Guarantor Details:**")
                guarantor_details = {"Identity of Guarantor": get_text(guarantor_node, 'identityOfTheGuarantor'), "Amount Provided": get_text(guarantor_node, 'amountProvidedByGuarantor'),}
                for key, val in guarantor_details.items():
                    if val != "—": parts.append(f"- **{key}:** {val}")
                guarantor_rating_node = guarantor_node.find('designatedNRSROGuarantor')
                if guarantor_rating_node:
                    rating_str = f"{get_text(guarantor_rating_node, 'nameOfDesignatedNRSRO')}: {get_text(guarantor_rating_node, 'creditRatingDesignatedNRSRO')}"
                    if rating_str != ":": parts.append(f"- **Rating:** {rating_str}")

            enhancement_node = node.find('enhancementProvider')
            if enhancement_node and enhancement_node.get_text(strip=True):
                parts.append("\n**C.16.a - Enhancement Details:**")
                enhancement_details = {"Identity of Provider": get_text(enhancement_node, 'identityOfTheEnhancementProvider'), "Type of Enhancement": get_text(enhancement_node, 'typeOfEnhancement'), "Amount Provided": get_text(enhancement_node, 'amountProvidedByEnhancement'), }
                for key, val in enhancement_details.items():
                    if val != "—": parts.append(f"- **{key}:** {val}")
                enhancement_rating_node = enhancement_node.find('designatedNRSROEnhancement')
                if enhancement_rating_node:
                    rating_str = f"{get_text(enhancement_rating_node, 'nameOfDesignatedNRSRO')}: {get_text(enhancement_rating_node, 'creditRatingDesignatedNRSRO')}"
                    if rating_str != ":": parts.append(f"- **Rating:** {rating_str}")
    
    sig = search_context.find('signature')
    if sig:
        parts.append("\n## N-MFP: Signatures")
        parts.append(f"**Registrant:** {get_text(sig, 'registrant')}")
        parts.append(f"**Date:** {get_text(sig, 'signatureDate')}")
        parts.append(f"**By:** {get_text(sig, 'signature')}")
        parts.append(f"**Name of Signing Officer:** {get_text(sig, 'nameOfSigningOfficer')}")
        parts.append(f"**Title of Signing Officer:** {get_text(sig, 'titleOfSigningOfficer')}")
            
    return "\n\n".join(parts)

def parse_sbse_a_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form SBSE-A or SBSE-A/A into a structured Markdown document.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'^(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_bool(value_str: str) -> str:
        s = value_str.strip().upper()
        if s == 'Y': return "Yes"
        if s == 'N': return "No"
        return "—"

    def format_name(name_node) -> str:
        if not name_node: return "—"
        first = get_text(name_node, 'firstName')
        middle = get_text(name_node, 'middleName')
        last = get_text(name_node, 'lastName')
        return " ".join(p for p in [first, middle, last] if p and p != "—")

    def format_address(addr_node) -> str:
        if not addr_node: return "—"
        parts = [
            get_text(addr_node, 'street1'), get_text(addr_node, 'street2'),
            get_text(addr_node, 'city'), get_text(addr_node, 'stateOrCountry'),
            get_text(addr_node, 'zipCode'),
        ]
        return ", ".join(p for p in parts if p and p.strip() != "—")

    OWNERSHIP_CODES = {
        'NA': "NA - less than 5%", 'A': "A - 5% but less than 10%",
        'B': "B - 10% but less than 25%", 'C': "C - 25% but less than 50%",
        'D': "D - 50% but less than 75%", 'E': "E - 75% or more",
    }

    parts = ["## Form SBSE-A: Registration for Security-Based Swap Dealers"]
    form_data = xml.find('formData')
    if not form_data:
        return "<!-- <formData> tag not found in SBSE-A/A XML -->"

    app1 = form_data.find('applicantOne')
    if app1:
        parts.append("\n### Applicant Information")
        app1_details = {
            "Full Applicant Name": get_text(app1, 'fullApplicantName'),
            "NFA Number": get_text(app1, 'applicantNFANumber'),
            "IRS Employer ID No.": get_text(app1, 'irsEmplIdentNo'),
            "CIK": get_text(app1, 'applicantCik'),
            "UIC": get_text(app1, 'applicantUic'),
            "Main Address": format_address(app1.find('mainAddress')),
            "Mailing Address": format_address(app1.find('mailingAddress')),
            "Business Telephone": get_text(app1, 'businessTelephoneNumber'),
        }
        for key, val in app1_details.items():
            if val and val != "—": parts.append(f"**{key}:** {val}")

        contact = app1.find('contactEmployee')
        if contact:
            parts.append("\n**Contact Employee:**")
            parts.append(f"- **Name:** {format_name(contact.find('contactEmployeeName'))}")
            parts.append(f"- **Title:** {get_text(contact, 'title')}")
            parts.append(f"- **Phone:** {get_text(contact, 'phone')}")
            parts.append(f"- **Email:** {get_text(contact, 'emailAddress')}")

        cco = app1.find('chiefComplianceOfficer')
        if cco:
            parts.append("\n**Chief Compliance Officer:**")
            parts.append(f"- **Name:** {format_name(cco.find('officerName'))}")
            parts.append(f"- **Title:** {get_text(cco, 'title')}")
            parts.append(f"- **Phone:** {get_text(cco, 'phone')}")
            parts.append(f"- **Email:** {get_text(cco, 'emailAddress')}")

    app2 = form_data.find('applicantTwo')
    if app2:
        parts.append("\n### Business and Activities")
        app2_details = {
            "Registered as Swap Dealer?": format_bool(get_text(app2, 'isSwapDealer')),
            "Registered as Swap Participant?": format_bool(get_text(app2, 'isSwapParticipant')),
            "Uses Mathematical Models?": format_bool(get_text(app2, 'isMathematicalModels')),
            "Is a Non-Resident Entity?": format_bool(get_text(app2, 'isNonResidentEntity')),
            "Subject to Prudential Regulator?": format_bool(get_text(app2, 'isSubjectToRegulator')),
            "Is an Investment Advisor?": format_bool(get_text(app2, 'isInvestmentAdvisor')),
            "Engaged in Other Business?": format_bool(get_text(app2, 'isEngageInOtherBusiness')),
            "Holds Customer Funds?": format_bool(get_text(app2, 'isHoldFunds')),
        }
        for key, val in app2_details.items(): parts.append(f"**{key}:** {val}")
        
        regulators = ", ".join(r.text for r in app2.find_all('prudentialRegulator'))
        if regulators: parts.append(f"**Prudential Regulators:** {regulators}")
        
        biz_desc = get_text(app2, 'descriptionBusiness')
        if biz_desc != "—": parts.append(f"\n**Description of Business:**\n{biz_desc}")

    app3 = form_data.find('applicantThree')
    if app3:
        parts.append("\n### Control and History")
        app3_details = {
            "Are records kept by another entity?": format_bool(get_text(app3, 'isRecordsKept')),
            "Does another entity hold funds on behalf of applicant?": format_bool(get_text(app3, 'isOnBehalf')),
            "Is control exercised through an agreement?": format_bool(get_text(app3, 'isControlThroughAgreement')),
            "Is applicant financed by another entity?": format_bool(get_text(app3, 'isWhollyOrPartiallyFinance')),
            "Is applicant succeeding a prior entity?": format_bool(get_text(app3, 'isSucceeding')),
            "Subject to foreign regulation?": format_bool(get_text(app3, 'isForeignRegulatory')),
            "Number of Principals": get_text(app3, 'numberOfPrincipals'),
        }
        for key, val in app3_details.items(): parts.append(f"**{key}:** {val}")
    
    schedule_a = form_data.find('scheduleA')
    if schedule_a:
        parts.append("\n### Schedule A: Principals")
        principals_data = []
        for principal in schedule_a.find_all('scheduleAInfo'):
            ownership_code = get_text(principal, 'ownershipCode')
            principals_data.append({
                "Name": format_name(principal.find('individualName')),
                "Title or Status": get_text(principal, 'titleOrStatus'),
                "Date Acquired": get_text(principal, 'dateTitleOrStatusAcquired'),
                "Date Began Working": get_text(principal, 'dateBeganWorking'),
                "Ownership": OWNERSHIP_CODES.get(ownership_code, ownership_code),
                "NFA ID No.": get_text(principal, 'nfaIdentificationNo'),
            })
        if principals_data:
            df = pd.DataFrame(principals_data)
            parts.append(to_compact_markdown(df, index=False))

    schedule_b = form_data.find('scheduleB')
    if schedule_b:
        parts.append("\n### Schedule B: Explanations")
        
        section1 = schedule_b.find('sectionOne')
        if section1:
            parts.append(f"\n**Description:**\n{get_text(section1, 'description')}")

        section2 = schedule_b.find('sectionTwo')
        if section2:
            for record_type in ['recordsKept', 'onBehalf', 'controlThroughAgreement']:
                records = section2.find_all(record_type)
                if records:
                    title = record_type.replace('Kept', ' Keeper').replace('controlThroughAgreement', 'Controlling Entity')
                    parts.append(f"\n**{title.title()}:**")
                    for record in records:
                        parts.append(f"- **Name:** {get_text(record, 'firmOrOrganizationName')}")
                        parts.append(f"  - **Address:** {format_address(record.find('firmAddress'))}")
                        parts.append(f"  - **Effective Date:** {get_text(record, 'firmEffectiveDate')}")
                        parts.append(f"  - **Arrangement:** {get_text(record, 'descriptionArrangement')}")

    execution = form_data.find('execution')
    if execution:
        parts.append("\n### Execution")
        exec_details = {
            "Date": get_text(execution, 'date'),
            "Name of Applicant": get_text(execution, 'nameOfApplicant'),
            "Signature": get_text(execution, 'signature'),
            "Printed Name": get_text(execution, 'nameOfPersonSigning'),
            "Title": get_text(execution, 'titleOfPersonSigning'),
        }
        for key, val in exec_details.items(): parts.append(f"**{key}:** {val}")

    return "\n\n".join(parts)

def parse_form_atsn_xml(xml: BeautifulSoup) -> str:
    """
    Parses any XML-based Form ATS-N filing (including /MA, /UA, /OFA, /CA, etc.)
    into a structured Markdown document.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'^(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_bool(value_str: str) -> str:
        s = value_str.strip().upper()
        if s == 'Y' or s == 'TRUE': return "Yes"
        if s == 'N' or s == 'FALSE': return "No"
        return "—"

    def format_address(addr_node) -> str:
        if not addr_node: return "—"
        parts = [
            get_text(addr_node, 'street1'), get_text(addr_node, 'street2'),
            get_text(addr_node, 'city'), get_text(addr_node, 'state'),
            get_text(addr_node, 'zip')
        ]
        return ", ".join(p for p in parts if p and p.strip() != "—")

    def get_rb_answer_and_details(parent_node, question_tag, details_tag):
        question_node = parent_node.find(question_tag)
        if not question_node: return "—", None
        
        bool_attr = next((attr for attr in question_node.attrs if attr.startswith('rb')), None)
        answer = format_bool(question_node.get(bool_attr, "N"))
        
        details_text = None
        if answer in ["Yes", "No"]:
            details_node = question_node.find(details_tag)
            if details_node and details_node.text.strip():
                details_text = details_node.text.strip()
        
        return answer, details_text
        
    form_data = xml.find('formData')
    if not form_data:
        cover = xml.find('cover') or xml.find(re.compile(r'(?:\w+:)?cover$', re.I))
        if cover:
             submission_type = get_text(xml, 'submissionType')
             title = f"Form {submission_type}: NMS Stock Alternative Trading System Report"
             parts = [f"## {title}\n\n### Cover Page"]
             parts.append(f"**NMS Stock ATS Name:** {get_text(cover, 'txNMSStockATSName')}")
             parts.append(f"\n**Statement About Amendment:**\n{get_text(cover, 'taStatementAboutAmendment')}")
             return "\n".join(parts)
        return "<!-- <formData> not found in ATS-N XML -->"

    submission_type_node = xml.find('submissionType')
    submission_type = submission_type_node.text.strip() if submission_type_node else "ATS-N"
    title = f"Form {submission_type}: NMS Stock Alternative Trading System Report"

    parts = [f"## {title}"]

    cover = form_data.find('cover')
    if cover:
        parts.append("\n### Cover Page")
        parts.append(f"**NMS Stock ATS Name:** {get_text(cover, 'txNMSStockATSName')}")
        parts.append(f"**Operates Pursuant to Form ATS?** {format_bool(get_text(cover, 'rbOperatesPursuantToFormATS'))}")
        parts.append(f"\n**Statement About Amendment:**\n{get_text(cover, 'taStatementAboutAmendment')}")

    p1 = form_data.find('partOne')
    parts.append("\n### Part I: Basic Information")
    p1_details = {
        "1. Is the ATS operated by a registered broker-dealer?": format_bool(get_text(p1, 'rbPart1Item1IsBd')),
        "2. Name of the NMS Stock ATS": get_text(p1, 'txPart1Item2ATSName'),
        "3. Name(s) under which business is conducted": ", ".join([n['txPart1Item3ATSName'] for n in p1.find_all('atsName')]),
        "4a. Broker-Dealer SEC File No.": get_text(p1, 'txPart1Item4aBdFileNumber'),
        "4a. Broker-Dealer CRD No.": get_text(p1, 'txPart1Item4aBdCrdNumber'),
        "5a. Self-Regulatory Organization": get_text(p1, 'txPart1Item5aNsaFullName'),
        "5b. Effective Date of Membership": get_text(p1, 'part1Item5bEffectiveMembershipDate'),
        "5c. MPID": get_text(p1, 'txtPart1Item5cNmsStockMPID'),
        "6u. Website": get_text(p1, 'txtPart1Item6uwebsite'),
        "7. Primary Site Address": format_address(p1.find('part1Item7PrimarySite')),
        "7. Secondary Site Address": format_address(p1.find('secondarySiteI7')),
        "8. Is Exhibit 1 (list of subscribers) on a public website?": format_bool(get_text(p1, 'cbPart1Item8Exhibit1atWebsite')),
        "9. Is Exhibit 2 (written standards for access) on a public website?": format_bool(get_text(p1, 'cbPart1Item9Exhibit2atWebsite')),
    }
    for key, val in p1_details.items():
        if val and val != "—":
            parts.append(f"**{key}:** {val}")
            
    p2 = form_data.find('partTwo')
    parts.append("\n### Part II: Written Safeguards and Procedures")
    
    answer, details = get_rb_answer_and_details(p2, 'part2Item1aArePermittedToEnterInterest', 'taPart2Item1aUnitNamesEnterInterest')
    parts.append(f"\n**1a. Are any business units of the Broker-Dealer Operator permitted to enter interest?** {answer}")
    if details: parts.append(f"   - **Details:** {details}")

    answer, details = get_rb_answer_and_details(p2, 'part2Item1bAreSevicesSametoAllSubscribers', 'taPart2Item2bExplainDiff')
    parts.append(f"**1b. Are the services offered and provided by the ATS to such business units the same?** {answer}")
    if details: parts.append(f"   - **Explanation:** {details}")
    
    parts.append(f"**1c. Are there any arrangements between the ATS and such business unit?** {format_bool(get_text(p2, 'rbPart2Item1cAreThereArrangements'))}")
    parts.append(f"**1d. Can order and trading interest of the business unit be routed out of the ATS?** {format_bool(get_text(p2, 'rbPart2Item1dCanOATInterestBeRouted'))}")

    answer, details = get_rb_answer_and_details(p2, 'affiliatesPermittedToEnterInterest', 'taPart2Item2aAfflThatEnterInterest')
    parts.append(f"\n**2a. Are any Affiliates of the Broker-Dealer Operator permitted to enter interest?** {answer}")
    if details: parts.append(f"   - **Affiliates:** {details}")

    answer, details = get_rb_answer_and_details(p2, 'part2Item2bAreSevicestoAfflSametoSubscribers', 'taPart2Item2bExplainDiff')
    parts.append(f"**2b. Are the services offered and provided by the ATS to such Affiliates the same?** {answer}")
    if details: parts.append(f"   - **Explanation:** {details}")

    answer, details = get_rb_answer_and_details(p2, 'part2Item2cAnyFrmlInfrmlArrngmnts', 'taPart2Item2cYesAfflteDtls')
    parts.append(f"**2c. Are there any arrangements between the ATS and such Affiliate?** {answer}")
    if details: parts.append(f"   - **Details:** {details}")

    parts.append(f"**2d. Can order and trading interest of the Affiliate be routed out of the ATS?** {format_bool(get_text(p2, 'rbPart2Item2dCanOATIBeRoutedByAffl'))}")
    
    answer, details = get_rb_answer_and_details(p2, 'part2Item3aCanSubscrOptOutWithOATIOfBD', 'taPart2Item3aExplianOptOut')
    parts.append(f"\n**3a. Can a Subscriber opt-out from interacting with the order and trading interest of the Broker-Dealer Operator?** {answer}")
    if details: parts.append(f"   - **Explanation:** {details}")
    
    answer, details = get_rb_answer_and_details(p2, 'part2Item3aCanSubscrOptOutWithOATIOfAffl', 'taPart2Item3bExplianOptOut')
    parts.append(f"**3b. Can a Subscriber opt-out from interacting with the order and trading interest of an Affiliate?** {answer}")
    if details: parts.append(f"   - **Explanation:** {details}")

    answer, details = get_rb_answer_and_details(p2, 'part2Item3cAreOptOutSametoAllSubscribers', 'taPart2Item3cExplainDiff')
    parts.append(f"**3c. Are the means to opt-out the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Explanation:** {details}")
    
    parts.append(f"\n**4a. Are there any arrangements between the Broker-Dealer Operator and a trading center?** {format_bool(get_text(p2, 'rbPart2Item4aAreThereArrangementsBtwBDAndTC'))}")

    answer, details = get_rb_answer_and_details(p2, 'part2Item5aDoesOfferProductsAndServices', 'taPart2Item5aProductsAndServices')
    parts.append(f"\n**5a. Does the Broker-Dealer Operator offer any products or services to Subscribers?** {answer}")
    if details: parts.append(f"   - **Products/Services:** {details}")
    
    answer, details = get_rb_answer_and_details(p2, 'part2Item5bAreSevicesSametoAllSubscribersAndBD', 'taPart2Item5bExplainDiff')
    parts.append(f"**5b. Are the terms and conditions of these products/services the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Explanation:** {details}")

    answer, details = get_rb_answer_and_details(p2, 'part2Item5cDoesAfflOfferProductsAndServices', 'taPart2Item5cAfflProvidedProductsAndServices')
    parts.append(f"**5c. Does an Affiliate of the Broker-Dealer Operator offer any products or services to Subscribers?** {answer}")
    if details: parts.append(f"   - **Products/Services:** {details}")
    
    parts.append(f"**5d. Are the terms and conditions of these products/services offered by the Affiliate the same for all Subscribers?** {format_bool(get_text(p2, 'rbPart2Item5dAreTCOfSevicesSametoAll'))}")
    
    answer, details = get_rb_answer_and_details(p2, 'part2Item6aDoesEmployeeAccessConfidentialInfo', 'taPart2Item6aUnitAfflEmployeeServices')
    parts.append(f"\n**6a. Do any employees of the Broker-Dealer Operator or its Affiliates access confidential trading information?** {answer}")
    if details: parts.append(f"   - **Details:** {details}")

    answer, details = get_rb_answer_and_details(p2, 'part2Item6bDoesAnyEntitySupportServices', 'taPart2Item6bServiceProvider')
    parts.append(f"**6b. Does any other entity provide services to the ATS?** {answer}")
    if details: parts.append(f"   - **Providers:** {details}")
    
    answer, details = get_rb_answer_and_details(p2, 'part2Item6cDoesServiceProviderUseATSServices', 'taPart2Item6cProviderAfflAndServicesUsed')
    parts.append(f"**6c. Do any of these service providers also use the services of the ATS?** {answer}")
    if details: parts.append(f"   - **Details:** {details}")

    answer, details = get_rb_answer_and_details(p2, 'part2Item6dAreATSSevicesSametoAll', 'taPart2Item6dExplainDiff')
    parts.append(f"**6d. Are the services of the ATS to such service provider the same as for other similar Subscribers?** {answer}")
    if details: parts.append(f"   - **Explanation:** {details}")

    parts.append(f"\n**7a. Description of Safeguards and Procedures:**\n{get_text(p2, 'taPart2Item7aDescrOfSafeGaurdsAndProcedures')}")
    parts.append(f"**7b. Can a Subscriber consent to the disclosure of its confidential trading information?** {format_bool(get_text(p2, 'rbPart2Item7bCanSubscriberConsentToDisclosure'))}")
    parts.append(f"**7d. Summary of roles of persons with access to confidential trading information:**\n{get_text(p2, 'taPart2Item7dSummaryOfRolesRespOfPersons')}")

    p3 = form_data.find('partThree')
    parts.append("\n### Part III: Manner of Operations")
    parts.append(f"\n**1. Types of Subscribers:** {', '.join(t.text for t in p3.find_all('taPart3Item1SubscriberType'))}")
    
    parts.append(f"**2a. Is a Subscriber required to be a registered broker-dealer?** {format_bool(get_text(p3, 'rbPart3Item2aRegisteredBD'))}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item2bSummaryOfConditions', 'taPart3Item2bSummaryOfCndtns')
    parts.append(f"**2b. Are there any other conditions for eligibility to become a Subscriber?** {answer}")
    if details: parts.append(f"   - **Conditions:** {details}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item2cSummaryOfConditions', 'taPart3Item2cSummaryOfDifferences')
    parts.append(f"**2c. Are the conditions for eligibility the same for all persons?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")
    
    parts.append(f"**2d. Is there a written agreement required to use the ATS?** {format_bool(get_text(p3, 'rbPart3Item2dIsThereWrittenAgreement'))}")

    answer, details = get_rb_answer_and_details(p3, 'part3Item3aSumryOfExcludngCondtns', 'taPart3Item3aExcludngSumryDtls')
    parts.append(f"\n**3a. Are there any conditions under which a Subscriber may be excluded?** {answer}")
    if details: parts.append(f"   - **Conditions:** {details}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item3bSummaryOfConditions', 'taPart3Item3bSummaryOfDifferences')
    parts.append(f"**3b. Are these conditions the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")

    parts.append(f"\n**4a. Hours of Operation:**\n{get_text(p3, 'taPart3Item4aHrsOfOperation')}")
    parts.append(f"**4b. Are the hours of operation the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item4bIsHrsOfOperationsame'))}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item5aProtocolDetails', 'taPart3Item5aProtocolused')
    parts.append(f"\n**5a. Are Subscribers permitted to enter orders and other messages by electronic means?** {answer}")
    if details: parts.append(f"   - **Protocols:** {details}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item5bProtocolDetails', 'taPart3Item5aProtocolSumryDtls')
    parts.append(f"**5b. Are these protocols the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item5cOthrDtls', 'taPart3Item5cOthrMeansDtls')
    parts.append(f"**5c. Are there any other means to enter orders?** {answer}")
    if details: parts.append(f"   - **Details:** {details}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item5dTnCDetails', 'taPart3Item5dTnCSumryDtls')
    parts.append(f"**5d. Are the terms and conditions for other means the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")

    parts.append(f"\n**6a. Are co-location services offered?** {format_bool(get_text(p3, 'rbPart3Item6aIsCoLocRltdSrvcsOfrd'))}")
    parts.append(f"**6c. Are any other means offered that reduce the latency of communications?** {format_bool(get_text(p3, 'rbPart3Item6cIsAnyOtherMeans'))}")
    parts.append(f"**6e. Are any other means offered that reduce the latency of communications between the ATS and its Subscribers?** {format_bool(get_text(p3, 'rbPart3Item6eIsAnyRducdSpOfCom'))}")
    
    parts.append(f"\n**7a. Order Types and Attributes:**\n{get_text(p3, 'taPart3Item7AOrdrTypExplain')}")
    answer, details = get_rb_answer_and_details(p3, 'part3Item7bTnCDetails', 'taPart3Item7bTnCSumryDtls')
    parts.append(f"**7b. Are the order types, attributes, and instructions the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")
    
    parts.append(f"\n**8a. Does the ATS require a minimum or maximum order size?** {format_bool(get_text(p3, 'rbPart3Item8aIsMinOrMaxSizeReqd'))}")

    answer, details = get_rb_answer_and_details(p3, 'part3Item8cOddltOrdrReqs', 'taPart3Item8cOddLtOrdrReqsnProcdurs')
    parts.append(f"**8c. Are odd-lot orders accepted and executed?** {answer}")
    if details: parts.append(f"   - **Procedures:** {details}")
    parts.append(f"**8d. Are odd-lot procedures the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item8dIsReqsProcdurSameForAll'))}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item8eMixltOrdrDetails', 'taPart3Item8eMixltOrdrReqsProcDtls')
    parts.append(f"**8e. Are mixed-lot orders accepted and executed?** {answer}")
    if details: parts.append(f"   - **Procedures:** {details}")
    parts.append(f"**8f. Are mixed-lot procedures the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item8fIsRecProcSameForAll'))}")
    
    parts.append(f"\n**9a. Does the ATS send any messages to indicate trading interest?** {format_bool(get_text(p3, 'rbPart3Item9aIsAnyMsgToIndicTI'))}")

    parts.append(f"\n**10a. Opening/Re-opening/Closing Procedures:**\n{get_text(p3, 'taPart3Item10aOpenReOpenDtls')}")
    parts.append(f"**10b. Are these procedures the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item10bIsOpnReopnSameForAll'))}")
    parts.append(f"**10c. Unexecuted Orders Procedures:**\n{get_text(p3, 'taPart3Item10cUnexeOrdrTIDtls')}")
    parts.append(f"**10d. Is there any difference in execution procedures during trading hours?** {format_bool(get_text(p3, 'rbPart3Item10dIsAnyDifBtwnExeProcTrdHrs'))}")
    parts.append(f"**10e. Is there any difference in pre-opening or execution procedures following a stoppage?** {format_bool(get_text(p3, 'rbPart3Item10eIsAnyDifBtwnPreOpExecFlwngStpg'))}")
    
    parts.append(f"\n**11a. Structure of the NMS Stock ATS:**\n{get_text(p3, 'taPart3Item11aStrucOfNmsStk')}")
    answer, details = get_rb_answer_and_details(p3, 'part3Item11bMeansFeciltsDtls', 'taPart3Item11bMeansFeciltsDtls')
    parts.append(f"**11b. Are the means that facilitate access the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")

    parts.append(f"**11c. Rules and procedures of the NMS Stock ATS:**\n{get_text(p3, 'taPart3Item11cRulsProcsOfNmsStk')}")
    parts.append(f"**11d. Are these rules and procedures the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item11dIsProcsRulsSameForAll'))}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item12aArngmntDtls', 'taPart3Item12aArngmntTCDtls')
    parts.append(f"\n**12a. Are there any arrangements to provide liquidity?** {answer}")
    if details: parts.append(f"   - **Details:** {details}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item13aSegmntDtls', 'taPart3Item13aSegProcdurDtls')
    parts.append(f"\n**13a. Is order or trading interest segmented?** {answer}")
    if details: parts.append(f"   - **Procedures:** {details}")

    parts.append(f"**13b. Is the segmentation the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item13bIsSegmntatnSameForAll'))}")
    parts.append(f"**13c. Does segmentation depend on whether the order is from a customer?** {format_bool(get_text(p3, 'rbPart3Item13cIsCustmrOrdr'))}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item13dDsclrContntDtls', 'taPart3Item13dDsclosrContntDtls')
    parts.append(f"**13d. Are segmentation categories disclosed to Subscribers?** {answer}")
    if details: parts.append(f"   - **Content:** {details}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item13eArngmntDtls', 'taPart3Item13eDsclosrDiffDtls')
    parts.append(f"**13e. Is the disclosure the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")
    
    answer, details = get_rb_answer_and_details(p3, 'part3Item14aCntrPrtySelectnDtls', 'taPart3Item14aCntrPrtyDtls')
    parts.append(f"\n**14a. Is a Subscriber designated to interact with specific trading interest?** {answer}")
    if details: parts.append(f"   - **Details:** {details}")

    answer, details = get_rb_answer_and_details(p3, 'part3Item14bSelectDtls', 'taPart3Item14bSelectnDiffDtls')
    parts.append(f"**14b. Is the counter-party selection the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")
    
    parts.append(f"\n**15a. Does the ATS use electronic communications to display order and trading interest?** {format_bool(get_text(p3, 'rbPart3Item15aIsElectrncCommu'))}")

    answer, details = get_rb_answer_and_details(p3, 'part3Item15bSubSctbDtls', 'taPart3Item15bSubscrBndDtls')
    parts.append(f"**15b. Is order and trading interest displayed to anyone other than Subscribers?** {answer}")
    if details: parts.append(f"   - **Details:** {details}")

    answer, details = get_rb_answer_and_details(p3, 'part3Item15cDsplyProcDtls', 'taPart3Item5cDsplyProcDiffDtls')
    parts.append(f"**15c. Are the display procedures the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")

    parts.append(f"\n**16a. Are orders or other messages routed out of the ATS?** {format_bool(get_text(p3, 'rbPart3Item16aIsInstRoutd'))}")
    
    parts.append(f"\n**17a. Is there any difference between the treatment of order and trading interest based on source?** {format_bool(get_text(p3, 'rbPart3Item17aIsDiffBtwnOrdTITrtmnt'))}")
    parts.append(f"**17b. Is the treatment the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item17bIsTrtmntSameForAll'))}")
    
    parts.append(f"\n**18a. Does the ATS execute trades outside of its regular trading hours?** {format_bool(get_text(p3, 'rbPart3Item18aIsOutsdeTrdingHrs'))}")
    
    parts.append(f"\n**19a. Fees:**\n{get_text(p3, 'taPart3Item19aSrvcUsgFees')}")
    parts.append(f"**19b. Bundled Services/Fees:**\n{get_text(p3, 'taPart3Item19bBundldSrvcUsgFees')}")
    parts.append(f"**19c. Rebates and Discounts:**\n{get_text(p3, 'taPart3Item19cRbtDiscOfFees')}")

    parts.append(f"\n**20a. Suspension of Trading Procedures:**\n{get_text(p3, 'taPart3Item20aSuspndProcdur')}")
    parts.append(f"**20b. Are these procedures the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item20bIsSuspndProcdurSameFrAll'))}")
    
    parts.append(f"\n**21a. Trade Reporting Arrangements:**\n{get_text(p3, 'taPart3Item21aMtrlArngmntDtls')}")
    parts.append(f"**21b. Are these arrangements the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item21bIsMtrlArngmtSameFrAll'))}")
    
    parts.append(f"\n**22a. Clearance and Settlement Arrangements:**\n{get_text(p3, 'taPart3Item22aMtrlArngmntDtls')}")
    answer, details = get_rb_answer_and_details(p3, 'part3Item22bMtrlArngmntDiffDtls', 'taPart3Item22bDiffDtls')
    parts.append(f"**22b. Are these arrangements the same for all Subscribers?** {answer}")
    if details: parts.append(f"   - **Differences:** {details}")
    
    parts.append(f"\n**23a. Market Data Sources:**\n{get_text(p3, 'taPart3Item23aMrktDatSrc')}")
    parts.append(f"**23b. Are these sources the same for all Subscribers?** {format_bool(get_text(p3, 'rbPart3Item23bIsSrcSameFrAll'))}")
    
    parts.append(f"\n**24a. Does the ATS aggregate Subscriber order and trading interest with that of other trading centers?** {format_bool(get_text(p3, 'rbPart3Item24aIsSubScrbrOrdr'))}")
    parts.append(f"\n**25a. Did the ATS exceed the volume thresholds of Regulation ATS?** {format_bool(get_text(p3, 'rbPart3Item25aIsAvgDlyTradinVolExcd'))}")
    parts.append(f"\n**26. Are order flow and execution statistics published?** {format_bool(get_text(p3, 'rbPart3Item26IsOrdrFloExecStatsPublshd'))}")


    return "\n\n".join(parts)

def parse_form_n_mfp3_xml(xml: BeautifulSoup, class_name_map: dict = None) -> str:
    """
    Parses an XML-based Form N-MFP3 (Monthly Schedule of Portfolio Holdings
    of Money Market Funds, 2024 schema) into a comprehensive Markdown document.
    """
    def get_text(node, tag, strip_ns=True):
        if not node: return "—"
        found = node.find(re.compile(f'^(?:\\w+:)?{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_val(value_str: str, type_hint: str = 'string') -> str:
        if not value_str or value_str.lower() in ('—', 'n/a', 'na'): return "—"
        try:
            val_float = float(value_str.replace(',', ''))
            if type_hint == 'dollar': return f"${val_float:.2f}"
            if type_hint == 'percent': return f"{val_float * 100:.4f}%"
            if type_hint == 'shares': return f"{val_float:.4f}"
            if type_hint == 'yield': return f"{val_float * 100:.4f}%"
            if type_hint == 'number': return f"{val_float:.4f}"
        except (ValueError, TypeError):
            pass
        if value_str.upper() == 'Y': return "Yes"
        if value_str.upper() == 'N': return "No"
        return value_str

    parts = ["# Form N-MFP3: Monthly Schedule of Portfolio Holdings"]
    
    filer_info_section = []
    header_data = xml.find('headerData')
    if header_data:
        submission_type = get_text(header_data, 'submissionType')
        filer_info_section.append(f"**Submission Type:** {submission_type}")
        filer_creds = header_data.find('filerCredentials')
        if filer_creds:
            filer_info_section.append(f"**CIK:** {get_text(filer_creds, 'cik')}")
    parts.append("## N-MFP: Filer Information\n" + "\n".join(filer_info_section))

    form_data = xml.find('formData')
    gen_info = form_data.find('generalInfo')
    
    filing_info_section = ["### General Information"]
    gen_data = {
        "Report for (YYYY-MM-DD)": get_text(gen_info, 'reportDate'),
        "Registrant Full Name": get_text(gen_info, 'registrantFullName'),
        "CIK Number of Registrant": get_text(gen_info, 'cik'),
        "LEI of Registrant": get_text(gen_info, 'registrantLEIId'),
        "Name of Series": get_text(gen_info, 'nameOfSeries'),
        "LEI of Series": get_text(gen_info, 'leiOfSeries'),
        "EDGAR Series Identifier": get_text(gen_info, 'seriesId'),
        "Total number of share classes in the series": get_text(gen_info, 'totalShareClassesInSeries'),
        "Is this the fund's final filing on Form N-MFP?": format_val(get_text(gen_info, 'finalFilingFlag')),
        "Has the fund acquired or merged with another fund?": format_val(get_text(gen_info, 'fundAcqrdOrMrgdWthAnthrFlag')),
    }
    for key, val in gen_data.items():
        filing_info_section.append(f"**{key}:** {val}")
    parts.append("\n\n".join(filing_info_section))

    series_info = form_data.find('seriesLevelInfo')
    parts.append("\n## Part A: Series-Level Information about the Fund")

    service_providers = []
    if (adviser_node := series_info.find("adviser")):
        service_providers.append({"Role": "Investment Adviser", "Details": get_text(adviser_node, 'adviserName'), "File/CIK Number": get_text(adviser_node, 'adviserFileNumber')})
    if (accountant_node := series_info.find('indpPubAccountant')):
        acc_details = f"{get_text(accountant_node, 'name')}<br>City: {get_text(accountant_node, 'city')}<br>State: {get_text(accountant_node, 'stateCountry')}"
        service_providers.append({"Role": "Independent Public Accountant", "Details": acc_details, "File/CIK Number": "—"})
    if (admin_node := series_info.find('administrator')):
        service_providers.append({"Role": "Administrator", "Details": get_text(admin_node, 'administratorName'), "File/CIK Number": "—"})
    if (transfer_agent_node := series_info.find('transferAgent')):
        ta_details = f"{get_text(transfer_agent_node, 'name')}<br>CIK: {get_text(transfer_agent_node, 'cik')}"
        service_providers.append({"Role": "Transfer Agent", "Details": ta_details, "File/CIK Number": get_text(transfer_agent_node, 'fileNumber')})
    if service_providers:
        parts.append("\n### Service Providers\n" + to_compact_markdown(pd.DataFrame(service_providers), index=False))

    fund_chars = {
        "Securities Act File Number": get_text(series_info, 'securitiesActFileNumber'),
        "Is this a Feeder Fund?": format_val(get_text(series_info, 'feederFundFlag')),
        "Is this a Master Fund?": format_val(get_text(series_info, 'masterFundFlag')),
        "Is this series for insurance company separate accounts?": format_val(get_text(series_info, 'seriesFundInsuCmpnySepAccntFlag')),
        "Money Market Fund Category": get_text(series_info, 'moneyMarketFundCategory'),
        "Is this a Retail Money Market Fund?": format_val(get_text(series_info, 'fundRetailMoneyMarketFlag')),
        "Is this a Government Money Market Fund?": format_val(get_text(series_info, 'govMoneyMrktFundFlag')),
        "WAM": f"{get_text(series_info, 'averagePortfolioMaturity')} days",
        "WAL": f"{get_text(series_info, 'averageLifeMaturity')} days",
        "Total Value of Portfolio Securities": format_val(get_text(series_info, 'totalValuePortfolioSecurities'), 'dollar'),
        "Amortized Cost of Portfolio Securities": format_val(get_text(series_info, 'amortizedCostPortfolioSecurities'), 'dollar'),
        "Cash": format_val(get_text(series_info, 'cash'), 'dollar'),
        "Total Other Assets": format_val(get_text(series_info, 'totalValueOtherAssets'), 'dollar'),
        "Total Liabilities": format_val(get_text(series_info, 'totalValueLiabilities'), 'dollar'),
        "Net Assets of Series": format_val(get_text(series_info, 'netAssetOfSeries'), 'dollar'),
        "Number of Shares Outstanding (Series)": format_val(get_text(series_info, 'numberOfSharesOutstanding'), 'number'),
        "Does the fund seek to maintain a stable price per share?": format_val(get_text(series_info, 'seekStablePricePerShare')),
        "Stable Price Per Share": format_val(get_text(series_info, 'stablePricePerShare'), 'dollar'),
        "Is cash management vehicle an affiliated fund?": format_val(get_text(series_info, 'cashMgmtVehicleAffliatedFundFlag')),
        "Does the fund apply liquidity fees?": format_val(get_text(series_info, 'liquidityFeeFundApplyFlag')),
    }
    parts.append("\n### Fund Characteristics & Assets")
    for key, val in fund_chars.items():
        if val not in ("—", " days"): parts.append(f"- **{key}:** {val}")

    liquid_asset_details = series_info.find_all('liquidAssetsDetails')
    if liquid_asset_details:
        liquid_data = [{
            "Date": get_text(d, 'totalLiquidAssetsNearPercentDate'),
            "Daily Liquid Assets ($)": format_val(get_text(d, 'totalValueDailyLiquidAssets'), 'dollar'),
            "Weekly Liquid Assets ($)": format_val(get_text(d, 'totalValueWeeklyLiquidAssets'), 'dollar'),
            "Daily Liquid Assets (%)": format_val(get_text(d, 'percentageDailyLiquidAssets'), 'percent'),
            "Weekly Liquid Assets (%)": format_val(get_text(d, 'percentageWeeklyLiquidAssets'), 'percent'),
        } for d in liquid_asset_details]
        parts.append("\n### Daily & Weekly Liquid Assets\n" + to_compact_markdown(pd.DataFrame(liquid_data), index=False))

    seven_day_yields = series_info.find_all('sevenDayGrossYield')
    if seven_day_yields:
        yield_data = [{
            "Date": get_text(y, 'sevenDayGrossYieldDate'),
            "7-Day Gross Yield": format_val(get_text(y, 'sevenDayGrossYieldValue'), 'yield'),
        } for y in seven_day_yields]
        parts.append("\n### 7-Day Gross Yield\n" + to_compact_markdown(pd.DataFrame(yield_data), index=False))

    daily_navs = series_info.find_all('dailyNetAssetValuePerShareSeries')
    if daily_navs:
        nav_data = [{
            "Date": get_text(n, 'dailyNetAssetValuePerShareDateSeries'),
            "Net Asset Value per Share": format_val(get_text(n, 'dailyNetAssetValuePerShareSeries'), 'shares'),
        } for n in daily_navs]
        parts.append("\n### Daily Net Asset Value per Share (Series)\n" + to_compact_markdown(pd.DataFrame(nav_data), index=False))
    
    class_level_nodes = form_data.find_all('classLevelInfo')
    if class_level_nodes:
        parts.append("\n## Part B: Class-Level Information about the Fund")
        if class_name_map is None: class_name_map = {}

        for node in class_level_nodes:
            class_id = get_text(node, 'classesId')
            class_name = get_text(node, 'classFullName') or class_name_map.get(class_id, f"Unknown Class ({class_id})")
            parts.append(f"\n### Class: {class_name}")
            
            class_details = {
                "Minimum Initial Investment": format_val(get_text(node, 'minInitialInvestment'), 'dollar'),
                "Net Assets of Class": format_val(get_text(node, 'netAssetsOfClass'), 'dollar'),
                "Number of Shares Outstanding": format_val(get_text(node, 'numberOfSharesOutstanding'), 'number'),
                "Expense Reimbursement/Waiver": get_text(node, 'nameOfPersonDescExpensePay'),
            }
            for key, val in class_details.items():
                if val not in ("—", "0.00"): parts.append(f"- **{key}:** {val}")

            class_daily_navs = node.find_all('dailyNetAssetValuePerShareClass')
            if class_daily_navs:
                nav_data = [{"Date": get_text(n, 'dailyNetAssetValuePerShareDateClass'), "NAV per Share": format_val(get_text(n, 'dailyNetAssetValuePerShareClass'), 'shares')} for n in class_daily_navs]
                parts.append("\n**Daily Net Asset Value per Share (Class)**\n" + to_compact_markdown(pd.DataFrame(nav_data), index=False))

            daily_flows = node.find_all('dialyShareholderFlowReported')
            if daily_flows:
                flow_data = [{"Date": get_text(f, 'dailyShareHolderFlowDate'), "Gross Subscriptions ($)": format_val(get_text(f, 'dailyGrossSubscriptions'), 'dollar'), "Gross Redemptions ($)": format_val(get_text(f, 'dailyGrossRedemptions'), 'dollar')} for f in daily_flows]
                parts.append("\n**Daily Shareholder Flows**\n" + to_compact_markdown(pd.DataFrame(flow_data), index=False))

            monthly_flow = node.find('monthlyShareholderFlowReported')
            if monthly_flow:
                 parts.append(f"**Total Gross Subscriptions (Month):** {format_val(get_text(monthly_flow, 'totalGrossSubscriptions'), 'dollar')}")
                 parts.append(f"**Total Gross Redemptions (Month):** {format_val(get_text(monthly_flow, 'totalGrossRedemptions'), 'dollar')}")
            
            class_yields = node.find_all('sevenDayNetYield')
            if class_yields:
                yield_data = [{"Date": get_text(y, 'sevenDayNetYieldDate'), "7-Day Net Yield": format_val(get_text(y, 'sevenDayNetYieldValue'), 'yield')} for y in class_yields]
                parts.append("\n**7-Day Net Yield (Class)**\n" + to_compact_markdown(pd.DataFrame(yield_data), index=False))
            
            owner_cats = node.find_all('beneficialRecordOwnerCategory')
            if owner_cats:
                owner_data = []
                for cat in owner_cats:
                    owner_data.append({
                        "Category": get_text(cat, 'beneficialRecordOwnerCategoryType'),
                        "Other Category": get_text(cat, 'otherInvestorCategory'),
                        "Record Owner %": format_val(get_text(cat, 'percentOutstandingSharesRecord'), 'percent'),
                        "Beneficial Owner %": format_val(get_text(cat, 'percentOutstandingSharesBeneficial'), 'percent'),
                    })
                parts.append("\n**Beneficial/Record Owner Categories**\n" + to_compact_markdown(pd.DataFrame(owner_data),  index=False))

    securities_nodes = form_data.find_all('scheduleOfPortfolioSecuritiesInfo')
    if securities_nodes:
        parts.append("\n## Part C: Schedule of Portfolio Securities")
        for i, node in enumerate(securities_nodes):
            parts.append(f"\n### Security {i+1}: {get_text(node, 'nameOfIssuer')}")
            
            security_details = [
                f"**C.1 - Title:** {get_text(node, 'titleOfIssuer')}",
                f"**C.6 - Investment Category:** {get_text(node, 'investmentCategory')}",
            ]
            id_data = {"C.3 - CUSIP": get_text(node, 'CUSIPMember'), "C.4 - ISIN": get_text(node, 'ISINId'), "C.3 - LEI": get_text(node, 'LEIID'), "C.5 - Other ID": get_text(node, 'otherUniqueId')}
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
            
            ratings = [f"{get_text(n, 'nameOfNRSRO')}: {get_text(n, 'rating')}" for n in node.find_all('assigningNRSRORating')]
            if ratings: security_details.append(f"**C.10 - Ratings:** {'; '.join(ratings)}")

            flags = {
                "C.9 - Eligible Security?": format_val(get_text(node, 'securityEligibilityFlag')),
                "C.14 - Has Demand Feature?": format_val(get_text(node, 'securityDemandFeatureFlag')),
                "C.15 - Has Guarantee?": format_val(get_text(node, 'securityGuaranteeFlag')),
                "C.16 - Has Enhancement?": format_val(get_text(node, 'securityEnhancementsFlag')),
                "C.22 - Is an Illiquid Security?": format_val(get_text(node, 'illiquidSecurityFlag')),
                "C.20 - Is a Daily Liquid Asset?": format_val(get_text(node, 'dailyLiquidAssetSecurityFlag')),
                "C.21 - Is a Weekly Liquid Asset?": format_val(get_text(node, 'weeklyLiquidAssetSecurityFlag')),
                "C.23 - Categorized at Level 3?": format_val(get_text(node, 'securityCategorizedAtLevel3Flag')),
            }
            flag_str = ", ".join([f"{k.split('-')[0].strip()} {k.split('-')[1].strip()} {v}" for k, v in flags.items() if v != "—"])
            if flag_str: security_details.append(f"**Characteristics:** {flag_str}")
            parts.append("\n".join(f"- {item}" for item in security_details))

            repo_node = node.find('repurchaseAgreement')
            if repo_node and repo_node.get_text(strip=True):
                parts.append("\n**C.8 - Repurchase Agreement Details:**")
                repo_details = {
                    "Is Open?": format_val(get_text(repo_node, 'repurchaseAgreementOpenFlag')),
                    "Is Cleared?": format_val(get_text(repo_node, 'repurchaseAgreementClearedFlag')),
                    "Name of CCP": get_text(repo_node, 'nameOfCCP'),
                    "Is Tri-party?": format_val(get_text(repo_node, 'repurchaseAgreementTripartyFlag')),
                }
                for key, val in repo_details.items():
                    if val != "—": parts.append(f"- **{key}:** {val}")

                collateral_issuers = repo_node.find_all('collateralIssuers')
                if collateral_issuers:
                    collateral_data = []
                    for issuer in collateral_issuers:
                        coupon_str = get_text(issuer, 'coupon')
                        yield_str = get_text(issuer, 'yield')
                        
                        try:
                            coupon_formatted = f"{float(coupon_str):.4f}%" if coupon_str != "—" else "—"
                        except (ValueError, TypeError):
                            coupon_formatted = coupon_str
                            
                        try:
                            yield_formatted = f"{float(yield_str):.4f}%" if yield_str != "—" else "—"
                        except (ValueError, TypeError):
                            yield_formatted = yield_str
                        
                        collateral_data.append({
                            "Issuer Name": get_text(issuer, 'nameOfCollateralIssuer'),
                            "Maturity Date": get_text(issuer.find('maturityDate'), 'date'),
                            "Coupon": coupon_formatted,
                            "Yield": yield_formatted,
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

def parse_form_sbsef_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Form SBSEF or SBSEF/A into a structured Markdown document.
    """
    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(f'^{tag}$', re.I))
        return found.text.strip() if found and found.text else "—"

    def format_address(addr_node) -> str:
        if not addr_node: return "—"
        parts = [
            get_text(addr_node, 'street1'),
            get_text(addr_node, 'street2'),
            get_text(addr_node, 'city'),
            get_text(addr_node, 'stateOrCountry'),
            get_text(addr_node, 'zipCode'),
        ]
        return ", ".join(p for p in parts if p and p != "—")

    submission = xml.find('edgarSubmission')
    if not submission:
        return "<!-- <edgarSubmission> tag not found in SBSEF XML -->"

    header = submission.find('headerData')
    form_data = submission.find('formData')
    principal_info = form_data.find('principalInfo') if form_data else None
    
    submission_type = get_text(header, 'submissionType')
    title = "FORM SBSEF/A: Amendment to Registration as a Security-Based Swap Execution Facility"
    if submission_type == "SBSEF":
        title = "FORM SBSEF: Registration as a Security-Based Swap Execution Facility"

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        f"## {title}\n"
    ]

    filer_creds = header.find('filerCredentials') if header else None
    parts.append("### Filer Information")
    parts.append(f"**Submission Type:** {submission_type}")
    parts.append(f"**CIK:** {get_text(filer_creds, 'cik')}")

    if principal_info:
        parts.append("\n### Principal Information")
        details = {
            "Full Name of Applicant": get_text(principal_info, 'applicantName'),
            "Principal Place of Business Address": format_address(principal_info)
        }
        for key, val in details.items():
            if val and val != "—":
                parts.append(f"**{key}:** {val}")

        amended_items_str = get_text(principal_info, 'amendedItemsList')
        if amended_items_str and amended_items_str != "—":
            parts.append("\n**Amended Items in this Filing:**")
            items = [item.strip() for item in amended_items_str.split(',')]
            for item in items:
                if item:
                    parts.append(f"- {item}")

    return "\n\n".join(parts)

def parse_any_xml(xml_contents, pdf_docs=None, class_name_map=None) -> str:
    """
    Route to the correct form-specific parser based on the form's unique XML structure.
    Accepts a list of XML content strings.
    """
    if not xml_contents and not pdf_docs:
        return ""

    all_parts = []

    if pdf_docs:
        pdf_md = parse_pdf_attachments(pdf_docs)
        if pdf_md:
            all_parts.append(pdf_md)

    for xml_content in xml_contents:
        if not xml_content.strip():
            continue

        xml_content = re.sub(r'\n(?=[a-z</])', '', xml_content)

        xml_content = re.sub(r'&(?![a-zA-Z0-9#]{2,};)', '&amp;', xml_content)

        soup = BeautifulSoup(xml_content, "lxml-xml")
        parsed_part = ""

        if soup.find(re.compile(r'^ownershipDocument$', re.I)):
            doc_type_tag = soup.find(re.compile(r'^documentType$', re.I))
            doc_type = doc_type_tag.text.strip() if doc_type_tag else ''
            if doc_type == '3':
                parsed_part = parse_form3_xml(soup)
            else:
                parsed_part = parse_form4_xml(soup, doc_type or "4")
        
        elif edgar_submission := soup.find(re.compile(r'(?:\w+:)?edgarSubmission$', re.I)):
            form_type_tag = edgar_submission.find(re.compile(r'(?:\w+:)?(submissionType|formtype)$', re.I))
            form_type = form_type_tag.text.strip().upper() if form_type_tag else ""


            if form_type.startswith("SBSEF"):
                parsed_part = parse_form_sbsef_xml(soup)
            elif form_type.startswith("ATS-N"):
                parsed_part = parse_form_atsn_xml(soup)
            elif form_type.startswith("SBSE-A"):
                parsed_part = parse_sbse_a_xml(soup)
            elif form_type.startswith("X-17A-5"):
                parsed_part = parse_form_x17a5_xml(soup)
            elif form_type.startswith("24F-2NT"):
                parsed_part = parse_form_24f2nt_xml(soup)
            elif form_type.startswith("CFPORTAL"):
                parsed_part = parse_form_cfportal_xml(soup)
            elif form_type.startswith("TA-1"):
                parsed_part = parse_form_ta1_xml(soup)
            elif form_type.startswith("TA-W"):
                parsed_part = parse_form_taw_xml(soup)
            elif form_type.startswith("TA-2"):
                parsed_part = parse_form_ta2_xml(soup)
            elif form_type.startswith("MA-I"):
                parsed_part = parse_form_mai_xml(soup)
            elif form_type.startswith("MA-W"):
                parsed_part = parse_form_maw_xml(soup)
            elif form_type.startswith("MA"):
                parsed_part = parse_form_ma_xml(soup)
            elif form_type.startswith("1-A") or form_type.startswith("DOS"):
                parsed_part = parse_form1a_xml(soup)
            elif form_type.startswith("1-K"):
                parsed_part = parse_form1k_xml(soup)
            elif form_type.startswith("1-Z"):
                parsed_part = parse_form1z_xml(soup)
            elif form_type.startswith("SCHEDULE 13G"):
                parsed_part = parse_schedule13g_xml(soup)
            elif form_type.startswith("SCHEDULE 13D"):
                parsed_part = parse_schedule13d_xml(soup)
            elif form_type.startswith("C"):
                parsed_part = parse_form_c_xml(soup)
            elif form_type == "D" or form_type == "D/A":
                parsed_part = parse_form_d_xml(soup)
            elif form_type in ("EFFECT", "QUALIF"):
                parsed_part = parse_effect_xml(soup)
            elif form_type.startswith("13F-"):
                parsed_part = parse_form13f_hr_xml(xml_contents)
                if parsed_part: all_parts.append(parsed_part)
                break
            elif form_type == "N-PX":
                parsed_part = parse_form_npx_xml(xml_contents)
                if parsed_part: all_parts.append(parsed_part)
                break
            elif form_type.startswith("N-MFP"):
                if form_type == "N-MFP3":
                    parsed_part = parse_form_n_mfp3_xml(soup, class_name_map=class_name_map)
                elif soup.find(re.compile(r'(?:\w+:)?seriesLevelInfo$', re.I)):
                    full_soup = BeautifulSoup(xml_content, "lxml-xml")
                    parsed_part = parse_legacy_n_mfp_xml(full_soup, class_name_map=class_name_map)
                elif soup.find('formData'):
                    parsed_part = parse_form_n_mfp2_xml(soup, class_name_map=class_name_map)
                else:
                    full_soup = BeautifulSoup(xml_content, "lxml-xml")
                    parsed_part = parse_legacy_n_mfp_xml(full_soup, class_name_map=class_name_map)
                    
            elif form_type.startswith("NPORT-P"):
                parsed_part = parse_nport_p_xml(soup)
            elif form_type.startswith("144"):
                parsed_part = parse_form144_xml(soup, form_type)
            elif form_type.startswith("N-CEN"):
                parsed_part = parse_form_n_cen_xml(soup)

        elif soup.find(re.compile(r'^notificationOfRemoval$', re.I)):
            parsed_part = parse_form25_xml(soup)
        elif asset_data_tag := soup.find(re.compile(r'^(?:ns\d+:)?assetData$', re.I)):
            if asset_data_tag.find(re.compile(r'^(?:ns\d+:)?assets$', re.I)):
                parsed_part = parse_abs_ee_xml(soup)
            else:
                parsed_part = parse_abs_ee_comments_xml(soup)

        elif soup.find(re.compile(r'^comments$', re.I)):
            parsed_part = parse_abs_ee_comments_xml(soup)

        if parsed_part:
            all_parts.append(parsed_part)
    
    return "\n\n".join(all_parts)

def parse_effect_xml(xml: BeautifulSoup) -> str:
    """
    Parses an XML-based Notice of Effectiveness (EFFECT) or Qualification (QUALIF)
    into a structured Markdown document, handling variations in the XML schema.
    """
    def get_text(node, tag):
        if not node or not (found := node.find(re.compile(f'^{tag}$', re.I))): return "—"
        return found.text.strip()

    parts = [
        "### UNITED STATES\n"
        "### SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
    ]

    submission_type_overall = get_text(xml.find('edgarSubmission'), 'submissionType')

    if submission_type_overall == 'QUALIF':
        parts.append("## Notice of Qualification")
    else:
        parts.append("## Notice of Effectiveness")

    effective_data = xml.find(re.compile(r'^effectiveData$', re.I))
    if not effective_data:
        return "<!-- <effectiveData> tag not found in filing -->"

    date_str = get_text(effective_data, 'finalEffectivenessDispDate')
    time_str = get_text(effective_data, 'finalEffectivenessDispTime')
    eff_date = "—"
    if date_str != "—":
        try:
            dt = datetime.datetime.strptime(date_str, '%Y-%m-%d')
            eff_date = f"{dt.strftime('%B')} {dt.day}, {dt.strftime('%Y')}"
        except ValueError:
            eff_date = date_str

    details = [f"**Date:** {eff_date}"]
    if time_str != "—":
        details.append(f"**Time:** {time_str}")

    accession_num = get_text(effective_data, 'accessionNumber')
    if accession_num != "—":
        details.append(f"**Accession Number:** {accession_num}")

    form_type = get_text(effective_data, 'submissionType')
    if form_type == "—":
        form_type = get_text(effective_data, 'form')
    
    if form_type != "—":
        details.append(f"**Form Type:** {form_type}")

    parts.append("\n\n".join(details))

    for filer_node in effective_data.find_all(re.compile(r'^filer$', re.I)):
        filer_details = [
            "\n---",
            f"**CIK:** {get_text(filer_node, 'cik')}",
            f"**Company Name:** {get_text(filer_node, 'entityName')}",
            f"**File Number:** {get_text(filer_node, 'fileNumber')}"
        ]
        parts.extend(filer_details)

    return "\n\n".join(parts)

def parse_form13f_hr_xml(xml_contents: list) -> str:
    """
    Parses a 13F-HR, 13F-NT, or 13F-HR/A filing from its XML components into a
    comprehensive Markdown document. This version formats the main table with
    fully merged headers for a professional appearance.
    """
    if not xml_contents:
        return "<!-- No XML content found for 13F filing -->"

    try:
        soups = [BeautifulSoup(xml, "lxml-xml") for xml in xml_contents]
        submission_soup = next((s for s in soups if s.find(re.compile(r'(?:\w+:)?edgarSubmission$', re.I))), None)
        infotable_soup = next((s for s in soups if s.find(re.compile(r'(?:\w+:)?informationTable$', re.I))), None)
    except Exception as e:
        return f"<!-- Failed to parse one or more XML documents for 13F. Error: {e} -->"

    if not submission_soup:
        return "<!-- Could not identify 13F submission XML -->"

    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'(?:\w+:)?{tag}$', re.I))
        text = found.text.strip() if found and found.text and found.text.strip() else "—"
        return html.unescape(text).replace(',', '')

    parts = [
        "### FORM 13F COVER PAGE\n"
    ]
    cover_page = submission_soup.find(re.compile(r'(?:\w+:)?coverPage$', re.I))
    manager = cover_page.find(re.compile(r'(?:\w+:)?filingManager$', re.I)) if cover_page else None
    manager_addr = manager.find(re.compile(r'(?:\w+:)?address$', re.I)) if manager else None
    parts.append(f"**Report for the Calendar Year or Quarter Ended:** {get_text(cover_page, 'reportCalendarOrQuarter')}")
    is_amendment = get_text(cover_page, 'isAmendment').lower() == 'true'
    am_info = cover_page.find(re.compile(r'(?:\w+:)?amendmentInfo$', re.I)) if is_amendment else None
    am_type = get_text(am_info, 'amendmentType') if am_info else ''
    
    amendment_no_text = get_text(cover_page, 'amendmentNo') if is_amendment else ''
    parts.append(f"**Check here if Amendment** [{'x' if is_amendment else ' '}] **Amendment Number:** {amendment_no_text}")

    parts.append(f"**This Amendment (Check only one.):** [{'x' if 'RESTATEMENT' in am_type.upper() else ' '}] is a restatement.")
    parts.append(f"[{'x' if 'NEW HOLDINGS' in am_type.upper() else ' '}] adds new holdings entries.")
    parts.append("\n**Institutional Investment Manager Filing this Report:**\n")
    filer_name = get_text(manager, 'name')
    
    addr_parts = {
        'street1': get_text(manager_addr, 'street1'),
        'street2': get_text(manager_addr, 'street2'),
        'city': get_text(manager_addr, 'city'),
        'state': get_text(manager_addr, 'stateOrCountry'),
        'zip': get_text(manager_addr, 'zipCode')
    }
    
    address_lines = []
    if addr_parts['street1'] != "—":
        address_lines.append(addr_parts['street1'])
    if addr_parts['street2'] != "—":
        address_lines.append(addr_parts['street2'])
    
    city_state_zip = f"{addr_parts['city']}, {addr_parts['state']} {addr_parts['zip']}"
    if city_state_zip.replace(",", "").replace(" ", "") != "—":
         address_lines.append(city_state_zip)

    filer_addr = "<br>".join(address_lines)

    parts.append(f"**Name:** {filer_name}<br>**Address:** {filer_addr}")
    parts.append(f"\n**Form 13F File Number:** {get_text(cover_page, 'form13FFileNumber')}")
    
    crd_num = get_text(cover_page, 'crdNumber')
    sec_num = get_text(cover_page, 'secFileNumber')
    if crd_num != "—":
        parts.append(f"**CRD Number (if applicable):** {crd_num}")
    if sec_num != "—":
        parts.append(f"**SEC File Number (if applicable):** {sec_num}\n")

    parts.append("The institutional investment manager filing this report and the person by whom it is signed hereby represent that the person signing the report is authorized to submit it, that all information contained herein is true, correct and complete, and that it is understood that all required items, statements, schedules, lists, and tables, are considered integral parts of this form.\n")
    sig_block = submission_soup.find(re.compile(r'(?:\w+:)?signatureBlock$', re.I))
    parts.append("**Person Signing this Report on Behalf of Reporting Manager:**\n")
    parts.append(f"**Name:** {get_text(sig_block, 'name')}<br>**Title:** {get_text(sig_block, 'title')}<br>**Phone:** {get_text(sig_block, 'phone')}")
    parts.append("\n**Signature, Place, and Date of Signing:**\n")
    signature_line = f"{get_text(sig_block, 'signature')}  {get_text(sig_block, 'city')}, {get_text(sig_block, 'stateOrCountry')}  {get_text(sig_block, 'signatureDate')}"
    placeholders_line = "[Signature]  [City, State]  [Date]"
    parts.append(f"{signature_line}<br>{placeholders_line}\n")
    report_type_str = get_text(cover_page, 'reportType').upper()
    parts.append("**Report Type (Check only one.):**")
    parts.append(f"[{'x' if 'HOLDINGS' in report_type_str else ' '}] **13F HOLDINGS REPORT.** (Check here if all holdings of this reporting manager are reported in this report.)")
    parts.append(f"[{'x' if 'NOTICE' in report_type_str else ' '}] **13F NOTICE.** (Check here if no holdings are reported in this report, and all holdings are reported by other reporting manager(s).)")
    parts.append(f"[{'x' if 'COMBINATION' in report_type_str else ' '}] **13F COMBINATION REPORT.** (Check here if a portion of the holdings for this reporting manager are reported in this report and a portion are reported by other reporting manager(s).)\n")
    
    parts.append("### Form 13F Summary Page\n")
    parts.append("**Report Summary:**\n")
    
    summary = submission_soup.find(re.compile(r'(?:\w+:)?summaryPage$', re.I))
    other_managers_info = cover_page.find(re.compile(r'(?:\w+:)?otherManagersInfo$', re.I)) if cover_page else None
    notice_managers = other_managers_info.find_all(re.compile(r'(?:\w+:)?otherManager$', re.I)) if other_managers_info else []

    if 'NOTICE' in report_type_str:
        parts.append(f"**Number of Other Included Managers:** 0")
        parts.append(f"**Form 13F Information Table Entry Total:** 0")
        parts.append(f"**Form 13F Information Table Value Total:** $0")
    else: 
        value_total_raw = get_text(summary, 'tableValueTotal')
        value_total_formatted = f"{int(value_total_raw):}" if value_total_raw.isdigit() else '—'
        parts.append(f"**Number of Other Included Managers:** {get_text(summary, 'otherIncludedManagersCount')}")
        parts.append(f"**Form 13F Information Table Entry Total:** {get_text(summary, 'tableEntryTotal')}")
        parts.append(f"**Form 13F Information Table Value Total:** ${value_total_formatted}")

    parts.append("      (round to nearest dollar)\n")
    parts.append("**List of Other Included Managers:**")
    parts.append("Provide a numbered list of the name(s) and Form 13F file number(s) of all institutional investment managers with respect to which this report is filed, other than the manager filing this report.")
    parts.append("[If there are no entries in this list, state “NONE” and omit the column headings and list entries.]")
    
    if notice_managers:
        manager_data = []
        for i, manager in enumerate(notice_managers, 1):
            manager_data.append({
                'No.': str(i),
                'Name': get_text(manager, 'name'),
                'Form 13F File Number': get_text(manager, 'form13FFileNumber'),
                'CRD Number': get_text(manager, 'crdNumber'),
                'SEC File Number': get_text(manager, 'secFileNumber'),
            })
        
        column_order = ['No.', 'Name', 'Form 13F File Number', 'CRD Number', 'SEC File Number']
        manager_df = pd.DataFrame(manager_data).reindex(columns=column_order).fillna("—")
        parts.append(to_compact_markdown(manager_df, index=False))
    else:
        summary_managers = summary.find_all(re.compile(r'(?:\w+:)?manager$', re.I)) if summary else []
        if summary_managers:
            manager_data = []
            for manager in summary_managers:
                 manager_data.append({
                    'No.': get_text(manager, 'managerSequenceNumber'),
                    'Name': get_text(manager, 'name'),
                    'Form 13F File Number': get_text(manager, 'form13FFileNumber'),
                 })
            column_order = ['No.', 'Name', 'Form 13F File Number']
            manager_df = pd.DataFrame(manager_data).reindex(columns=column_order).fillna("—")
            parts.append(to_compact_markdown(manager_df, index=False))
        else:
            parts.append("**NONE**")
            
    final_columns = [
        'NAME OF ISSUER##ROWSPAN_1##<br>NAME OF ISSUER##ROWSPAN_1##',
        'TITLE OF CLASS##ROWSPAN_2##<br>TITLE OF CLASS##ROWSPAN_2##',
        'CUSIP##ROWSPAN_3##<br>CUSIP##ROWSPAN_3##',
        'FIGI##ROWSPAN_4##<br>FIGI##ROWSPAN_4##',
        'VALUE (x$1000)##ROWSPAN_5##<br>VALUE (x$1000)##ROWSPAN_5##',
        'SHRS OR PRN AMT##ROWSPAN_6##<br>SHRS OR PRN AMT##ROWSPAN_6##',
        'SH/PRN##ROWSPAN_7##<br>SH/PRN##ROWSPAN_7##',
        'PUT/CALL##ROWSPAN_8##<br>PUT/CALL##ROWSPAN_8##',
        'INVESTMENT DISCRETION##ROWSPAN_9##<br>INVESTMENT DISCRETION##ROWSPAN_9##',
        'OTHER MANAGER##ROWSPAN_10##<br>OTHER MANAGER##ROWSPAN_10##',
        'VOTING AUTHORITY##COLSPAN_1##<br>SOLE',
        'VOTING AUTHORITY##COLSPAN_1##<br>SHARED',
        'VOTING AUTHORITY##COLSPAN_1##<br>NONE'
    ]

    if infotable_soup:
        rows = []
        for item in infotable_soup.find_all(re.compile(r'(?:\w+:)?infoTable$', re.I)):
            shrs_prn = item.find(re.compile(r'(?:\w+:)?shrsOrPrnAmt$', re.I))
            voting = item.find(re.compile(r'(?:\w+:)?votingAuthority$', re.I))
            
            def format_numeric(val_str):
                if val_str.isdigit():
                    num = int(val_str)
                    if num > 0:
                        return f"{num:}"
                    return "0"
                return '—'

            value_in_thousands = get_text(item, 'value')
            shares_amt = get_text(shrs_prn, 'sshPrnamt')
            vote_sole = get_text(voting, 'Sole')
            vote_shared = get_text(voting, 'Shared')
            vote_none = get_text(voting, 'None')

            row_data = {
                'NAME OF ISSUER##ROWSPAN_1##<br>NAME OF ISSUER##ROWSPAN_1##': get_text(item, 'nameOfIssuer'),
                'TITLE OF CLASS##ROWSPAN_2##<br>TITLE OF CLASS##ROWSPAN_2##': get_text(item, 'titleOfClass'),
                'CUSIP##ROWSPAN_3##<br>CUSIP##ROWSPAN_3##': get_text(item, 'cusip'),
                'FIGI##ROWSPAN_4##<br>FIGI##ROWSPAN_4##': get_text(item, 'figi'),
                'VALUE (x$1000)##ROWSPAN_5##<br>VALUE (x$1000)##ROWSPAN_5##': format_numeric(value_in_thousands),
                'SHRS OR PRN AMT##ROWSPAN_6##<br>SHRS OR PRN AMT##ROWSPAN_6##': format_numeric(shares_amt),
                'SH/PRN##ROWSPAN_7##<br>SH/PRN##ROWSPAN_7##': get_text(shrs_prn, 'sshPrnamtType'),
                'PUT/CALL##ROWSPAN_8##<br>PUT/CALL##ROWSPAN_8##': get_text(item, 'putCall'),
                'INVESTMENT DISCRETION##ROWSPAN_9##<br>INVESTMENT DISCRETION##ROWSPAN_9##': get_text(item, 'investmentDiscretion'),
                'OTHER MANAGER##ROWSPAN_10##<br>OTHER MANAGER##ROWSPAN_10##': get_text(item, 'otherManager'),
                'VOTING AUTHORITY##COLSPAN_1##<br>SOLE': format_numeric(vote_sole),
                'VOTING AUTHORITY##COLSPAN_1##<br>SHARED': format_numeric(vote_shared),
                'VOTING AUTHORITY##COLSPAN_1##<br>NONE': format_numeric(vote_none),
            }
            rows.append(row_data)
            
        if rows:
            df = pd.DataFrame(rows)
            df = df.reindex(columns=final_columns, fill_value='—')

            parts.append("\n### FORM 13F INFORMATION TABLE")
            
            table_md = md_table_2row_header(df)
            parts.append(f"\n---\n{table_md}\n---")
    
    return "\n\n".join(parts)

def parse_form_npx_xml(xml_contents) -> str:
    """
    Parses a two-part Form N-PX filing into a single, comprehensive Markdown document.
    """
    if not xml_contents:
        return "<!-- No XML content for N-PX -->"

    main_xml = BeautifulSoup(xml_contents[0], 'lxml-xml')

    def get_text(node, tag):
        if not node or not (found := node.find(re.compile(f'^{tag}$', re.I))):
            return "—"
        return found.text.strip()

    header = main_xml.find(re.compile(r'^headerData$', re.I))
    filer_info = header.find(re.compile(r'^filerInfo$', re.I)) if header else None
    filer_creds = filer_info.find(re.compile(r'^filer$', re.I)) if filer_info else None
    flags = filer_info.find(re.compile(r'^flags$', re.I)) if filer_info else None

    form_data = main_xml.find(re.compile(r'^formData$', re.I))
    cover_page = form_data.find(re.compile(r'^coverPage$', re.I)) if form_data else None
    reporting_person = cover_page.find(re.compile(r'^reportingPerson$', re.I)) if cover_page else None
    rp_addr = reporting_person.find(re.compile(r'address', re.I)) if reporting_person else None
    agent = cover_page.find(re.compile(r'^agentForService$', re.I)) if cover_page else None
    agent_addr = agent.find(re.compile(r'address', re.I)) if agent else None
    summary_page = form_data.find(re.compile(r'^summaryPage$', re.I)) if form_data else None
    sig_page = form_data.find(re.compile(r'^signaturePage$', re.I)) if form_data else None

    parts = [
        "## FORM N-PX\n"
        "### ANNUAL REPORT OF PROXY VOTING RECORD\n",
        "## N-PX: Filer Information",
        f"**Filer CIK:** {get_text(filer_creds, 'cik')}",
        f"**Date of Report:** {get_text(header, 'periodOfReport')}",
        f"**Are you a Registered Management Investment Company or an Institutional Manager?:** {'Institutional Manager' if get_text(filer_info, 'registrantType') == 'IM' else 'Registered Management Investment Company'}",
        f"**Is this a LIVE or TEST Filing?:** {get_text(filer_info, 'liveTestFlag')}",
        f"**Is this an electronic copy of an official filing submitted in paper format?:** [{'x' if get_text(flags, 'confirmingCopyFlag') == 'true' else ' '}]",
    ]

    sub_contact = None
    for tag in ('submissionContact', 'contactInfo'):
        sub_contact = header.find(re.compile(f'^{tag}$', re.I)) if header else None
        if sub_contact:
            break

    if sub_contact and sub_contact.get_text(strip=True):
        parts.append("\n### Submission Contact Information")
        sc_fields = {
            "Name":  get_text(sub_contact, 'name'),
            "Title": get_text(sub_contact, 'title'),
            "Phone": get_text(sub_contact, 'phoneNumber'),
            "Email": get_text(sub_contact, 'emailAddress'),
        }
        for label, val in sc_fields.items():
            if val != "—":
                parts.append(f"**{label}:** {val}")
    else:
        parts.append("\n### Submission Contact Information\n_Not provided in this filing._")

    parts.extend([
        f"\n### Notification Information\n**Notify via Filing Website only?:** [{'x' if get_text(flags, 'overrideInternetFlag') == 'true' else ' '}]",
        "\n## N-PX: Cover Page",
        f"**Name of reporting person:** {get_text(reporting_person, 'name')}",
        f"**Address:** {get_text(rp_addr, 'street1')}, {get_text(rp_addr, 'city')}, {get_text(rp_addr, 'stateOrCountry')} {get_text(rp_addr, 'zipCode')}",
        f"**Telephone number:** {get_text(reporting_person, 'phoneNumber')}",
        f"**Name of agent for service:** {get_text(agent, 'name')}",
        f"**Agent Address:** {get_text(agent_addr, 'street1')}, {get_text(agent_addr, 'city')}, {get_text(agent_addr, 'stateOrCountry')} {get_text(agent_addr, 'zipCode')}",
        f"**Reporting Period:** Report for the year ended {get_text(header, 'periodOfReport')}",
        f"**SEC File Number:** {get_text(cover_page, 'fileNumber')}",
        "**CRD Number (if any):** —",
        "**Other SEC File Number (if any):** —",
        f"**LEI (if any):** {get_text(cover_page, 'leiNumber')}",
    ])

    report_type = get_text(cover_page.find('reportInfo'), 'reportType')
    report_options = {
        "Institutional Manager.": [
            "Institutional Manager Voting Report",
            "Institutional Manager Notice Report",
            "Institutional Manager Combination Report"
        ],
        "Registered Management Investment Company.": [
            "Fund Voting Report",
            "Fund Notice Report"
        ]
    }
    parts.append("\n**Report Type (check only one):**")
    for category, options in report_options.items():
        parts.append(f"\n{category}")
        for option in options:
            parts.append(f"- [{'x' if option.upper() in report_type.upper() else ' '}] {option}")

    exp_choice = get_text(cover_page.find('explanatoryInformation'), 'explanatoryChoice')
    parts.extend([
        "\n**Do you wish to provide explanatory information pursuant to Special Instruction B.4?:**",
        f"- [{' ' if exp_choice == 'Y' else 'x'}] No",
        f"- [{'x' if exp_choice == 'Y' else ' '}] Yes",
        "\n**Additional information:** —"
    ])

    parts.extend([
        "\n## N-PX: Summary - Included Managers",
        f"**Number of Included Institutional Managers:** {get_text(summary_page, 'otherIncludedManagersCount')}",
        f"**Included Institutional Managers:** {get_text(summary_page, 'includedManagers') or 'NONE'}"
    ])

    if len(xml_contents) > 1:
        proxy_xml = BeautifulSoup(xml_contents[1], 'lxml-xml')
        if (proxy_vote_table := proxy_xml.find(re.compile(r'^proxyVoteTable$', re.I))):
            parts.append("\n## FORM N-PX PROXY VOTING RECORD\n")
            vote_records = []
            for item in proxy_vote_table.find_all(re.compile(r'^proxyTable$', re.I)):
                vote_node = item.find(re.compile(r'^vote$', re.I))
                how_voted, sv_ford, mgmt_rec = "—", "—", "—"
                if vote_node and (record := vote_node.find(re.compile(r'^voteRecord$', re.I))):
                    how_voted = get_text(record, 'howVoted')
                    sv_ford = get_text(record, 'sharesVoted')
                    mgmt_rec = get_text(record, 'managementRecommendation')
                category_text = "; ".join(c.text for c in item.select('categoryType'))
                vote_records.append({
                    'NAME OF ISSUER': get_text(item, 'issuerName'),
                    'CUSIP': get_text(item, 'cusip'),
                    'MEETING DATE': get_text(item, 'meetingDate'),
                    'VOTE DESCRIPTION': _collapse_newlines(get_text(item, 'voteDescription')),
                    'VOTE CATEGORY': category_text,
                    'SHARES VOTED': get_text(item, 'sharesVoted'),
                    'SHARES ON LOAN': get_text(item, 'sharesOnLoan'),
                    'HOW VOTED': how_voted,
                    'SHARES VOTED FOR OR AGAINST MANAGEMENT': sv_ford,
                    'FOR OR AGAINST MANAGEMENT': mgmt_rec,
                    'OTHER INFO': _collapse_newlines(get_text(item, 'voteOtherInfo'))
                })
            df = pd.DataFrame(vote_records).replace('—', '')
            parts.append(to_compact_markdown(df, index=False))

    parts.extend([
        "\n## N-PX: Signature Block",
        f"**Reporting Person:** {get_text(sig_page, 'reportingPerson')}",
        f"**By (Signature):** {get_text(sig_page, 'txSignature')}",
        f"**By (Printed Signature):** {get_text(sig_page, 'txPrintedSignature')}",
        f"**By (Title):** {get_text(sig_page, 'txTitle')}",
        f"**Date:** {get_text(sig_page, 'txAsOfDate')}"
    ])

    return "\n\n".join(parts)

def parse_form25_xml(xml: BeautifulSoup) -> str:
    """
    Parses the XML of a Form 25 filing into structured Markdown,
    including all standard boilerplate text for full context.
    """
    root = xml.find(re.compile(r'^notificationOfRemoval$', re.I))
    if not root:
        return "<!-- Could not find <notificationOfRemoval> in XML -->"

    def get_text(node, tag):
        if not node or not (found := node.find(re.compile(f'^{tag}$', re.I))): return "—"
        return found.text.strip()
    
    issuer_node = root.find(re.compile(r'^issuer$', re.I))
    exchange_node = root.find(re.compile(r'^exchange$', re.I))
    sig_node = root.find(re.compile(r'^signatureData$', re.I))

    issuer_name = get_text(issuer_node, 'entityName')
    file_number = get_text(issuer_node, 'fileNumber')
    exchange_name = get_text(exchange_node, 'entityName')
    
    address_node = issuer_node.find(re.compile(r'^address$', re.I))
    address = "—"
    if address_node:
        addr_parts = [get_text(address_node, p) for p in ['street1', 'city', 'stateOrCountry', 'zipCode']]
        address = ", ".join(p for p in addr_parts if p and p != "—")

    tel_num = get_text(issuer_node, 'telephoneNumber')
    security_desc = get_text(root, 'descriptionClassSecurity')
    rule_cited = get_text(root, 'ruleProvision')

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        "## FORM 25\n\n"
        "### NOTIFICATION OF REMOVAL FROM LISTING AND/OR REGISTRATION "
        "UNDER SECTION 12(b) OF THE SECURITIES EXCHANGE ACT OF 1934.\n",
        f"**Commission File Number:** {file_number}\n"
    ]

    details_data = {
        'Issuer:': issuer_name,
        'Exchange:': exchange_name,
        '(Exact name of Issuer as specified in its charter, and name of Exchange where security is listed and/or registered)': '',
        'Address:': address,
        'Telephone number:': tel_num,
        "(Address, including zip code, and telephone number, including area code, of Issuer's principal executive offices)": '',
        '(Description of class of securities)': security_desc,
    }
    details_df = pd.DataFrame(details_data.items(), columns=['', ''])
    parts.append(to_compact_markdown(details_df, index=False))
    
    parts.append("\n---\n\nPlease place an X in the box to designate the rule provision relied upon to strike the class of securities from listing and registration:\n")
    possible_rules = [ "17 CFR 240.12d2-2(a)(1)", "17 CFR 240.12d2-2(a)(2)", "17 CFR 240.12d2-2(a)(3)", "17 CFR 240.12d2-2(a)(4)", "Pursuant to 17 CFR 240.12d2-2(b), the Exchange has complied with its rules to strike the class of securities from listing and/or withdraw registration on the Exchange.", "Pursuant to 17 CFR 240.12d2-2(c), the Issuer has complied with its rules of the Exchange and the requirements of 17 CFR 240.12d2-2(c) governing the voluntary withdrawal of the class of securities from listing and registration on the Exchange."]
    for rule in possible_rules:
        parts.append(f"- [{'x' if rule_cited in rule else ' '}] {rule}")
            
    certification_text = f"Pursuant to the requirements of the Securities Exchange Act of 1934, {exchange_name} certifies that it has reasonable grounds to believe that it meets all of the requirements for filing the Form 25 and has caused this notification to be signed on its behalf by the undersigned duly authorized person."
    parts.extend(["\n---\n", textwrap.fill(certification_text, width=90), "\n"])
    
    sig_df = pd.DataFrame([{'Date': get_text(sig_node, 'signatureDate'), 'By': '', 'Name': get_text(sig_node, 'signatureName'), 'Title': get_text(sig_node, 'signatureTitle')}])
    parts.append(to_compact_markdown(sig_df, index=False))
    
    footer_text1 = "Form 25 and attached Notice will be considered compliance with the provisions of 17 CFR 240.19d-1 as applicable. See General Instructions."
    footer_text2 = "Persons who respond to the collection of information contained in this form are not required to respond unless the form displays a currently valid OMB Number."
    parts.extend(["\n\n" + textwrap.fill(footer_text1, width=90), "\n\n" + textwrap.fill(footer_text2, width=90)])

    return "\n".join(parts)

def parse_form144_xml(xml: BeautifulSoup, form_type: str) -> str:
    """
    Parses XML for Form 144 and 144/A filings into structured Markdown,
    including standard boilerplate text for full context.
    """
    submission = xml.find(re.compile(r'^(?:\w+:)?edgarSubmission$', re.I))
    if not submission: return "<!-- Could not find <edgarSubmission> in XML -->"

    header_data = submission.find(re.compile(r'^(?:\w+:)?headerData$', re.I))
    form_data = submission.find(re.compile(r'^(?:\w+:)?formData$', re.I))

    def get_text(node, tag):
        if not node: return "—"
        found = node.find(re.compile(rf'^(?:\w+:)?{tag}$', re.I))
        return found.text.strip() if found else "—"

    filer_info_node = header_data.find(re.compile(r'^(?:\w+:)?filerInfo$', re.I)) if header_data else None
    filer_creds_node = filer_info_node.find(re.compile(r'^(?:\w+:)?filer$', re.I)) if filer_info_node else None
    issuer_info = form_data.find(re.compile(r'^(?:\w+:)?issuerInfo$', re.I))
    issuer_address_node = issuer_info.find(re.compile(r'^(?:\w+:)?issuerAddress$', re.I)) if issuer_info else None
    
    issuer_address = "—"
    if issuer_address_node:
        addr_parts = [ get_text(issuer_address_node, t) for t in ['street1', 'city', 'stateOrCountry', 'zipCode'] ]
        issuer_address = ", ".join(p for p in addr_parts if p and p != "—")
    
    relationships_node = issuer_info.find(re.compile(r'^(?:\w+:)?relationshipsToIssuer$', re.I)) if issuer_info else None
    relationships = [rel.text.strip() for rel in relationships_node.find_all(re.compile(r'^(?:\w+:)?relationshipToIssuer$', re.I))] if relationships_node else []
    full_relationship = ", ".join(relationships) if relationships else "—"

    parts = [
        "### UNITED STATES SECURITIES AND EXCHANGE COMMISSION\n"
        "**Washington, D.C. 20549**\n\n"
        f"## FORM {form_type}\n\n"
        "### NOTICE OF PROPOSED SALE OF SECURITIES\n"
        "### PURSUANT TO RULE 144 UNDER THE SECURITIES ACT OF 1933\n"
    ]
    parts.extend([f"### {form_type}: Filer Information", f"**Filer CIK:** {get_text(filer_creds_node, 'cik')}"])
    if (prev_accession := get_text(header_data, 'previousAccessionNumber')) != "—":
        parts.append(f"**Previous Accession Number Of The Filing:** {prev_accession}")
    
    parts.append(f"**Is this a LIVE or TEST Filing?:** {get_text(header_data, 'liveTestFlag')}")

    sub_contact = header_data.find(re.compile(r'^(?:\w+:)?submissionContact$', re.I)) if header_data else None
    if sub_contact and sub_contact.get_text(strip=True):
        parts.append("\n### Submission Contact Information")
        sc_fields = {
            "Name":  get_text(sub_contact, 'name'),
            "Phone": get_text(sub_contact, 'phone'),
            "Email": get_text(sub_contact, 'email'),
        }
        for label, val in sc_fields.items():
            if val != "—":
                parts.append(f"**{label}:** {val}")
    else:
        parts.append("\n### Submission Contact Information\n_Not provided in this filing._")

    parts.extend([
        f"\n### {form_type}: Issuer Information",
        f"**Name of Issuer:** {get_text(issuer_info, 'issuerName')}",
        f"**SEC File Number:** {get_text(issuer_info, 'secFileNumber')}",
        f"**Address of Issuer:** {issuer_address}",
        f"**Phone:** {get_text(issuer_info, 'issuerContactPhone')}",
        f"**Name of Person for Whose Account the Securities are to Be Sold:** {get_text(issuer_info, 'nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold')}",
        f"**Relationship to Issuer:** {full_relationship}",
        "\n" + textwrap.fill("See the definition of \"person\" in paragraph (a) of Rule 144. Information is to be given not only as to the person for whose account the securities are to be sold but also as to all other persons included in that definition. In addition, information shall be given as to sales by all persons whose sales are required by paragraph (e) of Rule 144 to be aggregated with sales for the account of the person filing this notice.")
    ])

    sec_info = form_data.find(re.compile(r'^(?:\w+:)?securitiesInformation$', re.I))
    broker = sec_info.find(re.compile(r'^(?:\w+:)?brokerOrMarketmakerDetails$', re.I)) if sec_info else None
    full_broker_info = get_text(broker, 'name')
    if broker and (broker_addr_node := broker.find(re.compile(r'^(?:\w+:)?address$', re.I))):
        addr_lines = [get_text(broker_addr_node, t) for t in ['street1', 'street2']]
        city_state_zip = " ".join(p for p in [get_text(broker_addr_node, t) for t in ['city', 'stateOrCountry', 'zipCode']] if p and p != "—")
        if city_state_zip: addr_lines.append(city_state_zip)
        if valid_lines := [line for line in addr_lines if line and line != "—"]:
            full_broker_info += "<br>" + "<br>".join(valid_lines)
    
    df_proposed_data = {
        'Title of the Class of Securities To Be Sold': get_text(sec_info, 'securitiesClassTitle'),
        'Name and Address of the Broker': full_broker_info,
        'Number of Shares or Other Units To Be Sold': get_text(sec_info, 'noOfUnitsSold'),
        'Aggregate Market Value': get_text(sec_info, 'aggregateMarketValue'),
        'Number of Shares or Other Units Outstanding': get_text(sec_info, 'noOfUnitsOutstanding'),
        'Approximate Date of Sale': get_text(sec_info, 'approxSaleDate'),
        'Name the Securities Exchange': get_text(sec_info, 'securitiesExchangeName')
    }
    df_proposed = pd.DataFrame([df_proposed_data])

    parts.extend([f"\n### {form_type}: Securities Information", df_to_markdown(df_proposed, is_clean=True, disable_numparse=True), "\n" + textwrap.fill("Furnish the following information with respect to the acquisition of the securities to be sold and with respect to the payment of all or any part of the purchase price or other consideration therefor:")])

    acq_data = [{'Title of the Class': get_text(item, "securitiesClassTitle"), 'Date you Acquired': get_text(item, "acquiredDate"), 'Nature of Acquisition Transaction': get_text(item, "natureOfAcquisitionTransaction"), 'Name of Person from Whom Acquired': get_text(item, "nameOfPersonfromWhomAcquired"), 'Is this a Gift?': "Yes" if get_text(item, "isGiftTransaction") == 'Y' else "No", 'Date Donor Acquired': get_text(item, "donarAcquiredDate"), 'Amount of Securities Acquired': get_text(item, "amountOfSecuritiesAcquired"), 'Date of Payment': get_text(item, "paymentDate"), 'Nature of Payment *': get_text(item, "natureOfPayment")} for item in form_data.find_all(re.compile(r'^(?:\w+:)?securitiesToBeSold$', re.I))]
    if acq_data:
        df_acq = pd.DataFrame(acq_data)
        parts.extend([f"\n### {form_type}: Securities To Be Sold", df_to_markdown(df_acq, is_clean=True, disable_numparse=True), "\n" + textwrap.fill("* If the securities were purchased and full payment therefor was not made in cash at the time of purchase, explain in the table or in a note thereto the nature of the consideration given. If the consideration consisted of any note or other obligation, or if payment was made in installments describe the arrangement and state when the note or other obligation was discharged in full or the last installment paid."), "\n" + textwrap.fill("Furnish the following information as to all securities of the issuer sold during the past 3 months by the person for whose account the securities are to be sold.")])

    parts.append(f"\n### {form_type}: Securities Sold During The Past 3 Months")
    if get_text(form_data, 'nothingToReportFlagOnSecuritiesSoldInPast3Months') == 'Y':
        parts.append("Nothing to Report")
    else:
        past_sales_data = []
        for s in form_data.find_all(re.compile(r'^(?:\w+:)?securitiesSoldInPast3Months$', re.I)):
            seller_details_node = s.find(re.compile(r'^(?:\w+:)?sellerDetails$', re.I))
            seller_name = get_text(seller_details_node, "name")
            full_seller_info = seller_name
            if seller_details_node and (seller_addr_node := seller_details_node.find(re.compile(r'^(?:\w+:)?address$', re.I))):
                addr_lines = [get_text(seller_addr_node, t) for t in ['street1', 'street2']]
                city_state_zip = " ".join(p for p in [get_text(seller_addr_node, t) for t in ['city', 'stateOrCountry', 'zipCode']] if p and p != "—")
                if city_state_zip: addr_lines.append(city_state_zip)
                if valid_lines := [line for line in addr_lines if line and line != "—"]:
                    full_seller_info += "<br>" + "<br>".join(valid_lines)
            
            past_sales_data.append({
                'Name and Address of Seller': full_seller_info,
                'Title of Securities Sold': get_text(s, "securitiesClassTitle"),
                'Date of Sale': get_text(s, "saleDate"),
                'Amount of Securities Sold': get_text(s, "amountOfSecuritiesSold"),
                'Gross Proceeds': get_text(s, 'grossProceeds')
            })
        
        if past_sales_data: 
            parts.append(df_to_markdown(pd.DataFrame(past_sales_data), is_clean=True, disable_numparse=True))

    parts.append(f"\n### {form_type}: Remarks and Signature")
    if (remarks := form_data.find(re.compile(r'^(?:\w+:)?remarks$', re.I))) and remarks.text.strip():
        parts.append(f"**Remarks:** {textwrap.fill(remarks.text.strip())}")
    
    signature_node = form_data.find(re.compile(r'^(?:\w+:)?noticeSignature$', re.I))
    plan_adoption_dates_node = signature_node.find(re.compile(r'^(?:\w+:)?planAdoptionDates$', re.I)) if signature_node else None
    plan_date = get_text(plan_adoption_dates_node, "planAdoptionDate")

    parts.append(f"**Date of Notice:** {get_text(signature_node, 'noticeDate')}")

    if plan_date != "—":
        parts.append(f"**Date of Plan Adoption or Giving of Instruction, If Relying on Rule 10b5-1:** {plan_date}")

    parts.append("\nATTENTION:\n\n" + textwrap.fill("The person for whose account the securities to which this notice relates are to be sold hereby represents by signing this notice that he does not know any material adverse information in regard to the current and prospective operations of the Issuer of the securities to be sold which has not been publicly disclosed. If such person has adopted a written trading plan or given trading instructions to satisfy Rule 10b5-1 under the Exchange Act, by signing the form and indicating the date that the plan was adopted or the instruction given, that person makes such representation as of the plan adoption or instruction date."))

    parts.append(f"\n**Signature:** {get_text(signature_node, 'signature')}")

    return "\n\n".join(parts)

__all__ = [name for name in globals() if not name.startswith("__")]
