"""Diagnostics GTK-only pseudonyms for explicit login/password values (#3135).

Raw events, broker queries, file storage, console and raw exports never call
these helpers. All other values remain intact in the displayed text.
"""
from __future__ import annotations

import re
from typing import Any

_FIELD = re.compile(r"^(?:login|username|user_login|password|passwd|mot[_-]de[_-]passe)$", re.I)
_PAIR = re.compile(
    r"""(?i)(?<![\w.-])(?P<field>["']?(?:login|username|user_login|password|passwd|mot[_-]de[_-]passe)["']?\s*[:=]\s*)"""
    r"""(?P<value>"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}\]\r\n]+)"""
)
_PSEUDONYM = re.compile(r"^(?:login|mot-de-passe)-[1-9][0-9]*$")


def _kind(field: str) -> str | None:
    key = field.strip().strip("\"'").casefold()
    if _FIELD.fullmatch(key) is None:
        return None
    return "mot-de-passe" if key in {"password", "passwd", "mot_de_passe", "mot-de-passe"} else "login"


class DiagnosticsPseudonyms:
    def __init__(self) -> None:
        self._references: dict[tuple[str, str], str] = {}
        self._counts = {"login": 0, "mot-de-passe": 0}

    def value(self, key: str, raw: Any) -> Any:
        kind = _kind(key)
        if kind is None or type(raw) is not str:
            return raw
        if _PSEUDONYM.fullmatch(raw):
            return raw  # Idempotent across GTK rerenders, clipboard and preview.
        identity = kind, raw
        if identity not in self._references:
            self._counts[kind] += 1
            self._references[identity] = f"{kind}-{self._counts[kind]}"
        return self._references[identity]

    def text(self, raw: str) -> str:
        if type(raw) is not str:
            raise TypeError("Diagnostics text must be a string")

        def replace(match: re.Match[str]) -> str:
            value = match["value"]
            quote = value[0] if value[:1] in ("'", '"') and value[-1:] == value[:1] else ""
            naked = value[1:-1] if quote else value
            pseudonym = self.value(match["field"].split(":")[0].split("=")[0], naked)
            if pseudonym == naked:
                return match.group(0)
            return match["field"] + quote + pseudonym + quote

        return _PAIR.sub(replace, raw)


# One GTK process/session: stable pseudonyms across paging and navigation.
DISPLAY_PSEUDONYMS = DiagnosticsPseudonyms()
