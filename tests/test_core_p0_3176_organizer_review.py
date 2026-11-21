"""#3176: organizer consumes native Core advice; all writes are fixture setup."""
from pathlib import Path
from types import SimpleNamespace as NS
import hashlib

import pytest

from cifereca.core import library_organizer_review as review
from tests.test_core_p0_3171_atomic_publication_v14 import store, register, rows, BYTES
from tests.test_core_p0_2835_epub_layout import _book
from tests.test_core_p0_4030_epub_evidence import META
from tests.test_p1_4496_keeper_review import setup_copies


def _service(store, root):
    owner = NS(identity_id="A", directory_name="A")
    settings = {"library.directory": str(root)}
    config = NS(get=lambda k, d=None: settings.get(k, d))
    return NS(storage=store, config=config, current_identity=owner,
              identity_by_id=lambda key: owner if key == "A" else None,
              _source_context_epoch=1, _account_context_epoch=1,
              preview_library_filename=lambda *, title, author, extension:
                  f"{author} - {title}.{extension}" if author else f"{title}.{extension}")


def _put_epub(store, root, name, *, metadata=META, owner="A", catalogue=True):
    path = root / owner / name
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _book(metadata).getvalue()
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    artifact = "sha256:" + digest
    store.artifact_put({"artifact_id": artifact, "sha256": digest,
                        "size": len(data), "media_format": "epub"})
    pid = store.placement_put({"artifact_id": artifact, "identity_id": owner,
                               "state": "present", "path": str(path)})
    if catalogue:
        store.catalog_entry_put({
            "placement_id": pid, "artifact_id": artifact, "identity_id": owner,
            "title": "Roman commun", "author": "Autrice", "local_state": "local",
        })
    return path, pid


def _run(service):
    scope = review.admit_organizer_review(service)
    assert scope is not None
    result = review.run_organizer_review(service, scope)
    assert result is not None
    assert result["mutation_authorized"] is False
    assert result["classification_attested"] is False
    return scope, result


@pytest.mark.parametrize("suffix", [".epub", ".kepub.epub", ".fxl.kepub.epub"])
def test_review_preserves_convention_and_existing_directory(store, tmp_path, suffix):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    path, _ = _put_epub(store, root, "Acquisitions/current" + suffix)
    service = _service(store, root)
    before = rows(store)
    _, result = _run(service)
    group, = result["groups"]
    file, = group["files"]
    assert file["current"] == "Acquisitions/current" + suffix
    assert file["proposed_name"] == "Autrice - Roman commun" + suffix
    assert file["reason"] == "rename_advice_only"
    assert not group["relocation_proposed"]
    assert rows(store) == before
    assert path.is_file() and len(list(path.parent.iterdir())) == 1


@pytest.mark.parametrize("change,reason", [
    ("tamper", "physical_unverified"),
    ("no_catalogue", "description_unverified"),
    ("collision", "destination_occupied"),
    ("receipt", "receipt_discriminator_preserve"),
    ("outbox", "excluded_or_unverified_path"),
    ("staging", "excluded_or_unverified_path"),
])
def test_review_never_proposes_unsafe_filename(store, tmp_path, change, reason):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    name = {
        "receipt": "Acquisitions/Roman - " + "a"*64 + ".epub",
        "outbox": "Outbox/device/old.epub",
        "staging": ".canonical-delivery/run-test/old.epub",
    }.get(change, "Acquisitions/old.epub")
    path, _ = _put_epub(store, root, name, catalogue=change != "no_catalogue")
    if change == "tamper":
        path.write_bytes(b"tampered")
    if change == "collision":
        path.with_name("Autrice - Roman commun.epub").write_bytes(b"other")
    _, result = _run(_service(store, root))
    file, = result["groups"][0]["files"]
    assert file["proposed_name"] is None
    assert file["reason"] == reason
    assert path.exists()


def test_keeper_accept_all_is_advice_only(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    service = _service(store, root)
    before = rows(store)
    scope, result = _run(service)
    assert result["groups"][0]["keeper"] == "Livres/Autrice/Autrice - Roman.pdf"
    assert result["keeper_advice"].preselected == ()
    assert review.select_organizer_keeper_advice(scope, result) is None
    selected = review.select_organizer_keeper_advice(scope, result, confirmed=True)
    assert selected is not None and selected.executable is False
    choice, = selected.preselected
    assert choice.keeper_placement_id == pair[1].placement_id
    assert rows(store) == before
    service._account_context_epoch += 2
    assert review.select_organizer_keeper_advice(scope, result, confirmed=True) is None



@pytest.mark.parametrize("drift", ["sqlite", "binding", "other_scope", "missing_anchor"])
def test_direct_keeper_advice_rejects_stale_review_snapshot(store, tmp_path, monkeypatch, drift):
    root, _ = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    service = _service(store, root)
    scope, result = _run(service)
    assert review.select_organizer_keeper_advice(scope, result, confirmed=True) is not None
    if drift == "sqlite":
        store.metadata_set("organizer-advice-drift", "yes")
    elif drift == "binding":
        monkeypatch.setattr(
            store, "exact_profile_directory_binding", lambda _identity: ("wrong", True),
        )
    elif drift == "other_scope":
        scope = review.admit_organizer_review(service)
        assert scope is not None and scope is not result["review_anchor"].scope
    else:
        result = {k: v for k, v in result.items() if k != "review_anchor"}
    assert review.select_organizer_keeper_advice(scope, result, confirmed=True) is None


def test_direct_keeper_advice_rejects_sql_change_during_confirmation(store, tmp_path, monkeypatch):
    root, _ = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    service = _service(store, root)
    scope, result = _run(service)
    original = review.preview_keeper_bulk

    def changed_during_preview(*args, **kwargs):
        selected = original(*args, **kwargs)
        if kwargs.get("accept_all") is True:
            store.metadata_set("organizer-confirmation-drift", "yes")
        return selected

    monkeypatch.setattr(review, "preview_keeper_bulk", changed_during_preview)
    assert review.select_organizer_keeper_advice(scope, result, confirmed=True) is None


def test_page_continuation_invalidated_by_sql_or_identity_change(store, tmp_path):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    for i in range(5):
        _put_epub(store, root, f"Acquisitions/{i}.epub",
                  metadata=META.replace("Roman commun", f"Roman {i}"))
    service = _service(store, root)
    scope, result = _run(service)
    assert len(result["groups"]) == 4 and result["next_cursor"] is not None
    continuation = result["next_cursor"]
    second = review.run_organizer_review(service, scope, cursor=continuation)
    assert second is not None and second["page"] == 2
    assert len(second["groups"]) == 1 and second["next_cursor"] is None
    store.metadata_set("synthetic-organization-change", "1")
    assert review.run_organizer_review(service, scope, cursor=continuation) is None
    service._source_context_epoch += 1
    assert review.run_organizer_review(service, scope) is None


@pytest.mark.parametrize("stale", [False, True])
def test_native_organizer_only_runs_on_click_and_rejects_stale_delivery(store, tmp_path, stale):
    from cifereca.gui.library_organizer_line import add_library_organizer_line
    from tests.test_core_ui_1358_maintenance_preview_cancellation import Button, Container

    class Label(Button):
        def __init__(self, **kwargs):
            super().__init__()
            self.text = ""
        def set_wrap(self, value): pass
        def set_selectable(self, value): pass
        def set_text(self, value): self.text = value

    root, _ = setup_copies(store, tmp_path, (
        "Acquisitions/opaque.pdf", "Livres/Autrice/Autrice - Roman.pdf",
    ))
    service = _service(store, root)
    tasks = []
    host = NS(service=service, _closed=False, _route_display_token=object(),
              _identity_context_generation=1, action_button=lambda label, _: Button(label),
              runner=NS(submit=lambda *args, **kwargs: tasks.append(args)))
    gtk = NS(Box=lambda **kw: Container(), Orientation=NS(HORIZONTAL=0),
             Button=Button, Label=Label)
    adw = NS(PreferencesGroup=Container, ActionRow=Container)
    box = Container()
    assert add_library_organizer_line(host, box, Gtk=gtk, Adw=adw)
    group, actions, detail = box.children
    start, = group.children[0].children
    following, accept = actions.children
    assert not tasks and not detail.visible and not accept.sensitive
    start.click()
    start.click()
    assert len(tasks) == 1
    value = tasks[0][1]()
    if stale:
        host._route_display_token = object()
    tasks[0][2](value)
    tasks[0][2](value)
    if stale:
        assert not detail.visible and not accept.sensitive
    else:
        assert "Copie recommandée" in detail.text and accept.sensitive
        accept.click()
        assert "1 sélectionnés" in detail.text and not accept.sensitive
        assert len(tasks) == 1 and not following.sensitive
