"""Exercise the installed plugin ABI through the headless Core composition root."""
from dataclasses import replace
from hashlib import sha256
import socket

import pytest

from cifereca.config import Config
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.runtime import CiferecaCore
from cifereca.core.source_convergence import SourceKey
from cifereca.locking import InterProcessLock
from cifereca.paths import acquisition_lock_file
from cifereca.plugin_api import (
    Account,
    DownloadCapability,
    LoanInventoryScope,
    LoanInventorySnapshot,
    ProviderCapabilities,
    ProviderDescriptor,
    ProviderRightsScope,
    ProviderSearchResponse,
    PublicSearchNotice,
    ResourceObservation,
    ResourceSnapshot,
    SearchResult,
    SourceReference,
    SyncRequest,
    SyncResult,
)
from cifereca.plugin_api.downloadable_resources import as_legacy_rights_snapshot


class SyntheticSource:
    exact_catalogue_download_offer_required = True
    connected = True
    capabilities = ProviderCapabilities(search=True, loans=True, direct_download=True)

    def __init__(self, account):
        self.provider_id = account.provider_id
        self.credential_id = account.credential_id
        self.descriptor = ProviderDescriptor(
            id=self.provider_id, display_name="Synthetic library", description="",
            state="ready", capabilities=self.capabilities,
        )
        self.accounts = [account]
        self.searches = []
        self.inventories = []
        self.foreign_inventory_scope = None

    def search_with_response_facets(self, query, scope="all", **options):
        self.searches.append((query, scope))
        notice = SearchResult(
            id="edition", record_id="edition", docbase="catalogue", medium="digital",
            title=query, author=None, type="Book", format="EPUB", publisher=None,
            published_at=None, description=None, thumbnail_url=None, friendly_url=None,
            scenario="catalogue", source_provider=self.provider_id,
            source_credential=self.credential_id, source_collection="catalogue",
        )
        private = replace(
            notice, owned=True, source_account_id=self.accounts[0].unique_id,
            download_url="https://synthetic.invalid/private-file",
        )
        return ProviderSearchResponse([private], public_notices=(PublicSearchNotice(notice),))

    def loan_inventory_snapshot(self, account):
        self.inventories.append(account)
        return LoanInventorySnapshot(
            scope=self.foreign_inventory_scope or LoanInventoryScope.for_account(account),
            loans=(), status="success",
        )

    def loans(self, account):
        pytest.fail("Typed inventory fell back to the legacy loan reader")

    def download_direct(self, item, target_dir):
        pytest.fail("A read or rejected acquisition reached the delivery method")

    def close(self):
        pass


@pytest.fixture
def core(tmp_path, monkeypatch):
    for name in ("CONFIG", "DATA", "CACHE", "STATE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))

    def deny_network(*args, **kwargs):
        pytest.fail("Unexpected network connection in a synthetic Core test")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    config = Config(tmp_path / "config.json")
    config.set("identities", [
        {"id": "local-reader", "label": "Local", "directory_name": "local-reader"},
        {"id": "foreign-reader", "label": "Foreign", "directory_name": "foreign-reader"},
    ])
    config.set("library.directory", str(tmp_path / "books"))
    config.save()
    with CiferecaCore.open(
        config_path=tmp_path / "config.json", database_path=tmp_path / "library.sqlite",
        no_plugins=True, activate_plugins=False, diagnostics_enabled=False,
    ) as instance:
        yield instance


def bind_source(core, identity_id, credential_id, account_id, source_type=SyntheticSource):
    service = core.library
    account = Account(
        identity_id=identity_id, provider_id="fixture-integration-source",
        credential_id=credential_id, unique_id=account_id,
    )
    connection_id = core.storage.provider_connection_put({
        "identity_id": identity_id, "provider_id": account.provider_id,
        "credential_id": credential_id, "label": "Synthetic connection",
    })
    core.storage.provider_account_put({
        "connection_id": connection_id, "identity_id": identity_id,
        "provider_id": account.provider_id, "credential_id": credential_id,
        "account_id": account_id, "label": "Synthetic account",
    })
    source = source_type(account)
    service.accounts.append(account)
    service._sessions[service._session_key(account.provider_id, credential_id)] = source
    return account, source


def test_foreign_search_notice_cannot_route_to_the_displayed_accounts(core):
    service = core.library
    viewer = service.current_identity.identity_id
    foreign, source = bind_source(core, "foreign-reader", "foreign-card", "same-upstream-id")
    catalog = core.brokers["catalog"]
    acquisition = core.brokers["acquisition"]

    results = catalog.search("Synthetic book", provider_id=foreign.provider_id, force=True)
    assert source.searches == [("Synthetic book", "catalog")]
    assert len(results) == 1
    (notice,) = results
    assert notice.title == "Synthetic book"
    assert notice.source_provider == foreign.provider_id
    assert notice.source_account_id is None and notice.download_url is None
    assert notice.owned is False and notice.downloadable is False
    assert acquisition.can_download(notice) is False

    bind_source(core, viewer, "local-card", foreign.unique_id)
    assert acquisition.eligible_accounts(notice, identity_id=viewer) == ()
    with pytest.raises(ValueError, match="account not eligible"):
        acquisition.acquire(notice, identity_id=viewer, account_id=foreign.unique_id)
    assert core.storage.entitlements() == []
    assert core.storage.acquisitions() == []
    assert core.storage.placements() == []


def test_explicit_sync_uses_exact_scope_with_a_different_displayed_identity(core):
    service = core.library
    viewer = service.current_identity.identity_id
    local, local_source = bind_source(core, viewer, "local-card", "same-upstream-id")
    foreign, source = bind_source(core, "foreign-reader", "foreign-card", local.unique_id)
    sync = core.brokers["sync"]
    source_id = service.sync_source_id(foreign)

    denied = sync.synchronize_source(source_id, SyncRequest(identity_id=viewer))
    assert not denied.ok and denied.facts["reason"] == "identity_mismatch"
    denied = sync.synchronize_source(source_id, SyncRequest(
        identity_id=foreign.identity_id, options={"credential_id": local.credential_id},
    ))
    assert not denied.ok and denied.facts["reason"] == "target_mismatch"
    assert source.inventories == local_source.inventories == []

    request = SyncRequest(identity_id=foreign.identity_id, options={
        "credential_id": foreign.credential_id, "account_id": foreign.unique_id,
    })
    result = sync.synchronize_source(source_id, request)
    assert isinstance(result, SyncResult) and result.ok, result
    assert source.inventories == [foreign] and local_source.inventories == []
    assert service.current_identity.identity_id == viewer

    source.foreign_inventory_scope = LoanInventoryScope.for_account(local)
    rejected = sync.synchronize_source(source_id, request)
    assert not rejected.ok
    assert source.inventories == [foreign, foreign] and local_source.inventories == []
    assert core.storage.entitlements() == []
    assert core.storage.acquisitions() == []
    assert core.storage.placements() == []


class SyntheticResourceSource(SyntheticSource):
    def __init__(self, account):
        super().__init__(account)
        scope = ProviderRightsScope(
            account.identity_id, account.provider_id, account.credential_id, account.unique_id,
        )
        reference = SourceReference(scope, "purchases", "edition", "resource", "purchase")
        self.snapshot = ResourceSnapshot(scope, reference.branch, (
            ResourceObservation(reference, DownloadCapability("available", "offer"),
                                "observed-resource", "2026-11-13T09:00:00+00:00"),
        ), status="partial", coverage="partial")
        self.resource_reads = []

    def downloadable_resource_snapshot(self, account):
        self.resource_reads.append(account)
        return self.snapshot


def test_sync_keeps_completed_origin_and_placement_scoped_to_their_owner(core):
    service = core.library
    viewer = service.current_identity.identity_id
    local, local_source = bind_source(
        core, viewer, "local-card", "same-upstream-id", SyntheticResourceSource,
    )
    foreign, foreign_source = bind_source(
        core, "foreign-reader", "foreign-card", local.unique_id, SyntheticResourceSource,
    )
    sync = core.brokers["sync"]

    # Seed a completed delivery at the real Core publication boundary.
    snapshot = foreign_source.snapshot
    reference = snapshot.resources[0].source
    source = SourceKey(reference.scope, reference.edition_id, reference.resource_id,
                       reference.kind, reference.branch)
    library_root = service.library_dir()
    path = library_root / foreign.identity_id / "book.pdf"
    path.parent.mkdir(parents=True)
    content = b"%PDF-1.7\nSynthetic completed delivery\n"
    path.write_bytes(content)
    digest = sha256(content).hexdigest()
    with InterProcessLock(acquisition_lock_file()):
        publication = publish_completed_acquisition(
            core.storage, library_root=library_root, path=path, source=source,
            snapshot=as_legacy_rights_snapshot(snapshot), final_sha256=digest,
            delivery_receipt_ref="completed-delivery",
        )
    assert len(publication.origin_link_ids) == 1

    for account in (local, foreign):
        result = sync.synchronize_source(service.sync_source_id(account), SyncRequest(
            identity_id=account.identity_id,
        ))
        assert isinstance(result, SyncResult)
        assert result.facts.get("reason") in (None, "source_partial"), result
    assert local_source.resource_reads == [local]
    assert foreign_source.resource_reads == [foreign]

    # Same provider, upstream account and edition cannot inherit the other
    # identity's proof of completed delivery.
    with core.storage._connect() as db:
        origins = db.execute(
            "SELECT s.identity_id,s.credential_id,s.account_id,s.evidence_ref,"
            "o.evidence_ref FROM source_references s LEFT JOIN origin_links o "
            "ON o.source_reference_id=s.source_reference_id ORDER BY s.identity_id"
        ).fetchall()
    assert origins == [
        (foreign.identity_id, foreign.credential_id, foreign.unique_id,
         "observed-resource", "completed-delivery"),
        (local.identity_id, local.credential_id, local.unique_id, "observed-resource", None),
    ]
    assert path.read_bytes() == content
    assert [row["artifact_id"] for row in core.storage.artifacts()] == [publication.artifact_id]
    assert [(row["placement_id"], row["identity_id"]) for row in core.storage.placements()] == [
        (publication.placement_id, foreign.identity_id),
    ]
    assert core.storage.acquisitions() == []
    assert core.storage.entitlements() == []
