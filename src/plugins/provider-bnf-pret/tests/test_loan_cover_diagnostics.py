"""Observe existing loan metadata; never retrieve a cover or signed ACSM."""
import json
import logging
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import ProviderConfigurationError
import test_http_provider as fixture


ACTIVITY = (
    '<section class="active-loans">'
    '<div class="js-loan"><a href="/resources/future">'
    '<img src="/covers/future.jpg">2050, une Histoire du Futur</a>'
    '<a href="/loans/loan-future">Fichier</a></div>'
    '<div class="js-loan"><a href="/resources/dragon">La promesse du dragon</a>'
    '<a href="/loans/loan-dragon">Fichier</a></div></section>'
)


def connected(activity=ACTIVITY):
    s, calls, _ = fixture.HTTPProviderTests().make(activity)
    account, = s.connect()
    account.identity_id = "hugues"
    ref = NS(provider_id="bnf-pret", credential_id=s.credential_id,
             account_id=account.unique_id, identity_id="hugues", scope_version=2)
    s.identity_broker = NS(accounts=lambda identity_id: (ref,) if identity_id == "hugues" else ())
    return s, account, calls


def projection_events(records):
    events = []
    for record in records:
        message = record.getMessage()
        if message.startswith('DIAG_JSON '):
            payload = json.loads(message[len('DIAG_JSON '):])
            if payload['operation'] == 'BnF loan metadata':
                events.append(payload)
    return events


@pytest.mark.parametrize("entrypoint", [
    "loans", "loan_inventory_snapshot", "rights_observation_snapshot",
])
def test_exact_metadata_reaches_canonical_diagnostics_without_extra_http(entrypoint):
    s, account, calls = connected()
    events = []
    s.diagnostics = NS(add=lambda *args, **kwargs: events.append((args, kwargs)))
    before = len(calls)
    try:
        getattr(s, entrypoint)(account)
        assert len(calls) == before + 1
        assert not any(path.startswith(("/covers/", "/loans/")) for _, _, path in calls)
        assert len(events) == 1
        args, kwargs = events[0]
        assert args == ("COVER", "BnF loan metadata", "ok")
        detail = kwargs["detail"]
        assert detail == dict(
            provider_id="bnf-pret", credential_id=account.credential_id,
            account_id=account.unique_id, identity_id="hugues",
            phase="provider_projection", coverage="partial", observed_count=2,
            loans=[dict(loan_id="loan-future", resource_id="future",
                        title="2050, une Histoire du Futur",
                        thumbnail_url="https://pret.bnf.fr/covers/future.jpg",
                        thumbnail_status="observed"),
                   dict(loan_id="loan-dragon", resource_id="dragon",
                        title="La promesse du dragon", thumbnail_url=None,
                        thumbnail_status="unknown")],
        )
    finally:
        s.close()


def test_absent_broker_sink_uses_one_structured_canonical_journal_event(caplog):
    caplog.set_level(logging.INFO, logger="cifereca")
    s, account, _ = connected()
    try:
        s.loans(account)
        event, = projection_events(caplog.records)
        assert event['detail']['observed_count'] == 2
        assert event['detail']['loans'][0]['thumbnail_url'].endswith('/covers/future.jpg')
        assert event['detail']['loans'][1]['thumbnail_status'] == 'unknown'
        assert event['detail']['identity_id'] == 'hugues'
    finally:
        s.close()


def test_empty_partial_inventory_is_observed_without_claiming_any_absence():
    s, account, _ = connected('<section class="active-loans"></section>')
    events = []
    s.diagnostics = NS(add=lambda *args, **kwargs: events.append(kwargs['detail']))
    try:
        result = s.loan_inventory_snapshot(account)
        assert result.status == 'partial' and result.loans == ()
        assert events[0]['loans'] == []
        assert events[0]['observed_count'] == 0
        assert events[0]['coverage'] == 'partial'
    finally:
        s.close()


def test_failing_optional_sink_does_not_erase_positive_loans():
    s, account, _ = connected()

    def fail(*args, **kwargs):
        raise RuntimeError('synthetic sink failure')

    s.diagnostics = NS(add=fail)
    try:
        result = s.loan_inventory_snapshot(account)
        assert [loan.id for loan in result.loans] == ['loan-future', 'loan-dragon']
        assert result.loans[0].thumbnail_url == 'https://pret.bnf.fr/covers/future.jpg'
    finally:
        s.close()


@pytest.mark.parametrize("change", ["identity", "epoch", "broker", "close"])
def test_diagnostic_callback_cannot_publish_rebound_loans_or_token_eligibility(change):
    s, account, calls = connected()

    def rebound(*args, **kwargs):
        if change == 'identity':
            account.identity_id = 'magali'
        elif change == 'epoch':
            s._epoch += '-reconnected'
        elif change == 'broker':
            s.identity_broker = NS(accounts=s.identity_broker.accounts)
        else:
            s.close()

    s.diagnostics = NS(add=rebound)
    try:
        with pytest.raises(ProviderConfigurationError):
            s.loans(account)
        assert s._observed_loans == {}
        assert not any(path.startswith(("/covers/", "/loans/")) for _, _, path in calls)
    finally:
        s.close()


def test_signed_actions_from_same_page_are_not_added_to_metadata_event():
    html = ACTIVITY.replace(
        '<a href="/loans/loan-future">Fichier</a>',
        '<a href="/loans/loan-future?device_type=reader&amp;drm=acs4&amp;'
        'token=synthetic-secret">Fichier</a>',
    )
    s, account, _ = connected(html)
    events = []
    s.diagnostics = NS(add=lambda *args, **kwargs: events.append(kwargs['detail']))
    try:
        assert len(s.loans(account)) == 2
        assert 'synthetic-secret' not in json.dumps(events)
        assert events[0]['loans'][0]['loan_id'] == 'loan-future'
    finally:
        s.close()
