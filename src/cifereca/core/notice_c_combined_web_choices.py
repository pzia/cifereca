"""#6397: one C inventory, distinct public and authenticated choice authority.

Compose existing readers and resolvers. No new source writer, login, provider
request, URL reconstruction, rights or transfer. A public option never borrows
an authenticated session, even in a mixed menu.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .notice_c_cover_index import is_canonical_cover_document
from .notice_c_web_choices import (
    PublicWebChoice, PublicWebChoices, read_public_web_choices, resolve_public_web_choice,
)
from .notice_c_authenticated_web_choices import (
    AuthenticatedWebChoice, AuthenticatedWebChoices, _native_reader_scope,
    read_authenticated_web_choices_for_c, resolve_authenticated_web_choice,
)

_TOKEN = object()


@dataclass(frozen=True, slots=True, repr=False)
class CWebChoices:
    document: object
    epoch: int
    choices: tuple[PublicWebChoice | AuthenticatedWebChoice, ...]
    _public: PublicWebChoices = field(repr=False)
    _authenticated: AuthenticatedWebChoices | None = field(repr=False)
    _scope: object = field(repr=False)
    _token: object = field(repr=False)


def read_c_web_choices(service, document, *, reader_scope, expected_epoch,
                       required_source=None):
    """Worker-only 0/1/N inventory of public and individually bound links."""
    if (not is_canonical_cover_document(document)
            or type(expected_epoch) is not int or not 1 <= expected_epoch < 2**63
            or not _native_reader_scope(reader_scope, service)
            or not reader_scope.current(service)):
        return None
    try:
        public = read_public_web_choices(service.storage, document)
        if type(public) is not PublicWebChoices or public.epoch != expected_epoch:
            return None
        private = read_authenticated_web_choices_for_c(
            service, document, reader_scope=reader_scope, required_source=required_source,
        )
        if required_source is not None and private is None:
            return None
        if private is not None and (
                type(private) is not AuthenticatedWebChoices
                or private.epoch != expected_epoch or private.document != document
                or private._scope is not reader_scope):
            return None
        # The two independent, bounded readers must agree with the same C
        # revision. Refuse if the public inventory changes during the private read.
        if (read_public_web_choices(service.storage, document) != public
                or not reader_scope.current(service)):
            return None
        choices = public.choices + (private.choices if private is not None else ())
        if len(choices) > 512 or len({c.choice_id for c in choices}) != len(choices):
            return None
        return CWebChoices(document, expected_epoch, choices, public, private,
                           reader_scope, _TOKEN)
    except Exception:
        return None


def read_c_web_choices_for_search(service, row, *, reader_scope):
    """Use the same C inventory for an exact authenticated Search selection.

    The existing private reader establishes the required source and epoch;
    the combined reader then admits only that same C snapshot. No DTO URL,
    account transfer, or public relation is inferred from this selection.
    """
    from .notice_c_private_search_web import (
        _read_source_key, read_authenticated_web_for_search,
    )
    from .notice_cover_projection import notice_cover_document_ref

    if not _native_reader_scope(reader_scope, service):
        return None
    try:
        key = _read_source_key(row, reader_scope.identity_id)
        if key is None:
            return None
        private = read_authenticated_web_for_search(
            service, row, reader_scope=reader_scope,
        )
        if type(private) is not AuthenticatedWebChoices:
            return None
        menu = read_c_web_choices(
            service, private.document, reader_scope=reader_scope,
            expected_epoch=private.epoch, required_source=notice_cover_document_ref(key),
        )
        if (menu is None or menu._authenticated != private
                or _read_source_key(row, reader_scope.identity_id) != key
                or not reader_scope.current(service)):
            return None
        return menu
    except Exception:
        return None


def resolve_c_web_choice(service, menu, *, choice_id):
    """Resolve only the selected access mode through its original Core guard."""
    if (type(menu) is not CWebChoices or menu._token is not _TOKEN
            or not is_canonical_cover_document(menu.document)
            or type(menu.epoch) is not int or not 1 <= menu.epoch < 2**63
            or not _native_reader_scope(menu._scope, service)
            or not menu._scope.current(service)
            or type(menu.choices) is not tuple or not 1 <= len(menu.choices) <= 512
            or any(type(c) not in (PublicWebChoice, AuthenticatedWebChoice)
                   or type(c.choice_id) is not str for c in menu.choices)
            or type(choice_id) is not str):
        return None
    try:
        matches = [c for c in menu.choices if c.choice_id == choice_id]
        if len(matches) != 1:
            return None
        choice = matches[0]
        if type(choice) is PublicWebChoice:
            if (type(menu._public) is not PublicWebChoices
                    or menu._public.document != menu.document
                    or menu._public.epoch != menu.epoch
                    or not any(c is choice for c in menu._public.choices)):
                return None
            uri = resolve_public_web_choice(
                service.storage, menu.document, expected_epoch=menu.epoch, choice_id=choice_id,
            )
        else:
            private = menu._authenticated
            if (type(private) is not AuthenticatedWebChoices
                    or private.document != menu.document or private.epoch != menu.epoch
                    or private._scope is not menu._scope
                    or not any(c is choice for c in private.choices)):
                return None
            uri = resolve_authenticated_web_choice(service, private, choice_id=choice_id)
        return uri if menu._scope.current(service) else None
    except Exception:
        return None


__all__ = ["CWebChoices", "read_c_web_choices", "read_c_web_choices_for_search",
           "resolve_c_web_choice"]
