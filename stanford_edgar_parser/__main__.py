"""Command-line entrypoint for ``python -m stanford_edgar_parser``."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .api import (
    get_mistral_key_status_snapshot,
    main_one,
    reset_mistral_key_status,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse an SEC filing in HTML, HTM, or TXT format and convert it to Markdown.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("path", nargs="?", help="Path to the SEC filing.")
    parser.add_argument(
        "--to_mmd",
        action="store_true",
        help="Convert all tables in the final output to MultiMarkdown format.",
    )
    parser.add_argument(
        "--source-document-url",
        help="Optional absolute source document URL used to resolve normal relative links.",
    )
    parser.add_argument(
        "--disable_indentation",
        "--disable-indentation",
        action="store_true",
        dest="disable_indentation",
        help="Remove final-output indentation NBSP markers from the written Markdown.",
    )
    parser.add_argument(
        "--mistral-key-status",
        action="store_true",
        help="Print the shared Mistral key rotation/usage monitor JSON and exit.",
    )
    parser.add_argument(
        "--reset-mistral-key-status",
        action="store_true",
        help="Reset the shared Mistral key rotation/usage monitor JSON and exit.",
    )
    args = parser.parse_args(argv)

    if args.reset_mistral_key_status:
        print(json.dumps(reset_mistral_key_status(), indent=2, sort_keys=True))
        return 0
    if args.mistral_key_status:
        print(json.dumps(get_mistral_key_status_snapshot(), indent=2, sort_keys=True))
        return 0
    if not args.path:
        parser.error("the following arguments are required: path")

    file_path = pathlib.Path(args.path)
    if not file_path.is_file() and file_path.parts and file_path.parts[0] == "sec_parser":
        alt_path = pathlib.Path(*file_path.parts[1:])
        if alt_path.is_file():
            print(f"[info] Using '{alt_path}' instead of '{args.path}'.")
            file_path = alt_path
    if not file_path.is_file():
        print(f"Error: File not found at {args.path}", file=sys.stderr)
        return 1

    main_one(
        file_path,
        to_mmd=args.to_mmd,
        source_document_url=args.source_document_url,
        disable_indentation=args.disable_indentation,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
