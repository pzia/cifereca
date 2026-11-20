"""#7087 public DTOs only; exercise Core organizer without GTK or plugins."""
import pytest

from cifereca.core.library_organizer_broker import LibraryOrganizerReadBrokerImpl
from cifereca.plugin_api.library_organizer import LibraryOrganizerReadBroker, OrganizerPageAdvice
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, rows
from tests.test_core_p0_3176_organizer_review import _service, _put_epub, META
from tests.test_p1_4496_keeper_review import setup_copies


def test_public_preview_and_next_page_are_read_only(store, tmp_path):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    for i in range(5):
        _put_epub(store, root, f"Acquisitions/{i}.epub",
                  metadata=META.replace("Roman commun", f"Roman {i}"))
    service = _service(store, root)
    broker = LibraryOrganizerReadBrokerImpl(service)
    assert isinstance(broker, LibraryOrganizerReadBroker)
    before = rows(store)
    assert broker.preview(identity_id="A") is None
    assert broker.preview(identity_id="B", confirmed=True) is None
    first = broker.preview(identity_id="A", confirmed=True)
    assert type(first) is OrganizerPageAdvice and first.page == 1
    assert len(first.groups) == 4 and first.has_more
    assert not first.mutation_authorized
    assert not hasattr(first, "scope") and not hasattr(first, "keeper_reviews")
    assert broker.next_page(token=first.token) is None
    second = broker.next_page(token=first.token, confirmed=True)
    assert second.page == 2 and not second.has_more
    assert second.token != first.token
    assert broker.next_page(token=first.token, confirmed=True) is None
    assert broker.next_page(token=second.token, confirmed=True) is None
    assert rows(store) == before


@pytest.mark.parametrize("change", ["epoch", "navigation", "sql", "cancel", "revoke", "new_preview"])
def test_revoked_page_never_selects_advice(store, tmp_path, change):
    root, _ = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    service = _service(store, root)
    broker = LibraryOrganizerReadBrokerImpl(service)
    page = broker.preview(identity_id="A", confirmed=True)
    assert page.eligible == 1
    if change == "epoch":
        service._source_context_epoch += 1
    elif change == "navigation":
        service._account_context_epoch += 2
    elif change == "sql":
        store.metadata_set("synthetic-change", "yes")
    elif change == "cancel":
        broker.cancel(token=page.token)
    elif change == "revoke":
        broker.revoke()
    else:
        assert broker.preview(identity_id="A", confirmed=True).token != page.token
    assert broker.select_all_advice(token=page.token, confirmed=True) is None


def test_select_all_remains_advice_and_cannot_be_replayed(store, tmp_path):
    root, _ = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    broker = LibraryOrganizerReadBrokerImpl(_service(store, root))
    before = rows(store)
    page = broker.preview(identity_id="A", confirmed=True)
    assert page.eligible == 1 and page.selected == 0
    assert broker.select_all_advice(token=page.token) is None
    update = broker.select_all_advice(token=page.token, confirmed=True)
    assert update.selected == 1 and not update.mutation_authorized
    assert page.selected == 0
    assert broker.select_all_advice(token=page.token, confirmed=True) is None
    assert rows(store) == before
