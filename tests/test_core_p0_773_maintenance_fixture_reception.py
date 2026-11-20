"""#773: receive generated maintenance scenarios on real Core read-only APIs."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

from cifereca.core.canonical_keeper_review import review_canonical_keeper
from cifereca.core.canonical_placement_inventory import read_canonical_placement_inventory
from cifereca.core.canonical_dedup_preview import preview_canonical_duplicate
from cifereca.core.library_organizer_review import admit_organizer_review, run_organizer_review
from cifereca.storage import Storage
from tests.test_core_p0_3176_organizer_review import _service


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "simulation"))
    from plugin_scenarios import create

    dataset = tmp_path / "dataset"
    manifest = create(dataset)
    data = json.loads((dataset / "publication-cases.json").read_text())["maintenance"]
    db = Storage(tmp_path / "state.sqlite")
    root = tmp_path / "library"
    paths, ids, artifacts = {}, {}, {}
    try:
        for identity in ("A", "B"):
            db.identity_put({"identity_id": identity, "directory_name": identity,
                             "label": identity})
        for row in data["placements"]:
            raw = (dataset / row["fixture"]).read_bytes()
            digest = sha256(raw).hexdigest()
            assert digest == row["sha256"] == manifest["files"][row["fixture"]]
            artifact = "sha256:" + digest
            path = root / row["relative_path"]
            if row.get("present", True):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            db.artifact_put({"artifact_id": artifact, "sha256": digest,
                             "size": len(raw), "media_format": path.suffix[1:]})
            ids[row["id"]] = db.placement_put({
                "artifact_id": artifact, "identity_id": row["identity"],
                "path": str(path), "state": "present" if row.get("present", True) else "missing",
            })
            paths[row["id"]] = path
            artifacts[row["id"]] = artifact
        yield db, root, paths, ids, artifacts, data
    finally:
        db.close()


def test_keeper_advice_and_missing_inventory_consume_generated_fixture(scenario):
    db, root, paths, ids, artifacts, data = scenario
    expected = data["expected"]
    prior = db.placements()
    originals = {key: path.read_bytes() for key, path in paths.items() if path.exists()}
    review = review_canonical_keeper(db, library_root=root, identity_id="A",
                                    artifact_id=artifacts["keeper"])
    assert review.status == "review_required" and review.executable is False
    assert review.keeper_placement_id == ids[expected["same_sha_same_identity_keeper"]]
    assert review.candidate_placement_ids == tuple(ids[key] for key in expected["candidate_only"])
    assert review.excluded_copies == 1
    foreign = review_canonical_keeper(db, library_root=root, identity_id="B",
                                     artifact_id=artifacts["keeper"])
    assert foreign.status == "blocked"
    inventory = read_canonical_placement_inventory(db, identity_id="A")
    groups = {group.artifact_id: group for group in inventory.groups}
    assert groups[artifacts["missing"]].reason == "placement_recovery_required"
    assert groups[artifacts["collision"]].status == "unique"
    assert not any(row.placement_id == ids["other_profile"]
                   for group in inventory.groups for row in group.placements)
    host = _service(db, root)
    scope = admit_organizer_review(host)
    result = run_organizer_review(host, scope)
    assert result is not None and result["mutation_authorized"] is False
    assert result["classification_attested"] is False
    assert db.placements() == prior
    assert {key: path.read_bytes() for key, path in paths.items() if path.exists()} == originals
    assert not paths["missing"].exists()


@pytest.mark.parametrize("attack", ["sha_changed", "symlink_escape", "scope_changed"])
def test_fresh_duplicate_preview_refuses_changed_evidence(scenario, attack):
    db, root, paths, ids, artifacts, data = scenario
    candidate = paths["duplicate"]
    if attack == "sha_changed":
        candidate.write_bytes(candidate.read_bytes() + b"changed")
    elif attack == "symlink_escape":
        candidate.unlink()
        candidate.symlink_to(paths["other_profile"])
    else:
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE placements SET identity_id='B' WHERE placement_id=?",
                        (ids["duplicate"],))
    prior = db.placements()
    originals = {key: path.read_bytes() for key, path in paths.items() if path.exists()}
    evidence = preview_canonical_duplicate(
        db, library_root=root, candidate_placement_id=ids["duplicate"],
        keeper_placement_id=ids["keeper"],
    )
    assert evidence.status == "blocked" and not evidence.may_attempt_retirement
    assert db.placements() == prior
    assert {key: path.read_bytes() for key, path in paths.items() if path.exists()} == originals
    assert candidate.is_symlink() == (attack == "symlink_escape")
