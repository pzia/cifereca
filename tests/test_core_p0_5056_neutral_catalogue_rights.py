"""P0 #5056: untrusted provider rights never become public catalogue metadata."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.search_orchestration import _mirror_remote_results
from tests.test_core_p0_5056_catalogue_owned_attestation import (
    Storage, book, right,
)


@pytest.mark.parametrize("provider", [
    "numilog", "kobo", "paris", "fictional-a", "unknown-provider",
])
@pytest.mark.parametrize("owned_receipt", [False, True])
def test_provider_rights_are_never_mirrored_or_used_as_entitlement(
    provider, owned_receipt,
):
    notice = replace(book(provider), metadata={
        "rights": "signed/private-provider-secret",
        "subjects": ["Astronomie"],
        "description": "Description publique",
    })
    storage = Storage([right(provider)] if owned_receipt else [])
    service = NS(
        storage=storage,
        current_identity=NS(identity_id="alice"),
    )
    assert _mirror_remote_results(service, [notice], identity_id="alice") == 1
    row = storage.upserts[0]
    assert "rights" not in row["metadata"]
    assert "private-provider-secret" not in repr(row)
    assert row["metadata"]["subjects"] == ["Astronomie"]
    assert (row["local_state"] == "remote_owned") is owned_receipt


def test_rights_metadata_alone_does_not_make_an_unknown_provider_owned():
    notice = replace(book("fictional-b"), owned=False, metadata={
        "rights": {"signed_access": "private-token"},
        "subjects": ["Documentation"],
    })
    storage = Storage([])
    service = NS(storage=storage, current_identity=NS(identity_id="alice"))
    assert _mirror_remote_results(service, [notice], identity_id="alice") == 1
    assert storage.upserts[0]["local_state"] == "remote_catalog"
    assert "rights" not in storage.upserts[0]["metadata"]
