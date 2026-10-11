"""Opt-in OpenAI-compatible LLM adapter, with no default network access.

Only the Core reads the endpoint and credential environment variable. The
public Plugin API exposes neither credentials nor HTTP-specific machinery.
"""
from __future__ import annotations

import json
import os
import re
from ipaddress import ip_address
from threading import BoundedSemaphore, Event, Thread
from time import monotonic
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

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
    except ValueError:
        return False, False
    if (parsed.scheme not in {"http", "https"} or not host
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(ord(c) < 32 for c in endpoint)):
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


class OptionalLLMService:
    """One bounded generate()/structured-JSON transport, no implicit retries."""

    def __init__(self, config: Mapping[str, Any], *, opener=None):
        self._config = dict(config)
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect()).open

    def _settings(self) -> tuple[bool, bool, str, str, str, float, int, int]:
        cfg = self._config
        enabled = cfg.get("enabled") is True
        provider = str(cfg.get("provider") or "openai-compatible")
        model = str(cfg.get("model") or "").strip()[:128]
        endpoint = str(cfg.get("endpoint") or "").strip()
        permit, remote = _allowed_endpoint(endpoint, allow_remote=cfg.get("allow_remote") is True)
        try:
            timeout = min(15.0, max(0.2, float(cfg.get("timeout_seconds", 5))))
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
        if control is not None and control.cancelled():
            return failed("cancelled")
        if (not isinstance(request.prompt, str) or not request.prompt.strip()
                or len(request.prompt) > max_input
                or not isinstance(request.system, str) or len(request.system) > 1000
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
            headers = {"Content-Type": "application/json"}
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
                obj = json.loads(data)
                content = obj["choices"][0]["message"]["content"]
                if not isinstance(content, str) or len(content) > 16000:
                    result.append(failed("invalid_response"))
                    return
                structured = {}
                if request.task == "structured":
                    structured = json.loads(content)
                    if not isinstance(structured, dict):
                        result.append(failed("invalid_response"))
                        return
                result.append(LLMResult(
                    True, text=content, structured=structured,
                    provider=provider, model=model, generated=True, remote=remote,
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
            if control is not None and control.cancelled():
                return failed("cancelled")
            remaining = deadline - monotonic()
            if remaining <= 0:
                return failed("timeout")
            done.wait(timeout=min(0.05, remaining))
        if control is not None and control.cancelled():
            return failed("cancelled")
        return result[0] if result else failed("backend_unavailable")


def optional_llm_service(config) -> OptionalLLMService | None:
    """Absent entirely unless explicitly enabled in Core configuration."""
    settings = config.get("llm", {})
    if not isinstance(settings, Mapping) or settings.get("enabled") is not True:
        return None
    return OptionalLLMService(settings)


__all__ = ["OptionalLLMService", "optional_llm_service"]
