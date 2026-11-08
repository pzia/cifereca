from __future__ import annotations

import argparse
from contextlib import contextmanager
from functools import wraps
import threading
import hashlib
import io
import json
import re
import shlex
import sqlite3
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote

from .install_paths import ArchiveNotFoundError, resolve_archive
from .paths import config_dir, data_dir, database_file, state_dir
from .update_progress import (step, update_phase, update_operation, skip_update_phase,
                              child_progress_receiver, SMOKE_FAILURE_CODES, SMOKE_COUNT_FIELDS)
from .update_child import run_plugin_child, ChildOutputError

PROJECT_NAME = "cifereca"
PROJECT_DIR_NAME = "cifereca"
MAX_RELEASE_FILES = 20000
MAX_RELEASE_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024

PUBLIC_COMMANDS = (
    "cifereca",
    "cifereca-cli",
    "cifereca-update",
)
DEV_COMMAND = "cifereca-update-dev"
PYTHON_COMMAND = "cifereca-python"  # Runtime wrapper; not a package entry point.
DEFAULT_GITHUB_REPOSITORY = "pzia/cifereca"
TRANSACTION_FILE = ".cifereca-transaction.json"
TRANSACTION_SCHEMA = 1
# Sequential plugin installs have a separate 300s cap *per* package. 21
# installs cannot share a 300s parent deadline; cap the overall release
# child as well and keep strict failure-before-rollback semantics.
RELEASE_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS = 3600


class UpdateError(RuntimeError):
    pass


class ReleasePluginSmokeError(UpdateError):
    """Sanitized child smoke failure, never raw subprocess output."""

    def __init__(self, *, reason_code: str, smoke_counts: dict[str, int],
                 failed_contributions: list[dict[str, str]] | None = None):
        if (reason_code not in SMOKE_FAILURE_CODES
                or type(smoke_counts) is not dict or set(smoke_counts) != SMOKE_COUNT_FIELDS
                or any(type(v) is not int or not 0 <= v <= 2048
                       for v in smoke_counts.values())):
            raise ValueError("invalid_child_smoke_evidence")
        super().__init__("Upgrade des plugins de release échoué")
        self.reason_code = reason_code
        self.smoke_counts = dict(smoke_counts)
        self.failed_contributions = [dict(row) for row in (failed_contributions or [])]



_UPDATER_THREAD_LOCK = threading.RLock()
_UPDATER_LOCK_DEPTH = threading.local()
_UPDATER_INHERITED_LOCK_FD_ENV = "CIFERECA_UPDATER_INHERITED_LOCK_FD"


@contextmanager
def _exclusive_updater_lock(root: Path):
    """Shared process + OS lease, held for the full mutating updater command.

    Never unlink the lock file; all official installations/rollbacks and
    reconciliation must lock the same persistent inode (P0 #5069).
    """
    with _UPDATER_THREAD_LOCK:
        depth = getattr(_UPDATER_LOCK_DEPTH, "count", 0)
        if depth:
            _UPDATER_LOCK_DEPTH.count = depth + 1
            try:
                yield
            finally:
                _UPDATER_LOCK_DEPTH.count -= 1
            return
        # The Core updater activates plugins through a separate DevUpdater
        # process. Pass its *same open file description*: unrelated processes
        # still cannot acquire the kernel lock while Core is active.
        inherited = os.environ.pop(_UPDATER_INHERITED_LOCK_FD_ENV, None)
        owned = inherited is None
        try:
            import fcntl
            root.mkdir(parents=True, exist_ok=True)
            if owned:
                fd = os.open(root / ".cifereca-updater.lock",
                             os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            else:
                if not inherited.isascii() or not inherited.isdecimal():
                    raise UpdateError("Verrou updater délégué invalide")
                fd = int(inherited)
                if fd < 3:
                    raise UpdateError("Verrou updater délégué invalide")
                os.fstat(fd)
        except (ImportError, OSError, OverflowError, ValueError):
            raise UpdateError("Verrou updater indisponible") from None
        try:
            current = os.fstat(fd)
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or current.st_uid != os.getuid()
                    or current.st_mode & 0o077):
                raise UpdateError("Verrou updater non sûr")
            if not owned:
                # An environment variable alone is never an authorization.
                lock_path = root / ".cifereca-updater.lock"
                if lock_path.is_symlink():
                    raise UpdateError("Verrou updater délégué non sûr")
                path_stat = lock_path.stat()
                if (path_stat.st_dev, path_stat.st_ino) != (current.st_dev, current.st_ino):
                    raise UpdateError("Verrou updater délégué non sûr")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise UpdateError("Une opération updater est déjà en cours") from None
            _UPDATER_LOCK_DEPTH.count = 1
            _UPDATER_LOCK_DEPTH.fd = fd
            _UPDATER_LOCK_DEPTH.borrowed = not owned
            try:
                yield
            finally:
                _UPDATER_LOCK_DEPTH.count = 0
                _UPDATER_LOCK_DEPTH.fd = None
                _UPDATER_LOCK_DEPTH.borrowed = False
                if owned:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _serial_updater_operation(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        # Read-only dry-runs retain the existing no-runtime-directory contract.
        if kwargs.get("dry_run", False):
            return func(*args, **kwargs)
        with _exclusive_updater_lock(install_root()):
            return func(*args, **kwargs)
    return wrapped


def _run_bounded_process(command: list[str], *, timeout: int, **kwargs: Any) -> subprocess.CompletedProcess:
    """Bound a child process, preserving no raw argv/stdout/stderr in errors.

    subprocess.run kills and waits for the direct child on timeout. The caller
    owns the update phase and the rollback transaction; no automatic retry.
    """
    try:
        return subprocess.run(command, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired:
        # TimeoutExpired.__str__ contains the original command (possibly
        # sensitive), so expose only a fixed exception with no chained cause.
        raise TimeoutError("Délai dépassé pour un sous-processus de mise à jour") from None
    except OSError:
        raise UpdateError("Sous-processus de mise à jour indisponible") from None


def _home() -> Path:
    return Path.home()


def install_root() -> Path:
    override = os.environ.get("CIFERECA_INSTALL_ROOT")
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override))).resolve()
    data_home = Path(os.environ.get("XDG_DATA_HOME", _home() / ".local" / "share"))
    # Application data (SQLite, etc.) already lives in cifereca.
    # Keep the immutable runtime below a dedicated subdirectory.
    return (data_home / PROJECT_DIR_NAME / "runtime").resolve()


def bin_dir() -> Path:
    override = os.environ.get("CIFERECA_BIN_DIR")
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override))).resolve()
    return (_home() / ".local" / "bin").resolve()


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename.replace("\\", "/")
    p = PurePosixPath(name)
    wp = PureWindowsPath(name)
    if p.is_absolute() or wp.is_absolute() or bool(wp.drive) or ".." in p.parts or not p.parts:
        raise UpdateError("Chemin interdit dans l'archive")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise UpdateError("Symlink ZIP interdit dans l'archive")
    return p


def _extract_release(archive: Path, staging: Path) -> Path:
    try:
        z = zipfile.ZipFile(archive)
    except Exception as exc:
        raise UpdateError("Archive ZIP illisible") from exc
    with z:
        infos = z.infolist()
        if len(infos) > MAX_RELEASE_FILES:
            raise UpdateError(f"Archive trop volumineuse en nombre de fichiers: {len(infos)}")
        members = [x for x in infos if not x.is_dir()]
        if not members:
            raise UpdateError("Archive vide")
        total = 0
        roots: set[str] = set()
        for info in infos:
            p = _safe_member(info)
            total += int(info.file_size)
            if total > MAX_RELEASE_UNCOMPRESSED_BYTES:
                raise UpdateError("Archive trop volumineuse après extraction")
            roots.add(p.parts[0])
        if len(roots) != 1:
            raise UpdateError("Archive de release ambiguë: une seule racine projet est requise")
        root_name = next(iter(roots))
        for info in z.infolist():
            p = _safe_member(info)
            dest = staging.joinpath(*p.parts)
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, dest.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    root = staging / root_name
    if not (root / "pyproject.toml").is_file():
        raise UpdateError("pyproject.toml absent de la release")
    return root


def _metadata(source: Path) -> tuple[str, str]:
    try:
        data = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
        project = data["project"]
        name = str(project["name"])
        version = str(project["version"])
    except Exception as exc:
        raise UpdateError("Metadata pyproject.toml invalides") from exc
    if name != PROJECT_NAME:
        raise UpdateError(f"Projet inattendu: {name}")
    if not version:
        raise UpdateError("Version absente")
    version_file = source / "VERSION"
    if version_file.is_file() and version_file.read_text(encoding="utf-8").strip() != version:
        raise UpdateError("VERSION et pyproject.toml sont incohérents")
    scripts = project.get("scripts") or {}
    missing = [x for x in PUBLIC_COMMANDS if x not in scripts]
    if missing:
        raise UpdateError(f"Entry points publics manquants: {', '.join(missing)}")
    return name, version


def _python_version(exe: str) -> tuple[int, int, int] | None:
    try:
        proc = _run_bounded_process(
            [exe, "-c", "import sys,json;print(json.dumps(list(sys.version_info[:3])))"],
            text=True, capture_output=True, timeout=15, check=False,
        )
        if proc.returncode != 0:
            return None
        value = json.loads(proc.stdout)
        return tuple(int(x) for x in value[:3])  # type: ignore[return-value]
    except Exception:
        return None


def select_python(explicit: str | None = None) -> str:
    candidates: list[str] = []
    for value in (explicit, os.environ.get("CIFERECA_PYTHON"), sys.executable, "python3.14", "python3.13", "python3.12", "python3.11", "python3"):
        if value and value not in candidates:
            candidates.append(value)
    for candidate in candidates:
        resolved = shutil.which(candidate) if os.sep not in candidate else candidate
        if not resolved:
            continue
        version = _python_version(resolved)
        if version and version >= (3, 11, 0):
            return str(resolved)
    raise UpdateError("Aucun Python >= 3.11 compatible n'a été trouvé")


def _copy_source(source: Path, destination: Path) -> None:
    ignore = shutil.ignore_patterns(
        ".git", ".venv", ".updates", "release", "dist", "build", ".pytest_cache", "__pycache__", "*.pyc", "*.pyo"
    )
    shutil.copytree(source, destination, ignore=ignore)


def _create_venv(release: Path, python: str) -> Path:
    # P0 #2770: inspect the complete offline wheel inventory *before* venv
    # creation. A missing/modified proof must not start a pip installation.
    # The manifest belongs to a trusted, signed release archive; standalone
    # manifests do not independently authenticate publication timestamps.
    wheelhouse = release / "offline-wheels"
    if wheelhouse.is_dir():
        version = _metadata(release)[1]
        from .wheelhouse_quarantine import verify_wheelhouse
        from .plugins.python_quarantine import QuarantineRejected, quarantine_diagnostic
        try:
            verified = verify_wheelhouse(wheelhouse, core_version=version)
        except QuarantineRejected as exc:
            raise UpdateError(quarantine_diagnostic(exc)) from None
        core_wheels = sorted(wheelhouse.glob(f"cifereca-{version}-*.whl"))
        if not core_wheels or any(wheel not in verified for wheel in core_wheels):
            raise UpdateError("dependency_not_eligible")
    else:
        # Installing from source allows pip to resolve unverified third-party
        # build/runtime dependencies. A managed update must fail closed before
        # creating a venv when the attested wheelhouse is absent (#2770).
        raise UpdateError("dependency_provenance_unavailable")

    venv = release / ".venv"
    # PyGObject/GTK stays a system dependency, therefore the release venv may
    # see system site-packages. All cifereca Python runtime dependencies are,
    # however, installed from the bundled offline wheelhouse.
    proc = _run_bounded_process(
        [python, "-m", "venv", "--system-site-packages", str(venv)],
        timeout=300, check=False, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        raise UpdateError("Création du venv de release impossible")
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.is_file():
        raise UpdateError("Python du venv absent")
    command = [
        str(py), "-m", "pip", "install", "--disable-pip-version-check",
        "--no-index", "--find-links", str(wheelhouse), "--ignore-installed", f"cifereca=={version}",
    ]
    proc = _run_bounded_process(command, timeout=900, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise UpdateError("Installation du package dans le venv impossible")
    return venv


def _command_path(release: Path, command: str) -> Path:
    sub = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return release / ".venv" / sub / f"{command}{suffix}"


def _smoke(release: Path) -> dict[str, Any]:
    cli = _command_path(release, "cifereca-cli")
    app = _command_path(release, "cifereca")
    updater = _command_path(release, "cifereca-update")
    for path in (cli, app, updater):
        if not path.is_file():
            raise UpdateError(f"Entry point absent après installation: {path.name}")
    proc = _run_bounded_process([str(cli), "--version"], text=True, capture_output=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise UpdateError("Smoke test CLI --version échoué")
    proc = _run_bounded_process([str(app), "--version"], text=True, capture_output=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise UpdateError("Smoke test application --version échoué")
    py = _command_path(release, "cifereca-cli").parent / "python"
    core_code = "import cifereca,httpx,packaging; print(cifereca.__version__)"
    proc = _run_bounded_process([str(py), "-c", core_code], text=True, capture_output=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise UpdateError("Smoke test core/httpx échoué")
    gtk_code = (
        "import gi;"
        "gi.require_version('Gtk','4.0');gi.require_version('Adw','1');"
        "from gi.repository import Gtk,Adw;"
        "assert Gtk.get_major_version()>=4"
    )
    gtk = _run_bounded_process([str(py), "-c", gtk_code], text=True, capture_output=True, timeout=30, check=False)
    return {
        "core_ready": True,
        "desktop_ready": gtk.returncode == 0,
        "desktop_error": "" if gtk.returncode == 0 else "GTK/PyGObject indisponible",
    }


def _symlink_target(path: Path) -> Path | None:
    try:
        if not path.is_symlink():
            return None
        raw = os.readlink(path)
        p = Path(raw)
        return (path.parent / p).resolve() if not p.is_absolute() else p.resolve()
    except OSError:
        return None


def _replace_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f".{link.name}.{os.getpid()}.tmp")
    try:
        tmp.unlink(missing_ok=True)
        relative = os.path.relpath(target, link.parent)
        os.symlink(relative, tmp)
        os.replace(tmp, link)
    finally:
        tmp.unlink(missing_ok=True)


def _launcher_content(command: str, root: Path) -> str:
    target = root / "current" / ".venv" / "bin" / (
        "python" if command == PYTHON_COMMAND else command
    )
    if command == PYTHON_COMMAND:
        # Check active release at invocation time; never use system Python.
        quoted = shlex.quote(str(target))
        return (
            "#!/bin/sh\n"
            f"if [ ! -x {quoted} ]; then\n"
            '  echo "cifereca-python: active interpreter unavailable" >&2\n'
            "  exit 127\n"
            "fi\n"
            f'exec {quoted} "$@"\n'
        )
    return f'#!/bin/sh\nexec "{target}" "$@"\n'


def _write_launchers(root: Path) -> dict[str, str]:
    if os.name == "nt":
        raise UpdateError("Le bootstrap Windows n'est pas encore implémenté dans cette release")
    out: dict[str, str] = {}
    directory = bin_dir()
    directory.mkdir(parents=True, exist_ok=True)
    commands = [*PUBLIC_COMMANDS, PYTHON_COMMAND]
    dev_target = root / "current" / ".venv" / "bin" / DEV_COMMAND
    if dev_target.is_file():
        commands.append(DEV_COMMAND)
    for command in commands:
        dest = directory / command
        tmp = directory / f".{command}.{os.getpid()}.tmp"
        tmp.write_text(_launcher_content(command, root), encoding="utf-8")
        tmp.chmod(0o755)
        os.replace(tmp, dest)
        out[command] = str(dest)
    return out


RELEASE_RETENTION_SECONDS = 24 * 60 * 60
MIN_RETAINED_RELEASES = 2
RETENTION_STATE_FILE = ".cifereca-release-retention.json"
_UNFINISHED_TRANSACTION_STATES = frozenset({
    "prepared", "applying", "restoring", "rolling_back",
    "restoration_incomplete",
})


def _releases(root: Path) -> list[Path]:
    base = root / "releases"
    if not base.is_dir() or base.is_symlink():
        return []
    # Never traverse a symlink posing as a release directory.
    return sorted(
        (x for x in base.iterdir() if x.is_dir() and not x.is_symlink()),
        key=lambda x: x.stat().st_mtime, reverse=True,
    )


def _release_installed_at(release: Path) -> float | None:
    """Deployment time, not the mtime of a file modified after deployment.

    Old installs without installed_at use the immutable release directory
    timestamp if necessary. Invalid explicit timestamps fail closed.
    """
    metadata = release / ".cifereca-install.json"
    if metadata.is_symlink():
        return None
    if metadata.exists():
        try:
            value = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return None
        if type(value) is not dict:
            return None
        raw = value.get("installed_at")
        if raw is not None:
            if type(raw) is not str:
                return None
            from datetime import datetime
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    return None
                return parsed.timestamp()
            except (ValueError, OverflowError, OSError):
                return None
    # Historic releases did not always persist the install time. The
    # directory mtime is the bounded fallback, not nested content mtimes.
    try:
        return release.stat().st_mtime
    except OSError:
        return None


def _gc(root: Path, keep: int) -> dict[str, int]:
    """Post-commit cleanup: retain last 24 h AND >=2 restorable generations.

    Core symlink targets and any incomplete transaction are fail-closed.
    The currently applied transaction's *before_previous* is also needed:
    transactional rollback restores BOTH Core symlinks, not just current.
    No plugin version store, user state, backup, or transaction is purged.
    """
    keep = max(MIN_RETAINED_RELEASES, int(keep))
    now = time.time()
    releases_root = root / "releases"
    if releases_root.is_symlink():
        raise UpdateError("Nettoyage refusé : répertoire de releases non sûr")
    releases = _releases(root)
    by_path = {release.resolve(): release for release in releases}
    protected: set[Path] = set()

    def retain_reference(target: Path | None, *, required: bool = True) -> None:
        if target is None:
            return
        resolved = target.resolve()
        if resolved in by_path:
            protected.add(resolved)
        elif required:
            # Never discard apparently unneeded releases while a rollback
            # pointer refers to an unexpected or missing Core tree.
            raise UpdateError("Nettoyage refusé : référence Core non résolue")

    for link in ("current", "previous"):
        retain_reference(_symlink_target(root / link))

    # An update that has not finished might need any of its before/after
    # releases. The entire cleanup is postponed, including old releases.
    transactions = root / "transactions"
    if transactions.is_symlink():
        raise UpdateError("Nettoyage refusé : journal de transactions non sûr")
    if transactions.is_dir():
        for directory in transactions.iterdir():
            if directory.is_symlink():
                raise UpdateError("Nettoyage refusé : transaction non sûre")
            if not directory.is_dir():
                continue
            transaction = _read_transaction(directory)
            if transaction.get("state") in _UNFINISHED_TRANSACTION_STATES:
                return {
                    "retained": len(releases), "removed": 0,
                    "protected": len(protected), "deferred": 1,
                }
            if transaction.get("state") not in {
                "applied", "rolled_back", "update_failed_restored",
                "reconciled_degraded",
            }:
                raise UpdateError("Nettoyage refusé : état de transaction inconnu")

    active = _symlink_target(root / "current")
    if active is not None and active.is_dir():
        linked = _transaction_for_release(root, active)
        if linked is not None:
            _, transaction = linked
            if transaction.get("state") != "applied":
                raise UpdateError("Nettoyage refusé : transaction active non finalisée")
            core = transaction.get("core")
            if type(core) is not dict:
                raise UpdateError("Nettoyage refusé : preuve de rollback absente")
            retain_reference(_path_from_identity(core.get("before_current")))
            retain_reference(_path_from_identity(core.get("before_previous")))

    # Keep the newest N *distinct deployment trees* regardless of their
    # semantic version; identical version labels can have different octets.
    # The last-24h protection is additive to this minimum.
    dated = [(release, _release_installed_at(release)) for release in releases]
    ordered = sorted(
        dated, key=lambda row: (
            row[1] is not None, row[1] if row[1] is not None else 0.0,
            row[0].name,
        ), reverse=True,
    )
    protected.update(release.resolve() for release, _ in ordered[:keep])
    for release, installed in dated:
        if installed is None or installed > now - RELEASE_RETENTION_SECONDS:
            # Unknown/future times cannot authorize destruction.
            protected.add(release.resolve())

    removed = 0
    for release in releases:
        if release.resolve() in protected:
            continue
        # A concurrent link change must never turn a purge candidate into
        # the active or previous runtime. New transactions defer cleanup.
        for link in ("current", "previous"):
            if _symlink_target(root / link) == release.resolve():
                raise UpdateError("Nettoyage refusé : génération réactivée")
        if transactions.is_dir():
            for directory in transactions.iterdir():
                if directory.is_dir() and not directory.is_symlink():
                    state = _read_transaction(directory).get("state")
                    if state in _UNFINISHED_TRANSACTION_STATES:
                        raise UpdateError("Nettoyage différé : transaction en cours")
        if release.is_symlink() or not release.is_dir():
            raise UpdateError("Nettoyage refusé : génération devenue non sûre")
        # Do not ignore deletion errors: a partial cleanup must be diagnosed.
        shutil.rmtree(release)
        removed += 1

    summary = {
        "retained": len(releases) - removed, "removed": removed,
        "protected": len(protected), "deferred": 0,
    }
    _write_json_atomic(root / RETENTION_STATE_FILE, {
        "max_age_seconds": RELEASE_RETENTION_SECONDS,
        "minimum_generations": keep,
        "last_cleanup_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        **summary,
    })
    return summary


def _backup_persistent_state(label: str) -> Path:
    """Create a local pre-activation backup of mutable user state."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:10]
    base = state_dir() / "update-backups"
    target = base / f"{stamp}-{digest}"
    tmp = base / f".{target.name}.{os.getpid()}.tmp"
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        db_path = database_file()
        if db_path.is_file():
            with sqlite3.connect(db_path) as source_db, sqlite3.connect(tmp / "library.sqlite3") as backup_db:
                source_db.backup(backup_db)

        cfg = config_dir()
        if cfg.is_dir():
            shutil.copytree(cfg, tmp / "config")

        plugins = data_dir() / "plugins"
        if plugins.is_dir():
            shutil.copytree(plugins, tmp / "plugins")

        (tmp / "backup.json").write_text(
            json.dumps({"label": label, "created_at": stamp}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        base.mkdir(parents=True, exist_ok=True)
        os.replace(tmp, target)
        return target
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise UpdateError("Sauvegarde pré-activation impossible") from exc


def _release_plugin_manifest(source: Path) -> dict[str, str]:
    path = source / "src" / "cifereca" / "plugin-release.json"
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise UpdateError("Manifest des plugins de release invalide") from exc
    packages = raw.get("packages") if isinstance(raw, dict) else None
    if not isinstance(packages, dict):
        raise UpdateError("Manifest des plugins de release invalide")
    out: dict[str, str] = {}
    for package_id, row in packages.items():
        if not isinstance(row, dict):
            raise UpdateError(f"Entrée plugin de release invalide: {package_id}")
        version = str(row.get("version") or "").strip()
        if not version:
            raise UpdateError(f"Version plugin de release absente: {package_id}")
        out[str(package_id)] = version
    return out


def _plugin_packages_root() -> Path:
    # Resolving the package store must not allocate XDG data during read-only
    # recovery/retention inspection. Writers create their own directories.
    return data_dir(create=False) / "plugins" / "packages"


def _safe_package_id(package_id: str) -> str:
    value = str(package_id).strip()
    if not value or value in {".", ".."} or Path(value).name != value or "/" in value or "\\" in value:
        raise UpdateError(f"Identifiant de package plugin interdit: {package_id}")
    return value


def _package_identity_at(package_id: str, package: Path) -> dict[str, Any]:
    """Return an exact, deterministic identity for one plugin package directory."""
    package_id = _safe_package_id(package_id)
    if not package.exists():
        return {"exists": False, "active": None, "versions": [], "sha256": None, "files": {}}
    if not package.is_dir() or package.is_symlink():
        raise UpdateError(f"Stockage plugin invalide pour {package_id}")

    files: dict[str, str] = {}
    versions: list[str] = []
    active: Any = None
    for path in sorted(package.rglob("*"), key=lambda item: item.relative_to(package).as_posix()):
        relative = path.relative_to(package).as_posix()
        if path.is_symlink():
            raise UpdateError(f"Symlink interdit dans le package plugin {package_id}: {relative}")
        if path.is_file():
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise UpdateError(f"Entrée plugin invalide pour {package_id}: {relative}")
    pointer = package / "current.json"
    if pointer.is_file():
        try:
            raw = json.loads(pointer.read_text(encoding="utf-8"))
            active = raw.get("active") if isinstance(raw, dict) else None
        except Exception as exc:
            raise UpdateError(f"Pointeur d'activation plugin invalide: {package_id}") from exc
    versions = sorted(
        path.name for path in package.iterdir()
        if path.is_dir() and not path.is_symlink() and path.name != "versions"
    )
    legacy_versions = package / "versions"
    if legacy_versions.is_dir():
        versions.extend(
            path.name for path in legacy_versions.iterdir()
            if path.is_dir() and not path.is_symlink()
        )
        versions = sorted(set(versions))
    content = hashlib.sha256()
    for relative, digest in files.items():
        content.update(relative.encode("utf-8"))
        content.update(b"\0")
        content.update(digest.encode("ascii"))
        content.update(b"\0")
    return {
        "exists": True,
        "active": active,
        "versions": versions,
        "sha256": content.hexdigest(),
        "files": files,
    }


def _package_identity(package_id: str) -> dict[str, Any]:
    package_id = _safe_package_id(package_id)
    return _package_identity_at(package_id, _plugin_packages_root() / package_id)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        try:
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            pass
    finally:
        tmp.unlink(missing_ok=True)


def _core_identity(release: Path | None) -> dict[str, Any] | None:
    if release is None:
        return None
    metadata: dict[str, Any] = {}
    path = release / ".cifereca-install.json"
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                metadata = raw
        except Exception:
            metadata = {}
    version = str(metadata.get("version") or release.name.split("-", 1)[0])
    provenance = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    return {
        "path": str(release.resolve()),
        "name": release.name,
        "version": version,
        "commit": provenance.get("commit"),
        "provenance": provenance,
    }


def _transaction_path(root: Path, transaction_id: str) -> Path:
    return root / "transactions" / transaction_id


def _read_transaction(path: Path) -> dict[str, Any]:
    try:
        value = json.loads((path / "transaction.json").read_text(encoding="utf-8"))
    except Exception as exc:
        raise UpdateError(f"Transaction de mise à jour illisible: {path.name}") from exc
    if not isinstance(value, dict) or value.get("schema") != TRANSACTION_SCHEMA:
        raise UpdateError(f"Transaction de mise à jour incompatible: {path.name}")
    return value


def _save_transaction(path: Path, transaction: dict[str, Any], state: str) -> None:
    transaction["state"] = state
    transaction["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_json_atomic(path / "transaction.json", transaction)


def _create_update_transaction(
    root: Path,
    release: Path,
    old_current: Path | None,
    old_previous: Path | None,
    package_ids: list[str],
) -> tuple[Path, dict[str, Any]]:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    token = hashlib.sha256(f"{release}:{time.time_ns()}".encode()).hexdigest()[:12]
    transaction_id = f"{stamp}-{token}"
    final = _transaction_path(root, transaction_id)
    tmp = final.parent / f".{transaction_id}.{os.getpid()}.tmp"
    packages: dict[str, Any] = {}
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=False)
    try:
        for raw_id in sorted(set(package_ids)):
            package_id = _safe_package_id(raw_id)
            identity = _package_identity(package_id)
            packages[package_id] = {"before": identity, "after": None}
            source = _plugin_packages_root() / package_id
            if identity["exists"]:
                backup = tmp / "plugins" / package_id
                shutil.copytree(source, backup)
                if _package_identity_at(package_id, backup) != identity:
                    raise UpdateError(f"Snapshot plugin incomplet ou incohérent pour {package_id}")
        transaction: dict[str, Any] = {
            "schema": TRANSACTION_SCHEMA,
            "id": transaction_id,
            "state": "prepared",
            "created_at": stamp,
            "core": {
                "before_current": _core_identity(old_current),
                "before_previous": _core_identity(old_previous),
                "after": _core_identity(release),
            },
            "packages": packages,
        }
        _write_json_atomic(tmp / "transaction.json", transaction)
        os.replace(tmp, final)
        _write_json_atomic(release / TRANSACTION_FILE, {"schema": 1, "id": transaction_id})
        return final, transaction
    except Exception as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        if isinstance(exc, UpdateError):
            raise
        raise UpdateError("Snapshot transactionnel des plugins impossible") from exc


def _restore_transaction_packages(path: Path, transaction: dict[str, Any]) -> None:
    root = _plugin_packages_root()
    root.mkdir(parents=True, exist_ok=True)
    for package_id, row in sorted(transaction.get("packages", {}).items()):
        package_id = _safe_package_id(package_id)
        destination = root / package_id
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)
        before = row.get("before") if isinstance(row, dict) else None
        if isinstance(before, dict) and before.get("exists"):
            backup = path / "plugins" / package_id
            if not backup.is_dir():
                raise UpdateError("Backup plugin de restauration absent")
            shutil.copytree(backup, destination)
            if _package_identity(package_id) != before:
                raise UpdateError("Vérification de restauration plugin échouée")


def _smoke_restored_plugins(release: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    package_rows = transaction.get("packages", {})
    if not package_rows:
        return {"plugin_count": 0, "managed_packages": []}
    cli = _command_path(release, "cifereca-cli")
    proc = _run_bounded_process(
        [str(cli), "plugin", "list"], text=True, capture_output=True, timeout=120, check=False,
    )
    if proc.returncode != 0:
        raise UpdateError("Validation des plugins restaurés échouée")
    try:
        rows = json.loads(proc.stdout)
    except Exception as exc:
        raise UpdateError("Validation des plugins restaurés: sortie JSON invalide") from exc
    # The restored runtime is an untrusted inventory. Never echo its plugin
    # IDs or even the (locally stored) package names into public errors.
    if (type(rows) is not list or len(rows) > 4096
            or any(type(row) is not dict
                   or type(row.get("package_id")) is not str
                   or type(row.get("state")) is not str for row in rows)):
        raise UpdateError("Validation des plugins restaurés: liste JSON attendue")
    package_ids = set(package_rows)
    failed_count = sum(
        row["package_id"] in package_ids
        and row["state"] in {"error", "incompatible"}
        for row in rows
    )
    if failed_count:
        raise UpdateError("Plugins restaurés incompatibles ou en erreur")
    visible = {row["package_id"] for row in rows}
    active = {
        package_id for package_id, row in package_rows.items()
        if isinstance(row, dict) and isinstance(row.get("before"), dict)
        and row["before"].get("exists") and row["before"].get("active")
    }
    if active - visible:
        raise UpdateError("Plugins actifs restaurés absents de l'inventaire")
    return {"plugin_count": len(rows), "managed_packages": sorted(package_ids)}


def _transaction_for_release(root: Path, release: Path) -> tuple[Path, dict[str, Any]] | None:
    link = release / TRANSACTION_FILE
    if not link.is_file():
        return None
    try:
        raw = json.loads(link.read_text(encoding="utf-8"))
        transaction_id = _safe_package_id(str(raw["id"]))
    except Exception as exc:
        raise UpdateError(f"Référence de transaction invalide pour {release.name}") from exc
    path = _transaction_path(root, transaction_id)
    transaction = _read_transaction(path)
    if transaction.get("id") != transaction_id:
        raise UpdateError(f"Identité de transaction incohérente pour {release.name}")
    return path, transaction


def _path_from_identity(value: Any) -> Path | None:
    if not isinstance(value, dict) or not value.get("path"):
        return None
    return Path(str(value["path"]))


def _verify_restored_transaction_state(root: Path, transaction: dict[str, Any]) -> None:
    """Read-only proof after rollback: both Core links and every managed package.

    A successful copy or CLI smoke does not prove that absent packages stayed
    absent, nor that Core symlinks still target their pre-update releases.
    Consume only the transaction's own pre-mutation identities. No writes,
    network calls, plugin activation or untrusted fields in public failures.
    """
    core = transaction.get("core")
    packages = transaction.get("packages")
    if type(core) is not dict or type(packages) is not dict:
        raise UpdateError("rollback_evidence_unavailable")
    for link, key in (("current", "before_current"), ("previous", "before_previous")):
        expected = _path_from_identity(core.get(key))
        actual = _symlink_target(root / link)
        if ((expected is None) != (actual is None)
                or (expected is not None and (
                    not expected.is_dir() or actual is None
                    or actual.resolve() != expected.resolve()
                ))):
            raise UpdateError("rollback_core_state_mismatch")
    for package_id, row in sorted(packages.items()):
        before = row.get("before") if type(row) is dict else None
        if type(before) is not dict:
            raise UpdateError("rollback_evidence_unavailable")
        try:
            after = _package_identity(package_id)
        except Exception:
            raise UpdateError("rollback_package_state_unavailable") from None
        if after != before:
            raise UpdateError("rollback_package_state_mismatch")


def _complete_transaction_restore(root: Path, path: Path, transaction: dict[str, Any], state: str) -> None:
    """Idempotently complete a failed update or an interrupted voluntary rollback."""
    _save_transaction(path, transaction, state)
    errors: list[str] = []
    try:
        _restore_transaction_packages(path, transaction)
    except Exception as exc:
        errors.append("plugins")
    core = transaction.get("core") if isinstance(transaction.get("core"), dict) else {}
    try:
        _restore_release_links(
            root,
            _path_from_identity(core.get("before_current")),
            _path_from_identity(core.get("before_previous")),
        )
        if _path_from_identity(core.get("before_current")) is not None:
            _write_launchers(root)
            transaction["restoration"] = _smoke_restored_plugins(
                _path_from_identity(core["before_current"]), transaction,
            )
        else:
            for command in (*PUBLIC_COMMANDS, DEV_COMMAND, PYTHON_COMMAND):
                (bin_dir() / command).unlink(missing_ok=True)
    except Exception as exc:
        errors.append("core")
    if not errors:
        try:
            _verify_restored_transaction_state(root, transaction)
        except Exception:
            # Never mark an unproven restore successful. Preserve the
            # transaction for a safe later reconciliation; do not leak paths.
            errors.append("verification")
    if errors:
        transaction["restoration_errors"] = errors
        _save_transaction(path, transaction, "restoration_incomplete")
        raise UpdateError("Restauration transactionnelle incomplète: " + ", ".join(errors))
    transaction.pop("restoration_errors", None)
    _save_transaction(path, transaction, "rolled_back" if state == "rolling_back" else "update_failed_restored")


def _recover_incomplete_transactions(root: Path) -> list[str]:
    recovered: list[str] = []
    base = root / "transactions"
    if not base.is_dir():
        return recovered
    for path in sorted((item for item in base.iterdir() if item.is_dir()), key=lambda item: item.name):
        transaction = _read_transaction(path)
        state = str(transaction.get("state") or "")
        if state in {"prepared", "applying", "restoring", "rolling_back", "restoration_incomplete"}:
            mode = "rolling_back" if state == "rolling_back" else "restoring"
            _complete_transaction_restore(root, path, transaction, mode)
            recovered.append(str(transaction.get("id") or path.name))
    return recovered



def recovery_inspect(root: Path | None = None) -> dict[str, Any]:
    """Read-only, redacted diagnosis of blocking updater transactions (#5069).

    This deliberately does not repair, clear, or reinterpret a rollback proof.
    Public data contain only validated transaction IDs and closed reason codes;
    never package IDs, stored absolute paths, or exception messages.
    """
    root = root if root is not None else install_root()
    base = root / "transactions"
    rows: list[dict[str, Any]] = []
    incomplete_states = _UNFINISHED_TRANSACTION_STATES
    final_states = {"applied", "rolled_back", "update_failed_restored",
                    "reconciled_degraded", "abandoned_unrecoverable_previous"}

    def diagnostic_id(name: str) -> str:
        return name if re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{8,24}", name) else "invalid_id"

    if base.is_symlink():
        rows.append({"id": "invalid_id", "state": "unreadable",
                     "reasons": ["unsafe_transaction_store"], "action": "manual_review"})
    elif base.is_dir():
        for directory in sorted(base.iterdir(), key=lambda item: item.name):
            if not directory.is_dir() and not directory.is_symlink():
                continue
            identifier = diagnostic_id(directory.name)
            if directory.is_symlink():
                rows.append({"id": identifier, "state": "unreadable",
                             "reasons": ["unsafe_transaction_directory"],
                             "action": "manual_review"})
                continue
            try:
                transaction = _read_transaction(directory)
            except UpdateError:
                rows.append({"id": identifier, "state": "unreadable",
                             "reasons": ["transaction_unreadable"],
                             "action": "manual_review"})
                continue
            state = transaction.get("state")
            if state in final_states and transaction.get("id") == directory.name:
                continue
            reasons: set[str] = set()
            if transaction.get("id") != directory.name or not isinstance(state, str):
                reasons.add("transaction_identity_invalid")
            if state not in incomplete_states:
                reasons.add("transaction_state_invalid")
            core = transaction.get("core")
            packages = transaction.get("packages")
            if type(core) is not dict or type(packages) is not dict:
                reasons.add("transaction_evidence_invalid")
            else:
                releases_root = root / "releases"
                for link, key in (("current", "before_current"),
                                  ("previous", "before_previous")):
                    expected_value = core.get(key)
                    expected = _path_from_identity(expected_value)
                    actual_link = root / link
                    actual = _symlink_target(actual_link)
                    missing = "before_current_missing" if link == "current" else "before_previous_missing"
                    if expected_value is not None and (
                            type(expected_value) is not dict
                            or type(expected_value.get("path")) is not str
                            or expected is None):
                        reasons.add("transaction_evidence_invalid")
                        continue
                    if expected is not None:
                        # No .resolve() on an arbitrary evidence path: do not
                        # follow a symlink outside the updater-owned release store.
                        if (not expected.is_absolute()
                                or expected.parent != releases_root
                                or expected.is_symlink()
                                or releases_root.is_symlink()):
                            reasons.add("unsafe_core_reference")
                            continue
                        if not expected.is_dir():
                            reasons.add(missing)
                            # Missing 'previous' is the known failure mode:
                            # no pointer is better than a fabricated pointer.
                            if actual is not None:
                                reasons.add("core_link_mismatch")
                            continue
                    if (actual_link.exists() and not actual_link.is_symlink()
                            or (expected is None) != (actual is None)
                            or (expected is not None and actual != expected)):
                        reasons.add("core_link_mismatch")
                for package_id, row in packages.items():
                    if (type(package_id) is not str or type(row) is not dict
                            or type(row.get("before")) is not dict):
                        reasons.add("transaction_evidence_invalid")
                        continue
                    try:
                        _safe_package_id(package_id)
                        before = row["before"]
                        if before.get("exists"):
                            backup = directory / "plugins" / package_id
                            if backup.is_symlink() or not backup.is_dir():
                                reasons.add("plugin_snapshot_missing")
                            elif _package_identity_at(package_id, backup) != before:
                                reasons.add("plugin_snapshot_mismatch")
                        if _package_identity(package_id) != before:
                            reasons.add("plugin_state_mismatch")
                    except (OSError, UpdateError, ValueError, TypeError):
                        reasons.add("plugin_evidence_unavailable")
            if state == "restoration_incomplete" and not reasons:
                # A stale generic 'verification' is not proof of safety.
                reasons.add("unclassified_restoration_failure")
            rows.append({
                "id": identifier,
                "state": state if state in incomplete_states else "unreadable",
                "reasons": sorted(reasons),
                "action": ("restore_missing_evidence" if reasons & {
                    "before_current_missing", "before_previous_missing",
                    "plugin_snapshot_missing"} else "manual_review"),
            })
    return {"ok": True, "read_only": True, "blocked": bool(rows),
            "incomplete_transactions": rows}


def _preflight_incomplete_recovery(root: Path) -> None:
    """Stop permanent recovery failures before any checkout or source staging.

    Recoverable interrupted operations still pass through the existing
    transactional recover path. There is *no* implicit rollback bypass.
    """
    report = recovery_inspect(root)
    irrecoverable = {
        "before_current_missing", "before_previous_missing",
        "plugin_snapshot_missing", "plugin_snapshot_mismatch",
        "unsafe_core_reference", "unsafe_transaction_store",
        "unsafe_transaction_directory", "transaction_unreadable",
        "transaction_identity_invalid", "transaction_evidence_invalid",
        "transaction_state_invalid",
    }
    for row in report["incomplete_transactions"]:
        if (set(row["reasons"]) & irrecoverable
                or row["state"] == "unreadable"):
            # Even a transaction still labelled 'prepared' may contain a
            # manually deleted before_current/before_previous or a corrupt
            # snapshot. Never enter automatic restore with broken evidence.
            code = next((reason for reason in row["reasons"] if reason in irrecoverable),
                        "transaction_unreadable")
            raise UpdateError(
                "Restauration bloquante avant téléchargement: "
                f"transaction={row['id']} reason={code}; "
                "diagnostic: cifereca-update --recovery-inspect --json"
            )


def _previous_reconciliation_plan(root: Path, transaction_id: str) -> dict[str, Any]:
    """Strict read-only witness for the *single* lost-before_previous case.

    The hash is a one-shot operator acknowledgement, not a cryptographic
    signature. It binds the exact journal bytes, Core pointer, and current
    package proofs; it is rederived inside the updater mutation lock.
    """
    if not re.fullmatch(r"\d{8}T\d{6}Z-[a-f0-9]{8,24}", transaction_id):
        raise UpdateError("reconciliation_transaction_id_invalid")
    report = recovery_inspect(root)
    rows = report["incomplete_transactions"]
    if (len(rows) != 1 or rows[0]["id"] != transaction_id
            or rows[0]["state"] != "restoration_incomplete"
            or rows[0]["reasons"] != ["before_previous_missing"]):
        raise UpdateError("reconciliation_evidence_not_unique")
    folder = _transaction_path(root, transaction_id)
    journal = folder / "transaction.json"
    if folder.is_symlink() or journal.is_symlink():
        raise UpdateError("reconciliation_unsafe_journal")
    transaction = _read_transaction(folder)
    if transaction.get("restoration_errors") != ["verification"]:
        raise UpdateError("reconciliation_incompatible_failure")
    core = transaction["core"]
    before = _path_from_identity(core["before_current"])
    lost = _path_from_identity(core["before_previous"])
    # Only the lost *older* previous Core generation may be irrecoverable.
    # The full original current identity must still be attestable.
    if (before is None or lost is None
            or before.parent != root / "releases"
            or lost.parent != root / "releases"
            or not before.is_dir() or before.is_symlink()
            or lost.exists() or lost.is_symlink()
            or _symlink_target(root / "current") != before
            or (root / "previous").exists() or (root / "previous").is_symlink()
            or _core_identity(before) != core["before_current"]):
        raise UpdateError("reconciliation_core_evidence_mismatch")
    # The inventory above attested current package identities AND saved
    # snapshots. Bind the receipt to them without disclosing package names.
    raw = journal.read_bytes()
    proof = {
        package_id: _package_identity(package_id)
        for package_id in sorted(transaction["packages"])
    }
    token = hashlib.sha256(
        b"cifereca.reconcile-lost-previous/1\0"
        + raw + b"\0" + os.readlink(root / "current").encode("utf-8")
        + b"\0" + json.dumps(proof, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "ok": True, "read_only": True, "id": transaction_id,
        "reason": "before_previous_missing",
        "proposed_state": "reconciled_degraded",
        "acknowledgement_sha256": token,
        "requires_acknowledgement": True,
    }


def reconcile_lost_previous(
    transaction_id: str, *, acknowledge: str | None = None,
) -> dict[str, Any]:
    """Supervised resolution only: never fabricate a rollback generation.

    Planning is read-only. Apply takes an exclusive lock, rechecks all proof,
    fsyncs a private archival journal and only then changes transaction
    state to *degraded* (not restored). No plugin or user-state mutation.
    """
    root = install_root()
    if acknowledge is None:
        return _previous_reconciliation_plan(root, transaction_id)
    if type(acknowledge) is not str or not re.fullmatch(r"[a-f0-9]{64}", acknowledge):
        raise UpdateError("reconciliation_acknowledgement_invalid")
    with _exclusive_updater_lock(root):
        plan = _previous_reconciliation_plan(root, transaction_id)
        if acknowledge != plan["acknowledgement_sha256"]:
            raise UpdateError("reconciliation_evidence_changed")
        folder = _transaction_path(root, transaction_id)
        original = _read_transaction(folder)
        private_audit = root / "reconciliation-audit"
        if private_audit.is_symlink():
            raise UpdateError("reconciliation_audit_store_unsafe")
        private_audit.mkdir(mode=0o700, exist_ok=True)
        if (not private_audit.is_dir() or private_audit.is_symlink()
                or private_audit.stat().st_uid != os.getuid()
                or private_audit.stat().st_mode & 0o077):
            raise UpdateError("reconciliation_audit_store_unsafe")
        audit = private_audit / (transaction_id + "-" + acknowledge[:16] + ".json")
        receipt = {
            "schema": "cifereca.updater.reconciliation-audit/1",
            "transaction_id": transaction_id,
            "reason": "before_previous_missing",
            "result": "reconciled_degraded",
            "acknowledgement_sha256": acknowledge,
            "original_transaction": original,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        data = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n").encode("utf-8")
        if audit.exists() or audit.is_symlink():
            # Recovery from a crash *after* fsync(audit) but *before* saving
            # the degraded journal is allowed only for the identical proof.
            # A partial, foreign or replaced audit must not be rewritten.
            if audit.is_symlink():
                raise UpdateError("reconciliation_existing_audit_unsafe")
            fd = -1
            try:
                fd = os.open(audit, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                existing_stat = os.fstat(fd)
                if (not stat.S_ISREG(existing_stat.st_mode)
                        or existing_stat.st_nlink != 1
                        or existing_stat.st_uid != os.getuid()
                        or existing_stat.st_mode & 0o077
                        or existing_stat.st_size > 128 * 1024 * 1024):
                    raise UpdateError("reconciliation_existing_audit_unsafe")
                with os.fdopen(fd, "rb") as stream:
                    fd = -1
                    saved = json.loads(stream.read().decode("utf-8"))
            except (OSError, ValueError, UnicodeError):
                raise UpdateError("reconciliation_existing_audit_unreadable") from None
            finally:
                if fd >= 0:
                    os.close(fd)
            if (type(saved) is not dict
                    or saved.get("schema") != receipt["schema"]
                    or saved.get("transaction_id") != transaction_id
                    or saved.get("reason") != receipt["reason"]
                    or saved.get("result") != receipt["result"]
                    or saved.get("acknowledgement_sha256") != acknowledge
                    or saved.get("original_transaction") != original):
                raise UpdateError("reconciliation_existing_audit_mismatch")
        else:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(audit, flags, 0o600)
            except OSError:
                raise UpdateError("reconciliation_audit_write_refused") from None
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                raise UpdateError("reconciliation_audit_write_failed") from None
        directory_fd = os.open(private_audit, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        # Any drift between proof and journal commit must still fail closed;
        # leaving the audit receipt without a state switch is recoverable.
        refreshed = _previous_reconciliation_plan(root, transaction_id)
        if refreshed["acknowledgement_sha256"] != acknowledge:
            raise UpdateError("reconciliation_evidence_changed")
        original["reconciliation"] = {
            "schema": 1, "reason": "before_previous_missing",
            "audit": audit.name, "acknowledgement_sha256": acknowledge,
        }
        _save_transaction(folder, original, "reconciled_degraded")
        return {
            "ok": True, "id": transaction_id, "state": "reconciled_degraded",
            "reason": "before_previous_missing", "audit": audit.name,
            "no_previous_release_restored": True,
        }


def _build_release_plugin_snapshot(release: Path, destination: Path) -> Path | None:
    expected = _release_plugin_manifest(release)
    if not expected:
        return None
    plugins = release / "src" / "plugins"
    script = release / "scripts" / "package-dev-snapshot.py"
    if not plugins.is_dir() or not script.is_file():
        raise UpdateError("Sources plugins absentes de la release alors que des versions sont attendues")
    py = _command_path(release, "cifereca-cli").parent / ("python.exe" if os.name == "nt" else "python")
    destination.mkdir(parents=True, exist_ok=True)
    snapshot = destination / "release-plugins.zip"
    command = [
        str(py), str(script),
        "--plugins", str(plugins),
        "--out", str(destination),
        "--id", "cifereca-release-plugins",
        "--name", snapshot.name,
    ]
    proc = _run_bounded_process(command, text=True, capture_output=True, timeout=180, check=False)
    if proc.returncode != 0 or not snapshot.is_file():
        raise UpdateError("Construction du snapshot plugins impossible")
    return snapshot


def _snapshot_package_ids(snapshot: Path) -> list[str]:
    """Read exactly the packages the generated child snapshot will install.

    The frozen publication inventory is NOT the generated source snapshot:
    newer packages can be present in src/plugins before a formal release.
    Rollback must cover every package mutated by the real child, including
    packages absent from plugin-release.json, and no unrelated installed ones.
    Never echo archive paths, package names or parser errors on failure.
    """
    try:
        with zipfile.ZipFile(snapshot) as archive:
            names = archive.namelist()
            manifests = [
                name for name in names
                if len(PurePosixPath(name).parts) == 2
                and PurePosixPath(name).name == "cifereca-dev.json"
            ]
            if len(manifests) != 1:
                raise ValueError("snapshot manifest count")
            base = PurePosixPath(manifests[0]).parts[0]
            manifest_info = archive.getinfo(manifests[0])
            if manifest_info.file_size > 1024 * 1024:
                raise ValueError("oversized manifest")
            manifest = json.loads(archive.read(manifests[0]))
            rows = manifest.get("plugins") if type(manifest) is dict else None
            if type(rows) is not list or not 1 <= len(rows) <= 128:
                raise ValueError("invalid snapshot plugin list")
            ids: set[str] = set()
            for row in rows:
                rel = row.get("path") if type(row) is dict else None
                if type(rel) is not str or "\\" in rel or "\x00" in rel:
                    raise ValueError("invalid package member")
                p = PurePosixPath(rel)
                if (p.is_absolute() or ".." in p.parts or len(p.parts) != 2
                        or p.parts[0] != "packages" or p.suffix != ".zip"):
                    raise ValueError("invalid package member")
                info = archive.getinfo(base + "/" + p.as_posix())
                if not 0 < info.file_size <= 64 * 1024 * 1024:
                    raise ValueError("oversized package archive")
                with zipfile.ZipFile(io.BytesIO(archive.read(info))) as plugin:
                    member = plugin.getinfo("cifereca-plugin.json")
                    if not 0 < member.file_size <= 1024 * 1024:
                        raise ValueError("oversized package manifest")
                    package_manifest = json.loads(plugin.read(member))
                package = package_manifest.get("package") if type(package_manifest) is dict else None
                package_id = package.get("id") if type(package) is dict else None
                if (type(package_id) is not str or not 1 <= len(package_id) <= 128
                        or _safe_package_id(package_id) != package_id
                        or package_id in ids):
                    raise ValueError("invalid package identity")
                ids.add(package_id)
            return sorted(ids)
    except Exception:
        raise UpdateError("Inventaire des packages du snapshot de release invalide") from None


def _attested_snapshot_smoke_failures(
    snapshot: Path, value: Any,
) -> list[dict[str, str]]:
    """Admit child smoke IDs only when present in the exact release snapshot.

    The parent never trusts child CLI IDs, labels, errors, or arbitrary text.
    The only allowed report is a short manifest-bound identity and fixed
    runtime failure reason. Failure to attest just omits these optional facts.
    """
    from .dev_updater import SMOKE_FAILURE_REASONS, MAX_SMOKE_FAILURE_DETAILS
    if (type(value) is not list or not 1 <= len(value) <= MAX_SMOKE_FAILURE_DETAILS
            or any(type(item) is not dict
                   or set(item) != {"package_id", "plugin_id", "reason_code"}
                   or any(type(part) is not str for part in item.values())
                   or item["reason_code"] not in SMOKE_FAILURE_REASONS
                   for item in value)):
        return []
    try:
        # Prior transaction preparation already checked the complete package
        # list. Independently attest *contribution* IDs from the same ZIP.
        _snapshot_package_ids(snapshot)
        with zipfile.ZipFile(snapshot) as archive:
            manifests = [name for name in archive.namelist()
                         if len(PurePosixPath(name).parts) == 2
                         and PurePosixPath(name).name == "cifereca-dev.json"]
            if len(manifests) != 1:
                return []
            base = PurePosixPath(manifests[0]).parts[0]
            manifest = json.loads(archive.read(manifests[0]))
            allowed: set[tuple[str, str]] = set()
            for record in manifest["plugins"]:
                member = record["path"] if type(record) is dict else None
                if type(member) is not str:
                    return []
                with zipfile.ZipFile(io.BytesIO(archive.read(base + "/" + member))) as plugin:
                    descriptor = json.loads(plugin.read("cifereca-plugin.json"))
                pkg = descriptor["package"]
                entries = descriptor["plugins"]
                if type(pkg) is not dict or type(entries) is not list:
                    return []
                package_id = pkg.get("id")
                if type(package_id) is not str:
                    return []
                for entry in entries:
                    plugin_id = entry.get("id") if type(entry) is dict else None
                    if type(plugin_id) is str:
                        allowed.add((package_id, plugin_id))
            if any((row["package_id"], row["plugin_id"]) not in allowed
                   for row in value):
                return []
            # Duplicated child rows are untrustworthy, not independent errors.
            if len({(r["package_id"], r["plugin_id"]) for r in value}) != len(value):
                return []
            return [dict(row) for row in value]
    except Exception:
        return []


def _apply_release_plugin_snapshot(release: Path, snapshot: Path) -> dict[str, Any]:
    command = _command_path(release, DEV_COMMAND)
    if not command.is_file():
        raise UpdateError("cifereca-update-dev absent du nouveau runtime")
    # The direct child inherits a validated lease, never an unlocked gap.
    lease_fd = getattr(_UPDATER_LOCK_DEPTH, "fd", None)
    child_lease = ({
        "pass_fds": (lease_fd,),
        "extra_env": {_UPDATER_INHERITED_LOCK_FD_ENV: str(lease_fd)},
    } if lease_fd is not None else {})
    try:
        proc = run_plugin_child(
            [str(command), str(snapshot), "--json"],
            timeout=RELEASE_PLUGIN_SNAPSHOT_TIMEOUT_SECONDS, on_line=child_progress_receiver(),
            **child_lease,
        )
    except TimeoutError:
        raise TimeoutError("Délai dépassé pour un sous-processus de mise à jour") from None
    except (OSError, ChildOutputError, UnicodeError):
        raise UpdateError("Sous-processus plugins de mise à jour indisponible") from None
    if proc.returncode != 0:
        # Child stdout is already size-limited. Admit only the fixed error
        # schema, closed cause code and three bounded counters; never publish
        # stderr, arbitrary exception text, package IDs or plugin labels.
        if type(proc.stdout) is str and len(proc.stdout) <= 16384:
            try:
                failed = json.loads(proc.stdout)
            except (ValueError, RecursionError):
                failed = None
            if type(failed) is dict and failed.get("ok") is False:
                detail = failed.get("error")
                if (type(detail) is dict and detail.get("code") == "dev_update_error"
                        and type(detail.get("reason_code")) is str
                        and detail["reason_code"] in SMOKE_FAILURE_CODES):
                    counts = detail.get("smoke_counts")
                    if (type(counts) is dict and set(counts) == SMOKE_COUNT_FIELDS
                            and all(type(v) is int and 0 <= v <= 2048
                                    for v in counts.values())):
                        failures = (_attested_snapshot_smoke_failures(
                            snapshot, detail.get("failed_contributions"),
                        ) if detail["reason_code"] == "smoke_contributions_failed" else [])
                        if len(failures) > counts["contributions_error"] + counts["contributions_incompatible"]:
                            failures = []
                        raise ReleasePluginSmokeError(
                            reason_code=detail["reason_code"], smoke_counts=counts,
                            failed_contributions=failures,
                        ) from None
        raise UpdateError("Upgrade des plugins de release échoué")
    try:
        value = json.loads(proc.stdout)
    except (ValueError, RecursionError):
        raise UpdateError("Upgrade des plugins de release: sortie JSON invalide") from None
    if not isinstance(value, dict) or not value.get("ok"):
        raise UpdateError("Upgrade des plugins de release non confirmé")
    return value


def _restore_release_links(root: Path, old_current: Path | None, old_previous: Path | None) -> None:
    current_link = root / "current"
    previous_link = root / "previous"
    if old_current is not None and old_current.is_dir():
        _replace_symlink(current_link, old_current)
    else:
        current_link.unlink(missing_ok=True)
    if old_previous is not None and old_previous.is_dir():
        _replace_symlink(previous_link, old_previous)
    else:
        previous_link.unlink(missing_ok=True)


@_serial_updater_operation
def install_source(
    source: Path,
    *,
    python: str | None = None,
    keep: int = MIN_RETAINED_RELEASES,
    dry_run: bool = False,
    provenance: dict[str, str] | None = None,
    backup_persistent: bool = False,
) -> dict[str, Any]:
    if not dry_run:
        _preflight_incomplete_recovery(install_root())
    source = source.resolve()
    _, version = step("metadata", _metadata, source)
    py = step("python_selection", select_python, python)
    root = install_root()
    result: dict[str, Any] = {
        "ok": True,
        "version": version,
        "install_root": str(root),
        "python": py,
        "dry_run": bool(dry_run),
    }
    if provenance:
        result["provenance"] = dict(provenance)
    if dry_run:
        # A dry-run only checks metadata and Python selection. Explicitly
        # distinguish unexecuted stages from actual validated success,
        # without allocating a runtime tree or contacting plugin services.
        for skipped_stage in (
            "recover", "source_copy", "venv", "smoke", "backup",
            "plugin_snapshot", "transaction_prepare", "core_activation",
            "plugin_activation", "transaction_finalize", "cleanup",
        ):
            skip_update_phase(skipped_stage)
        return result
    recovered = step("recover", _recover_incomplete_transactions, root)
    if recovered:
        result["recovered_transactions"] = recovered

    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    release = releases / f"{version}-{stamp}-{os.getpid()}"
    old_current = _symlink_target(root / "current")
    old_previous = _symlink_target(root / "previous")
    plugins_result: dict[str, Any] | None = None
    transaction_path: Path | None = None
    transaction: dict[str, Any] | None = None
    switched = False
    try:
        step("source_copy", _copy_source, source, release)
        step("venv", _create_venv, release, py)
        smoke = step("smoke", _smoke, release) or {}
        backup = step(
            "backup", _backup_persistent_state,
            (provenance or {}).get("commit", version),
        ) if backup_persistent else None
        with tempfile.TemporaryDirectory(prefix="cifereca-release-plugins-") as plugin_tmp:
            plugin_snapshot = step("plugin_snapshot", _build_release_plugin_snapshot, release, Path(plugin_tmp))
            from datetime import datetime, timezone
            install_metadata: dict[str, Any] = {
                "version": version,
                "installed_at": datetime.now(timezone.utc).isoformat(),
            }
            if provenance:
                install_metadata["provenance"] = dict(provenance)
            (release / ".cifereca-install.json").write_text(
                json.dumps(install_metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            managed_packages = (_snapshot_package_ids(plugin_snapshot)
                                if plugin_snapshot is not None else [])
            transaction_path, transaction = step(
                "transaction_prepare", _create_update_transaction,
                root, release, old_current, old_previous, managed_packages,
            )
            _save_transaction(transaction_path, transaction, "applying")
            with update_phase("core_activation"):
                if old_current and old_current.is_dir():
                    _replace_symlink(root / "previous", old_current)
                _replace_symlink(root / "current", release)
                switched = True
                launchers = _write_launchers(root)
            if plugin_snapshot is not None:
                plugins_result = step("plugin_activation", _apply_release_plugin_snapshot, release, plugin_snapshot)
            for package_id, row in transaction["packages"].items():
                row["after"] = _package_identity(package_id)
            step("transaction_finalize", _save_transaction, transaction_path, transaction, "applied")
            cleanup_result = step("cleanup", _gc, root, keep)
    except (Exception, KeyboardInterrupt) as update_exc:
        # Ctrl-C after Core activation must attempt the same safe transactional
        # restore as ordinary failures; never leave a success-looking state.
        if transaction_path is not None and transaction is not None:
            try:
                step("rollback", _complete_transaction_restore, root, transaction_path, transaction, "restoring")
            except Exception as rollback_exc:
                raise UpdateError(
                    "Mise à jour échouée et restauration transactionnelle incomplète"
                ) from rollback_exc
        elif switched:
            try:
                step("rollback", _restore_release_links, root, old_current, old_previous)
            except Exception as rollback_exc:
                raise UpdateError(
                    "Mise à jour échouée et rollback Core impossible"
                ) from rollback_exc
        current = _symlink_target(root / "current")
        if current is None or current.resolve() != release.resolve():
            shutil.rmtree(release, ignore_errors=True)
        raise update_exc
    result.update({
        "release": str(release),
        "current": str(_symlink_target(root / "current") or ""),
        "previous": str(_symlink_target(root / "previous") or "") or None,
        "launchers": launchers,
        "core_ready": smoke.get("core_ready", True),
        "desktop_ready": smoke.get("desktop_ready", False),
        "desktop_error": smoke.get("desktop_error", ""),
        "backup": str(backup) if backup else None,
        "plugins": plugins_result,
        "cleanup": cleanup_result,
        "transaction": str(transaction_path) if transaction_path else None,
    })
    return result


def _github_commitish(ref: str) -> str:
    """Normalize an explicit GitHub branch/tag ref for the commits endpoint."""
    for prefix in ("refs/tags/", "refs/heads/"):
        if ref.startswith(prefix):
            value = ref[len(prefix):]
            if value:
                return value
    return ref


def _github_checkout(repository: str, ref: str, destination: Path) -> str:
    gh = shutil.which("gh")
    git = shutil.which("git")
    if not gh:
        raise UpdateError("GitHub CLI 'gh' absent; installer gh puis exécuter 'gh auth login'")
    if not git:
        raise UpdateError("git absent du PATH")
    commitish = _github_commitish(ref)
    endpoint = f"repos/{repository}/commits/{quote(commitish, safe='')}"
    with update_phase("resolve_github"):
        resolved = _run_bounded_process(
            [gh, "api", endpoint, "--jq", ".sha"],
            text=True, capture_output=True, timeout=30, check=False,
        )
        if resolved.returncode != 0 or not resolved.stdout.strip():
            raise UpdateError("Branche, tag ou commit GitHub introuvable ou accès refusé")
        # gh's response is untrusted until it matches the *entire* expected
        # commit object ID. Do not allow an extra line, git option, refspec,
        # truncated hash or arbitrary process output to reach 'git fetch'.
        # Reject it before clone, staging or mutation, even during dry-run.
        sha = resolved.stdout.strip()
        if re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None:
            raise UpdateError("Référence GitHub résolue sans SHA de commit valide")
        sha = sha.lower()
    with update_phase("clone"):
        clone = _run_bounded_process(
            [gh, "repo", "clone", repository, str(destination), "--", "--filter=blob:none", "--no-checkout"],
            text=True, capture_output=True, timeout=180, check=False,
        )
        if clone.returncode != 0:
            raise UpdateError("Clone GitHub impossible")
    with update_phase("fetch"):
        fetched = _run_bounded_process(
            [git, "-C", str(destination), "fetch", "--depth", "1", "origin", sha],
            text=True, capture_output=True, timeout=180, check=False,
        )
        if fetched.returncode != 0:
            raise UpdateError("Fetch GitHub impossible")
    with update_phase("checkout"):
        checkout = _run_bounded_process(
            [git, "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"],
            text=True, capture_output=True, timeout=60, check=False,
        )
        if checkout.returncode != 0:
            raise UpdateError("Checkout GitHub impossible")
    return sha



def _prepare_github_wheelhouse(source: Path, python: str) -> None:
    """Prepare eligible runtime wheels before a managed GitHub installation.

    A git checkout normally has no bundled wheelhouse.  Admission is run in
    the verified checkout's temporary directory, without writing to the
    active runtime.  If PyPI/packaging is unavailable, keep the old release.
    """
    wheelhouse = source / "offline-wheels"
    if wheelhouse.exists():
        # Do not accept a mutable pre-existing repository wheelhouse in place
        # of an independently produced, attested one.
        raise UpdateError("dependency_provenance_unavailable")
    script = source / "scripts" / "prepare-admitted-wheelhouse.py"
    if not script.is_file() or script.is_symlink():
        raise UpdateError("dependency_provenance_unavailable")
    result = _run_bounded_process(
        [python, str(script)], timeout=900, cwd=source,
        text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        # Child output is untrusted: it can contain private URLs or tokens.
        raise UpdateError("dependency_not_eligible")
    # A producer-side JSON is not a signature; this verifier is a local
    # integrity and configured age proof check. Trust in the checkout comes from the
    # already-selected GitHub commit, not from the JSON alone.
    from .wheelhouse_quarantine import verify_wheelhouse
    from .plugins.python_quarantine import QuarantineRejected, quarantine_diagnostic
    try:
        version = _metadata(source)[1]
        verify_wheelhouse(wheelhouse, core_version=version)
    except (QuarantineRejected, OSError, ValueError) as exc:
        reason = quarantine_diagnostic(exc) if isinstance(exc, QuarantineRejected) else "dependency_provenance_unavailable"
        raise UpdateError(reason) from None



@_serial_updater_operation
def install_github(
    ref: str = "main",
    *,
    repository: str = DEFAULT_GITHUB_REPOSITORY,
    python: str | None = None,
    keep: int = MIN_RETAINED_RELEASES,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not dry_run:
        _preflight_incomplete_recovery(install_root())
    with tempfile.TemporaryDirectory(prefix="cifereca-github-") as tmp:
        source = Path(tmp) / "source"
        sha = _github_checkout(repository, ref, source)
        provenance = {"type": "github", "repository": repository, "ref": ref, "commit": sha}
        if not dry_run:
            # Keep --github installable despite the fail-closed source fallback.
            # Admission precedes managed venv creation or release switching.
            step("manifest_validation", _prepare_github_wheelhouse, source, select_python(python))
        result = install_source(
            source,
            python=python,
            keep=keep,
            dry_run=dry_run,
            provenance=provenance,
            backup_persistent=True,
        )
        result.update({"github_repository": repository, "github_ref": ref, "github_commit": sha})
        return result


@_serial_updater_operation
def install_archive(
    archive: Path,
    *,
    python: str | None = None,
    keep: int = MIN_RETAINED_RELEASES,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not dry_run:
        _preflight_incomplete_recovery(install_root())
    try:
        archive = step("resolve_archive", resolve_archive, archive)
    except ArchiveNotFoundError as exc:
        raise UpdateError(str(exc)) from exc
    with tempfile.TemporaryDirectory(prefix="cifereca-update-") as tmp:
        source = step("snapshot_extract", _extract_release, archive, Path(tmp))
        return install_source(source, python=python, keep=keep, dry_run=dry_run)


def status() -> dict[str, Any]:
    root = install_root()
    current = _symlink_target(root / "current")
    current_transaction: dict[str, Any] | None = None
    if current and current.is_dir():
        linked = _transaction_for_release(root, current)
        if linked:
            path, transaction = linked
            current_transaction = {
                "id": transaction.get("id"),
                "state": transaction.get("state"),
                "path": str(path),
                "packages": {
                    package_id: _package_identity(package_id)
                    for package_id in sorted(transaction.get("packages", {}))
                },
            }
    incomplete: list[dict[str, str]] = []
    transaction_root = root / "transactions"
    if transaction_root.is_dir():
        for path in sorted(item for item in transaction_root.iterdir() if item.is_dir()):
            transaction = _read_transaction(path)
            state = str(transaction.get("state") or "")
            if state in {"prepared", "applying", "restoring", "rolling_back", "restoration_incomplete"}:
                incomplete.append({"id": str(transaction.get("id") or path.name), "state": state})
    retained = _releases(root)
    retention = {
        "max_age_hours": RELEASE_RETENTION_SECONDS // 3600,
        "minimum_generations": MIN_RETAINED_RELEASES,
        "retained_generations": len(retained),
        "last_cleanup_at": None,
        "last_removed": None,
    }
    retention_state = root / RETENTION_STATE_FILE
    if retention_state.is_file() and not retention_state.is_symlink():
        try:
            saved = json.loads(retention_state.read_text(encoding="utf-8"))
            if type(saved) is dict:
                if type(saved.get("last_cleanup_at")) is str:
                    retention["last_cleanup_at"] = saved["last_cleanup_at"]
                if type(saved.get("removed")) is int:
                    retention["last_removed"] = saved["removed"]
                if type(saved.get("minimum_generations")) is int:
                    retention["effective_minimum_generations"] = max(
                        MIN_RETAINED_RELEASES, saved["minimum_generations"],
                    )
        except (OSError, ValueError, UnicodeError):
            pass  # --status must never trigger a repair or full SHA traversal.
    return {
        "ok": True,
        "install_root": str(root),
        "current": str(current or "") or None,
        "previous": str(_symlink_target(root / "previous") or "") or None,
        "releases": [str(x) for x in retained],
        "release_retention": retention,
        "launchers": {x: str(bin_dir() / x) for x in (*PUBLIC_COMMANDS, PYTHON_COMMAND)},
        "transaction": current_transaction,
        "incomplete_transactions": incomplete,
    }


@_serial_updater_operation
def rollback(to: str | None = None) -> dict[str, Any]:
    root = install_root()
    _preflight_incomplete_recovery(root)
    recovered = step("recover", _recover_incomplete_transactions, root)
    if recovered:
        return {
            "ok": True,
            "current": str(_symlink_target(root / "current") or "") or None,
            "previous": str(_symlink_target(root / "previous") or "") or None,
            "launchers": {x: str(bin_dir() / x) for x in (*PUBLIC_COMMANDS, PYTHON_COMMAND)},
            "recovered_transactions": recovered,
        }
    current = _symlink_target(root / "current")
    candidates = _releases(root)
    target: Path | None = None
    if to:
        for candidate in candidates:
            if candidate.name == to or candidate.name.startswith(f"{to}-"):
                target = candidate
                break
    else:
        previous = _symlink_target(root / "previous")
        if previous and previous.is_dir():
            target = previous
        else:
            target = next((x for x in candidates if current is None or x.resolve() != current.resolve()), None)
    if not target or not target.is_dir():
        raise UpdateError("Aucune release de rollback disponible")

    managed_current = _release_plugin_manifest(current) if current and current.is_dir() else {}
    managed_target = _release_plugin_manifest(target)
    linked = _transaction_for_release(root, current) if current and current.is_dir() else None
    if linked:
        transaction_path, transaction = linked
        before = _path_from_identity(transaction.get("core", {}).get("before_current"))
        if before is None or before.resolve() != target.resolve():
            raise UpdateError(
                "Rollback transactionnel refusé: la cible ne correspond pas au Core précédant cette mise à jour"
            )
        if transaction.get("state") != "applied":
            raise UpdateError(f"Rollback transactionnel impossible depuis l'état {transaction.get('state')}")
        changed: list[str] = []
        for package_id, row in sorted(transaction.get("packages", {}).items()):
            after = row.get("after") if isinstance(row, dict) else None
            if not isinstance(after, dict) or _package_identity(package_id) != after:
                changed.append(package_id)
        if changed:
            raise UpdateError(
                "Rollback refusé: des plugins gérés ont changé après la mise à jour "
                f"({', '.join(changed)}). Aucun état n'a été écrasé."
            )
        step("smoke", _smoke, target)
        step("rollback", _complete_transaction_restore, root, transaction_path, transaction, "rolling_back")
        result = {
            "ok": True,
            "current": str(target),
            "previous": str(_symlink_target(root / "previous") or "") or None,
            "launchers": {x: str(bin_dir() / x) for x in (*PUBLIC_COMMANDS, PYTHON_COMMAND)},
            "transaction": str(transaction_path),
            "plugins_restored": sorted(transaction.get("packages", {})),
            "plugin_state": {
                package_id: _package_identity(package_id)
                for package_id in sorted(transaction.get("packages", {}))
            },
        }
        if recovered:
            result["recovered_transactions"] = recovered
        return result
    if managed_current or managed_target:
        raise UpdateError(
            "Rollback Core + plugins indisponible : aucun snapshot restaurable "
            "des packages, empreintes et activations n'est lié à la transaction. "
            "Aucun lien Core n'a été modifié. Voir le ticket #904."
        )
    step("smoke", _smoke, target)
    with update_phase("rollback"):
        if current and current.is_dir():
            _replace_symlink(root / "previous", current)
        _replace_symlink(root / "current", target)
        launchers = _write_launchers(root)
    result = {"ok": True, "current": str(target), "launchers": launchers}
    if recovered:
        result["recovered_transactions"] = recovered
    return result


def _print(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        for key, val in value.items():
            print(f"{key}: {val}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Updater atomique cifereca")
    p.add_argument("archive", nargs="?")
    p.add_argument(
        "--github",
        nargs="?",
        const="main",
        metavar="BRANCHE|TAG|COMMIT",
        help="Installe directement depuis pzia/cifereca à partir d'une branche, d'un tag ou d'un commit (défaut: main)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--recovery-inspect", action="store_true",
                   help="Diagnostic en lecture seule des transactions de reprise bloquées")
    p.add_argument("--retention-inspect", action="store_true",
                   help="Inventaire read-only de rétention Core/transactions/versions plugins ; jamais d'effacement")
    p.add_argument("--recovery-reconcile", metavar="TRANSACTION_ID",
                   help="Plan de réconciliation ciblée (lecture seule sauf --acknowledge)")
    p.add_argument("--acknowledge", metavar="SHA256",
                   help="Validation explicite du plan exact --recovery-reconcile")
    p.add_argument("--rollback", action="store_true")
    p.add_argument("--to")
    p.add_argument("--keep", type=int, default=MIN_RETAINED_RELEASES,
                   help="Plancher de générations conservées (min 2) en plus des 24 h")
    p.add_argument("--python")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.retention_inspect:
            if (a.archive or a.github is not None or a.rollback or a.status
                    or a.recovery_inspect or a.recovery_reconcile is not None
                    or a.acknowledge is not None or a.dry_run or a.to or a.python):
                p.error("--retention-inspect est exclusif et strictement read-only")
            from .updater_retention_plugins import inspect_updater_retention_readonly
            result = inspect_updater_retention_readonly(
                install_root(), _plugin_packages_root(), now=time.time(),
                backups_root=state_dir(create=False) / "dev-updates",
            )
        elif a.recovery_inspect:
            result = recovery_inspect()
        elif a.recovery_reconcile is not None:
            if a.archive or a.github is not None or a.rollback or a.status or a.dry_run:
                p.error("--recovery-reconcile incompatible avec installation, status ou rollback")
            result = reconcile_lost_previous(a.recovery_reconcile, acknowledge=a.acknowledge)
        elif a.status:
            result = status()
        elif a.rollback:
            with update_operation("rollback", stream=sys.stderr):
                result = rollback(a.to)
        elif a.github is not None:
            if a.archive:
                p.error("archive ZIP incompatible avec --github")
            with update_operation("github", stream=sys.stderr):
                result = install_github(a.github, python=a.python, keep=a.keep, dry_run=a.dry_run)
        else:
            if not a.archive:
                p.error("archive ZIP requise (ou utiliser --github [branche|tag|commit])")
            with update_operation("archive", stream=sys.stderr):
                result = install_archive(a.archive, python=a.python, keep=a.keep, dry_run=a.dry_run)
        _print(result, a.json)
        return 0
    except KeyboardInterrupt:
        _print({"ok": False, "error": {"code": "interrupted",
               "message": "Mise à jour interrompue"}}, a.json)
        return 130
    except TimeoutError:
        _print({"ok": False, "error": {"code": "update_error",
               "message": "Délai dépassé pour un sous-processus de mise à jour"}}, a.json)
        return 1
    except UpdateError as exc:
        detail = {"code": "update_error", "message": str(exc)}
        if type(exc) is ReleasePluginSmokeError:
            detail["reason_code"] = exc.reason_code
            detail["smoke_counts"] = dict(exc.smoke_counts)
            if exc.failed_contributions:
                detail["failed_contributions"] = [
                    dict(row) for row in exc.failed_contributions
                ]
        _print({"ok": False, "error": detail}, a.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

