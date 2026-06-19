from __future__ import annotations
import stanford_edgar_parser._state as _state

from stanford_edgar_parser.utils.bootstrap import (
    Any,
    Dict,
    List,
    Mistral,
    Optional,
    datetime,
    fcntl,
    hashlib,
    json,
    os,
    pathlib,
    re,
    time,
)
from stanford_edgar_parser.utils.tokenizer import estimate_parser_tokens

CP1252_CTRL_TO_UNICODE = str.maketrans({
    "\x80": "\u20AC", "\x82": "\u201A", "\x83": "\u0192", "\x84": "\u201E",
    "\x85": "\u2026", "\x86": "\u2020", "\x87": "\u2021", "\x88": "\u02C6",
    "\x89": "\u2030", "\x8A": "\u0160", "\x8B": "\u2039", "\x8C": "\u0152",
    "\x8E": "\u017D", "\x91": "\u2018", "\x92": "\u2019", "\x93": "\u201C",
    "\x94": "\u201D", "\x95": "\u2022", "\x96": "\u2013", "\x97": "\u2014",
    "\x98": "\u02DC", "\x99": "\u2122", "\x9A": "\u0161", "\x9B": "\u203A",
    "\x9C": "\u0153", "\x9E": "\u017E", "\x9F": "\u0178",
})

PUNCT_CANON = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\uFF07": "'", "\u2032": "'",
    "\u2010": "-", "\u2011": "-", "\u2212": "-",
})

OCR_API_URL = "https://api.mistral.ai/v1/ocr"
MISTRAL_KEY_STATUS_PATH = pathlib.Path(
    os.getenv("SEC_PARSER_MISTRAL_KEY_STATUS_PATH", "").strip()
    or (pathlib.Path(__file__).resolve().parents[2] / "mistral_key_status.json")
)
MISTRAL_KEY_LOCK_PATH = MISTRAL_KEY_STATUS_PATH.with_suffix(MISTRAL_KEY_STATUS_PATH.suffix + ".lock")
MISTRAL_KEY_ENV_LIMIT = 20
MISTRAL_KEY_STATE_VERSION = 1
MISTRAL_MONTHLY_TOKEN_BUDGET_ESTIMATE = 1_000_000_000
MISTRAL_QUOTA_ERROR_HINTS = (
    "quota",
    "credit",
    "credits",
    "billing",
    "monthly",
    "free tier",
    "experiment plan",
    "token limit",
    "tokens per month",
    "out of tokens",
    "usage limit",
    "insufficient",
)
MISTRAL_INVALID_KEY_HINTS = (
    "invalid api key",
    "incorrect api key",
    "authentication",
    "unauthorized",
    "forbidden",
    "revoked",
)


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _current_usage_month_label() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def _parse_iso_timestamp(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value).timestamp()
    except Exception:
        return None


def _mistral_key_fingerprint(api_key: str) -> str:
    digest = hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()
    return digest[:12]


def _mistral_env_name_for_index(index: int) -> str:
    return "MISTRAL_API_KEY" if index == 1 else f"MISTRAL_API_KEY{index}"


def _configured_mistral_key_specs(explicit_api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    specs: List[Dict[str, Any]] = []
    seen_keys = set()
    for idx in range(1, MISTRAL_KEY_ENV_LIMIT + 1):
        env_name = _mistral_env_name_for_index(idx)
        api_key = (os.getenv(env_name) or "").strip()
        if not api_key or api_key in seen_keys:
            continue
        seen_keys.add(api_key)
        specs.append(
            {
                "env_name": env_name,
                "ordinal": idx,
                "api_key": api_key,
                "fingerprint": _mistral_key_fingerprint(api_key),
            }
        )

    explicit_api_key = (explicit_api_key or "").strip()
    if explicit_api_key and explicit_api_key not in seen_keys:
        specs.insert(
            0,
            {
                "env_name": "MISTRAL_API_KEY",
                "ordinal": 1,
                "api_key": explicit_api_key,
                "fingerprint": _mistral_key_fingerprint(explicit_api_key),
            },
        )
    return specs


def _has_mistral_api_keys(explicit_api_key: Optional[str] = None) -> bool:
    return bool(_configured_mistral_key_specs(explicit_api_key=explicit_api_key))


def _mistral_no_keys_message() -> str:
    return "No Mistral API keys found in environment variables MISTRAL_API_KEY through MISTRAL_API_KEY20."


def _default_mistral_key_record(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "env_name": spec["env_name"],
        "ordinal": int(spec["ordinal"]),
        "fingerprint": spec["fingerprint"],
        "status": "available",
        "request_count": 0,
        "success_count": 0,
        "pages_processed": 0,
        "doc_size_bytes": 0,
        "estimated_output_tokens": 0,
        "last_selected_at": None,
        "last_success_at": None,
        "last_error_at": None,
        "last_error": None,
        "last_error_status_code": None,
        "last_rate_limit_headers": {},
        "cooldown_until": None,
        "exhausted_at": None,
    }


def _normalize_mistral_key_state(raw_state: Optional[Dict[str, Any]], key_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    state = dict(raw_state or {})
    state_usage_month = str(state.get("usage_month") or "").strip()
    current_usage_month = _current_usage_month_label()
    monthly_reset = state_usage_month != current_usage_month
    keys_state = {} if monthly_reset else dict(state.get("keys") or {})
    normalized_keys: Dict[str, Dict[str, Any]] = {}
    for spec in key_specs:
        record = dict(keys_state.get(spec["env_name"]) or {})
        default_record = _default_mistral_key_record(spec)
        default_record.update(record)
        default_record["env_name"] = spec["env_name"]
        default_record["ordinal"] = int(spec["ordinal"])
        default_record["fingerprint"] = spec["fingerprint"]
        normalized_keys[spec["env_name"]] = default_record

    state = {
        "version": MISTRAL_KEY_STATE_VERSION,
        "usage_month": current_usage_month,
        "updated_at": _utc_now_iso(),
        "active_env_name": state.get("active_env_name"),
        "keys": normalized_keys,
    }
    if state["active_env_name"] not in normalized_keys:
        state["active_env_name"] = None
    return state


def _read_mistral_key_state_unlocked(key_specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if MISTRAL_KEY_STATUS_PATH.exists():
        try:
            raw_state = json.loads(MISTRAL_KEY_STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw_state = {}
    else:
        raw_state = {}
    return _normalize_mistral_key_state(raw_state, key_specs)


def _write_mistral_key_state_unlocked(state: Dict[str, Any]) -> None:
    MISTRAL_KEY_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = MISTRAL_KEY_STATUS_PATH.with_suffix(MISTRAL_KEY_STATUS_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(MISTRAL_KEY_STATUS_PATH)


class _LockedMistralKeyState:
    def __init__(self, key_specs: List[Dict[str, Any]]):
        self.key_specs = key_specs
        self.handle = None
        self.state = None

    def __enter__(self) -> Dict[str, Any]:
        MISTRAL_KEY_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(MISTRAL_KEY_LOCK_PATH, "a+", encoding="utf-8")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        self.state = _read_mistral_key_state_unlocked(self.key_specs)
        return self.state

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.state is not None:
                self.state["updated_at"] = _utc_now_iso()
                _write_mistral_key_state_unlocked(self.state)
        finally:
            if self.handle is not None:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                self.handle.close()


def _locked_mistral_key_state(key_specs: List[Dict[str, Any]]) -> _LockedMistralKeyState:
    return _LockedMistralKeyState(key_specs)


def _select_mistral_key_spec(explicit_api_key: Optional[str] = None) -> Dict[str, Any]:
    key_specs = _configured_mistral_key_specs(explicit_api_key=explicit_api_key)
    if not key_specs:
        raise RuntimeError(_mistral_no_keys_message())

    lookup = {spec["env_name"]: spec for spec in key_specs}
    now_ts = time.time()
    with _locked_mistral_key_state(key_specs) as state:
        available: List[Tuple[int, Dict[str, Any]]] = []
        cooling: List[Tuple[float, int, Dict[str, Any]]] = []

        for spec in key_specs:
            record = state["keys"][spec["env_name"]]
            if record.get("status") in {"exhausted", "invalid"}:
                continue
            cooldown_until_ts = _parse_iso_timestamp(record.get("cooldown_until"))
            if cooldown_until_ts and cooldown_until_ts > now_ts:
                cooling.append((cooldown_until_ts, spec["ordinal"], record))
            else:
                available.append((spec["ordinal"], record))

        chosen_record: Optional[Dict[str, Any]] = None
        if available:
            chosen_record = sorted(available, key=lambda item: item[0])[0][1]
        elif cooling:
            chosen_record = sorted(cooling, key=lambda item: (item[0], item[1]))[0][2]

        if not chosen_record:
            raise RuntimeError(
                "All configured Mistral API keys are exhausted or unavailable; stopping after MISTRAL_API_KEY20."
            )

        chosen_record["last_selected_at"] = _utc_now_iso()
        chosen_record["request_count"] = int(chosen_record.get("request_count") or 0) + 1
        state["active_env_name"] = chosen_record["env_name"]
        spec = dict(lookup[chosen_record["env_name"]])
        spec["status"] = chosen_record.get("status") or "available"
        return spec


def _record_mistral_key_success(env_name: str, usage: Optional[Dict[str, Any]] = None, explicit_api_key: Optional[str] = None) -> None:
    key_specs = _configured_mistral_key_specs(explicit_api_key=explicit_api_key)
    if not key_specs:
        return
    usage = dict(usage or {})
    with _locked_mistral_key_state(key_specs) as state:
        record = state["keys"].get(env_name)
        if not record:
            return
        record["status"] = "available"
        record["cooldown_until"] = None
        record["success_count"] = int(record.get("success_count") or 0) + 1
        record["pages_processed"] = int(record.get("pages_processed") or 0) + int(usage.get("pages_processed") or 0)
        record["doc_size_bytes"] = int(record.get("doc_size_bytes") or 0) + int(usage.get("doc_size_bytes") or 0)
        record["estimated_output_tokens"] = int(record.get("estimated_output_tokens") or 0) + int(usage.get("estimated_output_tokens") or 0)
        if usage.get("rate_limit_headers"):
            record["last_rate_limit_headers"] = dict(usage["rate_limit_headers"])
        record["last_success_at"] = _utc_now_iso()


def _record_mistral_key_terminal_error(
    env_name: str,
    *,
    status: str,
    message: str,
    status_code: Optional[int],
    explicit_api_key: Optional[str] = None,
) -> None:
    key_specs = _configured_mistral_key_specs(explicit_api_key=explicit_api_key)
    if not key_specs:
        return
    now_iso = _utc_now_iso()
    with _locked_mistral_key_state(key_specs) as state:
        record = state["keys"].get(env_name)
        if not record:
            return
        record["status"] = status
        record["last_error"] = (message or "")[:1000]
        record["last_error_status_code"] = status_code
        record["last_error_at"] = now_iso
        record["cooldown_until"] = None
        if status == "exhausted":
            record["exhausted_at"] = now_iso


def _record_mistral_key_cooldown(
    env_name: str,
    *,
    message: str,
    status_code: Optional[int],
    cooldown_seconds: float,
    explicit_api_key: Optional[str] = None,
) -> None:
    key_specs = _configured_mistral_key_specs(explicit_api_key=explicit_api_key)
    if not key_specs:
        return
    cooldown_until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=max(1.0, float(cooldown_seconds)))
    with _locked_mistral_key_state(key_specs) as state:
        record = state["keys"].get(env_name)
        if not record:
            return
        record["status"] = "available"
        record["cooldown_until"] = cooldown_until.isoformat()
        record["last_error"] = (message or "")[:1000]
        record["last_error_status_code"] = status_code
        record["last_error_at"] = _utc_now_iso()


def _extract_rate_limit_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in dict(headers or {}).items():
        key_str = str(key)
        if key_str.lower().startswith("x-ratelimit") or key_str.lower() == "retry-after":
            out[key_str] = value
    return out


def _extract_retry_after_seconds(headers: Optional[Dict[str, Any]], default_seconds: float = 60.0) -> float:
    headers = dict(headers or {})
    raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return default_seconds
    try:
        return max(1.0, float(raw))
    except Exception:
        return default_seconds


def _extract_mistral_error_details(exc: Exception) -> Dict[str, Any]:
    status_code = getattr(exc, "status_code", None)
    response = getattr(exc, "response", None)
    headers: Dict[str, Any] = {}
    message_parts = [str(exc)]

    if response is not None:
        status_code = getattr(response, "status_code", status_code)
        headers = dict(getattr(response, "headers", {}) or {})
        try:
            response_text = response.text
        except Exception:
            response_text = ""
        if response_text:
            message_parts.append(response_text)
        try:
            response_json = response.json()
        except Exception:
            response_json = None
        if response_json:
            message_parts.append(json.dumps(response_json, ensure_ascii=False))

    for attr_name in ("body", "response_body", "message"):
        attr_value = getattr(exc, attr_name, None)
        if attr_value:
            if not isinstance(attr_value, str):
                try:
                    attr_value = json.dumps(attr_value, ensure_ascii=False)
                except Exception:
                    attr_value = str(attr_value)
            message_parts.append(attr_value)

    message = " ".join(part.strip() for part in message_parts if str(part).strip())
    message = re.sub(r"\s+", " ", message).strip()
    return {
        "status_code": status_code,
        "message": message[:2000],
        "rate_limit_headers": _extract_rate_limit_headers(headers),
        "retry_after_seconds": _extract_retry_after_seconds(headers),
    }


def _classify_mistral_exception(exc: Exception) -> Dict[str, Any]:
    details = _extract_mistral_error_details(exc)
    status_code = details["status_code"]
    message = (details["message"] or "").lower()

    if status_code == 429 and any(hint in message for hint in MISTRAL_QUOTA_ERROR_HINTS):
        details["action"] = "exhausted"
    elif status_code in {402, 403} and any(hint in message for hint in MISTRAL_QUOTA_ERROR_HINTS):
        details["action"] = "exhausted"
    elif status_code == 401 or any(hint in message for hint in MISTRAL_INVALID_KEY_HINTS):
        details["action"] = "invalid"
    elif status_code == 403:
        details["action"] = "invalid"
    elif status_code == 429:
        details["action"] = "cooldown"
    else:
        details["action"] = "raise"
    return details


def _summarize_ocr_usage(ocr_data: Optional[Dict[str, Any]], response_headers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from stanford_edgar_parser.parsers.ocr.ocr_utils import _normalize_ocr_text

    ocr_data = dict(ocr_data or {})
    usage_info = dict(ocr_data.get("usage_info") or {})
    pages = list(ocr_data.get("pages") or [])
    estimated_output_tokens = 0
    for page_obj in pages:
        estimated_output_tokens += estimate_parser_tokens(
            _normalize_ocr_text((page_obj.get("markdown") or page_obj.get("text") or "").strip())
        )
    pages_processed = usage_info.get("pages_processed")
    if pages_processed is None:
        pages_processed = len(pages)
    return {
        "pages_processed": int(pages_processed or 0),
        "doc_size_bytes": int(usage_info.get("doc_size_bytes") or 0),
        "estimated_output_tokens": int(estimated_output_tokens),
        "rate_limit_headers": _extract_rate_limit_headers(response_headers),
    }


def _run_with_mistral_key_rotation(
    operation_label: str,
    func,
    *,
    explicit_api_key: Optional[str] = None,
):
    key_specs = _configured_mistral_key_specs(explicit_api_key=explicit_api_key)
    if not key_specs:
        raise RuntimeError(_mistral_no_keys_message())

    max_attempts = max(1, len(key_specs) * 2)
    last_exc: Optional[Exception] = None

    for _ in range(max_attempts):
        key_spec = _select_mistral_key_spec(explicit_api_key=explicit_api_key)
        key_label = f"{key_spec['env_name']}[{key_spec['fingerprint']}]"
        try:
            client = Mistral(api_key=key_spec["api_key"])
            return func(client=client, api_key=key_spec["api_key"], key_spec=key_spec)
        except Exception as exc:
            decision = _classify_mistral_exception(exc)
            last_exc = exc
            if decision["action"] == "exhausted":
                print(f"[mistral] {key_label} exhausted during {operation_label}; switching to the next configured key.")
                _record_mistral_key_terminal_error(
                    key_spec["env_name"],
                    status="exhausted",
                    message=decision["message"],
                    status_code=decision["status_code"],
                    explicit_api_key=explicit_api_key,
                )
                continue
            if decision["action"] == "invalid":
                print(f"[mistral] {key_label} was rejected during {operation_label}; switching to the next configured key.")
                _record_mistral_key_terminal_error(
                    key_spec["env_name"],
                    status="invalid",
                    message=decision["message"],
                    status_code=decision["status_code"],
                    explicit_api_key=explicit_api_key,
                )
                continue
            if decision["action"] == "cooldown" and len(key_specs) > 1:
                cooldown_seconds = float(decision.get("retry_after_seconds") or 60.0)
                print(
                    f"[mistral] {key_label} hit a rate limit during {operation_label}; cooling it down for "
                    f"{cooldown_seconds:.0f}s and trying the next key."
                )
                _record_mistral_key_cooldown(
                    key_spec["env_name"],
                    message=decision["message"],
                    status_code=decision["status_code"],
                    cooldown_seconds=cooldown_seconds,
                    explicit_api_key=explicit_api_key,
                )
                continue
            raise

    raise RuntimeError(
        "All configured Mistral API keys are exhausted or unavailable; stopping after MISTRAL_API_KEY20."
    ) from last_exc


def get_mistral_key_status_snapshot(explicit_api_key: Optional[str] = None) -> Dict[str, Any]:
    key_specs = _configured_mistral_key_specs(explicit_api_key=explicit_api_key)
    with _locked_mistral_key_state(key_specs) as state:
        snapshot = json.loads(json.dumps(state))
    now_ts = time.time()
    snapshot["status_path"] = str(MISTRAL_KEY_STATUS_PATH)
    snapshot["configured_key_count"] = len(key_specs)
    snapshot["available_key_count"] = 0
    snapshot["exhausted_key_count"] = 0

    for record in snapshot.get("keys", {}).values():
        cooldown_until_ts = _parse_iso_timestamp(record.get("cooldown_until"))
        record["cooldown_remaining_s"] = (
            max(0.0, round(cooldown_until_ts - now_ts, 3)) if cooldown_until_ts and cooldown_until_ts > now_ts else 0.0
        )
        if record.get("status") == "exhausted":
            snapshot["exhausted_key_count"] += 1
        elif record.get("status") != "invalid":
            snapshot["available_key_count"] += 1
    return snapshot


def reset_mistral_key_status(explicit_api_key: Optional[str] = None) -> Dict[str, Any]:
    key_specs = _configured_mistral_key_specs(explicit_api_key=explicit_api_key)
    with _locked_mistral_key_state(key_specs) as state:
        fresh_state = _normalize_mistral_key_state({}, key_specs)
        state.clear()
        state.update(fresh_state)
        snapshot = json.loads(json.dumps(state))
    snapshot["status_path"] = str(MISTRAL_KEY_STATUS_PATH)
    return snapshot


def _estimate_monthly_mistral_tokens_used(explicit_api_key: Optional[str] = None) -> int:
    snapshot = get_mistral_key_status_snapshot(explicit_api_key=explicit_api_key)
    total = 0
    for record in (snapshot.get("keys") or {}).values():
        total += int(record.get("estimated_output_tokens") or 0)
    return total


def _print_mistral_monthly_usage(
    phase: str,
    file_name: str,
    *,
    explicit_api_key: Optional[str] = None,
) -> None:
    used_tokens = _estimate_monthly_mistral_tokens_used(explicit_api_key=explicit_api_key)
    print(
        f"[mistral-usage] {phase} PDF '{file_name}': "
        f"{used_tokens:,}/{MISTRAL_MONTHLY_TOKEN_BUDGET_ESTIMATE:,} estimated output tokens used this month"
    )

__all__ = [name for name in globals() if not name.startswith("__")]
