"""#577 — verify actual, selected plugins-only receipts in an isolated runtime.

This is a *post-install gate*, not proof of Cosign verification or a live GTK
reload. Never inspect or print a user's HOME; CI supplies a temporary store.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


INDEX_FILE = "cifereca-plugins-only.json"
RECEIPT_FILE = ".cifereca-last-plugin-update.json"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SNAPSHOT = re.compile(r"[A-Za-z0-9._+-]{1,128}\Z")
_COMMIT = re.compile(r"[0-9a-f]{7,40}\Z")
_VERSION = re.compile(r"[A-Za-z0-9._+-]{1,64}\Z")
_MAX_INDEX = 256 * 1024
_MAX_RECEIPT = 2048


class InstalledReceiptRefused(ValueError):
    """A package receipt cannot be matched to the installed ZIP."""


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid(value: object, pattern: re.Pattern[str]) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def verify_installed_receipts(
    archive: Path, store: Path, *, expected_sha256: str,
) -> dict[str, object]:
    """Check every selected receipt against the *actual* archive and index.

    The updater is separately required to have succeeded in strict mode.
    Here, even an updater exit code 0 is not sufficient: the package-specific
    installed receipts must agree with the selected bytes and tested Core.
    """
    if not _valid(expected_sha256, _DIGEST):
        raise InstalledReceiptRefused("Invalid expected archive digest")
    try:
        if _digest_file(archive) != expected_sha256:
            raise InstalledReceiptRefused("Installed archive digest mismatch")
        with zipfile.ZipFile(archive) as selected_zip:
            names = [name for name in selected_zip.namelist()
                     if name.endswith("/" + INDEX_FILE)]
            if len(names) != 1:
                raise InstalledReceiptRefused("Missing or ambiguous plugins-only index")
            item = selected_zip.getinfo(names[0])
            if not 0 < item.file_size <= _MAX_INDEX:
                raise InstalledReceiptRefused("Plugins-only index exceeds bounds")
            index = json.loads(selected_zip.read(item))
            if type(index) is not dict:
                raise InstalledReceiptRefused("Invalid plugins-only index")
            snapshot_id = index.get("id")
            source_commit = index.get("source_git_commit")
            core_version = index.get("core_version_tested")
            records = index.get("packages")
            if (not _valid(snapshot_id, _SNAPSHOT)
                    or names[0] != f"{snapshot_id}/{INDEX_FILE}"
                    or not _valid(source_commit, _COMMIT)
                    or not _valid(core_version, _VERSION)
                    or type(records) is not list or not records
                    or len(records) > 150):
                raise InstalledReceiptRefused("Malformed plugin selection or index")

            seen: set[str] = set()
            for row in records:
                if type(row) is not dict:
                    raise InstalledReceiptRefused("Invalid indexed package")
                package_id = row.get("package_id")
                version = row.get("version")
                member = row.get("path")
                member_sha = row.get("sha256")
                if (not _valid(package_id, _ID)
                        or not _valid(version, _VERSION)
                        or not _valid(member_sha, _DIGEST)
                        or type(member) is not str or len(member) > 256
                        or package_id in seen):
                    raise InstalledReceiptRefused("Unsafe or duplicate indexed package")
                seen.add(package_id)
                part = PurePosixPath(member)
                if (part.is_absolute() or not part.parts
                        or any(segment in (".", "..") for segment in member.split("/"))
                        or "\\" in member):
                    raise InstalledReceiptRefused("Unsafe indexed archive member")
                try:
                    package_zip = selected_zip.open(f"{snapshot_id}/{member}")
                except KeyError:
                    raise InstalledReceiptRefused("Selected package ZIP absent") from None
                package_digest = hashlib.sha256()
                with package_zip:
                    for chunk in iter(lambda: package_zip.read(1024 * 1024), b""):
                        package_digest.update(chunk)
                if package_digest.hexdigest() != member_sha:
                    raise InstalledReceiptRefused("Indexed package SHA-256 mismatch")

                directory = store / package_id
                receipt_path = directory / RECEIPT_FILE
                if directory.is_symlink() or receipt_path.is_symlink():
                    raise InstalledReceiptRefused("Refusing symlinked package receipt")
                if not directory.is_dir() or not receipt_path.is_file():
                    raise InstalledReceiptRefused("Missing installed package receipt")
                if not 0 < receipt_path.stat().st_size <= _MAX_RECEIPT:
                    raise InstalledReceiptRefused("Installed receipt exceeds bounds")
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                expected = {
                    "schema": 1,
                    "scope": "plugins-only",
                    "package_id": package_id,
                    "version": version,
                    "snapshot_id": snapshot_id,
                    "source_git_commit": source_commit,
                    "core_version_tested": core_version,
                    "snapshot_sha256": expected_sha256,
                    "package_sha256": member_sha,
                    "trusted_digest_verified": True,
                    "signature_verified": False,
                }
                if (type(receipt) is not dict
                        or type(receipt.get("schema")) is not int
                        or type(receipt.get("trusted_digest_verified")) is not bool
                        or receipt.get("signature_verified") is not False
                        or receipt != expected):
                    raise InstalledReceiptRefused("Installed receipt disagrees with selected package")
    except (OSError, zipfile.BadZipFile, UnicodeError, json.JSONDecodeError) as exc:
        raise InstalledReceiptRefused("Unreadable archive or installed receipt") from None

    # Do not include paths, source content, credentials or artifact URLs.
    return {"ok": True, "selected_packages": len(seen),
            "snapshot_sha256": expected_sha256, "signature_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--store", required=True, type=Path)
    parser.add_argument("--snapshot-sha256", required=True)
    args = parser.parse_args()
    try:
        result = verify_installed_receipts(
            args.archive, args.store, expected_sha256=args.snapshot_sha256,
        )
    except InstalledReceiptRefused as exc:
        print("Plugins-only installed provenance blocked: " + str(exc))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
