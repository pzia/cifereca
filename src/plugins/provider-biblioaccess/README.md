# BiblioAccess — La Rochelle (tenant 1409)

## État actuel — synchronisation positive des prêts (opt-in)

Suite à la double recette HTTP AJAX/CAS réelle du 08/11/2026,
la version **0.2.0** peut établir une session **HTTPS directe**, sans
navigateur ni cookie importé, et relire les prêts actifs de MyLoans.

**Consentement nécessaire par compte :**
- compte La Rochelle enregistré et activé ;
- option **`authenticated_sync=on`** sauvegardée explicitement
  (valeur par défaut `off`, les autres valeurs refusées) ;
- `Enregistrer et reconnecter` ou le prochain démarrage déclenche
  l'authentification via le client HTTPX du provider. Aucune opération
  au démarrage sans l'opt-in exact.

`experimental_http_probe=auto-post` et `Tester ce compte` restent
des outils de qualification séparés : **l'option `authenticated_sync`
est le seul consentement à la connexion automatisée**.

La session issue de AJAX `success_reported` → CAS ticket → landing tenant
1409 → `MyLoans` HTML avec formulaire de déconnexion a un **compte local
primaire** `primary` attaché à la connexion exacte (identité Core,
provider et credential). Il **n'existe pas d'identifiant emprunteur
upstream attesté** : `primary` est un identifiant local, jamais une
identité distante déduite d'un titre ou d'un HTML public.

Les lignes MyLoans reconnues dans le même conteneur (référence produit,
notice BiblioAccess, titre, date de fin locale `Europe/Paris`) produisent
des `Loan` positifs, sans action ni lien de livraison. L'échéance est
représentée avec un fuseau explicite et la date locale originale reste
une métadonnée ; sa sémantique métier définitive dépend du fournisseur.
L'identité du produit de ligne n'est jamais remplacée par l'identifiant
de notice. Le résultat typé `cifereca.loan.inventory/1` est **toujours
`partial`**, même quand MyLoans renvoie zéro ligne : ni expiration,
ni retour, ni suppression ne peut découler d'une absence.

Le cookie jar est conservé en RAM par la session et détruit sur
`close()`. Une lecture refusée, redirigée, dépourvue de structure
authentifiée ou expirée **invalide la session** : aucune reconnexion
automatique, aucun rejeu de POST en cours de lecture. Les comptes de
credentials distincts ont des clients et des états séparés.

**Toujours non fournis :** emprunt, retour, renouvellement, lecture
du contenu ACSM, téléchargement, DRM et inventaire exhaustif.
Le lien ACSM privé est détecté uniquement comme booléen dans
la phase d'inspection, sans être stocké dans `Loan.ebook_url`.
Le booléen `connected` atteste une session CAS applicative
du credential local, pas un identifiant distant supplémentaire.

*Les sections historiques ci-dessous documentent les étapes de
qualification antérieures à la version 0.2.0 ; leurs anciens états
« aucune synchronisation » ne décrivent pas le statut actuel.*


Issue #3691. Provider **désactivé par défaut**. Le compte peut être configuré ; les GET de recette réseau ne sont déclenchés qu'après un opt-in explicite.

## Faits établis et limites

Une capture Browser Recorder privée (08/11/2026) atteste le navigateur allant
de `/1409/Account/Login` (302) vers le portail institutionnel, puis un retour
CAS-like à BiblioAccess et `/1409/Catalog/MyLoans` (200). Les corps
d'authentification sont masqués. Un HTTP 200 n'atteste ni identité vérifiée,
ni API officielle, ni permission de télécharger ou d'emprunter.

Le module contient un classificateur **hors ligne** des transitions synthétiques.
La façade `ProviderSession` permet une recette HTTP de **lecture seule**, mais
ne confirme jamais une connexion ni un compte authentifié.
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

- `catalog.search/detail` : composants HTTPX de qualification disponibles, mais
  capacités non publiées faute de recette réelle et de validation du contrat HTML.
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


### Lecture des notices bibliographiques du catalogue 1409

`BiblioAccessCatalogueHTTP.read_observed_notice(book_id)` réutilise la
même session HTTPX pour récupérer le HTML d'une fiche **uniquement si son
identifiant apparaît dans la dernière recherche effectuée par ce client**.
L'appel ne permet pas d'énumérer des identifiants arbitraires, n'importe
aucune session navigateur et ne suit jamais de redirection. Le corps
HTML est borné ; le parseur existant `parse_book_metadata` n'accepte
que des métadonnées structurées `schema.org/Book`.

Si la fiche n'est pas reconnue, le résultat est `None` (indéterminé),
pas « livre absent ». Une notice structurée reste de type **C** :
`entitlement=unverified`, `acquisition=unsupported`, sans modifier
un emprunt, calculer un droit, ni publier de capacité provider.
Les tests utilisent des fiches et ISBN fictifs.

### Conservation du lien de fiche réellement observé

La recherche conserve désormais, avec chaque identifiant C, le premier lien
HTTPS validé vers `/1409/Catalog/Book/{id}/{slug}` rencontré dans le HTML.
Le détail HTTPX utilise **ce lien réellement observé** (slug compris),
plutôt que de reconstruire `/Book/{id}`, route susceptible de rediriger.
Les liens étrangers, les query signées et les tenants différents restent
rejetés ; aucune redirection n'est suivie. Une nouvelle recherche remplace
l'ensemble des liens admissibles ; aucun droit ni acquisition n'en est déduit.


### Recette sur compte réel dans Cifereca — mode opt-in GET uniquement

Le descripteur est `state=ready` **pour la configuration du compte** (identifiant
et mot de passe via les champs de credential publics), mais le package
reste **désactivé par défaut** et n'annonce **aucune capacité métier**.
`interactive_authentication=True` empêche un balayage automatique de
connexion ; aucune bibliothèque n'est connectée par ce descripteur.

Dans les paramètres Cifereca, activer le plugin
`provider.biblioaccess.la-rochelle` puis créer un compte avec les
identifiants usuels. Pour autoriser la recette, renseigner
**exactement `on`** dans l'option de compte « Recette HTTP expérimentale ».
Une demande de connexion manuelle provoque alors, **une seule fois par
session** :

1. GET `https://www.biblioaccess.com/1409/Account/Login` sans suivre le
   302 et vérification de la destination IdP et du tenant.
2. GET de l'URL HTTPS de login institutionnel effectivement observée,
   `service` vérifié, puis inspection bornée du HTML (noms d'inputs,
   scripts et références lexicales de paramètres AJAX).
3. Publication d'événements `API / La Rochelle HTTP probe` dans le journal
   canonique : phase, état HTTP et détails structuraux constatés.
4. **Échec explicite normal** :
   `biblioaccess_probe_completed_login_not_attempted`.

Il n'y a **aucun POST**, aucune tentative de mot de passe, aucun calcul
`_dc`, ni vérification/rejeu de `captchaCheckValue`. Le résultat
attendu est `connected=False`, comptes vides, et un diagnostic GET réel
exportable via l'outil de journal brut déjà présent dans Cifereca.
Le mot de passe est configuré pour une future V1 mais **n'est pas envoyé**
dans ce mode GET. Sans option `on`, `connect()` refuse **sans réseau**
avec `biblioaccess_probe_opt_in_required`.

L'export canonique brut peut contenir des credentials ou cookies issus
d'autres opérations : utiliser le contrôle et la confirmation d'export
existants avant partage. Aucun envoi automatique ni nouveau collecteur
HTTP/auth n'est ajouté au plugin. La recette peut confirmer les
redirections et la forme du login, **pas** les identifiants du compte ou
la validité d'un emprunt. Ne pas interpréter l'échec de `connect()`
comme un défaut de la recette si les GET ont produit les observations.


### Recette AJAX/CAS complète, explicitement opt-in `post`

Après configuration du compte La Rochelle, les modes de
`experimental_http_probe` sont :

- `off` (défaut) : aucun réseau depuis `connect()`.
- `on` : GET de qualification entrée BiblioAccess et login institutionnel ;
  le mot de passe n'est pas transmis.
- **`post`** : GET d'entrée, GET login, **un unique POST** de la route
  AJAX observée `/logon.svc/logon`, puis GET CAS / retour ticket / MyLoans
  **seulement si** le JSON de POST rapporte `success=true`.

Le mode `post` n'est activé **que si les six champs d'options suivants
sont présents** (tels qu'effectivement observés lors de l'authentification
courante) :

| Option | Champ transport | Politique |
| --- | --- | --- |
| `ajax_change_user` | query `changeUser` | valeur explicitement fournie |
| `ajax_provider` | query `provider` | valeur explicitement fournie |
| `ajax_dc` | query `_dc` | valeur fraîche fournie, option secrète |
| `ajax_remember_me` | corps `rememberMe` | valeur explicitement fournie |
| `ajax_captcha` | corps `captcha` | valeur explicitement fournie, **vide autorisé** |
| `ajax_captcha_check_value` | corps `captchaCheckValue` | valeur fraîche fournie, option secrète |

Les deux autres champs du corps AJAX, `username` et `password`, viennent
des credentials du compte. **Aucun paramètre n'est déduit d'une ancienne
capture, d'une horloge supposée ou d'un algorithme CAPTCHA inventé.**
Le mode `post` refuse **avant tout réseau** les options absentes,
les valeurs invalides et `captchaCheckValue` vide, conformément au contrat
POST de qualification existant. Un nouveau contexte de session / des valeurs
fraîches sont nécessaires avant tout nouveau POST ; aucune relance implicite.

Le journal Diagnostics produit les étapes `entry`,
`institution_login`, `login_preflight`, `ajax_logon`,
`cas_return` et `loans_page`, avec statuts, résultats JSON classifiés et
**jamais** mot de passe, paramètres dynamiques, ticket ou corps de réponse.
Le transfert de ticket CAS ne dépasse pas les origines HTTPS et le tenant
1409 préalablement validés. Le résultat terminal normal est
`biblioaccess_probe_completed_identity_unverified` :
**`connected=False`, aucun compte, aucun prêt ni téléchargement**.
Un 200/JSON success/MyLoans 200 ne suffit pas à établir l'identité.

**Pour la recette terrain** : commencer avec `on` et exporter le journal
brut canonique (après revue des données sensibles) ; activer `post`
**seulement** si les valeurs de session requises sont disponibles
légitimement et fraîches. Sinon, ne pas envoyer de POST et qualifier ces
champs lors de l'instrumentation suivante. Aucun navigateur interactif
n'est embarqué dans le provider.


### Recette manuelle depuis l'éditeur de compte (après #3691)

L'option `interactive_authentication=True` continue de **bloquer toute
connexion automatique** au démarrage, lors des synchronisations et des
reconnexions globales. Les captures terrain ont confirmé le code
`interactive_auth_required` dans le startup ; aucune requête La Rochelle
ne partait.

Une action générique **« Tester ce compte »** est désormais proposée par
l'éditeur des comptes **interactifs** enregistrés et activés. Elle vérifie
le compte persisté (identité/provider/credential) puis lance **une seule
session temporaire** via le contrat public du provider, sur un worker UI,
sans déconnecter les autres sources. Les modifications non enregistrées
doivent être sauvegardées au préalable. Le clic est la demande explicite
de recette du compte. Le provider applique ensuite son propre mode
`experimental_http_probe=on` ou `post`, et garde ses validations.

La session temporaire est systématiquement fermée. L'action ne crée
aucun compte Core, aucun droit et ne lance aucun prêt/acquisition.
Les traces `La Rochelle HTTP probe` se trouvent dans le journal brut
canonique. **Pour une première recette terrain, utiliser `on`** :
les deux GET devraient apparaître. Si les GET et la structure du login
sont confirmés, le mode `post` permet ensuite un unique POST explicitement
consenti, seulement avec les paramètres réellement connus, sans deviner de
valeur. Un retour `200` et `MyLoans` seul ne démontre toujours pas
l'identité d'un emprunteur et ne justifie pas l'activation de `sync.loans`.


### Recette réelle du catalogue C depuis « Tester ce compte »

L'option de compte **`experimental_catalogue_query`** (vide par défaut)
permet de saisir un mot-clé volontaire. Lors d'un clic explicite sur
« Tester ce compte » avec `experimental_http_probe=on` ou `post`,
le provider effectue une recherche **GET sur une page au plus** du
catalogue public du tenant 1409 et peut lire **une fiche** dont le lien
a réellement été observé dans cette recherche. Ce test est indépendant
du GET institutionnel : une panne SSO n'empêche pas le diagnostic de
recherche C. Les flux sont pris en charge par le transport HTTPX public.

Le journal canonique contient uniquement les phases
`catalogue_search`, `catalogue_detail`, le statut de qualification,
le nombre de pages et de liens observés et un éventuel code d'erreur
fermé. Il ne publie ni le terme de recherche, ni le titre de la
notice, ni le HTML, ni un droit utilisateur. Un code 200 ne prouve pas
l'exhaustivité du catalogue ; `completeness=partial` reste systématique.
**Aucune capacité `catalog.search`, `sync.loans` ni emprunt
n'est encore publiée** : la prochaine capture terrain établira le
véritable comportement fournisseur avant cette promotion.


### Verrou fail-closed de la préqualification CAS

Le GET institutionnel `/login?service=...` est désormais différencié en
`attempted` et `qualified`. La préqualification n'est validée **qu'après**
un HTTP 200 HTML, la lecture complète bornée et l'inspection structurelle
réussie. Redirection, 401/403, type inattendu, corps trop volumineux,
encodage invalide, HTML invalide et erreur de transport interdisent le POST
`/logon.svc/logon` sur cette instance. Aucune seconde tentative implicite
n'est autorisée. L'absence de cette preuve technique ne déclenche aucun
contournement de challenge ni activation de `connect()`.


### Entrée SSO : paramètre `returnUrl` observé sur le clic BiblioAccess

Le clic réel d'authentification sur BiblioAccess 1409 ouvre
`/1409/Account/Login?returnUrl=https%3A%2F%2Fwww.biblioaccess.com%2F1409%2F`.
Le GET expérimental utilisait auparavant la route **sans** `returnUrl`.
Dans la capture Cifereca du 08/11/2026 14:27, ce GET sans query a obtenu
une redirection 302 vers `/login?service=` (vide), refusée par
`cas_url_refused`. La valeur de `returnUrl` est désormais fixée à
la racine exacte du tenant 1409, telle que fournie par le clic utilisateur ;
elle n'est pas personnalisable et ne sert pas à fabriquer `service`.

**À vérifier après déploiement :** la nouvelle réponse 302 fournit-elle
un paramètre `service` CAS non vide, de même origine et de tenant exact ?
Les gardes sur la destination, le service, le POST AJAX et les tickets
demeurent inchangés ; aucune identité ni capacité de prêt n'est attestée.


### Refus des réponses AJAX contradictoires

Une réponse `success=true` HTTP 200 n'est classée `success_reported`
que si `errors` est absent ou explicitement une liste vide. Une réponse
contenant `errors` non vide, `null` ou d'un autre type reste `unknown` :
aucun GET CAS ne doit être déclenché sur cette seule base. Les clés JSON
dupliquées (y compris dans un objet imbriqué) et les constantes JSON
non standard `NaN`/`Infinity` sont refusées. Les valeurs d'erreur et
de continuation demeurent opaques. Cela ne prouve toujours ni identité,
ni propriété de prêts, ni mécanisme d'authentification supporté.


### Qualification consentie du JavaScript institutionnel (sans exécution)

Après un test GET `experimental_http_probe=on` réussi, le journal
`login_preflight.same_origin_script_paths` indique les chemins JS
institutionnels déjà observés dans le HTML du **même** GET /login. Pour
inspecter un seul de ces scripts, renseigner l'option facultative du compte
`experimental_login_script_path` avec son **chemin exact** (ex.
`/assets/logon.js` seulement si ce chemin a été observé), enregistrer,
puis cliquer **« Tester ce compte »**.

Le probe effectue les deux GET initiaux puis un unique GET additionnel,
uniquement sur l'origine IdP HTTPS, sans redirection, avec MIME JavaScript
autorisé et corps borné à 128 Kio. La sélection ne permet ni URL externe,
ni query, ni découverte/enregistrement arbitraire des scripts. Le journal
`login_script` publie uniquement les noms d'identifiants reconnus dans une
liste fermée et la présence lexicale de `/logon.svc/logon` : **ni source JS,
ni valeurs dynamiques, ni URL/headers/tokens de réponse**. Une référence
lexicale à `_dc` ou `captchaCheckValue` ne démontre pas leur provenance.

Sans option renseignée, aucun GET JS n'est effectué. Un chemin absent du
HTML observé, une redirection ou une erreur HTTP bloquent la recette ; en
mode `post`, ces erreurs bloquent également le POST. Il ne s'agit ni d'une
exécution JavaScript ni d'une résolution automatique de CAPTCHA. Aucun
état connecté ni droit de prêt n'est déduit de ces observations.


### Confidentialité des métadonnées HTML du login

Le diagnostic `login_preflight` ne restitue désormais que les **noms
de champs SSO connus** (liste fermée, notamment `username`,
`password`, `checkValue`, `rememberMe`, `captcha`, `RequestKey`).
Les noms de champs arbitraires peuvent incorporer des données de session :
ils sont ignorés et ne constituent pas une absence de champs sur le site.

Les chemins JS présentés pour l'inspection explicite sont filtrés avant
la journalisation : chemins statiques HTTPS même origine uniquement, sans
segments encodés, de traversée ou de forme manifestement opaque/tokenisée,
sans query affichée. Un script non admissible n'est **jamais** téléchargé
par le probe facultatif. Les nombres globaux de scripts restent observables
sans que les éléments écartés soient qualifiés de preuves négatives.

Ce filtrage ne valide toujours pas la provenance des paramètres du POST ;
l'activation de l'authentification et des droits reste exclue.


### Fidélité du GET JavaScript aux ressources observées

La page d'authentification peut référencer un script versionné
`/scripts/logon.js?v=...`. Le préflight n'affiche **que le chemin**
admissible, mais conserve en mémoire de la session temporaire l'URL
exacte observée. Le GET explicitement consenti vise désormais **cette
URL exacte**, avec sa query si elle était présente ; demander seulement
`/scripts/logon.js` risquait de recevoir un autre contenu.

La query et ses valeurs ne figurent jamais dans les diagnostics de
préflight ou de lecture du JS, ni dans la représentation textuelle des
observations. Le ZIP HTTP brut peut néanmoins contenir la requête :
il doit être considéré comme sensible et revu avant tout partage.
Si deux références HTML distinctes ont le même chemin mais des queries
différentes, ce chemin est **exclu** de l'inspection facultative pour
éviter toute sélection ambiguë. Aucun GET supplémentaire n'est lancé
pour résoudre cette ambiguïté. Ces garanties ne prouvent pas la
provenance des champs dynamiques ni un résultat d'authentification.


### Préparation de l'inventaire MyLoans après CAS

La dernière étape HTTP `GET /1409/Catalog/MyLoans` ne perd plus
systématiquement son corps HTML : le composant hors réseau
`biblioaccess_loans_page.py` extrait un **ensemble candidat** de liens
bibliographiques HTTPS strictement limités à
`/1409/Catalog/Book/{id}[/slug]` (maximum 128 identifiants).
Il conserve les URL de notices validées et leurs identifiants **uniquement
dans l'observation éphémère** du client, et expose deux compteurs de
diagnostic : `page_book_link_count` et `page_book_unique_count`.

Ce n'est PAS un parseur de prêts : le HTML pourrait être une page publique,
une liste de recommandations ou contenir des liens hors inventaire. Les
dates de retour, identifiants de prêt, états et droits nécessitent une
structure de page réelle, un rattachement d'identité et une recette positive
encore absents. Même zéro lien candidat signifie `completeness=unknown`,
jamais « aucun prêt ». Aucun `Account`, `Loan`, `sync.loans` ou
acquisition n'est produit. Cette étape constitue le point de raccordement
de la future normalisation quand le HTML authentifié aura été qualifié.


### Inspection automatique du script de connexion observé

En mode `experimental_http_probe=on`, après lecture de `/login`,
le client détecte désormais automatiquement l'unique script de connexion
`logon.js` ou `logon.js.pagespeed.*.js` **effectivement référencé** dans
le HTML du portail institutionnel, et l'inspecte via le GET JS existant
(même origine, URL exacte et query privée). Le chemin manuellement configuré
dans `experimental_login_script_path` conserve la priorité s'il est renseigné.
S'il n'existe aucun script de connexion reconnu ou plusieurs candidats,
aucune inspection automatique n'a lieu.

Pour la page observée le 08/11/2026 17:18, le chemin de cette famille est
`/ui/250297600004/plug-in/logon/logon.js.pagespeed.ce.E1q2XCAAyC.js`.
Le but est de supprimer une étape de saisie à chaque itération terrain ;
ce n'est ni une authentification ni une résolution automatique du challenge.
Le mode `post` ne déclenche pas cette inspection implicite : il continue
d'exiger le consentement et les paramètres de POST explicites jusqu'à
qualification du protocole. La future session syncable devra utiliser ce
parcours sans options expérimentales.


### Premier POST AJAX à partir de la page fraîche (qualification opt-in)

La capture terrain du 08/11/2026 17:37 contient le JavaScript de connexion
réel. Pour le formulaire ordinaire, `logonManager.provider='Form'`,
`logonManager.action='logon'`, `changeUser=true`, et `_dc` est
l'horodatage Unix en millisecondes. Le formulaire contient un
`#logon-captcha-checkvalue` caché, dont la **valeur doit être prélevée
dans le même GET /login**, pas enregistrée dans une option de compte.
Le CAPTCHA visuel est initialement masqué ; après refus du serveur,
le navigateur peut demander une vérification supplémentaire.

Le mode facultatif `experimental_http_probe=auto-post` est déclenché
**uniquement** par une action explicite « Tester ce compte » :
entrée BiblioAccess, GET institutionnel, lecture du contexte du formulaire,
POST AJAX **unique** avec identifiants déjà enregistrés et champs dynamiques
frais du HTML. Aucun script n'est exécuté, aucun CAPTCHA n'est résolu ;
un formulaire inattendu, un challenge initial visible ou une valeur
cachée absente interdisent le POST. Le mode `on` reste en GET uniquement ;
le mode `post` historique avec valeurs fournies reste disponible.
Le résultat AJAX peut conduire aux observations CAS/MyLoans existantes,
mais **ne constitue jamais** une authentification Core vérifiée :
aucun compte, prêt, téléchargement ou `sync.loans` n'est publié.
Les captures HTTP brutes de ce mode peuvent contenir des identifiants :
fichiers privés et partage après expurgation uniquement.


La reconnaissance lexicale de la route AJAX accepte désormais également
le littéral relatif `logon.svc/logon` observé dans le JS PageSpeed, sans
imposer un slash initial. Ce signal sert seulement au diagnostic : il
n'exécute pas le JS et ne conditionne pas la connexion.

Une régression MockTransport couvre désormais le chemin **positif
synthétique** complet : session cookie institutionnel initiale, POST avec
valeurs fraîches, cookie de succès, CAS avec ticket, cookie BiblioAccess,
puis lecture de MyLoans. Ce test ne vaut pas validation en production.


### Préparation de la prochaine recette du compte : phases et diagnostic expurgé

Pour `experimental_http_probe=auto-post`, un incident est désormais
attribué à **la phase exacte** (`entry`, `institution_login`,
`login_preflight`, `login_script`, `ajax_logon`, `cas_return`,
`loans_page`) au lieu d'une phase générique `preflight`.
L'authentification reste explicitement déclenchée, à tentative unique,
et les transitions CAS ne sont pas assouplies.

Si le POST renvoie un JSON de forme reconnue, l'événement `ajax_logon`
rapporte `reported_error_count` (nombre plafonné à 99, `null` si le champ
est mal typé) et `response_message_present` (booléen).
**Aucun corps JSON, message, erreur textuelle, credential, cookie ou ticket**
n'est restitué. Un `failure_reported` n'est pas forcément un CAPTCHA.

Interprétation après un **log réel privé** :
- arrêt `ajax_logon` : déterminer si le POST a été refusé, sans relance
  automatique ni contournement du challenge ;
- `success_reported` puis refus `cas_return` : analyser la continuité
  ticket/service/cookies dans les seules preuves autorisées ;
- `loans_page` 200 : qualifier l'identité réellement authentifiée,
  puis les *lignes de prêt* et leur caractère exhaustif avant de
  publier `Loan`, `sync.loans` ou une preuve d'absence de prêt.

Le maintien de `connected=False` est volontaire : ces indicateurs ne
constituent pas une preuve de compte ni de droit.


### Qualification MyLoans sur capture réelle 08/11/2026 19:11

La recette `auto-post` a **réussi deux fois** sur la source réelle :
entrée 302, institution `/login` 200, POST AJAX 200
`success_reported` avec 0 erreur déclarée, CAS 302 avec ticket éphémère,
landing tenant 1409 200, puis `Catalog/MyLoans` 200.
Les cookies, tickets, identifiants, corps et liens signés sont **privés**
et ne figurent pas dans Git.

Les deux pages MyLoans capturées (réponses Brotli décompressées hors du
dépôt) comportent une ligne de prêt stable de structure
`div.JSProductRow-<product-ref>`, avec `p.titleLoans`,
`span.jsDateEnd` au format jour/mois/année et heure,
et un lien de notice `/1409/Catalog/Book/<book-id>/...`.
La référence de produit de la ligne **diffère** de l'identifiant de la
notice. Une action ACSM porte un hôte exact
`entrepot2r.numilog.com` et un chemin `/fulfillment/URLLink.acsm` :
l'observation ne garde qu'un booléen et **ne conserve aucun paramètre de
son URL**, sans GET de téléchargement. Le formulaire de déconnexion
`POST /1409/Account/LogOff` est présent, sans être utilisé.

Le parseur MyLoans fournit désormais des **observations positives
strictes** `page_loan_row_count`, `page_loan_row_partial_count` et
`logout_form_observed` ; les références produit, notice, titre et
échéance restent mémoire et hors `repr`/journal. Il refuse les liens
d'autres tenants, les lignes ambiguës et les dates invalides.
Aucune valeur ni DOM réel n'est intégrée aux tests : uniquement des
fixtures synthétiques fondées sur la **structure** observée.

**Ne pas confondre cette étape avec la synchronisation :**
`identity_verified=False`, `loans_verified=False`,
`inventory_verified=False` et couverture `unknown` ;
les lignes sont des candidats positifs, ni des identifiants de prêts
définitifs ni un signal d'inventaire complet.
Reste à établir le contrat d'identité du compte et la durée de vie
d'une session authentifiée pour fournir des `Account`/`Loan`
scoppés sous le contrat public Core ; seul le Core autorisera
l'admission d'une acquisition (ACSM traité séparément).
