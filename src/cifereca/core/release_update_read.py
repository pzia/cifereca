"""#1005 — fail-closed, read-only public update broker, NOT an installer.

An announcement from GitHub REST is untrusted release *metadata*, not an
authenticated artifact.  No checksum in an announcement, adjacent .sha256
sidecar or unsigned manifest can authorize update.apply.  The existing
updater owns the Core+plugins transaction and remains wholly unreachable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from packaging.version import InvalidVersion, Version

from ..plugin_api.models import ReleaseInstallationSnapshot, ReleaseUpdatePreview

_OFFICIAL = "pzia/cifereca"
_SAFE_VERSION = re.compile(r"v?[0-9][A-Za-z0-9+_.-]{0,79}\Z", re.ASCII)
_MAX_INSTALL_MANIFEST_BYTES = 4096


def _version(value: object) -> str:
    if type(value) is not str or not _SAFE_VERSION.fullmatch(value):
        return ""
    try:
        Version(value.removeprefix("v"))
    except InvalidVersion:
        return ""
    return value


@dataclass(slots=True)
class ReleaseUpdateReadBrokerImpl:
    """A small, optional broker exposed at context.broker('release.update.read').

    This is not the updater's trusted source of provenance, nor a transaction
    handle.  In particular, it intentionally has no apply, install, download,
    authenticate, subprocess, or restart method.
    """

    root_override: Path | None = None

    def installation(self) -> ReleaseInstallationSnapshot:
        from ..updater import install_root

        try:
            root = Path(self.root_override) if self.root_override is not None else install_root()
            # Resolve through one *explicit* Core-owned "current" symlink;
            # ignore untrusted pointers outside managed releases.
            pointer = root / "current"
            if not pointer.is_symlink():
                return ReleaseInstallationSnapshot(state="not_installed")
            release = pointer.resolve(strict=True)
            allowed_parent = (root / "releases").resolve(strict=True)
            if release.parent != allowed_parent or not release.is_dir():
                return ReleaseInstallationSnapshot(state="unknown")
            manifest = release / ".cifereca-install.json"
            if not manifest.is_file() or manifest.is_symlink():
                return ReleaseInstallationSnapshot(state="unknown")
            if manifest.stat().st_size > _MAX_INSTALL_MANIFEST_BYTES:
                return ReleaseInstallationSnapshot(state="unknown")
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            if type(payload) is not dict:
                return ReleaseInstallationSnapshot(state="unknown")
            ver = _version(payload.get("version"))
            return ReleaseInstallationSnapshot(
                state="installed" if ver else "unknown", version=ver,
            )
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
            return ReleaseInstallationSnapshot(state="unknown")

    def preview_release(self, announcement: Mapping[str, object]) -> ReleaseUpdatePreview:
        """Pure policy preview, never an attestation, approval or update plan.

        Every possible input is blocked until Core has a verified byte stream
        and independently trusted origin attestation bound to those bytes.
        """
        if type(announcement) is not dict or len(announcement) > 32:
            return ReleaseUpdatePreview(state="blocked", reason_code="invalid_announcement")
        repository = announcement.get("repository")
        if type(repository) is not str or len(repository) > 128:
            return ReleaseUpdatePreview(state="blocked", reason_code="invalid_announcement")
        if repository != _OFFICIAL:
            return ReleaseUpdatePreview(state="blocked", reason_code="not_official_repository")
        if announcement.get("draft") is not False or announcement.get("published_at") is None:
            return ReleaseUpdatePreview(state="blocked", reason_code="not_published")
        if announcement.get("prerelease") is not False:
            # V1 supports official stable announcements only, never an implicit
            # channel upgrade. Later phases need user-selected channels.
            return ReleaseUpdatePreview(state="blocked", reason_code="channel_not_approved")
        ver = _version(announcement.get("tag_name"))
        if not ver:
            return ReleaseUpdatePreview(state="blocked", reason_code="invalid_announcement")
        # Do NOT inspect/trust user-supplied checksums, Sigstore JSON, release
        # assets, URLs or "verified": true booleans. They are not byte evidence.
        return ReleaseUpdatePreview(
            state="blocked", reason_code="provenance_not_verified",
            version=ver,
        )


__all__ = ["ReleaseUpdateReadBrokerImpl"]
