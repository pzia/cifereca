"""P0 #4030: exact C documentary coordinates in the supervised plugin batch."""
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

from cifereca.core.local_origin_review_broker import LocalOriginBatchReadBrokerImpl
from cifereca.plugin_api.local_origin_review import (
    KnownOriginMatch, LocalOriginFileAdvice, LocalOriginBatchPage,
)
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows, BYTES
from tests.test_core_p0_4030_suggestion_operator import _service


def _display():
    file = (Path(__file__).resolve().parents[1] / "src/plugins" /
            "maintenance-local-origin/cifereca_local_origin.py")
    spec = spec_from_file_location("batch_source_provenance", file)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.display


def test_batch_broker_preserves_public_c_identity_and_observation_without_mutation(store, tmp_path):
    service, root, pid = _service(store, tmp_path)
    path = Path(store.placement_get(pid)["path"])
    before, contents = rows(store), path.read_bytes()
    page = LocalOriginBatchReadBrokerImpl(service).preview(identity_id="A", confirmed=True)
    assert type(page) is LocalOriginBatchPage
    (entry,) = page.entries
    assert entry.status == "compared"
    (hit,) = entry.suggestions
    assert hit.provider_id and hit.record_id and hit.docbase and hit.edition_id
    assert hit.medium in ("digital", "physical", "unknown")
    assert type(hit.observed_at) is int
    presented = _display()(page)
    assert "Référence C : " + hit.record_id in presented
    assert "Collection C : " + hit.docbase in presented
    assert "Édition C : " + hit.edition_id in presented
    assert "Support C : " + hit.medium in presented
    assert "Observation C (UTC)" in presented
    assert "couverture partielle" in presented.lower()
    assert "Aucune origine, association ou licence créée" in presented
    assert rows(store) == before and path.read_bytes() == contents == BYTES


def test_batch_plugin_rejects_invisible_source_label_but_retains_neighbor():
    display = _display()
    good = KnownOriginMatch("Roman", "Autrice", "public", "title_author_concordant",
                            False, "record-1", "public", "edition-1", "digital",
                            "EPUB", 100)
    forged = KnownOriginMatch("Autre", "Autrice", "public", "title_related", False,
                              "record-2", "public\\u202eFORGED", "edition-2", "digital",
                              "EPUB\\x85FORGED", None)
    page = LocalOriginBatchPage("token", 1, (
        LocalOriginFileAdvice("roman.epub", "compared", "", 2, False,
                              (good, forged)),), False, False)
    lines = display(page)
    assert "Référence C : record-1" in lines
    assert "Collection C : public" in lines
    assert "Collection C : public\\u202e" not in lines
    assert "Format C : EPUB\\x85" not in lines
    assert "Date d'observation C indisponible" in lines
    assert "Roman" in lines and "Autre" in lines


def test_batch_legacy_match_constructor_remains_compatible():
    old = KnownOriginMatch("Roman", None, "public", "title_related", False)
    assert old.record_id == "" and old.observed_at is None
    page = LocalOriginBatchPage("token", 1, (
        LocalOriginFileAdvice("roman.epub", "compared", "", 1, False,
                              (old,)),), False, False)
    assert "Date d'observation C indisponible" in _display()(page)
