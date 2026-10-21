# Browser Recorder — outil technique opt-in (0.2.2, ZIP privé en revue)

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
suivants ». Préparer le ZIP, examiner les membres puis confirmer l'export
restent des actions séparées et volontaires.

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
et compressés sont omis ; la lecture de corps HTTP exige à la fois une
longueur déclarée et un compteur de taille réel Playwright disponibles et
sous les plafonds. Les réponses sans longueur sûre sont omises plutôt que
copiées par une API qui chargerait une quantité imprévisible en mémoire.
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
128 sessions / 128 Mio / 3 Mio par session. Le nouveau ZIP pour LLM est
toujours distinct, expurgé et soumis aux décisions unitaires + confirmation.

Pendant la capture, le collecteur écrit au plus une projection chiffrée
par seconde ; il finalise un état `completed` lors du Stop ou de la fermeture
normale de la dernière fenêtre Chromium. Après une interruption brutale,
**seule la dernière projection déjà durable** est récupérable :
`recording` ne signifie pas complété. Les erreurs récupérables peuvent
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
un cloud par défaut. Le ZIP **non anonymisé de diagnostic** est une action
distincte à ajouter sous #2698 ; ne jamais partager un fichier `.cbrv`
comme s'il était un ZIP sans donnée privée.


## Export de diagnostic privé chiffré (#2698) — action séparée

Depuis **Lister les sessions archivées**, sélectionnez un ID, saisissez votre
phrase secrète du coffre pour déverrouiller la session, puis choisissez une
**NOUVELLE phrase secrète dédiée au ZIP privé, différente de celle du coffre**.
Choisissez un **dossier existant que vous possédez, en mode 0700**, avec
chemin absolu explicite. Ne communiquez jamais la phrase secrète du coffre
à un destinataire de l'archive. La case « Je confirme cet export privé »
est **distincte** du consentement de capture et du ZIP anonymisé pour LLM.
L'export n'est jamais automatique ni téléversé. Le nom aléatoire finit par
`.brzip` et le fichier est créé en mode 0600 sans écraser de fichier.

**ATTENTION — ce ZIP n'est PAS anonymisé** : toutes les URL/query, redirections,
corps JSON/HTML/XML/formulaires et valeurs effectivement retenus sont dans
`trace.json` à l'intérieur de l'enveloppe chiffrée. Ce n'est pas une capture
« wire » complète : aucun cookie/Authorization supprimé au collecteur,
aucun binaire, DRM, profil navigateur ni corps hors budget ne peut réapparaître.
Ne jamais attacher une archive `.brzip` à un ticket ni la transmettre à un
LLM sans déchiffrement et revue personnelle. Un ZIP public demeure soumis
aux choix individuels, à l'inspection exacte et à sa confirmation dédiée.

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

Le ZIP interne n'est jamais écrit en clair par Cifereca. Le fichier
`.brzip` n'est donc **pas** directement ouvert par l'explorateur ZIP :
le déchiffrement en mémoire est une étape volontaire distincte.


## Choix de la destination ZIP (0.2.2)

Après l'examen **exact** de chaque membre et la confirmation explicite,
**« Enregistrer sous… le ZIP anonymisé »** ouvre le dialogue natif GTK4
avec choix du **répertoire et du nom** `.zip` par l'utilisateur.
Avant acceptation, aucune écriture n'a lieu. L'annulation, une destination
relative/symbolique, un nom hors format, un dossier inexistant ou un fichier
déjà présent n'écrivent rien et ne remplacent aucun ZIP. L'archive est créée
en mode 0600, et les permissions du dossier sont vérifiées.

La destination privée `.brzip` est choisie indépendamment à l'aide du bouton
**« Choisir un dossier privé… »** (sélection native) ou d'un chemin absolu
préexistant : le dossier doit appartenir à l'utilisateur avec permissions
0700. Le chiffrement, la phrase d'export distincte et la confirmation restent
obligatoires. Le contenu privé n'est jamais enregistré en ZIP non chiffré.


## Kit de création de plugin inclus dans le ZIP anonymisé (0.2.2, #2740)

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
