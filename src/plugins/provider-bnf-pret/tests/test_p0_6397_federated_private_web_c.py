"""Real BnF parser -> existing federated Search -> exact private C -> click."""
from dataclasses import asdict, replace
from copy import deepcopy

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.models import SearchResult
from cifereca.core.notice_c_private_search_web import (
    capture_authenticated_search_call, stamp_authenticated_search_rows,
    persist_authenticated_search_rows, read_authenticated_web_for_search,
)
from cifereca.core.notice_c_authenticated_web_choices import resolve_authenticated_web_choice
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.search_orchestration import capture_search_read_scope
from test_p0_6397_authenticated_web_proof import active, card, results, ORIGIN


@pytest.fixture
def harness(tmp_path, monkeypatch):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "library"))
    config.set("identities", [{"id": "alice", "label": "Alice"},
                              {"id": "bob", "label": "Bob"}])
    config.set("providers.accounts", [])
    service = LibraryService(config, Storage(tmp_path / "private.sqlite"), Diagnostics())
    session, account, client = active(results(card()))
    service.accounts = [account]
    service._sessions = {service._session_key(account.provider_id, account.credential_id): session}
    monkeypatch.setattr(service, "_provider_enabled_for_identity", lambda p, i=None: True)
    db = service.storage
    connection = db.provider_connection_put({
        "identity_id": "alice", "provider_id": account.provider_id,
        "credential_id": account.credential_id, "enabled": True,
    })
    db.provider_account_put({
        "connection_id": connection, "identity_id": "alice", "provider_id": account.provider_id,
        "credential_id": account.credential_id, "account_id": account.unique_id, "is_primary": True,
    })
    try:
        yield service, session, account, client
    finally:
        service.close()


@pytest.mark.parametrize("mode", ["sync", "progressive", "headless"])
def test_actual_federated_owner_search_admits_only_its_private_reference(harness, mode):
    service, session, account, client = harness
    assert service.current_identity.identity_id == "alice"
    scope = capture_search_read_scope(service, "alice")
    if mode == "sync":
        row, = service.search("fiction", provider_id=session.provider_id, force=True)
    elif mode == "progressive":
        batches = []
        def receive(batch):
            actual, = batch.results
            assert read_authenticated_web_for_search(service, actual, reader_scope=scope).choices
            batches.append(actual)
        summary = service.search_progressive("fiction", provider_id=session.provider_id,
                                             on_batch=receive, force=True)
        assert not summary.cancelled and summary.result_count == 1
        row, = batches
    else:
        summary, rows = service.search_progressive_result_for_scope(
            scope, "fiction", provider_id=session.provider_id, force=True,
        )
        assert not summary.cancelled and summary.result_count == 1
        row, = rows
    assert row.source_account_id == account.unique_id
    menu = read_authenticated_web_for_search(service, row, reader_scope=scope)
    choice, = menu.choices
    url = ORIGIN + "/resources/" + row.record_id
    assert resolve_authenticated_web_choice(service, menu, choice_id=choice.choice_id) == url
    c = read_consolidated_notice_cover(service.storage, menu.document)
    assert len(c.sources) == 1 and c.sources[0].visibility == "private"
    assert c.catalogue_web_references == () and c.candidates == ()
    assert search_public_notice_c(service.storage, "fiction").hits == ()
    # A detached view/cache row cannot republish a fresh observation; it
    # can only read the independently persisted exact source after scope checks.
    cached = SearchResult(**asdict(row))
    assert persist_authenticated_search_rows(service, [cached], identity_id="alice") == 0
    cached.friendly_url = ORIGIN + "/resources/wrong"
    saved = read_authenticated_web_for_search(service, cached, reader_scope=scope)
    assert resolve_authenticated_web_choice(service, saved, choice_id=saved.choices[0].choice_id) == url
    assert read_authenticated_web_for_search(
        service, row, reader_scope=capture_search_read_scope(service, "bob"),
    ) is None
    assert len(client.calls) == 1
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []


@pytest.mark.parametrize("changed", [
    "duplicate", "copy", "deepcopy", "proof", "row", "scope", "session", "connection",
])
def test_no_private_write_from_forged_or_stale_occurrence(harness, changed):
    service, session, account, client = harness
    call = capture_authenticated_search_call(service, session, "alice")
    assert call is not None
    row, = session.search("fiction")
    rows = [row, replace(row)] if changed == "duplicate" else [row]
    stamp_authenticated_search_rows(service, call, rows)
    if changed == "copy":
        rows = [replace(row)]
    elif changed == "deepcopy":
        rows = [deepcopy(row)]
    elif changed == "proof":
        row.metadata["authenticated_web_evidence"]["source_field"] = "other"
    elif changed == "row":
        row.friendly_url = ORIGIN + "/resources/other"
    elif changed == "scope":
        service._source_context_epoch += 2
    elif changed == "session":
        session.connected = False
    elif changed == "connection":
        service.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": account.provider_id,
            "credential_id": account.credential_id, "enabled": False,
        })
    assert persist_authenticated_search_rows(service, rows, identity_id="alice") == 0
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute("SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.%'").fetchone()[0] == 0
    assert len(client.calls) == 1


@pytest.mark.parametrize("batch", ["oversized_first", "oversized_last", "tuple"])
def test_invalid_new_batch_revokes_stale_private_web_receipt(harness, batch):
    service, session, _, client = harness
    call = capture_authenticated_search_call(service, session, "alice")
    assert call is not None
    row, = session.search("fiction")
    stamp_authenticated_search_rows(service, call, [row])
    assert "_core_private_web_search_receipt" in row.__dict__

    if batch == "tuple":
        invalid = (row,)
    else:
        invalid = [None] * 10001
        invalid[0 if batch == "oversized_first" else -1] = row
    # A fresh call was refused as malformed/over-budget. The old row
    # must not remain publishable if the caller mistakenly reuses it.
    stamp_authenticated_search_rows(service, call, invalid)
    assert "_core_private_web_search_receipt" not in row.__dict__
    assert persist_authenticated_search_rows(service, [row], identity_id="alice") == 0
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute(
            "SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.%'"
        ).fetchone()[0] == 0
    assert len(client.calls) == 1
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []


@pytest.mark.parametrize("damage", [
    "metadata_key", "evidence_key", "metadata_oversized",
])
def test_non_native_private_web_evidence_never_stamps_or_persists(harness, damage):
    service, session, _, client = harness
    call = capture_authenticated_search_call(service, session, "alice")
    row, = session.search("fiction")

    class ForeignKey:
        def __hash__(self):
            return hash("synthetic.unrelated.key.for.private.web")

        def __eq__(self, other):
            raise AssertionError("provider-defined dictionary key comparison")

    if damage == "metadata_key":
        row.metadata[ForeignKey()] = "synthetic"
    elif damage == "evidence_key":
        row.metadata["authenticated_web_evidence"][ForeignKey()] = "synthetic"
    else:
        row.metadata.update({f"extra-field-{i}": "untrusted" for i in range(257)})
    stamp_authenticated_search_rows(service, call, [row])
    assert "_core_private_web_search_receipt" not in row.__dict__
    assert persist_authenticated_search_rows(service, [row], identity_id="alice") == 0
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute(
            "SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.%'"
        ).fetchone()[0] == 0
    assert len(client.calls) == 1
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []


@pytest.mark.parametrize("invalid", ["tuple", "oversized", "iterator"])
def test_private_writer_rejects_nonadmitted_batch_even_with_valid_old_receipt(
    harness, invalid,
):
    service, session, _, client = harness
    call = capture_authenticated_search_call(service, session, "alice")
    row, = session.search("fiction")
    stamp_authenticated_search_rows(service, call, [row])
    assert "_core_private_web_search_receipt" in row.__dict__
    if invalid == "tuple":
        batch = (row,)
    elif invalid == "oversized":
        batch = [row] * 10001
    else:
        batch = iter([row])
    assert persist_authenticated_search_rows(
        service, batch, identity_id="alice",
    ) == 0
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute(
            "SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.%'"
        ).fetchone()[0] == 0
    # A valid batch is still allowed to commit the same Core-attested row.
    assert persist_authenticated_search_rows(
        service, [row], identity_id="alice",
    ) == 1
    assert len(client.calls) == 1
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []


@pytest.mark.parametrize("damage", [
    "row_id", "key", "key_provider", "proof", "proof_member",
    "proof_oversized", "call_scope", "scope_identity", "scope_epoch",
    "scope_service", "call_binding", "binding_descriptor",
    "call_observed", "identity",
])
def test_private_c_writer_refuses_modified_receipt_without_external_callbacks(
    harness, damage,
):
    from dataclasses import replace

    service, session, _, client = harness
    call = capture_authenticated_search_call(service, session, "alice")
    row, = session.search("fiction")
    stamp_authenticated_search_rows(service, call, [row])
    packet = row.__dict__["_core_private_web_search_receipt"]

    class Hostile:
        def __eq__(self, other):
            raise AssertionError("external private C receipt equality")

        def __hash__(self):
            raise AssertionError("external private C receipt hash")

        def current(self, *args):
            raise AssertionError("external private C current callback")

    if damage == "row_id":
        changed = replace(packet, row_id=Hostile())
    elif damage == "key":
        changed = replace(packet, key=Hostile())
    elif damage == "key_provider":
        changed = replace(packet, key=replace(packet.key, provider_id=Hostile()))
    elif damage == "proof":
        changed = replace(packet, proof=Hostile())
    elif damage == "proof_member":
        changed = replace(packet, proof=(Hostile(), *packet.proof[1:]))
    elif damage == "proof_oversized":
        changed = replace(packet, proof=("x" * 513, *packet.proof[1:]))
    elif damage == "call_scope":
        changed = replace(packet, call=replace(packet.call, scope=Hostile()))
    elif damage == "scope_identity":
        changed = replace(packet, call=replace(
            packet.call,
            scope=replace(packet.call.scope, identity_id=Hostile()),
        ))
    elif damage == "scope_epoch":
        changed = replace(packet, call=replace(
            packet.call,
            scope=replace(packet.call.scope, source_epoch=Hostile()),
        ))
    elif damage == "scope_service":
        changed = replace(packet, call=replace(
            packet.call,
            scope=replace(packet.call.scope, service=Hostile()),
        ))
    elif damage == "call_binding":
        changed = replace(packet, call=replace(packet.call, binding=Hostile()))
    elif damage == "binding_descriptor":
        changed = replace(packet, call=replace(
            packet.call,
            binding=replace(packet.call.binding, descriptor=Hostile()),
        ))
    elif damage == "call_observed":
        changed = replace(packet, call=replace(packet.call, observed_at=Hostile()))
    else:
        changed = packet

    row.__dict__["_core_private_web_search_receipt"] = changed
    identity = Hostile() if damage == "identity" else "alice"
    assert persist_authenticated_search_rows(
        service, [row], identity_id=identity,
    ) == 0
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []
    row.__dict__["_core_private_web_search_receipt"] = packet
    assert persist_authenticated_search_rows(
        service, [row], identity_id="alice",
    ) == 1
    assert len(client.calls) == 1
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []


def test_private_web_catalogue_access_hostile_value_is_not_a_c_receipt(harness):
    service, session, _, client = harness
    call = capture_authenticated_search_call(service, session, "alice")
    row, = session.search("fiction")

    class Hostile:
        def __eq__(self, other):
            raise AssertionError("provider metadata equality")

    row.metadata["catalogue_access"] = Hostile()
    stamp_authenticated_search_rows(service, call, [row])
    assert "_core_private_web_search_receipt" not in row.__dict__
    assert persist_authenticated_search_rows(service, [row], identity_id="alice") == 0
    assert len(client.calls) == 1
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []


def test_private_call_cannot_be_captured_as_foreign_reader(harness):
    service, session, _, client = harness
    assert capture_authenticated_search_call(service, session, "bob") is None
    assert not client.calls


def test_private_sql_failure_rolls_back_and_exact_receipt_can_retry(harness, monkeypatch):
    from sqlite3 import OperationalError
    from cifereca.core import notice_c_private_search_web as writer
    service, session, _, client = harness
    call = capture_authenticated_search_call(service, session, "alice")
    row, = session.search("fiction")
    stamp_authenticated_search_rows(service, call, [row])
    real = writer._persist_consolidated_cover_source_txn
    attempted = []

    def fail_after_source(db, **kw):
        # The source was already staged in the real SQLite transaction.
        attempted.append(kw["source"])
        raise OperationalError("synthetic write failure")

    with monkeypatch.context() as patch:
        patch.setattr(writer, "_persist_consolidated_cover_source_txn", fail_after_source)
        assert persist_authenticated_search_rows(service, [row], identity_id="alice") == 0
    assert len(attempted) == 1
    scope = capture_search_read_scope(service, "alice")
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute("SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.%'").fetchone()[0] == 0
    assert writer._persist_consolidated_cover_source_txn is real
    assert persist_authenticated_search_rows(service, [row], identity_id="alice") == 1
    menu = read_authenticated_web_for_search(service, row, reader_scope=scope)
    assert len(menu.choices) == 1 and len(client.calls) == 1
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []


def test_optional_private_sql_failure_does_not_hide_live_search_result(harness, monkeypatch):
    from sqlite3 import OperationalError
    from cifereca.core import notice_c_private_search_web as writer
    service, session, account, client = harness
    attempted = []

    def fail_after_source(db, **kw):
        attempted.append(kw["source"])
        raise OperationalError("synthetic write failure")

    monkeypatch.setattr(writer, "_persist_consolidated_cover_source_txn", fail_after_source)
    row, = service.search("fiction", provider_id=session.provider_id, force=True)
    assert row.source_account_id == account.unique_id
    assert len(attempted) == 1 and len(client.calls) == 1
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute("SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.%'").fetchone()[0] == 0
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []


def test_private_commit_failure_never_counts_rolled_back_publication(harness, monkeypatch):
    from contextlib import contextmanager
    from sqlite3 import OperationalError
    service, session, _, client = harness
    call = capture_authenticated_search_call(service, session, "alice")
    assert call is not None
    row, = session.search("fiction")
    stamp_authenticated_search_rows(service, call, [row])
    storage = service.storage
    real_connect = storage._connect
    attempted = []

    @contextmanager
    def fail_at_transaction_exit():
        conn = real_connect()
        yield conn
        # All Core writes have already succeeded; emulate a rejected SQLite
        # commit before any staged row can become durable. Storage owns this
        # reusable connection; closing it would invalidate the later retry.
        attempted.append("commit")
        conn.rollback()
        raise OperationalError("controlled commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(storage, "_connect", fail_at_transaction_exit)
        assert persist_authenticated_search_rows(service, [row], identity_id="alice") == 0
    assert attempted == ["commit"]
    with storage._lock, storage._connect() as db:
        assert db.execute("SELECT count(*) FROM metadata WHERE key LIKE 'notice.c.%'").fetchone()[0] == 0
    # Reuse the exact same Core receipt; do not relaunch Search/GET.
    assert persist_authenticated_search_rows(service, [row], identity_id="alice") == 1
    scope = capture_search_read_scope(service, "alice")
    choices = read_authenticated_web_for_search(service, row, reader_scope=scope)
    assert choices is not None and len(choices.choices) == 1
    assert len(client.calls) == 1
    assert storage.entitlements() == storage.acquisitions() == storage.placements() == []


def test_private_parse_witness_actual_http_search_approval_and_guard(harness, monkeypatch, tmp_path):
    """Q5–Q7: existing federated path, explicit approval/replay, durable owner-only alert."""
    from hashlib import sha256
    from pathlib import Path
    from types import SimpleNamespace as NS
    import json
    import zipfile
    import httpx

    from cifereca_bnf_pret_http import ReaderHTTPClient
    from cifereca.core.notice_c_private_search_web import _source_key
    from cifereca.core.notice_cover_projection import notice_cover_document_ref
    from cifereca.core.notice_cover_repository import read_notice_cover
    from cifereca.core.notice_parse_witness_approval import (
        private_search_witness, private_search_witness_diagnostic,
        replay_private_search_witness,
    )
    from cifereca.core.notice_parse_witness_store import read_approved_parse_witness
    from cifereca.gui.notice_parse_witness_controller import request_selected_parse_witness

    service, session, account, _ = harness
    body = results(card(extra='<span itemprop="author">Auteur privé</span>')).encode("utf-8")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/resources"
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content=body)
    session.client = ReaderHTTPClient(transport=httpx.MockTransport(handler))
    service._notice_regression_capture_directory = tmp_path / "private-captures"
    scope = capture_search_read_scope(service, "alice")
    row, = service.search("fiction", provider_id=session.provider_id, force=True)
    assert row.author == "Auteur privé"
    key = _source_key(row, "alice")
    doc = notice_cover_document_ref(key)
    source = read_notice_cover(service.storage, doc)
    assert source.observation.fields_copy()["private_bibliography"]["author"] == row.author
    proposal = private_search_witness(service, row, scope=scope)
    assert proposal is not None and proposal.receipt[0] == sha256(body).hexdigest()
    assert tuple(f.path[-1] for f in proposal.fields) == ("title", "author")
    def baseline():
        return read_approved_parse_witness(
            service.storage, key=key, parser_id=source.observation.parser_id,
        )
    assert baseline() is None
    assert private_search_witness(service, replace(row), scope=scope) is None
    assert private_search_witness(service, row, scope=capture_search_read_scope(service, "bob")) is None

    class Button:
        def __init__(self, label): self.label = label
        def connect(self, signal, callback): self.callback = callback
        def set_sensitive(self, value): pass

    actions, pending, toasts = [], [], []
    cell = NS(key="private-selected")
    panel = NS(add_actions=actions.extend, get_mapped=lambda: True)
    host = NS(service=service, _route_display_token=object(),
              search_page=NS(detail=panel, selection=NS(get_selected_item=lambda: cell)),
              search_objects={cell.key: row}, _search_detail_context_current=lambda *args: True,
              runner=NS(submit=lambda label, work, done, failed, **kw:
                        pending.append((work, done))), toast=toasts.append)
    def deliver():
        work, done = pending.pop(0)
        done(work())
    assert request_selected_parse_witness(
        host, row, selected_id=row.id, context=(1, 1, 1, "alice"), Gtk=NS(Button=Button),
    )
    deliver()
    button, = actions
    assert button._cifereca_action_id == "approve-private-parse-witness"
    assert baseline() is None
    button.callback(button)
    deliver()
    assert baseline().generation == 1
    assert baseline().baseline.approved_input_sha256 == sha256(body).hexdigest()
    assert len(calls) == 1
    assert not session._private_replay_pages
    assert not replay_private_search_witness(service, row, scope=scope, check_only=True)
    assert not replay_private_search_witness(service, row, scope=scope)

    # Explicit per-account opt-in only; a fresh authorized Search retains
    # its bounded original page in this session, never in Storage or a ZIP.
    from cifereca_bnf_pret import DESCRIPTOR
    opt_in, = [option for option in DESCRIPTOR.account_options
               if option.key == "private_search_replay"]
    assert opt_in.default == "off" and opt_in.value_type == "boolean"
    session.options["private_search_replay"] = "on"
    row, = service.search("fiction", provider_id=session.provider_id, force=True)
    assert len(calls) == 2
    assert session.private_search_replay(row, sha256(body).hexdigest(), check_only=True) is True
    assert replay_private_search_witness(service, row, scope=scope, check_only=True)
    assert not replay_private_search_witness(
        service, row, scope=capture_search_read_scope(service, "bob"),
    )
    assert not replay_private_search_witness(service, replace(row), scope=scope)
    actions.clear()
    groups = []
    panel.add_group = lambda title, values: groups.append((title, values))
    host.search_objects[cell.key] = row
    assert request_selected_parse_witness(
        host, row, selected_id=row.id, context=(2, 1, 1, "alice"), Gtk=NS(Button=Button),
    )
    deliver()
    replay_button, = [action for action in actions
                      if action._cifereca_action_id == "replay-private-parse-witness"]
    assert not groups

    # Simulate an installed parser/projection upgrade on the same HTTP input.
    # Only author disappears; the exact BnF record and account remain valid.
    original = session._search_result
    monkeypatch.setattr(session, "_search_result",
                        lambda record: replace(original(record), author=None))
    read_bytes = Path.read_bytes
    def upgraded(path):
        content = read_bytes(path)
        return (content + b"\n# synthetic upgrade" if path.name == "cifereca_bnf_pret_web.py"
                else content)
    monkeypatch.setattr(Path, "read_bytes", upgraded)
    replay_button.callback(replay_button)
    deliver()
    assert len(calls) == 2
    assert toasts[-1] == "Rejeu local terminé."
    replay_alert = private_search_witness_diagnostic(service, row, scope=scope)
    assert replay_alert["status"] == "parser_regression_confirmed"
    assert replay_alert["fields"] == ("author",)
    assert replay_alert["capture_state"] == "partial"
    assert read_notice_cover(service.storage, doc) == source
    assert groups[-1][0] == "Parsing : régression possible"
    replay_archive, = (tmp_path / "private-captures").glob("*.zip")
    rejected, = service.search("fiction", provider_id=session.provider_id, force=True)
    diagnostic = private_search_witness_diagnostic(service, rejected, scope=scope)
    assert diagnostic is not None
    assert diagnostic["status"] == "parser_regression_confirmed"
    assert diagnostic["fields"] == ("author",)
    assert diagnostic["capture_state"] == "partial"
    assert read_notice_cover(service.storage, doc) == source
    assert private_search_witness(service, rejected, scope=scope) is None
    assert private_search_witness_diagnostic(
        service, rejected, scope=capture_search_read_scope(service, "bob"),
    ) is None
    archive, = (tmp_path / "private-captures").glob("*.zip")
    assert archive == replay_archive
    with zipfile.ZipFile(archive) as capture:
        manifest = json.loads(capture.read("manifest.json"))
        assert set(manifest["omitted"]) == {"response.raw", "current_parse.json", "last_healthy.json"}
        assert all("Auteur privé".encode() not in capture.read(name) for name in capture.namelist())
    again, = service.search("fiction", provider_id=session.provider_id, force=True)
    assert private_search_witness_diagnostic(service, again, scope=scope)["status"] == "parser_regression_confirmed"
    assert list((tmp_path / "private-captures").glob("*.zip")) == [archive]
    assert len(calls) == 4
    assert replay_private_search_witness(service, again, scope=scope, check_only=True)
    session._private_replay_deadline = 0
    assert not replay_private_search_witness(service, again, scope=scope)
    assert not session._private_replay_pages and len(calls) == 4
    assert search_public_notice_c(service.storage, "fiction").hits == ()
    assert service.storage.entitlements() == service.storage.acquisitions() == service.storage.placements() == []
    session.connected = False
    assert private_search_witness(service, row, scope=scope, approved=proposal) is None
    assert private_search_witness_diagnostic(service, again, scope=scope) is None

    # Reopen the actual SQLite database with a new service and session. A
    # restored Search DTO contains no transient producer receipt. It can
    # inspect the durable alert, never approve or republish it.
    restored = SearchResult(**asdict(again))
    assert "_core_private_web_search_receipt" not in restored.__dict__
    assert "_core_private_notice_parse_input" not in restored.__dict__
    reopened = LibraryService(service.config, Storage(service.storage.path), Diagnostics())
    fresh_session, fresh_account, fresh_client = active(results(card()))
    reopened.accounts = [fresh_account]
    reopened._sessions = {
        reopened._session_key(fresh_account.provider_id, fresh_account.credential_id): fresh_session
    }
    monkeypatch.setattr(reopened, "_provider_enabled_for_identity", lambda *args: True)
    try:
        fresh_scope = capture_search_read_scope(reopened, "alice")
        assert private_search_witness(reopened, restored, scope=fresh_scope) is None
        assert not replay_private_search_witness(reopened, restored, scope=fresh_scope)
        assert private_search_witness_diagnostic(reopened, restored, scope=fresh_scope) == diagnostic
        assert private_search_witness_diagnostic(
            reopened, restored, scope=capture_search_read_scope(reopened, "bob"),
        ) is None
        assert private_search_witness_diagnostic(
            reopened, replace(restored, record_id="foreign-record"), scope=fresh_scope,
        ) is None
        actions.clear()
        groups = []
        panel.add_group = lambda title, values: groups.append((title, values))
        host.service = reopened
        host.search_objects[cell.key] = restored
        assert request_selected_parse_witness(
            host, restored, selected_id=restored.id,
            context=(2, 1, 1, "alice"), Gtk=NS(Button=Button),
        )
        deliver()
        inspect, = actions
        assert inspect._cifereca_action_id == "inspect-private-parse-witness"
        inspect.callback(inspect)
        deliver()
        assert groups[-1][0] == "Preuve du témoin"
        assert ("Champs atteints", "auteur") in groups[-1][1]
        assert read_notice_cover(reopened.storage, doc) == source
        assert not fresh_client.calls
        assert list((tmp_path / "private-captures").glob("*.zip")) == [archive]
        # Persisted connection revocation takes effect even when the new
        # session still holds its former Account and is marked connected.
        reopened.storage.provider_connection_put({
            "identity_id": "alice", "provider_id": fresh_account.provider_id,
            "credential_id": fresh_account.credential_id, "enabled": False,
        })
        assert private_search_witness_diagnostic(reopened, restored, scope=fresh_scope) is None
        assert not fresh_client.calls
    finally:
        reopened.close()
