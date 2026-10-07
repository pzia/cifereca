from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from recommendation_engine import (
    ProfileVector,
    RankedCandidate,
    _candidate_features,
    _jaccard,
    _rrf_scores,
    _text,
    build_profile,
    candidate_key,
    content_score,
    get_field,
    is_already_present,
    normalize,
)


def _feature_similarity(
    left: tuple[set[str], set[str], set[str], set[str]],
    right: tuple[set[str], set[str], set[str], set[str]],
) -> float:
    la, ls, lt, lser = left
    ra, rs, rt, rser = right
    return min(
        1.0,
        0.45 * _jaccard(la, ra)
        + 0.25 * _jaccard(ls, rs)
        + 0.20 * _jaccard(lt, rt)
        + 0.10 * _jaccard(lser, rser),
    )


def _mmr_fast(order: Sequence[RankedCandidate], exploration: int, limit: int) -> list[RankedCandidate]:
    """Bound CPU/GIL use while preserving MMR semantics on the useful head."""
    limit = max(1, int(limit))
    if exploration <= 0 or len(order) <= 2:
        return list(order[:limit])

    # Only plausible head candidates are diversified. This prevents the MMR tail
    # from monopolising the Python GIL when federated catalogs return many rows.
    pool_size = min(len(order), max(limit * 4, limit + 80))
    remaining = list(order[:pool_size])
    features = {id(row): _candidate_features(row.item) for row in remaining}
    max_similarity = {id(row): 0.0 for row in remaining}
    selected: list[RankedCandidate] = []
    penalty = min(0.60, max(0.0, exploration / 100.0 * 0.60))

    while remaining and len(selected) < limit:
        if not selected:
            chosen = remaining.pop(0)
        else:
            best_i = max(
                range(len(remaining)),
                key=lambda index: (
                    remaining[index].score / 100.0
                    - penalty * max_similarity[id(remaining[index])]
                ),
            )
            chosen = remaining.pop(best_i)
        selected.append(chosen)

        chosen_features = features[id(chosen)]
        for candidate in remaining:
            key = id(candidate)
            similarity = _feature_similarity(features[key], chosen_features)
            if similarity > max_similarity[key]:
                max_similarity[key] = similarity
    return selected


def rank_candidates_prepared(
    profile: ProfileVector,
    batches: Sequence[Sequence[Any]],
    *,
    limit: int = 120,
    exploration: int = 25,
) -> list[RankedCandidate]:
    """Rank against a profile built once, with cached content scores and fast MMR."""
    rrf, query_hits = _rrf_scores(batches)
    merged: dict[str, tuple[Any, float, tuple[str, ...]]] = {}
    sources_by_key: defaultdict[str, list[str]] = defaultdict(list)

    for batch in batches:
        for item in batch:
            key = candidate_key(item)
            if is_already_present(profile, item):
                continue
            source = _text(get_field(item, "source_provider", ""))
            if source and source not in sources_by_key[key]:
                sources_by_key[key].append(source)
            cscore, reasons = content_score(profile, item)
            previous = merged.get(key)
            if previous is None or cscore > previous[1]:
                merged[key] = (item, cscore, reasons)

    ranked: list[RankedCandidate] = []
    for key, (item, cscore, reasons) in merged.items():
        rscore = rrf.get(key, 0.0)
        hits = query_hits.get(key, 0)
        if cscore <= 0.0 and hits < 2:
            continue
        final = 0.72 * cscore + 0.28 * rscore
        reason_list = list(reasons)
        if hits >= 2 and len(reason_list) < 3:
            reason_list.append(f"retrouvé par {hits} signaux du profil")
        if not reason_list:
            reason_list.append("plusieurs signaux concordants")
        ranked.append(
            RankedCandidate(
                item=item,
                score=max(1, min(100, int(round(final)))),
                reasons=tuple(reason_list[:3]),
                sources=tuple(sources_by_key.get(key, ())),
                query_hits=hits,
            )
        )

    ranked.sort(
        key=lambda row: (
            -row.score,
            normalize(get_field(row.item, "title", "")),
            normalize(get_field(row.item, "author", "")),
        )
    )
    return _mmr_fast(
        ranked,
        exploration=max(0, min(100, int(exploration))),
        limit=max(1, int(limit)),
    )


def rank_candidates_fast(
    books: Sequence[Any],
    batches: Sequence[Sequence[Any]],
    *,
    limit: int = 120,
    exploration: int = 25,
) -> list[RankedCandidate]:
    return rank_candidates_prepared(
        build_profile(books), batches, limit=limit, exploration=exploration
    )
