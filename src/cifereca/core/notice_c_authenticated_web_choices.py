"""#6397: scoped stable authenticated web references, not browser authority.

First read-only tranche of the private C contract. A trusted Core producer
must have admitted the exact private source observation and strong C relation.
No public projection, provider call, locator resolution, login or acquisition.
A qualified eviction may retain a private reference under the same scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from sqlite3 import Error as SQLiteError
import json
import re
from urllib.parse import urlsplit

from ..catalogue_link import catalogue_notice_url
from ..models import Account
from ..providers.base import ProviderDescriptor
from .book_cover_projection import CoverDocumentScope
from .loan_action_scope import _inert_session_accounts, _inert_session_state
from .notice_c_cover_index import _db_key, _decode, is_canonical_cover_document
from .notice_cover_repository import _checked_source_state, _opaque_key
from .search_orchestration import SearchReadScope
from .notice_c_authenticated_web_retention import (
    FIELD, retained_private_web_field_txn, validated_private_web_field,
)
_TOKEN = object()
_EPOCH_LIMIT = 2**63


def _epoch(value):
    return type(value) is int and 1 <= value < _EPOCH_LIMIT


def _native_source_document(document):
    # A frozen scope may be mutated after issuance. Validate native scalar
    # members BEFORE invoking even the DTO's own __post_init__ (which uses
    # membership/equality), then recheck the model's kind/owner invariants.
    # A printable "loan" on a former catalogue scope is not a valid lease.
    if not (type(document) is CoverDocumentScope
            and all(type(getattr(document, name)) is str
                    and len(getattr(document, name)) <= 512
                    and getattr(document, name).isprintable() for name in (
                "kind", "source_key", "provider_id", "credential_id",
                "loan_id", "artifact_id", "placement_id",
            ))
            and (document.identity_id is None
                 or (type(document.identity_id) is str
                     and 0 < len(document.identity_id) <= 512
                     and document.identity_id.isprintable()))
            and (document.account_id is None
                 or (type(document.account_id) is str
                     and len(document.account_id) <= 512
                     and document.account_id.isprintable()))):
        return False
    try:
        document.__post_init__()
    except (TypeError, ValueError):
        return False
    return True


def _native_reader_scope(scope, service):
    # Reject altered Core-only reader leases before touching identity_by_id
    # (which can hash/compare its argument) or a source-context generation.
    return (type(scope) is SearchReadScope
            and scope.service is service
            and type(scope.identity_id) is str
            and 0 < len(scope.identity_id) <= 1024
            and not any(ord(char) in (0, 10, 13) for char in scope.identity_id)
            and type(scope.source_epoch) is int and scope.source_epoch >= 0)


@dataclass(frozen=True, slots=True, repr=False)
class _Binding:
    document: CoverDocumentScope
    account: Account
    session: object
    descriptor: ProviderDescriptor
    connection_epoch: int
    session_epoch: int | str | None

    def current(self, service, scope):
        try:
            doc = self.document
            coords = (scope.identity_id, doc.provider_id, doc.credential_id, doc.account_id)
            state = _inert_session_state(self.session)
            accounts = _inert_session_accounts(self.session)
            registered = service.accounts_for_identity(scope.identity_id)
            def exact(account):
                return type(account) is Account and (
                    account.identity_id, account.provider_id,
                    account.credential_id, account.unique_id,
                ) == coords
            return (
                scope.current(service)
                and doc.identity_id == scope.identity_id
                and service._sessions.get(service._session_key(
                    doc.provider_id, doc.credential_id)) is self.session
                and state is not None and accounts is not None
                and state.get("provider_id") == doc.provider_id
                and state.get("credential_id") == doc.credential_id
                and state.get("descriptor") is self.descriptor
                and state.get("connected") is True and state.get("_closed") is not True
                and type(state.get("_epoch")) is type(self.session_epoch)
                and state.get("_epoch") == self.session_epoch
                and exact(self.account)
                and sum(exact(a) for a in accounts) == 1
                and sum(a is self.account for a in accounts) == 1
                and type(registered) in (tuple, list) and len(registered) <= 512
                and sum(exact(a) for a in registered) == 1
                and sum(a is self.account for a in registered) == 1
                and service._provider_enabled_for_identity(
                    doc.provider_id, scope.identity_id) is True
                and service.storage.provider_scope_account_bound(
                    identity_id=scope.identity_id, provider_id=doc.provider_id,
                    credential_id=doc.credential_id, account_id=doc.account_id,
                ) is True
                and service.storage.provider_connection_epoch_current(
                    doc.provider_id, doc.credential_id, scope.identity_id,
                    self.connection_epoch) is True
                and scope.current(service)
            )
        except Exception:
            return False


def _capture(service, scope, doc):
    try:
        if (type(doc) is not CoverDocumentScope or doc.kind != "catalogue"
                or doc.identity_id != scope.identity_id
                or not doc.account_id or not doc.credential_id
                or doc.credential_id == "__public__" or _opaque_key(doc) is None):
            return None
        session = service._sessions.get(service._session_key(doc.provider_id, doc.credential_id))
        state = _inert_session_state(session)
        accounts = _inert_session_accounts(session)
        if state is None or accounts is None:
            return None
        descriptor = state.get("descriptor")
        if (type(descriptor) is not ProviderDescriptor or descriptor.id != doc.provider_id
                or descriptor.state != "ready" or descriptor.capabilities.authentication is not True):
            return None
        candidates = [a for a in accounts if type(a) is Account and (
            a.identity_id, a.provider_id, a.credential_id, a.unique_id,
        ) == (scope.identity_id, doc.provider_id, doc.credential_id, doc.account_id)]
        if len(candidates) != 1:
            return None
        connection = service.storage.provider_connection_epoch(
            doc.provider_id, doc.credential_id, scope.identity_id,
        )
        session_epoch = state.get("_epoch")
        # Installed providers use either counters or opaque reconnect
        # generations (e.g. a UUID hex). Never display or serialize this
        # transient value and never coerce bool/numeric/string aliases.
        valid_generation = (
            session_epoch is None
            or (type(session_epoch) is int and 0 <= session_epoch < _EPOCH_LIMIT)
            or (type(session_epoch) is str and 0 < len(session_epoch) <= 128
                and session_epoch.isprintable())
        )
        if not _epoch(connection) or not valid_generation:
            return None
        binding = _Binding(doc, candidates[0], session, descriptor, connection, session_epoch)
        return binding if binding.current(service, scope) else None
    except Exception:
        return None


def _stable_url(value, homepage):
    """Same installed origin, stable HTTPS page, never a signed locator."""
    if (type(value) is not str or not 0 < len(value) <= 512
            or type(homepage) is not str or len(homepage) > 512):
        return False
    try:
        parts, home = urlsplit(value), urlsplit(homepage)
        # First tranche admits only queryless, unescaped stable URLs.
        # Provider-controlled origin is not by itself proof of a specific book.
        return (
            parts.scheme == home.scheme == "https"
            and bool(parts.hostname) and parts.hostname == home.hostname
            and parts.port in (None, 443) and home.port in (None, 443)
            and parts.username is parts.password is home.username is home.password is None
            and not parts.query and not parts.fragment and "%" not in parts.path
            and "?" not in value and "#" not in value
            and catalogue_notice_url({"friendly_url": value}, base=homepage) == value
        )
    except (ValueError, UnicodeError):
        return False


def _source_links(source, binding):
    obs = source.observation
    key = obs.key
    if (obs.parser_id != "core.search.authenticated-web"
            or obs.parser_version != "1" or obs.status != "complete"
            or key.visibility != "private" or key.branch_id != "search.authenticated.catalogue"
            or source.document != binding.document):
        return ()
    return _field_links(obs.fields_copy().get(FIELD), binding)


def _field_links(raw, binding):
    field = validated_private_web_field(raw, binding.document.provider_id)
    if (field is None or field["connection_epoch"] != binding.connection_epoch
            or any(not _stable_url(link["url"], binding.descriptor.homepage)
                   for link in field["links"])):
        return ()
    return tuple(sorted(link["url"] for link in field["links"]))


@dataclass(frozen=True, slots=True)
class AuthenticatedWebChoice:
    choice_id: str
    provider_id: str
    label: str
    access_mode: str = "authenticated"


@dataclass(frozen=True, slots=True, repr=False)
class AuthenticatedWebChoices:
    document: CoverDocumentScope
    epoch: int
    choices: tuple[AuthenticatedWebChoice, ...]
    _scope: SearchReadScope = field(repr=False, compare=False)
    _bindings: tuple[_Binding, ...] = field(repr=False, compare=False)
    _witness: object = field(repr=False, compare=False)


def _read(service, document, bindings):
    """One canonical+source SQLite snapshot; private URLs never enter C."""
    storage = service.storage
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        row = db.execute("SELECT value FROM metadata WHERE key=?", (_db_key(document),)).fetchone()
        if row is None:
            return None, ()
        canonical = _decode(row[0], document)
        pairs = []
        members = {m.source_ref: m for m in canonical.sources}
        for binding in bindings:
            doc = binding.document
            member = members.get(doc.source_key)
            if (member is None or member.visibility != "private"
                    or member.provider_id != doc.provider_id):
                continue
            try:
                _, source, fence = _checked_source_state(db, doc, _opaque_key(doc))
                if source is None:
                    links = _field_links(retained_private_web_field_txn(
                        db, doc, member, fence,
                    ), binding)
                elif (fence is not None and source.epoch == member.source_epoch
                        and source.observation.source_revision == member.source_revision):
                    links = _source_links(source, binding)
                else:
                    continue
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
            for url in links:
                choice_id = sha256(json.dumps(
                    ["authenticated-web.v1", document.source_key, canonical.epoch,
                     doc.source_key, member.source_revision, url],
                    ensure_ascii=True, separators=(",", ":"),
                ).encode("ascii")).hexdigest()
                pairs.append((choice_id, doc.provider_id, url))
    # No URL, record or account identifier in labels. Ordinals distinguish
    # multiple references from the same provider.
    counts = {provider: sum(p == provider for _, p, _ in pairs)
              for _, provider, _ in pairs}
    ordinals = {}
    result = []
    for choice_id, provider, url in pairs:
        ordinals[provider] = ordinals.get(provider, 0) + 1
        suffix = (f" ({ordinals[provider]}/{counts[provider]})"
                  if counts[provider] > 1 else "")
        result.append((AuthenticatedWebChoice(
            choice_id, provider, provider + suffix + " · Connexion requise"), url))
    return canonical.epoch, tuple(result)


def read_authenticated_web_choices(service, document, *, reader_scope, source_documents):
    """Read only explicit account-scoped source locators from trusted Core."""
    if (not _native_source_document(document)
            or not is_canonical_cover_document(document)
            or not _native_reader_scope(reader_scope, service)
            or not reader_scope.current(service)
            or type(source_documents) is not tuple or not 1 <= len(source_documents) <= 32
            or any(not _native_source_document(d) for d in source_documents)
            or len(set(source_documents)) != len(source_documents)):
        return None
    bindings = tuple(b for doc in sorted(source_documents, key=lambda d: d.source_key)
                     if (b := _capture(service, reader_scope, doc)) is not None)
    if not bindings:
        return None
    try:
        epoch, pairs = _read(service, document, bindings)
    except (SQLiteError, ValueError, TypeError, KeyError, AttributeError):
        # A damaged private C or temporary local SQLite failure never
        # authorizes a web action and must not abort the surrounding Search.
        return None
    if (epoch is None or not reader_scope.current(service)
            or not all(b.current(service, reader_scope) for b in bindings)):
        return None
    return AuthenticatedWebChoices(document, epoch, tuple(c for c, _ in pairs),
                                   reader_scope, bindings, _TOKEN)


def resolve_authenticated_web_choice(service, menu, *, choice_id):
    """Explicit click revalidates the original scope, account, session and C."""
    # The GTK-held menu may have been copied/mutated after Core issued it.
    # Validate every native collection and member before calling .current()
    # or comparing a supplied choice id. Foreign equality or callbacks must
    # not be invoked from an invalid click receipt.
    if (type(menu) is not AuthenticatedWebChoices or menu._witness is not _TOKEN
            or not _native_source_document(menu.document)
            or not is_canonical_cover_document(menu.document)
            or not _epoch(menu.epoch) or type(choice_id) is not str
            or re.fullmatch(r"[0-9a-f]{64}", choice_id) is None
            or not _native_reader_scope(menu._scope, service)
            or type(menu._bindings) is not tuple
            or not 1 <= len(menu._bindings) <= 32
            or any(type(binding) is not _Binding
                   or not _native_source_document(binding.document)
                   for binding in menu._bindings)
            or type(menu.choices) is not tuple
            or not 1 <= len(menu.choices) <= 256
            or any(type(choice) is not AuthenticatedWebChoice
                   or type(choice.choice_id) is not str
                   or re.fullmatch(r"[0-9a-f]{64}", choice.choice_id) is None
                   or type(choice.provider_id) is not str
                   or type(choice.label) is not str
                   or type(choice.access_mode) is not str
                   or choice.access_mode != "authenticated"
                   for choice in menu.choices)
            or not menu._scope.current(service)
            or not all(b.current(service, menu._scope) for b in menu._bindings)
            or sum(c.choice_id == choice_id for c in menu.choices) != 1):
        return None
    try:
        epoch, pairs = _read(service, menu.document, menu._bindings)
    except (SQLiteError, ValueError, TypeError, KeyError, AttributeError):
        # The click resolves only this already-attested C. On a broken
        # snapshot, refuse navigation instead of using the old URL.
        return None
    if (epoch != menu.epoch or tuple(c for c, _ in pairs) != menu.choices
            or not menu._scope.current(service)
            or not all(b.current(service, menu._scope) for b in menu._bindings)):
        return None
    matches = [url for c, url in pairs if c.choice_id == choice_id]
    return matches[0] if len(matches) == 1 else None


__all__ = ["AuthenticatedWebChoice", "AuthenticatedWebChoices",
           "read_authenticated_web_choices", "resolve_authenticated_web_choice"]
