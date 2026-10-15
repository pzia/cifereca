from __future__ import annotations
import os
import sys
from pathlib import Path

APP_NAME = "cifereca"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _home() -> Path:
    return Path.home()


def config_dir(*, create: bool = True) -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", _home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", _home() / ".config"))
    return _ensure(base / APP_NAME) if create else base / APP_NAME


def data_dir(*, create: bool = True) -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", _home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", _home() / ".local" / "share"))
    return _ensure(base / APP_NAME) if create else base / APP_NAME


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", _home() / "AppData" / "Local")) / "Cache"
    elif sys.platform == "darwin":
        base = _home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", _home() / ".cache"))
    return _ensure(base / APP_NAME)


def state_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", _home() / "AppData" / "Local")) / "State"
    elif sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", _home() / ".local" / "state"))
    return _ensure(base / APP_NAME)


def log_dir() -> Path:
    # Kept as a compatibility name; logs live under the application state root.
    return state_dir()


def config_file(*, create: bool = True) -> Path:
    return config_dir(create=create) / "config.json"


def database_file(*, create: bool = True) -> Path:
    return data_dir(create=create) / "library.sqlite3"


def log_file() -> Path:
    return log_dir() / "cifereca.log"


def downloads_dir() -> Path:
    return _ensure(data_dir() / "downloads")


def acquisition_lock_file() -> Path:
    return state_dir() / "local-acquisition.lock"
