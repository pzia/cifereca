from cifereca.plugin_api import ArtifactFormat, ArtifactRef
from cifereca_epub_optimizer.ui import OptimizerPage


def artifact(fmt='epub'):
    media = 'application/pdf' if fmt == 'pdf' else 'application/epub+zip'
    return ArtifactRef('sha256:'+'0'*64,'book-1',ArtifactFormat(media,fmt),123,'0'*64,filename='Livre.'+fmt)


class Execution:
    def __init__(self): self.calls=[]
    def execute_transform(self, request, *, transformer_plugin_id='', control=None):
        self.calls.append((request, transformer_plugin_id, control))
        return ArtifactRef('sha256:'+'1'*64,'book-1',request.source.format,99,'1'*64,variant_kind='optimized',filename='variant.'+request.source.format.format)


class Context:
    def __init__(self, broker): self._broker=broker
    def broker(self, name):
        assert name=='artifact.execution'
        return self._broker


class Control:
    def cancelled(self): return False
    def check_cancelled(self): return None
    def report(self, **_kw): return None


def make_page(execution):
    page=object.__new__(OptimizerPage)
    page.context=Context(execution)
    page.max_uncompressed_mb=512
    page.max_pdf_pages=1000
    page.max_image_pixels=20_000_000
    page.max_image_mb=64
    return page


def test_epub_uses_public_artifact_execution():
    execution=Execution()
    page=make_page(execution)
    page._execute_artifact(artifact('epub'),'device.profile.xteink-x3',Control())
    request, transformer_id, _=execution.calls[0]
    assert request.capability_id=='epub.image-optimize-eink'
    assert transformer_id=='document.transformer.epub-optimizer'
    assert request.options['max_uncompressed_mb']==512
    assert request.options['max_image_mb']==64


def test_pdf_uses_public_artifact_execution():
    execution=Execution()
    page=make_page(execution)
    page._execute_artifact(artifact('pdf'),'device.profile.xteink-x3',Control())
    request, transformer_id, _=execution.calls[0]
    assert request.capability_id=='pdf.image-optimize-eink'
    assert transformer_id=='document.transformer.pdf-eink-optimizer'
    assert request.options['max_pdf_pages']==1000
