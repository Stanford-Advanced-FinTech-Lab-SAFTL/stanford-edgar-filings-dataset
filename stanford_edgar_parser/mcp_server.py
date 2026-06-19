"""Minimal stdio MCP server for the Stanford EDGAR parser.

The server intentionally uses only the Python standard library so it can run
from a fresh clone without installing an MCP SDK.
"""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import traceback
from typing import Any

from stanford_edgar_parser.api import main_one


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _json_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


PARSE_TOOL: dict[str, Any] = {
    "name": "parse_filing",
    "description": "Parse a local SEC filing TXT/HTML/SGML/XML submission into Markdown or MultiMarkdown.",
    "inputSchema": _json_schema(
        {
            "path": {"type": "string", "description": "Local filing path."},
            "to_mmd": {"type": "boolean", "default": True},
            "disable_indentation": {"type": "boolean", "default": False},
            "source_document_url": {"type": "string", "description": "Optional URL used to resolve relative links."},
        },
        ["path"],
    ),
}

RENDER_TOOL: dict[str, Any] = {
    "name": "render_markdown",
    "description": "Render a parsed Markdown/MultiMarkdown file to PDF with the repo's Node helpers.",
    "inputSchema": _json_schema(
        {
            "markdown_path": {"type": "string"},
            "pdf_path": {"type": "string", "description": "Optional output PDF path."},
        },
        ["markdown_path"],
    ),
}

CHECK_TOOL: dict[str, Any] = {
    "name": "check_showcase_tables",
    "description": "Run static checks for silent table-fragment bugs in parsed showcase Markdown.",
    "inputSchema": _json_schema(
        {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "default": ["examples"],
            }
        }
    ),
}

REVIEW_TOOL: dict[str, Any] = {
    "name": "review_snippet",
    "description": "Create a side-by-side raw-browser vs parsed-Markdown review snippet for a needle.",
    "inputSchema": _json_schema(
        {
            "example": {"type": "string", "description": "Example accession directory or path."},
            "needle": {"type": "string", "description": "Text to locate in raw and parsed output."},
            "output_dir": {"type": "string", "description": "Optional scratch output directory."},
        },
        ["example", "needle"],
    ),
}


def _has_repo_render_helpers() -> bool:
    return (ROOT / "multimarkdown.js").is_file() and (ROOT / "html-to-pdf.mjs").is_file()


def _has_repo_check_helper() -> bool:
    return (ROOT / "tools" / "check_showcase_tables.py").is_file()


def _has_repo_review_helper() -> bool:
    return (ROOT / "tools" / "review_snippet.py").is_file()


def _available_tools() -> list[dict[str, Any]]:
    tools = [PARSE_TOOL]
    if _has_repo_render_helpers():
        tools.append(RENDER_TOOL)
    if _has_repo_check_helper():
        tools.append(CHECK_TOOL)
    if _has_repo_review_helper():
        tools.append(REVIEW_TOOL)
    return tools


KNOWN_TOOL_NAMES = {
    "parse_filing",
    "render_markdown",
    "check_showcase_tables",
    "review_snippet",
}


def _tool_unavailable(name: str) -> RuntimeError:
    return RuntimeError(
        f"{name} is only available when running from a full repository clone "
        "with the repo helper scripts present. Package installs expose parse_filing."
    )


def _text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _path(value: str) -> pathlib.Path:
    return pathlib.Path(value).expanduser().resolve()


def _parse_filing(args: dict[str, Any]) -> str:
    path = _path(args["path"])
    if not path.is_file():
        raise FileNotFoundError(f"Filing not found: {path}")

    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        main_one(
            path,
            to_mmd=bool(args.get("to_mmd", True)),
            source_document_url=args.get("source_document_url") or None,
            disable_indentation=bool(args.get("disable_indentation", False)),
        )

    output_path = path.with_suffix(".md")
    parts = [f"output_path: {output_path}"]
    stats_path = output_path.with_suffix(".parse_stats.json")
    if stats_path.exists():
        parts.append(f"parse_stats_path: {stats_path}")
    if stdout.getvalue().strip():
        parts.append("\nstdout:\n" + stdout.getvalue().strip())
    if stderr.getvalue().strip():
        parts.append("\nstderr:\n" + stderr.getvalue().strip())
    return "\n".join(parts)


def _render_markdown(args: dict[str, Any]) -> str:
    markdown_path = _path(args["markdown_path"])
    if not markdown_path.is_file():
        raise FileNotFoundError(f"Markdown file not found: {markdown_path}")

    pdf_path = _path(args["pdf_path"]) if args.get("pdf_path") else markdown_path.with_suffix(".pdf")
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as tmp:
        html_path = pathlib.Path(tmp.name)

    try:
        with html_path.open("w", encoding="utf-8") as html_out:
            subprocess.run(
                ["node", str(ROOT / "multimarkdown.js"), str(markdown_path)],
                cwd=ROOT,
                stdout=html_out,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
        completed = subprocess.run(
            ["node", str(ROOT / "html-to-pdf.mjs"), str(html_path), str(pdf_path)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    finally:
        with contextlib.suppress(FileNotFoundError):
            html_path.unlink()

    out = [f"pdf_path: {pdf_path}"]
    if completed.stdout.strip():
        out.append("\nstdout:\n" + completed.stdout.strip())
    if completed.stderr.strip():
        out.append("\nstderr:\n" + completed.stderr.strip())
    return "\n".join(out)


def _run_script(args: list[str]) -> str:
    completed = subprocess.run(
        args,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    parts = [f"exit_code: {completed.returncode}"]
    if completed.stdout.strip():
        parts.append("\nstdout:\n" + completed.stdout.strip())
    if completed.stderr.strip():
        parts.append("\nstderr:\n" + completed.stderr.strip())
    return "\n".join(parts)


def _check_showcase_tables(args: dict[str, Any]) -> str:
    paths = args.get("paths") or ["examples"]
    return _run_script([sys.executable, str(ROOT / "tools" / "check_showcase_tables.py"), *map(str, paths)])


def _review_snippet(args: dict[str, Any]) -> str:
    cmd = [
        sys.executable,
        str(ROOT / "tools" / "review_snippet.py"),
        str(args["example"]),
        str(args["needle"]),
    ]
    if args.get("output_dir"):
        cmd.extend(["--output-dir", str(args["output_dir"])])
    return _run_script(cmd)


def _call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    handlers = {"parse_filing": _parse_filing}
    if _has_repo_render_helpers():
        handlers["render_markdown"] = _render_markdown
    if _has_repo_check_helper():
        handlers["check_showcase_tables"] = _check_showcase_tables
    if _has_repo_review_helper():
        handlers["review_snippet"] = _review_snippet
    if name not in handlers:
        if name in KNOWN_TOOL_NAMES:
            raise _tool_unavailable(name)
        raise ValueError(f"Unknown tool: {name}")
    return _text_result(handlers[name](args))


def _response(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")

    if method == "initialize":
        return _response(
            message_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stanford-edgar-parser", "version": "0.1.2"},
                "instructions": (
                    "Use parse_filing to convert local SEC submissions into layout-faithful "
                    "Markdown/MultiMarkdown. When visual fidelity matters, compare parsed output "
                    "against the raw browser rendering, especially tables, indentation, links, "
                    "currency/percent modifiers, accounting parentheses, and image placeholders. "
                    "Repo-only render/review tools are exposed only when helper scripts are present."
                ),
            },
        )
    if method == "tools/list":
        return _response(message_id, {"tools": _available_tools()})
    if method == "tools/call":
        params = message.get("params") or {}
        return _response(message_id, _call_tool(params.get("name"), params.get("arguments") or {}))
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method in {"resources/list", "prompts/list"}:
        return _response(message_id, {"resources": []} if method == "resources/list" else {"prompts": []})
    return _error(message_id, -32601, f"Method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        message_id: Any = None
        try:
            message = json.loads(line)
            message_id = message.get("id")
            response = _handle(message)
        except Exception as exc:  # MCP clients need JSON errors, not tracebacks on stdout.
            response = _error(message_id, -32603, f"{exc}\n{traceback.format_exc(limit=5)}")
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
