"""P0 #5056: supplier descriptions must pass neutral bibliographic sanitization."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.search_orchestration import _mirror_remote_results
from tests.test_core_p0_5056_catalogue_owned_attestation import Storage, book


def mirrored_description(provider: str, description: object):
    storage = Storage([])
    service = NS(storage=storage, current_identity=NS(identity_id="alice"))
    notice = replace(
        book(provider), owned=False, description=description,
    )
    assert _mirror_remote_results(service, [notice], identity_id="alice") == 1
    return storage.upserts[0]["metadata"]["description"]


@pytest.mark.parametrize("provider", ["paris", "kobo", "numilog", "fictional-provider"])
def test_public_bibliographic_description_remains_visible(provider):
    assert mirrored_description(
        provider, "  Une  belle histoire de lecture.  ",
    ) == "Une belle histoire de lecture."


@pytest.mark.parametrize("private", [
    "https://private.example.invalid/account/token",
    "Accès avec bearer abcdefg-secret",
    "Demander à alice@example.org",
    "refresh_token=secret",
    "auth=secret",
    "A" * 4001,
    "bibliographie\x00malformée",
])
def test_sensitive_or_unbounded_descriptions_never_persist(private):
    assert mirrored_description("fictional-provider", private) is None


def test_empty_and_untyped_descriptions_do_not_become_public_text():
    for raw in ("", "  ", None, {"private": "value"}, ["nested"]):
        assert mirrored_description("unknown-provider", raw) is None


def test_description_filter_is_not_purchase_or_acquisition_permission():
    assert mirrored_description("fictional-provider", "Droit signé: bearer secret") is None
