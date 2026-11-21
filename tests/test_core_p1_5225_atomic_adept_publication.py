"""#5225: an ADEPT publish exception may not delete a previous valid artifact."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace as NS
import uuid

import pytest

from cifereca.core import adept_acsm_plan, adept_artifact_publication
from cifereca.providers import ProviderConfigurationError


def _publish(tmp_path, *, repair, fail_replace, monkeypatch):
    previous_bytes = b"OLD-VALID-ARTIFACT"
    new_bytes = b"NEW-VALID-ARTIFACT"
    produced = tmp_path / "new.epub"
    produced.write_bytes(new_bytes)
    destination = tmp_path / "existing.epub"
    destination.write_bytes(previous_bytes)
    old = tmp_path / "prior.epub"
    old.write_bytes(b"PRIOR-LEDGER-ARTIFACT")
    if fail_replace:
        def rejected_link(*_args, **_kw):
            raise OSError("synthetic: rename denied")
        monkeypatch.setattr(os, "link", rejected_link)
    inspection = lambda path, *_args: NS(
        valid=True, media_format="epub", protected=False,
    )
    def operation():
        return adept_artifact_publication.publish(
            result={"artifact": {"path": str(produced), "format": "epub"}},
            adept_destination=produced, acsm_format="epub",
            loan=NS(
                id="synthetic-loan", title="synthetic-title",
                record_id="synthetic-edition", source_provider="synthetic",
                source_credential="synthetic-credential",
                source_account_id="synthetic-account", identity_id="synthetic-owner",
            ),
            repair_mode=repair, destination=destination, previous_path=old,
            Path=Path, ProviderConfigurationError=ProviderConfigurationError,
            inspect_artifact=inspection, adept_acsm_plan=adept_acsm_plan,
            uuid=uuid, _TRACE_LOG=logging.getLogger("synthetic"),
        )
    return operation, produced, destination, old, new_bytes, previous_bytes


@pytest.mark.parametrize("repair", [False, True])
def test_rename_error_preserves_previous_valid_publication(
    tmp_path, monkeypatch, repair,
):
    run, produced, destination, old, new, previous = _publish(
        tmp_path, repair=repair, fail_replace=True, monkeypatch=monkeypatch,
    )
    with pytest.raises(OSError, match="rename denied"):
        run()
    assert destination.is_file() and destination.read_bytes() == previous
    assert produced.is_file() and produced.read_bytes() == new
    assert old.read_bytes() == b"PRIOR-LEDGER-ARTIFACT"


@pytest.mark.parametrize("repair", [False, True])
def test_no_clobber_commits_new_bytes_without_unlink_first(
    tmp_path, monkeypatch, repair,
):
    run, produced, destination, old, new, previous = _publish(
        tmp_path, repair=repair, fail_replace=False, monkeypatch=monkeypatch,
    )
    final_path, *_rest = run()
    assert final_path != destination
    assert final_path.name.startswith("existing.reacquired-")
    assert final_path.read_bytes() == new
    assert destination.read_bytes() == previous
    assert not produced.exists()
    # Neither a physical publication nor a completed receipt proves
    # that an earlier file has no remaining dependencies.
    assert old.exists()
    assert adept_artifact_publication.cleanup_after_commit(
        previous_path=old, final_path=destination, repair_mode=repair,
        Path=Path, loan=NS(id="synthetic-loan", identity_id="owner"),
        _TRACE_LOG=logging.getLogger("synthetic"),
    ) is False
    assert old.read_bytes() == b"PRIOR-LEDGER-ARTIFACT"
    assert destination.read_bytes() == previous


def test_cleanup_never_removes_cross_profile_or_symlinked_prior(
    tmp_path, monkeypatch,
):
    a = tmp_path / "profile-A"
    b = tmp_path / "profile-B"
    a.mkdir()
    b.mkdir()
    previous = a / "private.epub"
    previous.write_bytes(b"OLD")
    final = b / "current.epub"
    final.write_bytes(b"NEW")
    kwargs = dict(
        previous_path=previous, final_path=final,
        repair_mode=True, Path=Path, _TRACE_LOG=logging.getLogger("synthetic"),
        loan=NS(id="synthetic", identity_id="owner"),
    )
    assert adept_artifact_publication.cleanup_after_commit(**kwargs) is False
    assert previous.read_bytes() == b"OLD"

    alias = b / "private.epub"
    alias.symlink_to(previous)
    assert adept_artifact_publication.cleanup_after_commit(
        **{**kwargs, "previous_path": alias}
    ) is False
    assert alias.is_symlink() and previous.read_bytes() == b"OLD"


def test_cleanup_does_not_attempt_unlink_after_committed_publication(
    tmp_path, monkeypatch,
):
    previous = tmp_path / "old.epub"
    previous.write_bytes(b"OLD")
    final = tmp_path / "final.epub"
    final.write_bytes(b"NEW")
    real_unlink = Path.unlink
    def reject(self, *args, **kwargs):
        if self == previous:
            pytest.fail("previous artifact must not be unlinked")
        return real_unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", reject)
    assert adept_artifact_publication.cleanup_after_commit(
        previous_path=previous, final_path=final, repair_mode=True,
        Path=Path, _TRACE_LOG=logging.getLogger("synthetic"),
        loan=NS(id="synthetic", identity_id="owner"),
    ) is False
    assert previous.read_bytes() == b"OLD"
    assert final.read_bytes() == b"NEW"


def test_services_invokes_cleanup_only_after_index_and_completed_ledger():
    # Protect the ordering contract against future extraction/reorganisation.
    services = (
        Path(__file__).resolve().parents[1] / "src/cifereca/services.py"
    ).read_text(encoding="utf-8")
    beginning = services.index("def _acquire_active_loan_automatically_guarded(")
    ending = services.index("\n    def ", beginning + 6)
    routine = services[beginning:ending]
    indexed = routine.index("loan_provider_delivery.index_local_loan_artifact(")
    receipt = routine.index("loan_completion_receipt.completed_receipt(")
    cleanup = routine.index("adept_artifact_publication.cleanup_after_commit(")
    assert indexed < receipt < cleanup
    assert routine.index("if acquisition.kind == \"acsm\":", receipt) < cleanup


@pytest.mark.parametrize("target", ["produced", "destination"])
def test_symlinked_adept_input_or_target_never_replaces_valid_bytes(
    tmp_path, monkeypatch, target,
):
    run, produced, destination, old, new, previous = _publish(
        tmp_path, repair=True, fail_replace=False, monkeypatch=monkeypatch,
    )
    external = tmp_path / "unrelated.epub"
    external.write_bytes(b"UNRELATED-VALID-ARTIFACT")
    selected = produced if target == "produced" else destination
    selected.unlink()
    selected.symlink_to(external)
    with pytest.raises(ProviderConfigurationError, match="Chemin d"):
        run()
    assert selected.is_symlink()
    assert external.read_bytes() == b"UNRELATED-VALID-ARTIFACT"
    assert old.read_bytes() == b"PRIOR-LEDGER-ARTIFACT"
    if target == "produced":
        assert destination.read_bytes() == previous
    else:
        assert produced.read_bytes() == new


def test_adept_publisher_rejects_symlinked_staging_directory(
    tmp_path, monkeypatch,
):
    source_dir = tmp_path / "actual-staging"
    source_dir.mkdir()
    actual = source_dir / "new.epub"
    actual.write_bytes(b"NEW")
    alias_dir = tmp_path / "stage-alias"
    alias_dir.symlink_to(source_dir, target_is_directory=True)
    alias = alias_dir / "new.epub"
    destination = tmp_path / "existing.epub"
    destination.write_bytes(b"OLD")

    with pytest.raises(ProviderConfigurationError, match="Chemin d"):
        adept_artifact_publication.publish(
            result={"artifact": {"path": str(alias), "format": "epub"}},
            adept_destination=alias, acsm_format="epub",
            loan=NS(id="synthetic", identity_id="A", source_provider="test"),
            repair_mode=False, destination=destination, previous_path=None,
            Path=Path, ProviderConfigurationError=ProviderConfigurationError,
            inspect_artifact=lambda *_a, **_k: pytest.fail("no inspection"),
            adept_acsm_plan=adept_acsm_plan, uuid=uuid,
            _TRACE_LOG=logging.getLogger("synthetic"),
        )
    assert actual.read_bytes() == b"NEW"
    assert destination.read_bytes() == b"OLD"


def test_nonrepair_adept_new_destination_is_published_at_exact_name(
    tmp_path, monkeypatch,
):
    run, produced, target, old, fresh, previous = _publish(
        tmp_path, repair=False, fail_replace=False, monkeypatch=monkeypatch,
    )
    target.unlink()  # no prior publication
    result, *_rest = run()
    assert result == target
    assert result.read_bytes() == fresh
    assert not produced.exists()
    assert old.is_file()


@pytest.mark.parametrize("repair", [False, True])
def test_adept_collision_exhaustion_keeps_old_bytes(
    tmp_path, monkeypatch, repair,
):
    run, produced, target, old, fresh, previous = _publish(
        tmp_path, repair=repair, fail_replace=False, monkeypatch=monkeypatch,
    )
    # A stable nonce collides across retries. Never overwrite either file
    # or infer ownership from a matching title/digest or source provider.
    alternate = target.with_name("existing.reacquired-stable.epub")
    alternate.write_bytes(b"OTHER RIGHT")
    monkeypatch.setattr(
        uuid, "uuid4", lambda: NS(hex="stable"),
    )
    with pytest.raises(ProviderConfigurationError, match="occupée"):
        run()
    assert target.read_bytes() == previous
    assert alternate.read_bytes() == b"OTHER RIGHT"
    assert produced.read_bytes() == fresh
    assert old.read_bytes() == b"PRIOR-LEDGER-ARTIFACT"


def test_nonrepair_adept_symlink_swap_during_commit_refuses_source(
    tmp_path, monkeypatch,
):
    run, produced, destination, old, fresh, previous = _publish(
        tmp_path, repair=False, fail_replace=False, monkeypatch=monkeypatch,
    )
    outside = tmp_path / "foreign.epub"
    outside.write_bytes(b"FOREIGN")
    real_link = os.link

    def swapped_source(src, dst, **kwargs):
        # The sole producer stage is replaced *after* the inspector and
        # initial stat, but before exclusive hardlink creation.
        produced.unlink()
        produced.symlink_to(outside)
        return real_link(src, dst, **kwargs)

    monkeypatch.setattr(os, "link", swapped_source)
    with pytest.raises(ProviderConfigurationError, match="modifié"):
        run()
    assert destination.read_bytes() == previous
    assert outside.read_bytes() == b"FOREIGN"
    assert produced.is_symlink()
    assert not list(tmp_path.glob("existing.reacquired-*"))
    assert old.read_bytes() == b"PRIOR-LEDGER-ARTIFACT"


def test_adept_nonrepair_cross_filesystem_stage_is_never_hardlinked(
    tmp_path, monkeypatch,
):
    """Simulate EXDEV for stage→library; only library-local links are allowed."""
    import errno

    stage = tmp_path / "other-volume"
    stage.mkdir()
    library = tmp_path / "identity" / "Acquisitions"
    library.mkdir(parents=True)
    produced = stage / "new.epub"
    produced.write_bytes(b"NEW FROM OTHER MOUNT")
    destination = library / "book.epub"
    destination.write_bytes(b"OLD VALID LOAN")
    real_link = os.link
    calls = []

    def across_mount_is_invalid(src, dst, **kwargs):
        assert kwargs["src_dir_fd"] == kwargs["dst_dir_fd"]
        fd = kwargs["src_dir_fd"]
        assert os.fstat(fd).st_ino == library.stat().st_ino
        src, dst = library / src, library / dst
        calls.append((src, dst))
        if src.parent != dst.parent:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_link(src.name, dst.name, **kwargs)

    monkeypatch.setattr(os, "link", across_mount_is_invalid)
    published = adept_artifact_publication._publish_new_adept_without_clobber(
        produced, destination, uuid=uuid,
        ProviderConfigurationError=ProviderConfigurationError,
    )
    assert published != destination
    assert published.parent == library
    assert published.read_bytes() == b"NEW FROM OTHER MOUNT"
    assert destination.read_bytes() == b"OLD VALID LOAN"
    assert not produced.exists()
    assert calls
    assert all(src.parent == library for src, dst in calls)
    assert not list(library.glob(".adept-publish-*"))


def test_adept_nonrepair_failed_link_preserves_stage_and_cleans_private_temp(
    tmp_path, monkeypatch,
):
    stage = tmp_path / "other-volume"
    stage.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    produced = stage / "new.epub"
    produced.write_bytes(b"NEW FROM OTHER MOUNT")
    destination = library / "existing.epub"
    destination.write_bytes(b"OLD VALID LOAN")
    def denied(*_args, **_kwargs):
        raise OSError("synthetic publication denied")
    monkeypatch.setattr(os, "link", denied)

    with pytest.raises(OSError, match="publication denied"):
        adept_artifact_publication._publish_new_adept_without_clobber(
            produced, destination, uuid=uuid,
            ProviderConfigurationError=ProviderConfigurationError,
        )
    assert produced.read_bytes() == b"NEW FROM OTHER MOUNT"
    assert destination.read_bytes() == b"OLD VALID LOAN"
    assert not list(library.glob(".adept-publish-*"))


def test_adept_nonrepair_copy_fails_closed_if_staged_inode_changes_while_reading(
    tmp_path, monkeypatch,
):
    """The staging copy is not a licence to adopt a swapped file."""
    stage = tmp_path / "other-volume"
    stage.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    produced = stage / "new.epub"
    produced.write_bytes(b"OLD STAGE")
    foreign = stage / "foreign.epub"
    foreign.write_bytes(b"UNRELATED")
    destination = library / "published.epub"
    real_open = os.open
    def swap_at_open(path, *args, **kwargs):
        if Path(path) == Path(produced.name) and kwargs.get("dir_fd") is not None:
            produced.unlink()
            produced.symlink_to(foreign)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(os, "open", swap_at_open)
    with pytest.raises((OSError, ProviderConfigurationError)):
        adept_artifact_publication._publish_new_adept_without_clobber(
            produced, destination, uuid=uuid,
            ProviderConfigurationError=ProviderConfigurationError,
        )
    assert not destination.exists()
    assert foreign.read_bytes() == b"UNRELATED"
    assert not list(library.glob(".adept-publish-*"))


@pytest.mark.parametrize("phase", ["link", "fsync", "fsync_error"])
def test_adept_directory_durability_refusal_preserves_stage_and_foreign_scope(
    tmp_path, monkeypatch, phase,
):
    import stat

    stage = tmp_path / "stage"
    stage.mkdir()
    library = tmp_path / "library"
    library.mkdir()
    retired = tmp_path / "retired"
    produced = stage / "new.epub"
    produced.write_bytes(b"NEW")
    final = library / "book.epub"
    old = library / "previous.epub"
    old.write_bytes(b"OLD")
    real_link, real_fsync = os.link, os.fsync
    injected = []

    def rebind():
        library.rename(retired)
        library.mkdir()
        final.write_bytes(b"FOREIGN")

    def link(src, dst, **kw):
        if phase == "link" and not injected:
            injected.append(True)
            rebind()
        return real_link(src, dst, **kw)

    def fsync(fd):
        if (not injected and stat.S_ISDIR(os.fstat(fd).st_mode)
                and final.exists()):
            assert produced.read_bytes() == b"NEW"
            injected.append(True)
            if phase == "fsync_error":
                raise OSError("synthetic directory fsync")
            rebind()
        return real_fsync(fd)

    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises((OSError, ProviderConfigurationError)):
        adept_artifact_publication._publish_new_adept_without_clobber(
            produced, final, uuid=uuid, ProviderConfigurationError=ProviderConfigurationError,
        )
    assert injected == [True] and produced.read_bytes() == b"NEW"
    if phase == "fsync_error":
        assert list(library.iterdir()) == [old]
        assert old.read_bytes() == b"OLD"
    else:
        assert final.read_bytes() == b"FOREIGN"
        assert list(retired.iterdir()) == [retired / old.name]
        assert (retired / old.name).read_bytes() == b"OLD"


def test_adept_nested_destination_symlink_never_creates_in_foreign_scope(tmp_path):
    stage = tmp_path / "stage.epub"
    stage.write_bytes(b"NEW")
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProviderConfigurationError, match="inattendu"):
        adept_artifact_publication._publish_new_adept_without_clobber(
            stage, alias / "nested" / "final.epub", uuid=uuid,
            ProviderConfigurationError=ProviderConfigurationError,
        )
    assert not list(outside.iterdir()) and stage.read_bytes() == b"NEW"


def test_adept_final_directory_synced_while_stage_exists(tmp_path, monkeypatch):
    import stat

    stage = tmp_path / "stage.epub"
    stage.write_bytes(b"NEW")
    final = tmp_path / "final.epub"
    real = os.fsync
    seen = []

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) and final.exists():
            assert stage.read_bytes() == final.read_bytes() == b"NEW"
            seen.append(True)
        return real(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    result = adept_artifact_publication._publish_new_adept_without_clobber(
        stage, final, uuid=uuid, ProviderConfigurationError=ProviderConfigurationError,
    )
    assert seen == [True] and result == final and not stage.exists()


@pytest.mark.parametrize("same_path", [False, True])
def test_adept_stage_requires_single_private_link(tmp_path, same_path):
    """Even identical bytes and inode are not authority to consume a shared stage."""
    stage = tmp_path / "stage"
    stage.mkdir()
    produced = stage / "source.epub"
    produced.write_bytes(b"PRIVATE SOURCE")
    other_owner = tmp_path / "another-owner.epub"
    os.link(produced, other_owner)
    destination = produced if same_path else tmp_path / "destination.epub"
    with pytest.raises(ProviderConfigurationError, match="invalide"):
        adept_artifact_publication._publish_new_adept_without_clobber(
            produced, destination, uuid=uuid,
            ProviderConfigurationError=ProviderConfigurationError,
        )
    assert produced.read_bytes() == other_owner.read_bytes() == b"PRIVATE SOURCE"
    if not same_path:
        assert not destination.exists()
    assert not list(tmp_path.glob(".adept-publish-*"))


def test_adept_stage_link_added_during_final_commit_refuses(tmp_path, monkeypatch):
    """A new outside hardlink changes nlink/ctime without altering size or mtime."""
    stage = tmp_path / "stage"
    stage.mkdir()
    produced = stage / "source.epub"
    produced.write_bytes(b"UNCHANGED CONTENT")
    destination = tmp_path / "destination.epub"
    outside = tmp_path / "other-owner.epub"
    original_link = os.link
    injected = []

    def attach_other_owner(source, target, **kwargs):
        if not injected:
            injected.append(True)
            original_link(produced, outside)
        return original_link(source, target, **kwargs)

    monkeypatch.setattr(os, "link", attach_other_owner)
    with pytest.raises(ProviderConfigurationError, match="modifié"):
        adept_artifact_publication._publish_new_adept_without_clobber(
            produced, destination, uuid=uuid,
            ProviderConfigurationError=ProviderConfigurationError,
        )
    assert injected == [True]
    assert produced.read_bytes() == outside.read_bytes() == b"UNCHANGED CONTENT"
    assert not destination.exists()
    assert not list(tmp_path.glob(".adept-publish-*"))


@pytest.mark.parametrize("artifact", [["not", "a", "mapping"], "malformed", 42, b"bad"])
def test_adept_runtime_nonmapping_artifact_refused_before_inspection(tmp_path, artifact):
    produced = tmp_path / "adept-stage.epub"
    produced.write_bytes(b"STAGED DOCUMENT")
    destination = tmp_path / "published.epub"
    destination.write_bytes(b"EXISTING DOCUMENT")

    with pytest.raises(ProviderConfigurationError, match="Résultat ADEPT invalide"):
        adept_artifact_publication.publish(
            result={"artifact": artifact},
            adept_destination=produced, acsm_format="epub",
            loan=NS(id="synthetic-loan", identity_id="identity-A", source_provider="test"),
            repair_mode=False, destination=destination, previous_path=None,
            Path=Path, ProviderConfigurationError=ProviderConfigurationError,
            inspect_artifact=lambda *_args, **_kwargs: pytest.fail("no inspection"),
            adept_acsm_plan=adept_acsm_plan, uuid=uuid,
            _TRACE_LOG=logging.getLogger("synthetic"),
        )
    assert produced.read_bytes() == b"STAGED DOCUMENT"
    assert destination.read_bytes() == b"EXISTING DOCUMENT"
    assert not list(tmp_path.glob(".adept-publish-*"))


def test_adept_stage_cannot_be_adopted_as_its_final_library_destination(tmp_path):
    """The final name requires its own exclusive durable publication step."""
    produced = tmp_path / "unpublished-stage.epub"
    produced.write_bytes(b"PRIVATE STAGING BYTES")
    with pytest.raises(ProviderConfigurationError, match="inattendu"):
        adept_artifact_publication._publish_new_adept_without_clobber(
            produced, produced, uuid=uuid,
            ProviderConfigurationError=ProviderConfigurationError,
        )
    assert produced.read_bytes() == b"PRIVATE STAGING BYTES"
    assert produced.stat().st_nlink == 1
    assert list(tmp_path.iterdir()) == [produced]



def test_adept_planned_stage_is_independent_and_published_through_bridge(tmp_path):
    """#5225 staging policy and the physical bridge form one admission contract."""
    destination = tmp_path / "book.epub"
    final, staged, repair = adept_acsm_plan.plan_adept_destination(
        destination, force_refresh=False, legacy_acsm_artifact=False,
        previous_path=None, unique_suffix=lambda: "nonce",
    )
    assert repair is False and final == destination and staged != final
    assert staged.parent == final.parent and staged.name.startswith(".adept-stage-")
    assert not final.exists()
    staged.write_bytes(b"VALIDATED-DOWNLOAD")
    published, *_rest = adept_artifact_publication.publish(
        result={"artifact": {"path": str(staged), "format": "epub"}},
        adept_destination=staged, acsm_format="epub",
        loan=NS(id="synthetic-loan", identity_id="A", source_provider="test"),
        repair_mode=repair, destination=final, previous_path=None,
        Path=Path, ProviderConfigurationError=ProviderConfigurationError,
        inspect_artifact=lambda *_args: NS(
            valid=True, media_format="epub", protected=False,
        ),
        adept_acsm_plan=adept_acsm_plan, uuid=uuid,
        _TRACE_LOG=logging.getLogger("synthetic"),
    )
    assert published == destination and published.read_bytes() == b"VALIDATED-DOWNLOAD"
    assert not staged.exists()
    assert not list(tmp_path.glob(".adept-publish-*"))


def test_adept_source_alias_added_after_final_fsync_does_not_retire_stage(
    tmp_path, monkeypatch,
):
    """A changed source after the last link/fsync witness cannot be unlinked."""
    from cifereca.core import loan_direct_publication as direct

    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    staged = stage_dir / "new.epub"
    staged.write_bytes(b"PRIVATE-NEW-ARTIFACT")
    final = tmp_path / "published.epub"
    external = tmp_path / "new-external-owner.epub"
    original = direct._source_current
    calls = []

    def alias_after_last_postlink_witness(fd, name, expected):
        still_owned = original(fd, name, expected)
        calls.append(still_owned)
        if len(calls) == 4 and still_owned:
            # The fourth call occurs after durable final fsync; a second
            # hardlink changes ctime and single-link source ownership.
            os.link(staged, external)
        return still_owned

    monkeypatch.setattr(direct, "_source_current", alias_after_last_postlink_witness)
    with pytest.raises(ProviderConfigurationError, match="modifié"):
        adept_artifact_publication._publish_new_adept_without_clobber(
            staged, final, uuid=uuid,
            ProviderConfigurationError=ProviderConfigurationError,
        )
    assert len(calls) >= 5
    assert staged.read_bytes() == external.read_bytes() == b"PRIVATE-NEW-ARTIFACT"
    assert not final.exists()  # the uncommitted final link was withdrawn
    assert not list(tmp_path.glob(".adept-publish-*"))
