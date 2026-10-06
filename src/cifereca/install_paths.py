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
