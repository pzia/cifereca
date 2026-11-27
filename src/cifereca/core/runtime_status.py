"""Consumer of the optional versioned runtime status port.

The legacy reader is only called by the existing installed-runtime facade
when its pre-port adapter has no native status method. It is read-only and
does not participate in rights, source selection or publication. Remove it
only once independently installed old runtime packages are no longer supported.
"""
from __future__ import annotations

from inspect import getattr_static
from typing import cast

from ..plugin_api.runtime_status import (
    RUNTIME_STATUS_CONTRACT, RuntimeStatusReader, RuntimeStatusSnapshot,
)

_MISSING = object()
_DETAIL_KEYS = frozenset({
    "configured_command", "resolved_executable", "resolved_update_executable",
    "software_version", "capabilities", "declared_capabilities", "strict_json_stdout",
    "probe_timings_ms", "contract_error", "capabilities_error", "status_error",
    "config_paths", "config_paths_error", "update_status", "update_status_error",
    "status", "error",
})
_FLAGS = (
    "available", "compatible", "operational", "download_available",
    "acsm_available", "delegated_available", "pinned_delivery_candidate",
    "separate_content_keys_replay", "protected_content_available",
)


def unavailable_state() -> dict[str, object]:
    state = {
        "available": False,
        "compatible": False,
        "operational": False,
        "download_available": False,
        "acsm_available": False,
        "kobo_delegated_available": False,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": False,
        "kobo_formats": [],
        "contract_version": None,
        "capabilities": {},
        "configured_command": None,
        "resolved_executable": None,
        "minimum_supported_contract": 8,
        "preferred_contract": 8,
        "error": {"code": "runtime_plugin_unavailable", "message": "Runtime Kobo-ADEPT indisponible"},
    }
    return state


def _invalid_state() -> dict[str, object]:
    state = unavailable_state()
    state["error"] = {"code": "runtime_status_contract_invalid",
                      "message": "Contrat de statut runtime indisponible ou invalide"}
    return state


def _copy_details(value, *, depth=0):
    """Accept bounded plain diagnostic values, not executable objects."""
    if depth > 12:
        raise ValueError("runtime_status_details_depth")
    if value is None or type(value) in (bool, int, float, str):
        if type(value) is str and len(value) > 65536:
            raise ValueError("runtime_status_detail_length")
        return value
    if type(value) is list and len(value) <= 1024:
        return [_copy_details(item, depth=depth + 1) for item in value]
    if type(value) is dict and len(value) <= 1024:
        if any(type(key) is not str or len(key) > 256 for key in value):
            raise ValueError("runtime_status_detail_key")
        return {key: _copy_details(item, depth=depth + 1)
                for key, item in value.items()}
    raise ValueError("runtime_status_detail_type")


def _project(snapshot: RuntimeStatusSnapshot) -> dict[str, object]:
    if (type(snapshot) is not RuntimeStatusSnapshot
            or type(snapshot.schema) is not str
            or snapshot.schema != RUNTIME_STATUS_CONTRACT
            or any(type(getattr(snapshot, name)) is not bool for name in _FLAGS)
            or type(snapshot.minimum_supported_contract) is not int
            or not 0 <= snapshot.minimum_supported_contract <= 1000
            or (snapshot.contract_version is not None
                and (type(snapshot.contract_version) is not int
                     or not 0 < snapshot.contract_version <= 1000))
            or type(snapshot.formats) is not tuple
            or len(snapshot.formats) > 32
            or any(type(item) is not str or not item or len(item) > 64
                   for item in snapshot.formats)
            or type(snapshot.details) is not dict):
        raise ValueError("runtime_status_contract_invalid")
    if (not snapshot.available and any(getattr(snapshot, name) for name in _FLAGS[1:])
            or snapshot.compatible and (
                snapshot.contract_version is None
                or snapshot.contract_version < snapshot.minimum_supported_contract)
            or snapshot.operational and not (
                snapshot.available and snapshot.compatible and snapshot.download_available)
            or (snapshot.acsm_available or snapshot.delegated_available)
                and not snapshot.download_available
            or (snapshot.pinned_delivery_candidate or snapshot.separate_content_keys_replay
                or snapshot.protected_content_available) and not snapshot.delegated_available):
        raise ValueError("runtime_status_inconsistent")
    if any(type(key) is not str or key not in _DETAIL_KEYS for key in snapshot.details):
        raise ValueError("runtime_status_unknown_detail")
    details = _copy_details(snapshot.details)
    if "strict_json_stdout" in details and type(details["strict_json_stdout"]) is not bool:
        raise ValueError("runtime_status_invalid_json_flag")
    for key in ("capabilities", "status", "probe_timings_ms"):
        if key in details and type(details[key]) is not dict:
            raise ValueError("runtime_status_invalid_detail")
    for key in ("error", "contract_error", "capabilities_error", "status_error",
                "config_paths_error", "update_status_error"):
        if details.get(key) is not None and type(details[key]) is not dict:
            raise ValueError("runtime_status_invalid_error")
    if details.get("resolved_executable") is not None and type(details["resolved_executable"]) is not bool:
        raise ValueError("runtime_status_invalid_resolution")
    # Keep the existing consumer ABI. These are legacy display keys, not a
    # decoding of the runtime's wire protocol and not provider authority.
    return {
        "capabilities": {},
        "configured_command": None,
        "resolved_executable": None,
        **details,
        "available": snapshot.available,
        "compatible": snapshot.compatible,
        "operational": snapshot.operational,
        "download_available": snapshot.download_available,
        "acsm_available": snapshot.acsm_available,
        "kobo_delegated_available": snapshot.delegated_available,
        "kobo_pinned_delivery_candidate": snapshot.pinned_delivery_candidate,
        "kobo_separate_content_keys_replay": snapshot.separate_content_keys_replay,
        "kobo_kdrm_available": snapshot.protected_content_available,
        "kobo_formats": list(snapshot.formats),
        "contract_version": snapshot.contract_version,
        "minimum_supported_contract": snapshot.minimum_supported_contract,
        "preferred_contract": snapshot.minimum_supported_contract,
    }


def read(adapter: object, *, full: bool = False) -> dict[str, object]:
    if adapter is None:
        return unavailable_state()
    # Do not downgrade an implemented but invalid native port to legacy.
    native = getattr_static(adapter, "runtime_status", _MISSING)
    if native is _MISSING:
        return _read_legacy(adapter, full=full)
    try:
        reader = cast(RuntimeStatusReader, adapter)
        return _project(reader.runtime_status(full=full))
    except (AttributeError, TypeError, ValueError):
        return _invalid_state()


def _read_legacy(adapter: object, *, full: bool) -> dict[str, object]:
    """Pre-status-port compatibility only; retain the old projection verbatim."""
    available = getattr(adapter, "available", None)
    if callable(available) and not available():
        state = {
            "available": False, "compatible": False, "operational": False,
            "download_available": False, "acsm_available": False,
            "kobo_delegated_available": False, "kobo_pinned_delivery_candidate": False,
            "kobo_separate_content_keys_replay": False, "kobo_kdrm_available": False,
            "kobo_formats": [], "contract_version": None, "capabilities": {},
            "configured_command": getattr(adapter, "configured_command", lambda: None)(),
            "resolved_executable": None, "minimum_supported_contract": 8, "preferred_contract": 8,
        }
        # Do not cache a pre-activation missing runtime for 60 seconds.
        # Plugin activation may finish moments after a startup/UI probe;
        # the next broker read must be allowed to see the new service.
        # This check is in-memory and never executes the vendor CLI.
        return state
    core_probe = getattr(adapter, "probe_core", None)
    probe = adapter.probe() if full or core_probe is None else core_probe()
    contract = probe.get("contract") if isinstance(probe, dict) else {}
    runtime_caps = probe.get("capabilities") if isinstance(probe, dict) else {}
    runtime_status = probe.get("status") if isinstance(probe, dict) else {}
    contract = contract if isinstance(contract, dict) else {}
    runtime_caps = runtime_caps if isinstance(runtime_caps, dict) else {}
    runtime_status = runtime_status if isinstance(runtime_status, dict) else {}
    try:
        contract_version = int(contract.get("contract_version") or runtime_caps.get("contract_version") or runtime_status.get("contract_version") or 0)
    except (TypeError, ValueError):
        contract_version = 0
    compatible = contract_version >= int(getattr(adapter, "required_contract", 8))
    capabilities = runtime_caps.get("capabilities") if isinstance(runtime_caps.get("capabilities"), dict) else {}
    download = capabilities.get("download") if isinstance(capabilities.get("download"), dict) else {}
    http_download = download.get("http") if isinstance(download.get("http"), dict) else {}
    acsm_download = download.get("acsm") if isinstance(download.get("acsm"), dict) else {}
    kobo_download = download.get("kobo") if isinstance(download.get("kobo"), dict) else {}
    declared = contract.get("capabilities") if isinstance(contract.get("capabilities"), list) else []
    declared_names = {str(x) for x in declared}
    acsm_available = bool(
        bool(acsm_download.get("available")) or "download_acsm_adept" in declared_names
    )
    kobo_delegated_available = bool(
        contract_version >= 8
        and (bool(kobo_download.get("available")) or "download_kobo_delegated" in declared_names)
    )
    kobo_formats = [
        str(value).strip().lower()
        for value in (kobo_download.get("formats") or [])
        if str(value).strip()
    ] if isinstance(kobo_download.get("formats"), list) else []
    kobo_pinned_delivery_candidate = bool(
        kobo_delegated_available and kobo_download.get("pinned_delivery_candidate") is True
    )
    kobo_separate_content_keys_replay = bool(
        kobo_delegated_available and kobo_download.get("separate_content_keys_replay") is True
    )
    kobo_drm = kobo_download.get("drm") if isinstance(kobo_download.get("drm"), dict) else {}
    kdrm_cap = kobo_drm.get("KDRM") if isinstance(kobo_drm.get("KDRM"), dict) else kobo_drm.get("kdrm")
    if isinstance(kdrm_cap, dict):
        kobo_kdrm_available = bool(kdrm_cap.get("available"))
    else:
        # 0.13.2's additive capability example makes pinned delivery +
        # separate key replay sufficient evidence of the new KDRM bridge,
        # while older v8 runtimes normally expose drm.KDRM explicitly.
        kobo_kdrm_available = bool(
            kobo_pinned_delivery_candidate and kobo_separate_content_keys_replay
        )
    download_available = (
        bool(http_download.get("available"))
        or "download_http_epub_pdf" in declared_names
        or acsm_available
        or kobo_delegated_available
    )
    strict_json_stdout = bool(contract_version >= 7 or "strict_json_stdout" in declared_names)
    contract_error = probe.get("contract_error")
    capabilities_error = probe.get("capabilities_error")
    status_error = probe.get("status_error")
    status_error_code = str(status_error.get("code") or "") if isinstance(status_error, dict) else ""
    # Contract v5 makes contract+capabilities authoritative for the normal
    # integration path.  Full status/config probes are diagnostic only; an
    # actual download will still return a structured config_error if needed.
    blocking_status_error = status_error if full and status_error_code == "config_error" else None
    core_error = contract_error or capabilities_error or blocking_status_error
    state = {
        "available": True,
        "compatible": compatible,
        "operational": bool(compatible and download_available and not blocking_status_error),
        "download_available": download_available,
        "acsm_available": acsm_available,
        "kobo_delegated_available": kobo_delegated_available,
        "kobo_pinned_delivery_candidate": kobo_pinned_delivery_candidate,
        "kobo_separate_content_keys_replay": kobo_separate_content_keys_replay,
        "kobo_kdrm_available": kobo_kdrm_available,
        "kobo_formats": kobo_formats,
        "minimum_supported_contract": int(getattr(adapter, "required_contract", 8)),
        "preferred_contract": int(getattr(adapter, "required_contract", 8)),
        "software_version": str(runtime_caps.get("software_version") or contract.get("version") or runtime_status.get("software_version") or ""),
        "contract_version": contract_version or None,
        "capabilities": capabilities,
        "declared_capabilities": list(declared),
        "strict_json_stdout": strict_json_stdout,
        "resolved_executable": probe.get("resolved_executable"),
        "resolved_update_executable": probe.get("resolved_update_executable"),
        "probe_timings_ms": probe.get("probe_timings_ms") or {},
        "contract_error": contract_error,
        "capabilities_error": capabilities_error,
        "status_error": status_error,
        "config_paths": probe.get("config_paths"),
        "config_paths_error": probe.get("config_paths_error"),
        "update_status": probe.get("update_status"),
        "update_status_error": probe.get("update_status_error"),
        "status": runtime_status,
        "error": core_error,
    }
    return state
