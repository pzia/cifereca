"""Closed, nonidentifying Kobo-ADEPT Core diagnostic capability projection.

The external CLI may return arbitrary nested JSON in successful responses.
Normal acquisition uses the runtime's own cached capabilities, not this probe.
"""
from __future__ import annotations

import re

_DECLARED = frozenset({
    "strict_json_stdout", "download_http_epub_pdf", "download_acsm_adept",
    "download_kobo_delegated",
})
_FORMATS = frozenset({"epub", "pdf", "kepub"})
_VERSION = re.compile(r"[0-9]{1,4}(?:\.[0-9]{1,4}){1,3}(?:[-+][A-Za-z0-9.]{1,32})?\Z")


def _bool(value):
    # Unknown vendor values must not silently turn into enabled capabilities.
    return value is True


def _version(value):
    return value if type(value) is str and _VERSION.fullmatch(value) else ""


def _object(value):
    return value if type(value) is dict else {}


def _formats(value):
    if type(value) is not list or len(value) > 32:
        return []
    found = set()
    for item in value:
        if type(item) is str and item.casefold() in _FORMATS:
            found.add(item.casefold())
    return sorted(found)


def public_core_probe(contract, capabilities, executable_resolved):
    """Only fields consumed by Core's operational status; no vendor extras."""
    contract = _object(contract)
    caps_payload = _object(capabilities)
    declared = contract.get("capabilities")
    declared = declared if type(declared) is list and len(declared) <= 128 else []
    clean_declared = sorted({
        item for item in declared if type(item) is str and item in _DECLARED
    })
    cv = contract.get("contract_version")
    version = cv if type(cv) is int and 0 <= cv <= 1000 else 0
    caps = _object(caps_payload.get("capabilities"))
    download = _object(caps.get("download"))
    http = _object(download.get("http"))
    acsm = _object(download.get("acsm"))
    kobo = _object(download.get("kobo"))
    drm = _object(kobo.get("drm"))
    kdrm = drm.get("KDRM")
    if type(kdrm) is not dict:
        kdrm = drm.get("kdrm")
    adept = _object(caps.get("adept"))
    loan_return = adept.get("loan_return")
    return {
        "contract": {
            "name": "kobo-adept",
            "contract_version": version,
            "version": _version(contract.get("version")),
            "capabilities": clean_declared,
        },
        "capabilities": {
            "ok": True,
            "software_version": _version(caps_payload.get("software_version")),
            "contract_version": version,
            "capabilities": {
                "download": {
                    "http": {"available": _bool(http.get("available"))},
                    "acsm": {"available": _bool(acsm.get("available"))},
                    "kobo": {
                        "available": _bool(kobo.get("available")),
                        "formats": _formats(kobo.get("formats")),
                        "pinned_delivery_candidate": _bool(kobo.get("pinned_delivery_candidate")),
                        "separate_content_keys_replay": _bool(kobo.get("separate_content_keys_replay")),
                        # Preserve absence for Core's documented legacy
                        # pinned+replay fallback. An explicit map, even {},
                        # remains authoritative and cannot become fallback.
                        **({"drm": {"KDRM": {"available": _bool(kdrm.get("available"))}}}
                           if type(kdrm) is dict else {}),
                    },
                },
                "adept": {
                    "loan_return": {
                        "available": _bool(loan_return.get("available"))
                        if type(loan_return) is dict else _bool(loan_return),
                    },
                },
            },
        },
        # Core and health checks only need to know if the executable resolves.
        # Never publish an absolute path, user home or arbitrary CLI argv.
        "resolved_executable": executable_resolved is True,
        "probe_timings_ms": {},
    }
