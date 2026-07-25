#!/usr/bin/env python3
"""Parse official SEC XML samples and measure source-field preservation."""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
import sys

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from stanford_edgar_parser.parsers.xml import (
    fund_and_ownership,
    ownership,
    regulatory_forms,
)
from stanford_edgar_parser.parsers.xml.preservation import (
    _compact,
    _direct_fields,
    _normalized,
    _output_numbers,
    _value_preserved,
)
from stanford_edgar_parser.parsers.xml.regulatory_forms import parse_any_xml


INDIVIDUAL_SAMPLE_FAMILIES = {
    "abs",
    "atsn",
    "cfportal",
    "form24f2",
    "form25",
    "form144",
    "formc",
    "formd",
    "formma",
    "formta",
    "ncen",
    "nmfp1",
    "nmfp2",
    "nmfp3",
    "nport",
    "rega",
    "sbsef",
    "schedule13d_g",
    "x17a5",
}
HANDLER_EVIDENCE = {
    "parse_abs_ee_xml": "abs official samples",
    "parse_abs_ee_comments_xml": "abs official EX-103 sample",
    "parse_schedule13d_xml": "schedule13d_g official samples and locked suite",
    "parse_schedule13g_xml": "schedule13d_g official samples and locked suite",
    "parse_form1a_xml": "rega official samples",
    "parse_form1k_xml": "rega official samples",
    "parse_form1z_xml": "rega official samples",
    "parse_form_ta1_xml": "formta official samples",
    "parse_form_ta2_xml": "formta official samples",
    "parse_form_taw_xml": "formta official samples",
    "parse_form_mai_xml": "formma official samples and locked suite",
    "parse_form_ma_xml": "formma official samples",
    "parse_form_maw_xml": "formma official samples",
    "parse_form_x17a5_xml": "x17a5 official samples",
    "parse_form_cfportal_xml": "cfportal official samples",
    "parse_form_24f2nt_xml": "form24f2 official samples and locked suite",
    "parse_legacy_n_mfp_xml": "nmfp1 official samples",
    "parse_sbse_a_xml": "sbs official SBSE-A sample",
    "parse_form_atsn_xml": "atsn official samples",
    "parse_form_n_mfp3_xml": "nmfp3 official samples and locked suite",
    "parse_form_sbsef_xml": "sbsef official samples",
    "parse_effect_xml": "edgarlink_online schema and locked EFFECT cases",
    "parse_form13f_hr_xml": "form13f official samples and locked suite",
    "parse_form_npx_xml": "npx official samples and locked suite",
    "parse_form25_xml": "form25 official samples",
    "parse_form144_xml": "form144 official samples and locked suite",
    "parse_form3_xml": "ownership official samples and locked suite",
    "parse_form4_xml": "ownership official samples and locked suite",
    "parse_form_d_xml": "formd official samples and locked suite",
    "parse_form_n_mfp2_xml": "nmfp2 official samples and locked suite",
    "parse_form_n_cen_xml": "ncen official samples and locked suite",
    "parse_form_c_xml": "formc official samples and locked suite",
    "parse_nport_p_xml": "nport official samples and locked suite",
}


def sample_xml_files(family_root: Path) -> list[Path]:
    paths = [
        path
        for path in family_root.rglob("*.xml")
        if not path.name.casefold().endswith(".xsd.xml")
    ]
    if family_root.name == "rega":
        paths = [path for path in paths if "Stylesheets" not in str(path)]
    return sorted(paths)


def score_documents(documents: list[str], output: str) -> tuple[int, int]:
    normalized_output = _normalized(output)
    compact_output = _compact(output)
    numbers = _output_numbers(output)
    total = preserved = 0
    for document in documents:
        soup = BeautifulSoup(document, "lxml-xml")
        for node in soup.find_all():
            for name, value in _direct_fields(node):
                total += 1
                preserved += int(
                    _value_preserved(
                        name,
                        value,
                        normalized_output,
                        compact_output,
                        numbers,
                    )
                )
    return total, preserved


def case_row(case_id: str, family: str, paths: list[Path]) -> dict:
    documents = [path.read_text(errors="replace") for path in paths]
    row = {
        "id": case_id,
        "family": family,
        "sources": [str(path) for path in paths],
        "parse_success": False,
    }
    try:
        output = parse_any_xml(documents)
        row["output_chars"] = len(output)
        row["parse_success"] = bool(output)
        if output:
            total, preserved = score_documents(documents, output)
            row["source_fields"] = total
            row["preserved_fields"] = preserved
            row["field_recall"] = preserved / total if total else 1.0
    except Exception as error:
        row["error"] = f"{type(error).__name__}: {error}"
    return row


def official_sample_cases(cache_root: Path) -> list[tuple[str, str, list[Path]]]:
    cases = []
    for family in sorted(INDIVIDUAL_SAMPLE_FAMILIES):
        for path in sample_xml_files(cache_root / family):
            cases.append((f"{family}:{path.name}", family, [path]))

    sbs = [
        path
        for path in sample_xml_files(cache_root / "sbs")
        if path.name == "SBSE-A.xml"
    ]
    cases.extend((f"sbs:{path.name}", "sbs", [path]) for path in sbs)

    ownership_files = {
        path.name: path for path in sample_xml_files(cache_root / "ownership")
    }
    for suffix in ("3", "3a", "4", "4a", "5", "5a"):
        cases.append(
            (
                f"ownership:{suffix}",
                "ownership",
                [ownership_files[f"filing{suffix}.xml"], ownership_files[f"doc{suffix}.xml"]],
            )
        )

    form13f = {
        path.name: path for path in sample_xml_files(cache_root / "form13f")
    }
    for name in ("Sample_13F-HR.xml", "Sample_13F-HRA.xml"):
        cases.append(
            (
                f"form13f:{name}",
                "form13f",
                [form13f[name], form13f["information_table.xml"]],
            )
        )

    npx = {path.name: path for path in sample_xml_files(cache_root / "npx")}
    for name in ("N-PX_sample.xml", "N-PX_A.xml"):
        cases.append(
            (
                f"npx:{name}",
                "npx",
                [npx[name], npx["ProxyVoteTable.xml"]],
            )
        )
    return cases


def routed_handlers() -> set[str]:
    handlers = set()
    for module in (fund_and_ownership, ownership, regulatory_forms):
        handlers.update(
            name
            for name, function in inspect.getmembers(module, inspect.isfunction)
            if name.startswith("parse_")
            and name.endswith("_xml")
            and name != "parse_any_xml"
        )
    return handlers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    handlers = routed_handlers()
    missing_evidence = sorted(handlers - HANDLER_EVIDENCE.keys())
    stale_evidence = sorted(HANDLER_EVIDENCE.keys() - handlers)
    rows = [
        case_row(case_id, family, paths)
        for case_id, family, paths in official_sample_cases(args.cache_dir)
    ]
    by_family = {}
    for family in sorted({row["family"] for row in rows}):
        family_rows = [row for row in rows if row["family"] == family]
        total = sum(row.get("source_fields", 0) for row in family_rows)
        preserved = sum(row.get("preserved_fields", 0) for row in family_rows)
        by_family[family] = {
            "case_count": len(family_rows),
            "parse_success_count": sum(row["parse_success"] for row in family_rows),
            "source_fields": total,
            "preserved_fields": preserved,
            "field_recall": preserved / total if total else 1.0,
        }

    total = sum(row.get("source_fields", 0) for row in rows)
    preserved = sum(row.get("preserved_fields", 0) for row in rows)
    report = {
        "summary": {
            "handler_count": len(handlers),
            "handlers_with_evidence": len(handlers & HANDLER_EVIDENCE.keys()),
            "missing_handler_evidence": missing_evidence,
            "stale_handler_evidence": stale_evidence,
            "case_count": len(rows),
            "parse_success_count": sum(row["parse_success"] for row in rows),
            "source_fields": total,
            "preserved_fields": preserved,
            "field_recall": preserved / total if total else 1.0,
        },
        "handler_evidence": HANDLER_EVIDENCE,
        "by_family": by_family,
        "cases": rows,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["summary"], indent=2))
    return int(
        bool(missing_evidence)
        or bool(stale_evidence)
        or report["summary"]["parse_success_count"] != len(rows)
        or preserved != total
    )


if __name__ == "__main__":
    raise SystemExit(main())
