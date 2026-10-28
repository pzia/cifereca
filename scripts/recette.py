#!/usr/bin/env python3
"""Disposable, offline Linux acceptance environment. Standard library only.

Never import Config here: opening a production Config chmods its file.
The OS sandbox, rather than rewritten settings, is the security boundary.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
import uuid


class Rejected(RuntimeError):
    pass


FORMAT = "cifereca-recette-v1"
SESSION_PARTS = {"sessions", "session", "cookies", "browser", "browser-profiles",
                 "chromium", "firefox", "playwright", "__pycache__"}
SESSION_KEYS = re.compile(r"(^|_)(session|cookie|cookies|token|refresh_token|access_token)($|_)", re.I)
PATH_KEYS = re.compile(r"(?:^|_)(path|directory|folder|root|file|socket|profile_dir|database|db)$", re.I)
OPAQUE_KEYS = {"id", "adept_profile", "password", "passwd", "pwd", "passphrase",
               "username", "login", "api_key", "apikey", "private_key", "secret", "client_secret"}


def opaque_field(key: str) -> bool:
    return key.lower() in OPAQUE_KEYS or key.lower().endswith("_id")


def recipe_root() -> Path:
    return Path.home() / "cifereca-recettes"


def no_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise Rejected(f"Symbolic link refused: {part}")


@contextmanager
def source_stream(path: Path):
    """No atime change, symlink following, FIFO/device access or source writes."""
    no_links(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Rejected(f"Not a regular file: {path}")
        yield stream


def read_private_source(path: Path) -> bytes:
    with source_stream(path) as stream:
        return stream.read()


def copy_private_source(source: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    with source_stream(source) as stream, destination.open("wb") as output:
        while chunk := stream.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    destination.chmod(0o700 if source.stat().st_mode & 0o111 else 0o600)
    return digest.hexdigest()


def fingerprint(path: Path) -> dict:
    with source_stream(path) as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    info = path.stat()
    return {"sha256": digest, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "mode": stat.S_IMODE(info.st_mode), "device": info.st_dev,
            "inode": info.st_ino, "links": info.st_nlink}


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


def load_config(path: Path) -> dict:
    data = read_private_source(path)
    value = tomllib.loads(data.decode()) if path.suffix == ".toml" else json.loads(data)
    if not isinstance(value, dict):
        raise Rejected("Configuration must be an object")
    return value


def local_path(value: str) -> Path:
    value = os.path.expandvars(os.path.expanduser(value))
    if "$" in value:
        raise Rejected("Unresolved environment variable in local path")
    path = Path(value)
    if not path.is_absolute():
        path = Path.home() / path
    no_links(path)
    return Path(os.path.abspath(path))


def target_path(value: Path | None) -> Path:
    root = recipe_root().absolute()
    no_links(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_uid != os.getuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise Rejected("Recipe root must belong to current user with mode 0700")
    target = value.absolute() if value else root / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    no_links(target)
    if target.parent != root or target.name.startswith("."):
        raise Rejected(f"Target must be a direct child of {root}")
    return target


def checked_target(path: Path) -> tuple[Path, dict]:
    target = path.absolute()
    no_links(target)
    if target.parent != recipe_root().absolute():
        raise Rejected("Target outside recipe root")
    info = target.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Rejected("Untrusted target owner/permissions")
    marker = json.loads((target / "recipe.json").read_text())
    if marker.get("format") != FORMAT or marker.get("target") != str(target):
        raise Rejected("Unrecognized recipe")
    return target, marker


class Copier:
    def __init__(self, target: Path):
        self.target = target
        self.mapping: dict[Path, Path] = {}
        self.sources: dict[str, dict] = {}
        self.databases: set[Path] = set()
        self.copied: set[Path] = set()
        self.excluded: list[str] = []
        self.content_roots: set[Path] = set()

    def register(self, source: Path, destination: Path) -> None:
        source = source.absolute()
        no_links(source)
        if source == Path("/") or source in self.target.parents or self.target.is_relative_to(source) or source.is_relative_to(self.target):
            raise Rejected(f"Overlapping or overbroad source: {source}")
        if source in self.mapping and self.mapping[source] != destination:
            raise Rejected(f"Ambiguous source mapping: {source}")
        self.mapping[source] = destination

    def mapped(self, path: Path) -> Path | None:
        for source in sorted(self.mapping, key=lambda p: len(str(p)), reverse=True):
            if path.is_relative_to(source):
                return self.mapping[source] / path.relative_to(source)
        return None

    def capture(self, source: Path, destination: Path) -> None:
        no_links(source)
        destination = self.mapped(source) or destination
        if not destination.is_relative_to(self.target) or ".." in destination.parts:
            raise Rejected("Copy destination outside recipe")
        if source in self.copied or not source.exists():
            return
        name = source.name.lower()
        is_content = any(source.is_relative_to(root) for root in self.content_roots)
        if not is_content and (name in SESSION_PARTS or name.startswith(("cookies.", "session.", "storage_state."))):
            self.excluded.append(str(source))
            return
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.copied.add(source)
            for entry in sorted(source.iterdir()):
                # Sidecars are staged together with their main database below.
                if entry.name.endswith(("-wal", "-shm", "-journal")):
                    continue
                self.capture(entry, destination / entry.name)
            return
        with source_stream(source) as stream:
            header = stream.read(16)
        if header == b"SQLite format 3\x00":
            self.backup(source, destination)
        else:
            self.sources[str(source)] = fingerprint(source)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            digest = copy_private_source(source, destination)
            if digest != self.sources[str(source)]["sha256"] or fingerprint(source) != self.sources[str(source)]:
                raise Rejected("Source changed during copy")
        self.copied.add(source)

    def backup(self, source: Path, destination: Path) -> None:
        """Stage a stable DB/WAL set without opening production via SQLite.

        A read-only SQLite connection can create/update SHM. Instead require
        two identical observations of the *entire* source set around staging,
        then run Backup API against the private set (including committed WAL).
        Busy writers cause bounded retry, never an inconsistent fallback.
        """
        stage = self.target / ".sqlite-stage"
        stage.mkdir(mode=0o700, exist_ok=True)
        try:
            for _attempt in range(5):
                paths = [source] + [Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")
                                    if Path(str(source) + suffix).exists()]
                before = {str(p): fingerprint(p) for p in paths}
                staged = {str(p): copy_private_source(p, stage / p.name) for p in paths}
                after_paths = [source] + [Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")
                                          if Path(str(source) + suffix).exists()]
                after = {str(p): fingerprint(p) for p in after_paths}
                staged_ok = all(staged[str(p)] == before[str(p)]["sha256"] for p in paths)
                if before == after and staged_ok:
                    break
                for p in stage.iterdir():
                    p.unlink()
            else:
                raise Rejected("SQLite did not stabilize; retry when production is quieter")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with sqlite3.connect((stage / source.name).as_uri() + "?mode=ro", uri=True) as src:
                with sqlite3.connect(destination) as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise Rejected("SQLite backup integrity failure")
            destination.chmod(0o600)
            self.databases.add(destination)
            self.sources.update(before)
        finally:
            shutil.rmtree(stage)

    def rewrite(self, value, key: str = ""):
        if isinstance(value, dict):
            return {k: self.rewrite(v, k) for k, v in value.items() if not SESSION_KEYS.search(k)}
        if isinstance(value, list):
            return [self.rewrite(v, key) for v in value]
        if not isinstance(value, str):
            return value
        if opaque_field(key):
            # Secrets and provider identifiers may legitimately start with /
            # or contain a source directory. They are not filesystem paths.
            return value
        if key == "relative_path":
            # Catalog metadata relative to its (already relocated) library
            # root is descriptive; resolving it against HOME loses the file.
            if Path(value).is_absolute() or ".." in Path(value).parts:
                raise Rejected("Invalid catalog relative path")
            return value
        if value[:1] in {"{", "["}:
            try:
                return json.dumps(self.rewrite(json.loads(value)), ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        prefix = next((p for p in ("sqlite://", "file://", "file:") if value.startswith(p)), "")
        candidate = value[len(prefix):]
        if candidate.startswith(("/", "~/")) or (value and PATH_KEYS.search(key) and "://" not in value):
            path = local_path(candidate)
            if path.is_relative_to(self.target):
                return value
            destination = self.mapped(path)
            if destination is None:
                # Unknown external paths are copied privately, including local
                # dependencies. Missing historical paths remain missing inside.
                destination = self.target / "imports" / hashlib.sha256(str(path).encode()).hexdigest()[:16] / path.name
                self.register(path, destination)
            self.capture(path, destination)
            return prefix + str(destination)
        # Embedded original roots in URLs/JSON/text are rewritten too.
        for old in sorted(self.mapping, key=lambda p: len(str(p)), reverse=True):
            value = value.replace(str(old), str(self.mapping[old]))
        return value

    def rewrite_database(self, path: Path) -> None:
        def quote(name):
            return '"' + name.replace('"', '""') + '"'
        with sqlite3.connect(path) as db:
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            for (table,) in tables:
                for column in db.execute(f"PRAGMA table_info({quote(table)})").fetchall():
                    name = column[1]
                    for (old,) in db.execute(f"SELECT DISTINCT {quote(name)} FROM {quote(table)} WHERE typeof({quote(name)})='text'").fetchall():
                        new = self.rewrite(old, name)
                        if new != old:
                            db.execute(f"UPDATE {quote(table)} SET {quote(name)}=? WHERE {quote(name)}=?", (new, old))
            # Cached provider responses can carry live cookies/signed URLs.
            if ("cache",) in tables:
                db.execute("DELETE FROM cache")
            if ("provider_connections",) in tables:
                db.execute("UPDATE provider_connections SET enabled=0")
            if ("device_bindings",) in tables:
                db.execute("UPDATE device_bindings SET enabled=0")
            db.commit()
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise Rejected("Rewritten SQLite failed integrity check")
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def sandbox(target: Path, command: list[str], *, network: bool = False, gui: bool = False) -> list[str]:
    if sys.platform != "linux" or not shutil.which("bwrap"):
        raise Rejected("Linux Bubblewrap is required; no unconfined fallback")
    args = [shutil.which("bwrap"), "--unshare-all", "--die-with-parent", "--new-session",
            "--cap-drop", "ALL", "--clearenv"]
    if network:
        args += ["--share-net"]  # Dependency installation only; never runtime.
    for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64"):
        if Path(path).exists():
            args += ["--ro-bind", path, path]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--tmpfs", "/run",
             "--dir", "/etc"]
    for path in ("/etc/ld.so.cache", "/etc/fonts", "/etc/ssl/certs", "/etc/dbus-1", "/etc/resolv.conf", "/etc/hosts", "/etc/passwd", "/etc/group"):
        if Path(path).exists():
            args += ["--ro-bind", path, path]
    args += ["--bind", str(target), str(target), "--chdir", str(target),
             "--setenv", "HOME", str(target / "home"),
             "--setenv", "XDG_CONFIG_HOME", str(target / "config"),
             "--setenv", "XDG_DATA_HOME", str(target / "data"),
             "--setenv", "XDG_CACHE_HOME", str(target / "cache"),
             "--setenv", "XDG_STATE_HOME", str(target / "state"),
             "--setenv", "XDG_RUNTIME_DIR", str(target / "runtime"),
             "--setenv", "TMPDIR", str(target / "tmp"),
             "--setenv", "PATH", str(target / "venv/bin") + ":/usr/bin:/bin",
             "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
             "--setenv", "PYTHONNOUSERSITE", "1", "--setenv", "LANG", "C.UTF-8",
             "--setenv", "PIP_DISABLE_PIP_VERSION_CHECK", "1",
             "--setenv", "PIP_CACHE_DIR", str(target / "cache/pip")]
    if (target / "runtime/machine-id").exists():
        args += ["--ro-bind", str(target / "runtime/machine-id"), "/etc/machine-id"]
    if network:
        # Package installation is allowed to fetch wheels, but must not see
        # the production *copies* either. A writable target bind by itself
        # would expose passwords/books to network-enabled build subprocesses.
        for name in ("config", "data", "library", "imports", "home", "state", "runtime", "tmp"):
            args += ["--tmpfs", str(target / name)]
    if gui:
        display = os.environ.get("DISPLAY", "")
        if not re.fullmatch(r":\d+(\.\d+)?", display):
            raise Rejected("Local X11 DISPLAY required (XWayland accepted)")
        number = display[1:].split(".")[0]
        socket = Path(f"/tmp/.X11-unix/X{number}")
        if not stat.S_ISSOCK(socket.stat().st_mode):
            raise Rejected("Missing local X11 socket")
        # Only the display socket is exposed; never host D-Bus/SSH agents,
        # devices, /run/user or other application sockets.
        args += ["--ro-bind", str(socket), str(socket), "--setenv", "DISPLAY", display,
                 "--setenv", "GDK_BACKEND", "x11", "--setenv", "NO_AT_BRIDGE", "1"]
        if (target / "runtime/Xauthority").exists():
            args += ["--setenv", "XAUTHORITY", str(target / "runtime/Xauthority")]
    # bwrap's synthetic root is otherwise owned/writable by the namespace's
    # user. Keep only the explicit target and private tmpfs mounts writable.
    return args + ["--remount-ro", "/", "--"] + command


def probe(target: Path) -> None:
    subprocess.run(sandbox(target, ["/usr/bin/true"]), check=True, timeout=15,
                   stdout=subprocess.DEVNULL)


def export_snapshot(target: Path, ref: str) -> str:
    if ref != "main" and not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
        raise Rejected("--ref must be main or a full commit SHA")
    repo = Path(__file__).resolve().parents[1]
    remote = subprocess.check_output(["git", "-C", str(repo), "remote", "get-url", "origin"], text=True).strip()
    if not re.fullmatch(r"https://[^/@\s]+/[^\s?#]+", remote):
        raise Rejected("origin must be an HTTPS URL without embedded credentials")
    gitdir = target / "snapshot.git"
    subprocess.run(["git", "init", "--bare", str(gitdir)], check=True, stdout=subprocess.DEVNULL)
    try:
        subprocess.run(["git", "--git-dir", str(gitdir), "fetch", "--depth=1", remote,
                        "refs/heads/main" if ref == "main" else ref], check=True)
        sha = subprocess.check_output(["git", "--git-dir", str(gitdir), "rev-parse", "FETCH_HEAD"], text=True).strip()
        archive = subprocess.check_output(["git", "--git-dir", str(gitdir), "archive", sha])
        code = target / "code"
        code.mkdir(mode=0o700)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for entry in tar:
                name = Path(entry.name)
                if name.is_absolute() or ".." in name.parts or not (entry.isdir() or entry.isfile()):
                    raise Rejected("Unsafe snapshot archive member")
                output = code / name
                if entry.isdir():
                    output.mkdir(parents=True, exist_ok=True, mode=0o700)
                else:
                    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    output.write_bytes(tar.extractfile(entry).read())
                    output.chmod(0o700 if entry.mode & 0o111 else 0o600)
        return sha
    finally:
        shutil.rmtree(gitdir)


def install(target: Path) -> None:
    probe(target)
    log = target / "install.log"
    with log.open("a") as stream:
        project = tomllib.loads((target / "code/pyproject.toml").read_text())["project"]
        commands = [
            (["/usr/bin/python3", "-m", "venv", "--copies", "--system-site-packages", str(target / "venv")], False),
            ([str(target / "venv/bin/python"), "-m", "pip", "install", "--only-binary=:all:", "setuptools>=69", "wheel", *project.get("dependencies", [])], True),
            ([str(target / "venv/bin/python"), "-m", "pip", "install", "--no-build-isolation", "--no-deps", str(target / "code")], False),
        ]
        # Only the wheel-fetch/install worker gets egress, with private copies
        # hidden. Subsequent Python startups may execute installed .pth files:
        # local builds and all later commands must already be offline.
        for command, network in commands:
            subprocess.run(sandbox(target, command, network=network), check=True, stdout=stream, stderr=subprocess.STDOUT)
        # venv creates a redundant lib64 -> lib convenience symlink.
        lib64 = target / "venv/lib64"
        if lib64.is_symlink() and lib64.readlink() == Path("lib"):
            lib64.unlink()
        freeze = subprocess.check_output(sandbox(target, [str(target / "venv/bin/python"), "-m", "pip", "freeze", "--local"]), text=True)
        (target / "requirements-installed.txt").write_text(freeze)
        # Exercise migrations using the installed frozen Core, before any UI
        # startup. This opens only the copied default database.
        subprocess.run(sandbox(target, [str(target / "venv/bin/python"), "-c",
                       "from cifereca.storage import Storage; s=Storage(); "
                       "print('schema_version='+str(s.schema_version()))"]), check=True,
                       stdout=stream, stderr=subprocess.STDOUT)


def inventory(target: Path) -> dict:
    files = {}
    for path in sorted(target.rglob("*")):
        info = path.lstat()
        if path.is_symlink() or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise Rejected(f"Link or special file inside recipe: {path.relative_to(target)}")
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise Rejected(f"Non-private entry: {path.relative_to(target)}")
        if path.is_file():
            if info.st_nlink != 1:
                raise Rejected(f"Hardlink inside recipe: {path.relative_to(target)}")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files[str(path.relative_to(target))] = {"sha256": digest, "size": info.st_size}
    return files


def confined_values(value, target: Path, key: str = "", *, forbidden_roots=()) -> None:
    """Verify path-bearing values without copying, rewriting or executing."""
    if isinstance(value, dict):
        for name, item in value.items():
            confined_values(item, target, name, forbidden_roots=forbidden_roots)
    elif isinstance(value, list):
        for item in value:
            confined_values(item, target, key, forbidden_roots=forbidden_roots)
    elif isinstance(value, str):
        if opaque_field(key):
            return
        if key == "relative_path":
            if Path(value).is_absolute() or ".." in Path(value).parts:
                raise Rejected("Invalid catalog relative path")
            return
        if value[:1] in {"{", "["}:
            try:
                confined_values(json.loads(value), target, forbidden_roots=forbidden_roots)
                return
            except json.JSONDecodeError:
                pass
        if any(old in value for old in forbidden_roots):
            raise Rejected("Production reference remains in database")
        prefix = next((p for p in ("sqlite://", "file://", "file:") if value.startswith(p)), "")
        candidate = value[len(prefix):]
        if candidate.startswith(("/", "~/")) or (value and PATH_KEYS.search(key) and "://" not in value):
            path = Path(candidate)
            if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(target):
                raise Rejected("Runtime path outside recipe")


def verify(path: Path, *, initial: bool = False) -> dict:
    target, marker = checked_target(path)
    manifest = json.loads((target / "manifest.json").read_text())
    if manifest.get("id") != marker.get("id"):
        raise Rejected("Manifest identity mismatch")
    files = inventory(target)
    changed = [p for p, info in manifest["sources"].items() if not Path(p).exists() or fingerprint(Path(p)) != info]
    if changed:
        raise Rejected(f"Production changed since capture ({len(changed)} files); no unchanged-source proof")
    for config in (target / "config/cifereca").glob("*.json"):
        confined_values(json.loads(config.read_text()), target)
    # Freeze the executable boundary; mutable application data are checked for
    # links/permissions and confinement, not required to remain byte-identical.
    immutable = ("code/", "venv/", "runtime-tool.py", "run.sh", "stop.sh")
    for name, info in manifest.get("files", {}).items():
        if name.startswith(immutable) and files.get(name) != info:
            raise Rejected(f"Executable inventory changed: {name}")
    for relative in manifest["databases"]:
        database = target / relative
        if not database.is_relative_to(target) or ".." in Path(relative).parts:
            raise Rejected("Database outside recipe")
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise Rejected("Database integrity failure")
            # All SQL text columns (including nested JSON) must be free of
            # original root references. A residual reference blocks startup.
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                quoted = '"' + table.replace('"', '""') + '"'
                rows = db.execute(f"SELECT * FROM {quoted}")
                columns = [column[0] for column in rows.description]
                for row in rows:
                    for column, value in zip(columns, row):
                        confined_values(value, target, column, forbidden_roots=manifest["path_map"])
    report = {"format": FORMAT, "id": marker["id"], "sha": manifest["sha"],
              "ok": True, "source_files_unchanged": len(manifest["sources"]),
              "files_checked": len(files), "network": "disabled", "sandbox": "required",
              "phase": "prepared" if initial else "verified"}
    write_json(target / "verification.json", report)
    return report


def seal_permissions(target: Path) -> None:
    for path in [target, *target.rglob("*")]:
        if not path.is_symlink():
            path.chmod(0o700 if path.is_dir() or path.stat().st_mode & 0o111 else 0o600)


def create(config_path: Path, target: Path | None, ref: str, *, no_start: bool) -> Path:
    if sys.platform != "linux":
        raise Rejected("Recette currently requires Linux")
    os.umask(0o077)
    no_links(config_path)
    config_path = Path(os.path.abspath(config_path))
    original = load_config(config_path)
    target = target_path(target)
    target.mkdir(mode=0o700)  # Exclusive: never reuse/overwrite an existing recipe.
    reservation = (target / "run.lock").open("a")
    fcntl.flock(reservation, fcntl.LOCK_EX | fcntl.LOCK_NB)
    marker = {"format": FORMAT, "id": uuid.uuid4().hex, "target": str(target), "state": "preparing"}
    try:
        write_json(target / "recipe.json", marker)
        for name in ("home", "config/cifereca", "data/cifereca", "cache", "state", "runtime", "tmp"):
            (target / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        (target / "runtime/machine-id").write_text(marker["id"] + "\n")
        copier = Copier(target)
        config_source = config_path.absolute()
        data_source = local_path(str(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "cifereca"))
        library_source = local_path(str(original.get("library", {}).get("directory") or "~/EBooks"))
        copier.register(config_source.parent, target / "config/cifereca")
        copier.register(data_source, target / "data/cifereca")
        copier.register(library_source, target / "library")
        copier.content_roots.add(library_source)
        # Runtime caches, browser profiles and installed old plugin code are
        # never copied. Packages come from the frozen source tree instead.
        copier.sources[str(config_source)] = fingerprint(config_source)
        for name in ("plugins-config.json", "plugins-state.json"):
            source = config_source.parent / name
            if source.exists():
                copier.capture(source, target / "config/cifereca" / name)
        if data_source.exists():
            for entry in sorted(data_source.iterdir()):
                if entry.name != "plugins":
                    copier.capture(entry, target / "data/cifereca" / entry.name)
        copier.capture(library_source, target / "library")
        # Disable executable hooks and remote actions before discovering paths.
        original.setdefault("downloads", {}).update(command=[], post_download_command=[])
        original.setdefault("kobo_backup", {})["enabled"] = False
        original.setdefault("llm", {}).update(enabled=False, allow_remote=False)
        original["experimental_actions"] = False
        original.setdefault("providers", {})["gallica_migration_031"] = True
        for account in original.get("providers", {}).get("accounts", []):
            account["enabled"] = False
        config = copier.rewrite(original)
        config.setdefault("library", {})["directory"] = str(target / "library")
        config["recette"] = {"id": marker["id"], "offline": True, "automatic_sync": False}
        write_json(target / "config/cifereca/config.json", config)
        # References discovered in one database may introduce another database.
        processed = set()
        while copier.databases - processed:
            database = sorted(copier.databases - processed)[0]
            copier.rewrite_database(database)
            processed.add(database)
        for path in (target / "config/cifereca").glob("plugins-*.json"):
            write_json(path, copier.rewrite(json.loads(path.read_text())))
        sha = export_snapshot(target, ref)
        install(target)
        default_database = target / "data/cifereca/library.sqlite3"
        if default_database.exists():
            copier.databases.add(default_database)
        # Include any JSON sidecar settings captured under application data.
        # Book content is left byte-for-byte intact.
        for path in (target / "data/cifereca").rglob("*.json"):
            write_json(path, copier.rewrite(json.loads(path.read_text())))
        # Install frozen functional packages without activating their sessions.
        packages = target / "data/cifereca/plugins/packages"
        for plugin in sorted((target / "code/src/plugins").iterdir()):
            if (plugin / "cifereca-plugin.json").is_file():
                metadata = json.loads((plugin / "cifereca-plugin.json").read_text())
                package_id = metadata["package"]["id"]
                version = metadata["package"]["version"]
                if not all(re.fullmatch(r"[A-Za-z0-9._+-]+", value) and value not in {".", ".."} for value in (package_id, version)):
                    raise Rejected("Unsafe plugin package identifier")
                shutil.copytree(plugin, packages / package_id / version)
                write_json(packages / package_id / "current.json", {"active": version, "previous": None})
        shutil.copyfile(Path(__file__), target / "runtime-tool.py")
        for filename, action in (("run.sh", "_run"), ("stop.sh", "_stop")):
            (target / filename).write_text("#!/bin/sh\nexec /usr/bin/python3 " + shlex.quote(str(target / "runtime-tool.py")) + " " + action + " " + shlex.quote(str(target)) + "\n")
            (target / filename).chmod(0o700)
        marker["state"] = "ready"
        write_json(target / "recipe.json", marker)
        seal_permissions(target)
        manifest = {**marker, "sha": sha, "requested_ref": ref, "network": "disabled",
                    "sources": copier.sources, "path_map": {str(k): str(v) for k, v in copier.mapping.items()},
                    "databases": [str(p.relative_to(target)) for p in sorted(copier.databases)],
                    "excluded_sessions": copier.excluded, "files": inventory(target)}
        write_json(target / "manifest.json", manifest)
        verify(target, initial=True)
        reservation.close()  # The runtime supervisor now owns the same lock.
        if not no_start:
            start(target)
        return target
    except BaseException:
        if not (target / "verification.json").exists():
            marker["state"] = "failed"
            write_json(target / "recipe.json", marker)
        raise
    finally:
        reservation.close()


def process_identity(pid: int) -> str | None:
    try:
        # comm can contain spaces or parentheses; starttime is field 22.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def runtime_record(target: Path) -> dict | None:
    try:
        row = json.loads((target / "process.json").read_text())
        if row["starttime"] and process_identity(row["pid"]) == row["starttime"]:
            return row
    except (OSError, ValueError, KeyError):
        pass
    return None


def run(target: Path) -> int:
    target, marker = checked_target(target)
    with (target / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected("Recipe already running")
        if marker["state"] != "ready":
            raise Rejected("Recipe is not ready")
        verify(target)
        probe(target)
        # Copy only display authorization; no session bus, provider cookies or
        # browser profile is inherited. The display uses a fresh private bus.
        auth = Path(os.environ.get("XAUTHORITY", str(Path.home() / ".Xauthority")))
        if auth.is_file():
            (target / "runtime/Xauthority").write_bytes(read_private_source(auth))
        command = sandbox(target, ["/usr/bin/dbus-run-session", "--", str(target / "venv/bin/python"),
                                   "-m", "cifereca", "--no-plugins"], gui=True)
        with (target / "runtime.log").open("a") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            write_json(target / "process.json", {"pid": process.pid, "starttime": process_identity(process.pid), "id": marker["id"]})
            try:
                return process.wait()
            finally:
                (target / "process.json").unlink(missing_ok=True)
                (target / "runtime/Xauthority").unlink(missing_ok=True)


def start(target: Path) -> None:
    # A detached supervisor holds run.lock until the entire sandbox exits.
    with (target / "launcher.log").open("a") as log:
        child = subprocess.Popen(["/usr/bin/python3", str(target / "runtime-tool.py"), "_run", str(target)],
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        result = child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return
    if result:
        raise Rejected(f"Startup failed; inspect {target / 'launcher.log'} and runtime.log")


def stop(target: Path) -> None:
    target, marker = checked_target(target)
    row = runtime_record(target)
    if row:
        if row.get("id") != marker["id"] or os.getpgid(row["pid"]) != row["pid"]:
            raise Rejected("Untrusted runtime process")
        command = Path(f"/proc/{row['pid']}/cmdline").read_bytes().split(b"\0")
        if not command or Path(os.fsdecode(command[0])).name != "bwrap" or os.fsencode(target) not in command:
            raise Rejected("PID does not belong to this recipe's sandbox")
        os.killpg(row["pid"], signal.SIGTERM)
        until = time.monotonic() + 5
        while process_identity(row["pid"]) == row["starttime"] and time.monotonic() < until:
            time.sleep(0.05)
        if process_identity(row["pid"]) == row["starttime"]:
            os.killpg(row["pid"], signal.SIGKILL)
    # Synchronize with the supervisor before removal; catches startup races.
    with (target / "run.lock").open("a") as lock:
        until = time.monotonic() + 1
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise Rejected("Supervisor still running; retry stop/destroy")
                time.sleep(0.05)
    (target / "process.json").unlink(missing_ok=True)


def destroy(target: Path) -> None:
    target, _ = checked_target(target)
    stop(target)
    # Never follow symlinks in cleanup; shutil.rmtree uses fd-based traversal
    # on supported Linux, and checked_target forbids aliased root/ancestors.
    if not shutil.rmtree.avoids_symlink_attacks:
        raise Rejected("Safe directory removal unavailable")
    with (target / "run.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected("A launch raced with destroy; retry")
        shutil.rmtree(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("verify", "destroy", "_run", "_stop"))
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--no-start", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action:
            if args.path is None:
                parser.error("action requires PATH")
            if args.action == "verify":
                print(json.dumps(verify(args.path), ensure_ascii=False))
            elif args.action == "destroy":
                destroy(args.path)
            elif args.action == "_stop":
                stop(args.path)
            else:
                return run(args.path)
        else:
            config = args.config or Path.home() / ".config/cifereca/config.toml"
            if args.config is None and not config.exists():
                config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cifereca/config.json"
            print(create(config, args.target, args.ref, no_start=args.no_start))
        return 0
    except (Rejected, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"Recette refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
