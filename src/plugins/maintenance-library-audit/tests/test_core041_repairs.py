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
    assert any('RuntimeError' in warning for warning in result.warnings)


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
    assert 'mutation refused' in result.warnings


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
    assert result.warnings == ('Doublon 1/1 : 1 avertissement(s) du service',)
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
