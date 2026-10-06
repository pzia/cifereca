from cifereca.plugin_api import ArtifactFormat, ArtifactRef, UiActionContext
from cifereca_epub_optimizer_plugin import EpubOptimizerPlugin


def artifact(fmt='epub', drm='none'):
    return ArtifactRef('a1','b1',ArtifactFormat('application/epub+zip',fmt,drm),123,'0'*64,filename='Livre.epub')


def test_book_detail_action_visible_only_for_local_plain_epub():
    plugin=EpubOptimizerPlugin()
    assert plugin._visible_for_epub(UiActionContext(target='book.detail',artifact=artifact()))
    assert not plugin._visible_for_epub(UiActionContext(target='book.detail',artifact=None))
    assert not plugin._visible_for_epub(UiActionContext(target='book.detail',artifact=artifact('pdf')))
    assert not plugin._visible_for_epub(UiActionContext(target='book.detail',artifact=artifact('epub','adept')))


def test_action_uses_public_host_and_navigates_without_private_path():
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
