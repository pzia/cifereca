"""CoreUI target action: no preparation/download on render, no stale callbacks."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.local_book_action_controller import add_local_book_actions
from tests.test_core_ui_1129_local_book_actions import _environment


def setup_action(provider="kobo", operation=None):
    window, entry, detail, actions, events, submissions, launched, deps = _environment(
        item={"provider_id": "kobo", "item_id": "book", "possessions": []}, local=False)
    target = NS(identity_id="alice", provider_id=provider, operation=operation)
    window.service.owned_library_download_target = lambda entry: target
    calls = []
    window.service.obtain_owned_library_item = lambda requested: calls.append(requested) or NS(path="fake-path")
    add_local_book_actions(window, detail, entry, **deps)
    return window, actions, calls, submissions, target


def test_unlinked_remote_purchase_shows_explicit_manual_download_refusal():
    """#3848: a Numilog remote card must not silently hide absent Core linkage."""
    window, entry, detail, actions, events, submissions, _, deps = _environment(
        item={'provider_id': 'numilog', 'item_id': 'synthetic-owned',
              'source_credential': 'card', 'state': 'remote_owned',
              'possessions': []},
        local=False,
    )
    groups = []
    detail.add_group = lambda title, rows: groups.append((title, rows))
    window.service.owned_library_download_target = lambda item: None
    add_local_book_actions(window, detail, entry, **deps)
    assert groups == [
        ('Acquisition', [
            ('Téléchargement manuel',
             'Indisponible : aucune cible exacte vérifiée pour ce compte et cette identité.'),
        ]),
    ]
    assert actions == [] and submissions == []


def test_untrusted_target_failure_is_closed_and_never_leaks_into_detail():
    """A provider/SQLite target error must not abort rendering or leak secrets."""
    private = 'PRIVATE_https://example.invalid/download?token=PRIVATE'
    window, entry, detail, actions, events, submissions, _, deps = _environment(
        item={'provider_id': 'numilog', 'item_id': 'synthetic-owned',
              'source_credential': 'card', 'state': 'remote_owned'},
        local=False,
    )
    groups, diagnostics = [], []
    detail.add_group = lambda title, rows: groups.append((title, rows))
    window.diagnostics = NS(add=lambda *args, **kwargs:
                            diagnostics.append((args, kwargs)))
    def unavailable(_item):
        raise RuntimeError(private)
    window.service.owned_library_download_target = unavailable
    add_local_book_actions(window, detail, entry, **deps)
    assert groups and diagnostics == [
        (('UI', 'owned download target', 'error'),
         {'detail': {'code': 'owned_download_target_unavailable'}}),
    ]
    assert private not in repr(groups) + repr(diagnostics)
    assert not actions and not submissions


@pytest.mark.parametrize('state', ['remote_catalog', 'local', None])
def test_public_and_local_notices_do_not_claim_missing_acquisition(state):
    window, entry, detail, actions, events, submissions, _, deps = _environment(
        item={'provider_id': 'numilog-catalog', 'item_id': 'public',
              'state': state}, local=False,
    )
    groups = []
    detail.add_group = lambda *args: groups.append(args)
    window.service.owned_library_download_target = lambda _item: None
    add_local_book_actions(window, detail, entry, **deps)
    assert groups == [] and actions == [] and submissions == []


def test_render_is_passive_and_only_explicit_click_submits_one_book():
    window, actions, calls, submissions, target = setup_action()
    assert len(actions) == 1 and actions[0]._cifereca_action_id == "download-owned-kobo"
    assert not calls and not submissions
    actions[0].click()
    args, kwargs = submissions[0]
    result = args[1]()
    args[2](result)
    assert calls == [target] and kwargs["kind"] == "obtain"


@pytest.mark.parametrize("when", ["before_click", "before_worker", "closed", "detached", "hidden", "identity_roundtrip"])
def test_stale_identity_closed_window_or_detached_action_never_downloads(when):
    window, actions, calls, submissions, _target = setup_action()
    if when == "before_click":
        window.service.current_identity = NS(identity_id="bob")
    elif when == "closed":
        window._closed = True
    elif when == "detached":
        actions[0].get_root = lambda: None
    elif when == "hidden":
        actions[0].get_mapped = lambda: False
    elif when == "identity_roundtrip":
        window._identity_context_generation = 2
    actions[0].click()
    if when == "before_worker":
        window.service.current_identity = NS(identity_id="bob")
        with pytest.raises(RuntimeError, match="contexte"):
            submissions[0][0][1]()
    else:
        assert not submissions
    assert not calls


def test_worker_and_failure_callback_never_publish_private_provider_errors():
    window, actions, _calls, submissions, _target = setup_action()
    def fail(target):
        raise ValueError("Bearer PRIVATE query /private/path")
    window.service.obtain_owned_library_item = fail
    actions[0].click()
    args, _kwargs = submissions[0]
    with pytest.raises(RuntimeError) as caught:
        args[1]()
    assert "PRIVATE" not in str(caught.value)



def test_numilog_action_is_passive_until_explicit_click_and_keeps_exact_target():
    window, actions, calls, submissions, target = setup_action('numilog')
    assert len(actions) == 1 and actions[0]._cifereca_action_id == 'download-owned-numilog'
    assert calls == [] and submissions == []
    actions[0].click()
    args, kwargs = submissions[0]
    result = args[1]()
    args[2](result)
    assert calls == [target] and kwargs['kind'] == 'obtain'
    assert kwargs['origin'] == 'user'
    assert 'Numilog' in args[0]


def test_numilog_stale_identity_never_submits_acquisition():
    window, actions, calls, submissions, target = setup_action('numilog')
    window._identity_context_generation = 2
    actions[0].click()
    assert calls == [] and submissions == []


def test_resume_action_only_explicit_click_indexes_without_claiming_download():
    window, actions, calls, submissions, target = setup_action('numilog', 'resume_index')
    button = actions[0]
    assert button._cifereca_action_id == 'resume-index-numilog'
    assert 'indexation' in button.label and 'sans téléchargement' in button.tooltip
    assert not calls and not submissions
    button.click()
    args, kwargs = submissions[0]
    args[2](args[1]())
    assert calls == [target] and kwargs['kind'] == 'index'
    assert kwargs['origin'] == 'user'


@pytest.mark.parametrize('mutation', ['identity', 'generation', 'hidden', 'detached', 'closed'])
def test_resume_action_never_submits_stale_or_hidden_context(mutation):
    window, actions, calls, submissions, target = setup_action('numilog', 'resume_index')
    if mutation == 'identity':window.service.current_identity = NS(identity_id='bob')
    if mutation == 'generation':window._identity_context_generation = 1
    if mutation == 'hidden':actions[0].get_mapped = lambda:False
    if mutation == 'detached':actions[0].get_root = lambda:None
    if mutation == 'closed':window._closed = True
    actions[0].click()
    assert not calls and not submissions


def test_resume_worker_preserves_cancellation_without_success_callback():
    from cifereca.plugin_api import Cancelled
    window, actions, calls, submissions, target = setup_action('numilog', 'resume_index')
    def cancelled(_target):raise Cancelled()
    window.service.obtain_owned_library_item = cancelled
    actions[0].click()
    with pytest.raises(Cancelled):submissions[0][0][1]()
    assert not calls


@pytest.mark.parametrize(("code", "label"), [
    ("manual_receipt_missing", "Reçu d’import ACSM d’origine introuvable"),
    ("artifact_shared_elsewhere", "Même empreinte déjà utilisée"),
    ("catalogue_conflict", "Notice du catalogue contradictoire"),
    ("adoption_unavailable", "Validation du placement d’origine indisponible"),
    ("acquisition_busy", "détient le verrou"),
    ("index_rejected", "catalogue a refusé"),
    ("indexing_failed", "indexation locale a échoué"),
    ("finalization_failed", "finalisation du reçu a échoué"),
])
def test_p0_numilog_resume_ui_shows_only_closed_sqlite_refusal(code, label):
    from cifereca.core.numilog_materialization_checkpoint import (
        NumilogIndexResumeRejected,
    )
    window, actions, calls, submissions, target = setup_action("numilog", "resume_index")
    visible = []
    window.show_error = lambda message, title: visible.append((message, title))
    def denied(_target):
        raise NumilogIndexResumeRejected(code)
    window.service.obtain_owned_library_item = denied
    actions[0].click()
    args, kwargs = submissions[0]
    with pytest.raises(NumilogIndexResumeRejected) as raised:
        args[1]()
    args[3](raised.value)
    assert len(visible) == 1
    assert label in visible[0][0]
    assert visible[0][1] == "Reprise d’indexation Numilog"
    assert kwargs["kind"] == "index"
    assert calls == []


def test_p0_numilog_resume_ui_keeps_private_errors_redacted():
    window, actions, calls, submissions, target = setup_action("numilog", "resume_index")
    messages = []
    window.show_error = lambda message, title: messages.append(message)
    def denied(_target):
        raise ValueError("https://private.invalid/?token=PRIVATE /home/PRIVATE")
    window.service.obtain_owned_library_item = denied
    actions[0].click()
    args, _kwargs = submissions[0]
    with pytest.raises(RuntimeError) as raised:
        args[1]()
    args[3](raised.value)
    assert len(messages) == 1
    assert messages[0].startswith("Reprise d’indexation indisponible.")
    assert "PRIVATE" not in str(raised.value) and "PRIVATE" not in messages[0]
    assert calls == []
