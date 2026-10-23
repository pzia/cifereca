# Browser Recorder — outil technique opt-in (0.2.17, ZIP privé en revue)

Plugin **désactivé par défaut**. Activez-le dans **Plugins**, puis ouvrez **Browser Recorder** (section Configuration).

**Parcours le plus simple pour un LLM :** utiliser « Parcours HTTP privé »,
consentir à l'archivage dans le coffre chiffré, démarrer Chromium et arrêter
la capture. L'onglet **Sessions et exports** propose alors **« Exporter directement cette
session privée pour LLM… »**, même si aucun identifiant n'a été anonymisé.
Cliquer **Ouvrir le coffre** et saisir la phrase secrète **une seule fois**,
puis choisir « Enregistrer sous » : le ZIP ordinaire est créé en mode 0600. Il contient les observations privées
retenues, **en clair** ; choisir volontairement à quel LLM le communiquer.
Aucun envoi automatique.

**Autre possibilité : le ZIP revu/filtré.** Décider chaque identifiant,
préparer l'archive exacte en mémoire, examiner ses membres et confirmer son
export local. Le filtrage peut écarter certaines informations utiles à
l'analyse ; ses limites sont détaillées dans le rapport de couverture.
Le HAR historique reste une fonction secondaire.

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
- **Paramètres** : hôtes tiers en métadonnées seulement, négociation HTTP,
  rétention et nettoyage sur aperçu/confirmation, fonctions HAR héritées
  repliées. Aucun réglage ne remplace le consentement d'une capture.

Le bandeau d'état et les boutons **Ouvrir le coffre / Fermer le coffre**
restent disponibles quel que soit l'onglet. Une fois ouvert, le coffre garde
la phrase secrète **en mémoire de la page seulement** : capture autorisée,
renommage, réouverture, export, prévisualisation et suppression n'exigent
plus de la retaper. **Fermer le coffre** efface au mieux la copie mutable
détenue en mémoire, ferme les dialogues privés, masque les aperçus et les
métadonnées déchiffrées, sans supprimer les fichiers chiffrés. La fermeture
de la page produit le même effacement ; aucune clé n'est sauvegardée dans
les préférences ni récupérée automatiquement au redémarrage. Avant ouverture,
la saisie ponctuelle par action reste compatible. Les autorisations de
capture, de suppression et d'export restent distinctes.
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
être conservés en mémoire privée et, après consentement dédié et déverrouillage, dans le coffre chiffré. Aucun replay HTTP n'est
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


## Coffre privé chiffré — sessions différées (#2725)

La page Browser Recorder présente un consentement supplémentaire au
**stockage local chiffré**, puis une entrée de phrase secrète (12 caractères
minimum ; préférer une phrase longue, unique et non partagée). La saisie est
**obligatoire avant de démarrer Chromium** ; en l'absence de coffre sûr ou de
`cryptography` disponible, la capture ne doit pas démarrer. Le Core propose
l'installation/réparation explicite des dépendances Python du plugin.
La simple ouverture de la page n'initialise ni coffre ni collecte.

Chaque parcours autorisé crée un ID aléatoire ; ses observations retenues
(URL/query, redirections, en-têtes techniques, corps textuels limités) sont
sauvegardées dans `private-sessions/<id>.cbrv`, un fichier **AES-256-GCM**
avec clé dérivée de la phrase secrète par **scrypt**, sel/nonce aléatoires
propres à la session et authentification du nom de fichier. Le répertoire
privé doit appartenir à l'utilisateur et être en mode 0700 ; fichiers
0600, sans symlink/hardlink, snapshots atomiques/fdatasync/fsync et bornes
128 sessions / 128 Mio / 3 Mio par session. Deux exports sont distincts : le ZIP **revu/filtré** requiert les décisions
individuelles, alors que le ZIP **session pour LLM** restitue les observations
conservées depuis le coffre chiffré, sans décisions d'anonymisation.

Pendant la capture, le collecteur écrit au plus une projection chiffrée
par seconde ; il finalise un état `completed` lors du Stop ou de la fermeture
normale de la dernière fenêtre Chromium. Après une interruption brutale,
**seule la dernière projection déjà durable** est récupérable :
`recording` ne signifie pas complété. À la réouverture, toute session dont
le dernier snapshot porte encore l'état `recording` est affichée
**« partielle (dernière sauvegarde seulement) »**, jamais en cours ni
terminée ; `interrupted` indique une fin anormale détectée. La date
affichée est celle du dernier snapshot durable, **pas celle du début**.
L'origine/les compteurs restent invisibles tant que la session n'est
pas déverrouillée et ne sont pas publiés dans Diagnostics. Les erreurs récupérables peuvent
produire `interrupted`. Ce mécanisme ne garantit pas les dernières requêtes,
les corps jamais retenus ni les écritures après une panne totale du disque.

« Lister les sessions archivées » affiche seulement des ID opaques ;
l'origine et les corps ne sont pas dans les noms ni les diagnostics. Pour
reprendre une session, sélectionner son ID, saisir la même phrase secrète,
puis « Ouvrir une session — nouvelle revue ». **Aucun navigateur ni
requête fournisseur** n'est lancé par cette action. Une nouvelle revue
n'altère pas la capture source et peut déboucher sur un ZIP anonymisé
différent, toujours avec aperçu et consentement. « Démarrer HTTP privé »
permet des captures en série après sauvegarde de la précédente. La suppression
exige de sélectionner la session, cocher sa confirmation et ressaisir la clé.

**Confidentialité et limites** : perdre la phrase secrète rend le fichier
irrécupérable ; la passphrase n'est jamais enregistrée. Les métadonnées privées
n'apparaissent pas dans le journal applicatif, mais les données restent
sensibles après déchiffrement en RAM. L'archive locale **ne contient pas**
les cookies/Authorization/Set-Cookie exclus en amont, les fichiers binaires,
les échanges hors périmètre, les corps refusés ni un profil navigateur.
La suppression du fichier ne garantit pas un effacement forensique des
snapshots SSD/sauvegardes. Ne pas synchroniser `private-sessions/` vers
un cloud par défaut. L'export privé pour LLM est une action explicite : il crée une **copie en
clair** de la session conservée. Ne pas partager un fichier `.cbrv` comme
s'il s'agissait d'un ZIP ordinaire.


## Export direct de session privée pour LLM (0.2.17)

Pour une capture que vous venez de terminer : après **Ouvrir le coffre**,
dans **Revue et ZIP LLM**, cliquer
**« Exporter directement cette session privée pour LLM… »**. Aucune étape
dans le catalogue Sessions et aucune décision d'anonymisation n'est requise.
Le ZIP correspond strictement à la session affichée.

Pour une capture plus ancienne : cliquer **Ouvrir le coffre** si nécessaire,
ouvrir **Sessions et export privé**, choisir la session, puis cliquer
**« Exporter cette session pour un LLM… »**. Dans les deux cas, choisir
le nom et l'emplacement via **Enregistrer sous**, puis valider.

**Aucune nouvelle phrase secrète de ZIP, aucun dossier 0700 à préparer,
aucune confirmation supplémentaire.** Le coffre demeure AES-256-GCM avec
ses permissions restrictives. Le ZIP de destination est un fichier standard
**non chiffré**, créé en `0600`, jamais écrasé, dans un dossier existant
choisi par l'utilisateur (par ex. Documents ou Téléchargements). Une annulation
ne crée aucun fichier. Aucun envoi à un LLM n'est automatique.

Ce ZIP comprend `README.txt` et `trace.json` avec **les URL d'origine, les
identifiants et les corps textuels effectivement conservés** ; il peut contenir
des données sensibles. Pour travailler avec un LLM, vérifier ses conditions de
confidentialité, lui transmettre explicitement l'archive et supprimer ensuite
la copie claire locale si elle n'est plus utile. Les secrets, réponses binaires,
cookies ou autres éléments rejetés *pendant la capture* ne peuvent pas être
restaurés. Le ZIP revu/anonymisé de Sessions et exports reste disponible en parallèle.

### Compatibilité des anciennes archives chiffrées `.brzip`

Le lecteur historique `CFRZIP01` reste disponible pour les anciennes
archives. Le nouvel export pour LLM **n'utilise plus ce format**.

**Format ouvert documenté `CFRZIP01`** : octets [0:8] = magic ASCII
`CFRZIP01`, [8:24] = sel scrypt aléatoire (16 octets), [24:36] = nonce
AES-GCM (12 octets), [36:] = ciphertext authentifié et tag 16 octets.
La clé est dérivée avec scrypt(`length=32,n=32768,r=8,p=1`) sur les octets
UTF-8 de la phrase secrète. AAD = la concaténation des 36 octets de l'en-tête.
Le plaintext est un ZIP ordinaire contenant `README.txt` et
`trace.json` (schéma `cifereca.browser-recorder.private-session/1`).
Un lecteur indépendant peut déchiffrer en mémoire avec `cryptography` :

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from getpass import getpass
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

data = Path("example.brzip").read_bytes()
assert data[:8] == b"CFRZIP01"
salt, nonce = data[8:24], data[24:36]
key = Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(
    getpass("Phrase secrète : ").encode()
)
raw = AESGCM(key).decrypt(nonce, data[36:], data[:36])
with ZipFile(BytesIO(raw)) as archive:
    print(archive.namelist())  # ni fichier clair ni partage implicite
```

Le lecteur des anciennes enveloppes `.brzip` déchiffre en mémoire.
Pour les nouveaux exports de session, le ZIP est directement lisible
par les outils habituels et n'exige aucune étape de déchiffrement.


## Choix de la destination ZIP (0.2.6)

Après l'examen **exact** de chaque membre et la confirmation explicite,
**« Enregistrer sous… le ZIP anonymisé »** ouvre le dialogue natif GTK4
avec choix du **répertoire et du nom** `.zip` par l'utilisateur.
Avant acceptation, aucune écriture n'a lieu. L'annulation, une destination
relative/symbolique, un nom hors format, un dossier inexistant ou un fichier
déjà présent n'écrivent rien et ne remplacent aucun ZIP. L'archive est créée
en mode 0600, et les permissions du dossier sont vérifiées.

Le nouveau bouton de Sessions et exports ouvre **Enregistrer sous** avec nom
`cifereca-session-privee.zip`. Aucun mode 0700 n'est imposé sur le dossier
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


## Bibliothèque de sessions déverrouillable (0.2.6, #2725)

« Lister les sessions archivées » ne montre que des **ID opaques**.
Saisir la phrase secrète puis « Déverrouiller les métadonnées des sessions… »
lance un travail de lecture AES-GCM en arrière-plan (jamais dans la boucle
GTK). Seules les sessions déchiffrées avec cette clé affichent alors
**date de la dernière sauvegarde, origine HTTPS, statut completed/partial/
interrupted, nombre d'échanges et omissions**. Un fichier chiffré avec
une autre clé ou corrompu demeure « verrouillé/illisible » sans empêcher
la consultation des autres sessions. Aucun index en clair n'est persisté.

« Lister » à nouveau masque les métadonnées ; quitter la page détruit
les libellés déchiffrés en mémoire GTK. Les opérations capturer, ouvrir,
supprimer, exporter sont désactivées pendant l'indexation asynchrone.
Un résultat d'indexation tardif après changement de capture ou fermeture
est ignoré. L'utilisateur peut avoir des **phrases secrètes différentes**
pour ses sessions ; les autres ne sont ni perdues ni révélées.


## Nom de session chiffré (0.2.6, #2725)

Depuis la bibliothèque privée, choisir une session, saisir un nom de **1 à
80 caractères** et la phrase secrète du coffre, puis cliquer
« Renommer cette session chiffrée ». Le travail de chiffrement AES-256-GCM
et de vérification scrypt passe par le broker de tâches, hors du thread GTK.

Le nom est sauvegardé indépendamment de la capture d'origine dans
`private-sessions/<id>.cbrn` (fichier propriétaire 0600, écriture atomique,
ID aléatoire opaque et authentification cryptographique associée à l'ID).
Le nom n'apparaît jamais dans les chemins, les diagnostics, les ZIP
anonymisés ou les ZIP privés. Il n'est affiché qu'après un nouveau
déverrouillage explicite de la bibliothèque ; le bouton « Lister »
remasque les métadonnées. La suppression volontaire d'une session purge
aussi son nom chiffré, sans promettre d'effacement forensique SSD.

Les noms contenant des caractères de contrôle, échappements ou caractères
de direction Unicode sont refusés, tout comme une étiquette altérée ou un
lien symbolique. Les sessions existantes n'ont aucun nom par défaut et
restent compatibles sans migration destructrice.


## Résidus temporaires après crash et quota conservateur (0.2.7, #2725)

Le coffre 0700 conserve au maximum 128 sessions et 128 Mio d'archives.
**Tout** le chiffrement local consomme désormais ce quota : snapshots
`.cbrv`, noms privés `.cbrn`, ainsi que les fichiers d'écriture
atomique `.part` et `.labelpart` qui pourraient subsister après un crash.
Aucune donnée privée ne passe en clair dans ces fichiers.

**Aucune purge automatique** : dans la page privée, « Examiner les résidus
chiffrés sans suppression » affiche uniquement un nombre et une taille,
sans identifiant ni origine. La case de confirmation distincte autorise
ensuite à nettoyer seulement les fichiers temporaires reconnus du Recorder
âgés de **plus de 24 h**. Une capture source `.cbrv` ou un nom `.cbrn`
n'est **jamais** concerné. Les liens symboliques/hardlinks, modes et
propriétaires suspects entraînent un refus sans purge partielle. La suppression
est explicite, irréversible sur les fichiers concernés et ne garantit pas
l'effacement forensique des sauvegardes disque.

Un verrou POSIX coopératif synchronise les snapshots, les renommages,
les suppressions et ce nettoyage. Les sessions interrompues restent
récupérables avec la dernière portion enregistrée. Si le quota est saturé,
faire la revue des captures et supprimer explicitement celles choisies, ou
libérer les seuls résidus temporaires anciens après inspection ; il n'existe
aucune politique automatique de conservation en fonction de l'âge.

## Rétention manuelle des captures terminées (0.2.8, #2725)

Le coffre garde les archives **indéfiniment par défaut**, dans la limite
de 128 sessions et 128 Mio ; **aucune purge automatique** n'existe.
Choisir une durée de 30, 90, 180 ou 365 jours, puis prévisualiser après
déverrouillage : cette étape ne supprime **aucun** fichier.
Seules les captures terminées dont la **dernière sauvegarde** dépasse
le délai choisi sont candidates. Les sessions partielles ou interrompues,
les clés différentes et les snapshots récents sont conservés.

Pour appliquer, cocher une confirmation spécifique, **ressaisir** la phrase
secrète du coffre et cliquer sur la suppression des seules sessions
prévisualisées. L'aperçu expire après dix minutes ; changer le seuil ou
refermer la page l'invalide. Sous verrou POSIX, le coffre réauthentifie
chaque archive et compare les empreintes SHA-256 des captures chiffrées
et de leurs noms chiffrés : toute altération, modification de fichier,
clé incorrecte ou permission non sûre bloque la purge avant la première
suppression. Les archives d'une autre clé, les captures partielles et
les résidus temporaires ne sont jamais concernés.

La suppression est irréversible pour Cifereca sans garantir l'effacement
physique sur SSD/backups. Aucun ZIP n'est automatiquement produit ou
transmis. Les tests fonctionnent uniquement sur des données fictives.


## Vérification d'authenticité des noms lors de la rétention (0.2.17)

Une étiquette de session est protégée par AES-256-GCM et associée
cryptographiquement à un ID de capture précis. La revue de rétention
réauthentifie désormais le contenu du nom privé, **sans l'afficher**, avant
de proposer ou d'exécuter une suppression. Si un nom est altéré,
transplanté depuis une autre session ou chiffré avec une autre clé,
l'aperçu est refusé et **aucune suppression groupée n'est autorisée**.
Les captures, mêmes anciennes, sont préservées. La confirmation manuelle
et le délai d'expiration du plan restent obligatoires.


## Renommage chiffré et concurrence avec la suppression (0.2.17, #2725)

Le renommage privé authentifie **sous le même verrou exclusif POSIX**
l'archive source et son nom AES-GCM existant, puis écrit le nouveau sidecar
atomiquement. La suppression d'une session ou une politique de rétention
manuelle ne peut plus intervenir entre la vérification cryptographique et
l'écriture du nom. Si la capture a été supprimée ou si un nom a été
altéré dans l'intervalle, le renommage échoue sans laisser de sidecar
orphelin, sans restaurer une session supprimée et sans écraser une
métadonnée non authentifiée.

Les tests simulent une suppression effective et une altération du nom
au moment exact où un renommage attend le verrou ; aucune donnée privée
fictive n'est publiée. Le verrou est coopératif entre processus Cifereca
et n'est pas une garantie contre un acteur local ayant accès en écriture
au coffre en dehors de l'application.


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
fermeture, verrouillage et effacement suppriment aussi l'affichage local.
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

## Sélecteur immédiatement cohérent après une capture (#2884)

Après la sauvegarde **terminée** dans le coffre chiffré, la page rafraîchit
immédiatement la liste opaque locale et sélectionne **l'ID exact** de cette
capture, avant toute nouvelle capture. Aucun réseau, déchiffrement
automatique, nom de fournisseur, titre ni secret n'est nécessaire pour
lister les IDs. Un arrêt vide ou échoué ne présélectionne pas de nouvelle
session « terminée ». Si le fichier final n'est pas listable, le message
signale l'index indisponible sans inventer une réussite de déchiffrement.

Les IDs des fichiers sont aléatoires : leur ordre alphanumérique **n'est
pas chronologique**. La liste verrouillée dit désormais « Archive chiffrée
· <ID court> », sans numérotation trompeuse. Un clic manuel sur « Lister »
préserve la sélection précédente si l'archive existe encore ; une **nouvelle
capture finalisée** a seule priorité pour sélectionner automatiquement
son propre ID. Déverrouiller les métadonnées reste un geste distinct.
Une erreur de clé pour une ancienne session n'est jamais réparée par la
capture d'une autre session sous une nouvelle clé ; ne pas conclure à une
corruption cryptographique à partir du seul sélecteur.


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
