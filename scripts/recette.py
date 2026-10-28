#!/usr/bin/env python3
"""Disposable, offline Linux acceptance environment. Standard library only.

Never import Config here: opening a production Config chmods its file.
The OS sandbox, rather than rewritten settings, is the security boundary.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
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


def readlink_noatime(parent: int, name: str) -> str:
    """readlink can update symlink atime even with O_PATH|O_NOATIME.

    Read through a read-only mount of the pinned parent instead. No source
    metadata is restored afterwards (that would itself write production).
    """
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise Rejected("Bubblewrap required to read source links without changing atime")
    command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session",
               "--cap-drop", "ALL", "--clearenv"]
    for system in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(system).exists():
            command += ["--ro-bind", system, system]
    command += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/etc"]
    if Path("/etc/ld.so.cache").exists():
        command += ["--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"]
    # Use Bubblewrap's FD binding, not another process's /proc/PID/fd path:
    # crossing into a user namespace can forbid that procfs dereference.
    # The bind operation itself validates the pinned directory identity.
    command += ["--ro-bind-fd", str(parent), "/source",
                "--remount-ro", "/", "--", "/usr/bin/python3", "-I", "-B", "-c",
                "import json,os,sys; print(json.dumps(os.readlink('/source/'+sys.argv[1])))", name]
    return json.loads(subprocess.check_output(command, text=True, timeout=15, pass_fds=(parent,)))


@contextmanager
def parent_fd(path: Path):
    """Pin every ancestor without following a symlink, including race swaps."""
    path = path.absolute()
    if ".." in path.parts:
        raise Rejected("Path traversal refused")
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY)
    try:
        for component in path.parent.parts[1:]:
            child = os.open(component, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def private_directory(path: Path) -> None:
    """Create private destination containers; never chmod an existing path."""
    no_links(path)
    if not path.parent.exists():
        private_directory(path.parent)
    with parent_fd(path) as fd:
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=fd)
        except FileExistsError:
            pass
        info = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise Rejected(f"Directory must belong to current user with mode 0700: {path}")


@contextmanager
def source_stream(path: Path):
    """No atime change, symlink following, FIFO/device access or source writes."""
    no_links(path)
    with parent_fd(path) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Rejected(f"Not a regular file: {path}")
        yield stream


def read_private_source(path: Path) -> bytes:
    with source_stream(path) as stream:
        return stream.read()


def copy_private_source(source: Path, destination: Path) -> str:
    digest = hashlib.sha256()
    private_directory(destination.parent)
    with source_stream(source) as stream:
        with parent_fd(destination) as parent:
            fd = os.open(destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as output:
            while chunk := stream.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
            os.fchmod(output.fileno(), 0o700 if os.fstat(stream.fileno()).st_mode & 0o111 else 0o600)
    return digest.hexdigest()


def fingerprint(path: Path) -> dict:
    with source_stream(path) as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        info = os.fstat(stream.fileno())
    return {"sha256": digest, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "mode": stat.S_IMODE(info.st_mode), "device": info.st_dev,
            "inode": info.st_ino, "links": info.st_nlink}


def write_json(path: Path, value) -> None:
    private_directory(path.parent)
    temp = path.name + "." + uuid.uuid4().hex + ".tmp"
    with parent_fd(path) as parent:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            os.replace(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        finally:
            try:
                os.unlink(temp, dir_fd=parent)
            except FileNotFoundError:
                pass


def private_append(path: Path):
    """Open a recipe log/lock without following or writing an existing alias."""
    with parent_fd(path) as parent:
        fd = os.open(path.name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=parent)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        os.close(fd)
        raise Rejected("Untrusted recipe log/lock")
    return os.fdopen(fd, "a")


def unlink_private(path: Path) -> None:
    with parent_fd(path) as parent:
        try:
            os.unlink(path.name, dir_fd=parent)
        except FileNotFoundError:
            pass


def load_config(path: Path) -> dict:
    data = read_private_source(path)
    value = tomllib.loads(data.decode()) if path.suffix == ".toml" else json.loads(data)
    if not isinstance(value, dict):
        raise Rejected("Configuration must be an object")
    return value


def local_path(value: str, *, links: bool = False) -> Path:
    value = os.path.expandvars(os.path.expanduser(value))
    if "$" in value:
        raise Rejected("Unresolved environment variable in local path")
    path = Path(value)
    if not path.is_absolute():
        path = Path.home() / path
    if not links:
        no_links(path)
    return Path(os.path.abspath(path))


def approved_root(value: Path | None = None) -> Path:
    root = (value if value is not None else recipe_root()).expanduser().absolute()
    if ".." in root.parts:
        raise Rejected("Recipe root traversal refused")
    no_links(root)
    return root


def root_identity(root: Path, *, create: bool = False) -> str:
    if not create and not root.exists():
        raise Rejected("Approved recipe root missing")
    private_directory(root)
    anchor = root / ".cifereca-recipe-root.json"
    if create and not anchor.exists():
        # An explicit create is the approval of this private root. Keep a
        # persistent identity so run.sh/verify/destroy need no default root.
        with parent_fd(anchor) as parent:
            try:
                fd = os.open(anchor.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w") as stream:
                    json.dump({"root": str(root), "id": uuid.uuid4().hex}, stream)
    info = anchor.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
        raise Rejected("Untrusted recipe root identity")
    value = json.loads(read_private_source(anchor))
    if (value.get("root") != str(root) or type(value.get("id")) is not str
            or re.fullmatch(r"[0-9a-f]{32}", value["id"]) is None):
        raise Rejected("Untrusted recipe root identity")
    return value["id"]


def target_path(value: Path | None, *, root: Path | None = None) -> Path:
    root = approved_root(root)
    target = value.expanduser().absolute() if value else root / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    no_links(target)
    if (not target.is_relative_to(root) or target == root
            or any(p == ".." or p.startswith(".") for p in target.relative_to(root).parts)):
        raise Rejected(f"Target must be below approved recipe root {root}")
    private_directory(root)
    private_directory(target.parent)
    return target


def check_recipe_filesystem(root: Path) -> None:
    """Require working POSIX modes, independent flock, rename and execution."""
    directory = root / (".probe-" + uuid.uuid4().hex)
    with parent_fd(directory) as parent:
        os.mkdir(directory.name, mode=0o700, dir_fd=parent)
    try:
        private_directory(directory)
        program = directory / "check"
        with parent_fd(program) as parent:
            fd = os.open(program.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
        with os.fdopen(fd, "w") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                raise Rejected("Recipe filesystem does not enforce private file modes")
            stream.write("#!/bin/sh\nexit 0\n")
            stream.flush()
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with program.open("r") as second:
                try:
                    fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise Rejected("Recipe filesystem does not enforce flock")
            os.fchmod(stream.fileno(), 0o700)
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o700:
                raise Rejected("Recipe filesystem does not enforce executable modes")
        renamed = directory / "executable"
        program.rename(renamed)
        subprocess.run([str(renamed)], env={"PATH": "/usr/bin:/bin"}, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Rejected("Recipe filesystem must support private modes, flock, atomic rename and execution (no noexec volume)") from exc
    finally:
        with parent_fd(directory) as parent:
            shutil.rmtree(directory.name, dir_fd=parent)


def checked_target(path: Path, *, root: Path | None = None) -> tuple[Path, dict]:
    target = path.expanduser().absolute()
    no_links(target)
    info = target.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise Rejected("Untrusted target owner/permissions")
    try:
        marker = json.loads(read_private_source(target / "recipe.json"))
    except (OSError, ValueError) as exc:
        raise Rejected("Unrecognized recipe") from exc
    if type(marker) is not dict or marker.get("format") != FORMAT or marker.get("target") != str(target):
        raise Rejected("Unrecognized recipe")
    recorded = marker.get("recipe_root")
    if recorded is None:
        # Old v1 recipes remain usable only in the old default location.
        if target.parent != approved_root(root):
            raise Rejected("Target outside recipe root")
        private_directory(target.parent)
    else:
        if type(recorded) is not str:
            raise Rejected("Untrusted recipe root identity")
        recorded = approved_root(Path(recorded))
        if root is not None and approved_root(root) != recorded:
            raise Rejected("Recipe root does not match recorded approval")
        if (target == recorded or not target.is_relative_to(recorded)
                or any(p == ".." or p.startswith(".") for p in target.relative_to(recorded).parts)
                or root_identity(recorded) != marker.get("root_id")):
            raise Rejected("Target outside approved recipe root")
        # All nested containers must retain their original private boundary.
        for parent in (target.parent, *target.parent.parents):
            private_directory(parent)
            if parent == recorded:
                break
    after = target.lstat()
    if (not stat.S_ISDIR(after.st_mode)
            or (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino)):
        raise Rejected("Recipe target changed during validation")
    marker["_checked_directory"] = (info.st_dev, info.st_ino)
    return target, marker


@contextmanager
def recipe_lock(target: Path, marker: dict):
    with parent_fd(target) as parent:
        directory = os.open(target.name, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(directory)
        if (info.st_dev, info.st_ino) != marker["_checked_directory"]:
            raise Rejected("Recipe target changed before locking")
        fd = os.open("run.lock", os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory)
        with os.fdopen(fd, "a") as lock:
            info = os.fstat(lock.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise Rejected("Untrusted recipe lock")
            yield lock, directory
    finally:
        os.close(directory)


class Copier:
    def __init__(self, target: Path):
        self.target = target
        self.mapping: dict[Path, Path] = {}
        self.sources: dict[str, dict] = {}
        self.databases: set[Path] = set()
        self.copied: set[Path] = set()
        self.excluded: list[str] = []
        self.content_roots: set[Path] = set()
        self.links: dict[str, dict] = {}
        self.directories: dict[str, dict] = {}

    @staticmethod
    def excluded_name(name: str) -> bool:
        return name.lower() in SESSION_PARTS or name.lower().startswith(("cookies.", "session.", "storage_state."))

    @staticmethod
    def directory_state(path: Path) -> dict:
        with parent_fd(path) as parent:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise Rejected("Source directory changed")
        return {"device": info.st_dev, "inode": info.st_ino, "mode": info.st_mode,
                "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}

    @staticmethod
    def directory_entries(path: Path) -> list[str]:
        with parent_fd(path) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=parent)
        try:
            with os.scandir(fd) as entries:
                return sorted(entry.name for entry in entries)
        finally:
            os.close(fd)

    @staticmethod
    def link_metadata(path: Path) -> dict:
        with parent_fd(path) as parent:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISLNK(info.st_mode):
                raise Rejected("Source symbolic link changed")
        return {"device": info.st_dev, "inode": info.st_ino,
                "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}

    @staticmethod
    def link_state(path: Path) -> dict:
        before = Copier.link_metadata(path)
        with parent_fd(path) as parent:
            value = readlink_noatime(parent, path.name)
        if Copier.link_metadata(path) != before:
            raise Rejected("Source symbolic link changed")
        return {"target": value, **before}

    def resolve_source(self, source: Path) -> Path:
        """Resolve only links between registered source roots; never probe an external target.

        Copies are materialized as independent regular files/directories at
        the alias's mapped destination, not as symlinks back to production.
        """
        source = source.absolute()
        if ".." in source.parts:
            raise Rejected("Source traversal refused")
        current = source
        seen = set()
        while True:
            prefix = Path("/")
            for index, component in enumerate(current.parts[1:], 1):
                prefix = prefix / component
                try:
                    with parent_fd(prefix) as parent:
                        info = os.stat(prefix.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    if seen:
                        raise Rejected(f"Broken source symbolic link: {source}") from None
                    return current
                if not stat.S_ISLNK(info.st_mode):
                    continue
                if self.mapped(prefix) is None:
                    raise Rejected(f"Symbolic link outside approved sources: {prefix}")
                if prefix in seen or len(seen) >= 40:
                    raise Rejected(f"Cyclic source symbolic link: {source}")
                seen.add(prefix)
                previous = self.links.get(str(prefix))
                if previous is not None:
                    if self.link_metadata(prefix) != {k: v for k, v in previous.items() if k != "target"}:
                        raise Rejected("Source symbolic link changed during copy")
                    state = previous
                else:
                    state = self.link_state(prefix)
                relative = Path(state["target"])
                if ".." in relative.parts:
                    raise Rejected(f"Symbolic link traversal refused: {prefix}")
                resolved = relative if relative.is_absolute() else prefix.parent / relative
                if self.mapped(resolved) is None:
                    raise Rejected(f"External source symbolic link refused: {prefix}")
                for root in self.mapping:
                    if resolved.is_relative_to(root) and any(
                            self.excluded_name(part) for part in resolved.relative_to(root).parts):
                        raise Rejected(f"Symbolic link targets excluded session: {prefix}")
                previous = self.links.setdefault(str(prefix), state)
                if previous != state:
                    raise Rejected("Source symbolic link changed during copy")
                current = resolved.joinpath(*current.parts[index + 1:])
                break
            else:
                return current

    def validate_links(self) -> None:
        for path, state in self.links.items():
            if self.link_metadata(Path(path)) != {k: v for k, v in state.items() if k != "target"}:
                raise Rejected("Source symbolic link changed during copy")

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

    def capture(self, source: Path, destination: Path, *, logical: Path | None = None,
                ancestors: frozenset[Path] = frozenset()) -> None:
        source = source.absolute()
        logical = logical or source
        destination = self.mapped(logical) or destination
        if not destination.is_relative_to(self.target) or ".." in destination.parts:
            raise Rejected("Copy destination outside recipe")
        if logical in self.copied:
            return
        is_content = any(logical.is_relative_to(root) for root in self.content_roots)
        if not is_content and self.excluded_name(source.name):
            self.excluded.append(str(source))
            return
        source = self.resolve_source(source)
        if not source.exists():
            return
        if source.is_dir():
            if source in ancestors:
                raise Rejected(f"Cyclic source directory link: {source}")
            before = self.directory_state(source)
            private_directory(destination)
            self.copied.add(logical)
            for name in self.directory_entries(source):
                # Sidecars are staged together with their main database below.
                if name.endswith(("-wal", "-shm", "-journal")):
                    continue
                self.capture(source / name, destination / name, logical=logical / name,
                             ancestors=ancestors | {source})
            if self.directory_state(source) != before:
                raise Rejected("Source directory changed during copy")
            self.directories[str(source)] = before
            self.validate_links()
            return
        with source_stream(source) as stream:
            header = stream.read(16)
        if header == b"SQLite format 3\x00":
            self.backup(source, destination)
        else:
            self.sources[str(source)] = fingerprint(source)
            private_directory(destination.parent)
            digest = copy_private_source(source, destination)
            if digest != self.sources[str(source)]["sha256"] or fingerprint(source) != self.sources[str(source)]:
                raise Rejected("Source changed during copy")
        self.copied.add(logical)
        self.validate_links()

    def backup(self, source: Path, destination: Path) -> None:
        """Stage a stable DB/WAL set without opening production via SQLite.

        A read-only SQLite connection can create/update SHM. Instead require
        two identical observations of the *entire* source set around staging,
        then run Backup API against the private set (including committed WAL).
        Busy writers cause bounded retry, never an inconsistent fallback.
        """
        no_links(destination)
        private_directory(destination.parent)
        stage = self.target / ".sqlite-stage"
        private_directory(stage)
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
            # SQLite opens only a fresh unpredictable name in private staging,
            # never the possibly replaced destination leaf. Install atomically
            # without overwriting any existing file/link, then remove the
            # temporary name so the independent copy has exactly one link.
            output = stage / (".backup-" + uuid.uuid4().hex + ".sqlite")
            with closing(sqlite3.connect((stage / source.name).as_uri() + "?mode=ro", uri=True)) as src:
                with closing(sqlite3.connect(output)) as dst:
                    src.backup(dst)
                    dst.commit()
                    if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        raise Rejected("SQLite backup integrity failure")
                    dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            with parent_fd(output) as staged_parent, parent_fd(destination) as parent:
                os.link(output.name, destination.name, src_dir_fd=staged_parent,
                        dst_dir_fd=parent, follow_symlinks=False)
                os.unlink(output.name, dir_fd=staged_parent)
            self.databases.add(destination)
            self.sources.update(before)
        finally:
            shutil.rmtree(stage)

    def rewrite(self, value, key: str = "", *, credential: bool = False):
        credential = credential or key.lower() in {"credentials", "credentials_json"}
        if isinstance(value, dict):
            return {k: self.rewrite(v, k, credential=credential) for k, v in value.items() if not SESSION_KEYS.search(k)}
        if isinstance(value, list):
            return [self.rewrite(v, key, credential=credential) for v in value]
        if not isinstance(value, str):
            return value
        if opaque_field(key) or (credential and not PATH_KEYS.search(key)):
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
                return json.dumps(self.rewrite(json.loads(value), credential=credential), ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        prefix = next((p for p in ("sqlite://", "file://", "file:") if value.startswith(p)), "")
        candidate = value[len(prefix):]
        if candidate.startswith(("/", "~/")) or (value and PATH_KEYS.search(key) and "://" not in value):
            path = local_path(candidate, links=True)
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
    with private_append(log) as stream:
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


def confined_values(value, target: Path, key: str = "", *, forbidden_roots=(), credential: bool = False) -> None:
    """Verify path-bearing values without copying, rewriting or executing."""
    credential = credential or key.lower() in {"credentials", "credentials_json"}
    if isinstance(value, dict):
        for name, item in value.items():
            confined_values(item, target, name, forbidden_roots=forbidden_roots, credential=credential)
    elif isinstance(value, list):
        for item in value:
            confined_values(item, target, key, forbidden_roots=forbidden_roots, credential=credential)
    elif isinstance(value, str):
        if opaque_field(key) or (credential and not PATH_KEYS.search(key)):
            return
        if key == "relative_path":
            if Path(value).is_absolute() or ".." in Path(value).parts:
                raise Rejected("Invalid catalog relative path")
            return
        if value[:1] in {"{", "["}:
            try:
                confined_values(json.loads(value), target, forbidden_roots=forbidden_roots, credential=credential)
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
    for source, expected in manifest.get("source_links", {}).items():
        if Copier.link_metadata(Path(source)) != {k: v for k, v in expected.items() if k != "target"}:
            raise Rejected("Source symbolic link changed since capture")
    for source, expected in manifest.get("source_directories", {}).items():
        if Copier.directory_state(Path(source)) != expected:
            raise Rejected("Source directory changed since capture")
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
        with parent_fd(path) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
                raise Rejected("Unsafe destination entry while sealing")
            os.fchmod(fd, 0o700 if stat.S_ISDIR(info.st_mode) or info.st_mode & 0o111 else 0o600)
        finally:
            os.close(fd)


def create(config_path: Path, target: Path | None, ref: str, *, no_start: bool,
           root: Path | None = None) -> Path:
    if sys.platform != "linux":
        raise Rejected("Recette currently requires Linux")
    os.umask(0o077)
    no_links(config_path)
    config_path = Path(os.path.abspath(config_path))
    original = load_config(config_path)
    config_source = config_path.absolute()
    data_source = local_path(str(Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "cifereca"))
    library_source = local_path(str(original.get("library", {}).get("directory") or "~/EBooks"))
    root = approved_root(root)
    for source in (config_source.parent, data_source, library_source):
        if root.is_relative_to(source) or source.is_relative_to(root):
            raise Rejected(f"Recipe root overlaps production source: {source}")
    target = target_path(target, root=root)
    root_id = root_identity(root, create=True)
    check_recipe_filesystem(root)
    with parent_fd(target) as parent:
        os.mkdir(target.name, mode=0o700, dir_fd=parent)  # Exclusive, no overwrite.
    private_directory(target)  # Check effective owner/mode on this filesystem.
    reservation = private_append(target / "run.lock")
    fcntl.flock(reservation, fcntl.LOCK_EX | fcntl.LOCK_NB)
    marker = {"format": FORMAT, "id": uuid.uuid4().hex, "target": str(target),
              "recipe_root": str(root), "root_id": root_id, "state": "preparing"}
    try:
        write_json(target / "recipe.json", marker)
        for name in ("home", "config/cifereca", "data/cifereca", "cache", "state", "runtime", "tmp"):
            (target / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        (target / "runtime/machine-id").write_text(marker["id"] + "\n")
        copier = Copier(target)
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
            before = copier.directory_state(data_source)
            for name in copier.directory_entries(data_source):
                entry = data_source / name
                if entry.name != "plugins":
                    copier.capture(entry, target / "data/cifereca" / entry.name)
            if copier.directory_state(data_source) != before:
                raise Rejected("Source directory changed during copy")
            copier.directories[str(data_source)] = before
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
                    "source_links": copier.links, "source_directories": copier.directories,
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
    with recipe_lock(target, marker) as (lock, _directory):
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
        with private_append(target / "runtime.log") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            write_json(target / "process.json", {"pid": process.pid, "starttime": process_identity(process.pid), "id": marker["id"]})
            try:
                return process.wait()
            finally:
                unlink_private(target / "process.json")
                unlink_private(target / "runtime/Xauthority")


def start(target: Path) -> None:
    # A detached supervisor holds run.lock until the entire sandbox exits.
    with private_append(target / "launcher.log") as log:
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
    with recipe_lock(target, marker) as (lock, directory):
        until = time.monotonic() + 1
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise Rejected("Supervisor still running; retry stop/destroy")
                time.sleep(0.05)
        try:
            os.unlink("process.json", dir_fd=directory)
        except FileNotFoundError:
            pass


def destroy(target: Path) -> None:
    target, marker = checked_target(target)
    stop(target)
    # Never follow symlinks in cleanup; shutil.rmtree uses fd-based traversal
    # on supported Linux, and checked_target forbids aliased root/ancestors.
    if not shutil.rmtree.avoids_symlink_attacks:
        raise Rejected("Safe directory removal unavailable")
    with recipe_lock(target, marker) as (lock, _directory):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected("A launch raced with destroy; retry")
        with parent_fd(target) as parent:
            actual = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISDIR(actual.st_mode)
                    or (actual.st_dev, actual.st_ino) != marker["_checked_directory"]):
                raise Rejected("Recipe target changed during destruction")
            shutil.rmtree(target.name, dir_fd=parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("verify", "destroy", "_run", "_stop"))
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--recipe-root", type=Path,
                        help="Approved private recipe root (default: ~/cifereca-recettes)")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--no-start", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action:
            if args.path is None:
                parser.error("action requires PATH")
            if args.recipe_root is not None:
                checked_target(args.path, root=args.recipe_root)
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
            print(create(config, args.target, args.ref, no_start=args.no_start, root=args.recipe_root))
        return 0
    except (Rejected, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f"Recette refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
