from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from stanford_edgar_parser.parsers.xml.fund_and_ownership import (
    parse_nport_p_xml,
)
from stanford_edgar_parser.parsers.xml.regulatory_forms import (
    parse_any_xml,
    parse_form144_xml,
    parse_form_24f2nt_xml,
    parse_form_n_mfp3_xml,
    parse_form_npx_xml,
    parse_schedule13d_xml,
)


def soup(xml: str) -> BeautifulSoup:
    return BeautifulSoup(xml, "lxml-xml")


class XmlSchemaCoverageTests(unittest.TestCase):
    def test_nport_preserves_attribute_metrics_identifiers_and_derivatives(self) -> None:
        xml = soup(
            """
            <edgarSubmission>
              <formData>
                <fundInfo>
                  <curMetrics><curMetric><curCd>USD</curCd>
                    <intrstRtRiskdv01 period1Yr="1.234567" period5Yr="5.678901"
                      period10Yr="10.111213"/>
                    <intrstRtRiskdv100 period1Yr="100.234567" period5Yr="500.678901"
                      period10Yr="1000.111213"/>
                  </curMetric></curMetrics>
                </fundInfo>
                <invstOrSecs><invstOrSec>
                  <name>Example Security</name><cusip>123456789</cusip>
                  <identifiers><isin value="US1234567890"/><ticker value="EXM"/></identifiers>
                  <valUSD>1000.00</valUSD>
                  <derivativeInfo><fwdDeriv derivCat="FWD">
                    <counterpartyLei>549300EXAMPLE</counterpartyLei>
                    <indexName>Example Index</indexName>
                    <notionalAmt>12345.67</notionalAmt>
                  </fwdDeriv></derivativeInfo>
                </invstOrSec></invstOrSecs>
              </formData>
            </edgarSubmission>
            """
        )
        output = parse_nport_p_xml(xml)
        for expected in (
            "1.234567",
            "100.234567",
            "US1234567890",
            "Ticker: EXM",
            "549300EXAMPLE",
            "Example Index",
            "12345.67",
        ):
            self.assertIn(expected, output)

    def test_npx_emits_every_vote_record_and_identifiers(self) -> None:
        main = """
        <edgarSubmission><headerData><periodOfReport>06/30/2025</periodOfReport></headerData>
          <formData><coverPage><reportingPerson><name>Example Manager</name></reportingPerson>
          <reportInfo><reportType>Institutional Manager Voting Report</reportType></reportInfo>
          </coverPage><summaryPage/><signaturePage/></formData></edgarSubmission>
        """
        votes = """
        <proxyVoteTable><proxyTable>
          <issuerName>Example Issuer</issuerName><cusip>123456789</cusip>
          <isin>US1234567890</isin><meetingDate>05/01/2025</meetingDate>
          <voteDescription>Elect directors</voteDescription><voteSource>ISSUER</voteSource>
          <vote><voteRecord><howVoted>FOR</howVoted><sharesVoted>101</sharesVoted>
          <managementRecommendation>FOR</managementRecommendation></voteRecord>
          <voteRecord><howVoted>AGAINST</howVoted><sharesVoted>202</sharesVoted>
          <managementRecommendation>FOR</managementRecommendation></voteRecord></vote>
          <voteSeries>S000000001</voteSeries>
        </proxyTable></proxyVoteTable>
        """
        output = parse_form_npx_xml([main, votes])
        self.assertIn("US1234567890", output)
        self.assertIn("S000000001", output)
        self.assertEqual(output.count("Example Issuer"), 2)
        self.assertIn("101", output)
        self.assertIn("202", output)

    def test_nmfp3_preserves_class_and_collateral_identifiers(self) -> None:
        xml = soup(
            """
            <edgarSubmission><headerData/><formData>
              <generalInfo><nameOfSeries>Example Fund</nameOfSeries></generalInfo>
              <seriesLevelInfo/>
              <classLevelInfo><classFullName>Institutional</classFullName>
                <classesId>C000000001</classesId></classLevelInfo>
              <scheduleOfPortfolioSecuritiesInfo>
                <nameOfIssuer>Example Counterparty</nameOfIssuer>
                <CUSIPMember>123456789</CUSIPMember>
                <repurchaseAgreement><collateralIssuers>
                  <nameOfCollateralIssuer>Example Treasury</nameOfCollateralIssuer>
                  <CUSIPMember>987654321</CUSIPMember>
                  <LEIID>549300EXAMPLE</LEIID>
                </collateralIssuers></repurchaseAgreement>
              </scheduleOfPortfolioSecuritiesInfo>
            </formData></edgarSubmission>
            """
        )
        output = parse_form_n_mfp3_xml(xml)
        for expected in ("C000000001", "123456789", "987654321", "549300EXAMPLE"):
            self.assertIn(expected, output)

    def test_schedule13d_emits_items_two_three_and_seven(self) -> None:
        xml = soup(
            """
            <edgarSubmission><coverPageHeader><issuerInfo><issuerName>Example Issuer</issuerName>
              <issuerCusip>123456789</issuerCusip></issuerInfo>
              <securitiesClassTitle>Common Stock</securitiesClassTitle></coverPageHeader>
              <formData><items1To7>
                <item2><filingPersonName>Example Reporting Person</filingPersonName>
                  <principalBusinessAddress>Example Address</principalBusinessAddress>
                  <principalJob>Investment manager</principalJob>
                  <convictionDescription>No proceedings</convictionDescription></item2>
                <item3><fundsSource>Working capital</fundsSource></item3>
                <item7><filedExhibits>Joint Filing Agreement</filedExhibits></item7>
              </items1To7></formData>
            </edgarSubmission>
            """
        )
        output = parse_schedule13d_xml(xml)
        self.assertIn("Item 2. Identity and Background", output)
        self.assertIn("Working capital", output)
        self.assertIn("Joint Filing Agreement", output)

    def test_24f_current_schema_emits_all_series_and_fee_fields(self) -> None:
        xml = soup(
            """
            <edgarSubmission><headerData/><formData><annualFilingInfo>
              <item2><reportSeriesClass>
                <rptSeriesClassInfo><seriesName>Series One</seriesName><seriesId>S000000001</seriesId>
                  <includeAllClassesFlag>true</includeAllClassesFlag></rptSeriesClassInfo>
                <rptSeriesClassInfo><seriesName>Series Two</seriesName><seriesId>S000000002</seriesId>
                  <includeAllClassesFlag>false</includeAllClassesFlag></rptSeriesClassInfo>
              </reportSeriesClass></item2>
              <item3><investmentCompActFileNo>811-00001</investmentCompActFileNo></item3>
              <item6><interestDue>12.34</interestDue></item6>
              <item7><totalOfRegistrationFeePlusAnyInterestDue>56.78</totalOfRegistrationFeePlusAnyInterestDue></item7>
            </annualFilingInfo></formData></edgarSubmission>
            """
        )
        output = parse_form_24f2nt_xml(xml)
        for expected in ("Series One", "S000000001", "Series Two", "S000000002", "$12.34", "$56.78"):
            self.assertIn(expected, output)

    def test_form144_emits_repeated_sales_and_plan_dates(self) -> None:
        xml = soup(
            """
            <edgarSubmission><headerData/><formData>
              <issuerInfo><issuerName>Example Issuer</issuerName></issuerInfo>
              <securitiesInformation><securitiesClassTitle>Class A</securitiesClassTitle>
                <noOfUnitsSold>100</noOfUnitsSold></securitiesInformation>
              <securitiesInformation><securitiesClassTitle>Class B</securitiesClassTitle>
                <noOfUnitsSold>200</noOfUnitsSold></securitiesInformation>
              <noticeSignature><noticeDate>07/01/2026</noticeDate>
                <planAdoptionDates><planAdoptionDate>01/01/2026</planAdoptionDate>
                <planAdoptionDate>02/01/2026</planAdoptionDate></planAdoptionDates>
                <signature>Example Signer</signature></noticeSignature>
            </formData></edgarSubmission>
            """
        )
        output = parse_form144_xml(xml, "144")
        for expected in ("Class A", "Class B", "100", "200", "01/01/2026", "02/01/2026"):
            self.assertIn(expected, output)

    def test_router_preserves_multiline_root_attributes(self) -> None:
        output = parse_any_xml(
            [
                """
                <edgarSubmission
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
                  <headerData><submissionType>ATS-N-W</submissionType>
                    <filerInfo><filer><MPID>MLTI</MPID>
                      <NMSStockATSName>Multiline Root ATS</NMSStockATSName>
                    </filer></filerInfo>
                  </headerData>
                  <formData><partFour>
                    <txSignatureName>Example Signer</txSignatureName>
                  </partFour></formData>
                </edgarSubmission>
                """
            ]
        )
        self.assertIn("Multiline Root ATS", output)
        self.assertIn("Example Signer", output)

    def test_router_handles_atsn_withdrawal(self) -> None:
        output = parse_any_xml(
            [
                """
                <edgarSubmission>
                  <headerData>
                    <submissionType>ATS-N-W</submissionType>
                    <filerInfo><filer><MPID>EXMP</MPID>
                      <NMSStockATSName>Example ATS</NMSStockATSName>
                    </filer></filerInfo>
                  </headerData>
                  <formData><partFour>
                    <txPart4ContactFirstName>Example</txPart4ContactFirstName>
                    <txPart4ContactLastName>Signer</txPart4ContactLastName>
                    <txSignatureName>Example Signer</txSignatureName>
                  </partFour></formData>
                </edgarSubmission>
                """
            ]
        )
        self.assertIn("Notice of Withdrawal", output)
        self.assertIn("Example ATS", output)
        self.assertIn("Example Signer", output)

    def test_router_handles_nport_np(self) -> None:
        output = parse_any_xml(
            [
                """
                <edgarSubmission>
                  <headerData><submissionType>NPORT-NP</submissionType></headerData>
                  <formData>
                    <genInfo><regName>Example Registrant</regName>
                      <seriesName>Example Series</seriesName></genInfo>
                    <fundInfo><totAssets>123.45</totAssets></fundInfo>
                  </formData>
                </edgarSubmission>
                """
            ]
        )
        self.assertIn("Form NPORT-NP", output)
        self.assertIn("Example Registrant", output)
        self.assertIn("123.45", output)

    def test_router_handles_npx_amendment(self) -> None:
        primary = """
        <edgarSubmission><headerData><submissionType>N-PX/A</submissionType>
          <periodOfReport>06/30/2025</periodOfReport></headerData>
          <formData><coverPage><reportingPerson><name>Example Manager</name>
          </reportingPerson><reportInfo><reportType>Amended Report</reportType>
          </reportInfo></coverPage><summaryPage/><signaturePage/></formData>
        </edgarSubmission>
        """
        votes = """
        <proxyVoteTable><proxyTable><issuerName>Example Issuer</issuerName>
          <meetingDate>05/01/2025</meetingDate>
          <voteDescription>Elect directors</voteDescription>
          <vote><voteRecord><howVoted>FOR</howVoted><sharesVoted>10</sharesVoted>
          <managementRecommendation>FOR</managementRecommendation>
          </voteRecord></vote></proxyTable></proxyVoteTable>
        """
        output = parse_any_xml([primary, votes])
        self.assertIn("FORM N-PX", output)
        self.assertIn("Example Issuer", output)


if __name__ == "__main__":
    unittest.main()
