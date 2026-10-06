from __future__ import annotations

import argparse
import json
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

from .install_paths import ArchiveNotFoundError, resolve_archive

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


class UpdateError(RuntimeError):
    pass


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
        raise UpdateError(f"Chemin interdit dans l'archive: {info.filename}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise UpdateError(f"Symlink ZIP interdit: {info.filename}")
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
        proc = subprocess.run(
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
    venv = release / ".venv"
    # PyGObject/GTK stays a system dependency, therefore the release venv may
    # see system site-packages. All cifereca Python runtime dependencies are,
    # however, installed from the bundled offline wheelhouse.
    proc = subprocess.run([python, "-m", "venv", "--system-site-packages", str(venv)], check=False)
    if proc.returncode != 0:
        raise UpdateError("Création du venv de release impossible")
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not py.is_file():
        raise UpdateError("Python du venv absent")
    wheelhouse = release / "offline-wheels"
    version = _metadata(release)[1]
    core_wheels = sorted(wheelhouse.glob(f"cifereca-{version}-*.whl")) if wheelhouse.is_dir() else []
    if core_wheels:
        command = [
            str(py), "-m", "pip", "install", "--disable-pip-version-check",
            "--no-index", "--find-links", str(wheelhouse), "--ignore-installed", f"cifereca=={version}",
        ]
    else:
        # Developer/source checkout fallback. Release archives must carry the
        # wheelhouse and are tested with the branch above.
        command = [str(py), "-m", "pip", "install", "--disable-pip-version-check", str(release)]
    proc = subprocess.run(command, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise UpdateError(f"Installation du package dans le venv impossible: {detail[-1200:]}")
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
    proc = subprocess.run([str(cli), "--version"], text=True, capture_output=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise UpdateError("Smoke test CLI --version échoué")
    proc = subprocess.run([str(app), "--version"], text=True, capture_output=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise UpdateError("Smoke test application --version échoué")
    py = _command_path(release, "cifereca-cli").parent / "python"
    core_code = "import cifereca,httpx,packaging; print(cifereca.__version__)"
    proc = subprocess.run([str(py), "-c", core_code], text=True, capture_output=True, timeout=30, check=False)
    if proc.returncode != 0:
        raise UpdateError("Smoke test core/httpx échoué")
    gtk_code = (
        "import gi;"
        "gi.require_version('Gtk','4.0');gi.require_version('Adw','1');"
        "from gi.repository import Gtk,Adw;"
        "assert Gtk.get_major_version()>=4"
    )
    gtk = subprocess.run([str(py), "-c", gtk_code], text=True, capture_output=True, timeout=30, check=False)
    return {
        "core_ready": True,
        "desktop_ready": gtk.returncode == 0,
        "desktop_error": "" if gtk.returncode == 0 else (gtk.stderr.strip() or gtk.stdout.strip() or "GTK/PyGObject indisponible")[-800:],
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
    target = root / "current" / ".venv" / "bin" / command
    return f'#!/bin/sh\nexec "{target}" "$@"\n'


def _write_launchers(root: Path) -> dict[str, str]:
    if os.name == "nt":
        raise UpdateError("Le bootstrap Windows n'est pas encore implémenté dans cette release")
    out: dict[str, str] = {}
    directory = bin_dir()
    directory.mkdir(parents=True, exist_ok=True)
    commands = list(PUBLIC_COMMANDS)
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


def _releases(root: Path) -> list[Path]:
    base = root / "releases"
    if not base.is_dir():
        return []
    return sorted((x for x in base.iterdir() if x.is_dir()), key=lambda x: x.stat().st_mtime, reverse=True)


def _gc(root: Path, keep: int) -> None:
    keep = max(2, int(keep))
    active = _symlink_target(root / "current")
    previous = _symlink_target(root / "previous")
    protected = {x.resolve() for x in (active, previous) if x is not None}
    releases = _releases(root)
    survivors = set(protected)
    for release in releases:
        if release.resolve() in survivors:
            continue
        if len(survivors) < keep:
            survivors.add(release.resolve())
            continue
        shutil.rmtree(release, ignore_errors=True)


def install_source(
    source: Path,
    *,
    python: str | None = None,
    keep: int = 3,
    dry_run: bool = False,
) -> dict[str, Any]:
    source = source.resolve()
    _, version = _metadata(source)
    py = select_python(python)
    root = install_root()
    result: dict[str, Any] = {
        "ok": True,
        "version": version,
        "install_root": str(root),
        "python": py,
        "dry_run": bool(dry_run),
    }
    if dry_run:
        return result

    releases = root / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    release = releases / f"{version}-{stamp}-{os.getpid()}"
    try:
        _copy_source(source, release)
        _create_venv(release, py)
        smoke = _smoke(release) or {}
        from datetime import datetime, timezone
        (release / ".cifereca-install.json").write_text(json.dumps({
            "version": version, "installed_at": datetime.now(timezone.utc).isoformat()
        }) + "\n", encoding="utf-8")
        old = _symlink_target(root / "current")
        if old and old.is_dir():
            _replace_symlink(root / "previous", old)
        _replace_symlink(root / "current", release)
        launchers = _write_launchers(root)
        _gc(root, keep)
    except Exception:
        current = _symlink_target(root / "current")
        if current is None or current.resolve() != release.resolve():
            shutil.rmtree(release, ignore_errors=True)
        raise
    result.update({
        "release": str(release),
        "current": str(_symlink_target(root / "current") or ""),
        "previous": str(_symlink_target(root / "previous") or "") or None,
        "launchers": launchers,
        "core_ready": smoke.get("core_ready", True),
        "desktop_ready": smoke.get("desktop_ready", False),
        "desktop_error": smoke.get("desktop_error", ""),
    })
    return result


def install_archive(
    archive: Path,
    *,
    python: str | None = None,
    keep: int = 3,
    dry_run: bool = False,
) -> dict[str, Any]:
    try:
        archive = resolve_archive(archive)
    except ArchiveNotFoundError as exc:
        raise UpdateError(str(exc)) from exc
    with tempfile.TemporaryDirectory(prefix="cifereca-update-") as tmp:
        source = _extract_release(archive, Path(tmp))
        return install_source(source, python=python, keep=keep, dry_run=dry_run)


def status() -> dict[str, Any]:
    root = install_root()
    return {
        "ok": True,
        "install_root": str(root),
        "current": str(_symlink_target(root / "current") or "") or None,
        "previous": str(_symlink_target(root / "previous") or "") or None,
        "releases": [str(x) for x in _releases(root)],
        "launchers": {x: str(bin_dir() / x) for x in PUBLIC_COMMANDS},
    }


def rollback(to: str | None = None) -> dict[str, Any]:
    root = install_root()
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
    _smoke(target)
    if current and current.is_dir():
        _replace_symlink(root / "previous", current)
    _replace_symlink(root / "current", target)
    launchers = _write_launchers(root)
    return {"ok": True, "current": str(target), "launchers": launchers}


def _print(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    else:
        for key, val in value.items():
            print(f"{key}: {val}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Updater atomique cifereca")
    p.add_argument("archive", nargs="?")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--rollback", action="store_true")
    p.add_argument("--to")
    p.add_argument("--keep", type=int, default=3)
    p.add_argument("--python")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        if a.status:
            result = status()
        elif a.rollback:
            result = rollback(a.to)
        else:
            if not a.archive:
                p.error("archive ZIP requise")
            result = install_archive(a.archive, python=a.python, keep=a.keep, dry_run=a.dry_run)
        _print(result, a.json)
        return 0
    except UpdateError as exc:
        error = {"ok": False, "error": {"code": "update_error", "message": str(exc)}}
        _print(error, a.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
