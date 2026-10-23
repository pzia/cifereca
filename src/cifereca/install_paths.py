"""Resolve install archives in cwd, then the desktop's XDG Downloads directory."""
from pathlib import Path
import os
import shlex


class ArchiveNotFoundError(RuntimeError):
    pass


def downloads_dir() -> Path:
    home = Path.home()
    config = Path(os.environ.get('XDG_CONFIG_HOME') or home / '.config')
    try:
        lines = (config / 'user-dirs.dirs').read_text(encoding='utf-8').splitlines()
    except (OSError, UnicodeError):
        lines = []
    for line in lines:
        key, sep, value = line.strip().partition('=')
        if sep and key.strip() == 'XDG_DOWNLOAD_DIR':
            try:
                tokens = shlex.split(value, comments=True, posix=True)
            except ValueError:
                continue
            if len(tokens) != 1:
                continue
            raw = tokens[0].replace('${HOME}', str(home)).replace('$HOME', str(home))
            path = Path(raw).expanduser()
            # Never execute shell substitutions or guess other variable values.
            if path.is_absolute() and '$' not in raw and '`' not in raw:
                return path
    return home / 'Downloads'


def resolve_archive(value: str | Path) -> Path:
    raw = str(value)
    local = Path(raw).expanduser().resolve()
    tried = [local]
    if local.is_file():
        return local
    # Only bare filenames get fallback; explicit paths keep their meaning.
    if raw and '/' not in raw and '\\' not in raw:
        candidate = (downloads_dir() / raw).resolve()
        if candidate not in tried:
            tried.append(candidate)
        if candidate.is_file():
            return candidate
    raise ArchiveNotFoundError('Archive ZIP introuvable. Emplacements essayés : ' + '; '.join(map(str, tried)))


def prefer_downloads_for_save(chooser) -> bool:
    """Suggest desktop Downloads for a user-initiated GTK Save As dialog.

    Resolve the desktop's configured XDG_DOWNLOAD_DIR (including localized
    names), then ~/Downloads when it exists.  Never create a directory,
    change a selected destination or make an export mandatory: the native
    chooser remains free to select any other path.  GI is imported only on
    invocation so headless Core paths keep working without GTK.
    """
    setter = getattr(chooser, "set_current_folder", None)
    if not callable(setter):
        return False
    try:
        folder = downloads_dir()
        if not folder.is_dir():
            folder = Path.home() / "Downloads"
        if not folder.is_dir():
            return False
        from gi.repository import Gio
        return setter(Gio.File.new_for_path(str(folder))) is not False
    except Exception:
        # A broken XDG/GTK/portal setup must not disable Save As.
        return False
