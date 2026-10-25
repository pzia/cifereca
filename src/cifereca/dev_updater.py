from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from . import updater
from .update_progress import step, update_phase, update_operation, skip_update_phase, jsonl_progress_writer, child_progress_receiver, SMOKE_FAILURE_CODES
from .update_child import PROGRESS_ENV, ChildOutputError, run_plugin_child
from .paths import data_dir, state_dir
from .install_paths import ArchiveNotFoundError, resolve_archive

DEV_MANIFEST = "cifereca-dev.json"
PLUGIN_MANIFEST = "cifereca-plugin.json"
PARTIAL_MANIFEST = "cifereca-plugins-only.json"
PARTIAL_KIND = "cifereca-plugins-only-preview"
DEV_FORMAT = 1
MAX_FILES = 30000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
# Only fixed, non-personal Core runtime classifications may accompany an
# identity attested independently from the selected package manifest.
SMOKE_FAILURE_REASONS = frozenset({
    "activation_failed", "contract_mismatch", "missing_service",
    "dependency_missing", "dependency_version", "dependency_disabled",
    "dependency_cycle", "conflict", "configuration", "system_requirement",
    "missing_broker", "plugin_api", "unknown",
})
MAX_SMOKE_FAILURE_DETAILS = 8
# Per-package watchdog. The release orchestrator has its own, longer deadline
# for sequential packages; progress never resets either deadline.
PLUGIN_INSTALL_TIMEOUT_SECONDS = 300


class DevUpdateError(RuntimeError):
    pass


class PluginSmokeError(DevUpdateError):
    """Only closed smoke evidence may cross the updater/rollback boundary."""

    def __init__(self, message: str, *, reason_code: str,
                 contributions_error: int = 0, contributions_incompatible: int = 0,
                 active_packages_missing: int = 0,
                 failed_contributions: list[dict[str, str]] | None = None):
        if reason_code not in SMOKE_FAILURE_CODES:
            raise ValueError("invalid_smoke_failure_code")
        counts = {
            "contributions_error": contributions_error,
            "contributions_incompatible": contributions_incompatible,
            "active_packages_missing": active_packages_missing,
        }
        if any(type(value) is not int or not 0 <= value <= 2048 for value in counts.values()):
            raise ValueError("invalid_smoke_failure_counts")
        # Even the constructor message is not an authority for public output.
        # Smoke failures are always emitted from a literal closed vocabulary.
        messages = {
            "smoke_cli_failed": "smoke plugins échoué",
            "smoke_json_invalid": "smoke plugins: JSON invalide",
            "smoke_inventory_invalid": "smoke plugins: liste JSON attendue",
            "smoke_contributions_failed": "smoke plugins: contributions installées en erreur",
            "smoke_active_missing": "smoke plugins: packages actifs absents de l'inventaire",
        }
        super().__init__(messages[reason_code])
        self.reason_code = reason_code
        self.smoke_counts = counts
        failures = failed_contributions if failed_contributions is not None else []
        if (type(failures) is not list or len(failures) > MAX_SMOKE_FAILURE_DETAILS
                or any(type(row) is not dict or set(row) != {
                    "package_id", "plugin_id", "reason_code",
                } or any(type(v) is not str for v in row.values())
                    or row["reason_code"] not in SMOKE_FAILURE_REASONS
                    for row in failures)):
            raise ValueError("invalid_smoke_failure_details")
        self.failed_contributions = [dict(row) for row in failures]


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename.replace("\\", "/")
    p = PurePosixPath(name)
    wp = PureWindowsPath(name)
    mode = (info.external_attr >> 16) & 0xFFFF
    if not name or p.is_absolute() or wp.is_absolute() or bool(wp.drive) or ".." in p.parts:
        raise DevUpdateError("chemin interdit dans le snapshot dev")
    if stat.S_ISLNK(mode):
        raise DevUpdateError("symlink ZIP interdit dans le snapshot dev")
    return p


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_plugin_package_id(archive: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(archive, "r") as z:
            raw = json.loads(z.read(PLUGIN_MANIFEST).decode("utf-8"))
    except Exception as exc:
        raise DevUpdateError("plugin ZIP invalide") from exc
    package = raw.get("package") if isinstance(raw, dict) else None
    if not isinstance(package, dict) or not package.get("id") or not package.get("version"):
        raise DevUpdateError("manifest plugin incomplet")
    package_id = str(package['id'])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', package_id):
        raise DevUpdateError('identifiant package plugin invalide')
    # Validate the complete manifest before any mutation, including entrypoints/API.
    try:
        from .plugins.manifest import parse_manifest
        parse_manifest(raw)
    except Exception as exc:
        raise DevUpdateError('manifest plugin invalide') from exc
    return package_id, str(package['version'])


def _extract_snapshot(archive: Path, destination: Path) -> tuple[Path, dict[str, Any]]:
    try:
        z = zipfile.ZipFile(archive, "r")
    except Exception as exc:
        raise DevUpdateError("snapshot dev ZIP illisible") from exc
    with z:
        infos = z.infolist()
        if len(infos) > MAX_FILES:
            raise DevUpdateError("snapshot dev contient trop de fichiers")
        total = 0
        roots: set[str] = set()
        for info in infos:
            p = _safe_member(info)
            total += int(info.file_size)
            if total > MAX_UNCOMPRESSED_BYTES:
                raise DevUpdateError("snapshot dev trop volumineux après extraction")
            roots.add(p.parts[0])
        if len(roots) != 1:
            raise DevUpdateError("snapshot dev doit contenir une racine unique")
        for info in infos:
            p = _safe_member(info)
            target = destination.joinpath(*p.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    root = destination / next(iter(roots))
    manifest_path = root / DEV_MANIFEST
    if not manifest_path.is_file():
        raise DevUpdateError(f"{DEV_MANIFEST} absent du snapshot")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DevUpdateError("manifest dev invalide") from exc
    if not isinstance(manifest, dict) or type(manifest.get("format")) is not int or manifest["format"] != DEV_FORMAT:
        raise DevUpdateError("format de snapshot dev non supporté")
    if str(manifest.get("kind") or "") != "cifereca-dev-snapshot":
        raise DevUpdateError("type de snapshot dev invalide")
    return root, manifest


def _plugin_store_root() -> Path:
    return data_dir() / "plugins" / "packages"


def _backup_plugins(package_ids: list[str], backup_root: Path) -> None:
    store = _plugin_store_root()
    backup_root.mkdir(parents=True, exist_ok=True)
    for package_id in package_ids:
        source = store / package_id
        if source.exists():
            shutil.copytree(source, backup_root / package_id, symlinks=False)


def _restore_plugins(package_ids: list[str], backup_root: Path) -> None:
    store = _plugin_store_root()
    store.mkdir(parents=True, exist_ok=True)
    for package_id in package_ids:
        target = store / package_id
        if target.exists():
            shutil.rmtree(target)
        saved = backup_root / package_id
        if saved.exists():
            shutil.copytree(saved, target, symlinks=False)


def _new_cli() -> Path:
    current = updater.install_root() / "current"
    return updater._command_path(current.resolve(), "cifereca-cli")


def _package_is_active(package_id: str) -> bool | None:
    root = _plugin_store_root() / package_id
    if not root.exists():
        return None
    pointer = root / "current.json"
    if not pointer.exists():
        return False
    try:
        value = json.loads(pointer.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(isinstance(value, dict) and value.get("active"))


def _install_plugin_replace(archive: Path, *, activate: bool) -> None:
    cli = _new_cli()
    command = [str(cli), "plugin", "install", str(archive), "--replace"]
    if not activate:
        command.append("--no-activate")
    # A single installer can invoke pip and activation subprocesses. Stream
    # bounded typed phase records and terminate the whole process group on
    # timeout or Ctrl-C BEFORE restoring package directories. Never forward
    # arbitrary stdout/stderr/argv from the installer.
    try:
        proc = run_plugin_child(
            command, timeout=PLUGIN_INSTALL_TIMEOUT_SECONDS, on_line=child_progress_receiver(),
        )
    except TimeoutError:
        raise TimeoutError("Délai installation plugin dépassé") from None
    except (OSError, UnicodeError, ChildOutputError):
        raise DevUpdateError("sous-processus plugin indisponible") from None
    if proc.returncode != 0:
        raise DevUpdateError("installation plugin dev échouée")


def _verified_smoke_failures(
    rows: list[dict[str, Any]], package_ids: list[str] | None,
) -> list[dict[str, str]]:
    """Allowlist only plugin identities from actually installed manifests.

    The CLI's state/reason are never trusted to mint an ID. Even on failure,
    no free-form error, display label, path, credential, URL or log is copied.
    """
    if package_ids is None:
        return []
    try:
        from .plugins.packages import PackageStore
        store = PackageStore(_plugin_store_root())
        allowed: set[tuple[str, str]] = set()
        for package_id in package_ids:
            if store.active_version(package_id) is None:
                continue
            manifest = store.load(package_id).manifest
            if manifest.package.id != package_id:
                return []
            allowed.update((package_id, item.id) for item in manifest.plugins)
    except Exception:
        return []
    found: list[dict[str, str]] = []
    for row in rows:
        if row["state"] not in {"error", "incompatible"}:
            continue
        package_id, plugin_id = row["package_id"], row.get("plugin_id")
        if type(plugin_id) is not str or (package_id, plugin_id) not in allowed:
            continue
        raw_reason = row.get("reason_code")
        reason = (raw_reason if type(raw_reason) is str
                  and raw_reason in SMOKE_FAILURE_REASONS else "unknown")
        item = {"package_id": package_id, "plugin_id": plugin_id,
                "reason_code": reason}
        if item not in found:
            found.append(item)
        if len(found) > MAX_SMOKE_FAILURE_DETAILS:
            return []
    return found


def _smoke_plugins(package_ids: list[str] | None = None) -> dict[str, Any]:
    cli = _new_cli()
    proc = updater._run_bounded_process(
        [str(cli), "plugin", "list"], text=True, capture_output=True, timeout=120, check=False,
    )
    if proc.returncode != 0:
        raise PluginSmokeError("smoke plugins échoué", reason_code="smoke_cli_failed")
    try:
        rows = json.loads(proc.stdout)
    except (ValueError, TypeError, RecursionError):
        raise PluginSmokeError("smoke plugins: JSON invalide", reason_code="smoke_json_invalid") from None
    if (type(rows) is not list or len(rows) > 4096
            or any(type(row) is not dict
                   or type(row.get("package_id")) is not str
                   or type(row.get("state")) is not str for row in rows)):
        raise PluginSmokeError("smoke plugins: liste JSON attendue", reason_code="smoke_inventory_invalid")
    # Match only exact package IDs and closed states. Do not propagate plugin
    # names, IDs, version text, exception details or CLI stdout into errors.
    selected = [
        row for row in rows
        if package_ids is None or row["package_id"] in package_ids
    ]
    error_count = min(2048, sum(row["state"] == "error" for row in selected))
    incompatible_count = min(2048, sum(row["state"] == "incompatible" for row in selected))
    if error_count or incompatible_count:
        raise PluginSmokeError(
            "smoke plugins: contributions installées en erreur",
            reason_code="smoke_contributions_failed",
            contributions_error=error_count,
            contributions_incompatible=incompatible_count,
            failed_contributions=_verified_smoke_failures(selected, package_ids),
        )
    if package_ids is not None:
        # Inactive packages have no active contribution and must never be
        # activated just to make smoke pass. Missing active packages are
        # classified without revealing their names.
        visible = {row["package_id"] for row in rows}
        missing_count = min(2048, sum(
            package_id not in visible and _package_is_active(package_id) is not False
            for package_id in package_ids
        ))
        if missing_count:
            raise PluginSmokeError(
                "smoke plugins: packages actifs absents de l'inventaire",
                reason_code="smoke_active_missing",
                active_packages_missing=missing_count,
            )
    return {"plugin_count": len(rows)}




def _verify_strict_installed_packages(
    packages: list[tuple[str, str, Path, bool]],
) -> None:
    """Attest the *installed* payload, not merely the plugin-list response.

    An install command returning zero can leave an old version activated or a
    partial version directory in place. Official plugins-only updates must
    reject those states and roll back before claiming success. Validation
    reads the selected ZIP and the actual package store; it makes no network
    requests and never modifies the installed files.
    """
    from .plugins.errors import PackageInstallError
    from .plugins.packages import PackageInstaller

    store = _plugin_store_root()
    for package_id, version, archive, activate in packages:
        package_root = store / package_id
        installed_root = package_root / version
        metadata_path = installed_root / ".cifereca-install.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            digest = metadata["sha256"]
            if not isinstance(digest, str) or not hmac.compare_digest(
                digest.lower(), _sha256(archive)
            ):
                raise ValueError("install checksum mismatch")
            with zipfile.ZipFile(archive) as source:
                infos = source.infolist()
                # The package installer validates archive member paths before
                # extraction. Repeat that check at the verification boundary.
                for info in infos:
                    PackageInstaller._validate_member(info)
                    relative = _safe_member(info)
                    target = installed_root.joinpath(*relative.parts)
                    if info.is_dir():
                        if not target.is_dir() or target.is_symlink():
                            raise ValueError("missing installed directory")
                        continue
                    if not target.is_file() or target.is_symlink():
                        raise ValueError("missing installed file")
                    with source.open(info) as expected:
                        expected_hash = hashlib.sha256()
                        for chunk in iter(lambda: expected.read(1024 * 1024), b""):
                            expected_hash.update(chunk)
                    if not hmac.compare_digest(expected_hash.hexdigest(), _sha256(target)):
                        raise ValueError("installed file differs from selected archive")
            pointer_path = package_root / "current.json"
            pointer = json.loads(pointer_path.read_text(encoding="utf-8")) if pointer_path.is_file() else {}
            if not isinstance(pointer, dict):
                raise ValueError("invalid activation pointer")
            active = pointer.get("active")
            if activate and active != version:
                raise ValueError("selected version not active")
            if not activate and active:
                raise ValueError("disabled package unexpectedly activated")
        except (OSError, ValueError, TypeError, KeyError, IndexError, zipfile.BadZipFile, PackageInstallError) as exc:
            raise DevUpdateError(
                f"plugins-only strict : installation non conforme pour {package_id} "
                f"{version} (intégrité/version/activation) ; rollback requis"
            ) from exc


def _installed_target_versions() -> tuple[str, str]:
    """Read the *target runtime*, not the updater process's import metadata."""
    current = updater.install_root() / "current"
    try:
        release = current.resolve(strict=True)
        core_version = updater._metadata(release)[1]
        source = (release / "src" / "cifereca" / "plugin_api" / "version.py").read_text(
            encoding="utf-8",
        )
        declarations = [
            node.value for node in ast.parse(source).body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "PLUGIN_API_VERSION"
                for target in node.targets
            )
        ]
        if len(declarations) != 1:
            raise ValueError("PLUGIN_API_VERSION absent ou ambigu")
        api_version = str(Version(ast.literal_eval(declarations[0])))
        return core_version, api_version
    except (OSError, ValueError, TypeError, SyntaxError, RuntimeError, updater.UpdateError) as exc:
        raise DevUpdateError(
            "runtime Core cible ou version Plugin API illisible ; "
            "installation plugins-only refusée"
        ) from exc


def _validate_plugins_only_index(
    root: Path,
    manifest: dict[str, Any],
    package_files: list[tuple[str, Path]],
) -> dict[str, Any] | None:
    """Fail closed on an indexed plugins-only snapshot, before any mutation.

    Existing generic dev snapshots without this optional index keep their
    historical behavior. The published preview index itself is a contract:
    tested Core version is exact, not a range or a forward-compatibility claim.
    """
    scope = manifest.get("scope")
    if scope not in (None, "plugins-only"):
        raise DevUpdateError("portée du snapshot inconnue ou invalide")
    index_file = root / PARTIAL_MANIFEST
    if not index_file.exists():
        if scope == "plugins-only":
            raise DevUpdateError(
                "snapshot plugins-only marqué sans index de provenance : installation refusée"
            )
        return None
    if not index_file.is_file():
        raise DevUpdateError("index plugins-only invalide")
    try:
        index = json.loads(index_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise DevUpdateError("index plugins-only illisible ou invalide") from exc
    if (not isinstance(index, dict)
            or type(index.get("format")) is not int or index["format"] != 1
            or index.get("kind") != PARTIAL_KIND
            or index.get("id") != manifest.get("id")
            or not isinstance(index.get("source_git_commit"), str)
            or not index["source_git_commit"].strip()
            or not isinstance(index.get("core_version_tested"), str)
            or not index["core_version_tested"].strip()
            or not isinstance(index.get("packages"), list)):
        raise DevUpdateError("index plugins-only : provenance ou format incohérent")
    if manifest.get("core") or not package_files:
        raise DevUpdateError("snapshot plugins-only : Core interdit ou sélection vide")

    actual = []
    for relative_path, archive in package_files:
        try:
            with zipfile.ZipFile(archive) as package_zip:
                raw = json.loads(package_zip.read(PLUGIN_MANIFEST))
            package = raw["package"]
            plugins = raw["plugins"]
            row = {
                "package_id": package["id"],
                "version": str(package["version"]),
                "plugin_api": str(package.get("plugin_api") or ">=1,<2"),
                "plugins": [
                    {"id": plugin_id, "version": version}
                    for plugin_id, version in sorted({
                        str(plugin["id"]): str(plugin.get("version") or package["version"])
                        for plugin in plugins
                    }.items())
                ],
                "path": relative_path,
                "sha256": _sha256(archive),
            }
        except (KeyError, TypeError, ValueError, OSError, zipfile.BadZipFile) as exc:
            raise DevUpdateError("index plugins-only : package illisible") from exc
        actual.append(row)
    if index["packages"] != sorted(actual, key=lambda row: row["package_id"]):
        raise DevUpdateError(
            "index plugins-only incohérent avec la sélection et les SHA-256 du snapshot"
        )

    target_core, target_api = _installed_target_versions()
    if target_core != index["core_version_tested"]:
        raise DevUpdateError(
            f"Core cible {target_core!r} différent du Core testé "
            f"{index['core_version_tested']!r} : installation refusée"
        )
    try:
        api = Version(target_api)
        for row in actual:
            if api not in SpecifierSet(row["plugin_api"]):
                raise DevUpdateError(
                    f"package {row['package_id']} incompatible avec "
                    f"Plugin API cible {target_api} ({row['plugin_api']})"
                )
    except (InvalidVersion, InvalidSpecifier) as exc:
        raise DevUpdateError("index plugins-only : contrainte Plugin API invalide") from exc
    return index


def _record_partial_install_provenance(
    indexed: dict[str, Any], packages: list[tuple[str, str, Path, bool]],
    *, snapshot_id: str, snapshot_sha256: str, trusted_digest_verified: bool,
) -> None:
    """Persist an informational receipt inside the plugin rollback boundary.

    The digest only proves a match with the caller-supplied SHA; this is NOT
    a Cosign signature verification or proof the GUI loaded the plugin.
    All values are derived from the validated local manifest and ZIP.
    """
    store = _plugin_store_root()
    commit = indexed.get("source_git_commit", "")
    source_commit = commit if type(commit) is str and re.fullmatch(r"[0-9a-f]{7,40}", commit) else ""
    safe_id = snapshot_id if re.fullmatch(r"[A-Za-z0-9._+-]{1,128}", snapshot_id) else ""
    core = indexed.get("core_version_tested")
    safe_core = core if type(core) is str and re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", core) else ""
    for package_id, version, package_archive, _activate in packages:
        directory = store / package_id
        if not directory.is_dir() or directory.is_symlink():
            raise DevUpdateError("plugin provenance : répertoire de package invalide")
        receipt = {
            "schema": 1,
            "scope": "plugins-only",
            "package_id": package_id,
            "version": version,
            "snapshot_id": safe_id,
            "source_git_commit": source_commit,
            "core_version_tested": safe_core,
            "snapshot_sha256": snapshot_sha256,
            "package_sha256": _sha256(package_archive),
            "trusted_digest_verified": trusted_digest_verified,
            # No statement about remote signature or loaded GUI state.
            "signature_verified": False,
        }
        # Atomic replace, even if a stale destination happens to be a symlink.
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory,
                prefix=".cifereca-provenance-", delete=False,
            ) as handle:
                temporary = handle.name
                json.dump(receipt, handle, sort_keys=True, ensure_ascii=True)
                handle.write("\n")
            os.replace(temporary, directory / ".cifereca-last-plugin-update.json")
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


def apply_snapshot(
    archive: Path,
    *,
    python: str | None = None,
    keep: int = 3,
    dry_run: bool = False,
    restart: bool = False,
    expected_sha256: str | None = None,
    require_plugins_only: bool = False,
) -> dict[str, Any]:
    try:
        archive = step("resolve_archive", resolve_archive, archive)
    except ArchiveNotFoundError as exc:
        raise DevUpdateError(str(exc)) from exc
    # An official plugins-only caller must supply its own trusted digest.
    # A marker *inside* the ZIP, or an adjacent .sha256 file, is not an
    # authenticity proof. Keep unmarked historical dev snapshots compatible.
    if require_plugins_only and expected_sha256 is None:
        raise DevUpdateError(
            "mode plugins-only strict : --expected-sha256 obtenu d'un canal fiable requis"
        )
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
            raise DevUpdateError("empreinte SHA-256 attendue invalide")
    with tempfile.TemporaryDirectory(prefix="cifereca-dev-update-") as tmp:
        # Verify the exact bytes that will be extracted. Hashing the original
        # and reopening it later would allow a replacement between check and
        # extraction (TOCTOU), including in the strict official path.
        snapshot = archive
        if expected_sha256 is not None:
            snapshot = Path(tmp) / "verified-snapshot.zip"
            try:
                step("snapshot_copy", shutil.copyfile, archive, snapshot)
                matching = hmac.compare_digest(
                    step("snapshot_sha", _sha256, snapshot), expected_sha256.lower()
                )
            except OSError as exc:
                raise DevUpdateError("snapshot illisible pour vérification SHA-256") from exc
            if not matching:
                raise DevUpdateError("SHA-256 du snapshot incompatible avec la provenance attendue")
        root, manifest = step("snapshot_extract", _extract_snapshot, snapshot, Path(tmp) / "contents")
        snapshot_id = str(manifest.get("id") or archive.stem)
        core_rel = str(manifest.get("core") or "")
        core_zip = (root / core_rel).resolve() if core_rel else None
        if core_zip is not None and (not core_zip.is_file() or root.resolve() not in core_zip.parents):
            raise DevUpdateError("archive core absente ou hors snapshot")
        plugin_items = manifest.get("plugins", [])
        if not isinstance(plugin_items, list):
            raise DevUpdateError("liste plugins invalide")
        plugin_archives: list[tuple[Path, bool]] = []
        indexed_packages: list[tuple[str, Path]] = []
        strict_packages: list[tuple[str, str, Path, bool]] = []
        package_ids: list[str] = []
        for item in plugin_items:
            rel = str(item.get("path") if isinstance(item, dict) else item)
            plugin_zip = (root / rel).resolve()
            if not plugin_zip.is_file() or root.resolve() not in plugin_zip.parents:
                raise DevUpdateError("plugin absent ou hors snapshot")
            package_id, package_version = step("manifest_validation", _read_plugin_package_id, plugin_zip)
            if package_id in package_ids:
                raise DevUpdateError("package plugin dupliqué dans snapshot")
            expected_sha = str(item.get("sha256") or "") if isinstance(item, dict) else ""
            if expected_sha and _sha256(plugin_zip) != expected_sha:
                raise DevUpdateError("SHA-256 plugin invalide")
            installed_active = _package_is_active(package_id)
            requested_activate = bool(item.get("activate", True)) if isinstance(item, dict) else True
            activate = requested_activate if installed_active is None else installed_active
            package_ids.append(package_id)
            plugin_archives.append((plugin_zip, activate))
            indexed_packages.append((rel, plugin_zip))
            strict_packages.append((package_id, package_version, plugin_zip, activate))
        if core_zip is None and not plugin_archives:
            raise DevUpdateError('snapshot vide: core ou plugins requis')
        core_sha = str(manifest.get("core_sha256") or "")
        if core_sha and (core_zip is None or _sha256(core_zip) != core_sha):
            raise DevUpdateError("SHA-256 du core invalide")
        # Indexed partial releases must prove compatibility with the installed
        # Core and API before dry-run, backup, plugin install or Core mutation.
        partial_index = step("manifest_validation", _validate_plugins_only_index, root, manifest, indexed_packages)
        if require_plugins_only and (manifest.get("scope") != "plugins-only" or partial_index is None):
            raise DevUpdateError(
                "mode plugins-only strict : marqueur de portée et index obligatoires"
            )
        result: dict[str, Any] = {
            "ok": True,
            "snapshot_id": snapshot_id,
            "archive": str(archive),
            "sha256": _sha256(snapshot),
            "plugins": package_ids,
            "plugins_only_indexed": partial_index is not None,
            "trusted_digest_verified": expected_sha256 is not None,
            "strict_plugins_only": bool(require_plugins_only),
            "dry_run": bool(dry_run),
        }
        if dry_run:
            result['core'] = updater.install_archive(core_zip, python=python, keep=keep, dry_run=True) if core_zip else None
            result['core_changed'] = core_zip is not None
            # The manifest/index, optional trusted digest and (when present)
            # Core dry-run were checked; no plugin backup or installation,
            # validation of the installed runtime, smoke or restart occurred.
            if core_zip is None:
                skip_update_phase("core_activation")
            for skipped_stage in (
                "plugin_backup", "plugin_install", "plugin_verification",
                "smoke", "restart",
            ):
                skip_update_phase(skipped_stage)
            return result

        # Manifest ids are labels, never filesystem paths. Keep previous backups.
        backup_name = hashlib.sha256(snapshot_id.encode()).hexdigest()[:16] + '-' + str(time.time_ns())
        backup_base = state_dir() / "dev-updates" / backup_name
        backup_plugins = backup_base / "plugins"
        step("plugin_backup", _backup_plugins, package_ids, backup_plugins)
        old_current = updater._symlink_target(updater.install_root() / "current")
        core_installed = False
        core_result = None
        try:
            if core_zip is not None:
                core_result = step("core_activation", updater.install_archive, core_zip, python=python, keep=max(keep, 3), dry_run=False)
                core_installed = True
            # The manifest is fully validated before mutation. The ordinal
            # marks the plugin currently attempted, NOT the number installed:
            # a failure at 2/N must never display 2 successful plugins.
            plugin_total = len(plugin_archives)
            for plugin_index, (plugin_zip, activate) in enumerate(plugin_archives, 1):
                with update_phase(
                    "plugin_install", plugin_index=plugin_index,
                    plugin_total=plugin_total,
                    package_id=package_ids[plugin_index - 1],
                    package_version=strict_packages[plugin_index - 1][1],
                ):
                    _install_plugin_replace(plugin_zip, activate=activate)
            if require_plugins_only:
                step("plugin_verification", _verify_strict_installed_packages, strict_packages)
            smoke = step("smoke", _smoke_plugins, package_ids)
            # Keep this write in the rollback transaction: a failed receipt
            # must not leave an installed package whose origin is unknown.
            if partial_index is not None:
                _record_partial_install_provenance(
                    partial_index, strict_packages,
                    snapshot_id=snapshot_id, snapshot_sha256=result["sha256"],
                    trusted_digest_verified=expected_sha256 is not None,
                )
        except (Exception, KeyboardInterrupt) as exc:
            # A cooperative Ctrl-C must restore the pre-update plugin state,
            # even when the interrupted installation already changed Core.
            restoration_errors = []
            try:
                step("rollback", _restore_plugins, package_ids, backup_plugins)
            except Exception:
                restoration_errors.append('plugins')
            current = updater._symlink_target(updater.install_root() / 'current')
            if core_zip is not None and old_current is not None and old_current.is_dir() and (core_installed or current != old_current):
                try:
                    step("rollback", updater.rollback, old_current.name)
                except Exception:
                    restoration_errors.append('core')
            if restoration_errors:
                raise DevUpdateError('Mise à jour échouée ; restauration incomplète : ' + ', '.join(restoration_errors)) from exc
            raise
        result.update({"core": core_result, "core_changed": core_installed, "smoke": smoke, "backup": str(backup_base)})
        if restart:
            app = updater._command_path(updater.install_root() / "current", "cifereca")
            try:
                # Success attests process launch only, not GTK readiness.
                step("restart", subprocess.Popen, [str(app)], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                result["restarted"] = True
            except Exception as exc:
                result["restarted"] = False
                result["restart_error"] = "redemarrage_impossible"
        return result


def _print(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        for key, val in value.items():
            print(f"{key}: {val}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Updater de snapshots de développement cifereca")
    parser.add_argument("snapshot", nargs="?", help="ZIP snapshot (core et/ou plugins), dossier courant puis Téléchargements XDG")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--expected-sha256", metavar="HEXADECIMAL", help="Empreinte SHA-256 du ZIP obtenue d'une source de confiance, vérifiée avant extraction")
    parser.add_argument("--require-plugins-only", action="store_true", help="Exiger le marqueur/index plugins-only et un --expected-sha256 externe fiable, y compris en dry-run")
    parser.add_argument("--restart", action="store_true", help="Lance la nouvelle application après mise à jour")
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--python")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.snapshot:
        parser.error("snapshot ZIP requis")
    child_progress = os.environ.get(PROGRESS_ENV) == "1"
    try:
        with update_operation(
            "dev_plugins_only" if args.require_plugins_only else "dev_snapshot",
            stream=None if child_progress else sys.stderr,
            callback=jsonl_progress_writer(sys.stderr) if child_progress else None,
        ):
            result = apply_snapshot(
                args.snapshot, python=args.python, keep=args.keep,
                dry_run=args.dry_run, restart=args.restart,
                expected_sha256=args.expected_sha256,
                require_plugins_only=args.require_plugins_only,
            )
        _print(result, args.json)
        return 0
    except KeyboardInterrupt:
        _print({"ok": False, "error": {"code": "interrupted",
               "message": "Mise à jour interrompue"}}, args.json)
        return 130
    except TimeoutError:
        _print({"ok": False, "error": {"code": "dev_update_error",
               "message": "Délai dépassé pour un sous-processus de mise à jour"}}, args.json)
        return 1
    except (DevUpdateError, updater.UpdateError) as exc:
        error = {"code": "dev_update_error", "message": str(exc)}
        if type(exc) is PluginSmokeError:
            error["reason_code"] = exc.reason_code
            error["smoke_counts"] = dict(exc.smoke_counts)
            if exc.failed_contributions:
                error["failed_contributions"] = [dict(row) for row in exc.failed_contributions]
        _print({"ok": False, "error": error}, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
