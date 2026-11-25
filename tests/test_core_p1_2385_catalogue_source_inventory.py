"""#2385: explicit catalogue ≠ personal inventory ≠ acquisition.

No plugin activation, network, sync, download or fake public Account is needed.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.catalogue_source_inventory import (
    CatalogueSourceCapability, catalogue_source_capabilities,
)
from cifereca.providers.base import (
    ProviderCapabilities, ProviderDescriptor, CredentialField,
)
from cifereca.providers.registry import ProviderRegistry
from cifereca.services import LibraryService
from cifereca.plugin_api import (
    CatalogueSourceCapability as PublicCatalogueSourceCapability,
    CatalogueSourceInventoryBroker,
)


def _plugin(name, *, search=False, loans=False, personal=False, state="ready",
            authenticated=False):
    descriptor = ProviderDescriptor(
        id=name, display_name="Private reader name; must not be exported",
        description="Sensitive text", state=state,
        capabilities=ProviderCapabilities(
            search=search, loans=loans, personal_library=personal,
            direct_download=search,
        ),
        credential_fields=(CredentialField(
            key="login", label="private", secret=True,
        ),) if authenticated else (),
    )
    return NS(descriptor=descriptor)


def _rig():
    registry = ProviderRegistry([
        _plugin("gallica", search=True),
        _plugin("gutenberg", search=True),
        _plugin("ebooksgratuits", search=True),
        _plugin("bnf-catalogue", search=True),
        _plugin("kobo", search=True, personal=True, authenticated=True),
        _plugin("numilog", search=True, personal=True, authenticated=True),
        _plugin("numilog-catalog", search=True),  # disabled public plugin
        _plugin("paris", search=True, loans=True, authenticated=True),
        _plugin("rights-only", personal=True, authenticated=True),
        _plugin("technical", search=False, personal=False),
        _plugin("disabled", search=True, state="skeleton"),
    ])
    attempts = []
    public_sessions = [
        NS(provider_id=name, connected=True, credential_id="__public__")
        for name in ("gallica", "gutenberg", "ebooksgratuits", "bnf-catalogue")
    ]
    account_sessions = [
        NS(provider_id="kobo", credential_id="a-kobo", connected=True),
        NS(provider_id="numilog", credential_id="a-numilog", connected=True),
        NS(provider_id="paris", credential_id="a-paris", connected=False),
        NS(provider_id="rights-only", credential_id="b-only", connected=True),
    ]
    service = NS(
        registry=registry,
        current_identity=NS(identity_id="alice"),
        identities=[NS(identity_id="alice"), NS(identity_id="bob")],
        _session_key=LibraryService._session_key,
        _sessions={LibraryService._session_key(x.provider_id, x.credential_id): x
                   for x in public_sessions + account_sessions},
        _provider_enabled_for_identity=lambda name, identity: (
            attempts.append(("enabled", name, identity)) or
            name not in {"numilog-catalog", "disabled"} and
            not (identity == "bob" and name in {"kobo", "ebooksgratuits"})
        ),
        accounts_for_identity=lambda identity: (
            attempts.append(("accounts", identity)) or
            ([NS(provider_id=n, credential_id=f"a-{n}", identity_id="alice")
              for n in ("kobo", "numilog", "paris")] if identity == "alice" else
             [NS(provider_id="rights-only", credential_id="b-only",
                 identity_id="bob")])
        ),
    )
    service.catalogue_source_capabilities = (
        lambda identity_id=None: catalogue_source_capabilities(
            service, identity_id=identity_id,
        )
    )
    return service, attempts


def test_all_active_catalogues_present_even_without_public_accounts():
    service, calls = _rig()
    sources = catalogue_source_capabilities(service, identity_id="alice")
    names = [x.provider_id for x in sources]
    assert names == sorted(names)
    assert names == [
        "bnf-catalogue", "ebooksgratuits", "gallica", "gutenberg",
        "kobo", "numilog", "paris", "rights-only",
    ]
    gallica = next(x for x in sources if x.provider_id == "gallica")
    assert gallica.identity_scope == "public"
    assert gallica.catalog_search_supported is True
    assert gallica.catalog_search_available is True
    assert gallica.catalog_refresh == "unsupported"
    assert gallica.personal_inventory_supported is False
    assert gallica.acquisition == "separate_explicit"
    assert gallica.schema_version == 1
    # Personal inventory is genuinely distinct from a commercial catalogue.
    kobo = next(x for x in sources if x.provider_id == "kobo")
    assert kobo.identity_scope == "authenticated"
    assert kobo.catalog_search_available is True
    assert kobo.personal_inventory_supported is True
    assert kobo.personal_inventory_available is True
    assert kobo.catalog_refresh == "unsupported"
    assert "numilog-catalog" not in names
    assert "technical" not in names
    assert "disabled" not in names
    # Public catalogue discovery never requires a synthetic public Account.
    assert calls.count(("accounts", "alice")) == 1
    assert all(x == "enabled" for x, *_ in calls if isinstance(x, str)
               and x != "accounts")


def test_identity_scope_applies_to_public_plugin_and_private_account_connections():
    service, _ = _rig()
    rows = catalogue_source_capabilities(service, identity_id="bob")
    names = {row.provider_id for row in rows}
    assert "ebooksgratuits" not in names
    assert "kobo" not in names
    assert "numilog" in names
    numilog = next(row for row in rows if row.provider_id == "numilog")
    assert numilog.catalog_search_supported
    assert not numilog.catalog_search_available
    assert numilog.personal_inventory_supported
    assert not numilog.personal_inventory_available
    assert "rights-only" in names
    assert next(row for row in rows if row.provider_id == "rights-only").personal_inventory_available
    assert catalogue_source_capabilities(service, identity_id="stranger") == ()
    assert catalogue_source_capabilities(service, identity_id="alice:private") == ()


def test_disconnected_provider_is_search_capable_but_not_actually_online():
    service, _ = _rig()
    values = catalogue_source_capabilities(service, identity_id="alice")
    paris = next(value for value in values if value.provider_id == "paris")
    assert paris.catalog_search_supported is True
    assert paris.catalog_search_available is False
    assert paris.personal_inventory_supported is True
    assert paris.personal_inventory_available is False


def test_search_does_not_authorize_catalogue_snapshot_or_background_acquisition():
    service, calls = _rig()
    service.search = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("inventory must never call a provider search")
    )
    service.synchronize = service.acquire_direct_item = lambda *_a, **_k: (
        _ for _ in ()
    ).throw(AssertionError("inventory must never sync or acquire"))
    refs = CatalogBrokerImpl(service).catalogue_source_capabilities("alice")
    assert len(refs) == 8
    assert all(r.catalog_refresh == "unsupported" for r in refs)
    assert all(r.acquisition == "separate_explicit" for r in refs)
    assert all("credential" not in repr(r) for r in refs)
    with pytest.raises(FrozenInstanceError):
        refs[0].catalog_refresh = "snapshot"


def test_core_library_service_facade_additive_without_legacy_broker_change():
    service, _ = _rig()
    values = LibraryService.catalogue_source_capabilities(service, identity_id="alice")
    assert values == catalogue_source_capabilities(service, identity_id="alice")
    from cifereca.plugin_api.brokers import CatalogBroker
    assert not hasattr(CatalogBroker, "catalogue_source_capabilities")
    assert PublicCatalogueSourceCapability is CatalogueSourceCapability
    assert hasattr(CatalogBrokerImpl, "catalogue_source_capabilities")
    assert isinstance(CatalogBrokerImpl(service), CatalogueSourceInventoryBroker)


def test_uncanonical_registry_entry_and_forged_caps_are_not_published():
    service, _ = _rig()
    service.registry.register(_plugin("identity.token", search=True))
    rows = catalogue_source_capabilities(service, identity_id="alice")
    assert "identity.token" not in {r.provider_id for r in rows}
    assert "Private reader name" not in repr(rows)
    assert not any("http" in repr(row) or "login" in repr(row)
                   for row in rows)


def test_unknown_or_missing_identity_yields_no_private_projection():
    service, calls = _rig()
    assert catalogue_source_capabilities(service, identity_id="bob/private") == ()
    assert all(x[0] != "accounts" for x in calls)
    service.current_identity = None
    assert catalogue_source_capabilities(service) == ()


def test_account_session_of_other_identity_never_marks_private_source_available():
    service, _ = _rig()
    rows = catalogue_source_capabilities(service, identity_id="bob")
    assert next(r for r in rows if r.provider_id == "numilog").personal_inventory_available is False
    assert next(r for r in rows if r.provider_id == "numilog").catalog_search_available is False


def test_catalogue_inventory_is_json_serializable_without_sensitive_metadata():
    service, _ = _rig()
    rows = catalogue_source_capabilities(service, identity_id="alice")
    import json
    data = json.dumps([asdict(x) for x in rows])
    for fragment in ("a-kobo", "a-numilog", "b-only", "Private reader", "login",
                     "account_id", "credential_id", "token"):
        assert fragment not in data


def test_headless_inventory_requires_explicit_identity_before_any_source_probe():
    service, calls = _rig()

    class Headless:
        def __getattr__(self, name):
            if name == "current_identity":
                pytest.fail("headless catalogue inventory read GTK navigation")
            return getattr(service, name)

    worker = Headless()
    assert catalogue_source_capabilities(worker) == ()
    assert calls == []
    bob = catalogue_source_capabilities(worker, identity_id="bob")
    assert bob
    assert "kobo" not in {row.provider_id for row in bob}
    assert ("accounts", "bob") in calls


def test_catalogue_facade_preserves_optional_sync_abi_broker_denies_missing_owner():
    service, calls = _rig()
    alice = LibraryService.catalogue_source_capabilities(service)
    assert alice == catalogue_source_capabilities(service, identity_id="alice")
    service.current_identity = NS(identity_id="bob")
    bob = LibraryService.catalogue_source_capabilities(service)
    assert bob == catalogue_source_capabilities(service, identity_id="bob")
    assert "kobo" not in {row.provider_id for row in bob}
    assert LibraryService.catalogue_source_capabilities(service, identity_id="alice") == alice
    service.catalogue_source_capabilities = lambda **_kwargs: pytest.fail(
        "missing broker scope must not call facade"
    )
    assert CatalogBrokerImpl(service).catalogue_source_capabilities() == ()
    assert CatalogBrokerImpl(service).catalogue_source_capabilities("") == ()


def test_baen_activation_and_reconnection_do_not_grant_refresh_or_acquisition():
    service, _ = _rig()
    service.registry.register(_plugin("baen", search=True))
    enabled = service._provider_enabled_for_identity
    active = {"alice": False, "bob": True}
    service._provider_enabled_for_identity = lambda name, identity: (
        active[identity] if name == "baen" else enabled(name, identity)
    )

    def row(identity):
        return next((r for r in catalogue_source_capabilities(
            service, identity_id=identity,
        ) if r.provider_id == "baen"), None)

    assert row("alice") is None
    assert row("bob").catalog_search_supported
    assert not row("bob").catalog_search_available
    service._sessions[service._session_key("baen", "__public__")] = NS(
        provider_id="baen", credential_id="__public__", connected=True,
    )
    assert row("alice") is None
    assert row("bob").catalog_search_available
    active["alice"] = True
    assert row("alice").catalog_search_available
    service._sessions[service._session_key("baen", "__public__")].connected = False
    for identity in ("alice", "bob"):
        value = row(identity)
        assert value.catalog_search_supported
        assert not value.catalog_search_available
        assert value.catalog_refresh == "unsupported"
        assert not value.personal_inventory_available
        assert value.acquisition == "separate_explicit"
    service._sessions[service._session_key("baen", "__public__")].connected = True
    active["bob"] = False
    assert row("bob") is None
    assert row("alice").catalog_search_available


def test_private_reconnection_never_reuses_previous_identity_readiness():
    service, _ = _rig()
    session = next(s for s in service._sessions.values()
                   if s.provider_id == "numilog")
    def row(identity):
        return next(r for r in catalogue_source_capabilities(
            service, identity_id=identity,
        ) if r.provider_id == "numilog")
    assert row("alice").personal_inventory_available
    assert not row("bob").personal_inventory_available
    session.connected = False
    assert not row("alice").personal_inventory_available
    assert not row("alice").catalog_search_available
    session.connected = True
    assert row("alice").personal_inventory_available
    assert not row("bob").personal_inventory_available
    assert not row("bob").catalog_search_available
    assert row("alice").catalog_refresh == row("bob").catalog_refresh == "unsupported"


def test_public_catalogue_availability_requires_anonymous_session():
    service, _ = _rig()
    session = next(s for s in service._sessions.values()
                   if s.provider_id == "gallica")
    def row():
        return next(r for r in catalogue_source_capabilities(service, identity_id="alice")
                    if r.provider_id == "gallica")
    assert row().catalog_search_available
    session.credential_id = "other-account"
    assert row().catalog_search_supported
    assert not row().catalog_search_available
    session.credential_id = "__public__"
    assert row().catalog_search_available


@pytest.mark.parametrize("credential", [None, "", "__public__", []])
def test_invalid_private_credential_is_not_account_readiness(credential):
    service, _ = _rig()
    service.accounts_for_identity = lambda identity: [
        NS(provider_id="numilog", identity_id=identity, credential_id=credential),
    ]
    session = next(s for s in service._sessions.values()
                   if s.provider_id == "numilog")
    session.credential_id = credential
    row = next(r for r in catalogue_source_capabilities(service, identity_id="alice")
               if r.provider_id == "numilog")
    assert row.catalog_search_supported
    assert row.personal_inventory_supported
    assert not row.catalog_search_available
    assert not row.personal_inventory_available
    assert row.catalog_refresh == "unsupported"


@pytest.mark.parametrize("provider,credential", [
    ("gallica", "__public__"), ("numilog", "a-numilog"),
])
def test_connected_session_must_occupy_exact_registry_key(provider, credential):
    service, _ = _rig()
    key = service._session_key(provider, credential)
    session = service._sessions.pop(key)
    service._sessions["obsolete-registration"] = session

    def row():
        return next(r for r in catalogue_source_capabilities(service, identity_id="alice")
                    if r.provider_id == provider)

    assert row().catalog_search_supported
    assert not row().catalog_search_available
    assert not row().personal_inventory_available
    # A different connected session cannot lend its registration key either.
    service._sessions[key] = NS(provider_id=provider, credential_id="other", connected=True)
    assert not row().catalog_search_available
    service._sessions[key] = session
    assert row().catalog_search_available
    assert row().personal_inventory_available == (credential != "__public__")
    assert row().catalog_refresh == "unsupported"


def test_shared_real_account_readiness_requires_live_association_per_profile():
    service, _ = _rig()
    shared = "a-numilog"
    other = "b-numilog"
    credentials = {"alice": shared, "bob": shared}
    service.accounts_for_identity = lambda identity: [
        NS(provider_id="numilog", identity_id=identity,
           credential_id=credentials[identity]),
    ] if identity in credentials else []

    def row(identity):
        return next(r for r in catalogue_source_capabilities(
            service, identity_id=identity,
        ) if r.provider_id == "numilog")

    def readiness(identity, expected):
        value = row(identity)
        assert value.catalog_search_supported
        assert value.personal_inventory_supported
        assert value.catalog_search_available is expected
        assert value.personal_inventory_available is expected
        assert value.catalog_refresh == "unsupported"
        assert value.acquisition == "separate_explicit"

    # A single genuinely shared account registration is sufficient for both
    # profiles; no duplicate session or synthetic public Account is required.
    readiness("alice", True)
    readiness("bob", True)
    # The same provider with a different real account cannot borrow readiness.
    credentials["bob"] = other
    readiness("alice", True)
    readiness("bob", False)
    service._sessions[service._session_key("numilog", other)] = NS(
        provider_id="numilog", credential_id=other, connected=True,
    )
    readiness("bob", True)
    service._sessions[service._session_key("numilog", shared)].connected = False
    readiness("alice", False)
    readiness("bob", True)
    # Revoking only the association must hide readiness even while the
    # original connected session remains available to other legitimate users.
    credentials["alice"] = other
    readiness("alice", True)
    del credentials["bob"]
    readiness("bob", False)
    readiness("alice", True)
