"""Validate a name or a selected direct child of the library root."""
from pathlib import Path
import re
from .directory_migration import DirectoryMigrationError
from ..library_files import safe_library_component


def directory_choice(value: str, root: Path) -> str:
    value = str(value or '').strip()
    path = Path(value).expanduser()
    if path.is_absolute():
        root = root.expanduser().resolve()
        # Compare lexical parents first, then reject symlinks at migration time.
        if path.parent.resolve() != root or path.name in {'.', '..'}:
            raise DirectoryMigrationError('Choisissez un sous-répertoire direct de la bibliothèque.')
        value = path.name
    if not re.fullmatch(r'[A-Za-z0-9._-]+', value) or safe_library_component(value, '') != value:
        raise DirectoryMigrationError('Nom de répertoire invalide : utilisez lettres, chiffres, point, tiret ou underscore.')
    return value
