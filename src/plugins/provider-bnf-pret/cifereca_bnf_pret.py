"""BnF reader provider using the same public provider contract as Paris."""
from __future__ import annotations
from pathlib import Path
import json
import logging
import traceback
from datetime import datetime, timezone
import threading
from uuid import uuid4
from cifereca.plugin_api import (
    Account, Loan, SearchResult, ItemDetail, CredentialField,
    ProviderCapabilities, ProviderSearchCapabilities, ProviderDescriptor,
    ProviderPlugin, ProviderSession, ProviderConfigurationError,
    ProviderCapabilityError, ProviderError, normalize_language_tag,
    LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage,
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError, ACTIVITY
from cifereca_bnf_pret_web import ProfileScope, parse_loans, WebParseError
from cifereca_bnf_pret_catalog import read_search, read_detail, MAX_SEARCH_PAGES, resource_url
from cifereca_bnf_pret_signed_action import signed_acsm_action
from cifereca_bnf_pret_download import save_private_loan_token, validate_loan_token

DESCRIPTOR = ProviderDescriptor(
    id='bnf-pret', display_name='BnF Prêt', state='ready',
    description='Catalogue, fiches et prêts du portail lecteur BnF, par HTTPX.',
    capabilities=ProviderCapabilities(authentication=True, multiple_credentials=True, loans=True,
                                      search=True, holdings=True),
    search_capabilities=ProviderSearchCapabilities(
        fields=('all',), scopes=('all', 'digital'), pagination='next-link',
        max_pages=MAX_SEARCH_PAGES,
        notes=('Pagination automatique via les seuls liens HTML observés, 5 pages maximum.',
               'Inventaire de catalogue non exhaustif ; aucun droit ou format déduit.'),
    ),
    credential_fields=(CredentialField('username', 'Identifiant BnF'),
                       CredentialField('password', 'Mot de passe', secret=True)),
    homepage='https://pret.bnf.fr',
    notes=('Inventaire partiel : aucune clôture de prêt par absence.',
           'SSO HTTP expérimental ; une étape interactive supplémentaire est refusée.'),
)


_PUBLIC_CODES = frozenset({
    'access_denied', 'auth_required', 'rate_limited', 'http_unavailable',
    'url_refused', 'mutation_refused', 'post_redirect_refused', 'redirect_invalid',
    'redirect_limit', 'unexpected_content', 'html_limit', 'invalid_html',
    'invalid_dom', 'dom_limit', 'item_limit', 'transport_unavailable',
    'operation_timeout', 'auth_interactive_required', 'unsupported_form',
    'authentication_failed', 'auth_step_limit', 'unrecognized_loans',
    'ambiguous_loans', 'scope_mismatch', 'invalid_activity_page', 'invalid_scope',
    'invalid_query', 'invalid_resource_page', 'unrecognized_search', 'partial_search',
    'unrecognized_detail', 'ambiguous_detail', 'next_page_unavailable',
    'search_redirect_mismatch', 'detail_redirect_mismatch', 'ambiguous_search',
    'search_page_limit', 'inventory_not_exhaustive',
    'loan_not_observed', 'invalid_loan_token', 'token_limit', 'session_closed',
    'download_redirect_refused', 'download_storage_failed',
})


# A failed *fresh inventory read* is not a failed ACSM GET. Preserve an
# explicit, finite phase-qualified reason without exposing response details.
# The unprefixed codes remain the token GET/validation reasons for v0.3.3
# compatibility. Do not prefix loan_not_observed: it already says the GET was
# blocked by a negative fresh observation.
_ACTIVITY_ERRORS = frozenset({
    'access_denied', 'auth_required', 'rate_limited', 'http_unavailable',
    'transport_unavailable', 'operation_timeout', 'url_refused',
    'redirect_invalid', 'redirect_limit', 'unexpected_content',
    'html_limit', 'invalid_dom', 'dom_limit', 'unrecognized_loans',
    'ambiguous_loans', 'invalid_activity_page', 'scope_mismatch',
})
_PUBLIC_CODES = _PUBLIC_CODES | frozenset('activity_' + code for code in _ACTIVITY_ERRORS) | {'activity_unavailable'}


def _public_code(error):
    code = str(error)
    return code if code in _PUBLIC_CODES else 'bnf_reader_failed'


class BnfProviderSession(ProviderSession):
    def __init__(self, *, client=None, diagnostics=None, identity_broker=None, **kwargs):
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.client = client or ReaderHTTPClient(diagnostics=diagnostics)
        self.diagnostics = diagnostics
        self.identity_broker = identity_broker
        self._lock = threading.RLock()
        self._epoch = uuid4().hex
        self._closed = False
        self.last_search_warnings = []
        # Only loans positively observed during this credential session are
        # eligible for an explicit, fresh-read token retrieval.
        self._observed_loans = {}

    def _report_failure(self, phase, error):
        code = _public_code(error)
        # The public error code is for callers, not a replacement for the
        # actual cause of this observed failure in the canonical raw journal.
        detail = {
            'provider': 'bnf-pret', 'phase': phase, 'code': code,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'error_traceback': ''.join(traceback.format_exception(error)),
        }
        try:
            if self.diagnostics is not None:
                self.diagnostics.add('API', 'BnF Prêt', 'error', detail=detail)
            else:
                payload = dict(
                    schema_version=1, category='API', operation='BnF Prêt',
                    message='BnF Prêt', status='error', detail=detail,
                )
                logging.getLogger('cifereca').error(
                    'DIAG_JSON %s', json.dumps(payload, ensure_ascii=False),
                )
        except Exception:
            pass  # Optional diagnostics never change the provider outcome.
        return code

    def _snapshot(self, html):
        scope = ProfileScope(self.credential_id, self.credential_id, self._epoch)
        return parse_loans(html, page_url=ACTIVITY, expected=scope, observed=scope)

    def connect(self):
        with self._lock:
            self.connected = False
            self._accounts = []
            self._observed_loans = {}
            username = str(self.credentials.get('username') or '').strip()
            password = str(self.credentials.get('password') or '')
            if self._closed or not username or not password:
                raise ProviderConfigurationError('bnf_credentials_required')
            epoch = self._epoch
            try:
                self._snapshot(self.client.login(username, password))
            except (ReaderHTTPError, WebParseError) as exc:
                self.client.clear()
                raise ProviderConfigurationError(self._report_failure('login', exc)) from None
            if self._closed or epoch != self._epoch:
                self.client.clear()
                raise ProviderConfigurationError('session_closed')
            # Stable local borrower key, isolated by provider + credential.
            self._accounts = [Account(display_name=self.label, unique_id=self.credential_id,
                                      provider_id=self.provider_id, credential_id=self.credential_id,
                                      provider_label=DESCRIPTOR.display_name)]
            self.connected = True
            return self.accounts

    def loans(self, account):
        with self._lock:
            if (not self.connected or self._closed or len(self._accounts) != 1
                    or account is not self._accounts[0]):
                raise ProviderConfigurationError('account_scope_mismatch')
            epoch = self._epoch
            try:
                snapshot = self._snapshot(self.client.activity())
            except (ReaderHTTPError, WebParseError) as exc:
                self.connected = False
                self.client.clear()
                raise ProviderConfigurationError(self._report_failure('loans', exc)) from None
            if self._closed or epoch != self._epoch:
                raise ProviderConfigurationError('session_closed')
            # A partial/empty page cannot close historical rights; nevertheless
            # it must not authorize token downloads from stale observations.
            self._observed_loans = {row.loan_id: row.resource_id for row in snapshot.loans}
            return [Loan(id=row.loan_id, provider='BNF_PRET', medium='digital',
                         record_id=row.resource_id, title=row.title or 'Prêt BnF',
                         author=', '.join(row.authors) or None,
                         thumbnail_url=row.thumbnail_url,
                         document_type='Prêt numérique',
                         source_provider=self.provider_id, source_credential=self.credential_id,
                         source_account_id=account.unique_id, identity_id=account.identity_id,
                         state='active', can_return=False, renewable=False,
                         raw={'inventory_exhaustive': False}) for row in snapshot.loans]

    def loan_inventory_snapshot(self, account):
        # Keep the loan GET, the active account and its typed envelope under
        # the SAME epoch/session lock. Otherwise connect() could replace
        # _accounts after loans() returns and before snapshot publication.
        with self._lock:
            return self._loan_inventory_snapshot_locked(account)

    def _loan_inventory_snapshot_locked(self, account):
        """Optional v1 inventory evidence: BnF HTML never attests completeness.

        Calls exactly one existing scoped loans() read. A successful GET/empty
        HTML section is still only a partial view of the active-loan corpus.
        This method never retrieves private loan tokens or starts ADEPT.
        """
        if (not self.connected or self._closed or len(self._accounts) != 1
                or account is not self._accounts[0]):
            raise ProviderConfigurationError('account_scope_mismatch')
        # The Account model is mutable even while the session lock holds:
        # another Core owner can rebind its identity during the HTML read.
        # Freeze the scope before touching the provider and check it again
        # before publishing positive loans under a typed envelope.
        scope = LoanInventoryScope.for_account(account)
        rows = self.loans(account)
        if not self.connected or self._closed:
            raise ProviderConfigurationError('session_closed')
        if (len(self._accounts) != 1 or account is not self._accounts[0]
                or LoanInventoryScope.for_account(account) != scope):
            raise ProviderConfigurationError('account_scope_mismatch')
        return LoanInventorySnapshot(
            scope=scope,
            loans=tuple(rows),
            status='partial',
            branches=(LoanBranchCoverage(
                upstream='bnf_html', medium='digital',
                completeness='partial', reason_code='inventory_not_exhaustive',
            ),),
            reason_code='inventory_not_exhaustive',
        )

    def rights_observation_snapshot(self, account):
        """One authenticated BnF activity read -> positive scoped loan claims.

        The HTML inventory is partial even when empty. In particular, neither
        missing loans nor a resource title grant any negative or purchase right.
        No ACSM/token call is made here; Core owns matching and acquisition.
        """
        # Same lock as connect/close and legacy loan GET. Freeze the mutable
        # borrower across both the typed inventory and rights projection.
        with self._lock:
            # A mutable Account.identity_id is not evidence that Core still
            # owns this credential's borrower. Require the same unique,
            # exact Core AccountRef binding used by the purchased-book
            # adapters; never infer a new right from an unlinked session.
            caller_scope = (
                account.unique_id, account.identity_id,
                account.provider_id, account.credential_id,
            ) if type(account) is Account else None

            def core_binding():
                broker = self.identity_broker
                if broker is None or caller_scope is None:
                    return None
                try:
                    refs = broker.accounts()
                    if type(refs) not in (tuple, list):
                        return None
                    owners = [
                        ref.identity_id if type(ref.identity_id) is str else ""
                        for ref in refs
                        if (getattr(ref, "provider_id", None) == self.provider_id
                            and getattr(ref, "account_id", None) == caller_scope[0])
                    ]
                    return (id(broker), tuple(sorted(owners)))
                except Exception:
                    return None

            owner_binding = core_binding()
            if (caller_scope is None or caller_scope[2:] !=
                    (self.provider_id, self.credential_id)
                    or type(caller_scope[1]) is not str or not caller_scope[1]
                    or owner_binding is None
                    or owner_binding[1] != (caller_scope[1],)):
                raise ProviderCapabilityError("bnf_rights_owner_unverified")
            inventory = self._loan_inventory_snapshot_locked(account)
            scope = inventory.scope
            if (not self.connected or self._closed or len(self._accounts) != 1
                    or account is not self._accounts[0]
                    or LoanInventoryScope.for_account(account) != scope
                    or (account.unique_id, account.identity_id,
                        account.provider_id, account.credential_id) != caller_scope
                    or core_binding() != owner_binding):
                raise ProviderConfigurationError('account_scope_mismatch')
            observed = datetime.now(timezone.utc).isoformat()
            rights = []
            for loan in inventory.loans:
                # Both opaque IDs come from the authenticated active-loan HTML:
                # never substitute a catalogue URL, title, ISBN or file hash.
                if (loan.medium != 'digital' or loan.state != 'active'
                        or type(loan.record_id) is not str or not loan.record_id
                        or type(loan.id) is not str or not loan.id):
                    continue
                rights.append(ProviderRightObservation(
                    edition_id=loan.record_id, right_id=loan.id,
                    kind='loan', state='active',
                    evidence_kind='provider_response',
                    evidence_ref='bnf_html.active_loans',
                    observed_at=observed, delivery='unknown',
                ))
            if (not self.connected or self._closed or len(self._accounts) != 1
                    or account is not self._accounts[0]
                    or LoanInventoryScope.for_account(account) != scope
                    or (account.unique_id, account.identity_id,
                        account.provider_id, account.credential_id) != caller_scope
                    or core_binding() != owner_binding):
                raise ProviderConfigurationError('account_scope_mismatch')
            return ProviderRightsSnapshot(
                scope=ProviderRightsScope(
                    scope.identity_id, scope.provider_id,
                    scope.credential_id, scope.account_id,
                ),
                rights=tuple(rights),
                status='partial', coverage='partial',
                pagination_complete=False, branch='bnf_html',
                observed_at=observed, snapshot_ref='bnf_html.active_loans',
            )

    def download_digital(self, loan: Loan, account: Account, target_dir: Path,
                         *, explicit_user_action: bool = False):
        """Explicit candidate ACSM fetch; the automatic Core path stays denied.

        The public digital_download capability remains false until the Core
        confirms selection, exact identity, staging and ADEPT consent end-to-end.
        Never infer a right from this GET or from the ACSM content alone.
        """
        if explicit_user_action is not True:
            # Generic acquisitions call the ProviderSession signature without
            # this opt-in. Never turn an observed Web loan into an implicit GET.
            raise ProviderCapabilityError('bnf_web_explicit_only')
        with self._lock:
            if (self._closed or not self.connected or len(self._accounts) != 1
                    or account is not self._accounts[0] or type(loan) is not Loan
                    or account.provider_id != self.provider_id
                    or account.credential_id != self.credential_id
                    or loan.source_provider != self.provider_id
                    or loan.source_credential != self.credential_id
                    or loan.source_account_id != account.unique_id
                    or loan.identity_id != account.identity_id
                    or loan.medium != 'digital' or loan.state != 'active'
                    or not loan.id or not loan.record_id
                    or self._observed_loans.get(loan.id) != loan.record_id):
                raise ProviderConfigurationError('loan_not_observed')
            epoch = self._epoch
            # Account and Loan are mutable even while this session lock is held:
            # the Core can rebind them during activity() or loan_token(). Pin
            # both scopes and refuse a token GET/publication after any change.
            account_scope = (account.provider_id, account.credential_id,
                             account.unique_id, account.identity_id)
            loan_scope = (loan.id, loan.record_id, loan.provider, loan.medium,
                          loan.state, loan.source_provider, loan.source_credential,
                          loan.source_account_id, loan.identity_id)
            loan_id, resource_id = loan.id, loan.record_id

            def scope_current():
                return (
                    (account.provider_id, account.credential_id,
                     account.unique_id, account.identity_id) == account_scope
                    and (loan.id, loan.record_id, loan.provider, loan.medium,
                         loan.state, loan.source_provider, loan.source_credential,
                         loan.source_account_id, loan.identity_id) == loan_scope
                    and self._observed_loans.get(loan_id) == resource_id
                )

            try:
                # No stale loan can trigger the GET: re-read the exact Web
                # account and demand the same loan/resource pair right now.
                try:
                    activity_html = self.client.activity()
                    fresh = self._snapshot(activity_html)
                except (ReaderHTTPError, WebParseError) as error:
                    # Do not conflate a failed fresh inventory read with an
                    # ACSM transfer: the token endpoint has NOT been called.
                    code = _public_code(error)
                    raise ReaderHTTPError(
                        'activity_' + code if code in _ACTIVITY_ERRORS
                        else 'activity_unavailable'
                    ) from None
                # close() revokes the epoch before acquiring _lock. In
                # particular, it may run while activity() is in flight.
                # Invalidate BEFORE the token GET, not only after it.
                if self._closed or epoch != self._epoch or not self.connected:
                    raise ReaderHTTPError('session_closed')
                if not scope_current():
                    raise ReaderHTTPError('loan_not_observed')
                matching = [row for row in fresh.loans
                            if row.loan_id == loan_id and row.resource_id == resource_id]
                if len(matching) != 1:
                    raise ReaderHTTPError('loan_not_observed')
                # Do not downgrade to unsigned GET when BnF's signed action
                # is absent or synthesized outside this HTML page.
                signed_url = signed_acsm_action(activity_html, loan_id, resource_id)
                if signed_url is None:
                    raise ReaderHTTPError('url_refused')
                if self._closed or epoch != self._epoch or not self.connected:
                    raise ReaderHTTPError('session_closed')
                if not scope_current():
                    raise ReaderHTTPError('loan_not_observed')
                token = self.client.loan_token(loan_id, signed_url)
                if self._closed or epoch != self._epoch:
                    raise ReaderHTTPError('session_closed')
                if not scope_current():
                    raise ReaderHTTPError('loan_not_observed')
                validate_loan_token(token)
                if self._closed or epoch != self._epoch:
                    raise ReaderHTTPError('session_closed')
                path = save_private_loan_token(token, target_dir)
                if self._closed or epoch != self._epoch:
                    path.unlink(missing_ok=True)
                    raise ReaderHTTPError('session_closed')
                if not scope_current():
                    path.unlink(missing_ok=True)
                    raise ReaderHTTPError('loan_not_observed')
                # URLs / opaque loan ids must not enter the generic Core
                # catalogue, diagnostics or acquisition hook.
                return path, ''
            except (ReaderHTTPError, WebParseError) as error:
                raise ProviderError(self._report_failure('download', error)) from None

    def _catalog_error(self, phase, error):
        code = self._report_failure(phase, error)
        failure = ProviderError(code)
        public_search_code = {
            'access_denied': 'search_access_denied',
            'auth_required': 'search_authentication_required',
            'transport_unavailable': 'search_temporarily_unavailable',
            'rate_limited': 'search_temporarily_unavailable',
            'operation_timeout': 'search_temporarily_unavailable',
        }.get(code)
        if public_search_code:
            failure.code = public_search_code
        return failure

    def search(self, query, scope='all', page=0, field='all', content_types=()):
        with self._lock:
            self.last_search_warnings = []
            if self._closed:
                raise ProviderConfigurationError('session_closed')
            # Unhashable scopes (lists/mappings) must be a capability
            # rejection, not an uncaught TypeError or a fake empty catalogue.
            if (type(scope) is not str or scope not in {'all', 'digital'}
                    or type(field) is not str or field != 'all'
                    or type(page) is not int or page != 0):
                raise ProviderCapabilityError('unsupported_search')
            # The Core may omit this optional filter. Once supplied, it must
            # be a finite collection of short, typed category labels; never
            # interpret a string as its iterable characters or try membership
            # on an integer/foreign mapping.
            if content_types is None:
                content_types = ()
            if (type(content_types) not in (tuple, list, set, frozenset)
                    or any(type(value) is not str or not 0 < len(value) <= 80
                           for value in content_types)
                    or (content_types and 'ebook' not in content_types)):
                raise ProviderCapabilityError('unsupported_content_type')
            epoch = self._epoch
            try:
                records, warnings = read_search(
                    self.client, query, is_current=lambda: not self._closed and epoch == self._epoch,
                )
            except (ReaderHTTPError, WebParseError) as error:
                if self._closed or epoch != self._epoch:
                    raise ProviderConfigurationError('session_closed') from None
                raise self._catalog_error('search', error) from None
            if self._closed or epoch != self._epoch:
                raise ProviderConfigurationError('session_closed')
            self.last_search_warnings = [
                _public_code(ReaderHTTPError(code)) for code in warnings
            ]
            return [self._search_result(record) for record in records]

    def _search_result(self, record):
        language = normalize_language_tag(record.language) if record.language else None
        return SearchResult(
            id='bnf-pret:' + record.resource_id, record_id=record.resource_id,
            docbase='BNF_PRET', medium='digital', title=record.title,
            author=', '.join(record.authors) or None, type='Livre numérique',
            format=None, publisher=record.publisher, published_at=None,
            description=record.description, thumbnail_url=None,
            friendly_url=resource_url(record.resource_id), scenario='BNF_PRET',
            source_provider=self.provider_id, source_credential=self.credential_id,
            content_type='ebook', owned=None, downloadable=False,
            metadata={'isbn': record.isbn, 'languages': [language] if language else [],
                      'availability': record.availability, 'inventory_exhaustive': False},
        )

    def holdings(self, item, search_terms=''):
        with self._lock:
            if self._closed:
                raise ProviderConfigurationError('session_closed')
            if (type(item) is not SearchResult or item.source_provider != self.provider_id
                    or item.source_credential != self.credential_id or item.docbase != 'BNF_PRET'):
                raise ProviderConfigurationError('catalog_scope_mismatch')
            epoch = self._epoch
            try:
                record = read_detail(self.client, item.record_id)
            except (ReaderHTTPError, WebParseError) as error:
                raise self._catalog_error('detail', error) from None
            if self._closed or epoch != self._epoch:
                raise ProviderConfigurationError('session_closed')
            unavailable = record.availability == 'unavailable'
            return ItemDetail(
                record_id=record.resource_id, docbase='BNF_PRET', title=record.title,
                author=', '.join(record.authors) or None, type='Livre numérique',
                year=None, thumbnail_url=None, availability_status=record.availability,
                availability_label=('Indisponibilité affichée par le portail' if unavailable
                                    else 'Disponibilité non vérifiée'), holdings=[],
                source_provider=self.provider_id, source_credential=self.credential_id,
            )

    def close(self):
        # Invalidate in-flight work before waiting for the bounded HTTP operation.
        self._closed = True
        self._epoch = uuid4().hex
        self.connected = False
        self._accounts = []
        self._observed_loans = {}
        with self._lock:
            self.client.clear()
            self.client.close()


class BnfProviderPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def activate(self, context):
        self._identity_broker = context.broker("identities")
        return self

    def deactivate(self):
        self._identity_broker = None

    def create_session(self, *, credential_id, label, credentials, options, config, storage, diagnostics):
        return BnfProviderSession(credential_id=credential_id, label=label,
                                  credentials=credentials, options=options, diagnostics=diagnostics,
                                  identity_broker=getattr(self, "_identity_broker", None))


PLUGIN = BnfProviderPlugin()
