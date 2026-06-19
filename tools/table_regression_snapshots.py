#!/usr/bin/env python3
"""Run isolated table snapshot regressions for the public parser package."""

from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import tempfile
import warnings
from dataclasses import dataclass
from pathlib import Path

from stanford_edgar_parser.api import main_one


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_DIR = ROOT / "tests" / "table_regressions" / "cases"


@dataclass
class RegressionCase:
    case_id: str
    case_dir: Path
    label: str
    to_mmd: bool
    source_document_url: str
    enabled: bool


def load_case(case_dir: Path) -> RegressionCase:
    payload = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    return RegressionCase(
        case_id=str(payload["case_id"]),
        case_dir=case_dir,
        label=str(payload.get("label") or payload["case_id"]),
        to_mmd=bool(payload.get("to_mmd", True)),
        source_document_url=str(payload.get("source_document_url", "") or ""),
        enabled=bool(payload.get("enabled", True)),
    )


def collect_case_dirs(cases_dir: Path, name_filter: str) -> list[Path]:
    if not cases_dir.exists():
        return []

    matches: list[Path] = []
    for child in sorted(cases_dir.iterdir()):
        if not child.is_dir() or not (child / "case.json").exists():
            continue
        case = load_case(child)
        if not case.enabled:
            continue
        if name_filter:
            needle = name_filter.lower()
            if needle not in child.name.lower() and needle not in case.label.lower():
                continue
        matches.append(child)
    return matches


def run_parser(table_html: str, case: RegressionCase) -> bytes:
    with tempfile.TemporaryDirectory(prefix=f"sefd_table_{case.case_id}_") as tmp:
        wrapped_path = Path(tmp) / "input_wrapped.txt"
        wrapped_path.write_text(f"<html><body>{table_html}</body></html>", encoding="utf-8")

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", FutureWarning)
                main_one(
                    wrapped_path,
                    to_mmd=case.to_mmd,
                    source_document_url=case.source_document_url or None,
                )

        output_path = wrapped_path.with_suffix(".md")
        if not output_path.exists():
            raise RuntimeError(f"Expected parser output was not written: {output_path}")
        return output_path.read_bytes()


def build_diff(expected: bytes, actual: bytes, case_id: str) -> str:
    expected_text = expected.decode("utf-8", errors="replace")
    actual_text = actual.decode("utf-8", errors="replace")
    return "\n".join(
        difflib.unified_diff(
            expected_text.splitlines(),
            actual_text.splitlines(),
            fromfile=f"{case_id}/expected.md",
            tofile=f"{case_id}/actual.md",
            lineterm="",
        )
    )


def run_cases(args: argparse.Namespace) -> int:
    cases_dir = Path(args.cases_dir).expanduser().resolve()
    case_dirs = collect_case_dirs(cases_dir, args.filter_text or "")
    if not case_dirs:
        print(f"No regression cases found in {cases_dir}")
        return 1

    failures = 0
    passed = 0
    updated = 0

    for case_dir in case_dirs:
        case = load_case(case_dir)
        input_path = case_dir / "input_table.html"
        expected_path = case_dir / "expected.md"

        checks: list[str] = []
        if not input_path.exists():
            checks.append("missing input_table.html")
            actual = b""
        else:
            actual = run_parser(input_path.read_text(encoding="utf-8"), case)

        if args.update_expected and actual:
            expected_path.write_bytes(actual)
            updated += 1

        if not expected_path.exists():
            checks.append("missing expected.md")
            expected = b""
        else:
            expected = expected_path.read_bytes()
            if expected != actual:
                diff = build_diff(expected, actual, case.case_id)
                checks.append("snapshot mismatch")
                if args.show_diff:
                    print(diff)

        if checks:
            failures += 1
            print(f"[fail] {case.case_id} | {case.label} | " + " | ".join(checks))
        else:
            passed += 1
            print(f"[pass] {case.case_id} | {case.label}")

    print("\nSummary")
    print(f"passed   : {passed}")
    print(f"failed   : {failures}")
    print(f"updated  : {updated}")
    print(f"cases dir: {cases_dir}")
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run table snapshot regression tests.")
    parser.add_argument("--cases-dir", default=str(DEFAULT_CASES_DIR))
    parser.add_argument("--filter-text", default="")
    parser.add_argument("--update-expected", action="store_true")
    parser.add_argument("--show-diff", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    return run_cases(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
