"""P0 #2770: check the exact PyPI wheel selected by pip's dry-run report.

pip remains the ONLY resolver and installer. The HTTPS PyPI release JSON
attests the upload date of the artifact, not its version or cache mtime.
An unverified wheel is never handed to a mutating pip command.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler
import ssl

from cifereca.http_trace import BROWSER_USER_AGENT

from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

DEFAULT_QUARANTINE_HOURS = 72
QUARANTINE_CONFIG_KEY = "security.python_dependency_quarantine_hours"
_UNSET = object()
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class QuarantineRejected(ValueError):
    """Closed code plus optional validated name/version for pip constraints."""

    def __init__(self, code: str, *, name: str | None = None,
                 version: str | None = None, quarantine_hours: int | None = None,
                 eligible_at: datetime | None = None):
        super().__init__(code)
        self.name = name
        self.version = version
        self.quarantine_hours = quarantine_hours
        self.eligible_at = eligible_at


def _rejected(reason: str, *, name: str | None = None,
              version: str | None = None, quarantine_hours: int | None = None,
              eligible_at: datetime | None = None) -> None:
    raise QuarantineRejected(reason, name=name, version=version,
                             quarantine_hours=quarantine_hours, eligible_at=eligible_at)


def quarantine_duration(hours: object = _UNSET) -> tuple[timedelta, int]:
    """One validated policy for online resolution, attestation and offline use.

    Missing settings inherit 72 h. Explicit invalid values NEVER disable
    admission, including booleans (which are subclasses of int).
    """
    if hours is _UNSET:
        from ..config import Config
        security = Config().get("security")
        if type(security) is not dict:
            _rejected("dependency_quarantine_configuration_invalid")
        hours = security.get("python_dependency_quarantine_hours",
                             DEFAULT_QUARANTINE_HOURS)
    if type(hours) is not int or hours <= 0:
        _rejected("dependency_quarantine_configuration_invalid")
    try:
        age = timedelta(hours=hours)
    except OverflowError:
        _rejected("dependency_quarantine_configuration_invalid")
    return age, hours


def quarantine_eligibility(uploaded: datetime, minimum_age: timedelta) -> datetime:
    """Represent the admission UTC instant or refuse an unrepresentable policy."""
    try:
        return uploaded + minimum_age
    except OverflowError:
        _rejected("dependency_quarantine_configuration_invalid")


def quarantine_diagnostic(exc: QuarantineRejected) -> str:
    """User-facing diagnostic without changing the stable machine error code."""
    code = str(exc)
    if code == "dependency_quarantine_configuration_invalid":
        return code + f" ({QUARANTINE_CONFIG_KEY}: expected positive integer hours)"
    if code == "dependency_too_recent" and exc.quarantine_hours is not None:
        details = f"{QUARANTINE_CONFIG_KEY}={exc.quarantine_hours}"
        if exc.eligible_at is not None:
            details += f"; eligible_at={exc.eligible_at.isoformat()}"
        return code + " (" + details + ")"
    return code


def _https_pypi_release(name: str, version: str) -> dict:
    """Fetch one bounded official release record; no mirrors or redirects."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        _rejected("dependency_provenance_unavailable")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]{0,127}", version):
        _rejected("dependency_provenance_unavailable")

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = build_opener(
        HTTPSHandler(context=ssl.create_default_context()),
        ProxyHandler({}), NoRedirect(),
    )
    url = f"https://pypi.org/pypi/{quote(name, safe='')}/{quote(version, safe='')}/json"
    try:
        with opener.open(Request(url, headers={"Accept": "application/json", "User-Agent": BROWSER_USER_AGENT}), timeout=8) as stream:
            if urlsplit(stream.geturl()).netloc != "pypi.org":
                _rejected("dependency_provenance_unavailable")
            data = stream.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            _rejected("dependency_provenance_unavailable")
        result = json.loads(data)
        if type(result) is not dict:
            _rejected("dependency_provenance_unavailable")
        return result
    except (OSError, ValueError, TypeError):
        _rejected("dependency_provenance_unavailable")


def check_pip_report(
    report: object, *, now: datetime,
    read_release=_https_pypi_release, quarantine_hours: object = _UNSET,
) -> tuple[str, ...]:
    """Return SHA-pinned HTTPS wheel URLs for pip install --no-deps.

    pip --dry-run --report determines the *entire* selected dependency graph,
    including transitive dependencies. No custom resolver, PyPI scraper,
    manually managed dependency constraint graph, or post-install checking.
    A pinned wheel URL is passed to pip only after EXACT SHA + age matching.
    """
    minimum_age, selected_hours = quarantine_duration(quarantine_hours)
    if (type(now) is not datetime or now.tzinfo is None
            or now.utcoffset() is None or type(report) is not dict
            or type(report.get("install")) is not list):
        _rejected("dependency_provenance_unavailable")
    now_utc = now.astimezone(timezone.utc)
    pinned: list[str] = []
    for item in report["install"]:
        if type(item) is not dict or type(item.get("metadata")) is not dict:
            _rejected("dependency_provenance_unavailable")
        metadata = item["metadata"]
        name, version = metadata.get("name"), metadata.get("version")
        download = item.get("download_info")
        if (type(name) is not str or type(version) is not str
                or type(download) is not dict):
            _rejected("dependency_provenance_unavailable")
        url = download.get("url")
        archive = download.get("archive_info")
        if type(url) is not str or type(archive) is not dict:
            _rejected("dependency_provenance_unavailable")
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != "files.pythonhosted.org"
                or parts.query or parts.fragment or not parts.path.endswith(".whl")):
            _rejected("dependency_provenance_unavailable")
        filename = parts.path.rsplit("/", 1)[-1]
        try:
            wheel_name, wheel_version, _build, _tags = parse_wheel_filename(filename)
            if wheel_name != canonicalize_name(name) or wheel_version != Version(version):
                _rejected("dependency_not_eligible")
        except (InvalidWheelFilename, InvalidVersion, ValueError, TypeError):
            _rejected("dependency_not_eligible")
        digests = archive.get("hashes")
        sha = digests.get("sha256") if type(digests) is dict else None
        if type(sha) is not str or not _HASH.fullmatch(sha):
            _rejected("dependency_provenance_unavailable")
        registry = read_release(name, version)
        info, files = registry.get("info"), registry.get("urls")
        if (type(info) is not dict or type(files) is not list
                or canonicalize_name(str(info.get("name") or "")) != canonicalize_name(name)
                or info.get("version") != version):
            _rejected("dependency_provenance_unavailable")
        matches = [row for row in files if type(row) is dict and row.get("filename") == filename]
        if len(matches) != 1:
            _rejected("dependency_provenance_unavailable")
        entry = matches[0]
        if (entry.get("url") != url or entry.get("packagetype") != "bdist_wheel"
                or type(entry.get("digests")) is not dict
                or entry["digests"].get("sha256") != sha):
            _rejected("dependency_provenance_unavailable")
        if entry.get("yanked") is not False:
            _rejected("dependency_not_eligible")
        date = entry.get("upload_time_iso_8601")
        if type(date) is not str or len(date) > 40:
            _rejected("dependency_provenance_unavailable")
        try:
            stamp = datetime.fromisoformat(date.replace("Z", "+00:00"))
        except ValueError:
            _rejected("dependency_provenance_unavailable")
        if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0) or stamp > now_utc:
            _rejected("dependency_provenance_unavailable")
        if now_utc - stamp < minimum_age:
            _rejected("dependency_too_recent", name=canonicalize_name(name),
                      version=str(Version(version)), quarantine_hours=selected_hours,
                      eligible_at=quarantine_eligibility(stamp, minimum_age))
        # pip checks the wheel SHA itself when downloading this exact URL.
        pinned.append(url + "#sha256=" + sha)
    return tuple(pinned)
