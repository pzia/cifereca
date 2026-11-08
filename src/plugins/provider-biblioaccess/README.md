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
HTTP : routes institutionnelles `GET /login`, `POST /logon.svc/logon`,
`GET /cas/login`, retour tenant 1409, classification conservatrice des
statuts et origines. Il inspecte les formulaires HTML sans lire ni retenir
la valeur des champs (noms/types uniquement), et produit un diagnostic de
réponse POST portant sur statut/type/taille, **sans contenu ni clés JSON**.
Les champs de formulaire synthétiques sont des exemples et ne prouvent
pas le vrai schéma de `/logon.svc/logon`.

Le module `biblioaccess_http_probe.py` fournit **un client HTTPX de
qualification** utilisant le broker public `HTTPX_TRANSPORT` :
GET `/login` borné, inspection de la structure du formulaire, puis **un
POST `/logon.svc/logon` unique** sous consentement explicite et correspondance exacte
des champs transmis, avec redirections désactivées, limites de temps et
taille, aucun rejeu après échec. Les réponses sont observées uniquement
sous forme statut/type/taille ; ni ticket CAS ni preuve de session ne sont
inférés. Le client n'est appelé par aucun parcours de production : le
provider reste `skeleton` et `connect()` refuse la connexion.

Le vrai schéma `/logon.svc/logon` (en particulier champs cachés et continuation CAS)
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

### HAR institutionnel reçu le 08/11/2026 — POST AJAX confirmé

Capture utilisateur privée (1 transaction) : POST HTTPS
`https://mediatheques.agglo-larochelle.fr/logon.svc/logon`,
`application/x-www-form-urlencoded`, paramètres URL **noms uniquement**
`changeUser`, `provider`, `_dc`, corps **noms uniquement** :
`username`, `password`, `rememberMe`, `captcha`, `captchaCheckValue`.
Réponse HTTP 200 `application/json`, clés : `errors`, `message`,
`success`, `d`. Deux `Set-Cookie` observés, sans reproduction des
noms/valeurs ; aucun 302 immédiatement attaché au POST.

`biblioaccess_ajax_logon.py` encode cette forme AJAX et propose un POST
HTTPX **explicite et unique**, sans navigateur, avec `X-Requested-With`,
limites réseau et réponse JSON structurelle. Les champs dynamiques,
notamment `captchaCheckValue`, doivent provenir d'un parcours courant
validé : ils ne sont jamais fabriqués ni lus dans la capture. `success=true`
reste un résultat déclaré par le service, **pas** une authentification
Core ou une continuation CAS attestée. Le module est encore hors parcours
`connect()`, et la capture privée HAR n'est pas versionnée.

Une seule transaction ne prouve pas le GET initial, la récupération de la
valeur dynamique, la continuation CAS, ni l'identité finale BiblioAccess.


### Continuation HTTP CAS après le POST AJAX observé

`biblioaccess_ajax_cas.py` réutilise le client `ObservedAjaxLogonClient`
et sa **même session HTTPX** pour : GET entrée BiblioAccess 1409 (302),
GET institutionnel `/login?service=...` (200), unique POST AJAX qualifié
(`/logon.svc/logon`), GET `/cas/login?service=...` (302), unique GET
retour avec ticket de service, puis unique GET `/1409/Catalog/MyLoans`.

Chaque transition valide le tenant et l'origine avant transfert, sans
redirection automatique, et les réponses sont bornées. Le ticket CAS, les
corps privés et l'URL complète de continuation ne sont pas retournés.
`success=true` et `MyLoans` 200 demeurent respectivement une déclaration
AJAX et une observation de HTML, **pas une preuve de compte ni de droit**.
Aucun inventaire A, téléchargement B ou mutation n'est activé.

**Limite V1 importante :** les valeurs dynamiques de `_dc` et
`captchaCheckValue` ne sont pas dérivées du GET institutionnel : le contrat
observé ne garantit pas comment les obtenir sans navigateur. Aucun champ
n'est inventé. Cette classe reste un outil de qualification non relié à
`ProviderSession.connect()`, sans navigateur interactif ni trafic réel
dans les tests. La suite métier doit établir un fournisseur valide de ces
valeurs fraîches et une preuve d'identité avant activation.

### Qualification des paramètres AJAX dynamiques sans navigateur

`biblioaccess_ajax_preflight.py` ajoute un inspecteur **hors réseau**
de la page de connexion institutionnelle, utilisé par
`ObservedAjaxCasClient.observe_login_once()` sur le HTML **déjà reçu** :
inventaire borné des scripts `src` HTTPS de même origine (chemins sans
query), nombres de scripts tiers/inline, **noms** de champs `input`, et
présence structurelle des identifiants `_dc`, `captchaCheckValue`,
`captcha`, `changeUser`, `provider` dans les scripts inline. Les
valeurs, mots de passe, textes de scripts, query de cache et corps HTML
ne sont pas restitués ; aucun script n'est téléchargé ou exécuté.

`login_preflight()` expose cette observation **uniquement après**
`observe_login_once()` sur la même instance ; elle ne provoque aucun
nouveau GET. Les indicateurs restent `status=unverified`,
`values_extracted=False`, `challenge_resolved=False`.

**Interprétation** : un nom trouvé dans un script ne prouve pas la façon
de calculer sa valeur ; l'absence du nom ne prouve pas qu'il n'existe pas
dans un JS externe. La suite requiert une qualification explicite du
calcul `_dc` et surtout du challenge institutionnel, sans supposer
qu'ils sont facultatifs ni contourner de CAPTCHA. Aucun navigateur
interactif, aucun login réel ou capacité provider n'est activé.

### Inspection optionnelle d'un script institutionnel référencé

`ObservedAjaxCasClient.inspect_login_script_once(path=..., consent=True)`
peut lire **un seul** script `.js` dont le chemin exact a été observé
dans le GET `/login`. Cette opération est séparée, sous consentement
explicite, limitée à **128 Kio**, autorise uniquement HTTPS sur l'origine
institutionnelle, sans query cache ni suivi de redirection. Le script est
analysé en mémoire et **jamais exécuté**. Aucun GET n'est déclenché par
la simple consultation du login.

Le résultat `JavascriptEvidence` donne seulement les identifiants
référencés parmi les cinq noms de paramètres AJAX connus, et l'indicateur
lexical de la présence de `/logon.svc/logon`. Il ne renvoie aucun octet
de JS, valeur de formulaire, cookie, mot de passe ou challenge. Une
référence `captchaCheckValue` **ne prouve pas** qu'on peut obtenir ou
reconstituer sa valeur ; aucun CAPTCHA n'est résolu ou contourné.
L'absence de référence ne prouve pas que le paramètre n'est pas produit
ailleurs. Toujours aucune capacité de connexion réelle.


### Lecture catalogue HTTPX bornée, hors authentification

`biblioaccess_catalogue_http.py` ajoute un client HTTPX de lecture seule
et non publié comme capacité provider. Il lance un GET
`/1409/Catalog/SearchResultSummary?query=...`, puis suit **manuellement
et seulement** la redirection 302 vers
`/1409/Catalog/Search?query=...&pageindex=...` après validation
de l'origine HTTPS exacte, du tenant, de la query, des paramètres autorisés
et du budget de pages (1 à 3 par appel). Les réponses HTML sont limitées
à 2 Mio avant décodage, et les erreurs HTTP restent fermées.
La réponse présente uniquement les IDs de notices `/Catalog/Book/{id}`,
le nombre de pages inspectées et un état de qualification.

Un `SearchResultSummary` HTTP 200 **n'est jamais interprété comme zéro
résultat établi** : l'état `summary_html_unverified` est explicite.
Une page sans lien extrait reste `partial` et n'atteste jamais
l'exhaustivité, la propriété ou le prêt. Le client ne fait aucun POST,
n'importe aucune session navigateur, n'accède pas aux prêts, et ne
déclare **aucune nouvelle capacité** dans le manifeste. Les tests
utilisent uniquement un transport HTTPX fictif. L'accès catalogue
réel et la forme HTML exacte restent à recetter avant publication.
