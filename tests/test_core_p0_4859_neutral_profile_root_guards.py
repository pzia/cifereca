"""#4859: legacy W2 locator guards are provider-neutral and non-authorizing."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.local_profile_root_guards import (
    ProfileRootRejected, checked_profile_roots, path_within_roots,
)
from cifereca.core.kobo_local_reconciliation import (
    ReconciliationRejected, _roots, _within,
)


def service(tmp_path):
    library = tmp_path / "EBooks"
    library.mkdir()
    profiles = [
        NS(identity_id="alice", directory_name="Alice"),
        NS(identity_id="bob", directory_name="Bob"),
    ]
    return NS(
        identities=profiles,
        identity_by_id=lambda key: next(
            (x for x in profiles if x.identity_id == key), None
        ),
    ), library


def test_neutral_scope_is_not_source_right_or_file_proof(tmp_path):
    host, library = service(tmp_path)
    roots = checked_profile_roots(
        host, "alice", roots_for_identity=lambda *_: (
            library, library / "incoming",
        ),
    )
    assert roots == (library / "incoming", library / "Alice")
    assert path_within_roots(
        str(library / "Alice" / "book.epub"), roots,
    )
    assert not path_within_roots(
        str(library / "Bob" / "book.epub"), roots,
    )
    assert not path_within_roots(
        str(library / "Alice"), roots,
    )
    # No regular-file existence test means this is never A, B or ownership.
    assert not (library / "Alice" / "book.epub").exists()


@pytest.mark.parametrize("identity", ["missing", "", "bad\x00id", 12])
def test_exact_identity_refuses_before_provider_root_callback(tmp_path, identity):
    host, library = service(tmp_path)
    called = []
    with pytest.raises(ProfileRootRejected, match="unknown_identity"):
        checked_profile_roots(
            host, identity, roots_for_identity=lambda *_: called.append(True),
        )
    assert called == []


def test_colliding_profile_directory_refuses(tmp_path):
    host, library = service(tmp_path)
    host.identities[1].directory_name = "Alice"
    with pytest.raises(ProfileRootRejected, match="ambiguous_identity_directory"):
        checked_profile_roots(
            host, "alice", roots_for_identity=lambda *_: (
                library, library,
            ),
        )


def test_legacy_w2_alias_preserves_codes_without_catching_provider_exception(
    tmp_path, monkeypatch,
):
    from cifereca.core import kobo_local_reconciliation as adapter

    host, library = service(tmp_path)
    monkeypatch.setattr(
        adapter, "_kobo_preview_paths", lambda *_: (library, library),
    )
    assert _roots(host, "alice") == (library, library / "Alice")
    assert _within(str(library / "Alice" / "book.epub"), (library,))
    with pytest.raises(ReconciliationRejected, match="unknown_identity"):
        _roots(host, "missing")

    def provider_error(*_):
        raise ValueError("upstream-data-unavailable")

    monkeypatch.setattr(adapter, "_kobo_preview_paths", provider_error)
    with pytest.raises(ValueError, match="upstream-data-unavailable"):
        _roots(host, "alice")
