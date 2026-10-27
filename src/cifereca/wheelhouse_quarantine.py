"""P0 #2770: offline wheelhouse admission, distinct from pip resolution.

The manifest MUST come from a trusted producer (e.g. a verified/signed release).
A self-declared JSON file is not a signature. Refuse incomplete, stale or
modified wheelhouses before passing any local wheel to mutating pip.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from .plugins.python_quarantine import (
    QuarantineRejected, _https_pypi_release, check_pip_report,
    quarantine_duration, _UNSET,
)

MANIFEST = "wheelhouse-provenance.json"
SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_WHEELS = 512
MAX_MANIFEST_BYTES = 1024 * 1024


def _reject(code: str = "dependency_provenance_unavailable", *,
            quarantine_hours: int | None = None,
            eligible_at: datetime | None = None) -> None:
    raise QuarantineRejected(code, quarantine_hours=quarantine_hours,
                             eligible_at=eligible_at)


def _utc(now: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        _reject()
    return current.astimezone(timezone.utc)


def _wheels(directory: Path) -> dict[str, Path]:
    if not directory.is_dir() or directory.is_symlink():
        _reject()
    entries = list(directory.iterdir())
    if len(entries) > MAX_WHEELS + 1:
        _reject("dependency_not_eligible")
    wheels: dict[str, Path] = {}
    for path in entries:
        if path.name == MANIFEST:
            if path.is_symlink() or not path.is_file():
                _reject()
            continue
        if path.is_symlink() or not path.is_file() or not path.name.endswith(".whl"):
            _reject("dependency_not_eligible")
        wheels[path.name] = path
    if not wheels:
        _reject()
    return wheels


def _identity(filename: str) -> tuple[str, str]:
    try:
        name, version, _build, _tags = parse_wheel_filename(filename)
    except (InvalidWheelFilename, InvalidVersion, ValueError, TypeError):
        _reject("dependency_not_eligible")
    return canonicalize_name(name), str(version)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        _reject()
    return digest.hexdigest()


def _timestamp(raw: object, now: datetime, *,
               minimum_age: timedelta, quarantine_hours: int) -> datetime:
    if not isinstance(raw, str) or len(raw) > 40:
        _reject()
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _reject()
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0) or stamp > now:
        _reject()
    if now - stamp < minimum_age:
        _reject("dependency_too_recent", quarantine_hours=quarantine_hours,
                eligible_at=stamp + minimum_age)
    return stamp


def attest_wheelhouse(
    directory: Path, *, core_version: str | None = None,
    now: datetime | None = None, read_release=_https_pypi_release,
    quarantine_hours: object = _UNSET,
) -> Path:
    """Producer-side proof: independently check PyPI for each exact third-party wheel.

    Call only in a trusted packaging pipeline, then sign/verify the containing
    archive. This verifies files already present; upstream fetch MUST separately
    enforce pre-download admission before creating those files.
    """
    _minimum_age, selected_hours = quarantine_duration(quarantine_hours)
    current = _utc(now)
    directory = Path(directory)
    wheels = _wheels(directory)
    records = []
    for filename, path in sorted(wheels.items()):
        name, version = _identity(filename)
        digest = _digest(path)
        if name == "cifereca" and core_version is not None and Version(version) == Version(core_version):
            records.append({"filename": filename, "name": name, "version": version,
                            "sha256": digest, "origin": "local-core"})
            continue
        registry = read_release(name, version)
        if not isinstance(registry, dict) or not isinstance(registry.get("urls"), list):
            _reject()
        matches = [item for item in registry["urls"]
                   if isinstance(item, dict) and item.get("filename") == filename]
        if len(matches) != 1:
            _reject()
        item = matches[0]
        url = item.get("url")
        if not isinstance(url, str):
            _reject()
        plan = {"install": [{
            "metadata": {"name": name, "version": version},
            "download_info": {"url": url, "archive_info": {"hashes": {"sha256": digest}}},
        }]}
        # Reuse the online admission contract (HTTPS origin, official PyPI
        # filename/SHA, non-yanked, exact wheel age).
        check_pip_report(plan, now=current, read_release=lambda _n, _v: registry,
                         quarantine_hours=selected_hours)
        records.append({
            "filename": filename, "name": name, "version": version,
            "sha256": digest, "origin": "pypi", "url": url,
            "uploaded_at": item["upload_time_iso_8601"],
        })
    payload = {"schema": 1, "wheels": records}
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        _reject()
    destination = directory / MANIFEST
    temporary = directory / (MANIFEST + ".tmp")
    # No manifest from a prior or failed build should be reused.
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except OSError:
        _reject()
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def verify_wheelhouse(
    directory: Path, *, core_version: str | None = None,
    now: datetime | None = None, quarantine_hours: object = _UNSET,
) -> tuple[Path, ...]:
    """Consumer preflight for a manifest carried by a TRUSTED release artifact.

    No network; compare complete directory inventory, canonical name/version,
    SHA-256, UTC artifact age and local Core exception. Fail closed otherwise.
    """
    minimum_age, selected_hours = quarantine_duration(quarantine_hours)
    current = _utc(now)
    directory = Path(directory)
    wheels = _wheels(directory)
    path = directory / MANIFEST
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_MANIFEST_BYTES:
            _reject()
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        _reject()
    if (not isinstance(manifest, dict) or manifest.get("schema") != 1
            or not isinstance(manifest.get("wheels"), list)
            or len(manifest["wheels"]) != len(wheels)):
        _reject()
    verified: set[str] = set()
    local_core = 0
    for record in manifest["wheels"]:
        if not isinstance(record, dict):
            _reject()
        filename, name, version, digest = (
            record.get("filename"), record.get("name"),
            record.get("version"), record.get("sha256"),
        )
        if (not isinstance(filename, str) or filename not in wheels or filename in verified
                or not isinstance(name, str) or not isinstance(version, str)
                or not isinstance(digest, str) or SHA.fullmatch(digest) is None):
            _reject()
        verified.add(filename)
        if _identity(filename) != (name, version) or _digest(wheels[filename]) != digest:
            _reject("dependency_not_eligible")
        if record.get("origin") == "local-core":
            if (name != "cifereca" or core_version is None
                    or Version(version) != Version(core_version)):
                _reject()
            local_core += 1
        elif record.get("origin") == "pypi":
            if not isinstance(record.get("url"), str):
                _reject()
            # The trusted attester checked official PyPI URL, timestamp and
            # wheel identity. The verifier checks schema + time + local bytes.
            from urllib.parse import urlsplit
            url = urlsplit(record["url"])
            if (url.scheme != "https" or url.netloc != "files.pythonhosted.org"
                    or url.query or url.fragment or url.path.rsplit("/", 1)[-1] != filename):
                _reject()
            _timestamp(record.get("uploaded_at"), current,
                       minimum_age=minimum_age, quarantine_hours=selected_hours)
        else:
            _reject()
    if local_core > 1:
        _reject("dependency_not_eligible")
    return tuple(wheels[name] for name in sorted(verified))
