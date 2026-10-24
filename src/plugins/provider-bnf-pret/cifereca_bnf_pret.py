"""BnF reader provider using the same public provider contract as Paris."""
from __future__ import annotations
from pathlib import Path
import threading
from uuid import uuid4
from cifereca.plugin_api import (
    Account, Loan, SearchResult, ItemDetail, CredentialField,
    ProviderCapabilities, ProviderSearchCapabilities, ProviderDescriptor,
    ProviderPlugin, ProviderSession, ProviderConfigurationError,
    ProviderCapabilityError, ProviderError, normalize_language_tag,
)
from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError, ACTIVITY
from cifereca_bnf_pret_web import ProfileScope, parse_loans, WebParseError
from cifereca_bnf_pret_catalog import read_search, read_detail, MAX_SEARCH_PAGES, resource_url
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


def _public_code(error):
    code = str(error)
    return code if code in _PUBLIC_CODES else 'bnf_reader_failed'


class BnfProviderSession(ProviderSession):
    def __init__(self, *, client=None, diagnostics=None, **kwargs):
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.client = client or ReaderHTTPClient()
        self.diagnostics = diagnostics
        self._lock = threading.RLock()
        self._epoch = uuid4().hex
        self._closed = False
        self.last_loan_errors = [{'code': 'inventory_not_exhaustive'}]
        self.last_search_warnings = []
        # Only loans positively observed during this credential session are
        # eligible for an explicit, fresh-read token retrieval.
        self._observed_loans = {}

    def _report_failure(self, phase, error):
        code = _public_code(error)
        try:
            if self.diagnostics is not None:
                self.diagnostics.add('API', 'BnF Prêt', 'error', detail={
                    'provider': 'bnf-pret', 'phase': phase, 'code': code,
                })
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
            self.last_loan_errors = [{'code': 'inventory_not_exhaustive'}]
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
                         source_provider=self.provider_id, source_credential=self.credential_id,
                         source_account_id=account.unique_id, identity_id=account.identity_id,
                         state='active', can_return=False, renewable=False,
                         raw={'inventory_exhaustive': False}) for row in snapshot.loans]

    def download_digital(self, loan: Loan, account: Account, target_dir: Path):
        """Explicit candidate ACSM fetch; the automatic Core path stays denied.

        The public digital_download capability remains false until the Core
        confirms selection, exact identity, staging and ADEPT consent end-to-end.
        Never infer a right from this GET or from the ACSM content alone.
        """
        with self._lock:
            if (self._closed or not self.connected or len(self._accounts) != 1
                    or account is not self._accounts[0] or type(loan) is not Loan
                    or loan.source_provider != self.provider_id
                    or loan.source_credential != self.credential_id
                    or loan.source_account_id != account.unique_id
                    or loan.identity_id != account.identity_id
                    or loan.medium != 'digital' or loan.state != 'active'
                    or not loan.id or not loan.record_id
                    or self._observed_loans.get(loan.id) != loan.record_id):
                raise ProviderConfigurationError('loan_not_observed')
            epoch = self._epoch
            try:
                # No stale loan can trigger the GET: re-read the exact Web
                # account and demand the same loan/resource pair right now.
                fresh = self._snapshot(self.client.activity())
                if not any(row.loan_id == loan.id and row.resource_id == loan.record_id
                           for row in fresh.loans):
                    raise ReaderHTTPError('loan_not_observed')
                token = self.client.loan_token(loan.id)
                validate_loan_token(token)
                if self._closed or epoch != self._epoch:
                    raise ReaderHTTPError('session_closed')
                path = save_private_loan_token(token, target_dir)
                if self._closed or epoch != self._epoch:
                    path.unlink(missing_ok=True)
                    raise ReaderHTTPError('session_closed')
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
            if (scope not in {'all', 'digital'} or field != 'all'
                    or type(page) is not int or page != 0):
                raise ProviderCapabilityError('unsupported_search')
            if content_types and 'ebook' not in content_types:
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

    def create_session(self, *, credential_id, label, credentials, options, config, storage, diagnostics):
        return BnfProviderSession(credential_id=credential_id, label=label,
                                  credentials=credentials, options=options, diagnostics=diagnostics)


PLUGIN = BnfProviderPlugin()
