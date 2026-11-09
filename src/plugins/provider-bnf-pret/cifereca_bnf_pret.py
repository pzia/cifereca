"""BnF reader provider using the same public provider contract as Paris."""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import logging
from datetime import datetime, timezone
import threading
from uuid import uuid4
from cifereca.plugin_api import (
    Account, Loan, SearchResult, ItemDetail, CredentialField, AccountOptionField,
    ProviderCapabilities, ProviderSearchCapabilities, ProviderDescriptor,
    ProviderPlugin, ProviderSession, ProviderConfigurationError,
    ProviderCapabilityError, ProviderError, BorrowRequestNotSubmitted,
    normalize_language_tag,
    LoanInventoryScope, LoanInventorySnapshot, LoanBranchCoverage,
    ProviderRightsScope, ProviderRightObservation, ProviderRightsSnapshot,
)
from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError, ACTIVITY
from cifereca_bnf_pret_web import ProfileScope, parse_loans, WebParseError
from cifereca_bnf_pret_catalog import read_search, read_detail, MAX_SEARCH_PAGES, resource_url
from cifereca_bnf_pret_signed_action import signed_acsm_action, reader_acs4_offer_pairs
from cifereca_bnf_pret_download import save_private_loan_token, validate_loan_token

DESCRIPTOR = ProviderDescriptor(
    id='bnf-pret', display_name='BnF Prêt', state='ready',
    description='Catalogue, fiches et prêts du portail lecteur BnF, par HTTPX.',
    capabilities=ProviderCapabilities(authentication=True, multiple_credentials=True, loans=True,
                                      search=True, holdings=True, borrow=True,
                                      loan_auto_materialization=True),
    search_capabilities=ProviderSearchCapabilities(
        fields=('all',), scopes=('all', 'digital'), pagination='next-link',
        max_pages=MAX_SEARCH_PAGES,
        notes=('Pagination automatique via les seuls liens HTML observés, 5 pages maximum.',
               'Inventaire de catalogue non exhaustif ; aucun droit ou format déduit.'),
    ),
    credential_fields=(CredentialField('username', 'Identifiant BnF'),
                       CredentialField('password', 'Mot de passe', secret=True)),
    homepage='https://pret.bnf.fr',
    # One BnF credential session exposes exactly one borrower. This is a
    # *user-declared* preference; never consulted by borrow, quota or sync.
    account_options=(AccountOptionField(
        'manual_loan_limit', 'Maximum d’emprunts (valeur personnelle)',
        default='4', value_type='unsigned_integer', min_value=0, max_value=99,
        help_text='4 par défaut. Valeur indicative renseignée par vous : '
                  'elle ne change pas vos droits auprès de la BnF.',
    ),),
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
    'borrow_resource_invalid', 'borrow_url_refused', 'borrow_scope_mismatch',
    'borrow_detail_unrecognized', 'borrow_action_unverified',
    'borrow_form_unverified', 'borrow_csrf_unverified',
    'borrow_not_authorized', 'borrow_claim_spent',
    'borrow_existing_loan', 'borrow_receipt_unverified',
    'borrow_activity_unverified', 'borrow_confirmation_side_effect_unverified',
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


def _bnf_core_admission_current(admission_current):
    """Fail closed on Core revocation; None retains the explicit legacy path."""
    try:
        return (admission_current is None or
                (callable(admission_current) and admission_current() is True))
    except Exception:
        return False


def _reader_acs4_offer_id(identity_id, credential_id, account_id, loan_id, resource_id):
    """Stable opaque action key; created ONLY after a signed action was seen.

    Never hash the signed URL itself: token/nonce/signature are transient and
    must not enter the Core rights ledger, logs, or public provider models.
    """
    coordinates = (identity_id, credential_id, account_id, loan_id, resource_id)
    if not all(type(value) is str and 0 < len(value) <= 256
               for value in coordinates):
        return ''
    payload = '\x00'.join(coordinates).encode('utf-8')
    return 'bnf.reader_acs4:' + hashlib.sha256(payload).hexdigest()


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
        # Exact, fresh coverage proof from the last authenticated activity
        # read; never a durable right/negative-reconciliation authority.
        self._last_loan_inventory_evidence = None
        # Extra same-session guard: Core already owns durable at-most-once.
        self._borrow_post_attempts = set()

    def _report_failure(self, phase, error):
        code = _public_code(error)
        # Exception messages and tracebacks may include signed ACSM URLs,
        # cookies, or SAML contents. Emit only closed, stable diagnostic facts.
        detail = {
            'provider': 'bnf-pret', 'phase': phase, 'code': code,
            'error_type': type(error).__name__,
            'error_message': code,
            'error_traceback': 'redacted',
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

    def _report_loan_metadata(self, scope, loans):
        """Record the actual provider projection, without another HTTP read.

        Unknown thumbnails are not missing books or failed image downloads.
        No signed action, HTML, token or inferred cover enters this event.
        """
        detail = {
            'provider_id': scope[2], 'credential_id': scope[3],
            'account_id': scope[0], 'identity_id': scope[1],
            'phase': 'provider_projection', 'coverage': 'partial',
            'observed_count': len(loans),
            'loans': [{
                'loan_id': loan.id, 'resource_id': loan.record_id,
                'title': loan.title, 'thumbnail_url': loan.thumbnail_url,
                'thumbnail_status': 'observed' if loan.thumbnail_url else 'unknown',
            } for loan in loans],
        }
        try:
            if self.diagnostics is not None:
                self.diagnostics.add('COVER', 'BnF loan metadata', 'ok', detail=detail)
            else:
                payload = dict(
                    schema_version=1, category='COVER', operation='BnF loan metadata',
                    message='BnF loan metadata', status='ok', detail=detail,
                )
                logging.getLogger('cifereca').info(
                    'DIAG_JSON %s', json.dumps(payload, ensure_ascii=False),
                )
        except Exception:
            pass  # A diagnostic sink cannot make an observed loan disappear.

    def connect(self):
        with self._lock:
            # Reauthentication is a new generation even with the same
            # Account/credential: revoke leases from before a reconnect.
            # This is independent of Core's identity context epoch.
            self._epoch = uuid4().hex
            self.connected = False
            self._accounts = []
            self._observed_loans = {}
            self._last_loan_inventory_evidence = None
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
        """Versioned legacy v1 entrypoint; no negative inventory claims."""
        with self._lock:
            return self._read_live_loans_locked(account)

    def _read_live_loans_locked(self, account):
        """One scoped HTML read shared by typed inventory and v1 callers.

        Called exclusively under the session lock; a typed observation never
        delegates its authority to an overridable v1 loans() method.
        """
        if (not self.connected or self._closed or len(self._accounts) != 1
                or account is not self._accounts[0]):
            raise ProviderConfigurationError('account_scope_mismatch')
        epoch = self._epoch
        # The legacy loans() bridge is still used during #3197 migration.
        # A Core Account is mutable independently of this session lock:
        # freeze its identity/credential before the provider HTML GET.
        caller_scope = (
            account.unique_id, account.identity_id,
            account.provider_id, account.credential_id,
        )
        if caller_scope[2:] != (self.provider_id, self.credential_id):
            raise ProviderConfigurationError('account_scope_mismatch')
        self._last_loan_inventory_evidence = None
        broker = self.identity_broker
        owner = (
            self._core_owner_binding(caller_scope[0], caller_scope[1])
            if broker is not None else None
        )
        if broker is not None and (
            owner is None or owner[1] != (caller_scope[1],)
        ):
            raise ProviderConfigurationError('account_scope_mismatch')
        try:
            activity_html = self.client.activity()
            snapshot = self._snapshot(activity_html)
        except (ReaderHTTPError, WebParseError) as exc:
            self.connected = False
            self.client.clear()
            raise ProviderConfigurationError(self._report_failure('loans', exc)) from None
        if self._closed or epoch != self._epoch:
            raise ProviderConfigurationError('session_closed')
        if (not self.connected or len(self._accounts) != 1
                or account is not self._accounts[0]
                or (account.unique_id, account.identity_id,
                    account.provider_id, account.credential_id) != caller_scope
                or self.identity_broker is not broker
                or (broker is not None and
                    self._core_owner_binding(caller_scope[0], caller_scope[1]) != owner)):
            raise ProviderConfigurationError('account_scope_mismatch')
        # Presence of one *actual* validated reader ACSM action in the
        # same authenticated HTML is an offered retrieval route, not an
        # acquired file. Parse once for all active loans; do not keep or
        # export signed URLs. No token GET occurs during this observation.
        offered_pairs = reader_acs4_offer_pairs(
            activity_html,
            tuple((row.loan_id, row.resource_id) for row in snapshot.loans),
        )
        if (self._closed or epoch != self._epoch or not self.connected
                or len(self._accounts) != 1 or account is not self._accounts[0]
                or (account.unique_id, account.identity_id,
                    account.provider_id, account.credential_id) != caller_scope
                or self.identity_broker is not broker
                or (broker is not None and
                    self._core_owner_binding(caller_scope[0], caller_scope[1]) != owner)):
            raise ProviderConfigurationError('account_scope_mismatch')
        loans = [Loan(id=row.loan_id, provider='BNF_PRET', medium='digital',
                     record_id=row.resource_id, title=row.title or 'Prêt BnF',
                     author=', '.join(row.authors) or None,
                     thumbnail_url=row.thumbnail_url,
                     due_at=row.due_at,
                     document_type='Prêt numérique',
                     source_provider=self.provider_id, source_credential=self.credential_id,
                     source_account_id=account.unique_id, identity_id=account.identity_id,
                     state='active', can_return=False, renewable=False,
                     raw={
                         'inventory_exhaustive': False,
                         **({
                             'reader_acs4_offer_id': _reader_acs4_offer_id(
                                 account.identity_id, self.credential_id,
                                 account.unique_id, row.loan_id, row.resource_id,
                             ),
                         } if (row.loan_id, row.resource_id) in offered_pairs
                         else {}),
                     }) for row in snapshot.loans]
        self._report_loan_metadata(caller_scope, loans)
        # Optional host diagnostics can run callbacks. Do not publish loans
        # (or token eligibility) after a reconnect/rebind during that callback.
        if (self._closed or epoch != self._epoch or not self.connected
                or len(self._accounts) != 1 or account is not self._accounts[0]
                or (account.unique_id, account.identity_id,
                    account.provider_id, account.credential_id) != caller_scope
                or self.identity_broker is not broker
                or (broker is not None and
                    self._core_owner_binding(caller_scope[0], caller_scope[1]) != owner)):
            raise ProviderConfigurationError('account_scope_mismatch')
        # A partial/empty page cannot close historical rights; nevertheless
        # it must not authorize token downloads from stale observations.
        self._observed_loans = {row.loan_id: row.resource_id for row in snapshot.loans}
        self._last_loan_inventory_evidence = (
            self._epoch, caller_scope, snapshot.exhaustive,
        )
        return loans

    def loan_inventory_snapshot(self, account):
        # Keep the loan GET, the active account and its typed envelope under
        # the SAME epoch/session lock. Otherwise connect() could replace
        # _accounts after loans() returns and before snapshot publication.
        with self._lock:
            return self._loan_inventory_snapshot_locked(account)

    def _loan_inventory_snapshot_locked(self, account):
        """Exact borrower-scoped active-loans coverage, never acquisition proof.

        Calls one authenticated scoped activity GET. Only a validated,
        nonempty, non-paginated loan widget can assert complete coverage;
        empty/ambiguous HTML and a monkeypatched v1 reader remain partial.
        No token GET, rights deletion, return, or ADEPT side effects.
        """
        if (not self.connected or self._closed or len(self._accounts) != 1
                or type(account) is not Account
                or type(self._accounts[0]) is not Account):
            raise ProviderConfigurationError('account_scope_mismatch')
        bound = self._accounts[0]
        caller_scope = (
            account.unique_id, account.identity_id,
            account.provider_id, account.credential_id,
        )
        bound_scope = (
            bound.unique_id, bound.identity_id,
            bound.provider_id, bound.credential_id,
        )
        if (caller_scope != bound_scope
                or caller_scope[2:] != (self.provider_id, self.credential_id)):
            raise ProviderConfigurationError('account_scope_mismatch')
        # Typed v1 can read an exact defensive Account copy; the actual
        # authenticated GET must still use the *live* session Account. The
        # historical loans() method remains instance-restricted.
        scope = LoanInventoryScope.for_account(account)
        self._last_loan_inventory_evidence = None
        rows = self._read_live_loans_locked(bound)
        evidence = self._last_loan_inventory_evidence
        if not self.connected or self._closed:
            raise ProviderConfigurationError('session_closed')
        if (len(self._accounts) != 1 or self._accounts[0] is not bound
                or (bound.unique_id, bound.identity_id,
                    bound.provider_id, bound.credential_id) != bound_scope
                or (account.unique_id, account.identity_id,
                    account.provider_id, account.credential_id) != caller_scope
                or LoanInventoryScope.for_account(bound) != scope):
            raise ProviderConfigurationError('account_scope_mismatch')
        complete = (
            type(evidence) is tuple and len(evidence) == 3
            and evidence == (self._epoch, bound_scope, True)
        )
        return LoanInventorySnapshot(
            scope=scope,
            loans=tuple(rows),
            status='success' if complete else 'partial',
            branches=(LoanBranchCoverage(
                upstream='bnf_html', medium='digital',
                completeness='complete' if complete else 'partial',
                pagination_complete=True if complete else None,
                reason_code=None if complete else 'inventory_not_exhaustive',
            ),),
            reason_code=None if complete else 'inventory_not_exhaustive',
        )

    def _core_owner_binding(self, account_id, identity_id):
        """One unique Core owner reference, or None when not attestable.

        This local broker read does not authenticate to BnF or grant a token;
        the caller decides whether a missing broker is legacy-compatible.
        """
        broker = self.identity_broker
        if broker is None:
            return None
        try:
            if type(identity_id) is not str or not identity_id:
                return None
            # Resolve the owner in the selected Core identity, not across
            # unrelated identities using the same BnF upstream account ID.
            refs = broker.accounts(identity_id)
            if type(refs) not in (tuple, list):
                return None
            owners = []
            for ref in refs:
                if (getattr(ref, "provider_id", None) != self.provider_id
                        or getattr(ref, "account_id", None) != account_id):
                    continue
                # #3171 Core AccountRef v2 adds exact credential proof.
                # Never mix another credential's identical upstream ID into
                # this user's rights or the explicitly requested token GET.
                # Keep credential-less v1 refs as unresolved candidates:
                # mixed v1/v2 must NOT silently adopt another credential.
                version = getattr(ref, "scope_version", 1)
                if type(version) is not int or version not in (1, 2):
                    return None
                if version == 2:
                    credential = getattr(ref, "credential_id", None)
                    if type(credential) is not str or not credential:
                        return None
                    if credential != self.credential_id:
                        continue
                identity = getattr(ref, "identity_id", None)
                owners.append(identity if type(identity) is str else "")
            return (id(broker), tuple(sorted(owners)))
        except Exception:
            return None

    def rights_observation_snapshot(self, account):
        """One authenticated BnF activity read -> positive scoped loan claims.

        Complete coverage is supported only for the exact nonempty,
        strictly validated active-loan widget and every loan's positive,
        unambiguous rights projection. An empty/ambiguous/partial widget or
        dropped/foreign row stays partial. Even a complete observation
        never grants negative reconciliation, a purchase right or mutation.
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
                return (
                    self._core_owner_binding(caller_scope[0], caller_scope[1])
                    if caller_scope is not None else None
                )

            owner_binding = core_binding()
            if (caller_scope is None or caller_scope[2:] !=
                    (self.provider_id, self.credential_id)
                    or type(caller_scope[1]) is not str or not caller_scope[1]
                    or owner_binding is None
                    or owner_binding[1] != (caller_scope[1],)):
                raise ProviderCapabilityError("bnf_rights_owner_unverified")
            # Core may pass a defensive Account copy. The only session
            # authorized for a BnF GET is still the *live* Account instance:
            # check exact scope before reading it and pin its instance, so
            # rebinds/reconnects A->A cannot authorize stale observations.
            if len(self._accounts) != 1 or type(self._accounts[0]) is not Account:
                raise ProviderConfigurationError('account_scope_mismatch')
            bound = self._accounts[0]
            bound_scope = (
                bound.unique_id, bound.identity_id,
                bound.provider_id, bound.credential_id,
            )
            if bound_scope != caller_scope:
                raise ProviderConfigurationError('account_scope_mismatch')
            inventory = self._loan_inventory_snapshot_locked(bound)
            scope = inventory.scope
            if (not self.connected or self._closed or len(self._accounts) != 1
                    or self._accounts[0] is not bound
                    or (bound.unique_id, bound.identity_id,
                        bound.provider_id, bound.credential_id) != bound_scope
                    or LoanInventoryScope.for_account(bound) != scope
                    or (account.unique_id, account.identity_id,
                        account.provider_id, account.credential_id) != caller_scope
                    or core_binding() != owner_binding):
                raise ProviderConfigurationError('account_scope_mismatch')
            observed = datetime.now(timezone.utc).isoformat()
            rights = []
            for loan in inventory.loans:
                # Both opaque IDs come from the authenticated active-loan HTML:
                # never substitute a catalogue URL, title, ISBN or file hash.
                if loan.medium != 'digital':
                    continue
                # The current BnF HTML parser limits route IDs to 128 ASCII
                # characters. Keep the public rights/1 boundary independent
                # of that parser: a malformed typed loan or future parser
                # regression must not erase valid sibling rights by raising
                # from ProviderRightObservation.__post_init__.
                coordinates = (loan.record_id, loan.id)
                if not all(
                    type(value) is str and 1 <= len(value) <= 256
                    and not any(char in value for char in ('\x00', '\r', '\n'))
                    for value in coordinates
                ):
                    continue
                # The signed ACSM action is a concrete, account-scoped
                # retrieval offer observed with THIS active loan. Never infer
                # the offer from a loan ID, catalogue entry or an old token.
                reported = (loan.raw.get('reader_acs4_offer_id')
                            if type(loan.raw) is dict else None)
                expected = _reader_acs4_offer_id(
                    scope.identity_id, self.credential_id, scope.account_id,
                    loan.id, loan.record_id,
                )
                offer_id = reported if (type(reported) is str
                                        and reported == expected) else ''
                rights.append(ProviderRightObservation(
                    edition_id=loan.record_id, right_id=loan.id,
                    kind='loan', state=('active' if loan.state == 'active' else 'unknown'),
                    evidence_kind='provider_response',
                    evidence_ref='bnf_html.active_loans',
                    observed_at=observed,
                    delivery='available' if offer_id else 'unknown',
                    delivery_offer_id=offer_id,
                ))
            if (not self.connected or self._closed or len(self._accounts) != 1
                    or self._accounts[0] is not bound
                    or (bound.unique_id, bound.identity_id,
                        bound.provider_id, bound.credential_id) != bound_scope
                    or LoanInventoryScope.for_account(bound) != scope
                    or (account.unique_id, account.identity_id,
                        account.provider_id, account.credential_id) != caller_scope
                    or core_binding() != owner_binding):
                raise ProviderConfigurationError('account_scope_mismatch')
            # A typed, complete active-loan branch is positive coverage of
            # *loans only*. Require a one-to-one projection of every borrower
            # row: an omitted invalid/unknown/foreign loan cannot silently
            # turn a partial rights read into complete. Neither this flag nor
            # pagination authorizes deletion, return, download or consent.
            branches = inventory.branches if type(inventory) is LoanInventorySnapshot else ()
            complete = (
                type(inventory) is LoanInventorySnapshot
                and inventory.status == 'success'
                and len(branches) == 1
                and branches[0].upstream == 'bnf_html'
                and branches[0].medium == 'digital'
                and branches[0].completeness == 'complete'
                and branches[0].pagination_complete is True
                and len(inventory.loans) > 0
                and len(rights) == len(inventory.loans)
                and all(
                    type(loan) is Loan
                    and loan.medium == 'digital'
                    and loan.state == 'active'
                    and (loan.source_provider, loan.source_credential,
                         loan.source_account_id, loan.identity_id) == (
                         self.provider_id, self.credential_id,
                         scope.account_id, scope.identity_id)
                    for loan in inventory.loans
                )
                and all(right.state == 'active' for right in rights)
            )
            return ProviderRightsSnapshot(
                scope=ProviderRightsScope(
                    scope.identity_id, scope.provider_id,
                    scope.credential_id, scope.account_id,
                ),
                rights=tuple(rights),
                status='success' if complete else 'partial',
                coverage='complete' if complete else 'partial',
                pagination_complete=True if complete else False,
                branch='bnf_html',
                observed_at=observed, snapshot_ref='bnf_html.active_loans',
            )

    def downloadable_resource_snapshot(self, account):
        """#3723 v2: exact source observations, not permission or delivery."""
        from cifereca.plugin_api import from_legacy_rights_snapshot
        return from_legacy_rights_snapshot(self.rights_observation_snapshot(account))

    def downloadable_selected_offer_snapshot(
        self, account, *, edition_id, resource_id, kind, branch,
        explicit_user_action=False,
    ):
        """#3723 v2: selected provider offer, rights state descriptive only."""
        from cifereca.plugin_api import from_legacy_rights_snapshot
        return from_legacy_rights_snapshot(
            self.rights_selected_delivery_offer_snapshot(
                account, edition_id=edition_id, right_id=resource_id,
                kind=kind, branch=branch,
                explicit_user_action=explicit_user_action,
            )
        )

    def downloadable_selected_delivery_candidate(
        self, account, *, edition_id, resource_id, kind, branch,
        selected_offer_id, target_dir, explicit_user_action=False,
    ):
        """Unchanged v2 fallback for explicit/Core callers without a live lease."""
        return self._downloadable_selected_delivery_candidate(
            account, edition_id=edition_id, resource_id=resource_id,
            kind=kind, branch=branch, selected_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
        )

    def downloadable_selected_delivery_candidate_guarded(
        self, account, *, edition_id, resource_id, kind, branch,
        selected_offer_id, target_dir, explicit_user_action=False,
        admission_current,
    ):
        """#3723: Core source ON generation must remain live during ACSM GET."""
        if not callable(admission_current) or not _bnf_core_admission_current(admission_current):
            raise ProviderCapabilityError("bnf_core_admission_revoked")
        return self._downloadable_selected_delivery_candidate(
            account, edition_id=edition_id, resource_id=resource_id,
            kind=kind, branch=branch, selected_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
            admission_current=admission_current,
        )

    def _downloadable_selected_delivery_candidate(
        self, account, *, edition_id, resource_id, kind, branch,
        selected_offer_id, target_dir, explicit_user_action=False,
        admission_current=None,
    ):
        """Stage one scoped ACSM/EPUB; Core owns SHA and any ADEPT completion."""
        from cifereca.plugin_api import SourceReference, DeliveryCandidate
        # Pin the caller scope *before* the underlying provider performs I/O.
        # A rebind must not manufacture a candidate for the new owner.
        scope = self._downloadable_scope(account)
        path, hint = self.rights_selected_delivery_candidate(
            account, edition_id=edition_id, right_id=resource_id,
            kind=kind, branch=branch, delivery_offer_id=selected_offer_id,
            target_dir=target_dir, explicit_user_action=explicit_user_action,
            **({"admission_current": admission_current}
               if admission_current is not None else {}),
        )
        # Compare the mutable caller against the frozen, previously valid
        # scope. Reconstructing ProviderRightsScope *after* GET could itself
        # raise on an empty/invalid account field, bypassing candidate cleanup.
        if ((account.identity_id, account.provider_id,
                account.credential_id, account.unique_id) != (
                scope.identity_id, scope.provider_id,
                scope.credential_id, scope.account_id)
                or not _bnf_core_admission_current(admission_current)):
            try:
                staged = Path(path)
                if (staged.parent == Path(target_dir) and staged.is_file()
                        and not staged.is_symlink()):
                    staged.unlink(missing_ok=True)
            except (OSError, TypeError, ValueError):
                pass
            raise ProviderCapabilityError("selected_delivery_scope_changed")
        return DeliveryCandidate(
            source=SourceReference(
                scope, branch,
                edition_id, resource_id, kind,
            ),
            selected_offer_id=selected_offer_id, path=Path(path),
            provider_hint=hint,
        )

    @staticmethod
    def _downloadable_scope(account):
        from cifereca.plugin_api import ProviderRightsScope
        return ProviderRightsScope(
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        )

    def rights_selected_delivery_offer_snapshot(
        self, account, *, edition_id, right_id, kind, branch,
        explicit_user_action=False,
    ):
        """Provider-neutral selected-right entrypoint for Core #3171.

        Reuses the attested active-loans HTML read (one activity GET); the
        opaque ACS4 offer is an action, not a final EPUB or an ADEPT receipt.
        The Core remains responsible for consent and delivery admission.
        """
        if (explicit_user_action is not True
                or kind != 'loan' or branch != 'bnf_html'
                or type(edition_id) is not str or not edition_id
                or type(right_id) is not str or not right_id
                or type(account) is not Account
                or account.provider_id != self.provider_id
                or account.credential_id != self.credential_id):
            raise ProviderCapabilityError('bnf_selected_offer_scope_mismatch')
        from dataclasses import replace

        with self._lock:
            pinned = (account.identity_id, account.provider_id,
                      account.credential_id, account.unique_id)
            selected = self.rights_observation_snapshot(account)
            observed_scope = (
                selected.scope.identity_id, selected.scope.provider_id,
                selected.scope.credential_id, selected.scope.account_id,
            )
            right = selected.exact(edition_id, right_id, kind)
            if (selected.branch != branch or observed_scope != pinned
                    or right is None or not right.provider_attested
                    or (account.identity_id, account.provider_id,
                        account.credential_id, account.unique_id) != pinned):
                raise ProviderCapabilityError('bnf_selected_offer_scope_changed')
            # A selection is deliberately NOT a full source inventory,
            # even if its parent HTML snapshot attested complete coverage.
            # Never attach branch-wide negative authority to one offered
            # resource, nor conflate its offer with delivery admission.
            return replace(
                selected, rights=(right,), status='partial',
                coverage='partial', pagination_complete=False,
            )

    def rights_selected_delivery_candidate(
        self, account, *, edition_id, right_id, kind, branch,
        delivery_offer_id, target_dir, explicit_user_action=False,
        admission_current=None,
    ):
        """Execute one selected *already owned* loan to a candidate ACSM.

        The existing guarded download_digital does the fresh action/token
        recheck. This method cannot attest an EPUB, ADEPT receipt or Core
        publication and must never be called before Core consent/admission.
        """
        if (explicit_user_action is not True
                or kind != 'loan' or branch != 'bnf_html'
                or type(account) is not Account
                or type(edition_id) is not str or not edition_id
                or type(right_id) is not str or not right_id
                or type(delivery_offer_id) is not str
                or account.provider_id != self.provider_id
                or account.credential_id != self.credential_id):
            raise ProviderCapabilityError('bnf_selected_delivery_scope_mismatch')
        if not _bnf_core_admission_current(admission_current):
            raise ProviderCapabilityError("bnf_core_admission_revoked")
        # Core hands over an already created private staging directory.
        # Reject an invalid or switched final-component symlink BEFORE any
        # authenticated HTML or ACSM GET. Legacy explicit download_digital()
        # retains its historical destination handling.
        if not isinstance(target_dir, (str, Path)):
            raise ProviderCapabilityError('bnf_selected_delivery_target_invalid')
        try:
            staging = Path(target_dir)
            if (staging.is_symlink() or not staging.is_dir()
                    or staging.resolve(strict=True) != staging.absolute()):
                raise ProviderCapabilityError('bnf_selected_delivery_target_invalid')
        except (OSError, ValueError, TypeError):
            raise ProviderCapabilityError('bnf_selected_delivery_target_invalid') from None
        with self._lock:
            # The optional Core path requires a broker-verified borrower,
            # unlike the historical brokerless manual token action.
            pinned = (account.identity_id, account.provider_id,
                      account.credential_id, account.unique_id)
            if (not self.connected or self._closed
                    or len(self._accounts) != 1
                    or type(self._accounts[0]) is not Account):
                raise ProviderCapabilityError('bnf_selected_delivery_owner_changed')
            live = self._accounts[0]
            owner = self._core_owner_binding(pinned[3], pinned[0])
            if ((live.identity_id, live.provider_id,
                 live.credential_id, live.unique_id) != pinned
                    or owner is None or owner[1] != (pinned[0],)
                    or delivery_offer_id != _reader_acs4_offer_id(
                        pinned[0], pinned[2], pinned[3], right_id, edition_id,
                    )):
                raise ProviderCapabilityError('bnf_selected_delivery_owner_changed')
            epoch = self._epoch
            # #3197: selected canonical acquisition cannot delegate its
            # authorization to the overridable, compatibility-only v1 loans()
            # façade. The typed inventory and this preflight share the same
            # guarded private HTML reader, with one activity GET here.
            loans = self._read_live_loans_locked(live)
            if not _bnf_core_admission_current(admission_current):
                raise ProviderCapabilityError("bnf_core_admission_revoked")
            if (self._closed or not self.connected or epoch != self._epoch
                    or len(self._accounts) != 1 or self._accounts[0] is not live
                    or (account.identity_id, account.provider_id,
                        account.credential_id, account.unique_id) != pinned
                    or (live.identity_id, live.provider_id,
                        live.credential_id, live.unique_id) != pinned
                    or self._core_owner_binding(pinned[3], pinned[0]) != owner):
                raise ProviderCapabilityError('bnf_selected_delivery_owner_changed')
            matches = [
                loan for loan in loans
                if loan.id == right_id and loan.record_id == edition_id
                and loan.source_account_id == pinned[3]
                and loan.raw.get('reader_acs4_offer_id') == delivery_offer_id
            ]
            if len(matches) != 1:
                raise ProviderCapabilityError('bnf_selected_delivery_unobserved')
            # A second fresh loan check still occurs inside download_digital
            # immediately before the signed token GET; no stale ACSM fallback.
            return self.download_digital(
                matches[0], live, staging,
                explicit_user_action=True,
                expected_offer_id=delivery_offer_id,
                **({"admission_current": admission_current}
                   if admission_current is not None else {}),
            )

    def download_digital(self, loan: Loan, account: Account, target_dir: Path,
                         *, explicit_user_action: bool = False,
                         expected_offer_id: str | None = None,
                         admission_current=None):
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
                    or loan.medium != 'digital'
                    # Legacy explicit GET preserves its historical state
                    # requirement. The canonical selected GET requires an
                    # exact *signed file offer*, not proof of an active right.
                    or (expected_offer_id is None and loan.state != 'active')
                    or not loan.id or not loan.record_id
                    or self._observed_loans.get(loan.id) != loan.record_id):
                raise ProviderConfigurationError('loan_not_observed')
            # A Core-admitted optional offer must bind the SAME credential,
            # borrower, loan and edition before any fresh private activity GET.
            # Supplying an offer never bypasses explicit_user_action or Core
            # consent. Legacy explicit callers may omit this extra guard.
            if expected_offer_id is not None and (
                type(expected_offer_id) is not str
                or expected_offer_id != _reader_acs4_offer_id(
                    account.identity_id, self.credential_id,
                    account.unique_id, loan.id, loan.record_id,
                )
            ):
                raise ProviderConfigurationError('loan_not_observed')
            # A connected identity broker may be rebound independently from
            # the local Account object. If present, require its one exact owner
            # before any fresh inventory GET and at every token/publication
            # fence. Brokerless explicit legacy operation stays unchanged.
            broker = self.identity_broker
            owner_binding = self._core_owner_binding(account.unique_id, account.identity_id)
            if broker is not None and (
                owner_binding is None
                or owner_binding[1] != (account.identity_id,)
            ):
                raise ProviderConfigurationError('loan_not_observed')
            epoch = self._epoch
            # Account and Loan are mutable even while this session lock is held:
            # the Core can rebind them during activity() or loan_token(). Pin
            # both scopes AND the unique live account instance: a replacement
            # with identical values (A->A) must also revoke this ACSM GET.
            account_scope = (account.provider_id, account.credential_id,
                             account.unique_id, account.identity_id)
            loan_scope = (loan.id, loan.record_id, loan.provider, loan.medium,
                          loan.state, loan.source_provider, loan.source_credential,
                          loan.source_account_id, loan.identity_id)
            loan_id, resource_id = loan.id, loan.record_id

            def scope_current():
                return (
                    _bnf_core_admission_current(admission_current)
                    and self.connected is True
                    and not self._closed and epoch == self._epoch
                    and (account.provider_id, account.credential_id,
                     account.unique_id, account.identity_id) == account_scope
                    and (loan.id, loan.record_id, loan.provider, loan.medium,
                         loan.state, loan.source_provider, loan.source_credential,
                         loan.source_account_id, loan.identity_id) == loan_scope
                    and self._observed_loans.get(loan_id) == resource_id
                    and self.provider_id == account_scope[0]
                    and self.credential_id == account_scope[1]
                    and len(self._accounts) == 1
                    and self._accounts[0] is account
                    and self.identity_broker is broker
                    and (broker is None or
                         self._core_owner_binding(account_scope[2], account_scope[3]) == owner_binding)
                )

            try:
                # Before even the private activity GET, enforce the same
                # Core lease later checked at every signed-token HTTPS hop.
                if not scope_current():
                    raise ReaderHTTPError('loan_not_observed')
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
                # Preserve legacy explicit reads; only a Core-selected offer
                # carries the epoch/borrower closure through every CDN hop.
                token = (self.client.loan_token(
                    loan_id, signed_url, admission_current=scope_current,
                ) if expected_offer_id is not None else
                    self.client.loan_token(loan_id, signed_url))
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
            description=record.description, thumbnail_url=record.thumbnail_url,
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
                year=None, thumbnail_url=record.thumbnail_url, availability_status=record.availability,
                availability_label=('Indisponibilité affichée par le portail' if unavailable
                                    else 'Disponibilité non vérifiée'), holdings=[],
                source_provider=self.provider_id, source_credential=self.credential_id,
            )

    def borrow(self, item: SearchResult, account: Account) -> Loan:
        """Legacy entrypoint MUST NOT bypass Core BnF's user-checked claim.

        The declared acquisition.borrow means a *dedicated manual CoreUI
        action* is available, never that arbitrary callers may POST here.
        """
        raise ProviderCapabilityError('bnf_borrow_requires_core_dispatch')

    def borrower_eligibility_for(self, item: SearchResult, account: Account):
        """Fresh read-only *partial* borrower evidence for #2055.

        Real BnF pages currently do not attest the subscription state,
        remaining borrower quota or final borrow authorization separately.
        Never infer their positivity from successful authentication, a
        .button-borrow link or an observed CSRF form. Consequently this
        method NEVER returns an eligible positive four-way result.

        This intentionally unadvertised provider API is only preparation
        for a future, independently qualified source contract. No POST,
        no ACSM, no acquired right, no diagnostic payload containing IDs.
        """
        from cifereca.plugin_api import BorrowerOperationEvidence
        with self._lock:
            if (type(item) is not SearchResult
                    or type(account) is not Account
                    or item.source_provider != self.provider_id
                    or item.source_credential != self.credential_id
                    or item.docbase != 'BNF_PRET'
                    or item.medium != 'digital'
                    or type(item.record_id) is not str
                    or not item.record_id or len(item.record_id) > 128
                    or not self.connected or self._closed
                    or len(self._accounts) != 1
                    or account is not self._accounts[0]):
                raise ProviderConfigurationError('account_scope_mismatch')
            epoch = self._epoch
            borrower = (
                account.unique_id, account.identity_id,
                account.provider_id, account.credential_id, account.is_primary,
            )
            notice = (
                item.id, item.record_id, item.docbase, item.medium,
                item.source_provider, item.source_credential,
                item.source_collection,
            )
            if (borrower[2:] != (
                    self.provider_id, self.credential_id, account.is_primary)
                    or not borrower[1] or type(borrower[4]) is not bool):
                raise ProviderConfigurationError('account_scope_mismatch')
            broker = self.identity_broker
            owner = (self._core_owner_binding(borrower[0], borrower[1])
                     if broker is not None else None)
            if broker is not None and (owner is None or owner[1] != (borrower[1],)):
                raise ProviderConfigurationError('account_scope_mismatch')

            def current():
                return (
                    not self._closed and self.connected is True
                    and self._epoch == epoch
                    and len(self._accounts) == 1
                    and account is self._accounts[0]
                    and self.identity_broker is broker
                    and (account.unique_id, account.identity_id,
                         account.provider_id, account.credential_id,
                         account.is_primary) == borrower
                    and (item.id, item.record_id, item.docbase, item.medium,
                         item.source_provider, item.source_credential,
                         item.source_collection) == notice
                    and self.provider_id == borrower[2]
                    and self.credential_id == borrower[3]
                    and (broker is None or
                         self._core_owner_binding(borrower[0], borrower[1]) == owner)
                )

            if not current():
                raise ProviderConfigurationError('account_scope_mismatch')
            # Existing positively observed loan is NOT a permission to
            # open a second one. Never interpret absence on a partial
            # activity page as a positive quota or availability signal.
            loans = self._read_live_loans_locked(account)
            if not current():
                raise ProviderConfigurationError('account_scope_mismatch')
            if any(loan.record_id == notice[1] for loan in loans):
                raise ProviderCapabilityError('borrow_existing_loan')
            try:
                record = read_detail(self.client, notice[1])
            except (ReaderHTTPError, WebParseError) as exc:
                raise ProviderError(
                    self._report_failure('borrow_eligibility', exc)) from None
            if not current() or record.resource_id != notice[1]:
                raise ProviderConfigurationError('account_scope_mismatch')
            # The sole qualified negative is the literal "unavailable"
            # marker *inside this exact book's microdata*. Missing marker
            # remains UNKNOWN, never an affirmative "available".
            return BorrowerOperationEvidence(
                identity_id=borrower[1], provider_id=borrower[2],
                credential_id=borrower[3], borrower_id=borrower[0],
                docbase=notice[2], record_id=notice[1],
                source_collection=notice[6], account_state='unknown',
                quota_state='unknown',
                item_offer=(
                    'unavailable' if record.availability == 'unavailable'
                    else 'unknown'
                ), permission='unknown',
            )

    def borrow_preflight(self, item: SearchResult, account: Account) -> dict:
        """Read-only inspection for #2055; never an authorization or POST.

        Deliberately not declared as acquisition.borrow or called by search,
        holdings, sync, startup or CoreUI. Future action admission must bind
        the user's explicit confirmation to this same live account/session.
        """
        from cifereca_bnf_pret_borrow import (confirmation_link, activation_form,
                                             _is_media_confirmation)
        with self._lock:
            if (type(item) is not SearchResult or item.source_provider != self.provider_id
                    or item.source_credential != self.credential_id
                    or item.docbase != 'BNF_PRET' or item.medium != 'digital'
                    or type(item.record_id) is not str
                    or not self.connected or self._closed
                    or len(self._accounts) != 1 or account is not self._accounts[0]):
                raise ProviderConfigurationError('account_scope_mismatch')
            epoch = self._epoch
            scope = (account.unique_id, account.identity_id,
                     account.provider_id, account.credential_id)
            if scope[2:] != (self.provider_id, self.credential_id) or not scope[1]:
                raise ProviderConfigurationError('account_scope_mismatch')
            broker = self.identity_broker
            owner = (self._core_owner_binding(scope[0], scope[1])
                     if broker is not None else None)
            if broker is not None and (owner is None or owner[1] != (scope[1],)):
                raise ProviderConfigurationError('account_scope_mismatch')

            def current():
                return (not self._closed and self.connected and self._epoch == epoch
                        and len(self._accounts) == 1 and account is self._accounts[0]
                        and (account.unique_id, account.identity_id,
                             account.provider_id, account.credential_id) == scope
                        and self.identity_broker is broker
                        and (broker is None or
                             self._core_owner_binding(scope[0], scope[1]) == owner))

            # Fresh positive inventory is used only to reject a *known*
            # existing loan, never to infer a right to borrow from absence.
            loans = self._read_live_loans_locked(account)
            if not current():
                raise ProviderConfigurationError('account_scope_mismatch')
            if any(loan.record_id == item.record_id for loan in loans):
                raise ProviderCapabilityError('borrow_existing_loan')
            try:
                target = resource_url(item.record_id)
                actual, html = self.client.request('GET', target)
                if not current():
                    raise ProviderConfigurationError('account_scope_mismatch')
                link = confirmation_link(
                    html, page_url=actual, resource_id=item.record_id)
                # The form GET is not assumed side-effect free. This
                # read-only preflight may inspect it but never visit it.
                if _is_media_confirmation(link):
                    return {'status': 'unverified',
                            'reason_code': 'borrow_confirmation_effect_unknown'}
                actual, confirmation = self.client.borrow_confirmation(
                    link, resource_id=item.record_id)
                if not current():
                    raise ProviderConfigurationError('account_scope_mismatch')
                # Do not return or log the action URL, CSRF or hidden fields.
                activation_form(confirmation, page_url=actual,
                                resource_id=item.record_id)
                if not current():
                    raise ProviderConfigurationError('account_scope_mismatch')
                return {'status': 'unverified',
                        'reason_code': 'borrow_confirmation_observed'}
            except (ReaderHTTPError, WebParseError) as error:
                raise ProviderError(self._report_failure('borrow_preflight', error)) from None

    def borrow_explicit_with_claim(
        self, item: SearchResult, account: Account, *,
        admission, claimed_receipt, confirmed=False,
        core_scope_current=None,
    ) -> Loan:
        """BnF-only confirmed manual attempt, under a Core-owned claim.

        The reader visually checked the edition on pret.bnf.fr and then
        confirmed this same target in CoreUI. The BnF server, not Cifereca
        quota markers, decides eligibility after the one POST. The provider
        validates exact activity, fresh detail/form/CSRF, and a resulting
        active loan. Never call this through legacy ProviderSession.borrow().
        No retry follows a claimed POST even when verification fails.
        """
        from cifereca.plugin_api import LendingActionAdmission
        from cifereca_bnf_pret_borrow import (
            confirmation_link, activation_form, verify_borrow_activation,
            _is_media_confirmation,
        )
        if (confirmed is not True
                or type(admission) is not LendingActionAdmission
                or admission.operation != 'loan.borrow'
                or admission.origin != 'manual'
                or admission.status != 'admitted'
                or type(claimed_receipt) is not dict
                or claimed_receipt.get('schema') != 1
                or claimed_receipt.get('operation') != 'loan.borrow'
                or claimed_receipt.get('status') != 'request_sent'
                or type(claimed_receipt.get('operation_id')) is not str
                or not 0 < len(claimed_receipt['operation_id']) <= 128
                or not callable(core_scope_current)):
            raise ProviderCapabilityError('borrow_not_authorized')
        with self._lock:
            if (type(item) is not SearchResult
                    or item.source_provider != self.provider_id
                    or item.source_credential != self.credential_id
                    or item.docbase != 'BNF_PRET' or item.medium != 'digital'
                    or type(item.record_id) is not str
                    or not self.connected or self._closed
                    or type(account) is not Account
                    or len(self._accounts) != 1 or account is not self._accounts[0]):
                raise ProviderConfigurationError('account_scope_mismatch')
            epoch = self._epoch
            reference = (item.id, item.record_id, item.docbase, item.medium,
                         item.source_provider, item.source_credential,
                         item.source_collection)
            borrower = (account.unique_id, account.identity_id,
                        account.provider_id, account.credential_id,
                        account.is_primary)
            broker = self.identity_broker
            owner = (self._core_owner_binding(borrower[0], borrower[1])
                     if broker is not None else None)

            def current():
                try:
                    return (not self._closed and self.connected is True
                            and self._epoch == epoch
                            and self.provider_id == borrower[2]
                            and self.credential_id == borrower[3]
                            and borrower[1]
                            and len(self._accounts) == 1
                            and self._accounts[0] is account
                            and (account.unique_id, account.identity_id,
                                 account.provider_id, account.credential_id,
                                 account.is_primary) == borrower
                            and (item.id, item.record_id, item.docbase,
                                 item.medium, item.source_provider,
                                 item.source_credential, item.source_collection)
                            == reference
                            and self.identity_broker is broker
                            and (broker is None or (
                                owner is not None and owner[1] == (borrower[1],)
                                and self._core_owner_binding(
                                    borrower[0], borrower[1]) == owner))
                            and core_scope_current() is True)
                except Exception:
                    return False

            if not current():
                raise ProviderConfigurationError('account_scope_mismatch')
            # Reusing a consumed operation must not even trigger new GETs.
            if claimed_receipt['operation_id'] in self._borrow_post_attempts:
                raise ProviderCapabilityError('borrow_claim_spent')
            post_started = False
            try:
                before = self._snapshot(self.client.activity())
                if not current():
                    raise ProviderConfigurationError('account_scope_mismatch')
                # An observed active loan is positive proof not to repeat.
                # An omitted loan on a partial page proves NOTHING.
                if any(row.resource_id == reference[1] for row in before.loans):
                    raise ProviderCapabilityError('borrow_existing_loan')
                page_url, page_html = self.client.request(
                    'GET', resource_url(reference[1]))
                if not current():
                    raise ProviderConfigurationError('account_scope_mismatch')
                link = confirmation_link(
                    page_html, page_url=page_url, resource_id=reference[1])
                media_get = _is_media_confirmation(link)
                if media_get:
                    # A GET with a form named loans_confirmation is not
                    # necessarily read-only. From this point onward a
                    # remote mutation MAY occur: retain durable claim on
                    # transport error, timeout, or ambiguous response.
                    post_started = True
                    self._borrow_post_attempts.add(claimed_receipt['operation_id'])
                confirm_url, confirm_html = self.client.borrow_confirmation(
                    link, resource_id=reference[1])
                if not current():
                    raise ProviderConfigurationError('account_scope_mismatch')
                form = activation_form(
                    confirm_html, page_url=confirm_url, resource_id=reference[1])
                if media_get:
                    # The GET could already have created a loan. A positive
                    # observation forbids a second mutating action. A
                    # partial absence never proves no loan was created.
                    after_get = self._snapshot(self.client.activity())
                    if not current():
                        raise ProviderConfigurationError('account_scope_mismatch')
                    if any(row.resource_id == reference[1]
                           for row in after_get.loans):
                        raise WebParseError('borrow_confirmation_side_effect_unverified')
                if not current():
                    raise ProviderConfigurationError('account_scope_mismatch')
                # The durable claim was already reserved by Core. Avoid
                # dispatching the same operation_id a second time even if an
                # internal caller mistakenly reuses the receipt. Core's
                # SQLite claim remains authoritative across process restarts.
                operation_id = claimed_receipt['operation_id']
                if operation_id in self._borrow_post_attempts and not media_get:
                    raise ProviderCapabilityError('borrow_claim_spent')
                # From here onward the POST *may* have reached BnF. Any
                # timeout, 4xx/5xx or parsing failure permanently spends the
                # Core claim until a separately verified resolution exists.
                post_started = True
                self._borrow_post_attempts.add(operation_id)
                receipt_html = self.client.borrow_activation_once(
                    form, confirmation_url=confirm_url,
                    resource_id=reference[1], admission=admission,
                    claimed_receipt=claimed_receipt, confirmed=True,
                )
                if not current():
                    # A scope change after the POST is indeterminate, NOT a
                    # reason to repeat the POST or publish a new right.
                    raise ProviderConfigurationError('account_scope_mismatch')
                after_html = self.client.activity()
                fresh = self._snapshot(after_html)
                if not current():
                    raise ProviderConfigurationError('account_scope_mismatch')
                proof = verify_borrow_activation(
                    receipt_html, after_html,
                    resource_id=reference[1],
                    before=before, expected_scope=fresh.scope,
                )
                matches = [row for row in fresh.loans if
                           row.loan_id == proof.loan_id
                           and row.resource_id == proof.resource_id]
                if len(matches) != 1 or not current():
                    raise ProviderCapabilityError('borrow_receipt_unverified')
                row = matches[0]
                self._observed_loans[row.loan_id] = row.resource_id
                return Loan(
                    id=row.loan_id, provider='BNF_PRET', medium='digital',
                    record_id=row.resource_id, title=row.title or item.title,
                    author=', '.join(row.authors) or None,
                    thumbnail_url=row.thumbnail_url,
                    due_at=row.due_at,
                    document_type='Prêt numérique', state='active',
                    source_provider=self.provider_id,
                    source_credential=self.credential_id,
                    source_account_id=account.unique_id,
                    identity_id=account.identity_id,
                    raw={'inventory_exhaustive': False},
                )
            except (ReaderHTTPError, WebParseError) as error:
                safe_code = self._report_failure('borrow', error)
                if not post_started:
                    # Read-only activity/detail/form GET failure only. The
                    # transport has NOT attempted a mutating request; Core
                    # may CAS-release its reserved checkpoint. This exception
                    # is NEVER emitted after entering borrow_activation_once.
                    raise BorrowRequestNotSubmitted(safe_code) from None
                raise ProviderError(safe_code) from None

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
