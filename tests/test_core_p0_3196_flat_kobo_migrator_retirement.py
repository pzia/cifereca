"""P0 #3196: the retired flat Kobo migrator cannot copy/unlink legacy files.

No LibraryService initialization, provider session, SQLite writes or bytes.
The private method signature stays available solely as an explicit veto.
"""
from __future__ import annotations

import inspect

import pytest

from cifereca.services import LibraryService


class ExplodingService:
    @property
    def storage(self):
        pytest.fail("retired migration must not consult legacy SQLite")

    def library_dir(self):
        pytest.fail("retired migration must not create the library folder")

    def library_identity_provider_dir(self, *_args):
        pytest.fail("retired migration must not choose a destination")


@pytest.mark.parametrize("identity", [None, object(), "historical"])
def test_flat_kobo_migrator_fails_before_any_source_or_filesystem_access(identity):
    with pytest.raises(RuntimeError, match="legacy_kobo_flat_migration_retired"):
        LibraryService._migrate_legacy_flat_kobo_files(ExplodingService(), identity)


def test_flat_kobo_migrator_preserves_facade_signature_without_authority():
    signature = inspect.signature(LibraryService._migrate_legacy_flat_kobo_files)
    assert tuple(signature.parameters) == ("self", "default_identity")
    assert signature.return_annotation in ("None", None)
