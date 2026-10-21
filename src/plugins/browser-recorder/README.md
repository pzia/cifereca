# Browser Recorder — outil technique opt-in (0.1.3, ZIP privé en revue)

Plugin **désactivé par défaut**. Activez-le dans **Plugins**, puis ouvrez **Browser Recorder** (section Configuration).

**Parcours recommandé (expérimental, #2527–#2531) :** utiliser
**« Parcours HTTP privé »**, distinct du HAR historique. Après consentement,
démarrer Chromium, naviguer manuellement (pause/reprise facultative),
puis « Arrêter / revoir ». Pour **chaque identifiant**, choisir anonymiser,
conserver explicitement ou exclure ; préparer ensuite le **ZIP exact en mémoire**,
inspecter ses membres et confirmer séparément l'export **local**. Aucun upload
LLM n'est réalisé. Les valeurs non approuvées et les noms de champs suspects
(identifiants dynamiques, fragments de secrets) peuvent être omis du ZIP ;
une clé de schéma n'est pas automatiquement publique. Relecture humaine
impérative avant partage. Le HAR ci-dessous reste une fonction secondaire.

## Accès à la fonction principale

Depuis **Configuration → Browser Recorder**, le premier parcours visible
est désormais : **Préparer le site et consentir → Démarrer HTTP privé →
Mettre en pause/Reprendre → Arrêter et revoir → Décider chaque identifiant →
Préparer et lire les membres exacts du ZIP → Confirmer et exporter localement**.
Aucun consentement de conservation par lot. Le téléchargement de Chromium
se fait séparément dans **Plugins** et exige une confirmation dédiée.

Le **HAR expurgé et son rejeu GET** restent disponibles dans le panneau
**« Fonctions avancées / héritées »**, **replié par défaut**. Ces fonctions
ne préparent pas l'archive HTTP privée pour analyse LLM et ne doivent
pas être confondues avec la capture textuelle principale.

## Pré-requis

**Aucun prérequis ne s'installe silencieusement au démarrage ou à la découverte du plugin.**

1. Dans **Plugins**, activez Browser Recorder. Si Playwright Python manque, le Core le signale comme **bloqué** et propose **« Installer les dépendances Python… »**. Cette opération attend une confirmation, exécute le gestionnaire de dépendances du **venv Cifereca** en arrière-plan (jamais `pip` dans le plugin), puis réactive les plugins sur le thread GTK. En dehors du venv Cifereca, le Core refuse la mutation ; les erreurs présentées sont expurgées.
2. **Chromium est un prérequis distinct** : lorsque Playwright Python est disponible, la fiche **Plugins → Browser Recorder** propose **« Vérifier Chromium… »** (ouvre uniquement le pilote Playwright local et lance un navigateur éphémère sans navigation) ou **« Installer Chromium… »**. Cette dernière action exige une **seconde confirmation** indiquant un téléchargement de plusieurs centaines de Mo et un minimum de 768 Mio libres. L'installation est déléguée au Core, sur un thread de travail, par la CLI officielle Playwright (`install --no-shell --no-remove chromium`) : uniquement le Chromium associé à la version de Playwright, aucun autre navigateur ni distribution système. Le cache doit être sous le répertoire personnel, sans symlink. Les variables `PLAYWRIGHT_DOWNLOAD_HOST` ou `PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST` pointant vers un miroir non revu font refuser ce téléchargement guidé (procédure administrateur distincte), sans bloquer les proxies habituels. Une révision ancienne ne vaut pas preuve de compatibilité.
3. Pendant l'installation, une action **« Annuler Chromium »** arrête le groupe de processus d'installation ; une limite absolue de 15 minutes est appliquée. Les événements publics ne contiennent que des phases et raisons fermées, jamais chemins, proxy, sortie brute de Playwright ou identifiants. L'installation terminée est suivie d'un **test de lancement local** ; si le navigateur est présent mais que les bibliothèques système manquent, elles doivent être installées séparément par l'administrateur.

**Limites de cette livraison :** aucune commande `sudo`/`apt` automatique, aucun téléchargement au démarrage, à la découverte ou à l'activation du plugin, aucun navigateur empaqueté dans le ZIP. Le contrôle de disque est préventif et ne garantit pas l'espace pendant le téléchargement ; une interruption peut laisser des fichiers incomplets propres à Playwright, sans autoriser la suppression des caches des autres clients. Le provisionnement par ce bouton et son UX doivent encore être validés sur **un vrai poste Linux vierge** ; les doubles de tests n'effectuent pas de téléchargement réel. Aucun fournisseur tiers n'est contacté par la vérification.
 
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
être conservés **uniquement en mémoire privée**. Aucun replay HTTP n'est
déclenché par le collecteur.

Limites fermées par session : 240 échanges, 32 Kio par corps, 768 Kio de corps
cumulés, 15 minutes, six origines explicites au maximum. Les corps binaires
et compressés sont omis ; la lecture de corps HTTP exige à la fois une
longueur déclarée et un compteur de taille réel Playwright disponibles et
sous les plafonds. Les réponses sans longueur sûre sont omises plutôt que
copiées par une API qui chargerait une quantité imprévisible en mémoire.
Les cookies/Authorization/en-têtes secrets ne sont jamais conservés.
L'objet privé contient néanmoins des URL et textes **potentiellement
sensibles** : ne jamais le journaliser, sérialiser ou transmettre.
`trace.destroy()` efface les références contrôlées quand la revue est
abandonnée. Aucune écriture disque n'est autorisée par cette API.

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
HTML/XML est réduit à un squelette de balises/sélecteurs génériques.
Les corps binaires et les licences/DRM restent exclus.

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
