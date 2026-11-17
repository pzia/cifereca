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
    refs = IdentityBrokerImpl(core).accounts("profile-A")
    assert len(refs) == 2
    assert {(row.identity_id, row.credential_id) for row in refs} == {
        ("profile-A", "cred-A"), ("profile-A", "cred-B"),
    }
    assert {row.account_id for row in refs} == {"same-upstream-id"}
    assert all(row.scope_version == 2 and row.connected for row in refs)
    assert {row.credential_id for row in IdentityBrokerImpl(core).accounts("profile-B")} == {"cred-C"}
    with pytest.raises(TypeError):
        IdentityBrokerImpl(core).accounts()
    assert core.account_reads == 2


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

    for invalid in (None, "", " ", "\t", 0):
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



@pytest.mark.parametrize("identity", [None, "", " ", "\t", 0])
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



@pytest.mark.parametrize("identity", ["", " ", "\t", 0])
def test_sync_sources_reject_explicit_invalid_identity_before_account_read(identity):
    from cifereca.brokers import SynchronizationBrokerImpl

    core = FakeCore()
    broker = SynchronizationBrokerImpl(core)

    with pytest.raises(ValueError, match="identity_id_required"):
        broker.sources(identity)

    assert core.account_reads == 0
    assert core.acquisitions == []



@pytest.mark.parametrize("identity", [None, "", " ", "\t", 0])
def test_sync_batch_rejects_invalid_identity_with_structured_result(identity):
    from cifereca.brokers import SynchronizationBrokerImpl
    from cifereca.plugin_api import SyncRequest

    core = FakeCore()
    result = SynchronizationBrokerImpl(core).synchronize(
        SyncRequest(identity_id=identity),
    )

    assert result.ok is False
    assert result.changed == 0
    assert result.facts == {"reason": "identity_required"}
    assert core.account_reads == 0
    assert core.acquisitions == []



@pytest.mark.parametrize("identity", [None, "", " ", "\t", 0])
def test_scheduled_inventory_rejects_invalid_identity_before_account_read(identity):
    from cifereca.brokers import SynchronizationBrokerImpl

    core = FakeCore()
    result = SynchronizationBrokerImpl(core).synchronize_scheduled_inventory(
        identity, "periodic",
    )

    assert result.ok is False
    assert result.facts == {"reason": "identity_required"}
    assert core.account_reads == 0
    assert core.acquisitions == []


def test_broker_account_reads_are_pinned_across_gtk_navigation_switch():
    """Two profiles sharing an upstream ID never exchange their credentials."""
    core = FakeCore()
    core.current_identity = NS(identity_id="profile-B")
    identity = IdentityBrokerImpl(core)
    acquisition = AcquisitionBrokerImpl(core)

    original = identity.accounts("profile-A")
    eligible = acquisition.eligible_accounts(item(), identity_id="profile-A")
    core.current_identity = NS(identity_id="profile-A")
    assert identity.accounts("profile-A") == original
    assert acquisition.eligible_accounts(item(), identity_id="profile-A") == eligible
    assert {ref.credential_id for ref in original} == {"cred-A", "cred-B"}
    assert all(ref.identity_id == "profile-A" for ref in (*original, *eligible))
    assert {ref.credential_id for ref in identity.accounts("profile-B")} == {"cred-C"}

    # Fail before the first account lookup, independently of what GTK shows.
    account_reads = core.account_reads
    with pytest.raises(TypeError):
        identity.accounts()
    with pytest.raises(TypeError):
        acquisition.eligible_accounts(item())
    assert core.account_reads == account_reads
    assert core.acquisitions == []


def test_broker_explicit_scope_rejects_wrong_types_without_core_side_effect():
    core = FakeCore()
    identity = IdentityBrokerImpl(core)
    acquisition = AcquisitionBrokerImpl(core)
    for bad in (None, "", "\t", 0, False, [], object()):
        with pytest.raises(ValueError, match="identity_id_required"):
            identity.accounts(bad)
        with pytest.raises(ValueError, match="identity_id_required"):
            acquisition.eligible_accounts(item("cred-A"), identity_id=bad)
    assert core.account_reads == 0
    assert core.acquisitions == []


def test_public_broker_protocols_and_implementations_have_no_implicit_identity():
    """A future adapter cannot accidentally reintroduce optional UI scope."""
    import inspect

    from cifereca.plugin_api.brokers import AcquisitionBroker, IdentityBroker

    for cls, method in (
        (IdentityBroker, "accounts"),
        (IdentityBrokerImpl, "accounts"),
        (AcquisitionBroker, "eligible_accounts"),
        (AcquisitionBrokerImpl, "eligible_accounts"),
    ):
        parameter = inspect.signature(getattr(cls, method)).parameters["identity_id"]
        assert parameter.default is inspect.Parameter.empty
        assert parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )


def test_acquisition_observed_account_cannot_be_replaced_by_another_borrower():
    core = FakeCore()
    first, second = core.accounts_data[:2]
    first.unique_id = "borrower-A"
    second.unique_id = "borrower-B"
    second.credential_id = first.credential_id
    notice = item(first.credential_id)
    notice.source_account_id = "borrower-A"
    broker = AcquisitionBrokerImpl(core)
    assert [r.account_id for r in broker.eligible_accounts(
        notice, identity_id="profile-A",
    )] == ["borrower-A"]
    with pytest.raises(ValueError, match="not eligible"):
        broker.acquire(notice, identity_id="profile-A", account_id="borrower-B")
    assert core.acquisitions == []
    assert broker.acquire(
        notice, identity_id="profile-A", account_id="borrower-A",
    ) == first.credential_id
    assert notice.source_account_id == "borrower-A"


def test_acquisition_does_not_rewrite_shared_unbound_notice():
    core = FakeCore()
    core.accounts_data[0].unique_id = "borrower-A"
    core.accounts_data[1].unique_id = "borrower-B"
    notice = item()
    broker = AcquisitionBrokerImpl(core)
    assert broker.acquire(notice, identity_id="profile-A", account_id="borrower-A") == "cred-A"
    assert notice.source_credential == ""
    assert broker.acquire(notice, identity_id="profile-A", account_id="borrower-B") == "cred-B"
    assert notice.source_credential == ""
    assert core.acquisitions == ["cred-A", "cred-B"]


@pytest.mark.parametrize("observed", [False, 0, [], "bad\nuid", "x" * 1025])
def test_acquisition_invalid_observed_account_never_enumerates_accounts(observed):
    core = FakeCore()
    notice = item("cred-A")
    notice.source_account_id = observed
    broker = AcquisitionBrokerImpl(core)
    with pytest.raises(ValueError, match="observed account invalid"):
        broker.acquire(notice, identity_id="profile-A")
    assert core.account_reads == 0
    assert core.acquisitions == []


def test_acquisition_stale_observed_account_never_falls_back_to_unique_credential():
    core = FakeCore()
    notice = item("cred-A")
    notice.source_account_id = "previous-borrower"
    with pytest.raises(ValueError, match="no eligible connected account"):
        AcquisitionBrokerImpl(core).acquire(notice, identity_id="profile-A")
    assert core.acquisitions == []


@pytest.mark.parametrize("bad", [False, "", 0, [], "bad\x00uid"])
def test_acquisition_malformed_explicit_account_is_not_an_omitted_selection(bad):
    core = FakeCore()
    with pytest.raises(ValueError, match="account_id_invalid"):
        AcquisitionBrokerImpl(core).acquire(
            item("cred-A"), identity_id="profile-A", account_id=bad,
        )
    assert core.account_reads == 0
    assert core.acquisitions == []


def test_primary_display_id_cannot_replace_explicit_observed_upstream_uid():
    core = FakeCore()
    primary = core.accounts_data[0]
    primary.unique_id = ""
    primary.is_primary = True
    notice = item("cred-A")
    notice.source_account_id = ""
    broker = AcquisitionBrokerImpl(core)
    assert broker.acquire(
        notice, identity_id="profile-A", account_id="kobo:cred-A:primary",
    ) == "cred-A"
    notice.source_account_id = "kobo:cred-A:primary"
    with pytest.raises(ValueError, match="no eligible connected account"):
        broker.acquire(notice, identity_id="profile-A")
    assert core.acquisitions == ["cred-A"]
