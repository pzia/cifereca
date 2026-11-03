# BnF Prêt — fournisseur HTTPX (#2055)

Depuis la version 0.2.0, le package remplace `ui.page.bnf-pret` par `provider.bnf-pret`, un
`ProviderPlugin` / `ProviderSession` utilisant `httpx.Client`, comme Paris.
L'installation et l'activation ne démarrent aucun navigateur et ne nécessitent
ni Playwright, ni Chromium, ni Browser Recorder. Les anciens modules navigateur sont supprimés.

## Couvertures du catalogue BnF — tranche P1 #4118 (qualification synthétique)

Les résultats de recherche et les fiches n'imposent plus une miniature toujours
inconnue : une URL est projetée dans `SearchResult.thumbnail_url` et
`ItemDetail.thumbnail_url` **seulement** si elle est réellement portée par
la carte liée à la ressource, ou par l'unique microdata `Book` de cette
ressource. Le validateur public d'images utilisé pour les prêts refuse les
origines non autorisées, URLs signées, paramètres privés et chemins suspects.
La famille observée dans la capture privée du 01/11/2026 est admissible uniquement
sous forme `https://covers.feedbooks.net/item/{id}.jpg?size={lettres}&t={chiffres}`
(les deux paramètres obligatoires, ordre libre, aucun autre paramètre, fragment,
port ou chemin alternatif). Les URLs historiques `pret.bnf.fr` et
`imagedelivery.net` conservent leur politique **sans query**.
Les couvertures déclarées `class=cover` / `itemprop=image` du `Book`
exact sont privilégiées par rapport aux images décoratives non marquées ;
les liens imbriqués vers d'autres ressources sont exclus. Toujours sans
résolution par ISBN/titre ni requête HTTP de miniature dans le plugin ;
les images multiples/conflictuelles, d'autres notices, les avatars et les
`og:image` globaux restent `unknown` (`None`). Les doublons de recherche
entre pages ne peuvent pas ressusciter une image contradictoire.

Aucun GET d'image supplémentaire n'est réalisé par le plugin, aucun accès
prêt/compte ni changement de capacité. Tests hors réseau sur DOM synthétique,
dont recherche paginée et projection provider. **À qualifier sur des pages BnF
réelles autorisées** : une capture privée du 01/11/2026 atteste des
balises `img.cover` dans les cartes de recherche et `img[itemprop=image]`
dans les détails, mais ces fixtures synthétiques ne prouvent pas que le
**chargement réseau** ou l'affichage GTK fonctionnent sur l'installation réelle.
Les images de recommandations et les URLs non admissibles restent inconnues.
Recette GTK de deux cartes/détails à couvertures réellement affichées toujours
requise avant fermeture de #4118.

## Échéances réelles BnF — P1 #4146 (version 0.3.29)

L'activité privée officielle `/my_profile/activity` contient une échéance
dans chaque carte `js-loan` validée par prêt/ressource : un unique
`article.js-loan__content .details-content__body > .time-remaining`
avec « Votre prêt expirera le » et un unique `strong` de date française.
Le parseur traduit cette **date calendaire locale attestée** en ISO-8601
sans fuseau/offset inventé, et projette `Loan.due_at` en liste et détail
génériques. Les dates absentes, ambiguës, invalides ou étrangères à la
carte restent `None` ; aucun prêt n'est clôturé par cette seule date.
Aucune lecture de jeton ACSM, mutation ou nouvelle opération réseau.
Le DOM privé ne figure ni dans GitHub ni dans les tests.

## Installer et essayer

Depuis la branche contenant cette version :

```bash
python scripts/package-plugin.py src/plugins/provider-bnf-pret --out dist
cifereca-cli plugin install dist/cifereca-plugin-provider-bnf-pret-0.3.29.zip --replace
cifereca-cli plugin enable provider.bnf-pret
```

Dans Configuration, ajouter une connexion **BnF Prêt** au bon profil Cifereca,
saisir l'identifiant et le mot de passe BnF, puis connecter. Les prêts reconnus
sont disponibles dans Emprunts et Synchronisation via les parcours Core habituels.
L'ancien identifiant de page UI n'est plus une contribution du package.

## Plafond personnel indicatif des emprunts (#4065 P1, plugin 0.3.27)

Le réglage par compte `manual_loan_limit` propose **4** par défaut
et accepte **0 à 99** entiers décimaux canoniques dans Paramètres.
Il appartient à la configuration locale du compte BnF ; il n'est ni une
limite attestée par le portail, ni un consentement, ni une autorisation
pour `borrow`, la synchronisation ou ADEPT. Une ancienne configuration
sans cette clé reste inchangée : la valeur 4 affichée ne crée pas
de nouveau droit. Le schéma déclaratif nécessite **Core Plugin API 1.4.5** ;
la version du package 0.3.27 refuse son installation sur une API antérieure.

## Emprunt BnF Prêt sur clic explicite unique (#2055/#3121, 0.3.26)

**Compatibilité :** le signal typé `BorrowRequestNotSubmitted` exige Core Plugin API **1.4.4**. Mettre à jour le Core avant le plugin 0.3.26 ; les corrections de recherche `/resources?q=…` introduites dans 0.3.23 restent en place.

**Arbitrage du 03/11/2026 — P1 #3121, remplaçant celui du 01/11 :**
le clic explicite sur **« Emprunter »** dans la fiche numérique BnF
constitue **l'unique demande volontaire**. Cifereca n'impose ni
double confirmation, ni popup « J’ai vérifié sur BnF », ni vérification
préalable de **pret.bnf.fr**. La BnF conserve la décision effective de
Pass, quota et disponibilité ; le clic n'est pas une preuve de droit.
La lecture machine informative des quotas reste en
**[#4063 P2](https://github.com/pzia/cifereca/issues/4063)**.

Le manifeste publie `acquisition.borrow` et le descripteur
`ProviderCapabilities.borrow=True`, pour la **seule confirmation
manuelle CoreUI dédiée**. La méthode générique
`ProviderSession.borrow` demeure verrouillée
(`bnf_borrow_requires_core_dispatch`) et refuse AVANT I/O.
Le contrôleur CoreUI appelle le Core avec `confirmed=True` pour
attester **le clic utilisateur unique**. L'argument historique
`site_checked` est accepté mais **ignoré** par le Core ; il ne peut
plus bloquer ou autoriser le prêt. La notice/l'identité exactes,
le bail courant et un claim SQLite de tentative durable restent requis.

Transaction : lecture fraîche activité → fiche exacte → lien/bouton unique non
désactivé → GET confirmation/formulaire/CSRF → sur formulaire média, relecture
d'activité intermédiaire → un POST d'activation sans redirect/retry →
relecture d'activité et corrélation du prêt actif.
`HTTP 200` seul n'est pas une confirmation. **Dès l'appel au transport GET média ou POST**, tout refus HTTP, timeout ou résultat incertain conserve le verrou durable ; aucun deuxième envoi de mutation, même après redémarrage. L'utilisateur vérifie son compte BnF. **Avant tout GET média et avant tout POST**, si le provider atteste une défaillance de lecture de l'activité ou de la fiche, le Core libère le verrou uniquement par CAS sur l'opération exacte (`borrow_not_submitted`). Aucune relance automatique : un nouveau geste volontaire est possible
uniquement si le Core a explicitement attesté `borrow_not_submitted`
et libéré le claim ; après une tentative incertaine, consulter
« Mes emprunts »/le portail BnF, sans réémettre la mutation. Aucun téléchargement ACSM/ADEPT implicite et aucune création de prêt depuis la synchronisation.

**Formulaire BnF observé en octobre 2026 :** la fiche peut présenter `<form method=get action=/media/{opaque}/loans_confirmation>` avec `_resource_id`, `notify`, `email` et `<button type=submit class=button-borrow>`, au lieu d'un lien `<a href=...>`. Un bouton de réservation `button-booking` ne permet jamais d'emprunter. Le parseur valide exclusivement cette forme GET/route et ses trois champs bornés, avec correspondance exacte `_resource_id`, puis le POST `/media/{même opaque}/loans_activation` sous l'admission Core existante. **Retour terrain du 01/11 à 21:47 :** l'action du POST réel conserve dans sa query les trois champs `_resource_id`, `notify`, `email` du GET de confirmation, alors que son corps contient seulement le CSRF caché. La version 0.3.25 rejetait cette query avec `borrow_url_refused`, après GET 200 et sans POST ; 0.3.26 exige désormais une **égalité stricte des trois paires** entre les deux étapes, sans répétition, substitution ni paramètres supplémentaires. Le champ `authenticity_token` est transmis exclusivement par le POST observé. **Attention :** le GET de confirmation peut être mutateur ; la tentative du 01/11 demeure verrouillée, aucune relance automatique ni réarmement de son claim. Les tests 0.3.26 restent synthétiques et l'emprunt réellement confirmé reste à recetter. Le GET de confirmation est traité **conservativement comme une mutation possible** : aucun GET depuis `borrow_preflight` ; après le clic explicite unique, son transport consomme le claim et aucun réarmement automatique n'est permis sur timeout ou résultat incertain. Une relecture de prêts actifs après GET interdit un second POST si le prêt cible apparaît déjà. Une seconde relecture après POST atteste l'état réel, sans que HTTP 200 seul suffise. L'archive Browser Recorder du 31/10 documente GET confirmation 200 → POST activation 200 → GET activité 200, mais ses octets privés ne sont pas versionnés dans le dépôt ; les nouvelles fixtures sont purement synthétiques et cette PR ne vaut pas recette transactionnelle réelle.

Le badge HTML générique `.lb_unavailable` est purement informatif lorsqu'un lien d'emprunt unique et actif est présent sur la fiche exacte. Lien absent, désactivé ou ambigu, confirmation / CSRF incorrect ou édition discordante restent bloquants avant POST.

La qualification de la permission pré-POST en quatre signaux a été
abandonnée comme **verrou P0** (voir décision produit #2055). La méthode
read-only `borrower_eligibility_for` n'est conservée que pour
l'information et les futures améliorations P2 ; son résultat `unknown`
ne bloque pas la demande utilisateur. Les tests de prêt de bout en bout
restent synthétiques ; **aucune nouvelle recette d'emprunt réel** n'est
revendiquée avant déploiement et action utilisateur consentie.

## Diagnostic des miniatures observées (#3104, 0.3.15)

La lecture existante de la page d'activité produit un événement canonique
`COVER / BnF loan metadata`, phase `provider_projection`, dans Diagnostics et
le journal habituel. Il porte le scope identité/provider/credential/compte,
`observed_count` et, pour chaque prêt projeté, `loan_id`, `resource_id`, titre,
`thumbnail_url` exacte ou `null` et `thumbnail_status=observed|unknown`.
L'inventaire reste `coverage=partial`, y compris lorsqu'il est vide.

Pour les deux livres de #3104, rechercher cet événement après la Sync : une URL
non vide en sortie provider permet à CoreUI de comparer cache, BookListItem et
chargement GTK ; `unknown` signifie seulement que le parseur n'a pas attesté de
miniature dans la carte exacte. Il ne prouve ni absence d'image côté BnF ni
échec de téléchargement. Comparer séparément la source `cover_resolution` de
Bibliothèque, qui peut provenir de l'EPUB ou d'une notice persistée.

Cette trace n'effectue aucune lecture supplémentaire, aucun GET image/ACSM,
aucune capture HTML et aucune modification de droit. Elle ne contient pas les
actions signées de la page. Sans broker Diagnostics, elle utilise le même
canal `DIAG_JSON` du journal canonique. Un sink défaillant n'efface pas les
prêts ; un changement de session/identité pendant son callback invalide le
résultat avant publication. Les tests sont synthétiques, pas une recette live.

## Retrait du lecteur v1 dans l'acquisition canonique (#3197, 0.3.14)

La livraison ciblée `rights_selected_delivery_candidate` n'appelle plus
`session.loans(account)`, façade de compatibilité v1 surchargeable.
Elle réutilise directement `_read_live_loans_locked`, le lecteur de page
HTML déjà commun à `LoanInventorySnapshot`, sous les mêmes verrous,
vérifications d'identité/credential/époque et revalidation du droit positif.
L'acquisition conserve **une première lecture exacte**, puis une seconde
lecture fraîche dans `download_digital` avant le GET ACSM signé. Aucune
mutation ni téléchargement n'a lieu en inventaire ordinaire, et une page
vide/partielle n'autorise aucune preuve de fin de droit.

Les tests ciblés interdisent artificiellement `loans()` tout en validant
la sélection et un ACSM synthétique via `httpx.MockTransport`.
La façade legacy demeure disponible pour ses anciens appelants, mais
ne participe plus à la sélection canonique. Ce n'est pas une recette BnF
réelle ni une clôture de la migration Core #3197.

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

## Qualification locale structurelle et privée (#2055, 0.3.20)

Pour repérer où se trouvent les signaux **sans publier une capture
personnelle**, l'outil hors réseau `scripts/bnf_borrow_dom_probe.py`
consomme exclusivement un HTML privé **sur l'entrée standard** et imprime
un JSON anonyme à champs fermés. Aucune copie HTML ni session n'est créée,
aucune requête n'est envoyée, aucune donnée de formulaire, nom de compte,
titre, ISBN, identifiant, URL ou CSRF n'est conservée dans le rapport.

Depuis le dépôt, avec des fichiers locaux privés déjà obtenus par une
capture **consentie** (le nom de fichier n'est jamais enregistré) :

```bash
python3 scripts/bnf_borrow_dom_probe.py --page profile < /chemin/prive/profil.html
python3 scripts/bnf_borrow_dom_probe.py --page detail < /chemin/prive/livre.html
python3 scripts/bnf_borrow_dom_probe.py --page confirmation < /chemin/prive/confirmation.html
```

Le rapport `bnf.borrow.dom-qualification/1` ne comprend que : état
`ok/unrecognized/limit/book_scope_unrecognized`, nombre plafonné à
`0/1/2` de *libellés candidats* « prêts restants », « Pass ... valide »,
« exemplaires disponibles », « Confirmer l'emprunt » et leurs chemins
**composés uniquement de noms de balises HTML autorisées** (jamais de
classes, attributs, valeurs, URLs ou texte). L'analyse de la fiche exige
une unique microdata `Book` fermée ; elle signale aussi le nombre
plafonné de contrôles `.button-borrow` et de marqueurs négatifs
`.lb_unavailable` à l'intérieur de ce livre. La page confirmation
retourne seulement le nombre plafonné de formulaires POST et de noms de
champs `authenticity_token/resource_id`, jamais leurs valeurs.

Les sources protégées (cookies/SAML/formulaire de connexion) sont refusées
par le parseur ; la taille maximale est 512 Kio et le JSON de sortie
ne reprend jamais une erreur ou une chaîne du DOM. **Un indice de présence,
même dans un HTML authentifié, ne certifie pas le compte, l'actualité du
quota, le droit du Pass, la permission du livre ni une offre disponible.**
Le rapport conserve donc inconditionnellement
`identity_bound=false`, `quota_verified=false`,
`offer_verified=false`, `borrow_permission_verified=false`,
`authorized=false` et `eligibility=unverified`.

Pour une qualification métier future, comparer **localement et
volontairement** les états d'un même compte connecté et d'une fiche
exacte, relever la provenance et les transitions des signaux, mais
**ne jamais publier les HTML privés, ZIP non relus, traces, captures
d'écran personnelles, mot de passe, cookies, jetons SAML, CSRF ou
liens ACSM** dans GitHub. Le rapport structurel seul ne doit pas
être promu en `BorrowerOperationEvidence`. Le prêt reste désactivé.

## Éligibilité BnF — preuve partielle uniquement (#2055, 0.3.19)

Le fournisseur propose désormais la méthode interne de qualification
`borrower_eligibility_for(item, account)`, **sans nouvelle capability**.
Elle réalise deux lectures GET bornées uniquement sur la session connectée
et le compte exact : inventaire actif puis fiche de l'édition sélectionnée.
Un prêt déjà positif interdit de répéter une demande ; un changement de
compte, de notice, de session, d'epoch ou de binding Core invalide la lecture.
Elle retourne une `BorrowerOperationEvidence` correctement scoppée, mais
**jamais éligible** : l'état de souscription, le quota individuel et la
permission explicite restent `unknown`, car le DOM qualifié ne prouve
aucun de ces droits. Un marqueur textuel `.lb_unavailable` dans la fiche
exacte peut seulement établir `item_offer=unavailable` ; son absence
reste `unknown`, pas `available`.

La méthode ne lit pas le CSRF, ne suit pas la confirmation et ne fait
aucun POST, téléchargement ACSM, ADEPT ni publication de droit. L'absence
sur une page partielle ne constitue pas un quota disponible. Les tests HTTPX
`MockTransport` incluent l'action tentante `.button-borrow`, le refus
d'un prêt actif, le changement de profil/notice et l'offre indisponible.

**P0 bloqué explicitement** : pour activer `acquisition.borrow`, il reste
à qualifier avec un compte réel consentant trois sources autorisées de
preuves positives (abonnement Pass actif, quota disponible, permission
d'emprunt pour cette édition), ainsi que le signal positif d'offre de
l'édition. Les sélecteurs encore inconnus ne sont pas simulés comme vrais.
Ni UI BnF active ni prêt réel : l'interface #4052 conserve son bouton masqué.
La lecture source est volontairement une préparation et ne transforme pas
une capacité de recherche en droit de prêt.

## Parcours BnF explicite non publié (#2055, 0.3.18)

L'adaptateur dormant `BnfProviderSession.borrow_explicit_with_claim`
enchaîne désormais **dans une même session verrouillée** : relecture d'activité
avant la tentative, refus d'un prêt actif exact déjà observé, nouvelle fiche,
nouveau formulaire de confirmation et CSRF, unique POST HTTPX, nouvelle lecture
d'activité, preuve du même `loan_id + resource_id`, puis projection d'une
`Loan` active. L'état de l'inventaire BnF reste `partial`; ce parcours
ne prétend pas démontrer par absence qu'un nouveau droit a été créé.

Préconditions obligatoires : consentement manuel strict, admission Core
`loan.borrow/admitted`, reçu durable Core `request_sent`, bail Core
réévalué à chaque frontière et compte/notice/session identiques. Le fournisseur
vérifie également les scopes locaux/époques et interdit la réutilisation
d'un même `operation_id` dans la session, même si la tentative distante
précédente a expiré. Core doit garantir l'unicité **inter-processus** du
checkpoint, et fournir une permission métier positive indépendante du
simple lien/formulaire HTML. Une réponse HTTP 200 ne vaut pas confirmation :
seule l'activité positive permet la projection. Une réponse indéterminée
ne doit jamais déclencher un deuxième POST.

**Non activé en production** : le manifeste ne publie toujours pas
`acquisition.borrow`, `ProviderSession.borrow` n'est pas implémentée,
l'interface et le Core ne déclenchent pas cette méthode interne. Il reste
à raccorder l'admission effective CoreUI, la permission fournisseur exacte,
le checkpoint persistant Core et le retour de succès au bon compte/profil.
Les tests sont synthétiques, aucune recette BnF réelle revendiquée.

## POST d'activation isolé et inactif (#2055, 0.3.17)

Le transport expose maintenant `ReaderHTTPClient.borrow_activation_once(...)`,
**non raccordé à l'UI ni à `ProviderSession.borrow`**. Il exige strictement
la confirmation explicite, une admission Core `loan.borrow/manual/admitted`,
et un reçu de réservation durable `loan.borrow/request_sent`. Ces valeurs
restent des prérequis **déclaratifs** : seul le futur appelant Core valide
l'authenticité du claim, la permission réelle du livre, le consentement
utilisateur et la portée de session/notice/compte. Le transport vérifie les
champs du formulaire éphémère, le CSRF, la ressource choisie, l'action et
l'origine `https://pret.bnf.fr`; il émet **un POST unique**, sans 30x suivi,
sans retry automatique, sans mutation de catalogue ni publication de droits.
Une réponse HTTP 200 est uniquement un reçu non vérifié. Après appel, le
Core devra relire l'activité et corréler le prêt via
`verify_borrow_activation`. Un timeout consomme le claim et ne permet que
des vérifications, pas une deuxième tentative.

**Ce n'est pas encore un emprunt fonctionnel** : la capacité publique
`acquisition.borrow` reste absente et aucun composant n'invoque cette
méthode. Les tests HTTPX synthétiques vérifient l'absence de requête avant
admission, le POST unique, les refus 30x/4xx/5xx/binaire et les erreurs
sans rejeu. Le garde Core de scope et de checkpoint est traité séparément
dans la PR liée à #2055 ; son intégration applicative reste à réaliser.

## Préqualification d'un nouvel emprunt (#2055, 0.3.16 — tranche lecture seule)

Le parcours humain réel est documenté dans l'issue #2055 : fiche → écran de
confirmation → POST Web unique avec `authenticity_token` → vérification dans
l'activité → téléchargement séparé. La capture privée n'est pas reproduite dans
les sources, tests ou diagnostics.

Le module `cifereca_bnf_pret_borrow.py` inspecte **sans réseau** les HTML
fraîchement obtenus : un lien `.button-borrow` unique dans le `Book` exact,
transition HTTPS de même origine et de type emprunt, puis formulaire POST
unique, jeton CSRF présent, contrôle de la ressource et champs cachés bornés.
Les URLs, champs et jetons sont éphémères, non publiés, avec `repr` expurgé.
Les boutons seuls, comptes étrangers, actions multiples, redirections,
formulaires SSO et chemins d'API privée sont refusés.

Le transport dispose d'un **GET distinct et strict** pour l'écran de
confirmation, sans redirection et sans élargir la liste des endpoints génériques.
`BnfProviderSession.borrow_preflight(item, account)` impose une session
connectée, l'objet Account exact, le credential, l'identité et l'époque
invariables ; il consulte l'activité pour refuser un prêt déjà observé,
puis relit la fiche et la confirmation sous le même scope. Son seul résultat
positif est `status=unverified, reason_code=borrow_confirmation_observed` :
**ce n'est ni une éligibilité attestée, ni une création de prêt**.

La vérification pure `verify_borrow_activation` exige **deux preuves positives
distinctes** après une éventuelle mutation : un lien de prêt unique dans
la page d'activation, puis le même `loan_id + resource_id` dans une
relecture de la section des prêts actifs de la même session. Toute page
tronquée/ambiguë, un lien différent, un reçu sans prêt ou une confusion
de compte sont des refus. Elle ne déduit **pas** qu'il s'agit d'un prêt
nouvellement créé : l'inventaire BnF est partiel et un prêt préexistant
pourrait avoir été absent de la première page. Aucun GET ni POST n'est
déclenché par ce vérificateur pur.

**Inactif en production** : le manifeste n'annonce toujours pas
`acquisition.borrow`, `ProviderCapabilities.borrow` reste faux, aucune UI
n'appelle ce préflight, et aucun POST métier n'a été ajouté au transport.
Les tests HTTPX synthétiques couvrent les refus et la non-mutation.
Pour rendre l'emprunt opérationnel, il faut encore raccorder l'admission Core
#3121, la confirmation utilisateur, le POST unique protégé contre les
répétitions, la relecture positive du prêt exact, puis la recette terrain sur
compte consentant. Aucun succès distant n'est revendiqué par cette tranche.

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

**Qualification P1 #4142 (02/11/2026).** La lecture officielle authentifiée
`/my_profile/activity` d'une **unique section active-loans validée, non vide,
aux cartes /loans/{id} ↔ /resources/{id} univoques**, atteste désormais
l'exhaustivité de la **branche numérique BnF pour cet emprunteur**, sous la
décision produit du 02/11. Le producteur `loan_inventory_snapshot()` publie
alors `status=success`, `bnf_html/digital=complete` et
`pagination_complete=true`. Une page vide sans état zéro explicite, une
section non close, une carte incorrecte/contradictoire, un indicateur de
pagination ou de chargement conservé (y compris masqué) publie `partial`
avec `inventory_not_exhaustive` ; aucune absence de prêt n'est inférée de ces cas.
Le passage à `complete` ne prouve **ni réception locale, ni droit de restitution,
ni expiration**, ni provenance cryptographique des fichiers. Le contrat
`rights_observation_snapshot()` demeure positif seulement et non autorisant
pour la réconciliation négative. Aucun token, ACSM, POST ou GET supplémentaire.

Le canal mutable historique `last_loan_errors` a été retiré du plugin (#3197).
Dans la migration ciblée #3628, le producteur typé utilise directement la
lecture HTML BnF scoppée sous verrou, **sans appeler la façade v1
\`session.loans(account)\`**. Les deux entrées partagent la même implémentation
interne de GET : une seule lecture par invocation. La façade v1 reste
exposée pour les plugins installés selon le contrat API existant ; elle ne certifie jamais l'exhaustivité par elle-même, ni le retour ou la
disparition d'un droit.
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

Le GET `/resources?q=…` utilise le terme encodé, de 2 à 128 caractères. Le client
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


## Correctif recherche catalogue 0.3.22 — #2055 (01/11/2026)

Recette utilisateur : la recherche du portail utilise
`https://pret.bnf.fr/resources?q=r%C3%A9volte`, alors que la version
précédente tentait `/results?q=...` et recevait HTTP 404 (18 réponses
après le déploiement du 01/11). Le plugin utilise désormais `/resources?q=`
pour la recherche en lecture seule et pour la validation des liens de page,
avec une conservation stricte des filtres et du numéro de page observé.
La route `/results` n'est plus autorisée. `/resources/{id}` demeure
la route distincte des fiches ; ni la recherche ni ce correctif ne
réalisent d'emprunt, n'élargissent les routes SSO ou ne contournent
les contrôles HTTPS et de provenance.

Les tests sont synthétiques, en particulier le balisage des cartes et les
liens de pagination. La réponse réelle du portail (HTTP 200, parsing DOM,
résultats affichés et éventuelle pagination) reste à valider après déploiement.

## Catalogue HTML réel 0.3.23 — #2055 (01/11/2026)

Le portail répond à la recherche GET /resources?q= avec une page complète
contenant ul.list-details, li.list-details__item et des articles
view-details itemscope Book ; le nom est présenté dans un h3
.details-content__title, et l'auteur dans une microdata Person imbriquée.
Ces preuves proviennent d'un HTML de recherche réel analysé localement,
**non commité** parce qu'il contient des données de compte et des jetons CSRF.
Les anciens sélecteurs search-results__list/search-resource-result désignent
les fragments d'autocomplétion, pas la page /resources, et restent pris en charge.

Le parseur valide le lien unique de l'édition sous le même domaine,
l'itemtype Book, la présence d'un titre non ambigu et les bornes de page.
Les cartes incorrectes produisent une sortie partielle, pas de droit ni
d'autorisation d'emprunt ; une page inconnue ou vide non attestée échoue
explicitement. La synchronisation, le prêt et les POST restent inchangés.
Les tests utilisent exclusivement des HTML fictifs mimant la structure.
Le parcours HTTP 200 et la pagination réelle demandent une recette après
déploiement, sans importer la capture privée dans GitHub ou CI.


### P1 #4142 — Cohérence des deux projections BnF de synchronisation

Lorsqu'une section authentifiée **non vide** `active-loans` est intégralement
validée et que **tous** ses prêts numériques actifs (identité, compte,
credential et identifiants exacts) se projettent sans omission en observations
de droits, `loan_inventory_snapshot()` et `rights_observation_snapshot()`
déclarent chacun `status=success`, la branche `bnf_html` en
`coverage=complete` et `pagination_complete=true`. La projection v2
`downloadable_resource_snapshot()` conserve ce même niveau de couverture.
Ce signal ne dit rien des fichiers présents : le Core conserve son propre
contrôle des placements, acquisitions, erreurs ADEPT et consentements.

Une page vide sans preuve de zéro explicite, un chargeur/paginateur, une
carte contradictoire, une donnée manquante ou un prêt non projetable impose
`partial` sur les **deux** projections. Même une couverture complète ne
permet pas la réconciliation négative des droits, ni une restitution, une
acquisition automatique ou une suppression de fichier. Tests HTML synthétiques,
sans requête de jeton ACSM.
