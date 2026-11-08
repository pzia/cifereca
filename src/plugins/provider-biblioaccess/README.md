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
