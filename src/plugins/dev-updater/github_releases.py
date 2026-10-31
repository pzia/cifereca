"""Read-only GitHub release inspection for the opt-in developer updater.

No credential handling, archive downloads, installation, or network on import.
The only HTTP request is made from an explicit user action in the page.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import quote
import httpx
from cifereca.plugin_api import HTTPX_TRANSPORT

from packaging.version import InvalidVersion, Version

MAX_RESPONSE = 512 * 1024
MAX_RELEASES = 30
DEFAULT_REPOSITORY = "pzia/cifereca"
_REPO_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,159}$")


class ReleaseCheckError(Exception):
    """User-safe error. Never contains an upstream URL, token or raw payload."""


@dataclass(frozen=True)
class Release:
    tag: str
    published: str
    prerelease: bool
    notes: str
    zip_names: tuple[str, ...]
    verification_names: tuple[str, ...]


def repository_slug(value):
    value = str(value or "")
    parts = value.split("/")
    if len(parts) != 2 or any(
        not _REPO_PART.fullmatch(p) or p in {".", ".."} for p in parts
    ):
        raise ReleaseCheckError("Dépôt GitHub mal configuré (attendu : propriétaire/dépôt).")
    return value


def channel_name(value):
    name = str(value or "stable")
    if name not in ("stable", "prerelease", "dev"):
        raise ReleaseCheckError("Canal inconnu.")
    return name


def installed_version():
    try:
        return version("cifereca")
    except PackageNotFoundError:
        return None


def version_comparison(installed, tag):
    """Only assert newer when both PEP 440 versions are comparable."""
    if not installed:
        return "Version locale indisponible"
    try:
        now = Version(str(installed).removeprefix("v"))
        offered = Version(str(tag).removeprefix("v"))
    except InvalidVersion:
        return "Versions non comparables : vérifier manuellement"
    if offered > now:
        return "Version publiée plus récente (installation non vérifiée)"
    if offered == now:
        return "Version publiée identique à la version locale"
    return "Version publiée antérieure à la version locale"


def _assets(entry):
    zip_names, verification = [], []
    for item in entry if isinstance(entry, list) else ():
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not _ASSET_NAME.fullmatch(name):
            continue
        if name.endswith(".zip") and len(zip_names) < 15:
            zip_names.append(name)
        elif (name.endswith(".zip.sha256") or name.endswith(".zip.sigstore.json")
              ) and len(verification) < 30:
            verification.append(name)
    return tuple(zip_names), tuple(verification)


def select_release(payload, *, channel="stable"):
    """Published release only, newest publication date; no draft or fabricated URLs."""
    channel = channel_name(channel)
    if not isinstance(payload, list):
        raise ReleaseCheckError("Réponse GitHub invalide.")
    qualified = []
    for item in payload[:MAX_RELEASES]:
        if not isinstance(item, dict) or item.get("draft") is not False:
            continue
        prerelease = item.get("prerelease") is True
        if channel == "stable" and prerelease:
            continue
        if channel == "prerelease" and not prerelease:
            continue
        tag = item.get("tag_name")
        published = item.get("published_at")
        if (not isinstance(tag, str) or not 1 <= len(tag) <= 100
                or not isinstance(published, str) or
                not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", published)):
            continue
        notes = item.get("body")
        zip_names, verification = _assets(item.get("assets"))
        qualified.append(Release(
            tag=tag, published=published, prerelease=prerelease,
            notes=str(notes or "")[:2500] if isinstance(notes, str) else "",
            zip_names=zip_names, verification_names=verification,
        ))
    return max(qualified, key=lambda r: r.published) if qualified else None


def fetch_releases(repository=DEFAULT_REPOSITORY, *, channel="stable", transport=None):
    """Bounded raw JSON read via Core. Explicit action, no redirect or cookie reuse."""
    slug = repository_slug(repository)
    selected_channel = channel_name(channel)
    owner, name = slug.split("/")
    url = ("https://api.github.com/repos/" + quote(owner, safe="") +
           "/" + quote(name, safe="") + "/releases?per_page=30")
    headers = {
        "Accept": "application/vnd.github+json",
        "Accept-Encoding": "identity",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with HTTPX_TRANSPORT.client(producer="dev-updater", timeout=10,
                                    follow_redirects=False, transport=transport) as client:
            with client.stream("GET", url, headers=headers) as response:
                status = response.status_code
                if status in (403, 429):
                    raise ReleaseCheckError("GitHub limite les vérifications ; réessayez ultérieurement.")
                if status in (401, 404):
                    raise ReleaseCheckError("Dépôt ou accès aux releases indisponible.")
                if status >= 400:
                    raise ReleaseCheckError("Vérification GitHub indisponible.")
                if str(response.url) != url or status != 200:
                    raise ReleaseCheckError("Réponse redirigée ou invalide.")
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if mime not in ("application/json", "application/vnd.github+json"):
                    raise ReleaseCheckError("La réponse GitHub n'est pas du JSON.")
                data = bytearray()
                for chunk in response.iter_raw(chunk_size=64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE:
                        raise ReleaseCheckError("Réponse GitHub trop volumineuse.")
    except (httpx.HTTPError, TimeoutError, OSError):
        raise ReleaseCheckError("Connexion à GitHub indisponible.") from None
    try:
        payload = json.loads(data)
    except (UnicodeError, ValueError):
        raise ReleaseCheckError("JSON GitHub invalide.") from None
    if not isinstance(payload, list):
        raise ReleaseCheckError("Réponse GitHub inattendue.") from None
    return select_release(payload, channel=selected_channel)
