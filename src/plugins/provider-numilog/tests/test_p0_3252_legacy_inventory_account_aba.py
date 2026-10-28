"""#3252: no legacy positive Numilog inventory after active Account A->A swap.

These are synthetic rows and broker identities, no authenticated HTTP, file
delivery, ADEPT, catalog enrichment or external access.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_rights_observation_snapshot import session, owned_row


@pytest.mark.parametrize("race", ["replace_list", "replace_row", "mutate_row"])
def test_legacy_library_refuses_account_rebind_during_owned_get(race):
    s, broker = session()

    def rows(**kwargs):
        if race == "replace_list":
            s._accounts = [replace(s._accounts[0])]
        elif race == "replace_row":
            s._accounts[0] = replace(s._accounts[0])
        else:
            s._accounts[0].credential_id = "other-credential"
        return [owned_row()]

    s._rows = rows
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.library()
        assert broker.owner == "identity-A"  # unchanged Core label alone is insufficient
    finally:
        s.close()


def test_legacy_library_refuses_account_replacement_during_projection():
    s, _ = session()
    s._rows = lambda **kwargs: [owned_row()]
    original = s._normalize

    def change_during_projection(row):
        normalized = original(row)
        s._accounts = [replace(s._accounts[0])]
        return normalized

    s._normalize = change_during_projection
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.library()
    finally:
        s.close()


def test_legacy_search_refuses_post_library_account_rebind():
    s, _ = session()
    first = s._normalize(owned_row())
    calls = []

    def library():
        calls.append("library")
        s._accounts = [replace(s._accounts[0])]
        return [first]

    s.library = library
    try:
        with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
            s.search("Livre synthétique")
        assert calls == ["library"]
    finally:
        s.close()


def test_stable_core_account_retains_library_and_search_legacy_contract():
    s, broker = session()
    reads = []
    s._rows = lambda **kwargs: (reads.append(kwargs) or [owned_row()])
    try:
        assert [item.title for item in s.library()] == ["Livre synthétique"]
        assert [item.title for item in s.search("livre")] == ["Livre synthétique"]
        assert reads == [{}, {}]
        assert broker.owner == "identity-A"
    finally:
        s.close()


def test_legacy_library_without_core_account_rows_retains_old_read_behavior():
    s, _ = session()
    s._accounts = []  # historical externally hosted plugin fixtures
    s._rows = lambda **kwargs: [owned_row()]
    try:
        assert len(s.library()) == 1
    finally:
        s.close()
