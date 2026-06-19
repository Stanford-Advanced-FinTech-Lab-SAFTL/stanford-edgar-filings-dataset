from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.utils.bootstrap import (
    Any,
    Dict,
    Optional,
    os,
    pathlib,
    re,
)

_TOKENIZER = None
_TOKENIZER_KEY = None
_TOKENIZER_KIND = None

def _requested_tokenizer() -> str:
    return os.getenv("SEC_PARSER_TOKENIZER", "cl100k_base").strip() or "cl100k_base"


def _resolve_hf_tokenizer_name(name: str) -> str:
    lowered = name.strip().lower()
    aliases = {
        "qwen3-1.7b": "Qwen/Qwen3-1.7B",
        "qwen/qwen3-1.7b": "Qwen/Qwen3-1.7B",
        "qwen3_1.7b": "Qwen/Qwen3-1.7B",
    }
    if lowered.startswith("hf:"):
        return name.split(":", 1)[1]
    resolved = aliases.get(lowered, name)
    if os.getenv("SEC_PARSER_TOKENIZER_FORCE_HUB", "0").strip().lower() in {"1", "true", "yes", "on"}:
        return resolved
    if "/" in resolved:
        cached = _cached_hf_snapshot(resolved)
        if cached is not None:
            return str(cached)
    return resolved


def _cached_hf_snapshot(repo_id: str) -> Optional[pathlib.Path]:
    cache_root = pathlib.Path(os.getenv("HF_HOME", pathlib.Path.home() / ".cache" / "huggingface")) / "hub"
    repo_cache = cache_root / ("models--" + repo_id.replace("/", "--"))
    refs_main = repo_cache / "refs" / "main"
    snapshot_root = repo_cache / "snapshots"
    candidates: List[pathlib.Path] = []
    try:
        if refs_main.exists():
            candidates.append(snapshot_root / refs_main.read_text(encoding="utf-8").strip())
        if snapshot_root.exists():
            candidates.extend(sorted(path for path in snapshot_root.iterdir() if path.is_dir()))
    except Exception:
        return None
    for candidate in candidates:
        if (candidate / "tokenizer.json").exists() or (candidate / "vocab.json").exists():
            return candidate
    return None


def _tokenizer_metadata() -> Dict[str, Any]:
    return {
        "tokenizer": _requested_tokenizer(),
        "tokenizer_resolved": _resolve_hf_tokenizer_name(_requested_tokenizer()),
        "tokenizer_kind": _TOKENIZER_KIND or "unloaded",
        "add_special_tokens": False,
    }


def _is_debug_enabled() -> bool:
    return os.getenv("SEC_PARSER_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}


def _debug_print(*args, **kwargs) -> None:
    if _is_debug_enabled():
        print(*args, **kwargs)


def estimate_parser_tokens(text: str) -> int:
    """Count output tokens with the configured tokenizer, falling back to a stable approximation."""
    global _TOKENIZER, _TOKENIZER_KEY, _TOKENIZER_KIND
    text = text or ""
    if not text:
        return 0
    tokenizer_name = _requested_tokenizer()
    if _TOKENIZER is None or _TOKENIZER_KEY != tokenizer_name:
        _TOKENIZER_KEY = tokenizer_name
        lowered = tokenizer_name.lower()
        try:
            if lowered in {"cl100k_base", "tiktoken:cl100k_base"}:
                import tiktoken

                _TOKENIZER = tiktoken.get_encoding("cl100k_base")
                _TOKENIZER_KIND = "tiktoken"
            else:
                from transformers import AutoTokenizer

                resolved = _resolve_hf_tokenizer_name(tokenizer_name)
                revision = os.getenv("SEC_PARSER_TOKENIZER_REVISION") or None
                _TOKENIZER = AutoTokenizer.from_pretrained(resolved, revision=revision)
                _TOKENIZER_KIND = "huggingface"
        except Exception as exc:
            if lowered not in {"cl100k_base", "tiktoken:cl100k_base"}:
                raise RuntimeError(f"Could not load SEC_PARSER_TOKENIZER={tokenizer_name!r}") from exc
            _TOKENIZER = False
            _TOKENIZER_KIND = "regex_fallback"
    if _TOKENIZER:
        try:
            if _TOKENIZER_KIND == "tiktoken":
                return len(_TOKENIZER.encode(text))
            return len(_TOKENIZER.encode(text, add_special_tokens=False))
        except Exception:
            pass
    return max(1, len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)))


def normalize_form_type_for_stats(form_type: str) -> str:
    form = (form_type or "unknown").strip().upper()
    if not form:
        return "unknown"
    base = form[:-2] if form.endswith("/A") else form
    compact = re.sub(r"[^A-Z0-9]", "", base)
    if compact in {"8K", "10K", "10Q"}:
        return compact.lower()
    if compact.startswith("NPORT"):
        return "nport"
    if compact.startswith("NCEN"):
        return "ncen"
    if compact.startswith("NPX"):
        return "npx"
    if compact.startswith("NMFP"):
        return "nmfp"
    if compact.startswith("13F"):
        return "13f"
    if compact in {"3", "4", "5"}:
        return compact
    return compact.lower() or "unknown"

__all__ = [name for name in globals() if not name.startswith("__")]
