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
