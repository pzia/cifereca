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
    assert len(ranked) == 1
    lines = ranked[0][1]
    labels = [detail.split(" · ")[0] for _, detail in lines[3:]
    assert labels == ["Prêt en cache", "Catalogue public", "Catalogue public"]
    assert "jamais attribution" in repr(lines[0])
    assert rows(service.storage) == before and path.read_bytes() == data
    dispose()
    assert controller.compared == []
