from types import SimpleNamespace

import recommendation_engine_fast as fast
from recommendation_engine import RankedCandidate
from recommendation_service_v022 import compute_recommendations


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
