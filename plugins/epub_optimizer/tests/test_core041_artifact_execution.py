from types import SimpleNamespace

from cifereca.plugin_api import ArtifactFormat, ArtifactRef
from cifereca_epub_optimizer.ui import OptimizerPage


def artifact():
    return ArtifactRef('sha256:'+'0'*64,'book-1',ArtifactFormat('application/epub+zip','epub'),123,'0'*64,filename='Livre.epub')


class Execution:
    def __init__(self): self.calls=[]
    def execute_transform(self, request, *, transformer_plugin_id='', control=None):
        self.calls.append((request, transformer_plugin_id, control))
        return ArtifactRef('sha256:'+'1'*64,'book-1',request.source.format,99,'1'*64,variant_kind='optimized',filename='Livre [X3].epub')


class Context:
    def __init__(self, broker):
        self._broker=broker
    def broker(self, name):
        assert name=='artifact.execution'
        return self._broker


class Control:
    def cancelled(self): return False
    def check_cancelled(self): return None
    def report(self, **_kw): return None


def test_optimizer_executes_current_artifact_through_core_broker():
    execution=Execution()
    page=object.__new__(OptimizerPage)
    page.context=Context(execution)
    page.max_uncompressed_mb=512
    result=page._execute_artifact(artifact(),'device.profile.xteink-x3',Control())
    request, transformer_id, control=execution.calls[0]
    assert request.source.artifact_id.startswith('sha256:')
    assert request.capability_id=='epub.image-optimize-eink'
    assert request.profile_plugin_id=='device.profile.xteink-x3'
    assert request.options['max_uncompressed_mb']==512
    assert transformer_id=='document.transformer.epub-optimizer'
    assert result.variant_kind=='optimized'
