"""Install agent-facing Stanford EDGAR parser skills.

This mirrors the user-facing pattern used by agent-aware Python packages:

    from stanford_edgar_parser.ai import install_skill
    install_skill()

The function copies bundled skill instructions into local Codex and/or Claude
skill directories. It does not configure MCP automatically because MCP config
locations differ by client.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
from importlib import resources
from typing import Iterable


SKILL_NAME = "stanford-edgar-parser"


def _asset_root() -> pathlib.Path:
    return pathlib.Path(str(resources.files("stanford_edgar_parser").joinpath("agent_assets")))


def _codex_skill_root(codex_dir: str | os.PathLike[str] | None = None) -> pathlib.Path:
    if codex_dir:
        return pathlib.Path(codex_dir).expanduser()
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return pathlib.Path(codex_home).expanduser() / "skills"
    dot_codex = pathlib.Path.home() / ".codex"
    if dot_codex.exists():
        return dot_codex / "skills"
    return pathlib.Path.home() / ".agents" / "skills"


def _claude_skill_root(claude_dir: str | os.PathLike[str] | None = None) -> pathlib.Path:
    if claude_dir:
        return pathlib.Path(claude_dir).expanduser()
    return pathlib.Path.home() / ".claude" / "skills"


def _copy_skill(src: pathlib.Path, dst_root: pathlib.Path, overwrite: bool) -> str:
    if not src.is_dir():
        raise FileNotFoundError(f"Bundled skill asset not found: {src}")
    dst_root.mkdir(parents=True, exist_ok=True)
    dst = dst_root / SKILL_NAME
    if dst.exists():
        if not overwrite:
            return f"exists: {dst}"
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return f"installed: {dst}"


def install_skill(
    targets: Iterable[str] = ("codex", "claude"),
    *,
    overwrite: bool = False,
    codex_dir: str | os.PathLike[str] | None = None,
    claude_dir: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Install bundled Codex and/or Claude skills.

    Parameters
    ----------
    targets:
        Iterable containing ``"codex"``, ``"claude"``, or both.
    overwrite:
        Replace an existing installed skill directory when true.
    codex_dir:
        Optional target skills directory. Defaults to ``$CODEX_HOME/skills`` if
        set, else ``~/.codex/skills`` when present, else ``~/.agents/skills``.
    claude_dir:
        Optional target skills directory. Defaults to ``~/.claude/skills``.
    """

    normalized = {target.lower() for target in targets}
    unknown = normalized - {"codex", "claude"}
    if unknown:
        raise ValueError(f"Unknown skill target(s): {', '.join(sorted(unknown))}")

    assets = _asset_root()
    results: dict[str, str] = {}
    if "codex" in normalized:
        results["codex"] = _copy_skill(assets / "codex" / SKILL_NAME, _codex_skill_root(codex_dir), overwrite)
    if "claude" in normalized:
        results["claude"] = _copy_skill(assets / "claude" / SKILL_NAME, _claude_skill_root(claude_dir), overwrite)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Stanford EDGAR parser skills for agent clients.")
    parser.add_argument(
        "--target",
        choices=["all", "codex", "claude"],
        default="all",
        help="Which skill target to install.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing installed skill directory.")
    parser.add_argument("--codex-dir", help="Override Codex skills directory.")
    parser.add_argument("--claude-dir", help="Override Claude skills directory.")
    args = parser.parse_args(argv)

    targets = ("codex", "claude") if args.target == "all" else (args.target,)
    result = install_skill(
        targets,
        overwrite=args.overwrite,
        codex_dir=args.codex_dir,
        claude_dir=args.claude_dir,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
