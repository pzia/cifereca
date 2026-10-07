"""Educational in-memory catalogue, never used as a real source."""
from cifereca.plugin_api import Account, ProviderCapabilities, ProviderDescriptor, ProviderPlugin, ProviderSession, SearchResult


DESCRIPTOR = ProviderDescriptor(id='example', display_name='Exemple', description='Catalogue pédagogique hors ligne', state='ready',
    capabilities=ProviderCapabilities(search=True), homepage='https://example.invalid')


class Session(ProviderSession):
    def connect(self):
        self.connected = True
        self._accounts = [Account(unique_id='public', provider_id=self.provider_id, credential_id=self.credential_id)]
        return self.accounts

    def close(self):
        self.connected = False

    def search(self, query, scope='all', page=0, field='all'):
        if page != 0 or scope == 'physical': return []
        title = 'Livre pédagogique'
        if query and query.casefold() not in title.casefold(): return []
        return [SearchResult(id='example:one', record_id='one', docbase='EXAMPLE', medium='digital', title=title,
            author='Auteur exemple', type='Livre numérique', format='epub', publisher=None, published_at=None,
            description='Résultat synthétique pour tester la projection.', thumbnail_url=None,
            friendly_url='https://example.invalid/books/one', scenario='public', source_provider=self.provider_id,
            source_credential=self.credential_id)]


class Catalogue(ProviderPlugin):
    descriptor = DESCRIPTOR
    def create_session(self, *, credential_id, label, credentials, options, config, storage, diagnostics):
        return Session(descriptor=self.descriptor, credential_id=credential_id, label=label, credentials=credentials, options=options)


class Plugin:
    def activate(self, context):
        self.context = context
        return Catalogue()
    def deactivate(self):
        pass


PLUGIN = Plugin()
