from collections import Counter
from types import SimpleNamespace

import recommendation_engine as engine
import recommendation_service_v022 as service
import recommendation_engine_fast as fast
from recommendation_engine import RankedCandidate
from recommendation_service_v022 import _provisional_head, compute_recommendations


class Control:
    def __init__(self):
        self._cancelled = False
    def cancel(self):
        self._cancelled = True
    def cancelled(self):
        return self._cancelled
    def check_cancelled(self):
        if self._cancelled:
            raise RuntimeError("cancelled")
    def report(self, **_kwargs):
        return None


class Metadata:
    def library_entries(self, **kwargs):
        assert kwargs == {"identity_id": "id-1", "scan": False}
        return [
            {"title": "Seed 1", "author": "Alice", "metadata": {"subjects": ["histoire"]}},
            {"title": "Seed 2", "author": "Alice", "metadata": {"subjects": ["mémoire"]}},
        ]


class Catalog:
    def __init__(self):
        self.controls = []

    def searchable_providers(self):
        return [("a", "Catalogue A"), ("b", "Catalogue B")]

    def search_progressive(self, query, *, on_batch, control=None, **kwargs):
        self.controls.append(control)
        assert kwargs["force"] is False
        on_batch(SimpleNamespace(
            provider_id="a",
            results=({"title": f"{query} A", "author": "Alice", "source_provider": "a"},),
            warnings=(),
            error_message="",
            cached=True,
        ))
        on_batch(SimpleNamespace(
            provider_id="b",
            results=({"title": f"{query} B", "author": "Alice", "source_provider": "b"},),
            warnings=(),
            error_message="",
            cached=False,
        ))
        return SimpleNamespace(warnings=(), cancelled=False)


def test_progressive_results_and_control_are_forwarded():
    catalog = Catalog()
    control = Control()
    partials = []
    progress = []
    result = compute_recommendations(
        Metadata(),
        catalog,
        "id-1",
        max_queries=2,
        partial=partials.append,
        progress=lambda *args: progress.append(args),
        control=control,
    )
    assert result.ranked
    assert any("Profil prêt" in event[2] and "2 livres" in event[2] for event in progress)
    assert partials
    assert all(item is control for item in catalog.controls)
    assert result.cache_batches == len(result.queries)
    assert result.network_batches == len(result.queries)
    assert partials[0].provider_done == 1
    assert partials[0].providers == (("a", "Catalogue A"), ("b", "Catalogue B"))


def test_cancelled_task_does_not_start_catalog_search():
    catalog = Catalog()
    control = Control()
    control.cancel()
    result = compute_recommendations(
        Metadata(),
        catalog,
        "id-1",
        max_queries=2,
        control=control,
    )
    assert result.cancelled
    assert catalog.controls == []


def test_fast_mmr_extracts_candidate_features_once(monkeypatch):
    rows = [
        RankedCandidate(
            item={"title": f"Book {i}", "author": f"Author {i % 5}", "metadata": {"subjects": [f"s{i % 7}"]}},
            score=100 - i,
            reasons=("test",),
            sources=("p",),
        )
        for i in range(40)
    ]
    calls = 0
    original = fast._candidate_features

    def counted(item):
        nonlocal calls
        calls += 1
        return original(item)

    monkeypatch.setattr(fast, "_candidate_features", counted)
    result = fast._mmr_fast(rows, exploration=25, limit=20)
    assert len(result) == 20
    assert calls == 40


class GuardedCounter(Counter):
    def values(self):
        raise AssertionError("profile counter rescan during candidate scoring")

    def __add__(self, other):
        raise AssertionError("profile counter rebuilt during candidate scoring")


def test_candidate_scoring_reuses_precomputed_profile_aggregates():
    profile = engine.build_profile([
        {
            "title": "Histoire de Paris",
            "author": "Alice",
            "description": "mémoire urbaine architecture",
            "metadata": {"subjects": ["histoire", "ville"], "series": ["Paris"]},
        },
        {
            "title": "Mémoire des rues",
            "author": "Alice",
            "description": "archives et patrimoine",
            "metadata": {"subjects": ["histoire", "patrimoine"], "series": ["Paris"]},
        },
    ])

    # Candidate scoring must use only the aggregates prepared once by build_profile().
    # Re-scanning values() or rebuilding title_terms + description_terms would fail here.
    for name in (
        "authors",
        "subjects",
        "series",
        "title_terms",
        "description_terms",
        "lexical_terms",
    ):
        object.__setattr__(profile, name, GuardedCounter(getattr(profile, name)))

    score, reasons = engine.content_score(
        profile,
        {
            "title": "Paris, mémoire d'une ville",
            "author": "Alice",
            "description": "histoire architecture patrimoine",
            "metadata": {"subjects": ["histoire", "patrimoine"], "series": ["Paris"]},
        },
    )

    assert score > 0
    assert reasons


def test_provisional_head_matches_full_sort_without_returning_the_tail():
    provisional = {
        f"k-{i}": (
            {"title": f"Book {i:03d}", "author": f"Author {i % 7}"},
            float((i * 37) % 101),
            ("test",),
            ["p"],
        )
        for i in range(250)
    }
    expected = sorted(
        (
            RankedCandidate(
                item=item,
                score=max(1, min(100, int(round(score)))),
                reasons=reasons,
                sources=tuple(sources),
                query_hits=0,
            )
            for item, score, reasons, sources in provisional.values()
            if score > 0.0
        ),
        key=lambda row: (
            -row.score,
            engine.normalize(engine.get_field(row.item, "title", "")),
            engine.normalize(engine.get_field(row.item, "author", "")),
        ),
    )[:40]

    actual = _provisional_head(provisional, 40)

    assert len(actual) == 40
    assert [(row.score, row.item["title"]) for row in actual] == [
        (row.score, row.item["title"]) for row in expected
    ]



def test_repeated_candidates_are_scored_once_across_streaming_and_final_rank(monkeypatch):
    calls = 0
    original = fast.content_score

    def counted(profile, item):
        nonlocal calls
        calls += 1
        return original(profile, item)

    monkeypatch.setattr(fast, "content_score", counted)

    repeated = {
        "isbn": "9781234567890",
        "title": "Paris et mémoire",
        "author": "Alice",
        "description": "histoire patrimoine architecture",
        "source_provider": "a",
    }

    class DuplicateCatalog:
        def searchable_providers(self):
            return [("a", "Catalogue A"), ("b", "Catalogue B")]

        def search_progressive(self, query, *, on_batch, control=None, **kwargs):
            on_batch(SimpleNamespace(
                provider_id="a",
                results=(dict(repeated),),
                warnings=(),
                error_message="",
                cached=True,
            ))
            duplicate = dict(repeated)
            duplicate["source_provider"] = "b"
            on_batch(SimpleNamespace(
                provider_id="b",
                results=(duplicate,),
                warnings=(),
                error_message="",
                cached=True,
            ))
            return SimpleNamespace(warnings=(), cancelled=False)

    result = compute_recommendations(
        Metadata(),
        DuplicateCatalog(),
        "id-1",
        max_queries=2,
        partial=lambda _snapshot: None,
    )

    assert result.ranked
    assert calls == 1



def test_compute_builds_library_profile_once(monkeypatch):
    calls = 0
    original = engine.build_profile

    def counted(books):
        nonlocal calls
        calls += 1
        return original(books)

    def forbidden(_books):
        raise AssertionError("build_queries rebuilt the prepared library profile")

    monkeypatch.setattr(service, "build_profile", counted)
    monkeypatch.setattr(engine, "build_profile", forbidden)

    result = service.compute_recommendations(
        Metadata(),
        Catalog(),
        "id-1",
        max_queries=2,
    )

    assert result.ranked
    assert calls == 1



class SinglePassBooks(list):
    def __init__(self, rows):
        super().__init__(rows)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("library books rescanned while building queries")
        return super().__iter__()


def test_build_queries_does_not_rescan_library_after_profile_build():
    books = SinglePassBooks([
        {"title": "Un", "author": "Alice Martin", "metadata": {"subjects": ["histoire"], "series": ["Paris"]}},
        {"title": "Deux", "author": "Alice Martin", "metadata": {"subjects": ["mémoire"], "series": ["Paris"]}},
    ])

    queries = engine.build_queries(books)

    assert queries[0] == "Alice Martin"
    assert "Paris" in queries
    assert books.iterations == 1
