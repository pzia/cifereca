"""#7092 exact, worker-only contextual review projected through a public port.

Private foundation, not registered until the contextual action cutover. The
existing Core operator is the sole owner of byte verification and C lookup.
An activation-scoped lease will additionally guard entry and result delivery.
"""
from secrets import token_hex
from threading import Lock

from ..plugin_api.local_origin_detail import (
    LocalOriginDetailMatch, LocalOriginDetailPage, LocalOriginPublicChoice,
    LocalOriginPublicMatch, LocalOriginPublicReport,
    LocalOriginPrivateChoice, LocalOriginPrivateMatch, LocalOriginPrivateReport,
    LocalOriginRemoteLoanChoice, LocalOriginRemoteLoanMatch, LocalOriginRemoteLoanReport,
    LocalOriginDeclarationAdvice,
)
from .activation_scoped_broker import ActivationScopedReadFactory
from .local_origin_public_catalogue import (
    PublicCatalogueCandidate, available_local_public_catalogues,
    search_local_public_catalogue,
)
from .local_origin_private_catalogue import (
    PrivateCatalogueContinuation, PrivateDocumentaryMatch,
    available_known_private_catalogues, compare_known_private_catalogue,
)
from .local_origin_remote_loans import (
    RemoteLoanDocumentaryMatch, available_remote_loan_catalogues, compare_remote_loan_catalogue,
)
from .local_origin_documentary_suggestions import DocumentarySuggestionPage
from .local_origin_declaration_bridge import (
    preview_private_loan_declaration, preview_private_purchase_declaration,
)
from ..plugin_api.local_origin_detail import LocalOriginPurchaseDeclarationAdvice
from .local_origin_suggestion_operator import (
    admit_local_origin_suggestion, continue_local_origin_suggestion,
    run_local_origin_suggestion,
)


class LocalOriginDetailReadBrokerImpl:
    def __init__(self, service, *, activation_current=None):
        self._service = service
        self._activation_current = activation_current
        self._public = {}
        self._private = {}
        self._remote_loans = {}
        self._declaration_choices = {}
        self._declaration_window = None
        self._native_preview = None
        self._lock = Lock()
        self._generation = 0
        self._active = None
        self._revoked = False

    def _view_live(self, generation, view):
        # Pure lifetime check. The declaration authority calls its
        # cancellation predicate inside its own SQLite transaction.
        # request.current() must not be called on that connection then.
        with self._lock:
            if self._revoked or generation != self._generation:
                return False
        try:
            return ((self._activation_current is None or self._activation_current() is True)
                    and view() is True)
        except Exception:
            return False

    def _live(self, generation, view, request):
        return self._view_live(generation, view) and request.current() is True

    def _read(self, request, view, generation):
        try:
            if not self._live(generation, view, request):
                return None
            result = run_local_origin_suggestion(self._service, request)
            if (type(result) is not dict or result.get("status") != "partial"
                    or any(result.get(flag) is not False for flag in (
                        "association_authorized", "may_write_origin_or_right",
                        "provider_access_performed"))):
                return None
            known = result.get("suggestions")
            if (type(known) is not DocumentarySuggestionPage
                    or known.page != request.page or known.page_size != request.page_size
                    or len(known.suggestions) > request.page_size
                    or any(flag is not False for flag in (
                        known.coverage_complete, known.provider_access_performed,
                        known.may_write_origin_or_right))):
                return None
            following = continue_local_origin_suggestion(self._service, request, result)
            if not self._live(generation, view, request):
                return None
            # Only a verified first OPF page can offer an explicit anonymous
            # comparison. Discovery is inert and never dispatches a provider.
            choices = private_choices = remote_loans = ()
            if (request.page == 0 and self._activation_current is not None
                    and result["metadata_evidence"] == "verified_epub_opf_description"):
                choices = tuple((token_hex(32), choice)
                                for choice in available_local_public_catalogues(
                                    self._service, request,
                                    evidence=result.get("public_comparison_evidence")))
                private_choices = tuple((token_hex(32), choice)
                                        for choice in available_known_private_catalogues(
                                            self._service, request,
                                            evidence=result.get("public_comparison_evidence")))
                remote_loans = tuple((token_hex(32), choice)
                                     for choice in available_remote_loan_catalogues(
                                         self._service, request,
                                         evidence=result.get("public_comparison_evidence")))
            page = LocalOriginDetailPage(
                token_hex(32), known.page + 1,
                tuple(LocalOriginDetailMatch(
                    match.hit.provider_id, match.hit.record_id, match.hit.docbase,
                    match.hit.edition_id, match.hit.title, match.hit.author,
                    match.comparison, match.hit.stale, match.hit.observed_at,
                ) for match in known.suggestions),
                known.known_matches, known.has_more, following is not None,
                known.status, result["metadata_evidence"],
                public_choices=tuple(LocalOriginPublicChoice(token, choice.provider_id)
                                     for token, choice in choices),
                private_choices=tuple(LocalOriginPrivateChoice(
                    token, choice.scope.provider_id, choice.scope.credential_id,
                    choice.scope.account_id, choice.kind,
                ) for token, choice in private_choices),
                remote_loan_choices=tuple(LocalOriginRemoteLoanChoice(
                    token, choice.scope.provider_id, choice.scope.credential_id,
                    choice.scope.account_id,
                ) for token, choice in remote_loans),
            )
            with self._lock:
                if self._revoked or self._generation != generation:
                    return None
                self._active = (page.token, following, view)
                self._public = {token: (choice, view, generation) for token, choice in choices}
                self._private = {token: (choice, view, generation, None) for token, choice in private_choices}
                self._remote_loans = {token: (choice, view, generation) for token, choice in remote_loans}
                self._declaration_choices.clear()
                self._native_preview = None
            if not self._live(generation, view, request):
                self.cancel(token=page.token)
                return None
            return page
        except Exception:
            return None

    def preview(self, *, identity_id, placement_id, still_current,
                confirmed=False, page_size=20):
        if (confirmed is not True or not callable(still_current)
                or type(page_size) is not int or not 1 <= page_size <= 20):
            return None
        with self._lock:
            if self._revoked:
                return None
            self._generation += 1
            generation = self._generation
            self._active = None
            self._public.clear()
            self._private.clear()
            self._remote_loans.clear()
            self._declaration_choices.clear()
            self._native_preview = None
        try:
            if still_current() is not True:
                return None
            request = admit_local_origin_suggestion(
                self._service, identity_id=identity_id, placement_id=placement_id,
                confirmed=True, page_size=page_size,
            )
            if request is None:
                return None
            return self._read(request, still_current, generation)
        except Exception:
            return None

    def next_page(self, *, token, confirmed=False):
        if confirmed is not True or type(token) is not str:
            return None
        with self._lock:
            if self._revoked or self._active is None:
                return None
            expected, request, view = self._active
            if token != expected or request is None:
                return None
            self._active = None
            self._public.clear()
            self._private.clear()
            self._remote_loans.clear()
            self._declaration_choices.clear()
            self._native_preview = None
            self._generation += 1
            generation = self._generation
        return self._read(request, view, generation)

    def search_public(self, *, token, confirmed=False):
        if (confirmed is not True or type(token) is not str
                or self._activation_current is None):
            return None
        with self._lock:
            if self._revoked:
                return None
            selected = self._public.pop(token, None)
            if selected is None:
                return None
            choice, view, generation = selected
        request = choice.request

        def live():
            return self._live(generation, view, request)

        try:
            if not live():
                return None
            result = search_local_public_catalogue(
                self._service, request, provider_id=choice.provider_id,
                confirmed=True, choice=choice, still_current=live,
            )
            if (not live() or type(result) is not dict
                    or result.get("status") not in ("partial", "excluded", "scope_changed",
                                                   "unavailable", "response_unverified", "auth_required",
                                                   "access_denied", "rate_limited", "timeout", "cancelled")
                    or type(result.get("provider_access_performed")) is not bool
                    or any(result.get(flag) is not False for flag in (
                        "coverage_complete", "fresh_network_verified",
                        "association_authorized", "may_write_origin_or_right"))
                    or type(result.get("candidates")) is not tuple
                    or len(result["candidates"]) > 50):
                return None
            if any(type(row) is not PublicCatalogueCandidate or row.provider_id != choice.provider_id
                   for row in result["candidates"]):
                return None
            report = LocalOriginPublicReport(
                choice.provider_id, result["status"],
                tuple(LocalOriginPublicMatch(
                    row.provider_id, row.record_id, row.docbase, row.medium, row.title,
                    row.author, row.media_format, row.isbns, row.comparison,
                ) for row in result["candidates"]),
                result["provider_access_performed"],
            )
            return report if live() else None
        except Exception:
            return None

    def compare_private(self, *, token, confirmed=False):
        if (confirmed is not True or type(token) is not str
                or self._activation_current is None):
            return None
        with self._lock:
            if self._revoked:
                return None
            selected = self._private.pop(token, None)
            if selected is None:
                return None
            choice, view, generation, continuation = selected
            self._declaration_choices.clear()
            self._native_preview = None
            declaration_window = object()
            self._declaration_window = declaration_window
        request = choice.request

        def live():
            return self._live(generation, view, request)

        try:
            if not live():
                return None
            result = compare_known_private_catalogue(
                self._service, request, account=choice.account, kind=choice.kind,
                choice=choice, confirmed=True, still_current=live,
                continuation=continuation,
            )
            if (not live() or type(result) is not dict
                    or result.get("status") not in ("partial", "excluded", "scope_changed", "unavailable")
                    or type(result.get("window_truncated")) is not bool
                    or any(result.get(flag) is not False for flag in (
                        "coverage_complete", "fresh_network_verified", "provider_access_performed",
                        "association_authorized", "may_write_origin_or_right"))
                    or type(result.get("candidates")) is not tuple
                    or len(result["candidates"]) > 50):
                return None
            if any(type(row) is not PrivateDocumentaryMatch or row.source.scope != choice.scope
                   or row.source.kind != choice.kind for row in result["candidates"]):
                return None
            following = result.get("next_cursor")
            page = continuation.page if continuation is not None else 1
            next_token = None
            if following is not None:
                if (result["status"] != "partial" or result["window_truncated"] is not True
                        or type(following) is not PrivateCatalogueContinuation
                        or following.request is not request
                        or following.account is not choice.account
                        or following.scope != choice.scope or following.kind != choice.kind
                        or following.page != page + 1):
                    return None
                next_token = token_hex(32)
            candidate_tokens = tuple(
                token_hex(32) if choice.kind in ("loan", "purchase") and result["status"] == "partial"
                and row.comparison != "purchase_description_conflicting"
                else None for row in result["candidates"])
            report = LocalOriginPrivateReport(
                result["status"], choice.scope.provider_id, choice.scope.credential_id,
                choice.scope.account_id, choice.kind,
                tuple(LocalOriginPrivateMatch(
                    row.source.edition_id, row.title, row.author, row.comparison,
                    row.evidence, row.title_observed_at,
                    row.source.branch, row.source.resource_id, candidate_token,
                ) for row, candidate_token in zip(result["candidates"], candidate_tokens)), result["window_truncated"],
                page=page, next_token=next_token,
            )
            if not live():
                return None
            if following is not None:
                with self._lock:
                    if self._revoked or self._generation != generation:
                        return None
                    # Replace the consumed token with one continuation.
                    # Never expose the private cursor or expand the active
                    # per-catalogue choice budget.
                    if next_token in self._private or len(self._private) >= 16:
                        return None
                    self._private[next_token] = (choice, view, generation, following)
            if not live():
                with self._lock:
                    if next_token is not None:
                        self._private.pop(next_token, None)
                return None
            with self._lock:
                if (self._revoked or self._generation != generation
                        or self._declaration_window is not declaration_window):
                    return None
                self._declaration_choices = {
                    candidate_token: (choice, row, continuation, view, generation)
                    for row, candidate_token in zip(result["candidates"], candidate_tokens)
                    if candidate_token is not None
                }
            if not live():
                with self._lock:
                    if self._declaration_window is declaration_window:
                        self._declaration_choices.clear()
                        self._native_preview = None
                return None
            return report
        except Exception:
            return None

    def _preview_declaration(self, *, token, kind, confirmed=False):
        """One explicitly picked loan from the latest exact cached comparison.

        The existing declaration authority prepares the preview. The public
        result is inert; its private writer token never crosses this port.
        """
        if (confirmed is not True or type(token) is not str
                or self._activation_current is None):
            return None
        with self._lock:
            if self._revoked:
                return None
            selected = self._declaration_choices.get(token)
            if selected is None or selected[0].kind != kind:
                return None
            self._declaration_choices.clear()
            self._native_preview = None
            window = self._declaration_window
        choice, candidate, continuation, view, generation = selected

        def view_live():
            with self._lock:
                if self._declaration_window is not window:
                    return False
            return self._view_live(generation, view)

        def live():
            return view_live() and choice.request.current() is True

        try:
            if not live():
                return None
            # The bridge revalidates request/account/file before and after
            # the writer preview. Inside its transaction, pass only the
            # revocable view/activation guard; the writer owns SQL scope.
            operation = (preview_private_loan_declaration if kind == "loan"
                         else preview_private_purchase_declaration)
            preview = operation(
                self._service, choice=choice, candidate=candidate,
                continuation=continuation, confirmed=True, still_current=view_live)
            if preview is None or not live():
                return None
            source, scope = candidate.source, candidate.source.scope
            advice = (LocalOriginDeclarationAdvice if kind == "loan"
                      else LocalOriginPurchaseDeclarationAdvice)
            projected = advice(scope.provider_id, scope.credential_id, scope.account_id,
                               source.edition_id, source.resource_id)
            # Keep one exact private preview per activation. The DTO is
            # deliberately inert and its identity is not an authorization.
            with self._lock:
                if (self._revoked or self._generation != generation
                        or self._declaration_window is not window):
                    return None
                self._native_preview = (projected, preview, view_live)
            return projected
        except Exception:
            return None

    def _take_native_preview(self, advice, *, service, identity_id, placement_id):
        """Native host only: consume the exact worker result, never a copied DTO."""
        with self._lock:
            pending = self._native_preview
            if (pending is None or pending[0] is not advice or self._revoked
                    or self._service is not service):
                return None
            _, preview, current = pending
            request = preview.choice.request
            if (request.identity_id != identity_id or request.placement_id != placement_id):
                return None
            self._native_preview = None
        return (preview, current) if current() is True else None

    def preview_loan_declaration(self, *, token, confirmed=False):
        return self._preview_declaration(token=token, confirmed=confirmed, kind="loan")

    def preview_purchase_declaration(self, *, token, confirmed=False):
        return self._preview_declaration(token=token, confirmed=confirmed, kind="purchase")

    def compare_remote_loan(self, *, token, confirmed=False):
        if (confirmed is not True or type(token) is not str
                or self._activation_current is None):
            return None
        with self._lock:
            if self._revoked:
                return None
            selected = self._remote_loans.pop(token, None)
            if selected is None:
                return None
            choice, view, generation = selected
        request = choice.request

        def live():
            return self._live(generation, view, request)

        try:
            if not live():
                return None
            result = compare_remote_loan_catalogue(
                self._service, choice, confirmed=True, still_current=live,
            )
            if (not live() or type(result) is not dict
                    or result.get("status") not in ("partial", "excluded", "scope_changed", "unavailable")
                    or type(result.get("window_truncated")) is not bool
                    or type(result.get("provider_read_requested")) is not bool
                    or any(result.get(flag) is not False for flag in (
                        "coverage_complete", "fresh_network_verified",
                        "association_authorized", "may_write_origin_or_right",
                        "local_description_transmitted"))
                    or type(result.get("candidates")) is not tuple
                    or len(result["candidates"]) > 50
                    or (result.get("observed_at") is not None
                        and type(result["observed_at"]) is not str)
                    or any(type(row) is not RemoteLoanDocumentaryMatch
                           for row in result["candidates"])):
                return None
            report = LocalOriginRemoteLoanReport(
                result["status"], choice.scope.provider_id, choice.scope.credential_id,
                choice.scope.account_id,
                tuple(LocalOriginRemoteLoanMatch(
                    row.edition_id, row.loan_id, row.title, row.author, row.comparison,
                ) for row in result["candidates"]), result["window_truncated"],
                result["observed_at"], result["provider_read_requested"],
            )
            return report if live() else None
        except Exception:
            return None

    def cancel(self, *, token):
        with self._lock:
            if self._active is not None and type(token) is str and token == self._active[0]:
                self._active = None
                self._public.clear()
                self._private.clear()
                self._remote_loans.clear()
                self._declaration_choices.clear()
                self._native_preview = None
                self._generation += 1

    def revoke(self):
        """Private host-only retirement; no I/O or waiting for file hashing."""
        with self._lock:
            self._revoked = True
            self._active = None
            self._public.clear()
            self._private.clear()
            self._remote_loans.clear()
            self._declaration_choices.clear()
            self._native_preview = None
            self._generation += 1


class LocalOriginDetailReadFactory:
    """Inject the actual activation predicate into remote admission, not merely
    a post-call wrapper. The scoped resolver still owns lifetime and allowlist.
    """
    def __init__(self, service):
        self._service = service

    def for_activation(self, plugin_id, *, current):
        factory = ActivationScopedReadFactory(
            lambda: LocalOriginDetailReadBrokerImpl(self._service, activation_current=current),
            ("preview", "next_page", "search_public", "compare_private", "compare_remote_loan",
             "preview_loan_declaration", "preview_purchase_declaration", "cancel"),
        )
        return factory.for_activation(plugin_id, current=current)


def take_native_local_origin_declaration(manager, resolved, advice, *, service,
                                         identity_id, placement_id):
    """Resolve the existing activation's private reader, never a new registry.

    Only CoreUI receives this private handoff. A public broker lease has no
    method for taking a preview, executing it or obtaining its writer token.
    """
    if type(advice) not in (LocalOriginDeclarationAdvice, LocalOriginPurchaseDeclarationAdvice):
        return None
    try:
        from .activation_scoped_broker import ActivationBrokerResolver, _ActivationReadLease
        if (manager._registry_generation != resolved.registry_generation
                or manager.service(resolved.plugin_id) is not resolved.service):
            return None
        record = manager._records.get(resolved.plugin_id)
        if record is None or record.service is not resolved.service:
            return None
        resolver = record.activation_brokers
        if type(resolver) is not ActivationBrokerResolver or resolver.current() is not True:
            return None
        with resolver._lock:
            lease = resolver._scoped.get("library.origin.detail.read")
        if (type(lease) is not _ActivationReadLease
                or type(lease._reader) is not LocalOriginDetailReadBrokerImpl
                or resolver.current() is not True):
            return None
        return lease._reader._take_native_preview(
            advice, service=service, identity_id=identity_id, placement_id=placement_id)
    except Exception:
        return None
