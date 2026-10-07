from cifereca.plugin_api import ArtifactFormat, ArtifactRef, UiActionContext
from cifereca_epub_optimizer_plugin import EpubOptimizerPlugin


def artifact(fmt='epub', drm='none'):
    media = 'application/pdf' if fmt == 'pdf' else 'application/epub+zip'
    return ArtifactRef('a1','b1',ArtifactFormat(media,fmt,drm),123,'0'*64,filename='Livre.'+fmt)


def test_book_detail_action_visible_for_plain_epub_or_pdf_only():
    plugin=EpubOptimizerPlugin()
    assert plugin._visible_for_document(UiActionContext(target='book.detail',artifact=artifact('epub')))
    assert plugin._visible_for_document(UiActionContext(target='book.detail',artifact=artifact('pdf')))
    assert not plugin._visible_for_document(UiActionContext(target='book.detail',artifact=None))
    assert not plugin._visible_for_document(UiActionContext(target='book.detail',artifact=artifact('mobi')))
    assert not plugin._visible_for_document(UiActionContext(target='book.detail',artifact=artifact('epub','adept')))


def test_action_uses_public_host_and_keeps_stable_route():
    plugin=EpubOptimizerPlugin()
    current=artifact()
    class Host:
        def action_context(self): return UiActionContext(target='book.detail',artifact=current)
        def navigate(self, route): self.route=route
        def toast(self,*args,**kwargs): pass
    host=Host()
    result=plugin._open_from_detail(host)
    assert result is current
    assert host.route=='epub-optimize'
    assert plugin._pending_artifact is current
