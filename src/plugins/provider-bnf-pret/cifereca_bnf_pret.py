"""BnF reader provider using the same public provider contract as Paris."""
from __future__ import annotations
import threading
from uuid import uuid4
from cifereca.plugin_api import (
    Account, Loan, CredentialField, ProviderCapabilities, ProviderDescriptor,
    ProviderPlugin, ProviderSession, ProviderConfigurationError,
)
from cifereca_bnf_pret_http import ReaderHTTPClient, ReaderHTTPError, ACTIVITY
from cifereca_bnf_pret_web import ProfileScope, parse_loans, WebParseError

DESCRIPTOR = ProviderDescriptor(
    id='bnf-pret', display_name='BnF Prêt', state='ready',
    description='Prêts en cours du portail lecteur BnF, par HTTPX.',
    capabilities=ProviderCapabilities(authentication=True, multiple_credentials=True, loans=True),
    credential_fields=(CredentialField('username', 'Identifiant BnF'),
                       CredentialField('password', 'Mot de passe', secret=True)),
    homepage='https://pret.bnf.fr',
    notes=('Inventaire partiel : aucune clôture de prêt par absence.',
           'SSO HTTP expérimental ; une étape interactive supplémentaire est refusée.'),
)


class BnfProviderSession(ProviderSession):
    def __init__(self, *, client=None, **kwargs):
        super().__init__(descriptor=DESCRIPTOR, **kwargs)
        self.client = client or ReaderHTTPClient()
        self._lock = threading.RLock()
        self._epoch = uuid4().hex
        self._closed = False
        self.last_loan_errors = [{'code': 'inventory_not_exhaustive'}]

    def _snapshot(self, html):
        scope = ProfileScope(self.credential_id, self.credential_id, self._epoch)
        return parse_loans(html, page_url=ACTIVITY, expected=scope, observed=scope)

    def connect(self):
        with self._lock:
            self.connected = False
            self._accounts = []
            username = str(self.credentials.get('username') or '').strip()
            password = str(self.credentials.get('password') or '')
            if self._closed or not username or not password:
                raise ProviderConfigurationError('bnf_credentials_required')
            epoch = self._epoch
            try:
                self._snapshot(self.client.login(username, password))
            except (ReaderHTTPError, WebParseError) as exc:
                self.client.clear()
                raise ProviderConfigurationError(str(exc)) from None
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
                raise ProviderConfigurationError(str(exc)) from None
            if self._closed or epoch != self._epoch:
                raise ProviderConfigurationError('session_closed')
            return [Loan(id=row.loan_id, provider='BNF_PRET', medium='digital',
                         record_id=row.resource_id, title=row.title or 'Prêt BnF',
                         source_provider=self.provider_id, source_credential=self.credential_id,
                         source_account_id=account.unique_id, identity_id=account.identity_id,
                         state='active', can_return=False, renewable=False,
                         raw={'inventory_exhaustive': False}) for row in snapshot.loans]

    def close(self):
        # Invalidate in-flight work before waiting for the bounded HTTP operation.
        self._closed = True
        self._epoch = uuid4().hex
        self.connected = False
        self._accounts = []
        with self._lock:
            self.client.clear()
            self.client.close()


class BnfProviderPlugin(ProviderPlugin):
    descriptor = DESCRIPTOR

    def create_session(self, *, credential_id, label, credentials, options, config, storage, diagnostics):
        return BnfProviderSession(credential_id=credential_id, label=label,
                                  credentials=credentials, options=options)


PLUGIN = BnfProviderPlugin()
