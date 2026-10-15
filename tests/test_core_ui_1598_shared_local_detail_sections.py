"""P1 #1598: equivalent local Search/Library projection, without GTK."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.detail_model import book_detail_presentation, detail_language
from cifereca.gui.library_detail_controller import library_selected
from cifereca.gui.local_book_detail_sections import local_book_detail_sections


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def _item(**changes):
    item = dict(
        key="book-1", title="Titre", author="A",
        provider_id="paris", identity_id="alice",
        media_format="epub", metadata={"languages": ["fr", "en"]},
        local_path="/private/alice/book.epub", sync_status="done", state="local",
    )
    item.update(changes)
    return item


def _options(*, availability="Disponible", status="À jour"):
    calls = []
    def provider(key):
        calls.append(("provider", key))
        return key.title()
    def identity(key):
        calls.append(("identity", key))
        return NS(label="Alice") if key == "alice" else None
    def lang(meta):
        calls.append(("language", meta))
        return "français, anglais" if meta else ""
    def acquire(key):
        calls.append(("sync", key))
        return status
    def available(item):
        calls.append(("availability", item))
        return availability
    return dict(
        provider_display_name=provider,
        identity_by_id=identity,
        language_label=lang,
        sync_status_label=acquire,
        availability_label=available,
    ), calls


def test_helper_has_no_gtk_main_window_or_network_import():
    tree = ast.parse((ROOT / "local_book_detail_sections.py").read_text())
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("gi" in x or "main_window" in x or "requests" in x
                   for x in imports)


def test_order_values_identity_and_nominal_acquisition_are_exact():
    opts, calls = _options()
    item = _item()
    assert local_book_detail_sections(item, **opts) == [
        ("Essentiel", [
            ("Source", "Paris"), ("Format", "EPUB"),
            ("Langue", "français, anglais"), ("Disponibilité", "Disponible"),
        ]),
        ("Bibliothèque", [
            ("Identité", "Alice"),
            ("Fichier local", "/private/alice/book.epub"),
            ("État acquisition", ""),
        ]),
    ]
    assert calls == [
        ("availability", item), ("identity", "alice"), ("sync", "done"),
        ("provider", "paris"), ("language", item["metadata"]),
    ]


def test_missing_metadata_stays_empty_with_no_implicit_status_or_identity():
    opts, calls = _options(availability="")
    assert local_book_detail_sections({}, **opts) == [
        ("Essentiel", [
            ("Source", "Local"), ("Format", ""), ("Langue", ""),
            ("Disponibilité", ""),
        ]),
        ("Bibliothèque", [
            ("Identité", ""), ("Fichier local", ""), ("État acquisition", ""),
        ]),
    ]
    assert ("identity", "") in calls
    assert not any(key == "sync" for key, value in calls)


def test_offline_fallback_is_search_only_and_requires_local_state():
    opts, _ = _options(availability="")
    for state in ("local", "remote_owned", "remote_loan_active", ""):
        item = _item(state=state)
        library = dict(local_book_detail_sections(item, **opts))["Essentiel"]
        search = dict(local_book_detail_sections(
            item, **opts, offline_if_local=True,
        ))["Essentiel"]
        assert library[-1] == ("Disponibilité", "")
        assert search[-1] == (
            "Disponibilité", "Disponible hors ligne" if state == "local" else "",
        )


def test_attested_availability_and_sync_non_nominal_are_preserved():
    opts, _ = _options(availability="Possédé en ligne", status="En attente")
    data = dict(local_book_detail_sections(
        _item(), **opts, offline_if_local=True,
    ))
    assert data["Essentiel"][-1] == ("Disponibilité", "Possédé en ligne")
    assert data["Bibliothèque"][-1] == ("État acquisition", "En attente")


def test_unresolved_identity_keeps_its_original_id_not_another_account():
    opts, calls = _options()
    section = dict(local_book_detail_sections(
        _item(identity_id="unknown"), **opts,
    ))["Bibliothèque"]
    assert section[0] == ("Identité", "unknown")
    assert [value for key, value in calls if key == "identity"] == ["unknown"]


def test_multivalued_metadata_passed_unmodified_without_concatenation():
    opts, calls = _options()
    metadata = {"languages": ["fr", "en", "fr"], "authors": ["A", "B"]}
    local_book_detail_sections(_item(metadata=metadata), **opts)
    assert [v for key, v in calls if key == "language"] == [metadata]
    assert metadata["languages"] == ["fr", "en", "fr"]


def test_shared_presentation_keeps_one_renderer_and_compacts_empty_rows():
    opts, _ = _options()
    item = _item()
    model = book_detail_presentation(
        title=item["title"], subtitle=item["author"],
        section_rows=local_book_detail_sections(item, **opts),
        metadata=item["metadata"],
    )
    assert [section.title for section in model.sections[:2]] == [
        "Essentiel", "Bibliothèque",
    ]
    assert [field.label for field in model.sections[0].fields] == [
        "Source", "Format", "Langue", "Disponibilité",
    ]
    assert [field.label for field in model.sections[1].fields] == [
        "Identité", "Fichier local",
    ]


class Button:
    def __init__(self, label=None): self.label = label
    def connect(self, *_): pass
    def add_css_class(self, *_): pass


def _adapters(item, availability):
    search_projection, library_projection, remote_calls = [], [], []
    service = NS(
        provider_display_name=lambda provider: provider.upper(),
        identity_by_id=lambda key: NS(label=f"Profile {key}") if key else None,
        can_download_loan=lambda loan: False,
    )
    def panel(log):
        return NS(
            show_presentation=lambda model, **kwargs: log.append(model),
            add_actions=lambda actions: None,
            add_group=lambda title, values: None,
            empty=lambda *args: None,
        )
    common = dict(
        _closed=False, service=service,
        _library_availability_label=lambda _: availability,
        _kobo_backup_status_label=lambda key: "À jour" if key == "done" else "En attente",
        _book_description=lambda _: "Résumé",
        _cancel_cached_book_details=lambda: None,
        _add_book_notice=lambda *a, **k: None,
        _add_local_book_actions=lambda *a: None,
        _add_facets=lambda *a: None,
        _request_cached_book_details=lambda *a: None,
    )
    sw = NS(**common, _search_detail_request_generation=0,
            _search_local_objects={item["key"]: item},
            search_page=NS(detail=panel(search_projection)),
            search_controls=object())
    sw.run_search = lambda online: remote_calls.append(online)
    lw = NS(**common, _library_objects={item["key"]: item},
            library_page=NS(detail=panel(library_projection)),
            library_controls=object(), loan_objects={},
            _add_book_recommendation_action=lambda *a: None)
    source = ROOT / "search_details.py"
    method = next(node for node in ast.parse(source.read_text()).body
                  if isinstance(node, ast.FunctionDef) and node.name == "_search_selected")
    future = ast.ImportFrom(module="__future__",
                            names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, method], type_ignores=[]))
    namespace = dict(
        Gtk=NS(Button=Button), book_detail_presentation=book_detail_presentation,
        detail_language=detail_language, local_book_detail_sections=local_book_detail_sections,
    )
    exec(compile(module, str(source), "exec"), namespace)
    return namespace["_search_selected"], sw, lw, search_projection, library_projection, remote_calls


def _library(lw, item, links=()):
    return library_selected(
        lw, NS(key=item["key"]), Gtk=NS(Button=Button),
        unique_library_loan=lambda loans, row: None,
        book_detail_presentation=book_detail_presentation,
        detail_language=detail_language,
        document_links=lambda row: links,
    )


def test_real_adapters_emit_identical_shared_sections_without_acquisition():
    item = _item()
    search, sw, lw, sr, lr, remote = _adapters(item, "Disponible")
    search(sw, NS(key=item["key"]))
    _library(lw, item)
    assert len(sr) == len(lr) == 1
    assert sr[0].sections == lr[0].sections
    assert sr[0].title == lr[0].title
    assert sr[0].subtitle == lr[0].subtitle
    assert sr[0].thumbnail_url == lr[0].thumbnail_url
    assert remote == []


def test_search_offline_fallback_and_library_links_remain_context_specific():
    item = _item(sync_status=None)
    search, sw, lw, sr, lr, remote = _adapters(item, "")
    search(sw, NS(key=item["key"]))
    _library(lw, item, links=("public-link",))
    assert sr[0].sections[0].fields[-1].value == "Disponible hors ligne"
    assert [field.label for field in lr[0].sections[0].fields] == [
        "Source", "Format", "Langue",
    ]
    assert sr[0].links == ()
    assert lr[0].links == ("public-link",)
    assert remote == []


def test_empty_selection_preserves_existing_empty_detail_in_both_adapters():
    item = _item()
    search, sw, lw, sr, lr, remote = _adapters(item, "")
    search(sw, None)
    library_selected(lw, None, Gtk=NS(Button=Button),
                     unique_library_loan=None,
                     book_detail_presentation=book_detail_presentation,
                     detail_language=detail_language,
                     document_links=lambda row: ())
    assert sr == lr == [] and remote == []
