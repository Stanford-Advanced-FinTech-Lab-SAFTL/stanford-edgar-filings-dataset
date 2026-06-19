# Stanford EDGAR Filings Dataset Parser

[![PyPI](https://img.shields.io/pypi/v/stanford-edgar-parser.svg)](https://pypi.org/project/stanford-edgar-parser/)
[![Python](https://img.shields.io/pypi/pyversions/stanford-edgar-parser.svg)](https://pypi.org/project/stanford-edgar-parser/)
[![Publish](https://github.com/Stanford-Advanced-FinTech-Lab-SAFTL/stanford-edgar-filings-dataset/actions/workflows/publish.yml/badge.svg?event=push)](https://github.com/Stanford-Advanced-FinTech-Lab-SAFTL/stanford-edgar-filings-dataset/actions/workflows/publish.yml)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![arXiv](https://img.shields.io/badge/arXiv-2606.18192-b31b1b.svg)](https://arxiv.org/abs/2606.18192)
[![Dataset](https://img.shields.io/badge/%F0%9F%A4%97%20Dataset-SEFD--v1-yellow.svg)](https://huggingface.co/datasets/anonymous-md/EDGAR_FILINGS_DATASET)

The Stanford EDGAR Filings Dataset (SEFD) is a 550B-token reconstruction of 18.5M SEC filings into layout-faithful MultiMarkdown for LLM pretraining, financial reasoning, document understanding, and evaluation.

SEFD reverse-engineers EDGAR's heterogeneous disclosure formats into a token-efficient representation that preserves financial tables, indentation, merged headers, numeric signs, currency and percent symbols, document hierarchy, and other layout cues that carry financial meaning. Internal validation shows our rule-based reconstruction methodology achieves greater than 99% structural and semantic accuracy on sampled outputs.

The software routes and parses the full EDGAR source-format surface, including legacy fixed-width text, tag-soup HTML, SGML wrappers, XML submissions, and PDF attachments, with specialized reconstruction for more than 30 SEC XML schemas, including Forms 3, 4, 5, D, 13D/G, N-PX, N-PORT, N-CEN, 13F, 144, ATS-N, 1-A/K/Z, C, MA, TA, X-17A-5, 24F-2NT, ABS-EE, and related amendments, withdrawals, and corrections. PDF attachments are parsed with Mistral OCR 3.

Our first public release, SEFD-v1, is a 152B-token dataset covering filings from January 2022 through June 2025.

- Paper: [The Stanford EDGAR Filings Dataset](https://arxiv.org/abs/2606.18192)
- Dataset: [SEFD-v1 on Hugging Face](https://huggingface.co/datasets/anonymous-md/EDGAR_FILINGS_DATASET)

## Install

From PyPI:

```bash
pip install stanford-edgar-parser
```

Agent-friendly install alias:

```bash
pip install "stanford-edgar-parser[ai]"
```

Or directly from GitHub:

```bash
pip install "stanford-edgar-parser[ai] @ git+https://github.com/Stanford-Advanced-FinTech-Lab-SAFTL/stanford-edgar-filings-dataset.git"
```

## Usage

Parse a local filing and convert tables to MultiMarkdown:

```bash
stanford-edgar-parser path/to/filing.txt --to_mmd
```

or:

```bash
python -m stanford_edgar_parser path/to/filing.txt --to_mmd
```

Optional rendering helpers:

```bash
node multimarkdown.js path/to/file.md > file.html
node html-to-pdf.mjs file.html file.pdf
```

## Agent Setup

The `[ai]` extra is an agent-facing install alias. It currently uses the same runtime dependencies as the base package, but gives agent clients a stable target for MCP and skill setup.

### Codex

Install the Codex skill:

```bash
uvx --from "stanford-edgar-parser[ai]" stanford-edgar-install-skill --target codex --overwrite
```

Add the MCP server to `~/.codex/config.toml`:

```toml
[mcp_servers.stanford_edgar_parser]
command = "uvx"
args = ["--from", "stanford-edgar-parser[ai]", "stanford-edgar-mcp"]
startup_timeout_sec = 120
```

For full repo-local render/review tools, point Codex at a clone instead:

```toml
[mcp_servers.stanford_edgar_parser]
command = "python"
args = ["-m", "stanford_edgar_parser.mcp_server"]
cwd = "/path/to/stanford-edgar-filings-dataset"
startup_timeout_sec = 120
```

### Claude Code

Install the Claude skill:

```bash
uvx --from "stanford-edgar-parser[ai]" stanford-edgar-install-skill --target claude --overwrite
```

For project-local MCP, add this `.mcp.json` to your project or use the one included in this repository:

```json
{
  "mcpServers": {
    "stanford-edgar-parser": {
      "command": "uvx",
      "args": ["--from", "stanford-edgar-parser[ai]", "stanford-edgar-mcp"]
    }
  }
}
```

For repo-local render/review tools, use a clone-backed `.mcp.json`:

```json
{
  "mcpServers": {
    "stanford-edgar-parser": {
      "command": "python",
      "args": ["-m", "stanford_edgar_parser.mcp_server"],
      "cwd": "/path/to/stanford-edgar-filings-dataset"
    }
  }
}
```

### Claude Desktop

Add this to your Claude Desktop MCP config:

```json
{
  "mcpServers": {
    "stanford-edgar-parser": {
      "command": "uvx",
      "args": ["--from", "stanford-edgar-parser[ai]", "stanford-edgar-mcp"]
    }
  }
}
```

### Python Skill Installer

The same bundled skills can also be installed from Python:

```python
from stanford_edgar_parser.ai import install_skill

install_skill(targets=("codex", "claude"), overwrite=True)
```

### GitHub MCP Install

Use the GitHub source directly before a PyPI release:

```toml
[mcp_servers.stanford_edgar_parser]
command = "uvx"
args = [
  "--from",
  "stanford-edgar-parser[ai] @ git+https://github.com/Stanford-Advanced-FinTech-Lab-SAFTL/stanford-edgar-filings-dataset.git",
  "stanford-edgar-mcp"
]
startup_timeout_sec = 120
```

Package-installed MCP always exposes `parse_filing`. Repo-local rendering and review tools are exposed when the server runs from a full clone containing `multimarkdown.js`, `html-to-pdf.mjs`, and `tools/`.

## MCP Tools

The MCP server exposes:

- `parse_filing`: parse local SEC submissions to Markdown or MultiMarkdown
- `render_markdown`: render parsed Markdown to PDF when running from a full repo clone
- `check_showcase_tables`: run static table-integrity checks when running from a full repo clone
- `review_snippet`: build raw-browser-vs-parsed review snippets when running from a full repo clone

## Citation

```bibtex
@article{bettencourt2026stanfordedgar,
  title={The Stanford EDGAR Filings Dataset: Reconstructing U.S. Corporate and Financial Disclosures into Layout-Faithful and Token-Efficient Pretraining Data},
  author={Bettencourt, Nick and Ding, Xiaowei and Giesecke, Kay},
  journal={arXiv preprint arXiv:2606.18192},
  year={2026},
  url={https://arxiv.org/abs/2606.18192}
}
```
