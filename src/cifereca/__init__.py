from importlib.metadata import PackageNotFoundError, version as _distribution_version
from pathlib import Path

PROJECT_NAME = "cifereca"


def _source_tree_version(source_file: Path | None = None) -> str | None:
    """Return VERSION when running directly from a source/release tree."""
    source = (source_file or Path(__file__)).resolve()
    for parent in source.parents:
        candidate = parent / "VERSION"
        if not candidate.is_file():
            continue
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None


def _resolve_version(
    source_file: Path | None = None,
    metadata_version=_distribution_version,
) -> str:
    source_version = _source_tree_version(source_file)
    if source_version:
        return source_version
    try:
        value = str(metadata_version(PROJECT_NAME)).strip()
    except PackageNotFoundError:
        value = ""
    return value or "0+unknown"


__version__ = _resolve_version()


def version() -> str:
    return __version__
