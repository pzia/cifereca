## #2656 — Distinguer cache privé valide et nouvelle inspection (P0)

Lors du clic « Examiner les cas », l'activité intégrée affiche d'abord la
préparation. Le **Core seul** annonce ensuite `cached` quand il a réellement
consommé le cache privé de la dernière analyse (usage unique et portée
revalidée), ou `new_scan` quand une nouvelle inspection hors réseau
démarre. Dans ce dernier cas, la ligne mentionne explicitement
« couverture inconnue », la phase bornée et la durée écoulée ; un
compteur de fichiers *observés* ne devient pas une promesse d'exhaustivité.

Les événements de mode sont **deux constantes**, pas des données de compte,
chemin, EPUB ou SQLite. Les callbacks en retard sont ignorés après annulation,
changement de portée ou destruction de la ligne GTK. Le dernier rapport
Maintenance est indépendant de ce statut et reste consultable sans rescanner.

# Réconciliation de bibliothèque — première tranche Core (#2565)

Cette primitive hors réseau inventorie les EPUB/PDF de racines explicitement
autorisées par identité, lit les métadonnées OPF sans couverture/cache et croise
les preuves de fichiers avec un snapshot SQLite. Elle ne répare aucun lien,
ne lance aucune acquisition et ne démarre ni `LibraryService` ni `Storage`.

## Utilisation autonome

Fournir les chemins et identifiants exacts locaux ; les valeurs ci-dessous sont des exemples synthétiques. Ne pas copier
les commandes réelles ou le rapport privé vers les journaux, LLM ou CI.

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m cifereca.core.library_reconciliation \
  --database /chemin/vers/cifereca.sqlite \
  --root identite-exemple=/chemin/vers/bibliotheque/identite-exemple
```

Répéter `--root` pour des racines disjointes autorisées. Aucun répertoire n'est
créé ou découvert automatiquement. La sortie standard est un JSON expurgé :
compteurs et causes fermées, sans chemins, titres, comptes ni SHA. Code retour
`0` pour une analyse complète, `2` pour limites/indisponibilité. Ctrl+C interrompt
la lecture entre entrées ou blocs SHA ; le rapport conserve l'incomplétude.

Pour consulter les preuves complètes **localement**, ajouter explicitement
`--private-report /chemin/local/nouveau-rapport.json`. Cette option est la seule
écriture volontaire : fichier nouveau en mode 0600, jamais de remplacement.
Il contient des données privées (chemins, titres, auteurs, identités, comptes,
SHA et lignes de preuve). Ne pas le joindre à un ticket ou l'exporter vers un LLM.
Le rapport est un constat daté implicitement par son exécution, jamais un jeton
permettant une réparation ultérieure.

## Contrats et limites visibles

- `scan_identity_roots()` reçoit des capacités de racines validées par l'appelant.
  Il refuse chevauchements, symlinks dans les ancêtres/racines/entrées et fichiers
  spéciaux. Limites globales : entrées, fichiers, octets/fichier, octets SHA,
  profondeur, durée, métadonnées privées cumulées (64 Mio au maximum) ; progression expurgée et annulation injectable.
- SHA, OPF et structure/CRC sont lus sur le même descripteur ; inode, taille et dates de
  modification sont revérifiés après lecture, ainsi que le nom courant du fichier.
  Le résultat n'est pas une garantie qu'un livre n'a pas changé ensuite.
- `inspect_epub(..., metadata_only=True)` conserve l'extraction OPF existante,
  sans lire les images ni appeler le cache. Le répertoire central est borné
  **avant** construction de `ZipFile`. ZIP64/multivolume non pris en charge,
  limites ZIP/XML et rejet DTD/entités, y compris UTF-16/32.
- EPUB `ok` signifie métadonnées OPF lisibles. Le champ distinct
  `structure_validation=epub_structure_crc_ok` exige en plus les règles
  structurelles du validateur Core (mimetype, conteneur, racines/package, doublons,
  membres ZIP non chiffrés) et les CRC de tous les membres. L'adaptateur borne
  entrées, octets décompressés cumulés (par fichier et par audit), ratio et durée ;
  chaque bloc est annulable. Aucune couverture n'est extraite ou mise en cache.
  Le graphe ne confirme un EPUB qu'avec ces deux contrôles réussis. Cela ne
  remplace pas EPUBCheck ou une validation de rendu/spine/accessibilité.
- PDF `pdf_structure_minimal` exige signature `%PDF-` et marqueur `%%EOF`,
  **pas** conformité PDF ni preuve DRM. Le graphe ne confirme pas un PDF sur
  cette seule base. Les résultats structurels incomplets/invalides restent visibles.
- `read_library_snapshot()` lit les six tables de bibliothèque et les deux tables
  de rattachements ADEPT locaux en mémoire, sans démarrer `Storage`. Il utilise
  une copie stable de la base sans journal, ou une capture WAL isolée sous Linux
  avec un interpréteur Python normal et APSW (dépendance Core).
- La capture WAL s'exécute dans un processus Python distinct : ouvrir/fermer SHM
  dans l'application pourrait libérer ses propres verrous POSIX. Le processus
  enfant ouvre seulement les fichiers DB/WAL/SHM existants, via descripteurs
  no-follow, et prend des verrous partagés DMS/read0/read1 non bloquants. Il ne
  crée, n'écrit, ne récupère et ne checkpoint aucun fichier source. Des writers
  peuvent continuer ; un changement pendant la capture produit une indisponibilité.
- Le header SHM publié doit être dupliqué, valide et inchangé. Seul son préfixe
  WAL commis est retenu ; les frames ultérieures non publiées sont exclues. Les
  checksums WAL et SHM sont vérifiés pour ne jamais accepter le repli silencieux
  de SQLite sur un état antérieur après une corruption. SQLite natif effectue
  ensuite le rejeu dans un VFS privé uniquement en mémoire, sans fallback disque.
  Les verrous source sont relâchés avant ce rejeu. Les projections finales sont
  query-only/trusted-schema-off. Images source et résultat sont bornés à 128 Mio,
  ainsi que lignes, durée et protocole enfant ; annuler tue et récupère l'enfant.
- Journaux rollback, SHM manquant/occupé, plateforme non Linux, exécutable gelé
  ou APSW absent restent explicitement indisponibles. Il n'y a ni lecture
  immutable ignorant le WAL ni création de SHM pour contourner ces limites.
  Une fermeture normale de Cifereca permet généralement la voie sans journal ;
  **ne pas supprimer manuellement les journaux**.
- `library_audit.audit_library()` orchestre les phases sous un signal d'annulation
  et une échéance partagés. La progression est expurgée ; le statut global inclut
  les limites des candidats et des chemins, même si le graphe a réussi. Les
  racines autorisées sont copiées avant les callbacks. Il s'agit d'une primitive
  privée Core, pas encore du broker public Maintenance.
- `confirmed_link` exige un droit actif d'achat attesté par le manifeste personnel
  Kobo ou la bibliothèque authentifiée Numilog, le même compte/credential/identité/
  achat, un unique ledger `complete`, un placement explicite et un artefact
  `sha256:` de même SHA/taille, et structure/CRC EPUB validés. Aucun droit n'est attribué par nom, ISBN ou SHA.
  Les snapshots incomplets ne produisent pas de confirmation.
- `present_unlinked` est une correspondance d'octets avec un artefact ou une
  sauvegarde de la même identité, sans attribution de droits. Les liens refusés
  restent `local_file_without_attested_right`/`ambiguous` ; les divergences sont
  `hash_mismatch`. Un droit non vérifié n'est jamais appelé livre absent.
- Une passe `inspect_stored_paths()` distincte vérifie les chemins explicites
  des placements dans les seules racines autorisées. `stored_path_missing`
  exige ENOENT, deux observations concordantes et les mêmes ancêtres/racine.
  Permissions, symlinks, fichiers spéciaux, racine absente ou remplacée restent
  `stored_path_unavailable` ; les chemins étrangers sont `stored_path_out_of_scope`.
  Limites de durée/chemins et annulation, sans ouverture du contenu des livres.
- `metadata_candidates()` recherche uniquement des identifiants explicites
  scheme/valeur dans les notices de **la même identité**. Les ISBN 10/13 exigent
  une clé de contrôle valide, sans recherche approximative ni déduction par
  titre/auteur/nom. Plusieurs notices concordantes restent ambiguës. La certitude
  est toujours `metadata_only`, jamais un droit ou une autorisation de réparation.
  Le snapshot ne conserve que ces identifiants bibliographiques des métadonnées
  catalogue (pas les blobs JSON, jetons ou URL). Budgets de références/candidats
  et annulation sont visibles.
- La CLI restreint les lignes privées aux identités explicitement sélectionnées.
  Les acquisitions contradictoires du même droit/compte restent conservées pour
  ne pas transformer un conflit en confirmation. Les artefacts globaux, dépourvus
  de compte ou de titre, servent seulement à comparer les octets.
- Les compteurs par identité/fournisseur utilisent des indices d'identité
  éphémères ; leur correspondance est uniquement dans le rapport privé local.
  Un fichier sans lien confirmé reste `unattributed`, jamais Kobo/Numilog par
  simple SHA, ISBN ou dossier. Les libellés fournisseurs exportés sont fermés.
- Les références ADEPT lues sont les seuls rattachements persistés identité/profil
  et emprunteur Paris/profil. `local_reference_only` ne prouve ni profil utilisable,
  ni prêt actif, ni droit. Aucune licence/certificat/ACSM n'est lu et aucune CLI
  ADEPT ou source fournisseur n'est exécutée.
- Les relations sont des indices éphémères de fichiers et de lignes, avec raison
  et certitude (`bytes`/`attested`). Les sources ADEPT ne sont pas interrogées :
  `adept_remote_inventory=not_requested`. Aucun fichier de profil/licence/jeton
  n'est lu ; seuls les identifiants de rattachement SQLite sont conservés en privé.

## Suite de #2565 (non livrée par cette tranche)

Rapprochements par édition au-delà des identifiants explicitement attestés,
liens catalog_entries/backup plus riches,
inventaire ADEPT distant consenti, API de service et
entrée Maintenance asynchrone avec filtres/inspection. L'absence de chemin dans
cet inventaire ne suffit jamais à conclure `stored_path_missing` : seule la
passe de contrôle explicite ci-dessus peut produire ce constat.

Les réparations sont un lot distinct : jeton de confirmation, preuve fraîche,
transactions et annulation. Ne jamais convertir ce rapport en autorisation de
rattachement ; le protocole Kobo existant reste la seule voie autorisée pour les
cas qu'il sait attester. Les lots UI et réparation n'ont pas de recette attestée
par cette tranche. L'issue #2565 reste ouverte.

Références techniques : [format et verrous WAL SQLite](https://sqlite.org/walformat.html),
[WAL sans SHM externe en mode exclusif](https://www.sqlite.org/wal.html),
[VFS APSW](https://rogerbinns.github.io/apsw/vfs.html).
Le mode exclusif concerne exclusivement les copies privées en mémoire.

## Statut actuel (30/10/2026)

La section suivante documente le **contrat historique de lecture**, conservé
uniquement pour compatibilité. Le tool Maintenance
`maintenance.audit.reconciliation` a été retiré en 0.2.10 : ne plus le présenter
comme une entrée disponible dans les outils ou comme le moteur Sync.
Les audits locaux en lecture seule et les garanties d’intégrité restent valables.

## Facette publique de lecture pour Maintenance

Le broker optionnel `library.reconciliation.read` expose seulement
`audit(identity_id=..., control=...)` via `LibraryReconciliationReadBroker`.
Il conserve le broker historique `library.read`. Le consommateur détecte sa
présence ; l'absence sur un ancien Core signifie fonctionnalité indisponible.

L'identité demandée doit être l'identité active. Les racines sont calculées
sans I/O à partir de `Config.identities()` et de ses `directory_name` normalisés,
avec le sous-arbre identité/Kobo d'un éventuel répertoire de sauvegarde distinct.
Les chevauchements inter-identités sont refusés globalement et les racines de la
même identité dédupliquées. Aucun dossier global/historique ni chemin SQLite ne
permet d'étendre ce périmètre ; les méthodes service qui font mkdir sont exclues.

Le snapshot précède le scan pour refuser un journal de migration de répertoire
encore présent. La configuration, la projection des identités, l'identité active
et son epoch sont revérifiés pendant l'audit et avant la réponse ; un changement
rejette même les compteurs déjà calculés. Ce snapshot reste une observation à
l'instant de capture, jamais une autorisation d'action ultérieure.

Seuls les compteurs/causes fermées et la progression statique `TaskControl`
sont publics. Aucun titre, chemin, SHA, compte, ligne SQLite, graphe privé ou
jeton d'action n'est fourni. Les exceptions sont fermées à cette frontière.
Le broker n'accepte ni chemin, export privé, interrogation distante ni commande
de réparation. L'inspection locale des cas et la page Maintenance restent des
livraisons séparées ; un plugin ne doit pas importer les primitives privées Core.

## Présentation locale des résultats Maintenance

Le framework Maintenance propose « Résultats… » après une exécution terminée,
y compris incomplète. Il affiche le résumé, les avertissements/limites et le
champ public optionnel `MaintenanceResult.details` (tuple de chaînes destinées
à l’utilisateur, vide pour les anciens plugins). Les `facts` opaques ne sont
jamais affichés, sérialisés ou exportés par cette fenêtre. Le texte est littéral,
sélectionnable, défilant et borné (64 lignes par section, 2048 caractères par
ligne, avec indication des coupures). Le plugin choisit ses libellés métier.
Le résultat est oublié au prochain essai, à l’actualisation ou au changement
de contexte ; les callbacks périmés/annulés ne peuvent pas le publier.
Ce contrat ne fournit encore ni filtre ni inspection privée des cas individuels.

## Inspection privée locale depuis Maintenance

« Résultats… → Examiner les cas… » reprend **une seule fois** la projection
privée de la dernière réconciliation terminée (si sa portée et ses fichiers
SQLite/WAL/SHM sont toujours valides et si son âge est inférieur à 5 minutes) ;
sinon, elle lance une nouvelle lecture hors réseau pour l’identité active.
Cette inspection Core, séparée du broker public expurgé,
reste en mémoire et n’offre ni export, ni réparation, ni acquisition. La liste
paginée (50 cas) filtre les fichiers, droits non vérifiés et chemins enregistrés
par fournisseur/état ; l’identité est limitée à celle explicitement active.
Changer d’identité exige de relancer l’analyse, sans consultation inter-identités.

Un fournisseur n’est attribué à un fichier que par son lien confirmé. Un candidat
ISBN, une empreinte ou la source déclarée d’une notice ne suffisent jamais ; les
droits sans fichier vérifié gardent séparément leur fournisseur déclaré. Le détail
local présente chemins, métadonnées OPF, SHA calculé, preuves explicitement liées,
identifiants bibliographiques et niveaux de certitude. Les lignes hors identité
sont masquées, même lors d’un conflit ; licences et blobs arbitraires ne sont pas
chargés. Les constats ne sont pas une autorisation future de réparation.

La projection est bornée à 10 000 cas et 8 Mio de texte, 32 K caractères/cas,
20 preuves de chaque type affichées, avec limites visibles. Le changement de
configuration, de projection d’identités, de base ou d’epoch révoque la session ;
la fermeture, navigation, nouvelle prévisualisation et annulation oublient les
résultats. Chaque lecture locale revalide le contexte ; la fenêtre vérifie aussi
sa révocation pendant sa consultation. Les preuves restent celles de l’analyse,
pas une garantie que le fichier ou un droit n’a pas changé entre-temps.


## Diagnostic WAL par phases fermées — P0 #2565 (21/10/2026)

Le Core trace déjà le parcours `reconciliation_snapshot` (`direct` ou
`wal`, code fermé, durée bornée). Une lecture WAL avec enfant Linux émet
désormais, seulement après l'appel explicite à **Exécuter**, jusqu'à cinq
messages `TRACE reconciliation_wal phase=stage step=<étape> elapsed_ms=<ms>` :

- `capture` : entrée dans la capture cohérente DB/WAL/SHM avec verrous SHM
  partagés. Aucun checkpoint, migration ni connexion SQLite sur source ;
- `validate` : capture achevée, validation du préfixe WAL publié et checksums ;
- `replay` : validation terminée, rejeu SQLite dans une image *privée* ;
- `serialize` : sérialisation de l'image privée en mémoire ;
- `emit` : retour de l'image privée sur le canal binaire déjà existant.

L'enfant écrit **seulement** ces marqueurs fixes dans un canal distinct
`stderr` ; le parent draine les deux tubes sans blocage, n'interprète au
maximum que 1 024 octets du canal de diagnostic et ignore toute ligne inconnue.
Seuls les noms d'étapes appartenant à l'allowlist du Core sont journalisés ;
jamais texte SQLite, traces Python, chemins, métadonnées, SHA ou identifiants.
Aucune donnée de diagnostic privée n'est transmise aux plugins ou publiée
dans l'UI.

Pour interpréter un délai `snapshot_time_budget`, regarder la **dernière
étape fermée émise** avant `reconciliation_snapshot phase=finish stage=wal`.
Cela localise approximativement capture, validation, rejeu, sérialisation
ou transmission, **mais l'absence d'étape ne prouve pas son exécution**.
Les mesures sont des durées cumulées de l'appel parent (horloge monotone),
pas un profil CPU détaillé. Les contraintes restent 10 s par défaut, 128 Mio
maximum, zéro modification du stockage et résultat `incomplete_scan`
en cas de dépassement. Les tests CI utilisent des WAL synthétiques et ne
constituent pas une recette de la bibliothèque utilisateur.


## Progression lisible dans Maintenance — P0 #2565

Pendant **« Préparer l’analyse… → Exécuter »**, le Core affiche maintenant les phases
publiques produites par le broker de réconciliation, au lieu d'une activité
indifférenciée : **Inventaire local**, **Lecture des preuves locales**,
**Comparaison des preuves locales**, **Comparaison des identifiants
bibliographiques**, **Vérification des chemins enregistrés**, puis **Audit
local terminé**.

Le nombre de fichiers déjà observés peut être affiché en phase
**Inventaire local** (ex. « 517 fichier(s) »). Il n'existe **pas** de
dénominateur fiable pour le scan récursif et le calcul SHA : l'interface
ne fabrique donc aucun pourcentage et conserve une activité indéterminée.
**100 % reste réservé exclusivement à l'achèvement réussi**. Un audit
incomplet affiche son résultat et ses limites sans devenir « terminé »
dans la progression.

La présentation accepte uniquement six textes littéraux fermés issus
du Core, et un compteur entier borné. Les noms de fichier, chemins,
ISBN, identifiants, textes de plugin et messages d'exception ne passent
pas dans la ligne de progression. Le broker continue de révoquer le
travail en cas de changement d'identité ou d'annulation. Cette
amélioration d'UX **ne relève pas le délai SQLite/WAL ni les budgets SHA**,
et ne transforme pas un candidat bibliographique en droit attesté.
 

## Échec d’inspection : conserver la cause fermée, sans fausse révocation (P0 #2565)

Lorsque **« Examiner les cas… »** doit lancer une nouvelle lecture hors réseau, le
broker Core peut renvoyer une enveloppe d'échec expurgée (sans session privée)
plutôt qu'une `LocalInspection`. L'interface distingue maintenant les codes
validés de **timeout SQLite `snapshot_time_budget`**, d'indisponibilité du
lecteur WAL, de lecture sécurisée impossible, de base modifiée, de migration et
de **révocation réelle de l'identité**. Un budget atteint **n'indique pas**
que l'identité a changé, ni qu'un livre est absent.

Une enveloppe malformée, inconnue ou présentant plusieurs causes ne devient
pas un diagnostic privilégié : seul un message générique fixe est affiché ;
aucun texte de retour brut, chemin, compte, ISBN ou détail SQLite n'est repris.
Le résultat de l'Inspection n'est jamais réutilisé comme jeton de réparation.
L'inspection réutilise au plus une fois la projection privée fraîche de la
dernière analyse réussie (#2655), ou effectue une nouvelle lecture si elle
est absente/expirée/invalide. Cette revalidation est une condition d'affichage,
**pas une attestation du droit à réparer**.

Si le timeout se répète : consulter localement les traces fermées
`TRACE reconciliation_snapshot` et `TRACE reconciliation_wal` pour
identifier la dernière phase atteinte. Aucun relèvement du budget de 10 s,
ni accès réseau/écriture source, ne résulte de cette amélioration d'UX.


## Projection SQLite bornée par lots — P0 #2565

Après capture cohérente et privée, le traitement des tables SQL se fait
désormais par `fetchmany(256)`, plutôt que par `fetchall()` pouvant
matérialiser jusqu'à 100 000 tuples bruts *en plus* des dictionnaires
d'évidence et métadonnées analysées.

La vérification de contexte/annulation est effectuée à chaque lot et au plus
toutes les 64 lignes projetées, ainsi que par le `sqlite3` progress handler
pendant les requêtes. Cela évite de reconstruire le contexte identité,
configuration et racines **pour chaque notice bibliographique** sur une grande
bibliothèque. Le délai global (10 secondes par défaut), la limite de 100 000
lignes sur l'ensemble des tables, les 128 Mio d'image privée, les causes
fermées `snapshot_cancelled`/`snapshot_time_budget`/
`snapshot_row_budget`, et le principe **zéro écriture** restent inchangés.

Une interruption en cours de projection abandonne **toutes les preuves
partielles** et ne valide aucun lien. Les EPUB, les droits et les libellés
privés ne sont jamais copiés vers les journaux. La recette synthétique en CI
utilise 4 096 notices commises dans une DB en WAL actif. Ceci limite
l'empreinte mémoire additionnelle et le coût des callbacks, **sans attester
que le timeout de la base utilisateur est résolu** : les traces de phase et
la recette réelle demeurent nécessaires.


## Projection SQL ciblée par identité — P0 #2565

Le broker Maintenance fournit au lecteur une **identité active explicite
préalablement contrôlée**. Après capture de l'image SQLite/WAL cohérente,
la projection SQL des tables `entitlements`, `placements`, `backup_items`
et `catalog_entries` applique `WHERE identity_id=?` **dans l'image en
mémoire**, avant le décodage des métadonnées et les budgets de lignes.
L'inventaire et l'audit global hors broker (plusieurs identités autorisées)
ne changent pas de sémantique : sans filtre explicite, toutes les tables
sont projetées comme précédemment.

**Important :** `acquisition_ledger` reste lu intégralement afin de
conserver les acquisitions de comptes étrangers portant sur le même
`entitlement_id` ou le même triplet fournisseur/connexion/édition que
les droits sélectionnés. Ces lignes sont ensuite strictement filtrées par
`scope_evidence` pour ne garder que les conflits établis. Les artefacts
adressés par SHA ne sont pas filtrés par identité, et le SHA seul ne prouve
jamais un droit. Aucune nouvelle association automatique n'en résulte.

Pour diagnostiquer les timeout, un marqueur littéral supplémentaire
`TRACE reconciliation_snapshot phase=projection stage=direct|wal` signale
la **fin de la capture et l'entrée en projection**. Si `snapshot_time_budget`
survient **après** ce marqueur, le temps est consommé dans la projection
en mémoire ; s'il survient avant, exploiter les étapes WAL connues. Une
absence de marqueur n'établit pas à elle seule la phase (journalisation
éventuellement tronquée). Aucun chemin, livre, compte, empreinte ou texte SQL
n'est ajouté aux traces. Les budgets de 10 secondes/128 Mio/100 000 lignes
et les garanties sans réseau ni mutation restent inchangés.


## Inspection compréhensible des droits Numilog — P0 #2657

Une ligne **« Droit sans fichier vérifié »** représente un droit actif non
confirmé par la chaîne *droit → acquisition complète → artefact →
placement explicite → fichier SHA et EPUB validés*. Ce n'est **jamais** une
preuve d'absence physique du livre, ni un indice suffisant pour télécharger.
Un fichier physiquement présent mais non confirmé se trouve dans
**« Fichiers sans provenance attestée »** (pas sous « Local » et pas sous
« Numilog » par simple concordance de nom, de titre ou de SHA).
Un lien de navigation rapide permet de passer des droits non vérifiés
aux fichiers sans droit attesté **sans fabriquer de correspondance 1:1**.

Le libellé local d'un fichier privilégie le titre et les auteurs extraits
de l'OPF ; son nom de fichier reste visible. Une notice privée du même
fournisseur, de la même édition et de la même identité peut fournir
un titre/auteur pour un **droit**, mais un tel titre n'atteste ni la
propriété d'un EPUB local ni son identité fournisseur. Chaque droit
non confirmé indique les **preuves locales manquantes, conflictuelles
ou non vérifiables** : attestation du droit, acquisition incomplète,
placements multiples/absents, chemin non observé, artefact/SHA/taille,
structure EPUB et éventuel audit partiel. L'examen ne produit aucun
plan de réparation, aucun appel fournisseur, aucune mutation SQLite.

La fenêtre conserve une **synthèse non additive** : effectifs des fichiers
inspectés, liens confirmés, fichiers sans droit attesté, droits non vérifiés,
fichiers sans provenance et chemins en anomalie. Les catégories
peuvent se recouvrir et les droits/fichiers n'ont **pas** le même
dénominateur : additionner ces chiffres pour reconstruire « achats =
fichiers » serait erroné. Le filtre texte se combine au fournisseur
et à l'état pour rechercher instantanément titre, auteur, nom de fichier
et identifiant d’édition visibles dans les libellés, sans requête réseau.
Les SHA et le texte technique des preuves restent consultables dans les
détails mais ne participent pas à la recherche libre. Les racines **configurées
pour inspection** sont lisibles **uniquement dans la fenêtre privée** :
leur présence n'atteste pas qu'elles aient été parcourues complètement.
Le panneau « Voir les racines et exclusions » détaille les répertoires
inaccessibles, liens symboliques/fichiers spéciaux ignorés et les causes
fermées des limites de scan. Aucun chemin local n'est ajouté au broker
expurgé, aux diagnostics ou à un export.


## Inspection sans modale d'attente — P0 #2656

L'action **« Résultats… → Examiner les cas… »** n'ouvre plus la boîte de
dialogue d'attente « Inspection locale ». La préparation asynchrone se
signale **dans la ligne Maintenance d'origine** avec un libellé constant
« Préparation des cas locaux… » et un bouton **Annuler**. Si une session
privée en mémoire est encore valide (#2655), elle est affichée directement
sans modalité d'attente, sans nouvelle analyse artificielle et sans
pourcentage inventé. Si une nouvelle lecture est nécessaire, les six
phases littérales du Core peuvent remplacer le libellé sur cette ligne ;
aucun chemin, nom de livre, identifiant, message arbitraire ou SHA n'est
accepté comme progression.

L'ancienne **fenêtre finale de consultation** n'est pas supprimée : elle
s'ouvre uniquement quand la session privée est valide et prête. La fin,
l'annulation, la fermeture du panneau, le changement d'identité ou une
nouvelle exécution retirent l'activité en ligne et invalident les callbacks
tardifs. Les enveloppes d'échec conservent leurs causes fermées sans
confondre timeout SQLite et livre absent. Aucun scan supplémentaire n'est
déclenché pour animer l'interface ; audit et inspection restent hors réseau,
lecture seule et sans droit de réparation/acquisition.


## Comparer un droit non vérifié à ses candidats EPUB privés — P0 #2657

Depuis **Réconciliation → Examiner les cas → Droit sans fichier vérifié**,
une colonne de comparaison s'affiche à côté du détail du droit. Elle montre
**jusqu'à 20 fichiers candidats non probants** et permet de parcourir
leurs métadonnées OPF, leurs chemins et leurs contrôles SHA/structure sans
changer de droit sélectionné.

Les **candidats exacts** viennent uniquement des fichiers **de l'identité
active**, effectivement inventoriés et **sans lien de droit confirmé** :
un identifiant bibliographique explicite correspond à une notice locale
de la même identité, du fournisseur et de l'édition sélectionnée.
`metadata_candidates()` reste fondé **exclusivement sur les identifiants**
et classe ces correspondances `metadata_only` : aucun titre, ISBN papier,
chemin ni SHA ne peut constituer une preuve de compte, d'achat ou de droit.

Depuis les PR **#3046 et #3047**, deux cas supplémentaires sont affichés
sans les confondre avec ces candidats exacts :

- **Zéro candidat exact** : le navigateur commun Kobo/Numilog/Paris peut
  présenter jusqu'à 20 **pistes descriptives** à partir de fichiers EPUB
  observés pour la même identité : **titre OPF ET auteur OPF** concordent,
  après normalisation déterministe des accents/casses/espaces, avec la
  description locale du droit ou de sa notice. Le fichier doit avoir un SHA
  observé, mais le SHA ne sert **pas** au rapprochement descriptif. Ce n'est
  ni un rapprochement fuzzy, ni une preuve d'édition numérique, de compte,
  de provenance ou de possession. L'API privée `descriptive_files()` est
  distincte de `candidate_files()` ; aucune de ces APIs n'autorise une
  écriture. L'index est limité à 10 000 fichiers, 50 par clé, 20 par droit.
- **Un identifiant partagé par plusieurs fichiers** : chaque EPUB
  concerné est marqué **`ambiguous`**, même lorsqu'une seule notice
  correspond. Les ambiguïtés sont dédoublonnées dans les compteurs
  expurgés ; ni une notice→N EPUB ni N notices→un EPUB ne donne une
  attribution 1:1. Un budget interrompu reste explicitement incomplet.

Sans piste exacte **ni** descriptive, le navigateur manuel de tous les
fichiers non liés reste immédiatement accessible, paginé et filtrable.
Un inventaire partiel peut produire des pistes pour les fichiers déjà
observés, jamais une preuve négative concernant les fichiers non parcourus.
Toutes les pistes restent privées, bornées, révocables et non probantes,
sans publication de titres/comptes dans le rapport expurgé et sans
modification de fichier, licence, droit, artefact ou SQLite.

Recette synthétique : 6 EPUB Numilog, 3 chaînes de droits complètes,
3 droits sans lien confirmé, 2 fichiers partageant le même ISBN et
deux droits associés chacun à une notice de ce même ISBN. Les candidats
restent affichés pour **chacun** sans prouver aucun rattachement.


## Causes précises sans fausse provenance — P0 #2657

La fiche privée de chaque **droit sans fichier vérifié** distingue :

- une acquisition complète **du même compte, connexion, identité,
  fournisseur et édition** d'une ligne du ledger portant la même
  référence de droit mais issue d'un autre périmètre ; cette dernière
  est signalée comme **preuve contradictoire** sans divulguer ses champs
  privés et ne compte jamais comme une acquisition probante ;
- l'artefact de l'acquisition et l'artefact d'un placement explicite :
  une divergence est nommée au lieu d'un message générique ;
- un chemin SQLite **absent** au contrôle sécurisé, **inaccessible**,
  **hors racines autorisées**, ou **présent à un contrôle ponctuel**
  mais non inventorié avec SHA. Un chemin présent ne certifie ni
  son contenu EPUB ni un droit. Un chemin absent ne certifie pas
  l'absence du livre à un autre emplacement ;
- un inventaire **incomplet** avec ses causes de budget/accès/parser,
  qui ne doit jamais être interprété comme un achat non téléchargé.

Dans Maintenance, le périmètre est désormais décrit comme **racines
configurées**, et non comme « racines inspectées » sans preuve. Le
nombre d'exclusions est visible en permanence ; la liste des chemins
reste dans un panneau privé de la fenêtre de consultation, révoqué
avec la session. Ces éléments ne sont pas exposés au cache disque,
au broker, aux logs, aux plugins ou aux diagnostics.

Les fixtures de non-régression couvrent chemin hors périmètre,
ENOENT sécurisé, lien symbolique, fichier physiquement présent mais
hors formats scannés, collision d'identifiant de droit entre comptes,
et acquisition/placement divergents, sans lecture réseau ni mutation.


## Explorer manuellement les fichiers sans lien — P0 #2657

Si aucune notice ne partage l'ISBN ou un autre identifiant explicite du
livre, la **comparaison privée à deux colonnes** ne s'arrête pas à
« 0 candidat ». Ouvrir le droit non vérifié, puis sélectionner
**« Comparer tous les fichiers non liés »** : la seconde colonne permet de
parcourir, **un fichier à la fois**, les fichiers inventoriés **sans lien de droit confirmé**
**de l'identité active seulement**, y compris ceux dont le SHA n'a pas pu
être vérifié (état « Fichier non vérifié » conservé). Le champ de recherche filtre
le libellé affiché (titre/auteurs OPF, nom de fichier), jamais les preuves
cachées ; navigation et nombre total restent disponibles même au-delà de
50 fichiers. « Voir les candidats exacts » restaure la sélection
bibliographique initiale, sans modifier les sources ni l'audit.

**Distinction impérative :** en mode exploration, tous les fichiers sont
de simples fichiers locaux **sans lien confirmé**. Une empreinte SHA
indisponible ne démontre ni la présence exploitable du fichier ni son
absence : l'état reste explicitement « non vérifié ». Leur présence à côté d'un droit
Numilog ne prouve **ni** qu'ils proviennent de Numilog, **ni** qu'ils sont
achetés, attribuables ou manquants. Il n'existe aucune sélection
automatique d'une « bonne contrepartie » parmi les fichiers. Le compteur
de fichiers n'est pas soustrait du compteur de droits et les cas
multicomptes ne sont jamais mélangés. Les recherches, détails et résultats
n'existent que dans la session locale révocable ; aucun scan, écriture,
téléchargement ou réparation supplémentaire n'est déclenché.

Recette : vérifier sur un jeu synthétique 6 EPUB/6 droits/3 liens attestés
que les trois fichiers restants demeurent consultables **sans ISBN commun**
ou notice complète, et que le droit sélectionné conserve son statut
« sans fichier vérifié ». Vérifier recherche, navigation, changement
d'identité et absence d'accès aux fichiers d'une autre identité.


## Fenêtre d'inspection redimensionnable / agrandissable — #2657

La consultation des cas locaux (fournisseur, état de preuve, recherche,
droits non vérifiés et fichiers sans provenance attestée, inspection
**côte à côte** de candidats `metadata_only`) s'ouvre désormais dans
une `Adw.Window` explicitement décorée et redimensionnable avec
`Adw.ToolbarView` + `Adw.HeaderBar`.

- Taille initiale préservée : **920 × 620** ; le bureau reste maître
  de la taille effective pour ne pas dépasser les petits écrans.
- Commandes de titre affichées par libadwaita et bouton explicite
  **« Agrandir / Restaurer »** utilisable sans raccourci clavier, sans
  création d'une autre fenêtre et sans nouvelle analyse.
- Bouton « Fermer » existant conservé. Changement d'identité,
  fermeture et invalidation de la session révoquent toujours les
  informations locales privées ; le bouton n'altère que l'état
  d'affichage du même objet `Gtk.Window`.
- Validation Core par double bascule maximiser/restaurer, E2E
  GTK/XTEST de la barre, du bouton et de la persistance de la session
  juste avant sa révocation ; en Xvfb **sans gestionnaire de fenêtres**,
  la demande d'agrandissement peut ne pas modifier la géométrie.
  La dimension réellement obtenue reste à recetter avec le WM du poste
  utilisateur à 1280 et 1366 px.

Aucun changement des preuves, filtres, nombres, classification
Numilog/Kobo, recherche bibliographique, export public, téléchargement
ou autorisation de réparation.


## Résultats de réconciliation en fenêtre agrandissable — #2712

L'action « Résultats… » d'une ligne Maintenance dotée de l'inspecteur
privé de bibliothèque ouvre une **fenêtre autonome Adw.Window**,
décorée, redimensionnable et non modale (860 × 650 au démarrage,
géométrie gérée par le WM), avec :
`Adw.ToolbarView` + `Adw.HeaderBar`, bouton explicite
« Agrandir / Restaurer », corps à scroll vertical **étirable** et
boutons « Examiner les cas… » / « Fermer ». Les résultats incomplets
continuent d'afficher les motifs fermés avant les compteurs (#2724).
Le texte reste sélectionnable et aucune donnée `MaintenanceResult.facts`
n'est rendue.

Le bouton **« Examiner les cas… »** ferme cette fenêtre, puis
lance uniquement à ce moment la session privée existante ; aucun
nouvel audit ni fichier n'est lu en cliquant sur Agrandir/Restaurer.
Une fermeture par le bureau ou la touche de fermeture rend possible
une réouverture depuis le bouton « Résultats… ». Un changement
d'identité/refresh ou la disparition du parent révoque les deux vues.

Les autres outils Maintenance gardent leur `Adw.MessageDialog`
préexistant, de même que l'ancienne version de libadwaita sans
`Adw.ToolbarView`. Ce lot ne change aucun dialogue de confirmation,
réparation, acquisition, téléchargement ou connexion.

Le E2E GTK4/XTEST vérifie la fenêtre réellement rendue, son header,
les actions et le passage vers « Examiner les cas » avec une fixture
locale synthétique, sans compte réel. Xvfb sans gestionnaire de fenêtres
n'atteste pas la géométrie maximisée : vérification terrain nécessaire
à 1280/1366 px.


## Budget temporel pour la réconciliation interactive complète (#2565, 21/10/2026)

Le broker public `library.reconciliation.read.audit`, pour **l'identité active
explicitement sélectionnée**, dispose d'un budget **global de 600 secondes**.
L'acquisition initiale du snapshot SQLite/WAL, les vérifications de fraîcheur
et la préparation des checkpoints consomment **cette même enveloppe**. Une
fois le snapshot prêt, l'inventaire ne reçoit **pas** les 600 secondes
restantes en entier : le broker réserve jusqu'à **90 secondes** aux phases
suivantes (30 s de comparaison des droits, 30 s d'index bibliographique
et 30 s de rapprochement des fichiers). Si le budget restant est court,
la réserve est limitée à sa moitié pour laisser un scan utile. Exemple : avec
600 s restantes après le snapshot, plafond inventaire **510 s**, suivi des
phases de preuve dans la même échéance globale. Le temps non utilisé par
l'inventaire peut servir aux phases suivantes ; la réserve ne garantit pas
que chaque phase bénéficiera de tout son plafond en cas d'expiration globale.

Les valeurs génériques des APIs `audit_library` (180 secondes) et
`scan_identity_roots` (120 secondes) ne changent **pas**. La CLI ne reçoit
pas implicitement cette réservation. Les plafonds d'octets, d'entrées,
de fichiers, la validation individuelle EPUB/CRC, les lectures nofollow et
le snapshot SQLite/WAL restent indépendants et bornés : aucune phase ne
certifie la complétude ni l'absence d'un livre qui n'a pas été inspecté.
Un inventaire interrompu conserve le verdict `incomplete_scan` même si
la comparaison des seules observations positives va jusqu'au bout.
L'annulation, le changement de compte/identité/racines et les erreurs
de lecture restent des causes explicites de refus ; aucun téléchargement
ni réparation n'est effectué.
