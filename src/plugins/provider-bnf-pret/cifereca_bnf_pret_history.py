"""Explicit BnF history qualification, isolated from active loan execution."""
from __future__ import annotations
from cifereca.plugin_api import Account, ProviderConfigurationError, ProviderError
from cifereca_bnf_pret_http import ReaderHTTPError
from cifereca_bnf_pret_web import ProfileScope, WebParseError


def read_history_events(self, account):
    """Explicit qualification read only; not ProviderSession.history().

    No caller on startup, Sync or navigation. A history event is not an
    ended loan or a right. Keep the existing capability disabled.
    """
    from cifereca_bnf_pret_web import parse_history_events
    with self._lock:
        if (type(account) is not Account or self.connected is not True
                or self._closed or len(self._accounts) != 1
                or account is not self._accounts[0]):
            raise ProviderConfigurationError('account_scope_mismatch')
        epoch = self._epoch
        client = self.client
        broker = self.identity_broker
        borrower = (account.unique_id, account.identity_id,
                    account.provider_id, account.credential_id)
        if (borrower[2:] != (self.provider_id, self.credential_id)
                or account.is_primary is not True
                or any(type(value) is not str or not value or len(value) > 256
                       for value in borrower)):
            raise ProviderConfigurationError('account_scope_mismatch')
        owner = (self._core_owner_binding(borrower[0], borrower[1])
                 if broker is not None else None)
        if broker is not None and (owner is None or owner[1] != (borrower[1],)):
            raise ProviderConfigurationError('account_scope_mismatch')

        def current():
            return (
                self.connected is True and not self._closed
                and self._epoch == epoch and self.client is client
                and self.identity_broker is broker
                and len(self._accounts) == 1 and self._accounts[0] is account
                and account.is_primary is True
                and (account.unique_id, account.identity_id,
                     account.provider_id, account.credential_id) == borrower
                and (self.provider_id, self.credential_id) == borrower[2:]
                and (broker is None or
                     self._core_owner_binding(borrower[0], borrower[1]) == owner)
            )

        if not current():
            raise ProviderConfigurationError('account_scope_mismatch')
        scope = ProfileScope(borrower[1], borrower[0], epoch)
        try:
            page_url, html = client.history_events_page()
            if not current():
                raise ProviderConfigurationError('account_scope_mismatch')
            result = parse_history_events(
                html, page_url=page_url, expected=scope, observed=scope,
            )
        except (ReaderHTTPError, WebParseError) as exc:
            # History failure cannot publish an empty result. Do not
            # alter active-loan evidence or retry an expired login here.
            raise ProviderError(self._report_failure('history', exc)) from None
        if not current():
            raise ProviderConfigurationError('account_scope_mismatch')
        return result

