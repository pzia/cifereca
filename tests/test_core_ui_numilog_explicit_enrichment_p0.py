"""Explicit Numilog search detail action; headless, no GTK or network."""
from dataclasses import replace
from types import SimpleNamespace

from cifereca.gui.numilog_enrichment_action import add_numilog_enrichment_action
from cifereca.models import SearchResult, ItemDetail


def item():
    return SearchResult(
        id="book", record_id="numilog:scope:42", docbase="NUMILOG",
        medium="digital", title="Mon livre", author=None,
        type="Livre", format=None, publisher=None, published_at=None,
        description=None, thumbnail_url=None,
        friendly_url="https://www.numilog.com/1958476/book.ebook",
        scenario="NUMILOG_LIBRARY", source_provider="numilog",
        source_credential="alice", source_collection="library",
        owned=True, downloadable=True, download_url=None,
    )


class Button:
    def __init__(self, label, icon):
        self.label = label
        self.icon = icon
        self.callback = None

    def connect(self, signal, callback):
        assert signal == "clicked"
        self.callback = callback

    def click(self):
        self.callback(self)


class Host:
    def __init__(self):
        self.context = (3, 4, 5, "id-A")
        self.active = True
        self.actions = []
        self.tasks = []
        self.refresh = []
        self.toasts = []
        self.search_objects = {"book": item()}
        self.search_page = SimpleNamespace(
            detail=SimpleNamespace(add_actions=self.actions.extend),
        )
        self.runner = SimpleNamespace(submit=self.submit)
        self.service = SimpleNamespace(enrich_numilog_search_result=self.enrich)
        self.network_requests = 0

    def _search_detail_context_current(self, key, context):
        return self.active and key == "book" and context == self.context

    def _apply_search_detail(self, *args, **kwargs):
        self.refresh.append((args, kwargs))

    def toast(self, message, *_):
        self.toasts.append(message)

    def enrich(self, row, *, expected_identity_id):
        self.network_requests += 1
        assert expected_identity_id == "id-A"
        return replace(row, author="Autrice détaillée")

    def submit(self, *args, **kwargs):
        self.tasks.append((args, kwargs))


def detail():
    return ItemDetail(
        record_id="numilog:scope:42", docbase="NUMILOG", title="Mon livre",
        author=None, type="Livre", year=None, thumbnail_url=None,
        availability_status="owned", availability_label="Acheté",
        holdings=[], source_provider="numilog", source_credential="alice",
    )


def build(host, row=None, context=None):
    return add_numilog_enrichment_action(
        host, "book", row or item(), detail(),
        host.context if context is None else context,
        button_factory=Button,
    )


def test_button_is_opt_in_and_never_fetches_while_rendering():
    host = Host()
    assert build(host)
    assert len(host.actions) == 1
    assert host.network_requests == 0 and host.tasks == []
    assert host.actions[0].label == "Enrichir depuis Numilog"


def test_click_submits_worker_with_user_origin_and_identity_snapshot():
    host = Host()
    build(host)
    host.actions[0].click()
    assert host.network_requests == 0
    args, kwargs = host.tasks[0]
    assert kwargs["origin"] == "user"
    assert kwargs["control"] is host.actions[0]
    assert kwargs["kind"] == "plugin"
    enriched = args[1]()
    assert host.network_requests == 1
    args[2](enriched)
    assert host.search_objects["book"].author == "Autrice détaillée"
    assert host.refresh[0][0][2].author == "Autrice détaillée"
    assert host.refresh[0][1]["context"] == host.context


def test_stale_click_does_not_schedule_network_and_stale_result_is_discarded():
    host = Host()
    build(host)
    host.active = False
    host.actions[0].click()
    assert host.tasks == [] and host.network_requests == 0
    host.active = True
    host.actions[0].click()
    args, _kwargs = host.tasks[0]
    enriched = args[1]()
    host.active = False
    args[2](enriched)
    args[3](RuntimeError("PRIVATE_TOKEN"))
    assert host.search_objects["book"].author is None
    assert host.refresh == [] and host.toasts == []


def test_provider_error_never_reaches_ui_toast_text():
    host = Host()
    build(host)
    host.actions[0].click()
    args, _kwargs = host.tasks[0]
    args[3](RuntimeError("https://numilog.com/PRIVATE?token=PASSWORD"))
    assert host.toasts == ["Enrichissement Numilog indisponible ou non attesté."]


def test_public_ownership_false_is_eligible_but_no_private_scope():
    host = Host()
    public = replace(item(), source_provider="numilog-catalog",
                     source_collection="public_catalog",
                     source_credential="__public__", owned=False,
                     downloadable=False)
    assert build(host, row=public)
    assert host.network_requests == 0


def test_wrong_source_or_missing_context_never_displays_action():
    host = Host()
    assert not build(host, row=replace(item(), source_provider="kobo"))
    assert add_numilog_enrichment_action(
        host, "book", item(), detail(), None, button_factory=Button,
    ) is False
    assert len(host.actions) == 0
