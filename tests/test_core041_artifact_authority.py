from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import hashlib
import shutil

from cifereca.artifact_runtime import ArtifactExecutionBrokerImpl, ArtifactRoutingBrokerImpl, LibraryMaintenanceBrokerImpl, LibraryReadBrokerImpl
from cifereca.plugin_api import (
    ArtifactFormat, ArtifactRef, ArtifactRouteRequest, LibraryMutationRequest, ResolvedPluginService,
    StorageResult, StorageTarget, TransformCapability, TransformPlan, TransformRequest, TransformResult,
)
from cifereca.storage import Storage


class Control:
    def cancelled(self): return False
    def check_cancelled(self): return None
    def report(self, **_kw): return None


class FakeTransformer:
    def __init__(self):
        self.plan_calls = 0
        self.transform_calls = 0

    def capabilities(self):
        fmt=ArtifactFormat('application/epub+zip','epub')
        return (TransformCapability('optimize',(fmt,),(fmt,),supports_plan=True),)
    def plan(self, request, source, control=None):
        self.plan_calls += 1
        return TransformPlan(ArtifactFormat('application/epub+zip','epub'),'Optimized.epub')
    def transform(self, request, source, destination, control):
        self.transform_calls += 1
        data=source.path.read_bytes()+b'-optimized'
        destination.path.write_bytes(data)
        return TransformResult(ArtifactFormat('application/epub+zip','epub'),'Optimized.epub',hashlib.sha256(data).hexdigest(),len(data))


class FakeStorageTarget:
    def __init__(self, mount: Path): self.mount=mount
    def configured_targets(self): return (StorageTarget('reader',self.mount,'Reader'),)
    def matches(self, target): return True
    def import_artifact(self, artifact, target_id, *, filename=None):
        out=self.mount/(filename or artifact.path.name); out.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(artifact.path,out)
        return StorageResult(target_id,out.name,out,True)


class FakeManager:
    def __init__(self, transformer=None, target=None): self.transformer=transformer; self.target=target
    def services(self, category):
        if category=='document.transformer' and self.transformer:
            return (ResolvedPluginService('document.transformer.test','1.0.0','pkg','1','document.transformer',self.transformer,50,'cifereca.document.transformer','1',('artifact.plan','artifact.transform'),7),)
        if category=='storage.target' and self.target:
            return (ResolvedPluginService('storage.test','1.0.0','pkg2','1','storage.target',self.target,20,'cifereca.storage','1',(),8),)
        return ()
    def statuses(self):
        return ()


def make_service(tmp_path: Path):
    storage=Storage(tmp_path/'db.sqlite3')
    source=tmp_path/'library'/'Book.epub'; source.parent.mkdir(); source.write_bytes(b'book-data')
    sha=hashlib.sha256(source.read_bytes()).hexdigest(); aid=f'sha256:{sha}'
    storage.artifact_put({'artifact_id':aid,'sha256':sha,'size':source.stat().st_size,'media_format':'epub','mime_type':'application/epub+zip','metadata':{}})
    pid=storage.placement_put({'artifact_id':aid,'path':str(source),'identity_id':'id1','provider_id':'local','source_item_id':'book-1','state':'present','metadata':{}})
    storage.catalog_entry_put({'artifact_id':aid,'placement_id':pid,'title':'Book','author':'Author','media_format':'epub','identity_id':'id1','provider_id':'local','item_id':'book-1','local_state':'local','metadata':{'path':str(source)}})
    def preview_library_filename(*,title,author='',series='',index='',extension='epub'):
        return f'{author} - {title}.{extension}' if author else f'{title}.{extension}'
    return SimpleNamespace(storage=storage,plugin_manager=None,preview_library_filename=preview_library_filename), ArtifactRef(aid,'book-1',ArtifactFormat('application/epub+zip','epub'),source.stat().st_size,sha,filename=source.name), pid


def test_schema_10_and_transform_is_core_authoritative(tmp_path):
    service, source, _pid=make_service(tmp_path)
    service.plugin_manager=FakeManager(transformer=FakeTransformer())
    broker=ArtifactExecutionBrokerImpl(service)
    result=broker.execute_transform(TransformRequest(source,'optimize'),control=Control())
    assert result.artifact_id != source.artifact_id
    assert service.storage.schema_version() == 14
    variants=broker.variants(source)
    assert len(variants)==1 and variants[0].artifact_id==result.artifact_id
    placements=[p for p in service.storage.placements() if p['artifact_id']==result.artifact_id]
    assert len(placements)==1 and Path(placements[0]['path']).read_bytes().endswith(b'-optimized')


def test_identical_transform_reuses_materialized_variant_before_plan(tmp_path):
    service, source, _pid = make_service(tmp_path)
    transformer = FakeTransformer()
    service.plugin_manager = FakeManager(transformer=transformer)
    broker = ArtifactExecutionBrokerImpl(service)

    request = TransformRequest(source, 'optimize', options={'quality': 80})
    first = broker.execute_transform(request, control=Control())
    second = broker.execute_transform(request, control=Control())

    assert second.artifact_id == first.artifact_id
    assert transformer.plan_calls == 1
    assert transformer.transform_calls == 1

    changed = TransformRequest(source, 'optimize', options={'quality': 81})
    broker.execute_transform(changed, control=Control())
    assert transformer.plan_calls == 2
    assert transformer.transform_calls == 2


def test_library_read_deduplicates_artifact_and_exposes_placements(tmp_path):
    service, source, pid=make_service(tmp_path)
    second=tmp_path/'other'/'Book.epub'; second.parent.mkdir(); shutil.copy2(tmp_path/'library'/'Book.epub',second)
    service.storage.placement_put({'artifact_id':source.artifact_id,'path':str(second),'identity_id':'id2','provider_id':'local','source_item_id':'book-1','state':'present','metadata':{}})
    view=LibraryReadBrokerImpl(service).book('book-1')
    assert view is not None
    assert [a.artifact_id for a in view.artifacts] == [source.artifact_id]
    assert {p.identity_id for p in view.placements} == {'id1','id2'}
    assert {p.kind for p in view.placements} == {'library'}


def test_library_maintenance_normalize_and_delete_duplicate(tmp_path):
    service, source, pid=make_service(tmp_path)
    broker=LibraryMaintenanceBrokerImpl(service)
    plan=broker.plan(LibraryMutationRequest('normalize_filename',placement_id=pid))
    result=broker.run(plan,Control())
    assert result.ok and (tmp_path/'library'/'Author - Book.epub').is_file()
    # Add a verified duplicate and remove only that placement.
    dup=tmp_path/'dup.epub'; shutil.copy2(tmp_path/'library'/'Author - Book.epub',dup)
    dupid=service.storage.placement_put({'artifact_id':source.artifact_id,'path':str(dup),'identity_id':'id1','provider_id':'local','source_item_id':'book-1','state':'present','metadata':{}})
    plan=broker.plan(LibraryMutationRequest('delete_duplicate',placement_id=dupid,keep_placement_id=pid))
    result=broker.run(plan,Control())
    assert result.ok and not dup.exists() and service.storage.placement_get(dupid) is None
    assert service.storage.placement_get(pid) is not None


def test_artifact_routing_selects_storage_target_and_verifies_sha(tmp_path):
    service, source, _pid=make_service(tmp_path)
    target=FakeStorageTarget(tmp_path/'reader'); service.plugin_manager=FakeManager(target=target)
    execution=ArtifactExecutionBrokerImpl(service); routing=ArtifactRoutingBrokerImpl(service,execution)
    request=ArtifactRouteRequest(source,target_id='reader')
    targets=routing.targets(request)
    assert len(targets)==1 and targets[0].target.target_id=='reader'
    result=routing.transfer(routing.plan(request),Control())
    assert result.ok and result.placement is not None and result.placement.kind=='storage'
    assert (tmp_path/'reader'/'Book.epub').is_file()
    assert result.facts['sha256']==source.sha256


def test_new_public_broker_protocols_and_version():
    from cifereca.plugin_api import ArtifactExecutionBroker, ArtifactRoutingBroker, LibraryMaintenanceBroker, LibraryReadBroker, PLUGIN_API_VERSION, MANIFEST_VERSION
    assert PLUGIN_API_VERSION == '1.4.2'
    assert MANIFEST_VERSION == 1
    assert ArtifactExecutionBroker and ArtifactRoutingBroker and LibraryMaintenanceBroker and LibraryReadBroker


def test_variant_preference_and_delete_are_core_owned(tmp_path):
    service, source, _pid=make_service(tmp_path)
    service.plugin_manager=FakeManager(transformer=FakeTransformer())
    execution=ArtifactExecutionBrokerImpl(service)
    variant=execution.execute_transform(TransformRequest(source,'optimize'),control=Control())
    maintenance=LibraryMaintenanceBrokerImpl(service)
    plan=maintenance.plan(LibraryMutationRequest('set_preferred_variant',artifact_id=variant.artifact_id,purpose='transfer'))
    maintenance.run(plan,Control())
    read=LibraryReadBrokerImpl(service)
    assert read.preferred_artifact('book-1',purpose='transfer').artifact_id == variant.artifact_id
    plan=maintenance.plan(LibraryMutationRequest('delete_variant',artifact_id=variant.artifact_id))
    result=maintenance.run(plan,Control())
    assert result.ok and service.storage.artifact_variants(artifact_id=variant.artifact_id)==[]
    assert read.preferred_artifact('book-1',purpose='transfer').artifact_id == source.artifact_id

