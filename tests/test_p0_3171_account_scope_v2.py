"""#3171: credential-qualified public account scope and fail-closed routing."""
from dataclasses import FrozenInstanceError
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import AcquisitionBrokerImpl, IdentityBrokerImpl
from cifereca.plugin_api import AccountRef


class FakeCore:
    def __init__(self):
        self.accounts_data = [
            NS(identity_id="profile-A", provider_id="kobo", credential_id="cred-A",
               unique_id="same-upstream-id", display_name="A"),
            NS(identity_id="profile-A", provider_id="kobo", credential_id="cred-B",
               unique_id="same-upstream-id", display_name="B"),
            NS(identity_id="profile-B", provider_id="kobo", credential_id="cred-C",
               unique_id="same-upstream-id", display_name="C"),
        ]
        self._sessions = {
            self._session_key("kobo", credential): NS(
                connected=True, capabilities=NS(direct_download=True),
            )
            for credential in ("cred-A", "cred-B", "cred-C")
        }
        self.acquisitions = []
        self.account_reads = 0
        self.duplicate_during_selection = False

    @staticmethod
    def _session_key(provider, credential):
        return provider + ":" + credential

    def accounts_for_identity(self, identity_id=None):
        self.account_reads += 1
        if self.duplicate_during_selection and self.account_reads == 2:
            self.accounts_data.append(self.accounts_data[0])
        return tuple(
            row for row in self.accounts_data
            if identity_id is None or row.identity_id == identity_id
        )

    def acquire_direct_item(self, item):
        self.acquisitions.append(item.source_credential)
        return item.source_credential


def item(credential=""):
    return NS(source_provider="kobo", source_credential=credential)


def test_legacy_account_ref_defaults_preserved():
    ref = AccountRef("upstream", "profile-A", "kobo", "Account")
    assert ref.credential_id == ""
    assert ref.scope_version == 1
    with pytest.raises(FrozenInstanceError):
        ref.credential_id = "foreign"


def test_public_broker_v2_distinguishes_same_upstream_id_by_credential_and_identity():
    core = FakeCore()
    refs = IdentityBrokerImpl(core).accounts()
    assert len(refs) == 3
    assert {(row.identity_id, row.credential_id) for row in refs} == {
        ("profile-A", "cred-A"), ("profile-A", "cred-B"),
        ("profile-B", "cred-C"),
    }
    assert {row.account_id for row in refs} == {"same-upstream-id"}
    assert all(row.scope_version == 2 and row.connected for row in refs)
    assert {row.credential_id for row in IdentityBrokerImpl(core).accounts("profile-B")} == {"cred-C"}


def test_ambiguous_account_id_must_not_choose_first_credential_or_download():
    core = FakeCore()
    broker = AcquisitionBrokerImpl(core)
    assert len(broker.eligible_accounts(item(), identity_id="profile-A")) == 2
    with pytest.raises(ValueError, match="ambiguous eligible account"):
        broker.acquire(item(), identity_id="profile-A", account_id="same-upstream-id")
    assert core.acquisitions == []


def test_credential_filter_routes_only_selected_owner_and_does_not_cross_profile():
    core = FakeCore()
    broker = AcquisitionBrokerImpl(core)
    chosen = item("cred-B")
    refs = broker.eligible_accounts(chosen, identity_id="profile-A")
    assert [(row.identity_id, row.credential_id) for row in refs] == [
        ("profile-A", "cred-B"),
    ]
    assert broker.acquire(chosen, identity_id="profile-A", account_id="same-upstream-id") == "cred-B"
    assert core.acquisitions == ["cred-B"]
    with pytest.raises(ValueError, match="not eligible"):
        broker.acquire(item("cred-C"), identity_id="profile-A", account_id="same-upstream-id")
    assert core.acquisitions == ["cred-B"]


def test_binding_duplicated_between_selection_and_acquisition_fails_closed():
    core = FakeCore()
    core.accounts_data = core.accounts_data[:1]
    core.duplicate_during_selection = True
    with pytest.raises(ValueError, match="binding changed or ambiguous"):
        AcquisitionBrokerImpl(core).acquire(
            item(), identity_id="profile-A", account_id="same-upstream-id",
        )
    assert core.acquisitions == []

def test_immutable_item_with_missing_credential_cannot_bypass_selected_account():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class ImmutableItem:
        source_provider: str = "kobo"
        source_credential: str = ""

    core = FakeCore()
    core.accounts_data = core.accounts_data[:1]
    broker = AcquisitionBrokerImpl(core)
    with pytest.raises(ValueError, match="item credential binding unavailable"):
        broker.acquire(ImmutableItem(), identity_id="profile-A", account_id=None)
    assert core.acquisitions == []


def test_immutable_item_already_bound_to_exact_credential_can_route():
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class ImmutableItem:
        source_provider: str = "kobo"
        source_credential: str = "cred-B"

    core = FakeCore()
    assert AcquisitionBrokerImpl(core).acquire(
        ImmutableItem(), identity_id="profile-A", account_id="same-upstream-id",
    ) == "cred-B"
    assert core.acquisitions == ["cred-B"]



def test_identity_broker_explicit_empty_scope_never_uses_navigation_selection():
    from pytest import raises
    core = FakeCore()
    broker = IdentityBrokerImpl(core)

    for invalid in ("", " ", "\t"):
        with raises(ValueError, match="identity_id_required"):
            broker.accounts(invalid)

    # Existing explicit scoped reads remain stable regardless of UI selection.
    assert {row.credential_id for row in broker.accounts("profile-B")} == {"cred-C"}



@pytest.mark.parametrize("identity", [None, "", " ", "\t", 0])
def test_acquisition_requires_explicit_identity_before_any_account_read(identity):
    core = FakeCore()
    broker = AcquisitionBrokerImpl(core)

    with pytest.raises(ValueError, match="identity_id_required"):
        broker.acquire(item("cred-A"), identity_id=identity, account_id="same-upstream-id")

    assert core.account_reads == 0
    assert core.acquisitions == []


def test_acquisition_scoped_identity_ignores_other_profiles_with_same_account_id():
    core = FakeCore()
    broker = AcquisitionBrokerImpl(core)

    assert broker.acquire(
        item("cred-C"), identity_id="profile-B", account_id="same-upstream-id",
    ) == "cred-C"
    assert core.acquisitions == ["cred-C"]



@pytest.mark.parametrize("identity", ["", " ", "\t", 0])
def test_eligible_account_read_refuses_explicit_invalid_identity(identity):
    core = FakeCore()
    broker = AcquisitionBrokerImpl(core)

    with pytest.raises(ValueError, match="identity_id_required"):
        broker.eligible_accounts(item("cred-A"), identity_id=identity)

    assert core.account_reads == 0


@pytest.mark.parametrize("identity", [None, "", " ", 0])
def test_broker_acsm_import_refuses_navigation_identity_without_service_call(identity):
    class ImportService:
        def __init__(self):
            self.calls = []

        def import_acsm(self, path, **kwargs):
            self.calls.append((path, kwargs))
            return {"ok": True}

    service = ImportService()
    broker = AcquisitionBrokerImpl(service)

    with pytest.raises(ValueError, match="identity_id_required"):
        broker.import_acsm("/fake.acsm", identity_id=identity)

    assert service.calls == []


def test_broker_acsm_import_forwards_exact_identity():
    class ImportService:
        def import_acsm(self, path, **kwargs):
            return path, kwargs

    path, options = AcquisitionBrokerImpl(ImportService()).import_acsm(
        "/fake.acsm", identity_id="profile-B", provider_id="synthetic",
    )
    assert path == "/fake.acsm"
    assert options == {"identity_id": "profile-B", "provider_id": "synthetic"}
