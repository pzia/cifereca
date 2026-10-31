"""#3171/#3185: post-placement handoff is exclusively read-only preview."""
from pathlib import Path
from types import SimpleNamespace

from cifereca.core import canonical_post_placement as post
from cifereca.core import canonical_dedup_supervisor as supervisor


def test_post_placement_preview_never_commits(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        post, "verified_post_placement_duplicates",
        lambda *_args, **_kwargs: ("candidate-1", "candidate-2"),
    )
    def fake_supervise(_storage, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status="preview_blocked", reason="active_references")
    monkeypatch.setattr(supervisor, "supervise_canonical_duplicate", fake_supervise)
    result = post.preview_post_placement_supervised_pairs(
        object(), library_root=Path(tmp_path), placement_id="keeper",
    )
    assert result == (
        ("candidate-1", "preview_blocked", "active_references"),
        ("candidate-2", "preview_blocked", "active_references"),
    )
    assert len(calls) == 2
    assert all(
        call["action"] == "preview"
        and call["keeper_placement_id"] == "keeper"
        and call["candidate_placement_id"] in ("candidate-1", "candidate-2")
        and "confirmed" not in call and "shared_lock" not in call
        for call in calls
    )


def test_empty_post_placement_never_enters_supervisor(monkeypatch, tmp_path):
    monkeypatch.setattr(
        post, "verified_post_placement_duplicates",
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        supervisor, "supervise_canonical_duplicate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected")),
    )
    assert post.preview_post_placement_supervised_pairs(
        object(), library_root=Path(tmp_path), placement_id="keeper",
    ) == ()
