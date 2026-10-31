"""Opt-in OpenAI-compatible LLM adapter, with no default network access.

Only the Core reads the endpoint and credential environment variable. The
public Plugin API exposes neither credentials nor HTTP-specific machinery.
"""
from __future__ import annotations

import json
import math
import os
import re
from ipaddress import ip_address
from threading import BoundedSemaphore, Event, Thread
from time import monotonic
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from cifereca.http_trace import BROWSER_USER_AGENT

from cifereca.plugin_api.llm import LLMRequest, LLMResult

# Timed-out calls do not spawn an unbounded number of worker threads.
_LLM_SLOTS = BoundedSemaphore(2)
_MAX_RESPONSE_BYTES = 32768
# max_input_chars is a user-facing character limit, but JSON escaping can
# expand Unicode text by several times. Bound the actual HTTP request too.
_MAX_REQUEST_BYTES = 65536


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _allowed_endpoint(endpoint: str, *, allow_remote: bool) -> tuple[bool, bool]:
    """(permitted, remote): never infer localness from arbitrary DNS."""
    try:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").lower()
        _ = parsed.port
    except (ValueError, UnicodeError):
        return False, False
    # Direct Core callers do not necessarily use the GTK normalization path.
    # Forbid invisible controls, bidi spoofing and invalid surrogates before
    # urllib can parse/encode an untrusted endpoint or disclose a destination.
    if (parsed.scheme not in {"http", "https"} or not host
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(
                ord(c) < 32 or ord(c) == 127 or 0x80 <= ord(c) <= 0x9F
                or ord(c) == 0x061C or ord(c) in (0x200E, 0x200F)
                or 0x202A <= ord(c) <= 0x202E
                or 0x2066 <= ord(c) <= 0x2069
                or 0xD800 <= ord(c) <= 0xDFFF
                for c in endpoint
            )):
        return False, False
    if host == "localhost":
        local = True
    else:
        try:
            local = ip_address(host).is_loopback
        except ValueError:
            local = False
    if local:
        return True, False
    return (bool(allow_remote and parsed.scheme == "https"), True)


def _strict_model_json(raw: bytes | str) -> Any:
    """Parse model output without silently accepting ambiguous JSON facts.

    The default json.loads accepts duplicate keys, NaN and Infinity. Those
    values cannot be safely treated as structured facts by plugin callers.
    """
    def no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        obj: dict[str, Any] = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate JSON key")
            obj[key] = value
        return obj

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("nonfinite JSON float")
        return number

    def finite_constant(_value: str) -> None:
        raise ValueError("nonfinite JSON constant")

    return json.loads(
        raw, object_pairs_hook=no_duplicates,
        parse_constant=finite_constant, parse_float=finite_float,
    )


def _safe_model_name(value: object) -> str:
    """Match the GTK settings safety boundary for direct/config-file callers.

    Do not silently truncate an overlong identifier: doing so would disclose
    one model in the approval preview but request a different one upstream.
    """
    if type(value) is not str or len(value) > 128 or any(
        ord(ch) < 32 or ord(ch) == 127 or 0x80 <= ord(ch) <= 0x9F
        or ord(ch) == 0x061C or ord(ch) in (0x200E, 0x200F)
        or 0x202A <= ord(ch) <= 0x202E
        or 0x2066 <= ord(ch) <= 0x2069
        or 0xD800 <= ord(ch) <= 0xDFFF
        for ch in value
    ):
        return ""
    return value.strip()


class OptionalLLMService:
    """One bounded generate()/structured-JSON transport, no implicit retries."""

    def __init__(self, config: Mapping[str, Any], *, opener=None):
        self._config = dict(config)
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect()).open

    def _settings(self) -> tuple[bool, bool, str, str, str, float, int, int]:
        cfg = self._config
        enabled = cfg.get("enabled") is True
        provided = cfg.get("provider") or "openai-compatible"
        # Never reflect arbitrary unsupported provider labels from a manually
        # edited config into diagnostics, consent UI or failure envelopes.
        provider = "openai-compatible" if provided == "openai-compatible" else "unsupported"
        model = _safe_model_name(cfg.get("model") or "")
        endpoint = str(cfg.get("endpoint") or "").strip()
        permit, remote = _allowed_endpoint(endpoint, allow_remote=cfg.get("allow_remote") is True)
        try:
            raw_timeout = float(cfg.get("timeout_seconds", 5))
            if not math.isfinite(raw_timeout):
                raise ValueError("non-finite timeout")
            timeout = min(15.0, max(0.2, raw_timeout))
            max_input = min(16000, max(1, int(cfg.get("max_input_chars", 4000))))
            max_tokens = min(1024, max(1, int(cfg.get("max_output_tokens", 256))))
        except (TypeError, ValueError, OverflowError):
            return False, False, provider, model, endpoint, 0.2, 0, 0
        valid = enabled and provider == "openai-compatible" and bool(model) and permit
        return valid, remote, provider, model, endpoint, timeout, max_input, max_tokens

    def destination(self) -> dict[str, str | bool]:
        """Safe per-request disclosure for explicit UI consent; no URL path/key."""
        available, remote, provider, model, endpoint, *_ = self._settings()
        try:
            host = urlsplit(endpoint).hostname if available else ""
        except ValueError:
            host = ""
        return {
            "available": bool(available), "remote": bool(remote) if available else False,
            "provider": provider[:80], "model": model[:128], "host": str(host or "")[:180],
        }

    def available(self) -> bool:
        return self._settings()[0]

    def capabilities(self) -> tuple[str, ...]:
        return ("generate", "structured") if self.available() else ()

    def generate(self, request: LLMRequest, *, control=None) -> LLMResult:
        enabled, remote, provider, model, endpoint, timeout, max_input, max_tokens = self._settings()

        def failed(reason: str) -> LLMResult:
            return LLMResult(False, reason=reason, provider=provider, model=model, remote=remote)

        if not enabled:
            return failed("unavailable")
        if not isinstance(request, LLMRequest):
            return failed("invalid_request")

        def cancellation_state() -> bool | None:
            """Malformed plugin controls must not abort the optional service."""
            if control is None:
                return False
            try:
                value = control.cancelled()
            except Exception:
                return None
            return value if type(value) is bool else None

        cancelled = cancellation_state()
        if cancelled is not False:
            return failed("cancelled" if cancelled is True else "invalid_request")
        # Plugins can construct LLMRequest at runtime despite its type hints.
        # Reject str subclasses and foreign task objects before invoking their
        # __str__/strip/__eq__ hooks on a path that must fail safely.
        if (type(request.prompt) is not str or not request.prompt.strip()
                or len(request.prompt) > max_input
                or type(request.system) is not str or len(request.system) > 1000
                or type(request.task) is not str
                or request.task not in ("generate", "structured")
                or type(request.max_output_tokens) is not int
                or request.max_output_tokens < 1):
            return failed("invalid_request")
        try:
            env_name = str(self._config.get("api_key_env") or "")
            api_key = (
                os.environ.get(env_name, "")
                if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,127}", env_name)
                else ""
            )
            messages = []
            if request.system:
                messages.append({"role": "system", "content": request.system})
            messages.append({"role": "user", "content": request.prompt})
            payload = {
                "model": model, "messages": messages,
                "max_tokens": min(max_tokens, request.max_output_tokens),
            }
            if request.task == "structured":
                payload["response_format"] = {"type": "json_object"}
            raw = json.dumps(payload).encode("utf-8")
            if len(raw) > _MAX_REQUEST_BYTES:
                return failed("request_too_large")
            headers = {"Content-Type": "application/json", "User-Agent": BROWSER_USER_AGENT}
            if api_key:
                headers["Authorization"] = "Bearer " + api_key
            http_request = Request(endpoint, data=raw, headers=headers, method="POST")
        except (ValueError, TypeError):
            return failed("invalid_request")

        if not _LLM_SLOTS.acquire(blocking=False):
            return failed("busy")
        done = Event()
        result: list[LLMResult] = []

        def worker() -> None:
            try:
                # Never emit prompts, responses, URLs, API keys or exception
                # messages to logs, diagnostics, or user-visible errors.
                with self._opener(http_request, timeout=timeout) as response:
                    data = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(data) > _MAX_RESPONSE_BYTES:
                    result.append(failed("response_too_large"))
                    return
                # Validate both transport envelope and structured payload.
                # An upstream response can be syntactically JSON yet contain
                # conflicting duplicate keys or IEEE non-finite numbers.
                try:
                    obj = _strict_model_json(data)
                    content = obj["choices"][0]["message"]["content"]
                    if (type(content) is not str or len(content) > 16000
                            or any(0xD800 <= ord(ch) <= 0xDFFF for ch in content)):
                        raise ValueError("invalid model text")
                    structured = {}
                    if request.task == "structured":
                        structured = _strict_model_json(content)
                        if type(structured) is not dict:
                            raise ValueError("structured JSON object required")
                except (ValueError, TypeError, KeyError, IndexError,
                        UnicodeError, RecursionError, OverflowError):
                    result.append(failed("invalid_response"))
                    return
                result.append(LLMResult(
                    True, text=content, structured=structured,
                    provider=provider, model=model, generated=True, remote=remote,
                ))
            except HTTPError as exc:
                # Expose only safe status categories: never error bodies/URLs.
                if exc.code in (401, 403):
                    result.append(failed("auth_refused"))
                elif exc.code == 404:
                    result.append(failed("not_found"))
                elif exc.code == 429:
                    result.append(failed("rate_limited"))
                else:
                    result.append(failed("backend_unavailable"))
            except TimeoutError:
                # urllib's own socket timeout can win the race with the
                # surrounding request deadline. Both are the same public
                # outcome, never an intermittent backend-unavailable error.
                result.append(failed("timeout"))
            except URLError as exc:
                # urllib wraps connect/read timeouts in URLError on some
                # platforms. Inspect only the exception TYPE, not its text:
                # error messages can contain the endpoint or credentials.
                reason = getattr(exc, "reason", None)
                result.append(failed(
                    "timeout" if isinstance(reason, TimeoutError)
                    else "backend_unavailable"
                ))
            except Exception:
                result.append(failed("backend_unavailable"))
            finally:
                done.set()
                _LLM_SLOTS.release()

        try:
            Thread(target=worker, daemon=True, name="cifereca-llm",).start()
        except RuntimeError:
            _LLM_SLOTS.release()
            return failed("backend_unavailable")
        deadline = monotonic() + timeout
        while not done.is_set():
            cancelled = cancellation_state()
            if cancelled is not False:
                return failed("cancelled" if cancelled is True else "invalid_request")
            remaining = deadline - monotonic()
            if remaining <= 0:
                return failed("timeout")
            done.wait(timeout=min(0.05, remaining))
        cancelled = cancellation_state()
        if cancelled is not False:
            return failed("cancelled" if cancelled is True else "invalid_request")
        return result[0] if result else failed("backend_unavailable")


def optional_llm_service(config) -> OptionalLLMService | None:
    """Absent entirely unless explicitly enabled in Core configuration."""
    settings = config.get("llm", {})
    if not isinstance(settings, Mapping) or settings.get("enabled") is not True:
        return None
    return OptionalLLMService(settings)


__all__ = ["OptionalLLMService", "optional_llm_service"]
