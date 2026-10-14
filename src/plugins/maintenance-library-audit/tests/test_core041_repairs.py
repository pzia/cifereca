import pytest

from cifereca.plugin_api import (
    ArtifactFormat, ArtifactPlacementRef, ArtifactRef, BookRef, LibraryMutationResult,
    LogicalBookArtifacts, MaintenanceContext,
)
from cifereca_library_audits import DuplicateCleanupTool, NamingRepairTool


ARTIFACT='sha256:'+'a'*64


class Metadata:
    def library_entries(self, **_options):
        return [{'artifact_id':ARTIFACT,'title':'Titre','author':'Auteur','format':'EPUB'}]


class Naming:
    def preview(self, **_kwargs): return 'Auteur - Titre.epub'


class Read:
    def __init__(self, duplicates=False, fail=False):
        self.duplicates=duplicates
        self.fail=fail
    def for_artifact(self, artifact_id):
        if self.fail:
            raise RuntimeError("library.read unavailable")
        placements=[ArtifactPlacementRef('p1',artifact_id,'id1',filename='bad.epub')]
        if self.duplicates:
            placements.append(ArtifactPlacementRef('p2',artifact_id,'id1',filename='copy.epub'))
        return LogicalBookArtifacts(
            BookRef('book-1','Titre',('Auteur',)),
            artifacts=(ArtifactRef(artifact_id,'book-1',ArtifactFormat('application/epub+zip','epub'),10,'a'*64,filename='bad.epub'),),
            placements=tuple(placements),
        )


class Maintenance:
    def __init__(self): self.requests=[]
    def plan(self, request):
        self.requests.append(request)
        return type('Plan',(),{'request':request})()
    def run(self, plan, control=None):
        return LibraryMutationResult(True,changed=1,summary='ok')


class Context:
    def __init__(self, duplicates=False, read_fail=False):
        self.maintenance=Maintenance()
        self.brokers={'metadata':Metadata(),'library.naming':Naming(),'library.read':Read(duplicates,read_fail),'library.maintenance':self.maintenance}
    def broker(self,name): return self.brokers[name]


class Control:
    def cancelled(self): return False
    def check_cancelled(self): return None
    def report(self, **_kw): return None


def test_naming_repair_plans_then_delegates_to_library_maintenance():
    ctx=Context(); tool=NamingRepairTool().activate(ctx)
    plan=tool.plan(MaintenanceContext(scope='library',identity_id='id1'))
    assert plan.facts['placement_ids']==('p1',)
    result=tool.run(plan,Control())
    assert result.changed==1
    assert ctx.maintenance.requests[0].operation=='normalize_filename'
    assert ctx.maintenance.requests[0].placement_id=='p1'


def test_duplicate_cleanup_never_deletes_directly_and_uses_verified_core_operation():
    ctx=Context(duplicates=True); tool=DuplicateCleanupTool().activate(ctx)
    plan=tool.plan(MaintenanceContext(scope='library',identity_id='id1'))
    assert plan.facts['pairs']==(('p2','p1'),)
    assert tool.describe().danger_level=='destructive'
    result=tool.run(plan,Control())
    assert result.changed==1
    request=ctx.maintenance.requests[0]
    assert request.operation=='delete_duplicate'
    assert request.placement_id=='p2' and request.keep_placement_id=='p1'


def test_naming_repair_reports_library_read_failure_in_plan():
    ctx=Context(read_fail=True); tool=NamingRepairTool().activate(ctx)
    plan=tool.plan(MaintenanceContext(scope='library',identity_id='id1'))
    assert plan.facts['placement_ids']==()
    assert plan.facts['read_failures']==1
    assert any('library.read en erreur' in warning for warning in plan.warnings)


def test_duplicate_cleanup_reports_library_read_failure_in_plan():
    ctx=Context(duplicates=True,read_fail=True); tool=DuplicateCleanupTool().activate(ctx)
    plan=tool.plan(MaintenanceContext(scope='library',identity_id='id1'))
    assert plan.facts['pairs']==()
    assert plan.facts['read_failures']==1
    assert any('library.read en erreur' in warning for warning in plan.warnings)


def test_naming_repair_reports_mutation_failure_as_failed_result():
    ctx=Context(); tool=NamingRepairTool().activate(ctx)
    plan=tool.plan(MaintenanceContext(scope='library',identity_id='id1'))

    def fail_plan(_request):
        raise RuntimeError('mutation unavailable')

    ctx.maintenance.plan=fail_plan
    result=tool.run(plan,Control())

    assert result.ok is False
    assert result.changed==0
    assert result.facts['failures']==1
    assert any('RuntimeError' in warning for warning in result.warnings)


def test_duplicate_cleanup_reports_mutation_failure_as_failed_result():
    ctx=Context(duplicates=True); tool=DuplicateCleanupTool().activate(ctx)
    plan=tool.plan(MaintenanceContext(scope='library',identity_id='id1'))

    def fail_plan(_request):
        raise RuntimeError('mutation unavailable')

    ctx.maintenance.plan=fail_plan
    result=tool.run(plan,Control())

    assert result.ok is False
    assert result.changed==0
    assert result.facts['failures']==1
    # The planning warning is retained, followed by one redacted run failure.
    assert result.warnings[-1] == 'Doublon 1/1 : échec du service'
    assert any('Chaque suppression' in message for message in result.warnings)


def test_naming_repair_reports_unsuccessful_mutation_result():
    ctx=Context(); tool=NamingRepairTool().activate(ctx)
    plan=tool.plan(MaintenanceContext(scope='library',identity_id='id1'))
    ctx.maintenance.run=lambda _plan, _control=None: LibraryMutationResult(
        False,changed=0,summary='failed',warnings=('mutation refused',)
    )

    result=tool.run(plan,Control())

    assert result.ok is False
    assert result.changed==0
    assert result.facts['failures']==1
    assert 'Renormalisation 1/1 : 1 avertissement(s) du service' in result.warnings
    assert all('mutation refused' not in warning for warning in result.warnings)


def test_duplicate_cleanup_reports_unsuccessful_mutation_result():
    ctx=Context(duplicates=True); tool=DuplicateCleanupTool().activate(ctx)
    plan=tool.plan(MaintenanceContext(scope='library',identity_id='id1'))
    ctx.maintenance.run=lambda _plan, _control=None: LibraryMutationResult(
        False,changed=0,summary='failed',warnings=('mutation refused',)
    )

    result=tool.run(plan,Control())

    assert result.ok is False
    assert result.changed==0
    assert result.facts['failures']==1
    assert 'Doublon 1/1 : 1 avertissement(s) du service' in result.warnings
    assert all('mutation refused' not in warning for warning in result.warnings)


class UserCancelled(Exception):
    """Test-only cancellation raised by the maintenance control."""


class CancelControl(Control):
    def __init__(self):
        self.stopped = False

    def cancelled(self):
        return self.stopped

    def check_cancelled(self):
        if self.stopped:
            raise UserCancelled("explicit cleanup cancellation")


def test_duplicate_cleanup_does_not_swallow_cancellation_inside_mutation():
    """#1358: a cancelled Core deletion is not an ordinary failed file."""
    ctx = Context(duplicates=True)
    tool = DuplicateCleanupTool().activate(ctx)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="id1"))
    control = CancelControl()

    def interrupted(_plan, _control=None):
        control.stopped = True
        raise OSError("mutation interrupted by cancellation")

    ctx.maintenance.run = interrupted
    with pytest.raises(UserCancelled, match="explicit cleanup cancellation"):
        tool.run(plan, control)
    assert len(ctx.maintenance.requests) == 1
    assert ctx.maintenance.requests[0].operation == "delete_duplicate"


def test_duplicate_cleanup_checks_cancel_after_final_mutation():
    """#1358: a final item cancelling mid-flight cannot report 'complete'."""
    ctx = Context(duplicates=True)
    tool = DuplicateCleanupTool().activate(ctx)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="id1"))
    control = CancelControl()

    def cancel_while_finishing(_plan, _control=None):
        control.stopped = True
        return LibraryMutationResult(True, changed=1, summary="last mutation committed")

    ctx.maintenance.run = cancel_while_finishing
    with pytest.raises(UserCancelled, match="explicit cleanup cancellation"):
        tool.run(plan, control)
    assert len(ctx.maintenance.requests) == 1


def test_naming_repair_private_preview_and_core_warnings_are_redacted():
    class PrivateMetadata:
        def library_entries(self, **_options):
            return [{
                "artifact_id": ARTIFACT,
                "title": "PRIVATE_BOOK_TITLE", "author": "PRIVATE_AUTHOR",
            }]

    class PrivateRead:
        def for_artifact(self, artifact_id):
            assert artifact_id == ARTIFACT
            return LogicalBookArtifacts(
                BookRef("PRIVATE_BOOK_ID", "PRIVATE_BOOK_TITLE", ("PRIVATE_AUTHOR",)),
                artifacts=(),
                placements=(ArtifactPlacementRef(
                    "PRIVATE_PLACEMENT_ID", artifact_id, "id1",
                    filename="PRIVATE_SOURCE_TOKEN.epub",
                ),),
            )

    class PrivateNaming:
        def preview(self, **_options):
            return "PRIVATE_EXPECTED_TOKEN.epub"

    class CaptureControl(Control):
        def __init__(self):
            self.events = []

        def report(self, **event):
            self.events.append(dict(event))

    ctx = Context()
    ctx.brokers["metadata"] = PrivateMetadata()
    ctx.brokers["library.read"] = PrivateRead()
    ctx.brokers["library.naming"] = PrivateNaming()
    ctx.maintenance.run = lambda _plan, _control=None: LibraryMutationResult(
        False, changed=1, summary="PRIVATE_RESPONSE_TITLE",
        warnings=(
            "Bearer PRIVATE_ACCESS_TOKEN",
            "/home/PRIVATE_USER/books/secret.epub",
        ),
    )

    tool = NamingRepairTool().activate(ctx)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="id1"))
    assert plan.facts["placement_ids"] == ("PRIVATE_PLACEMENT_ID",)
    assert plan.changes == ("Renommer le placement n°1",)
    assert plan.warnings == ()

    control = CaptureControl()
    result = tool.run(plan, control)
    assert not result.ok and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 1}
    assert result.warnings == (
        "Renormalisation 1/1 : 2 avertissement(s) du service",
    )
    assert [(event.get("current"), event.get("total"))
            for event in control.events] == [(0, 1), (1, 1)]
    assert "échec" in control.events[-1]["message"]
    assert ctx.maintenance.requests[0].operation == "normalize_filename"
    assert ctx.maintenance.requests[0].placement_id == "PRIVATE_PLACEMENT_ID"

    public = str((
        plan.summary, plan.changes, plan.warnings, result.summary,
        result.warnings, result.facts, control.events,
    ))
    for private in (
        "PRIVATE_", "Bearer", "secret.epub", "/home/", "TOKEN",
        "BOOK_TITLE", "EXPECTED", "ACCESS",
    ):
        assert private not in public


def test_naming_repair_nonsequence_core_warning_is_counted_not_iterated():
    ctx = Context()
    tool = NamingRepairTool().activate(ctx)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="id1"))

    def broker_result(_plan, _control):
        return type("Result", (), {
            "ok": True, "changed": 0,
            "warnings": "Bearer PRIVATE_ACCESS_TOKEN",
        })()

    ctx.maintenance.run = broker_result
    result = tool.run(plan, Control())
    assert result.ok and result.changed == 0
    assert result.warnings == (
        "Renormalisation 1/1 : 1 avertissement(s) du service",
    )
