# Browser Recorder — outil technique opt-in (0.2.24, ZIP privé et SDK providers)

Plugin **désactivé par défaut**. Activez-le dans **Plugins**, puis ouvrez **Browser Recorder** (section Configuration).

**Parcours le plus simple pour un LLM :** utiliser « Parcours HTTP privé »,
consentir à l'archivage dans le coffre chiffré, démarrer Chromium et arrêter
la capture. L'onglet **Sessions et exports** propose alors **« Exporter directement cette
session privée pour LLM… »**, même si aucun identifiant n'a été anonymisé.
Choisir « Enregistrer sous » : le ZIP ordinaire est créé en mode 0600. Il contient les observations privées
retenues, **en clair** ; choisir volontairement à quel LLM le communiquer.
Aucun envoi automatique.

**Guide provider dans les deux exports :** le ZIP direct privé **et** le ZIP
revu/filtré incluent maintenant `plugin-dev-kit/PROVIDER-DEVELOPMENT.md`,
les contrats publics courants `plugin-dev-kit/contracts/*.py`, un exemple
offline `catalog.service` et un `plugin-dev-kit/manifest.json` avec SHA-256.
Lire ce guide AVANT de proposer un plugin : isolation du Core,
catalogue public C ≠ inventaire de prêt/achat A ≠ fichier B, tenants et
credentials distincts, pagination/quotas/DRM, tests autonomes. Source de
référence dans le dépôt : `docs/PROVIDER-DEVELOPMENT.md` ; copie statique
dans ce package identique byte pour byte, vérifiée en CI.
Le ZIP **direct** reste non anonymisé et conserve `trace.json` ; la présence
d'une documentation publique n'expurge pas la trace. Les deux ZIP sont
préparés localement, sans navigation ni envoi supplémentaire.

**Autre possibilité : le ZIP revu/filtré.** Décider chaque identifiant,
préparer l'archive exacte en mémoire, examiner ses membres et confirmer son
export local. Le filtrage peut écarter certaines informations utiles à
l'analyse ; ses limites sont détaillées dans le rapport de couverture.
Le HAR historique reste une fonction secondaire.

## Origines complémentaires : même capture, consentement distinct (#4932)

Dans **Paramètres**, deux champs HTTPS distincts permettent de sélectionner
jusqu'à **quatre origines complémentaires au total** pour la **capture en
cours seulement** :

- **Capture normale complémentaire** : Chromium enregistre les mêmes
  chronologies de requêtes/réponses, corps textuels soumis aux mêmes limites,
  en-têtes techniques, redirections, téléchargements, revue et archives
  privées que pour l'origine initiale.
- **Métadonnées seulement** : seules méthode, durée, statut et ordre de
  requête sont conservés, sans URL, corps ni en-têtes. Le mode historique
  `metadata_only_origins` conserve ce comportement.
- **Non renseignée** : l'origine ne sera pas observée. Une redirection vers
  un autre hôte ne l'autorise **jamais** implicitement.

Saisir uniquement des **origines HTTPS exactes**, sans chemin, paramètre
de query, identifiant, wildcard ou doublon. Le même hôte ne peut figurer dans
les deux modes. Les deux champs sont vidés au démarrage, le consentement est
figé pour la session active, et la couverture numérique apparaît ensuite
**par origine**. Les origines absentes ne sont pas ajoutées au ZIP ou au
coffre ; un inventaire local opt-in est possible pour diagnostiquer les
domaines exclus.

Les pages d'authentification sont un cas particulier de **protection
commune à toutes les origines** (et non une exception fournisseur) :
URL/redirect avec clés connues (`ticket`, `code`, `state`, `password`, etc.)
sont expurgés de leurs valeurs avant rétention ; les champs identifiés comme
secrets dans les corps JSON et formulaires sont masqués avant stockage, et
les formulaires HTML susceptibles de contenir des credentials sont omis.
Les corps de POST/PUT/PATCH et de réponse sur les routes d'authentification courantes (`/login`,
`/oauth`, `/cas`, `/sso`, etc.) ne sont pas lus. Les autres échanges
restent des données **privées** qui peuvent contenir des identifiants non
reconnus : ne communiquer le ZIP direct qu'après examen et consentement.
Le parcours de connexion reste manuel et le collecteur ne rejoue aucun appel.

Le SSO La Rochelle (#3691) est une recette parmi d'autres. Cette fonction
peut également servir à un catalogue, un fournisseur de contenus, une API
ou un autre tenant. Aucune adresse spécifique n'est codée dans le collecteur.

## Accès à la fonction principale

Depuis **Configuration → Browser Recorder**, l'écran est organisé en
**trois onglets défilants indépendamment** :

- **Capture** (ouvert par défaut) : URL HTTPS, consentements de capture et
  de stockage chiffré, démarrage volontaire de Chromium. Pendant la capture,
  Arrêter et Pause/Reprendre restent accessibles sous tous les onglets.
- **Sessions et exports** : sessions chiffrées, consultation, réouverture,
  renommage et suppression confirmée. Export privé direct par **Enregistrer
  sous** : ZIP **en clair**, sans second mot de passe ni anonymisation imposée.
  La revue individuelle et l'aperçu exact du ZIP anonymisé sont disponibles
  ensuite dans le même onglet ; ouvrir une session n'exporte aucun fichier.
- **Paramètres** : origines complémentaires en capture normale ou métadonnées seulement, négociation HTTP,
  fonctions HAR héritées
  repliées. Aucun réglage ne remplace le consentement d'une capture.

Le coffre commun du Core est accessible sans déverrouillage manuel. Le consentement à la capture, au stockage, à la suppression et à l’export reste distinct. La fermeture de la page révoque son accès actif et efface au mieux ses aperçus privés. Les limites de rétention sont configurées dans Diagnostics.

Changer d'onglet n'autorise aucun export. Aucun consentement de
conservation par lot. Le téléchargement de Chromium se fait séparément dans
**Plugins** et exige une confirmation dédiée.

**Lecture du ZIP LLM :** `timeline.json` précise les méthodes, onglets/pages,
statuts, durées, et présence d'une redirection. `coverage-report.json` offre,
pour chaque échange, l'état de capture de chaque corps (requête/réponse),
l'état de sa projection dans le ZIP et la présence réelle d'un corps exporté,
plus les omissions agrégées. `exchanges/NNNN.json` reste la preuve canonique
des champs et liens exportés ; `bodies/` contient uniquement les projections
structurelles expurgées. Une omission ne prouve jamais que la donnée n'existait
pas côté fournisseur. Les valeurs privées, corps bruts ou la table de
correspondance des pseudonymes ne figurent pas dans le rapport.

Le **HAR expurgé et son rejeu GET** restent disponibles dans le panneau
**« Fonctions avancées / héritées »**, **replié par défaut**. Ces fonctions
ne préparent pas l'archive HTTP privée pour analyse LLM et ne doivent
pas être confondues avec la capture textuelle principale.

## Pré-requis

**Aucun prérequis ne s'installe silencieusement au démarrage ou à la découverte du plugin.**

1. Dans **Plugins**, recherchez Browser Recorder. **Il peut rester désactivé pendant la préparation** : la fiche propose déjà **« Installer les dépendances Python… »** si Playwright manque, puis **« Installer Chromium… »** quand le paquet Python compatible est présent. Le diagnostic de disponibilité initial lit uniquement les métadonnées locales de Playwright, sans lancer Chromium, installer ou modifier le cache. La réparation Python exige une confirmation et délègue au **Core dans le venv Cifereca**, en arrière-plan (jamais `pip` dans le plugin), puis réactive sur le thread GTK. En dehors du venv, mutation refusée et erreurs expurgées. L'activation du plugin n'autorise ni téléchargement ni capture.
2. **Chromium est un prérequis distinct** : lorsque Playwright Python est disponible, la fiche **Plugins → Browser Recorder** propose **« Vérifier Chromium… »** (ouvre uniquement le pilote Playwright local et lance un navigateur éphémère sans navigation) ou **« Installer Chromium… »**. Cette dernière action exige une **seconde confirmation** indiquant un téléchargement de plusieurs centaines de Mo et un minimum de 768 Mio libres. L'installation est déléguée au Core, sur un thread de travail, par la CLI officielle Playwright (`install --no-shell --no-remove chromium`) : uniquement le Chromium associé à la version de Playwright, aucun autre navigateur ni distribution système. Le cache doit être sous le répertoire personnel, sans symlink. Les variables `PLAYWRIGHT_DOWNLOAD_HOST` ou `PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST` pointant vers un miroir non revu font refuser ce téléchargement guidé (procédure administrateur distincte), sans bloquer les proxies habituels. Une révision ancienne ne vaut pas preuve de compatibilité.
3. Pendant l'installation, une action **« Annuler Chromium »** arrête le groupe de processus d'installation ; une limite absolue de 15 minutes est appliquée. Les événements publics ne contiennent que des phases et raisons fermées, jamais chemins, proxy, sortie brute de Playwright ou identifiants. L'installation terminée est suivie d'un **test de lancement local** ; si le navigateur est présent mais que les bibliothèques système manquent, elles doivent être installées séparément par l'administrateur.

**Diagnostics Core après une action explicite :** les boutons **« Installer les dépendances Python… »**, **« Vérifier Chromium… »** et **« Installer Chromium… »** dans **Plugins → Browser Recorder** enregistrent désormais le résultat dans la page **Diagnostics**, sous le composant `PLUGIN` et les opérations fixes `browser recorder python dependencies`, `browser recorder chromium verification`, `browser recorder chromium installation`. Chaque événement publié par le broker Diagnostics contient uniquement `plugin_id=ui.page.browser-recorder`, un nom d'opération fermé et un code fermé (`playwright_missing`, `chromium_missing`, `linux_libraries_missing`, `download_failed`, `ready`…). Ces entrées restent consultables **après fermeture du dialogue**, pendant la session Core ; aucun journal privé ni capture réseau n'est créé. La simple ouverture de Plugins n'émet pas d'échec. Les erreurs du parcours de capture lui-même relèvent du volet distinct #2663 restant à couvrir.

**Limites de cette livraison :** aucune commande `sudo`/`apt` automatique, aucun téléchargement au démarrage, à la découverte ou à l'activation du plugin, aucun navigateur empaqueté dans le ZIP. Le contrôle de disque est préventif et ne garantit pas l'espace pendant le téléchargement ; une interruption peut laisser des fichiers incomplets propres à Playwright, sans autoriser la suppression des caches des autres clients. Le **banc CI de provisionnement neuf** (`.github/workflows/browser-recorder-fresh-chromium-e2e.yml`) installe réellement le navigateur avec `install_chromium(consent=True)` dans un **venv et un HOME/cache vides**, après avoir prouvé qu'aucune installation n'a lieu sans consentement. Il lance ensuite la révision compatible, interdit Firefox/WebKit/headless shell et vérifie qu'une seconde demande n'entraîne pas de téléchargement. Les bibliothèques système sont installées **uniquement comme prérequis de la machine CI éphémère**, jamais par le produit. Ce banc valide aussi la **vraie fiche Plugins GTK/libadwaita** avec ZIP installé : un second venv/cache ne possède initialement **ni Playwright Python ni Chromium**, ouvre les dialogues « Installer les dépendances Python… » puis « Installer Chromium… », vérifie l'annulation avant mutation, chaque consentement distinct, la réactivation du panneau sur le thread GTK et le navigateur prêt à la fin. La recette CI contrôle maintenant **les vrais boutons de la fiche Plugins et des dialogues avec des frappes clavier physiques XTEST** (ouverture, annulation/confirmation séparées de l'installation Python et de Chromium, puis fermeture du diagnostic), sur Xvfb et dans un cache vierge. Aucune souris réelle ni revue exploratoire humaine n'est revendiquée. Il reste à réaliser une exploration humaine sur un poste Linux vierge, les tests de coupure/proxy et l'annulation pendant un transfert réel. Aucun fournisseur tiers n'est contacté par la vérification.
 
## Revue au clavier (GTK4)

Sur chaque identifiant de la revue, utilisez **Tab** pour atteindre son
sélecteur, puis **1** pour *anonymiser*, **2** pour *conserver explicitement*
ou **3** pour *exclure l'échange*. Les raccourcis agissent sur l'identifiant
focalisé **uniquement** ; aucune décision n'est appliquée en lot. Les touches
du pavé numérique sont également prises en charge. Naviguez entre les
pages de 24 identifiants avec les boutons « Identifiants précédents /
suivants » ou « Aller au prochain identifiant sans décision » : ce dernier
n'applique **aucun choix** et parcourt seulement les entrées à examiner.
Préparer le ZIP, examiner les membres puis confirmer l'export restent des
actions séparées et volontaires. Dans l'onglet **Sessions et exports**, la
recherche filtre uniquement les **noms des membres** du ZIP exact, et le
bouton « Voir directement le rapport de couverture » ouvre
`coverage-report.json` ; tous les contenus restent en mémoire, sans
recherche réseau ni acceptation implicite de l'archive. L'archive est
**filtrée/revue, pas nécessairement anonymisée** : les identifiants
conservés explicitement peuvent rester sensibles.

## Recette d'installation du plugin (synthétique, #2531)

La CI vérifie désormais deux niveaux distincts :

- **Source** : Chromium réel avec faux fournisseur localhost, interface GTK4
  et clavier XTEST ; aucun compte réel ni export durable publié.
- **ZIP installé** : construction d'un ZIP depuis les fichiers de production,
  validation/extraction dans un `PackageStore` temporaire, activation
  volontaire du plugin désactivé par défaut par le **vrai PluginManager**,
  ouverture de la contribution publique `ui.page` sous GTK4 aux largeurs
  1280/1366/1600. Cette recette utilise un venv isolé avec **Playwright
  Python réellement installé**, mais ne télécharge pas Chromium et ne
  déclenche ni navigation, capture ni export. Les modules ne sont pas
  chargés à partir du chemin source du plugin.

Dans une seconde recette, le **collecteur réseau seul** reçoit une
trace HTTP entièrement fictive : le package installé et sa vraie page GTK
traitent **81 identifiants individuellement**, préparent le ZIP exact,
nécessitent une seconde confirmation, puis enregistrent l'archive 0600.
Un oracle **indépendant** (bibliothèque Python `zipfile` et SHA-256) ouvre
le fichier exporté et vérifie ses membres, leurs contenus exacts et
l'absence d'une clé fictive obligatoirement masquée.

Une **troisième recette E2E** exécute enfin le **vrai Chromium visible
avec la capture HTTP réelle depuis le ZIP installé** : le navigateur
parcourt uniquement un faux fournisseur localhost déterministe (POST
fictif, 302, JSON, XML, 401/403/429, image binaire exclue). Le Core
active la contribution GTK du plugin packagé ; le bouton de capture
démarre réellement `record_http` dans un worker et l'utilisateur du
test arrête la capture, révise les identifiants individuellement, puis
confirme et exporte le ZIP. Un second processus indépendant contrôle
le contenu, les empreintes SHA-256 et la suppression des secrets
synthétiques. **Aucune collecte réseau n'est simulée** dans cette
troisième recette : seule la politique HTTPS/localhost reçoit une
exception strictement bornée au serveur et au port du test. Aucune
politique du produit n'est modifiée.

**Limites :** ces tests ne remplacent **pas** la recette utilisateur
sur VM Linux vierge avec installation Chromium depuis l'UI, la navigation
physique XTEST de la revue/export ou une revue humaine de l'accessibilité
et de l'utilité de l'archive pour écrire un provider. Elles restent
ouvertes dans #2531.

## Capture

1. Saisir l’**URL HTTPS** de départ d'un site autorisé. Sites localhost, IP littérales et URL contenant des identifiants refusés.
2. Cocher le consentement puis **Démarrer la capture**. Chromium est ouvert dans un contexte **éphémère**, sans profil/session persistante et sans téléchargements.
3. Se connecter **manuellement** (SSO/MFA normaux) et naviguer. Le plugin n'enregistre ni frappe, ni DOM, ni capture écran, ni cookies/tokens, ni corps, ni en-têtes HTTP. Il n'utilise **pas** la fonction HAR brute de Playwright.
4. Cliquer **Arrêter / enregistrer** : un fichier `.har` privé (mode 0600) est produit dans le répertoire des données du plugin, sous `captures/`. **Aucune synchronisation ni publication**.

L'export est un **HAR 1.2 limité aux métadonnées**, pas une archive de trafic permettant de reproduire une requête : méthodes HTTP, codes de retour et formes d'URL du seul site HTTPS ciblé. Les paramètres de requête et fragments sont intégralement supprimés ; les segments de chemins qui ne sont pas des mots génériques autorisés deviennent `~redacted~`. Les redirections externes ne sont pas exportées. Seuls **300 événements maximum** sont conservés ; la sauvegarde refuse au-delà de 256 Kio. Le site choisi et les heures restent visibles et peuvent être sensibles : **ne pas publier les captures sans revue**.

## Rejeu sous supervision

Dans l'interface, conserver le même site HTTPS et renseigner le nom `.har` (automatiquement rempli après la capture). **Rejouer** ouvre une nouvelle session vierge et visite la page d'accueil du site. Refaire l'authentification **manuellement** ; chaque clic sur **Étape GET suivante** autorise **une** navigation vers un chemin statique explicitement autorisé. **Arrêter** interrompt le parcours. Les URL dynamiques expurgées et requêtes avec query sont ignorées ; **aucune mutation, POST, authentification automatique, achat ni téléchargement ne sont rejoués**. Le rejeu est donc volontairement **partiel** si le fournisseur dépend d'URL signées, d'étapes POST ou de chemins identifiants.

## Limites de confiance

Le navigateur peut toujours afficher des informations privées et contacter des hôtes tiers **dans la session** pendant une connexion normale ; ce n'est pas un proxy de sécurité et ne bloque pas le trafic réel. Le plugin ne collecte aucun de ces contenus tiers dans l'export. Les sessions sont terminées à l'arrêt ; pas de persistance de mots de passe ou d'état Playwright. Fermer Cifereca termine une capture en cours, sans la sauvegarder si l'arrêt explicite n'a pas eu lieu. Les tests comprennent des doubles Playwright **et des scénarios Chromium/GTK4 réels sur serveurs locaux synthétiques**. La recette en boîte noire de la page distribuée (#2531) reste ouverte ; aucune authentification auprès d'un fournisseur réel n'est revendiquée.

## Travail P1 #2528 — collecteur HTTP en mémoire (sans export utilisateur)

Un nouveau module optionnel `browser_recorder_http.py` est disponible comme
**fondation technique** de la future v0.2 ; il ne modifie pas le bouton
« Arrêter / enregistrer » de v0.1.0, qui produit toujours un HAR expurgé
**sans corps**. La nouvelle API `record_http(...)` lance un Chromium visible
et collecte dans `PrivateHttpTrace` une chronologie de requêtes-réponses
pour les origines explicitement autorisées (par défaut, seule l'origine de
départ). Les échanges sont identifiés, les redirections et les erreurs
réseau sont distinguées, et les corps textuels JSON/HTML/XML/plain peuvent
être conservés en mémoire privée et, après consentement dédié, dans le coffre chiffré. Aucun replay HTTP n'est
déclenché par le collecteur.

Limites fermées par session : 240 échanges, 32 Kio par corps, 768 Kio de corps
cumulés, 15 minutes, six origines explicites au maximum. Les corps binaires
et compressés sont omis. Pour les **requêtes et réponses non compressées**, la taille
effective mesurée par Chromium/Playwright doit être disponible, entière,
strictement positive et sous le budget **avant** toute lecture du corps.
`Content-Length` peut être absent (HTTP chunked/HTTP2) : cela ne bloque
plus un petit corps textuel GET ou POST si Chromium fournit cette mesure fiable. En revanche,
un `Content-Length` présent mais malformé, une taille effective inconnue,
nulle ou excessive, et toute compression sont refusés avant `body()`.
Les omissions comptabilisent séparément `unknown_size`, `oversize` et
`compressed` ; cela n'autorise ni streaming non borné ni relecture réseau.
Les cookies/Authorization/en-têtes secrets ne sont jamais conservés.
L'objet privé contient néanmoins des URL et textes **potentiellement
sensibles** : ne jamais le journaliser ni le transmettre.
`trace.destroy()` efface les références RAM contrôlées quand la revue est
abandonnée. Sans `vault_writer`, `record_http()` n'écrit toujours rien ;
avec `vault_writer` préalablement consenti, seules des projections chiffrées
authentifiées sont persistées, jamais des octets privés en clair.

**Livré séparément** : revue individuelle (#2529) et archive filtrée
explicitement exportable localement (#2530). Des tests Chromium et GTK4
réels sur fournisseurs synthétiques existent ; la recette autonome de la
page distribuée (clavier, focus et accessibilité, #2531) reste à réaliser. Aucune trace HTTP brute n'est exportée ni
transmise ; l'ancien HAR reste strictement limité aux métadonnées.



## Travail P1 #2529 — revue individuelle des identifiants en mémoire

Le module `browser_recorder_review.py` propose localement,
pour chaque **valeur** rencontrée dans une trace privée `PrivateHttpTrace`,
une catégorie et ses contextes (route, paramètres, JSON/XML/texte, en-têtes
techniques). Les valeurs détectées ont des identifiants de revue stables
(`I0001`, `I0002`…), distincts des identifiants de fournisseur.

Les listes jusqu'à **256 identifiants** sont présentées en **pages de 24**
avec navigation Précédents/Suivants. Les choix restent associés à chaque
valeur lorsque vous changez de page ; aucune page ni groupe d'identifiants
n'est approuvé automatiquement. Vous devez terminer toutes les pages avant
de préparer le ZIP.

Chaque valeur reconnue exige une décision individuelle explicite avant de
préparer une **prévisualisation locale seulement** : `anonymize` (même
pseudonyme dans plusieurs échanges), `keep` (conserver la valeur avec risque
résiduel) ou `exclude` (retirer l'échange qui contient la valeur).
Les décisions peuvent être révisées. Les mots de passe, cookies, tokens,
licences et autres secrets détectés ne sont jamais des candidats conservables
et leurs valeurs sont masquées dans la prévisualisation. L'absence de
détection n'est **pas une garantie d'anonymat**.

Une section GTK distincte **« Parcours HTTP privé »** est disponible
en bas de la page Browser Recorder : démarrage volontaire, arrêt/revue,
liste déroulante par identifiant, aperçu local et effacement. Chaque valeur
doit recevoir une décision avant l'aperçu ; aucune décision de conservation
n'est précochée. Quitter la page détruit les références privées contrôlées.
Le HAR v0.1 reste une action distincte ; aucune trace HTTP privée n'y est
ajoutée.

Cette étape de revue ne **publie pas** de données HTTP. Même après toutes
les décisions, le champ `approved_for_export` de l'aperçu brut reste
`false`. L'aperçu GTK est limité à 80 000 caractères et n'est pas celui
du ZIP. La préparation distincte du ZIP applique les projections
structurelles conservatrices ci-dessous. Ne jamais partager la trace brute
ni les valeurs de revue locale.



## Travail P1 #2530 — archive ZIP filtrée et export local explicite

Le module `browser_recorder_zip.py` prépare une **archive ZIP versionnée**
à partir des décisions individuelles de `IdentifierReview`, sans accès
réseau ni écriture spontanée. Les valeurs inconnues de query/path et les
textes libres non approuvés deviennent des marqueurs ; JSON conserve sa
structure et les identifiants explicitement gardés ou pseudonymisés ;
HTML/XHTML est réduit à un squelette de balises/sélecteurs génériques.
XML conserve la casse des balises et les valeurs explicitement revues.
Les corps binaires et les licences/DRM restent exclus.

Dans les `exchanges/*.json`, chaque `bodies.request/response` contient deux
états fermés distincts : `capture_state` est celui du collecteur
(`captured`, `body_omitted`, `binary_skipped`, `redirect`,
`unfinished`, `network_error`, etc.) ; `state` est celui de la
projection expurgée (structure JSON/HTML/XML conservée ou omise).
Une provenance inconnue devient `capture_state=unknown`, sans reprise
de texte libre. La présence d'un corps dans la capture **ne prouve pas**
son export : seul `bodies.*.file` non nul référence un membre du ZIP.

**Fidélité métier bornée (premier lot #2530) :** le ZIP conserve désormais
quelques segments de routes *statiques* courants, les couples de pagination
`page/limit` ou `offset/limit` lorsqu'ils sont canoniques et dans des
bornes étroites, des valeurs d'énumération techniques explicitement
répertoriées (`format=epub`, `sort=title`, `availability=ready`, etc.)
et quelques sélecteurs HTML génériques (`data-testid`, `id`). Ces exceptions
ne s'appliquent jamais aux champs classés comme secrets ; les valeurs
inconnues, titres personnels, champs libres et segments arbitraires restent
masqués. Un nombre de pagination isolé reste masqué sans preuve contextuelle.
Ceci ne remplace **ni** la revue individuelle **ni** l'inspection du ZIP
exact avant tout partage et ne garantit pas la reproduction complète d'un
site. Les endpoints propres à un fournisseur, les corps omis et les contenus
HTML riches restent à traiter dans des lots ultérieurs de #2530/#2528.

**Gabarits de routes (#2530) :** deux segments inconnus différents reçoivent
des marqueurs opaques différents (`~route-0001~`, `~route-0002~`, etc.). Un
même segment, y compris encodé en pourcentage, garde le même marqueur dans
les requêtes et redirections du ZIP. La correspondance reste en mémoire
pendant la préparation et n'est jamais jointe ; ces marqueurs ne désignent
pas le nom réel de l'endpoint et ne sont pas comparables entre archives.
Les identifiants déjà revus gardent leur valeur approuvée ou pseudonyme,
encodé comme un seul segment URL. Les secrets restent masqués ; aucun
gabarit n'autorise un rejeu authentifié. Maximum : 4 096 segments inconnus
distincts par archive, puis refus sans écriture.

**XML revu (#2530) :** les noms de balises/attributs sûrs conservent leur
casse ; les identifiants approuvés ou pseudonymisés et les énumérations
publiques restent lisibles. Autres textes/attributs masqués, secrets masqués
y compris dans leurs sous-éléments. Les namespaces Atom/DC/OPDS connus
restent identiques ; les URI inconnues deviennent des labels opaques propres
au corps (préfixes XML réécrits), sans table de correspondance. XML invalide,
DTD/entités déclarées ou dépassement de budget : corps omis explicitement.
Limites : 32 768 caractères en entrée, 1 600 éléments, profondeur 16,
32 attributs par élément, 16 000 octets projetés. Aucun accès externe.

Le plan fige **les octets exacts** et permet une prévisualisation locale
de chaque membre avec son empreinte SHA-256. Après avoir décidé **chaque**
identifiant, cliquer « Préparer l'aperçu exact du ZIP ». La liste présente
les membres et permet de lire **leur texte exact, sans troncature**. La
case de confirmation est décochée initialement. « Exporter ce ZIP localement »
n'enregistre qu'après cette confirmation ; tout changement de décision
invalide immédiatement le plan. Aucun upload n'est effectué. Bornes : 240 échanges,
600 membres, 4 Mio décompressés, 2 Mio ZIP ; aucun chemin de membre libre
ni profil Playwright. Le fichier local utilise un nom aléatoire et le mode
0600, avec refus des destinations finales symboliques.

**Attention avant partage externe :** la détection et le rescannage des
valeurs sensibles restent heuristiques et **ne garantissent pas
l'anonymat absolu**. Ce flux est expérimental ; seules des fixtures
synthétiques ont été utilisées dans les tests automatiques. La recette
en boîte noire de la page distribuée (#2531) reste à effectuer ;
un export contenant des données privées doit être relu soigneusement
avant toute transmission volontaire. Ne jamais déduire un droit
d'acquisition d'un JSON partiellement préservé.


## Coffre commun chiffré du Core (#8181)

Le service permanent du Core est indépendant de l’activation du Recorder. Le broker `capture.vault` limite chaque producteur à son périmètre autorisé. Le stockage d’une nouvelle session exige le consentement dédié. Aucun rejeu automatique.

La clé aléatoire d’installation (256 bits) est conservée séparément des captures, dans la configuration privée du Core (`capture-secrets/key`, fichier 0600, répertoire 0700). Les captures sont protégées par AES-GCM avec dérivation HKDF et conservées dans `capture-vault-v1`. La perte de la clé interdit l’accès et ne provoque aucune régénération silencieuse. Ce mécanisme ne protège pas contre un processus disposant des mêmes droits utilisateur ou d’une copie simultanée de la clé et du coffre.

Le plafond Recorder est de 3 Mio par session ; le coffre commun contient au plus 128 sessions et 128 Mio. La durée (90 jours par défaut) et le volume sont configurables dans Diagnostics. Les épingles durent 30 jours et sont bornées. Les résidus chiffrés reconnus de plus de 24 heures sont purgés automatiquement. Les sauvegardes interrompues restent consultables selon leur dernier état authentifié. Aucun ancien coffre n’est migré ; son répertoire éventuel reste ignoré.

## Export direct de session privée pour LLM

Choisir une session, puis confirmer la destination locale par « Enregistrer sous ». Le ZIP standard, non chiffré, est écrit en 0600 sans écrasement. Il contient les observations privées et le kit public de développement avec manifeste SHA-256. Relire avant tout partage ; aucun envoi automatique. Le ZIP revu et filtré exige toujours les décisions individuelles et la validation de son aperçu. L’ancien format `.brzip` n’est plus pris en charge.

## Choix de la destination ZIP (0.2.6)

Après l'examen **exact** de chaque membre et la confirmation explicite,
**« Enregistrer sous… le ZIP anonymisé »** ouvre le dialogue natif GTK4
avec choix du **répertoire et du nom** `.zip` par l'utilisateur.
Avant acceptation, aucune écriture n'a lieu. L'annulation, une destination
relative/symbolique, un nom hors format, un dossier inexistant ou un fichier
déjà présent n'écrivent rien et ne remplacent aucun ZIP. L'archive est créée
en mode 0600, et les permissions du dossier sont vérifiées.

Le nouveau bouton de Sessions et exports ouvre **Enregistrer sous** avec nom
`cifereca-session-privee.zip` par défaut. Lorsqu'une session a été renommée puis listée, le nom proposé est par exemple `cifereca-session-privee-parcours-catalogue-bnf.zip`. Il est modifiable dans Enregistrer sous. **Ce nom de fichier est visible en clair** dans le système de fichiers, contrairement au libellé enregistré dans le coffre. Les labels ne sont ni déchiffrés sur le thread GTK ni envoyés à Diagnostics ; verrouillage, actualisation, renommage et suppression invalident leur cache transitoire. Aucun mode 0700 n'est imposé sur le dossier
choisi et aucun mot de passe ZIP n'est demandé. La copie créée est **en clair
et réservée à l'utilisateur (0600)** ; ne pas la confondre avec le coffre chiffré.


## Kit de création de plugin inclus dans le ZIP anonymisé (0.2.6, #2740)

Chaque archive **revue** contient maintenant le répertoire
`plugin-dev-kit/` avec ses guides indépendants, le
`contract-registry.json`, l'API publique **exacte** du Core qui a préparé
le ZIP (`contracts/*.py`) et un `manifest.json` documentaire de
provenance, versions, SHA-256 et limites. Les informations viennent
**uniquement du paquet Python public installé** : aucun réseau à l'export,
aucune requête au fournisseur, aucune copie des modules privés du Core.
Les fichiers de documentation sont visibles individuellement dans la revue
**exacte** du ZIP et soumis aux limites de taille et au même consentement.

`core_version` et `plugin_api_version` indiquent la version observée.
Si la révision Git n'est pas attestée, elle reste explicitement
`unknown`. Si un contrat requis est introuvable ou si le kit dépasse le
budget, **la préparation du ZIP refuse** plutôt que de produire des
spécifications faussement compatibles. L'archive est un guide de
développement, pas une preuve de droits/licences, et ne contient ni tokens
ni profil Chromium ni source private Core.


## Plugin de référence hors ligne (0.2.6, #2740)

Le kit documentaire du ZIP anonymisé inclut désormais un **exemple complet**
dans `plugin-dev-kit/examples/provider-fixture-example/` : un manifeste
`catalog.service`, un module d'entrée `fixture_catalog:PLUGIN` et ses
tests pytest. Il couvre recherche, pagination, détail, cache limité et
exceptions typées, sur **trois livres entièrement fictifs**. Aucun fournisseur
externe, compte, prêt, DRM ou requête réseau ; `owned` et
`downloadable` demeurent `null` (non attestés).

La CI extrait uniquement les fichiers du ZIP, valide leur manifeste contre
le **Core effectivement installé**, construit une archive plugin par
`build_plugin_zip`/`validate_plugin_zip`, puis exécute les tests de cet
exemple dans un sous-processus indépendant. Ceci est une **preuve de
reproductibilité du squelette**, pas une preuve qu'un LLM isolé reconstitue
avec succès une API réelle sans hypothèse. L'exemple doit être adapté aux
endpoints effectivement observés et aux permissions autorisées.


## Bibliothèque de sessions du coffre commun

L’actualisation authentifie les captures et leurs métadonnées via le broker dans une tâche de fond. La sélection est liée à l’identifiant exact, conservée si la session existe toujours. Renommage, épinglage et suppression confirmée passent par le même service ; aucun déverrouillage manuel. Un fichier ou une clé invalide entraîne un refus, jamais un remplacement silencieux. La purge automatique utilise le même verrou que les écritures.

## Téléchargements Chromium sous décision explicite (0.2.17, #2528)

Le mode de capture HTTP privé utilise maintenant **`accept_downloads=True`**
(la capture HAR legacy demeure distincte). Un fichier obtenu par Chromium
reste **temporaire dans Playwright** et est supprimé lors de la fermeture du
contexte, sauf décision spécifique de l'utilisateur. À la fin du parcours
(bouton Arrêter ou fermeture normale de la dernière fenêtre Chromium),
Cifereca propose **un dialogue natif « Enregistrer sous… » par téléchargement**
jusqu'à **8** téléchargements. Annuler ne conserve rien. Chaque dialogue
attend au plus **120 secondes**, sans bloquer la boucle GTK ; fermer la page
annule le choix en cours. Le nom suggéré par le serveur est ignoré au profit
d'un nom générique, modifiable avant validation.

La sauvegarde, indépendante de la trace et du ZIP, accepte un chemin
absolu dont les composants ne sont pas des symlinks ; dossier propriétaire
non modifiable par groupe/autres ; nom ASCII borné, sans traversée ;
**aucun écrasement** d'un fichier existant. Après `Download.save_as` vers un
staging éphémère 0700, le contenu (au plus **128 Mio**) est copié dans
un temporaire 0600 et publié par lien atomique après `fsync`, puis le
temporaire est supprimé. En cas d'annulation, échec ou dépassement de
limite, aucun nom final partiel n'est laissé. Une interruption système
brutale peut cependant laisser un temporaire 0600 après consentement ;
aucun effacement forensique SSD n'est promis.

**Aucun octet de téléchargement ni licence** ACSM/LCPL/EPUB/PDF,
réponse `Content-Disposition: attachment`, MIME de licence, ni contenu
binaire ne rejoint la trace chiffrée, le ZIP anonymisé ou le ZIP privé.
Les événements de téléchargement sont suivis par **codes fermés** et
compteurs locaux, jamais par URL, nom de fichier du fournisseur ou chemin
dans Diagnostics. Les GET restent ceux du navigateur ; aucun rejeu HTTP
ni publication réseau de fichier n'est introduit. La conservation d'un
fichier volontairement téléchargé ne constitue pas un déchiffrement DRM.

Les tests utilisent uniquement un faux portail local et un faux fichier
ACSM, sans compte fournisseur, titre réel ou licence réelle.


## Provenance des omissions HTTP (0.2.14, #2528)

Chaque échange du ZIP anonymisé expose maintenant un code fermé
`bodies.request/response.capture_reason` (taille Chromium non fiable,
en-têtes refusés, compression, contenu binaire, budget, erreur de lecture,
réponse absente ou capture réussie). Le manifeste publie uniquement les
compteurs agrégés `body_capture_reasons` et les anciens états
`capture_state` restent inchangés. Ces codes n'intègrent aucune URL,
identité, valeur d'en-tête ni contenu privé.

Une session chiffrée issue d'une version antérieure reste consultable :
les motifs inexistants sont `unknown`, jamais reconstruits par hypothèse.
Ces diagnostics n'autorisent pas la lecture d'une réponse compressée ou
d'une taille inconnue ; les corps rejetés restent absents du ZIP.

## Budget des ressources statiques (#2864)

Le collecteur réserve au plus 64 des 240 emplacements aux requêtes GET
que Chromium classe image, police, média ou feuille de style (un tiers du
plafond si celui-ci est réduit). Les suivantes sont comptées comme
`static_metadata_budget`, sans conserver leurs URLs ou corps. Ce classement
porte sur l'intention de la requête, pas sur le MIME de la réponse absente.
Aucune éviction ni renumérotation des observations déjà conservées.

Les requêtes document, XHR/fetch, scripts et toutes les mutations restent
soumises au budget général ; les origines, refus des binaires et limites
des corps restent inchangés. Le compteur de ressources hors budget ne doit
pas être interprété comme autant de binaires observés.

Un bandeau commun donne en direct les échanges conservés/plafond, réponses
textuelles réellement conservées, binaires et compression exclus, ressources
statiques hors budget et hors périmètre. Il avertit à 80 % du plafond et
quand aucune réponse n'est disponible pour l'analyse. Le même bilan est
recalculé lors de l'ouverture d'une session privée.

## Réponses compressées : option de négociation HTTP (#2863)

Pour une **nouvelle capture**, activez volontairement dans Paramètres
« Demander des réponses non compressées ». Chromium envoie alors
`Accept-Encoding: identity` sur les requêtes de ce navigateur, y compris
ses onglets et les sites tiers. La collecte reste limitée aux origines
autorisées ; aucun appel supplémentaire, rejeu ni élargissement de capture.
Le choix est indiqué dans la source privée et `coverage-report.json` par
`content_negotiation=identity_requested` ; il s'agit d'une demande, pas
d'une garantie sur la réponse du serveur.

Les réponses effectivement non compressées passent les mêmes limites de
MIME, taille mesurée par Chromium et budget cumulé. Sans l'option séparée « accepter les réponses compressées », un serveur qui
impose gzip/Brotli reste refusé (`capture_reason=compressed`). En cas
d'acceptation volontaire, Chromium fournit le texte déjà décodé et peut
subir un pic mémoire AVANT contrôle des plafonds de rétention. Les captures antérieures ne peuvent pas être
réparées et aucune méthode de décompression bornée n'est revendiquée.


## Diagnostic privé des domaines exclus (#2864)

Pour **une nouvelle capture**, cocher dans Paramètres l'affichage local des
origines exclues. L'option est désactivée par défaut et consommée à chaque
démarrage : aucun consentement implicite pour la capture suivante. Seules
16 origines normalisées et leur nombre de requêtes sont conservés en RAM,
jamais leurs chemins, paramètres, en-têtes ou corps. Les requêtes hors de
cet inventaire sont décomptées séparément. Pause ne collecte aucun domaine.

La liste locale de Sessions est informative : elle ne prouve pas qu'un
CDN ou un domaine est nécessaire au scraper. Pour examiner une origine,
démarrer une nouvelle capture sur son URL avec consentement, ou l'autoriser
explicitement en métadonnées seulement. Aucun ajout automatique au périmètre.
La liste n'entre ni dans le coffre, ni dans aucun ZIP, ni dans Diagnostics ;
fermeture et effacement suppriment aussi l'affichage local.
Les sessions réouvertes n'ont donc pas cet inventaire transitoire.

Les GET de scripts Chromium partagent désormais l'échantillon statique de
64 avec images/polices/médias/feuilles de style. Les 176 places restantes
restent disponibles aux documents, XHR/fetch et POST. Ce classement exprime
l'intention de Chromium, pas le MIME final ; les scripts omis sont comptés
comme `static_metadata_budget`. Une capture de plus de 240 échanges utiles
reste bornée : les compteurs et l'avertissement de capacité restent vrais.

Les corps des GET statiques partagent aussi un sous-budget de 64 Kio maximum
(et au plus un quart du budget textuel initial). Leur lecture reste soumise
aux mêmes preuves de taille et de MIME ; un script omis faute de budget
porte `response_body_reason=over_budget`. Cette réserve empêche les scripts
précoces de vider le budget des réponses API tardives.


## Mode opt-in : conserver les réponses compressées (#2863)

Dans **Paramètres**, pour une capture seulement, cocher **« Accepter les réponses
HTTP compressées »** (gzip/Brotli/deflate). Cette option est désactivée par
défaut et se décoche au démarrage. Elle fonctionne avec ou sans l'option
« Demander des réponses non compressées » : si le site ignore cette dernière
et envoie gzip/br, le collecteur peut maintenant essayer de récupérer le
texte décodé par Chromium. Aucun appel supplémentaire ou rejeu.

**Risque accepté explicitement :** Playwright fournit le corps déjà
décompressé en mémoire. Mesurer la taille des octets transmis AVANT
`response.body()` ne limite pas la mémoire nécessaire à la décompression
Chromium/Playwright. Une compression malveillante peut entraîner un pic de
mémoire, un ralentissement ou un crash. Ce risque ne vient pas du format ZIP
de sortie et ne doit jamais être qualifié de nul. Après lecture, le corps
décodé reste limité à **32 Kio**, au budget textuel global (768 Kio au
maximum) et au sous-budget statique. Les gros corps ne sont pas conservés ;
ils portent la raison `over_budget`. Les tailles réseau inconnues, les
encodages inconnus/empilés, les binaires, licences/téléchargements et les
origines non autorisées restent exclus. Aucun ZIP n'est produit sans action
volontaire. Ce mode ne restaure pas les anciennes sessions.

`compressed_responses_accepted` figure en booléen dans la capture chiffrée
(et le ZIP privé qui la contient), puis dans le rapport de couverture et le
manifest du ZIP anonymisé ; les anciens coffres sans champ restent lisibles.
La négociation `content_negotiation` demeure indépendante. Le corps du ZIP
reste du **texte décodé**, jamais une archive HTTP gzip/br brute.


## Taille maximale d'un corps textuel par capture (#2528)

Dans **Paramètres**, l'opérateur peut choisir **32 / 64 / 128 Kio**
par corps de requête ou réponse textuelle pour une **nouvelle capture**.
Le défaut est toujours **32 Kio** et le choix revient à cette valeur dès le
démarrage. La limite globale de **768 Kio** et la réserve statique de
**64 Kio** restent inchangées. Les budgets sont vérifiés AVANT la lecture
pour les réponses dont Chromium rapporte une taille réseau mesurée ;
les autres portent toujours `size_unavailable` sans lecture. Les MIME
binaires, licences/DRM, téléchargements et origines exclues le restent.

Pour les réponses dont le serveur impose gzip/Brotli, l'option distincte
« Accepter les réponses compressées » doit toujours être explicitement
cochée ; Playwright décompresse alors avant que le code Python puisse
vérifier le plafond décodé : risque de pic mémoire inchangé. Le réglage de
taille ne vaut donc jamais consentement implicite à la compression.

Les sessions chiffrées retiennent `max_body` pour identifier le plafond
réellement utilisé. Le ZIP public revu publie uniquement la valeur numérique
`max_capture_body_bytes` dans `coverage-report.json` et
`manifest.json`. La revue anonymisée peut produire une projection textuelle
plus courte que la source privée ; l'utilisateur doit consulter le rapport de
couverture et les raisons d'omission avant d'inférer une couverture complète.

Cette amélioration réduit potentiellement les exclusions `over_budget`
observées sur le terrain, **sans garantir** la capture d'un corps de taille
>128 Kio, ni résoudre `size_unavailable`. Aucune ancienne archive n'est
réécrite ni complétée.


## Indices numériques d'exploitabilité de la capture privée (#2528)

L'onglet **Capture** distingue désormais les échanges HTTP observés des
**preuves textuelles conservées** : `JSON conservés / JSON observés`,
`HTML conservés / HTML observés`, `POST/PUT/PATCH avec corps de requête
conservé / observés`, et réponses textuelles écartées pour
`size_unavailable` ou `over_budget`. Les MIME comptés proviennent de la
liste fermée du collecteur ; ces ratios n'infèrent ni la nécessité des
réponses absentes ni une complétude de protocole. Les compteurs ne contiennent
aucune URL, domaine, nom d'utilisateur, cookie, identifiant ou corps.

Le même objet numérique `capture_evidence` est présent dans
`coverage-report.json` et `manifest.json` du **ZIP revu** : il décrit
uniquement la capture privée source, **avant** les décisions de revue et les
réductions de projection. Il doit être lu avec `body_coverage` (nombre de
corps réellement publiés) et avec les états/motifs par échange. Une source
avec 1/11 JSON capturé n'en devient pas 11/11 après export. `size_unavailable`
reste refusé faute de taille réseau mesurée fiable ; aucun `response.body()`
aveugle ni rejeu réseau n'est introduit.

## Sélecteur cohérent après une capture (#2884)

La liste des captures authentifiées est actualisée après enregistrement et sélectionne l’identifiant exact de la session terminée. Une actualisation ordinaire conserve la sélection encore présente. Une erreur de lecture est signalée sans substituer une autre capture.

## Finalisation des réponses en attente (#2528)

Une capture **terminée** ne doit plus exporter de réponse en état `pending`.
Au moment de fermer l'enregistrement, le collecteur marque `unfinished`
toute réponse textuelle dont l'événement Chromium de fin était absent, même
lorsqu'un autre callback a déjà retiré la requête des échanges en cours.
La chronologie, le statut HTTP et les en-têtes techniques déjà observés sont
conservés ; aucun corps absent n'est inventé ni relu, aucune requête n'est
rejouée. Le compteur `omitted.unfinished` reflète ces réponses manquantes,
et la raison fermée `response_body_reason=unfinished` traverse le coffre,
le ZIP privé et les rapports de revue.

`completed` signifie seulement **fin de la session de navigation** et
**ne garantit pas la disponibilité de tous les corps**. Ce correctif ne
récupère pas les corps devenus indisponibles et ne change pas le consentement.


## Schéma SSO sans valeurs (#5097)

Lorsqu'une origine **HTTPS** a été explicitement autorisée en capture
**normale**, Browser Recorder peut observer la structure d'un POST
`application/x-www-form-urlencoded` : **noms uniquement** des paramètres
de query et des champs. Une réponse JSON bornée peut fournir ses **clés et
types de premier niveau** (`string`, `number`, `boolean`, `null`,
`object`, `array`) ; les valeurs, objets internes, messages, identifiants,
tickets CAS et contenus CAPTCHA ne sont jamais conservés dans ces schémas.

Le corps d'authentification reste **absent** (`auth_redacted`).
L'inspection structurelle n'est effectuée que si les mesures réseau et
l'enveloppe sont vérifiées **avant lecture** : pour les POST, un
`Content-Length` valide doit correspondre à la taille Chromium ; aucun
contenu comprimé, inconnu ou supérieur à 8 Kio n'est inspecté.
Le nombre de clés est plafonné à 32, seules des clés de forme simple sont
admises. Les pages HTML et objets téléchargés restent exclus, sans rejeu.

Dans une capture `metadata_only` ou hors origine autorisée, aucun schéma
n'est produit. Les codes `schema_captured`, `auth_schema_unavailable`,
`auth_schema_unsupported` et `not_applicable` distinguent les cas.
L'affichage de revue privée présente le schéma brut **sans valeurs** ;
le ZIP revu retire en plus les noms suspects/dynamiques. Le ZIP privé
conserve la projection structurelle sous chiffrement, jamais les corps
d'authentification. Les archives v1 antérieures sans champs optionnels de
schéma restent lisibles sans réécriture.

**Attention :** un schéma observé ne prouve ni l'authentification réussie,
ni la permission d'automatiser un SSO, ni la résolution d'un CAPTCHA.
Le Browser Recorder ne reconstitue pas les paramètres dynamiques perdus,
n'extrait pas les secrets et ne propose pas de contournement DRM.
