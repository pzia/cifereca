"""#4030: ranking of *already compared* surfaces, never a source of authority."""
import importlib.util
from pathlib import Path

import pytest

from cifereca.plugin_api.local_origin_detail import (
    LocalOriginPublicReport, LocalOriginPublicMatch,
    LocalOriginPrivateReport, LocalOriginPrivateMatch,
    LocalOriginRemoteLoanReport, LocalOriginRemoteLoanMatch,
)
from tests.test_core_p0_4030_private_catalogue import private_rig, rig, store, rows
from tests.test_core_p0_7092_origin_detail_controller import mounted, deliver

ROOT = Path(__file__).resolve().parents[1] / "src/plugins/maintenance-local-origin"


def _ranking():
    spec = importlib.util.spec_from_file_location(
        "origin_evidence_ranking", ROOT / "cifereca_local_origin_detail.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ranked_documentary_rows


def _public(comparison, record="pub"):
    return LocalOriginPublicReport(
        "catalogue", "partial",
        (LocalOriginPublicMatch("catalogue", record, "catalogue", "digital",
                                "Roman commun", "Autrice", "EPUB", (), comparison),),
        True,
    )


def _private(comparison, kind="loan", edition="edition"):
    return LocalOriginPrivateReport(
        "partial", "seller", "card", "account", kind,
        (LocalOriginPrivateMatch(edition, "Roman commun", "Autrice",
                                 comparison, "cached_loan_description", None),),
        False,
    )


def _remote(comparison):
    return LocalOriginRemoteLoanReport(
        "partial", "seller", "card", "account",
        (LocalOriginRemoteLoanMatch("edition", "loan", "Roman commun",
                                    "Autrice", comparison),),
        False, None, True,
    )


@pytest.mark.parametrize("compared,expected", [
    ([("loan", _private("title_author_concordant")),
      ("public", _public("title_author_concordant"))],
     ["Catalogue public", "Prêt en cache"]),
    ([("public", _public("title_related")),
      ("loan", _private("title_author_concordant"))],
     ["Prêt en cache", "Catalogue public"]),
    ([("purchase", _private("title_author_concordant", kind="purchase")),
      ("remote-loan", _remote("title_author_concordant"))],
     ["Achat en cache", "Inventaire de prêts relu"]),
    ([("public", _public("isbn_claim_ambiguous")),
      ("loan", _private("title_author_concordant"))],
     ["Prêt en cache", "Catalogue public"]),
    ([("loan", _private("title_author_concordant")),
      ("public", _public("isbn_title_author_concordant"))],
     ["Catalogue public", "Prêt en cache"]),
])
def test_cross_surface_rank_prioritizes_evidence_then_surface_only_at_ties(
    compared, expected,
):
    ranking = _ranking()
    before = tuple(compared)
    rows_ = ranking(compared)
    labels = [detail.split(" · ")[0] for _, detail in rows_[3:]]
    assert labels == expected
    assert "jamais attribution" in repr(rows_[0])
    assert "Partielle" in repr(rows_)
    assert tuple(compared) == before


def test_plugin_comparative_review_requires_two_explicit_sources_and_preserves_storage(
    private_rig,
):
    service, request, account, session, calls, path, *_ = private_rig
    service._provider_enabled_for_identity = lambda p, i: (
        i == "A" and p in ("seller", "new-catalogue")
    )
    before, data = rows(service.storage), path.read_bytes()
    controller, dispose, host, active, tasks, actions, groups, toasts = mounted(
        (service, session, request, calls, path),
    )
    assert actions["preview"][2]()
    deliver(tasks)
    assert not [g for g in groups if g[0] == "Classement documentaire — lecture seule"]
    assert "public-0" in actions and "private-0" in actions
    assert actions["public-0"][2]()
    deliver(tasks)
    assert len(calls) == 1
    assert actions["private-0"][2]()
    deliver(tasks)
    ranked = [g for g in groups if g[0] == "Classement documentaire — lecture seule"]
    # Known C was reviewed on the first page, so the public lookup already
    # adds a comparative snapshot; the private lookup adds the next one.
    assert len(ranked) == 2
    lines = ranked[-1][1]
    labels = [detail.split(" · ")[0] for _, detail in lines[3:]]
    assert labels == ["Notice C déjà connue", "Prêt en cache",
                      "Catalogue public", "Catalogue public"]
    assert "jamais attribution" in repr(lines[0])
    assert rows(service.storage) == before and path.read_bytes() == data
    dispose()
    assert controller.compared == []



def test_cross_surface_ranking_bounds_large_multi_page_window_without_losing_weak_ties():
    from dataclasses import replace

    rank = _ranking()
    public = _public("title_related")
    row, = public.matches
    many = replace(
        public, matches=tuple(
            replace(row, record_id=f"public-{i:04d}", title=f"Faible {i:04d}")
            for i in range(1500)
        ),
    )
    purchase = _private("title_author_concordant", kind="purchase")
    highest = _public("isbn_title_author_concordant", record="strong-public")
    first = rank((("public", many), ("purchase", purchase), ("public", highest)))
    assert len(first) == 14  # 3 advisory lines, 10 results, 1 limit
    assert first[3][1].startswith("Catalogue public · catalogue")
    assert "strong-public" in first[3][1]
    assert first[4][1].startswith("Achat en cache · seller")
    assert "1492 candidats examinés de plus" in first[-1][1]
    assert "aucune absence attestée" in first[-1][1]
    assert "Faible 0000" in first[5][0]
    assert "Faible 0007" in first[12][0]
    assert all("FAKE RIGHT" not in repr(x) for x in first)


def test_cross_surface_ranking_includes_more_than_ten_exact_equal_c_sources_in_source_order():
    from dataclasses import replace
    ranking = _ranking()
    row, = _public("title_author_concordant").matches
    report = replace(
        _public("title_author_concordant"),
        matches=tuple(replace(row, record_id=f"same-{i}", title=f"Un {i}")
                      for i in range(30)),
    )
    observed = ranking((("public", report),))
    assert [name for name, _ in observed[3:13]] == [f"Un {i}" for i in range(10)]
    assert "20 candidats examinés de plus" in observed[-1][1]
    assert len(observed) == 14
