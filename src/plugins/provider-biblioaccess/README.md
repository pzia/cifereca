# BiblioAccess — La Rochelle (tenant 1409)

Issue #3691. Prototype **désactivé par défaut**, sans réseau ni authentification automatique.

## Faits établis et limites

Une capture Browser Recorder privée (08/11/2026) atteste le navigateur allant
de `/1409/Account/Login` (302) vers le portail institutionnel, puis un retour
CAS-like à BiblioAccess et `/1409/Catalog/MyLoans` (200). Les corps
d'authentification sont masqués. Un HTTP 200 n'atteste ni identité vérifiée,
ni API officielle, ni permission de télécharger ou d'emprunter.

Le module contient un classificateur **hors ligne** de transitions synthétiques
et une façade `ProviderSession` qui échoue explicitement à `connect()`.
`interactive_authentication=True` empêche un démarrage automatique, mais
ne constitue pas un navigateur, un protocole d'auth, ni un transport de cookies.

### Composants hors réseau ajoutés

`biblioaccess_observations.py` fournit :
- construction bornée de la route `SearchResultSummary` ; classification conservatrice de 302, 200, 401/403, 429 et erreurs serveur ;
- extraction de liens vers `Catalog/Book/{id}` limitée au domaine HTTPS et au tenant 1409, et détection d'une pagination `pageindex` bornée (100 pages maximum) ; la couverture est **toujours partielle** tant qu'aucun contrat métier ne garantit la liste complète ;
- inspection structurelle de XML ACSM ADEPT, avec limite d'octets et rejet DTD/entités ; seul le format, le type déclaré et les durées sont restitués, jamais les tokens, les URL, les identifiants, un droit ou un fichier acquis.

Le module `biblioaccess_metadata.py` inspecte également les fiches
`Catalog/Book/{id}` **hors réseau**, à partir de métadonnées explicites
`schema.org/Book` : titre, auteur, éditeur, ISBN-13 contrôlé, date et image
HTTPS du domaine fournisseur. Une fiche sans structure `Book` reconnue
reste inconnue (pas de fallback heuristique générant de fausses notices).
Le bouton de prêt, les actions JSON-LD et les URLs de livraison ne confèrent
aucun droit ni offre.

Les tests utilisent exclusivement du HTML/XML synthétique. Ces helpers ne
remplacent **ni** un parseur de fiche/listes validé sur observations
expurgées, **ni** l'authentification, **ni** un runtime ADEPT.

### Portée effective

- `catalog.search/detail` : non publiés ; parsing HTTP/HTML réel et
  pagination encore à implémenter/tester avec fixtures épurées.
- `sync.loans` : non publié ; inventaire de prêts complet et scoping d'identité
  restent à implémenter.
- `acquisition.download` : non publié ; lien ACSM et contenu XML sont
  attestés, mais livraison, contrôle de session et broker ADEPT non branchés.
- Prêt, retour, renouvellement, choix DRM : mutations non attestées et interdites.

### Condition de déblocage

Identifier et implémenter **un transport de session autorisé et supporté**
entre le navigateur institutionnel et le Core, sans import implicite de
cookies ni réemploi de ticket CAS. Si ce transport n'existe pas, maintenir
la connexion manuelle hors de Cifereca et ne pas annoncer une capacité
authentifiée. Ne pas solliciter une nouvelle capture d'identifiants.

Les secrets, ACSM réel et captures privées ne sont jamais dans le dépôt.
Le tenant 1717 (Place des Arts, #2056) reste distinct ; un moteur partagé
sera extrait seulement lors d'une implémentation prouvée pour les deux.

## SSO navigateur : contrat d'état expérimental (#3691)

`biblioaccess_sso_lifecycle.py` est un garde **hors réseau** du futur parcours
Chromium visible : opt-in individuel, propriétaire exact
`identity_id / credential_id / provider_id / tenant_id=1409`, jeton de
corrélation local non persistant, **contexte navigateur épinglé par identité
d'objet**, expiration (15 minutes), annulation, fermeture et rejet des
callbacks tardifs/issus d'un autre compte.

L'état `browser_confirmed` ne signifie **pas** `ProviderSession.connected`.
Le paramètre `page_is_authenticated` doit provenir d'un vérificateur indépendant
sur la session vivante, **jamais** d'un simple HTTP 200, de la présence d'un cookie,
d'un titre HTML ou d'une URL de retour CAS. Ce vérificateur et l'adaptateur
Playwright ne sont **pas** encore livrés. Aucun dialogue IdP n'est rejoué.
Le navigateur doit rester le seul propriétaire de ses cookies ; aucune
réutilisation du coffre ou des ZIP Browser Recorder.

L'approche permet un login manuel chez le fournisseur, mais les conditions
d'utilisation et un parcours de recette consenti doivent encore être confirmés.
