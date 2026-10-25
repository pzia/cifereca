"""#3135: only login and password values are pseudonymized in GTK."""
from __future__ import annotations

from diagnostics_pseudonyms import DiagnosticsPseudonyms
from cifereca_diagnostics_page import redacted_detail, _redacted_text
from diagnostics_explorer import fetch_explorer_page, sanitized_rows


def test_exact_fields_are_stable_and_every_other_field_visible():
    renderer = DiagnosticsPseudonyms()
    first = renderer.text(
        'login=alice@example.test password="very-secret" '
        'token=TOKEN cookie=COOKIE bearer=BEARER '
        'account_id=42 title="Les Trois Mousquetaires" '
        'url=https://bnf.fr/LOAN/123?token=VISIBLE'
    )
    assert 'login=login-1' in first
    assert 'password="mot-de-passe-1"' in first
    for fragment in ('token=TOKEN', 'cookie=COOKIE', 'bearer=BEARER',
                     'account_id=42', 'Les Trois Mousquetaires',
                     'https://bnf.fr/LOAN/123?token=VISIBLE'):
        assert fragment in first
    assert renderer.text('login=alice@example.test password="very-secret"').endswith(
        'password="mot-de-passe-1"'
    )
    assert renderer.text('login=bob password=new').startswith('login=login-2 password=mot-de-passe-2')
    assert renderer.text(first) == first


def test_structured_detail_masks_only_two_explicit_values():
    row = redacted_detail({
        'login': 'alice@example.test',
        'password': 'secret',
        'cookie': 'keep-cookie',
        'token': 'keep-token',
        'path': '/home/user/file.acsm',
        'title': 'Livre original',
        'email': 'display@example.test',
        'nested': {'username': 'another-user', 'authorization': 'Bearer KEEP'},
    })
    assert row['login'].startswith('login-')
    assert row['password'].startswith('mot-de-passe-')
    assert row['cookie'] == 'keep-cookie'
    assert row['token'] == 'keep-token'
    assert row['path'] == '/home/user/file.acsm'
    assert row['title'] == 'Livre original'
    assert row['email'] == 'display@example.test'
    assert row['nested']['authorization'] == 'Bearer KEEP'
    assert row['nested']['username'].startswith('login-')


def test_plain_credentials_without_label_are_not_rewritten():
    assert _redacted_text('alice@example.test token=visible') == 'alice@example.test token=visible'


def test_global_search_goes_to_core_before_pagination():
    class Broker:
        def __init__(self):
            self.query = None
        def events(self, **kwargs):
            self.query = kwargs
            return {'events': [], 'total': 0, 'offset': 0,
                    'sources': [{'source': 'journal', 'status': 'empty'}]}
    broker = Broker()
    fetch_explorer_page(broker, source='journal', period='all', search='Réconciliation 403', offset=450)
    assert broker.query['search'] == 'Réconciliation 403'
    assert broker.query['source'] == 'journal'
    assert broker.query['offset'] == 450
    assert broker.query['since'] is None
