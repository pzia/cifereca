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


def test_optimized_copy_targets_exact_core_placement_for_each_profile():
    """A global SHA does not identify which original file Core may read."""
    from dataclasses import replace

    execution = Execution()
    page = make_page(execution)
    shared = artifact("epub")
    alice = replace(
        shared, identity_id="alice", placement_id="original-alice",
        filename="/local/alice/Book.epub",
    )
    bob = replace(
        shared, identity_id="bob", placement_id="original-bob",
        filename="/local/bob/Book.epub",
    )
    for selected in (alice, bob):
        page._execute_artifact(selected, "device.profile.test", Control())

    first, second = (item[0] for item in execution.calls)
    assert first.source is alice and second.source is bob
    assert (first.identity_id, first.placement_id) == (
        "alice", "original-alice",
    )
    assert (second.identity_id, second.placement_id) == (
        "bob", "original-bob",
    )
    assert first.options == second.options
    assert first.source.sha256 == second.source.sha256
    assert "local" not in first.options and "local" not in second.options


def test_legacy_and_incomplete_artifacts_do_not_invent_a_placement():
    from dataclasses import replace

    execution = Execution()
    page = make_page(execution)
    original = artifact("pdf")
    page._execute_artifact(original, "device.profile.test", Control())
    legacy = execution.calls[-1][0]
    assert legacy.identity_id is None and legacy.placement_id is None

    # If Core only supplied one coordinate, it must reach Core unchanged,
    # which rejects partial source scopes before reading any bytes.
    incomplete = replace(original, identity_id="alice")
    page._execute_artifact(incomplete, "device.profile.test", Control())
    request = execution.calls[-1][0]
    assert request.identity_id == "alice"
    assert request.placement_id is None
