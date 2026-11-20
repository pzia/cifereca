"""Pure filename convention helpers; no format, layout or rights inference."""
from pathlib import PurePath


def split_filename_suffix(path: PurePath) -> tuple[str, str]:
    """Split the longest known Kobo suffix, preserving the source spelling.

    Other extensions retain pathlib's existing single-suffix semantics.
    A suffix is a name convention, not proof of container format, fixed
    layout, a compatible rendering engine, a provider, or authorization.
    """
    name = path.name
    for suffix in (".fxl.kepub.epub", ".kepub.epub"):
        if len(name) > len(suffix) and name.lower().endswith(suffix):
            return name[:-len(suffix)], name[-len(suffix):]
    return path.stem, path.suffix


def epub_filename_convention(path: PurePath) -> str | None:
    """One descriptive name convention, not actual EPUB layout or format."""
    stem, suffix = split_filename_suffix(path)
    if not stem:
        return None
    return {
        ".epub": "epub",
        ".kepub.epub": "kepub",
        ".fxl.kepub.epub": "fxl.kepub",
    }.get(suffix.casefold())
