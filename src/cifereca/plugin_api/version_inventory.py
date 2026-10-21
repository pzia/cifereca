"""Read-only, public broker version projection shared by both system pages.

This is NOT a support report. Only explicitly allowed manifest fields may be
displayed or copied; identifiers and versions must match closed grammars.
No runtime introspection, paths, accounts, provider calls or diagnostics text.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,95}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}\Z")
_NAME = re.compile(r"[\wÀ-ÿ][\wÀ-ÿ .,()&+\-]{0,79}\Z")
_PRIVATE_WORD = re.compile(r"(?i)password|passphrase|secret|token|cookie|bearer|authorization|credential|email|account|identity|(?:api|private)[_-]?key")
_STATES = frozenset({
    "ready", "blocked", "error", "incompatible", "degraded",
    "disabled", "loaded", "active", "pending", "unknown",
    "discovered", "unavailable", "failed",
})
_STATE_LABEL = {
    "ready": "Prêt", "blocked": "Bloqué", "error": "Erreur",
    "incompatible": "Incompatible", "degraded": "Dégradé",
    "disabled": "Désactivé", "loaded": "Chargé", "active": "Actif",
    "pending": "En attente", "unknown": "Inconnu",
    "discovered": "Détecté", "unavailable": "Indisponible",
    "failed": "Échec",
}
_MAX_PLUGINS = 300


def _value(value, grammar, default="inconnue"):
    if type(value) is not str or not grammar.fullmatch(value):
        return default
    return value


def _name(value, fallback):
    if (type(value) is not str or not _NAME.fullmatch(value)
            or _PRIVATE_WORD.search(value)):
        return fallback
    return value


@dataclass(frozen=True)
class VersionRow:
    plugin_id: str
    name: str
    version: str
    package_version: str
    expected_version: str
    state: str
    enabled: bool | None

    @property
    def status(self):
        label = _STATE_LABEL[self.state]
        enabled = ("oui" if self.enabled is True else
                   "non" if self.enabled is False else "inconnu")
        return f"{label} · activé : {enabled}"

    @property
    def details(self):
        pieces = [
            f"Extension : {self.version}",
            f"Paquet actif : {self.package_version}",
        ]
        if self.expected_version != "inconnue":
            pieces.append(f"Paquet attendu : {self.expected_version}")
            if (self.package_version != "inconnue"
                    and self.package_version != self.expected_version):
                pieces.append("Écart paquet actif/attendu")
        pieces.append(self.status)
        return " · ".join(pieces)


@dataclass(frozen=True)
class VersionInventory:
    core_version: str
    plugins: tuple[VersionRow, ...]
    limited: bool = False

    @property
    def text(self):
        lines = [
            f"Core Cifereca · v{self.core_version}",
            f"Plugins : {len(self.plugins)}" +
            (" (affichage limité à 300)" if self.limited else ""),
        ]
        for item in self.plugins:
            lines.append(f"{item.name} [{item.plugin_id}] · {item.details}")
        return "\n".join(lines)


def version_inventory(system_status: Mapping, inventory: Iterable) -> VersionInventory:
    """Freeze bounded broker values; do not use diagnostic/support payloads.

    Refuse malformed plugin IDs rather than copying arbitrary manifest text.
    Never derive a plugin version from the distribution or expected release.
    """
    status = system_status if isinstance(system_status, Mapping) else {}
    core = _value(status.get("version"), _VERSION)
    rows = []
    limited = False
    for raw in inventory:
        if not isinstance(raw, Mapping):
            continue
        plugin_id = _value(raw.get("plugin_id"), _ID, "")
        if not plugin_id:
            continue
        if len(rows) >= _MAX_PLUGINS:
            limited = True
            break
        state = raw.get("state")
        state = state if type(state) is str and state in _STATES else "unknown"
        enabled = raw.get("enabled")
        enabled = enabled if type(enabled) is bool else None
        rows.append(VersionRow(
            plugin_id=plugin_id,
            name=_name(raw.get("display_name"), plugin_id),
            version=_value(raw.get("version"), _VERSION),
            package_version=_value(raw.get("package_version"), _VERSION),
            expected_version=_value(raw.get("expected_package_version"), _VERSION),
            state=state,
            enabled=enabled,
        ))
    rows.sort(key=lambda row: (
        0 if row.state in {"blocked", "error", "incompatible"} else 1,
        row.name.casefold(), row.plugin_id,
    ))
    return VersionInventory(core, tuple(rows), limited)
