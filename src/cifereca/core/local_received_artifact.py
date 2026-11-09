"""Provider-neutral local artifact evidence for an already received file.

This is a file-boundary check, not an entitlement, delivery receipt, or
authorization to download. The caller still owns exact account/scope and CAS.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def received_book_file_within_root(path: Any, root_dir: Any) -> bool:
    """Accept only an existing regular book file below a real, symlink-free root.

    Neither identical bytes nor a path that merely shares the root prefix is
    proof of the same acquisition. Intermediate aliases must not escape scope.
    """
    try:
        if (not isinstance(path, Path) or not isinstance(root_dir, Path)
                or root_dir.is_symlink() or root_dir.parent.is_symlink()
                or not root_dir.is_dir()
                or path.is_symlink() or not path.is_file()):
            return False
        root = root_dir.resolve(strict=True)
        current = path.parent
        if not path.resolve(strict=True).is_relative_to(root):
            return False
        while current != root_dir:
            if current.is_symlink() or current == current.parent:
                return False
            current = current.parent
        return path.suffix.casefold() in {".epub", ".pdf", ".kepub"}
    except Exception:
        return False


__all__ = ["received_book_file_within_root"]
