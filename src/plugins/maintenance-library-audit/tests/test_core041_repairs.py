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
    def __init__(self, duplicates=False): self.duplicates=duplicates
    def for_artifact(self, artifact_id):
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
    def __init__(self, duplicates=False):
        self.maintenance=Maintenance()
        self.brokers={'metadata':Metadata(),'library.naming':Naming(),'library.read':Read(duplicates),'library.maintenance':self.maintenance}
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
