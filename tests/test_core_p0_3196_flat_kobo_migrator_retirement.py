"""P0 #3196: the flat Kobo 0.7 migrator has no remaining Core entrypoint.

Historical files are never copied or deleted through a legacy wrapper.
"""
from __future__ import annotations

import inspect

from cifereca.services import LibraryService


def test_retired_flat_kobo_migrator_has_no_core_facade():
    assert "_migrate_legacy_flat_kobo_files" not in vars(LibraryService)
    assert inspect.getattr_static(
        LibraryService, "library_identity_provider_dir",
    ) is not None
