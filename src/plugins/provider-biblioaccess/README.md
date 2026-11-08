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
`interactive_authentication=True` empêche le démarrage automatique ; ce drapeau n'autorise aucun navigateur interactif dans le plugin. Le fournisseur utilise exclusivement les primitives HTTP.

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

### Qualification HTTP du SSO (V1)

**Règle du dépôt : aucun navigateur interactif dans un plugin provider.**
Browser Recorder est une exception technique explicite et indépendante.

`biblioaccess_http_sso.py` qualifie **hors réseau** la forme du futur client
HTTP : routes institutionnelles `GET /login`, `POST /logon`,
`GET /cas/login`, retour tenant 1409, classification conservatrice des
statuts et origines. Il inspecte les formulaires HTML sans lire ni retenir
la valeur des champs (noms/types uniquement), et produit un diagnostic de
réponse POST portant sur statut/type/taille, **sans contenu ni clés JSON**.
Les champs de formulaire synthétiques sont des exemples et ne prouvent
pas le vrai schéma de `/logon`.

Le module `biblioaccess_http_probe.py` fournit **un client HTTPX de
qualification** utilisant le broker public `HTTPX_TRANSPORT` :
GET `/login` borné, inspection de la structure du formulaire, puis **un
POST `/logon` unique** sous consentement explicite et correspondance exacte
des champs transmis, avec redirections désactivées, limites de temps et
taille, aucun rejeu après échec. Les réponses sont observées uniquement
sous forme statut/type/taille ; ni ticket CAS ni preuve de session ne sont
inférés. Le client n'est appelé par aucun parcours de production : le
provider reste `skeleton` et `connect()` refuse la connexion.

Le vrai schéma `/logon` (en particulier champs cachés et continuation CAS)
doit encore être qualifié par recapture ciblée ou instrumentation V1 sous
recette consentie. Les tests HTTPX utilisent seulement un transport fictif.
Aucun POST réel n'a été effectué.
Aucun Playwright, Chromium, profil ni cookie de navigateur.

Les redirections CAS et une page de prêts HTTP 200 ne constituent pas une
preuve de session valide ou de propriété. Déterminer la preuve métier
positive et les garde-fous d'identité avant publication des capacités.

Les secrets, ACSM réel et captures privées ne sont jamais dans le dépôt.
Le tenant 1717 (Place des Arts, #2056) reste distinct ; un moteur partagé
sera extrait seulement lors d'une implémentation prouvée pour les deux.

### Renforcement du client HTTP de qualification

- Le POST de qualification est lié à la **forme effectivement inspectée** sur la même instance HTTPX ; aucune forme fabriquée par un appelant ne suffit à déclencher le réseau.
- **Au plus un POST par instance**, même après HTTP 200, erreur, timeout ou résultat incertain. Recommencer implique un nouveau client et une nouvelle inspection explicite.
- `inspect_cas_entry()` consulte uniquement `/1409/Account/Login`, **sans suivre la redirection** : elle classe la destination institutionnelle autorisée et ne renvoie ni URL ni ticket de service. HTTP 200 demeure une observation non vérifiée.
- `connect()` reste refusé avec le code `biblioaccess_http_sso_logon_unqualified` ; aucun navigateur ni droit d'emprunt n'est inféré.

### Route POST observée et continuation CAS (#3691)

La capture de terrain consignée dans le ticket établit **POST institutionnel
sur une route terminant par `/logon`**, avec les **noms** de paramètres de query
`changeUser`, `provider`, `param` ; elle n'établit pas les valeurs, les
champs du corps ni un formulaire HTML postable. La première version supposait
à tort `POST /logon` sans query.

Le client HTTPX qualifie désormais **l'action exacte fournie par le formulaire
GET dans la même instance** et n'autorise le POST qu'après consentement ;
la query d'action reste éphémère en mémoire du client, **jamais dans les
`FormShape` exposés**. La vérification autorise le suffixe `/logon` sur
l'hôte institutionnel exact, et seulement la triade attestée si query présente.
Un formulaire absent ou une connexion JavaScript non reconnue reste `unsupported` :
aucun POST fictif ne se substitue au script de connexion.

Le premier GET de BiblioAccess peut livrer `/login?service=...` ; le client
conserve cette URL uniquement en mémoire, contrôle que `service` désigne le
tenant HTTPS 1409 et l'utilise pour **le GET de login suivant**. Après un POST
HTTP 200 qualifié, `inspect_cas_return_once()` peut effectuer un GET
`/cas/login?service=...`, puis **un seul GET** de retour muni du ticket CAS
fraîchement émis, à destination strictement identique au service initial.
Les 30x ne sont jamais suivis automatiquement, la destination est contrôlée
avant l'envoi, et ni le ticket ni l'URL ne sont retournés au consommateur.
Cela reste un **probe non branché à `ProviderSession.connect()`** ; une page
de retour 200 n'est pas une preuve de compte connecté.

La chaîne de cinq échanges est vérifiée avec HTTPX `MockTransport` et des
valeurs totalement fictives. Aucune authentification réelle, capture nouvelle,
navigateur ni transition vers `MyLoans` n'a été effectuée.
