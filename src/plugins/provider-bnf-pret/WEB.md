# BnF Prêt — fournisseur HTTPX (#2055)

Depuis la version 0.2.0, le package remplace `ui.page.bnf-pret` par `provider.bnf-pret`, un
`ProviderPlugin` / `ProviderSession` utilisant `httpx.Client`, comme Paris.
L'installation et l'activation ne démarrent aucun navigateur et ne nécessitent
ni Playwright, ni Chromium, ni Browser Recorder. Les anciens modules navigateur sont supprimés.

## Installer et essayer

Depuis la branche contenant cette version :

```bash
python scripts/package-plugin.py src/plugins/provider-bnf-pret --out dist
cifereca-cli plugin install dist/cifereca-plugin-provider-bnf-pret-0.3.13.zip --replace
cifereca-cli plugin enable provider.bnf-pret
```

Dans Configuration, ajouter une connexion **BnF Prêt** au bon profil Cifereca,
saisir l'identifiant et le mot de passe BnF, puis connecter. Les prêts reconnus
sont disponibles dans Emprunts et Synchronisation via les parcours Core habituels.
L'ancien identifiant de page UI n'est plus une contribution du package.

## Scoping du propriétaire du prêt (#3252, 0.3.13)

Les gardes de lecture du manifeste, d'observation de droits, de sélection
d'offre et de téléchargement explicite interrogent désormais le broker Core
par `broker.accounts(identity_id)` pour l'identité emprunteuse *épinglée*
au début de l'opération. Le même identifiant de compte BnF peut exister chez
deux identités sans rendre ambiguë la propriétaire exacte. L'absence de
binding ou deux bindings pour la même identité restent bloquants ; le
contrôle de credential, l'epoch et les gardes après GET restent inchangés.
La façade legacy sans broker reste en lecture positive seule, sans preuve
négative. Les tests sont synthétiques, sans compte BnF réel.

## Authentification et limites connues

Observation publique sans compte du 23 octobre 2026 : activité →
`pret.bnf.fr/sso/saml/init` → `idppub.bnf.fr/idp/profile/…` →
`authentification.bnf.fr/login`. La page de connexion présente un formulaire
POST relatif `login`, champs `username`, `password`, `execution`, `_eventId`,
`geolocation`. Le client lit le formulaire reçu et ses champs cachés ; il ne
reconstruit pas de jeton. Les valeurs de tests sont intégralement synthétiques.

Après soumission des identifiants au seul hôte d'authentification officiel,
le client peut soumettre une réponse SAML fraîchement émise dans cette même
session à l'origine du portail. Aucune assertion issue d'une capture, cookie
importé, export d'état, HTML privé ou persistance SSO n'est utilisé. Une étape
2FA/CAPTCHA/JavaScript ou un formulaire inconnu est refusé avec
`auth_interactive_required` / `unsupported_form`, sans repli navigateur.
**Recette utilisateur du 24/10/2026** : la connexion réelle a réussi et deux
prêts numériques ont été affichés dans « Mes emprunts » (voir #3104). La
synchronisation manuelle est volontairement `partial`, sans clôture par
absence. Cette preuve ne valide pas les dates, couvertures, le GET de jeton
ACSM ni le fonctionnement d'ADEPT sur un véritable prêt.

Les redirections sont contrôlées avant chaque requête. Les POST sont réservés
à la connexion, sans retry ni transfert POST par 307/308. Une lecture de prêts
ne peut pas suivre une redirection SSO, déclencher un emprunt ou télécharger.
Limites : 512 Kio décompressés/document, 12 redirections/appel, 8 étapes de
formulaire, timeout HTTP de 15 s et budget global connexion de 60 s.
Les cookies restent dans le client du credential ; fermeture et échec de
connexion les révoquent. Les erreurs publiques n'exposent que des codes fermés. Le fournisseur publie
également ces codes dans Diagnostics avec la seule phase `login`, `loans`, `search`, `detail` ou `download`,
car le Core affiche une erreur générique à la connexion. Aucun identifiant,
URL, cookie, formulaire ou texte de réponse n'est ajouté au diagnostic ; une
panne du collecteur n'altère pas le résultat de l'opération.

## Prêts et synchronisation

Projection HTML de `.active-loans .js-loan`, paire unique de liens
`/resources/{id}` et `/loans/{id}`. Le titre vient du texte du lien de ressource
quand il est unique. Les dates et la pagination des prêts ne sont pas qualifiées :
aucune date n'est inventée. Chaque `Loan` porte provider, credential, compte et
identité exacts. Le lien de téléchargement n'est pas transmis au Core.

L'inventaire est **toujours partiel** : `loan_inventory_snapshot()` publie
`status=partial`, branche `bnf_html` et raison typée `inventory_not_exhaustive`.
Le canal mutable historique `last_loan_errors` a été retiré du plugin (#3197).
Dans la migration ciblée #3628, le producteur typé utilise directement la
lecture HTML BnF scoppée sous verrou, **sans appeler la façade v1
\`session.loans(account)\`**. Les deux entrées partagent la même implémentation
interne de GET : une seule lecture par invocation. La façade v1 reste
exposée pour les plugins installés selon le contrat API existant ; elle
ne certifie jamais l'exhaustivité, ni le retour ou la disparition d'un droit.
 La synchronisation ajoute/actualise les prêts
observés, mais une page vide, tronquée, une session expirée ou un prêt absent
ne prouve aucune fin de droit. Le Core actuel (#2975) conserve les anciens droits
sans preuve négative exhaustive ; #2055 interdit les acquisitions BnF implicites.
Aucun fichier, placement ou historique n'est supprimé par ce fournisseur.

La version **0.3.0** ajoute `catalog.search` et `catalog.detail` à `sync.loans`.
Emprunt, réservation, retour et téléchargement ADEPT ne sont pas annoncés.

## Métadonnées de prêt attestées — 0.3.2 (#3104)

Le type de la ligne est désormais indiqué comme **Prêt numérique** dès qu'un
prêt numérique BnF est positivement observé. Un auteur n'est renseigné que
si la **carte de ce prêt** (`.active-loans .js-loan`) fournit elle-même
`itemprop=author` : jamais via un en-tête, une fiche homonyme, un autre
compte, un élément masqué ou un autre livre imbriqué. Plusieurs auteurs
explicitement présents sont conservés ; plus de huit valeurs rendent le
champ inconnu sans effacer le prêt.

Cette règle de lecture est testée sur HTML synthétique ; la présence réelle
de `itemprop=author` dans l'activité privée n'est **pas encore attestée**.
En l'absence d'un sélecteur fiable et de la valeur d'échéance rattachée au
prêt du compte, `due_at` reste inconnu. La version 0.3.3 accepte une couverture **uniquement** si une image publique unique est incluse dans l’ancre `/resources/{id}` de la carte exacte du prêt : origine `pret.bnf.fr` avec chemin image borné, ou variante publique structurée `imagedelivery.net`, sans requête, fragment ni URL signée. Une image ambiguë, non liée ou sur un hôte tiers laisse `thumbnail_url` vide. Aucun GET supplémentaire n’est effectué par le plugin. La présence réelle de ce balisage dans la page privée reste à qualifier.
La fiche publique de certains livres propose une image sur
`imagedelivery.net` ; cela ne qualifie pas les images des cartes de prêt.
Ce travail n'ajoute aucun achat, droit, URL de téléchargement ni mutation.

## Correctif couverture 0.3.8 — variante CDN publique /default (#3104)

La variante `imagedelivery.net/.../default` identifiée sur une fiche BnF
publique est désormais admise, avec le même rattachement impératif à l'ancre
de ressource exacte du prêt. Hôtes, structure de chemin et refus des URL
privées/signées, queries et fragments restent inchangés. Une image hors
de l'ancre ne devient pas une couverture attestée. Aucun GET supplémentaire
ni date n'est inventé. La présence réelle de cette image dans la carte privée
reste non qualifiée ; ce correctif seul ne clôture pas #3104.

## Préparation du téléchargement explicite — 0.3.1

Le fournisseur expose uniquement pour intégration future son adaptateur
`download_digital(loan, account, target_dir, explicit_user_action=True)`, **sans déclarer**
`digital_download` ou `acquisition.download` dans ses capacités publiques.
La synchronisation ne l'appelle jamais : seul le parcours CoreUI initié par
l'utilisateur invoque l'adaptateur avec `explicit_user_action=True`. Toute
invocation générique est refusée avant une requête Web. Le garde Core
`bnf_web_explicit_only` reste actif : aucun ACSM n'est obtenu à l'ouverture.

Ce chemin exige une connexion active, l'objet de compte **exact** et un prêt
numérique avec la même identité / connexion / ressource que la dernière page
d'activité observée. Il relit la page d'activité du même compte avant
**un seul GET** de `/loans/{id}`, construit à partir de l'identifiant déjà
observé et non d'une URL arbitraire. Les redirections sont toutes refusées,
sans retry ni POST. Une page vide, un prêt disparu, un autre compte ou une
session fermée bloquent le téléchargement même si le ledger conserve le droit.

Le corps reçu est plafonné à 2 Mio, n'est reconnu comme *candidat ACSM* que
si sa structure XML bornée a la racine Adobe ADEPT `fulfillmentToken` de
type `loan`; les DTD et entités sont refusées. Un `.bin`/MIME ne suffit pas.
Le token validé est sauvegardé dans le dossier de staging Core demandé,
avec un nouveau nom neutre `.acsm` et permissions 0600, sans écraser un
fichier existant. L'URL privée, le corps XML et la référence de prêt ne sont
pas envoyés dans les diagnostics ni retournés comme URL de catalogue.

**Ce n'est pas un fulfillment ADEPT ni une preuve transactionnelle serveur.**
Le parcours CoreUI est disponible depuis #3110 : deux confirmations séparées
(GET du jeton, import ADEPT manuel), contrôle du compte et de l'identité.
Un import réussi reste identifié comme `manual`, sans inventer une attestation
de droit BnF. Le Core revalide indépendamment le jeton et n'accepte pas un
ACSM d'achat ou du XML à entités au seul motif que le fournisseur l'a proposé.
Restent à recetter sur compte réel : réponse effective de `/loans/{id}`,
rattachement/validité du jeton, profil ADEPT d'origine et résultat EPUB/PDF.
Ne pas déduire de cette UI que l'obtention BnF est opérationnelle en réel.

## Recette terrain du jeton ACSM (après #3131)

Depuis **Mes emprunts**, sélectionner un prêt BnF actif, choisir « Récupérer le jeton
ACSM BnF… », puis confirmer cette action. Si un candidat valide est reçu, une
**deuxième confirmation distincte** propose l'import ADEPT, qui reste manuel.
Cette procédure ne crée pas de nouveau droit ; ne pas utiliser d'import
automatique ni annoncer un EPUB/PDF avant résultat ADEPT effectivement attesté.

En cas d'échec, ouvrir Diagnostics et filtrer sur `bnf-pret` ou
`loan_download`. Le Core distingue désormais les actions terminales
`stage_loan_token` et `import_acsm`, avec `phase`, `status` et
`reason_code` fermés, ainsi que la référence de tâche uniquement si elle
existe réellement. La phase `provider_delivery` comprend la **relecture**
du prêt : elle ne prouve pas à elle seule qu'un GET ACSM a eu lieu.
Recueillir seulement l'action et le motif sûr, **jamais** l'URL du prêt,
le XML ACSM, les cookies, les credentials, le HTML privé ou les chemins locaux.
Ces nouveaux événements ne permettent pas de déterminer rétrospectivement
la cause des deux échecs du 25 octobre 2026.

### BnF-Prêt 0.3.4 — localiser le refus avant le GET ACSM (#2055)

Le parcours sous double consentement conserve ses garanties précédentes. Le
fournisseur distingue maintenant les échecs de **relecture d'activité**
(`activity_http_unavailable`, `activity_access_denied`,
`activity_unrecognized_loans`, etc.) des échecs au **GET du jeton ACSM**
(`http_unavailable`, `access_denied`, `invalid_loan_token`, etc.).
Le Core publie les mêmes raisons fermées avec le préfixe `bnf_` dans
`reason_code`. Une raison `bnf_activity_*` prouve que le GET ACSM **n'a pas
été déclenché par cette tentative**. `provider_delivery` reste la phase
englobante : ce seul libellé n'atteste pas le GET. Le cas de prêt disparu
demeure `bnf_loan_not_observed`, lui aussi **avant** le GET.

La révocation de session durant la relecture est désormais vérifiée **avant**
la requête de téléchargement ; cela refuse le GET et ne produit ni jeton
ni import. Aucune nouvelle requête, aucune réponse privée ni URL n'entre
dans les événements. Les anciens plugins 0.3.3 restent compatibles : leur
code `bnf_http_unavailable` en `provider_delivery` ne permet toujours pas
de déduire quelle requête a échoué. Cette distinction n'est vérifiée qu'en
tests HTTPX synthétiques ; la réponse réelle du serveur et ADEPT restent à
qualifier sur un prêt choisi manuellement.

## Catalogue et fiches en lecture seule

Dans le Core, après configuration et connexion du compte BnF, sélectionner
**BnF Prêt** comme source de Recherche. La recherche textuelle générale accepte
les périmètres « Tous » et « Numérique ». Auteur/ISBN comme champs spécialisés,
recherche physique et formats de fichier ne sont pas déclarés. Le client peut
lire les pages publiques sans s'authentifier, mais le Core sélectionne ses
sources parmi les sessions connectées : aucune recherche anonyme dans l'IHM
n'est promise par cette version.

Le GET `/results?q=…` utilise le terme encodé, de 2 à 128 caractères. Le client
suit seulement une ancre visible `rel=next` ou `.pagination a.next`, validée sur
la même origine, les mêmes critères et le numéro suivant. Une recherche lit
au maximum cinq pages dans un budget total de 30 secondes. Aucun numéro de page
n'est reconstruit pour contourner une ancre absente ; l'argument SDK `page`
reste à zéro puisque la pagination est automatique par liens observés.

Les notices sont dédupliquées par ressource, pas par titre. Le titre, l'auteur,
l'éditeur, l'ISBN, la langue et la description proviennent uniquement des
propriétés HTML présentes. Les filtres, hôtes étrangers, liens de prêt/API,
paramètres inconnus et redirections modifiant la requête sont refusés.
Une panne de page ultérieure conserve les notices déjà observées avec un
avertissement ; une panne de première page n'est jamais un catalogue vide réussi.
L'absence de page suivante ne prouve pas l'exhaustivité : le Core reçoit toujours
un avertissement de résultats partiels, et non un faux total ou un zéro exhaustif.

La fiche utilise uniquement `/resources/{id}` du même fournisseur/credential.
Le contrat `ItemDetail` restitue titre, auteur et disponibilité observée. Un
bouton « Emprunter » seul laisse la disponibilité inconnue ; un marqueur
explicite d'indisponibilité peut être restitué. Aucun exemplaire ou droit n'est
fabriqué. Ni la recherche ni la fiche ne déclenchent SSO, emprunt ou téléchargement.
La fermeture invalide les résultats en vol et interrompt la pagination.

**Qualification restante :** les structures DOM et le garde microdata Book sont
couverts par fixtures synthétiques ; aucune recette de catalogue/fiches réels
n'est revendiquée tant que l'accès depuis l'environnement reste refusé.

## Validation

```bash
PYTHONPATH=src python -m pytest src/plugins/provider-bnf-pret/tests -q
python scripts/verify-packaging.py
python -m unittest scripts.test_plugin_packaging
python scripts/package-plugin.py src/plugins/provider-bnf-pret --out dist
```

Tests HTTPX MockTransport : chaîne SSO/formulaires/cookies, inventaire partiel,
compte exact, deux sessions isolées, HTML modifié, erreurs 403/429, taille,
redirection interdite, absence de mutation et fermeture. Les suites Core et
CoreUI couvrent le double consentement, l'immuabilité du prêt choisi et la
validation indépendante de l'ACSM. Ces tests synthétiques ne remplacent ni la
recette du GET réel, ni celle d'ADEPT.

## Diagnostic des lectures de prêt (0.3.6)

Le journal canonique et Diagnostics reçoivent `BnF HTTP`, filtrable par
`provider_id=bnf-pret`. Chaque lecture existante de l’activité ou du jeton
émet `begin`, `response` si des en-têtes ont été reçus, puis `terminal`, avec
un `request_ref` commun aux observations de cette requête uniquement.
`logical_endpoint=activity` / `phase=activity_recheck` distingue la relecture
de `logical_endpoint=loan_token` / `phase=loan_token_get`. Le statut
`http_status` numérique et le `content_type` reçu sont conservés sans
reconstruction. Avant réception, le statut HTTP est absent. Une interruption
de lecture après HTTP 200 reste un échec de transport. Une redirection HTML
est un résultat partiel `redirect_received`, pas une lecture de contenu achevée.

`http_read_completed` atteste seulement la lecture HTTP bornée : validation
ACSM, staging Core et fulfillment ADEPT restent des étapes indépendantes.
Aucune nouvelle requête, relance, modification de route ou capture de
formulaire SSO n’est ajoutée. Un échec d’activité ne déclenche pas le GET jeton.
Les réponses de test sont synthétiques ; la cause HTTP du refus terrain et
le fulfillment réel restent à qualifier dans #2055.

## Action ACSM signée du portail (0.3.7, #2055)

Après le consentement existant et une relecture positive du prêt/ressource
sur le compte exact, le plugin extrait le contrôle ACSM de ce même HTML frais.
Les attributs observés sont les classes `actions-list__link`,
`actions-list__sublink`, `js-accordeon__item` et
`aria-controls="{loan_id}-reader-acs"`. Le chemin du href doit désigner
exactement ce prêt. Ce rattachement explicite ne suppose pas que le contrôle
est enfant de `.js-loan` ; si une carte le contient, ses identifiants doivent
également correspondre. Les fixtures couvrent les deux positions, sans
prétendre attester les parents DOM du portail réel.

Seuls HTTPS sur `pret.bnf.fr/loans/{id}` et les cinq paramètres uniques
`device_type=reader`, `drm=acs4`, `nonce`, `timestamp`, `token` sont admis.
Les trois dernières valeurs restent opaques : aucun format de signature,
recalcul, réordonnancement ou nouvelle sérialisation n'est inventé.
L'attribut HTML est décodé une fois (`&amp;`) puis la query transmise intacte.
Un contrôle absent, invalide, contradictoire ou lié à une autre carte est
refusé avec `url_refused` ; aucun GET non signé de secours n'est exécuté.

Le même client authentifié exécute un seul GET sans suivi de redirection ni
retry. La session est revérifiée avant/après le réseau et après extraction.
L'URL ne devient aucun champ de Loan, snapshot d'inventaire, diagnostic ou
cache ; seul le HTML frais du parcours explicite la fournit. Les validations
ACSM et le staging privé existants restent suivis du consentement ADEPT
séparé. Tests synthétiques réussis ne valent pas recette du GET signé réel
ni preuve de fulfillment ADEPT ; #2055 conserve ces validations terrain.


## Livraison ACSM sélectionnée vers Core — P0 #3633

Cette tranche ne concerne **que le contrat de livraison candidat BnF** et ne
réimplémente ni les snapshots typés #3628, ni la Sync, ni ADEPT Core.

| Situation | Observation / refus côté BnF | GET token | Staging |
| --- | --- | --- | --- |
| Prêt actif HTML authentifié, paire `loan_id/resource_id` et contrôle signé ACS4 uniques | Offre opaque `bnf.reader_acs4:…` scoppée sur identité, credential, compte, prêt et édition ; couverture toujours partielle | Un GET HTTPS signé seulement après admission Core et relecture fraîche | ACSM brut vérifié, fichier aléatoire privé, retour `(Path, "")` sans URL |
| Aucun prêt, action absente, URL signée contradictoire, page remplacée ou identifiant différent | `unknown` / refus explicite | **0** | **0** |
| Owner A→B, A→A par nouvelle instance, credential ou session modifiés pendant relecture | Refus avant GET, même si tous les champs du compte remplacé sont identiques | **0** | **0** |
| Réaffectation pendant transfert ou staging | Refus au contrôle suivant, suppression de l'ACSM éventuellement écrit | Au plus le GET déjà commencé, jamais un second | **0** final |
| URL signée tournée à droit/compte/prêt constants | Offre opaque stable ; URL uniquement depuis le nouveau HTML frais | Un GET de la nouvelle signature | ACSM du prêt exact |
| 302, 401, 403, 429, 500, HTML, ACSM malformé, XML hors namespace ADEPT, dépassement taille | Échec transport/token typé, sans fallback ni retry signé | Au plus un GET | **0** |
| Staging Core absent, non-répertoire, lien symbolique ou argument invalide | Refus avant tout réseau privé | **0** | **0** |

`rights_selected_delivery_offer_snapshot(..., explicit_user_action=True)`
requalifie l'unique action signée du prêt exact, et
`rights_selected_delivery_candidate(..., delivery_offer_id, target_dir,
explicit_user_action=True)` exige le broker Core unique et un répertoire de
staging **déjà créé**. L'exécuteur reread le prêt exact, puis
`download_digital` relit encore l'activité immédiatement avant le GET
`/loans/{id}` signé. Aucune URL signée n'est publiée ni retournée.
Une date d'expiration n'est **pas inventée** depuis le HTML : seule la
présence actuelle dans `active-loans` et d'une action signée admissible vaut
observation positive de candidature. Un refus d'accès HTTP au jeton peut
signaler une expiration, sans présumer de sa cause métier.

La révalidation pendant/après GET vérifie non seulement les valeurs gelées
`provider/credential/account/identity/loan/edition` et le broker, mais
aussi **l'instance unique vivante** du compte de session. Une réassignation
identique A→A échoue donc sans publier l'ACSM, de même qu'une mutation de
`session.provider_id` ou `session.credential_id`.

**Preuves synthétiques :**
`tests/test_p0_3633_selected_acsm_staging.py` et les suites déjà livrées
`test_p1_3252_selected_delivery_candidate.py`,
`test_p1_3252_bnf_signed_delivery_offer.py`,
`test_explicit_token_failures.py`,
`test_p0_2055_mutable_signed_token_scope.py`, etc.
Tous les flux HTTPX restent en `MockTransport` ; aucun login réel, emprunt,
retour, ADEPT, SQLite, OriginLink ou Placement n'est provoqué.

**Handoff #3228 / #3252 → agent A :** le plugin ne délivre **qu'un ACSM
candidat** et ne confirme ni EPUB/PDF final, ni identité d'activation ADEPT,
ni idempotence Sync après crash, ni liens exacts avec les fichiers
`manual/` préexistants. Les preuves Core de consentement persistant,
ADEPT par identité, SHA/placement atomique, convergence et 2e Sync sans GET
restent à qualifier séparément, ainsi que la recette d'une offre réelle
avec un prêt autorisé. #3633 est borné à la candidature avant Core.

### Réception du candidat par Core (#2055 / #3228)

`tests/test_core_candidate_reception.py` raccorde la vraie session BnF et
son transport HTTPX simulé à `complete_selected_delivery` du Core. SQLite,
le verrou d'acquisition, la détection ACSM, l'inspection du PDF et le calcul
SHA sont réels. Le callback ADEPT est **simulé** : il produit un PDF de test,
sans contacter Adobe ni consommer une licence.

La réception vérifie le prêt/offre exacts, un seul GET du jeton, le profil
Adobe demandé, le staging sous le profil cible et les reçus Core distincts
de l'observation fournisseur. Un profil Adobe différent dans le résultat est
refusé ; une identité réaffectée avant admission ne déclenche aucun GET.
Ce consommateur ne publie lui-même aucun Placement : la transaction et
l'idempotence restent au moteur #3171. Ce test ne certifie ni une licence
réelle, ni l'EPUB manuel préexistant, ni la reprise après crash.
