"""P0 #1120: safe ADEPT staging and publish path selection is read-only."""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core.adept_acsm_plan import plan_adept_destination


def _plan(destination: Path, *, previous=None, legacy=False, force=False, nonce=None):
    return plan_adept_destination(
        destination,
        force_refresh=force,
        legacy_acsm_artifact=legacy,
        previous_path=previous,
        unique_suffix=nonce or (lambda: "fixed-nonce"),
    )


@pytest.mark.parametrize("extension", [".epub", ".pdf"])
def test_noncolliding_destination_uses_exact_caller_path(tmp_path, extension):
    destination = tmp_path / ("book" + extension)
    published, staged, repair = _plan(destination)
    assert published == destination
    assert staged == destination
    assert repair is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("force", [False, True])
def test_collision_never_overwrites_existing_book(tmp_path, force):
    original = tmp_path / "already.epub"
    original.write_bytes(b"preserve old book")
    counter = []

    def nonce():
        counter.append("called")
        return "unique-123"

    published, staged, repair = _plan(original, force=force, nonce=nonce)
    assert published == tmp_path / "already.reacquired-unique-123.epub"
    assert staged == published
    assert repair is False
    assert counter == ["called"]
    assert original.read_bytes() == b"preserve old book"
    assert not staged.exists()


def test_legacy_auto_repair_uses_distinct_repairing_file(tmp_path):
    previous = tmp_path / "historical.epub"
    previous.write_bytes(b"old protected license")
    destination = tmp_path / "book.epub"
    destination.write_bytes(b"historical collision")
    published, staged, repair = _plan(
        destination, previous=previous, legacy=True,
        nonce=lambda: pytest.fail("repair must not allocate a new identity"),
    )
    assert repair is True
    assert published == destination
    assert staged == tmp_path / "book.repairing.epub"
    assert previous.read_bytes() == b"old protected license"
    assert destination.read_bytes() == b"historical collision"
    assert not staged.exists()


def test_explicit_refresh_cannot_become_implicit_repair(tmp_path):
    previous = tmp_path / "old.pdf"
    previous.write_bytes(b"old loan")
    destination = tmp_path / "new.pdf"
    destination.write_bytes(b"existing file")
    published, staged, repair = _plan(
        destination, previous=previous, legacy=True, force=True,
    )
    assert repair is False
    assert published.name == "new.reacquired-fixed-nonce.pdf"
    assert staged == published
    assert previous.read_bytes() == b"old loan"
    assert destination.read_bytes() == b"existing file"


@pytest.mark.parametrize("previous", [None, "missing"])
def test_missing_prior_artifact_cannot_enter_repair(tmp_path, previous):
    prior = None if previous is None else tmp_path / "missing.epub"
    destination = tmp_path / "book.epub"
    destination.write_bytes(b"another book")
    published, staged, repair = _plan(destination, previous=prior, legacy=True)
    assert repair is False
    assert published.name == "book.reacquired-fixed-nonce.epub"
    assert staged == published
    assert destination.read_bytes() == b"another book"


def test_without_legacy_flag_previous_file_is_not_repaired(tmp_path):
    prior = tmp_path / "prior.epub"
    prior.write_bytes(b"old")
    destination = tmp_path / "new.epub"
    published, staged, repair = _plan(destination, previous=prior, legacy=False)
    assert (published, staged, repair) == (destination, destination, False)
    assert prior.read_bytes() == b"old"


def test_no_collision_does_not_consume_random_suffix(tmp_path):
    target = tmp_path / "brand-new.epub"
    published, staged, repair = _plan(
        target, nonce=lambda: pytest.fail("random ID only on collision")
    )
    assert published == staged == target
    assert repair is False


@pytest.mark.parametrize("kind", ["file", "directory", "dangling_symlink", "symlink"])
def test_fresh_stage_refuses_every_existing_entry(tmp_path, kind):
    from cifereca.core.adept_acsm_plan import fresh_adept_destination

    stage = tmp_path / "book.repairing.epub"
    outside = tmp_path / "outside.epub"
    outside.write_bytes(b"prior")
    if kind == "file":
        stage.write_bytes(b"staged")
    elif kind == "directory":
        stage.mkdir()
    else:
        stage.symlink_to(outside if kind == "symlink" else tmp_path / "absent")
    before = stage.lstat()
    assert fresh_adept_destination(stage) is False
    assert stage.lstat() == before
    assert outside.read_bytes() == b"prior"


def test_fresh_stage_requires_existing_nofollow_ancestors(tmp_path):
    from cifereca.core.adept_acsm_plan import fresh_adept_destination

    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    assert fresh_adept_destination(actual / "book.epub") is True
    assert fresh_adept_destination(alias / "book.epub") is False
    assert fresh_adept_destination(tmp_path / "absent" / "book.epub") is False
    assert fresh_adept_destination(Path("book.epub")) is False
    assert not list(actual.iterdir())


def test_facade_checks_stage_before_runtime_and_never_unlinks_it():
    import ast

    file = Path(__file__).parents[1] / "src/cifereca/services.py"
    tree = ast.parse(file.read_text(encoding="utf-8"))
    method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                  and node.name == "_acquire_active_loan_automatically_guarded")
    calls = [(ast.unparse(node.func), node.lineno) for node in ast.walk(method)
             if isinstance(node, ast.Call)]
    assert not any(name == "adept_destination.unlink" for name, _ in calls)
    positions = dict(calls)
    assert positions["adept_acsm_plan.fresh_adept_destination"] < positions[
        "loan_adept_fulfillment.fulfill"]
