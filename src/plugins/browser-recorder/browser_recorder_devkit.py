"""Browser Recorder adapter to the common offline Core contract kit."""
from cifereca.plugin_api.capture_vault import (
    core_contract_kit as _core_contract_kit, VaultError,
    CAPTURE_KIT_PUBLIC_SOURCES as _PUBLIC_SOURCES,
)
from importlib import resources
from browser_recorder import RecorderError


def core_contract_kit():
    try:
        return _core_contract_kit()
    except VaultError as exc:
        raise RecorderError(exc.args[0]) from exc
