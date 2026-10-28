# CIFERECA — architecture et rationalisation

**Contrat canonique et parcours nominal : [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — décision du propriétaire [#3171](https://github.com/pzia/cifereca/issues/3171) du 28 octobre 2026.**

Une **synchronisation automatique unique** observe achats/prêts exacts (A, SourceReference, identité/compte/branche), vérifie une copie locale par l'origine fournisseur attestée et son SHA final/Placement **dans le profil** (B), puis **acquiert les absents non satisfaits** dans les limites de droits, offres et consentements autorisés. Un titre/ISBN similaire ne suffit pas à rapprocher ; un scan incomplet n'est ni une absence prouvée ni un veto permanent. Après ACSM/ADEPT éventuel, publication transactionnelle Artifact global SHA, Placement propre au profil et OriginLink contextuels. Les notices bibliographiques (C) demeurent descriptives. Le rapprochement manuel #2797 est un rattrapage **exceptionnel**, pas une seconde boucle obligatoire.

**État réel (28/10)** : les observations en lecture seule des fournisseurs Kobo/Numilog/Paris/BnF sont disponibles ; le moteur transactionnel convergent **n'est pas encore livré**. Les PR #3523 (plan pur) et #3528 (lecture scopée) sont des tranches ouvertes, non une publication d'OriginLink SQLite. La migration de backup_items relève de #3196 ; les flux historiques restent actifs et protégés jusqu'à équivalence vérifiée.

Repères du dépôt :
- Core : src/cifereca/ ; API des plugins : src/cifereca/plugin_api/ ; CoreUI : src/cifereca/gui/ et builtin_ui/.
- **Plugins fonctionnels : src/plugins/<plugin>/** ; src/cifereca/plugins/ est le framework/loader/manager, et plugins/ à la racine n'est pas une source active.
- Paris : src/plugins/provider-paris/ ; src/cifereca/paris/ **n'existe pas** dans le tree actuel. src/cifereca/kobo/ garde un reliquat historique.
- Tests : [docs/TEST-ORGANIZATION.md](docs/TEST-ORGANIZATION.md) ; simulation fonctionnelle **déjà existante** : simulation/ ; environnements/ pour les runtimes.
- Rationalisation et statuts : [docs/RATIONALIZATION-STATUS.md](docs/RATIONALIZATION-STATUS.md).

> Les sections de versions ci-dessous sont **historiques**, ne priment pas sur le contrat #3171 et n'attestent pas les fonctionnalités non fusionnées.

---

# Core 0.9.33+dev.1

Détails partagés et denses, actions et liens catalogue/local, refresh des prêts ciblé, rattachement ADEPT en cas de contradiction, navigation filtrée Accueil032 et barres adaptatives. Voir [RELEASE-NOTES-0.9.33-dev.1.md](docs/RELEASE-NOTES-0.9.33-dev.1.md) et le [contrat plugins](docs/CONTRAT-PLUGINS-CIFERECA-0.9.33.md). Les recettes GTK et Paris/ADEPT authentifiées restent à valider.

# Core 0.9.23+dev.1

Focus limité aux pages rattachées à une fenêtre GTK, identités actualisées après sauvegarde et rattachement ADEPT manuel par sélection visuelle. Voir [RELEASE-NOTES-0.9.23-dev.1.md](docs/RELEASE-NOTES-0.9.23-dev.1.md).

# Core 0.9.21+dev.1

Inventaire public des prêts ADEPT par profil lié à l’identité, vérification sans téléchargement et protection des acquisitions Kobo face aux livres importés sans liaison. Voir [RELEASE-NOTES-0.9.21-dev.1.md](docs/RELEASE-NOTES-0.9.21-dev.1.md).

# CIFERECA Core 0.9.17+dev.1

Vague Core après Plugins 0.9.16-dev.1. Les packages externes, leurs versions, configuration et activation sont conservés.

- Recherche : deux validations Entrée consécutives sans interaction déclenchent successivement le connu puis En ligne. Même requête en ligne en cours dédupliquée ; autre touche/clic/défilement/critère réinitialise la séquence.
- Liste/détail : source et formats filtrables cliquables, recherche connue conservant les autres critères ; auteur textuel cliquable sans répétition en bouton.
- Formats : alternatives explicitement annoncées filtrables ; fichier local prioritaire. Backup : conteneur final inspecté, protection détectable contrôlée, taille/SHA-256 recalculés, provenance annoncé/candidat/préféré/observé sur Artifact ; invalides non complets. Fichiers existants inspectés sans téléchargement.
- HTTP : protection/handling explicites conservés, refus avant fetch ; contrôle du transport respecté.
- ADEPT : recettes v2, capture de loan_id publié et profil d’origine avec Artifact/provenance sur import/prêts Core ; retour séparé et confirmé avec profil d’origine, fichier conservé. Sans loan_id ou profil d’origine exploitable, retour non proposé. Anciennes matérialisations sans binding non inventées.
- Inventaire : version UI intégrée exacte.

Installer : `cifereca-update-dev cifereca-dev-0029-core-0917.zip`. Le nom seul est recherché dans le dossier courant puis Téléchargements. Option --dry-run disponible. L’archive source complète sert à reprendre le développement.

Tests : 490 source + 490 installés, 120 tests Plugins source + 120 sur les plugins installés avec ce Core ; validation isolée de mise à jour, configuration, activation et versions conservées. Voir INSTALL-E2E-0.9.17-dev.1.json. Recette GTK/clavier, services authentifiés et retour réel non effectués. Inspection de conteneur ne remplace pas validation complète EPUB, lecture PDF ni vérification DRM exhaustive.

Reste au backlog : redirections ACS4, cartes Accueil et vignettes Gutenberg (prochaine vague Plugins), provisioning nominal et qualification des schémas, liaison ACSM Kobo, modèle d’offres/formats et brokers UI/logs. Aucun passage RC annoncé.

---

# CIFERECA — Plugins 0.9.16-dev.1 sur Core 0.9.15+dev.1

Nouvelle vague Plugins : Accueil externe, formats Kobo sans EPUB inventé, avertissements Paris, téléchargements audio Gallica bornés et publiés sans écrasement, limites/erreurs Gutenberg, dépendance catalogue réelle, contrôles DRM/HTTP/stockage et aide.

```sh
cifereca-update-dev cifereca-dev-0028-plugins-0916.zip --dry-run
cifereca-update-dev cifereca-dev-0028-plugins-0916.zip
```

120 tests source et 120 installés, 456 tests Core. Installation, conservation des paramètres/activations et restauration après échec simulé vérifiées. GTK/services authentifiés restent à recetter. Prochaine vague Core : formats multiples, qualification finale du backup, profil/retour ADEPT et dispatch HTTP ; détails BACKLOG.md.

La suite ci-dessous documente le Core préalable, conservé par la livraison Plugins.

# Core 0.9.15+dev.1 — fusion de dossiers et mise en page

La vague Core suit Plugins 0.9.14-dev.1. Paris 1.2.2, runtime Kobo-Adept 1.2.1 et les autres plugins externes gardent leurs versions, activations et réglages.

Application fermée :

```sh
cifereca-update-dev cifereca-dev-0027-core-0915.zip --dry-run
cifereca-update-dev cifereca-dev-0027-core-0915.zip
```

Le nom seul est recherché dans le dossier courant puis Téléchargements XDG. Le ZIP dev est à installer. `cifereca-source-0.9.15-dev.1.zip` contient toute l’arborescence du projet sans filtrage : code, tests, scripts, docs, dépendances offline, intégrations. BACKLOG.md figure dans chaque nouveau ZIP.

## Destination déjà existante

Un cas moteur a été reproduit : un dossier existant non déclaré, déjà scanné, recevait une identité déduite de son nom. La migration le refusait ensuite comme dossier d’une autre identité. Le scanner ne crée plus ce propriétaire fictif. Le plan reconnaît les anciens indices exacts de nom de dossier seulement si aucun propriétaire déclaré, acquisition ou droit n’en atteste la propriété. Les fichiers réellement liés à une autre identité restent protégés. Les collisions gardent les deux contenus ; les chemins de base/configuration sont actualisés et la bibliothèque rescannée après migration. Les protections des liens symboliques, journal, reprise et rollback sont conservées.

Dans Paramètres → Identités et comptes, le champ de répertoire propose désormais le choix d’un dossier existant. Seul un enfant direct de la racine de bibliothèque est accepté. Une saisie du chemin absolu de cet enfant est aussi acceptée ; le nom et sa casse sont conservés. Choisir ou annuler ne déplace rien : le déplacement/fusion a lieu lors de l’enregistrement. Un nom simple peut toujours désigner une destination nouvelle. Racine de bibliothèque et sous-répertoire d’identité doivent être modifiés en opérations distinctes.

Ce cas reproduit n’est pas une preuve de la cause exacte du signalement utilisateur, qui n’incluait pas de message d’erreur. La recette du sélecteur et du parcours GTK sur le poste reste nécessaire.

## Pages et détails

Les actions de Bibliothèque sont en icônes sur la ligne du titre, après le nombre d’éléments : rescan, import ACSM, synchronisation Kobo et ouverture du dossier. Les filtres gardent leur ligne propre. Actualiser Récents est dans l’en-tête ; Emprunts, Historique, Réservations, Paramètres et État du système appliquent aussi la règle. Les actions contextuelles des livres, cards ou réglages restent avec leur contenu.

Notice utilise une grille de deux colonnes qui revient à une colonne lorsque la largeur est insuffisante. Les valeurs longues sont enveloppées. Accueil intégré présente les identités en cards dans une grille adaptative de deux colonnes. Le nom affiché est un bouton : il sélectionne l’identité exacte, vide les listes/caches de l’identité précédente et ouvre sa bibliothèque. Les réponses de chargement d’une identité devenue inactive ne sont pas appliquées à l’IHM. Les capacités sont présentées avec des libellés lisibles, uniquement depuis les sessions connectées autorisées pour cette identité. Les sources à connecter restent distinguées. Une capacité provider ne certifie pas la disponibilité d’un traitement DRM externe.

## Contrat pour le plugin Accueil

`UiIdentityHost` est une extension publique additionnelle de Plugin API v1 : projection locale détachée `identity_overview`, sélection/navigation `open_identity_library`, action `synchronize`, abonnement `subscribe_identity_overview` avec désabonnement idempotent. Le protocole minimal `UiPageHost` reste inchangé. La lecture ne déclenche pas de réseau et ne livre aucun credential/secret. Voir UI-PLUGIN-CONTRACT.md.

Cette vague met à jour l’UI intégrée et rend son extraction possible. Le package Accueil externe n’est pas encore livré : HOME-P05 est prévu dans la prochaine vague Plugins, sans accès host.window ou aux services internes.

## Validation

456 tests source et 456 installés passent, sans test ignoré, dont 28 nouveaux. Ils couvrent dossiers réellement sur disque/SQLite après rescan, anciennes inférences, preuves et propriétaires protégés, collisions, sélection/saisie/configuration, annulation, Notice/cards/en-têtes, sélection précise et compatibilité du protocole. Les handlers GTK sont exercés avec doubles : GI/GTK/libadwaita ne sont pas disponibles ici. Pas de capture visuelle ni de certification bureau annoncée.

Upgrade depuis Core 0.9.13, Paris 1.2.2/runtime 1.2.1 et autres versions/activations/configurations conservés ; recherche du nom dans Téléchargements XDG et dry-run vérifiés. La vérification installée utilise l’exécutable immuable de la release ; le lanceur stable n’est pas certifié dans cet environnement. Voir INSTALL-E2E-0.9.15-dev.1.json. Aucun nouveau prêt ni téléchargement authentifié.

Prochaine vague Plugins : extraction Accueil avec le contrat livré, qualification des formats Paris et autres sujets encore ouverts. Restitution ADEPT, modèle complet des offres/formats et sync restent au backlog. Vivlio reste abandonné ; Sapiens 4 reste confirmé par l’utilisateur.


---

## Livraisons précédentes

# Core 0.9.13+dev.1 — profil ADE et reprise ACSM

Cette vague Core suit Plugins 0.9.12-dev.1. Les plugins externes, leurs versions, activations et réglages sont conservés ; notamment le runtime Kobo-Adept 1.2.1 installé par la vague précédente. La réussite de Sapiens 4 signalée par l’utilisateur est conservée comme recette de ce titre. Aucun binaire Kobo-Adept n’est livré ici.

Fermer l’application, puis :

```sh
cifereca-update-dev cifereca-dev-0025-core-0913.zip --dry-run
cifereca-update-dev cifereca-dev-0025-core-0913.zip
```

Le nom seul est recherché dans le dossier courant, puis dans Téléchargements XDG. L’archive dev est celle à installer. Le ZIP simple `cifereca-source-0.9.13-dev.1.zip` contient toute l’arborescence du projet, code/tests/docs/scripts/intégrations/dépendances offline, sans filtrage. BACKLOG.md figure dans chaque nouveau ZIP.

## Choisir le profil Adobe d’une identité

Dans Paramètres → Identités et comptes, le champ Profil ADE propose **Choisir…**. La liste est chargée en tâche de fond et affiche uniquement les profils capables de récupérer un ACSM ; un signal explicite de provisioning non prêt les exclut. Les profils sans indication de capacité ne sont pas supposés prêts. Aucun profil n’est sélectionné par défaut, même si la liste contient un seul élément. Choisir le profil correspondant à l’identité Adobe du prêt, enregistrer les paramètres, puis relancer la synchronisation.

Le choix renseigne la référence stable du profil dans le formulaire ; elle est persistée par l’enregistrement commun. Le nom CLI est résolu à chaque exécution, ce qui tolère le renommage d’un profil externe. Annuler ne change rien. Une erreur de chargement ou une liste vide est expliquée ; elle n’écrase pas la liaison existante. Le détail des erreurs/actions de synchronisation donne un accès direct à l’onglet Identités et comptes.

## Réutiliser l’intermédiaire d’un prêt

Après récupération et hook réussi, un ACSM XML reconnu peut être réutilisé pendant au maximum **24 heures**. Le contrôle porte sur identité, provider, credential, emprunteur exact, prêt, notice, échéance, empreinte de l’URL source et empreinte du fichier. L’échéance du prêt et toute expiration reconnue dans l’ACSM priment sur cette durée. Fichier absent, modifié, XML incorrect, DTD, symlink, changement de provenance ou date invalide provoquent une nouvelle récupération fournisseur. Les anciens ACSM sans manifeste ne sont pas automatiquement réutilisés.

Le manifeste contient des références et empreintes, sans URL signée/token ni contenu ACSM. Il est écrit atomiquement ; son écriture est facultative pour la réussite de l’acquisition. La réutilisation n’exécute pas à nouveau le hook déjà réussi. Après succès ADEPT validé, ACSM et manifeste correspondant sont supprimés. Après échec, l’intermédiaire est gardé ; cela ne transforme jamais l’erreur en succès.

Avec plusieurs profils non liés, deux synchronisations téléchargent une seule fois l’ACSM et ne lancent aucun téléchargement ADEPT. Après liaison du profil, une synchronisation reprend ce même intermédiaire s’il respecte encore les contrôles. Une nouvelle tentative ADEPT reste possible après échec du runtime : le Core ne masque pas l’action derrière un succès supposé. Le bouton manuel **Récupérer le fichier du prêt** force une récupération fournisseur fraîche. Il prépare le fichier/intermédiaire du prêt existant ; il ne crée pas de nouvel emprunt et ne déclenche pas à lui seul le traitement ADEPT.

Le cache ne certifie pas l’autorisation Adobe ou l’état distant du prêt : ils restent validés par les services réels lors du traitement. Un prêt retourné/expiré connu n’est pas réutilisé. L’API interne `acquire_digital_loan` / `prepare_digital_download` accepte aussi `force_refresh=True`.

## Validation et suite

428 tests source et installés passent, sans tests ignorés, dont 26 nouveaux cas. Ils couvrent les scopes/fraîcheur/XML/expiration/hashes/symlinks, hooks, nettoyage, deux synchronisations réelles du service avec transports simulés, résolution explicite puis acquisition, échec runtime, choix stable/annulation/chargement vide et accès à l’onglet. Le cycle installé valide l’upgrade depuis Core 0.9.11, la conservation du runtime 1.2.1 et des autres packages, de l’activation et de la configuration, ainsi que la recherche du ZIP dans Téléchargements XDG et le dry-run.

GI/GTK/libadwaita sont indisponibles ici : les handlers sont testés avec doubles ; la recette graphique et un téléchargement authentifié restent à faire sur le poste. Les vérifications installées utilisent l’exécutable immuable de la release ; le lanceur stable n’est pas certifié dans cet environnement. Voir INSTALL-E2E-0.9.13-dev.1.json.

Le binding de restitution ADEPT, le modèle complet des formats, la régression Paris EPUB distant, l’extraction des pages UI, le plugin sync et la qualification Vivlio restent ouverts. Prochaine vague : Plugins, périmètre à qualifier dans le release plan. Aucun passage RC n’est annoncé avant validation des critères restants.


---

## Livraisons précédentes

# Core 0.9.11+dev.1 — diagnostic et actions de prêts

La vague Core suit Plugins 0.9.10-dev.1. Les plugins externes restent installés à leur version actuelle, dont Paris 1.2.1 ; cette livraison ne met pas à jour Kobo-Adept. La correction UTF signalée par l’utilisateur est faite côté Kobo-Adept et n’est pas incluse ni certifiée par le Core.

Fermer l’application, puis :

```sh
cifereca-update-dev cifereca-dev-0023-core-0911.zip --dry-run
cifereca-update-dev cifereca-dev-0023-core-0911.zip
```

Le nom seul est recherché dans le dossier courant puis Téléchargements XDG. Relancer ensuite l’application et la synchronisation. Le ZIP à installer est le snapshot dev ; le ZIP `cifereca-source-0.9.11-dev.1.zip` contient toute l’arborescence du projet avec code, tests, scripts, docs, intégrations et dépendances offline. BACKLOG.md est présent dans chaque nouveau ZIP.

## Changements

Paramètres → Diagnostic : la modification du niveau ou de l’activateur est immédiatement enregistrée et appliquée. Elle ne sauvegarde pas les autres champs de la page. Le chargement suivant reprend ce choix. En cas d’échec d’écriture, aucun changement actif n’est publié. Les overrides diagnostiques temporaires du runtime ne remplacent pas la préférence enregistrée.

En cas d’erreur d’acquisition Kobo-Adept, le Core récupère immédiatement les statistiques sûres disponibles : code de retour, durée, tailles stdout/stderr et présence/taille du fichier cible. Elles apparaissent dans le diagnostic, le message de l’action/erreur de synchronisation et le ledger. Le code de retour de l’exception prime sur une statistique contradictoire. L’événement reste visible au niveau errors, et le journal d’échec n’exige plus le niveau trace. Aucune sortie brute, requête stdin ou clé n’est copiée. Le contenu stdout/stderr ne peut pas être reconstitué : sa capture sûre relève du runtime (backlog ADE-P01). Une cible présente après une erreur n’est pas annoncée comme acquisition réussie.

L’origine d’une erreur structurée reste celle du stage : provider ou kobo-adept. Un refus de redirection Paris ne sera plus attribué à drm.ade.

Le bouton d’un prêt numérique affiche « Récupérer le fichier du prêt » dans Emprunts et Bibliothèque. Il nécessite une source connectée avec capacité digital_download. L’aide précise qu’un ACSM reste à traiter par Kobo-Adept et que l’action ne crée pas un nouvel emprunt. Le comportement reste la récupération du fichier/intermédiaire du prêt existant.

Les liens auteurs transmettent un nom sans virgule ni rôle Auteur, et conservent la recherche dans le champ Auteur. Accents, apostrophes et traits d’union sont conservés ; séries et collections ne passent pas par cette normalisation.

## Validation et limites

402 tests source passent, dont 14 nouveaux cas : persistance après réouverture, niveaux, override, échec disque, sauvegarde immédiate via handler UI, normalisation/recherche auteur, capacités de source, attribution provider et statistiques de processus avec/sans fichier. Aucun test n’est ignoré. Les contrôles GTK sont exercés avec doubles/handlers extraits ; GI/GTK/libadwaita indisponibles ici. Les tests réseau utilisent des transports simulés.

402 tests passent aussi après installation. Upgrade depuis Core 0.9.9+dev.2, versions/activations des plugins (Paris 1.2.1), paramètres et profil ADE conservés ; logging relu après réouverture du Core installé. Le cycle est documenté dans INSTALL-E2E-0.9.11-dev.1.json. Les vérifications post-upgrade utilisent l’exécutable immuable de la release : le lanceur stable n’est pas certifié dans cet environnement. Une reprise Sapiens 4 réelle, la correction UTF externe et le bureau GTK doivent être vérifiés sur le poste utilisateur. Le Core ne fait pas de nouveau prêt pour ce test.

Les chantiers profil ADE explicite/reprise ACSM, restitution ADEPT, modèle complet des formats, Paris EPUB distant, sync/UI et qualification Vivlio restent au backlog. La migration des dossiers d’identité livrée en 0.9.9+dev.2 est conservée.


---

## Documentation de la livraison précédente

# Core 0.9.9+dev.2 — changement de répertoire d’identité

Cette itération poursuit la vague Core 0.9.9. Aucun package provider n’est mis à jour.
Les demandes PNB/auteur reçues pendant cette itération sont au backlog de la
prochaine version ; l’analyse Vivlio y est conservée.

```sh
cifereca-update-dev cifereca-dev-0021-core-099.zip --dry-run
cifereca-update-dev cifereca-dev-0021-core-099.zip
```

Fermer l’application pour installer, puis la relancer. Le nom seul est recherché
dans le dossier courant puis Téléchargements XDG. L’archive simple
`cifereca-source-0.9.9-dev.2.zip` accompagne le Core et contient l’arborescence
complète : code, tests, scripts, docs, dépendances offline et intégration Plugins.
BACKLOG.md est présent dans chacun des nouveaux ZIP.

## Utilisation

Dans Paramètres → Identités et comptes, modifier le champ de répertoire de
l’identité, puis Enregistrer. Exemple : `ma-bibliotheque` → `hugues` déplace les
fichiers depuis `~/EBooks/ma-bibliotheque` vers `~/EBooks/hugues`, pour la racine
bibliothèque effectivement configurée. Changer seulement le libellé affiché ne
déplace rien ; l’identifiant technique reste stable.

L’enregistrement se fait en tâche de fond. Enregistrer et reconnecter attend
le succès de l’enregistrement avant de reconnecter. Une migration est refusée
si une autre tâche UI est active ; attendre sa fin. Le verrou des acquisitions
locales sérialise migration, synchronisation d’ouverture et backup Kobo entre
instances. Une acquisition est refusée tant que le journal attend sa reprise.

La destination peut déjà exister : fusion récursive, pas d’écrasement. Un
fichier identique réutilise la destination ; un fichier différent reçoit un
suffixe `-migration-<empreinte>` (numéroté si nécessaire). Les dossiers contenant
un manifeste de bundle ou une playlist M3U/M3U8 sont copiés comme unités, afin
de conserver leurs références internes. Les collisions sont comptées dans le
résultat ; bibliothèque et récupérations récentes sont actualisées.

Les placements, chemins de backup et chemins structurés dans les métadonnées
du catalogue/ledger/Artifacts/droits sont mis à jour dans une seule transaction
SQLite. Les IDs métier, identités et Artifacts sont conservés. Le rescan est
automatique après migration ; il réutilise les placements existants par chemin
et conserve titre/provenance de l’Artifact inchangé.

## CLI et reprise

```sh
cifereca-cli identity-directory default hugues --dry-run
cifereca-cli identity-directory default hugues
```

Remplacer `default` par l’identifiant technique de l’identité. Le dry-run
vérifie et décrit le plan sans déplacer les fichiers ni publier de configuration.

La migration copie et vérifie les données avant de valider chemins et journal
en base, sauvegarde la configuration, puis supprime seulement les sources qui
correspondent à la destination vérifiée. Il faut donc assez d’espace pour une
copie temporaire des fichiers à déplacer. Les sources restent présentes en cas
d’échec de copie ou de validation. Une interruption laisse un journal ; la reprise
se fait automatiquement au lancement, avant connexion des sources, et le journal
est effacé uniquement après réussite du rescan.

Avant validation en base, une migration suspendue peut être annulée sans
supprimer les originaux :

```sh
cifereca-cli identity-directory --abort
```

Les copies créées par cette migration ne sont retirées que si l’original et la
copie ont encore le contenu attendu. Une destination préexistante n’est pas
supprimée. Après validation en base, on reprend vers la destination ; `--abort`
refuse de restaurer aveuglément des références déjà validées.

Si un utilisateur modifie un fichier ou la configuration pendant une migration,
elle s’arrête avec une erreur et conserve les données restantes. Ne pas supprimer
manuellement l’unique copie d’un fichier pour contourner cette erreur. Reprise
après interruption du processus éprouvée aux frontières de copie, DB, config,
nettoyage partiel et rescan ; perte matérielle du disque non certifiée.

## Limites et validation

Les liens symboliques/entrées non régulières sont refusés. Une destination
attribuée à une autre identité est refusée, même via un placement déjà indexé.
Les échanges entre deux identités nécessitent une étape intermédiaire. Le
changement de la racine entière de bibliothèque n’est pas une migration prise
en charge par cette fonction ; ne pas le combiner au changement de sous-dossier.
Une édition manuelle du JSON sans journal ne déclenche pas cette migration :
utiliser Paramètres ou la CLI.

388 tests Core : fichiers, SQLite, configuration et contrats existants ; handlers
GTK exécutés avec doubles de widgets pour vérifier staging/tâche de fond/reconnexion.
La recette bureau réelle reste nécessaire : GI/GTK/libadwaita non disponibles ici.
La validation d’installation applique le snapshot via l’API de l’updater, puis
exécute la CLI de la release immuable installée. L’activation par le lien
`current` et la relance via le lanceur standard ne sont pas certifiées par cet
E2E dans cet environnement ; elles restent à recetter sur le bureau.

Les tests n’attestent aucun nouveau parcours authentifié de provider.


---

## Historique

# CIFERECA — Plugins 0.9.8-dev.2 et Core 0.9.9+dev.1

Installer d’abord la vague Plugins, puis la vague Core :

```sh
cifereca-update-dev cifereca-dev-0019-plugins-098.zip --dry-run
cifereca-update-dev cifereca-dev-0019-plugins-098.zip
cifereca-update-dev cifereca-dev-0020-core-099.zip --dry-run
cifereca-update-dev cifereca-dev-0020-core-099.zip
```

Le nom seul fonctionne depuis 0.9.7 : dossier courant puis Téléchargements XDG.
Fermer l’application avant la mise à jour et la relancer ensuite.
L’IHM Plugins importe les ZIP de packages présents dans `packages/` du snapshot
Plugins. Les snapshots dev complets s’installent avec `cifereca-update-dev`.

## Changements

Gallica 1.2.1 reprend la version fournie (1.2.0), ses EPUB OPDS et son audio
SRU/OAI, puis corrige le lancement de branches inutiles et le cumul séquentiel
des délais. Filtre EPUB : seule branche ebook ; MP3 et autres formats audio :
seule branche audio ; sans filtre : branches concurrentes. Une panne laisse les
résultats de l’autre branche visibles et un avertissement. BnF Catalogue général
1.0.0 est intégré ; ses notices ne prouvent ni fichier numérique ni prêt. Aucun
squelette BnF prêt supprimé n’est réintroduit. Les autres packages sont conservés.

Le Core conserve le résumé après chargement des exemplaires. Recherche et
Bibliothèque commencent sur Numériques à chaque lancement ; le choix changeable
reste en session, y compris lors du rafraîchissement des pages de plugins.

La sélection ZIP utilise Gtk.FileDialog à partir de GTK 4.10. Sur GTK antérieur,
la fenêtre conserve le sélecteur natif et le détruit après retour de son handler.
L’annulation ne lance pas d’installation ; la sélection doit être un ZIP local
existant. Le parcours de confirmation/inspection reste commun.
Référence API : https://docs.gtk.org/gtk4/method.FileDialog.open_finish.html

Le compteur Erreurs/Actions de synchronisation propose un détail par livre,
avec code et cause masquée pour les secrets. Relancer une synchronisation pour
obtenir ce détail : les anciennes lignes ne peuvent pas reconstituer une cause
absente. Une panne de recherche est distincte d’une acquisition en erreur.

Les pannes réseau, authentification, service et XML arrêtent les requêtes plus
lâches ; échecs et réponses partielles ne sont pas conservés six heures. Le
cache de recherche change de namespace pour ne pas reprendre les anciens vides.

## Validation et limites

357 tests Core et 41 tests Plugins ; transport simulé et vrais cycles de
packages/installation en environnement isolé. Les méthodes GTK sont exercées
avec doubles de widgets ; GI/GTK/libadwaita indisponibles ici. Ne pas considérer
le crash thaw_updates comme certifié corrigé sur le bureau à partir de ces seuls
tests : recette import/annuler/réouvrir/installer et redémarrage nécessaire.
Aucune acquisition authentifiée, panne Gallica publique ni retour ADEPT réel
n’est certifié par cette livraison. Les quatre erreurs historiques ne sont pas
attribuées arbitrairement aux quatre ACSM ; le détail rend la prochaine exécution
analysable.

BACKLOG.md dans chaque ZIP. Le ZIP simple `cifereca-source-0.9.9-dev.1.zip`
contient toute l’arborescence Core et l’intégration Plugins sous integrations/.
Restent ouverts Paris EPUB distant, modèle complet des formats, restitution
ADEPT, migration/fusion/rescan et plugins sync/pages. Plan actualisé dans
`docs/RELEASE-PLAN-CIFERECA-PREVISIONNEL.md`.


---

## Historique

# Core 0.9.7+dev.5 — clavier et menu

Menu natif des pages à l’extrémité droite de la barre de titre. Raccourcis Ctrl+Q, Ctrl+W, F10/Ctrl+M, Ctrl+F, Ctrl+virgule, Ctrl+Page précédente/suivante et F1 ; aide accessible dans le menu. Le squelette BnF et ses entrées de build/packages/tests spécifiques ont été supprimés ; Gallica est conservé. Deux références de logger indéfini lors des notifications d’identité/compte ont aussi été corrigées.

```sh
cifereca-update-dev cifereca-dev-0018-core-ui-097.zip --dry-run
cifereca-update-dev cifereca-dev-0018-core-ui-097.zip
```

Le ZIP source cifereca-source-0.9.7-dev.5.zip accompagne le snapshot et reprend l’arborescence complète. BACKLOG.md est présent dans chacun. Les demandes d’évolution vont au backlog jusqu’à une demande de version, avec alternance Core/Plugins conservée ; ce lot poursuit la vague Core 0.9.7.

341 tests automatisés. Recette bureau GTK/clavier réelle restante ; les tests de shell exécutent les méthodes réelles avec une arborescence GTK/Gio simulée. Les providers externes de production gardent leurs versions. Restitution ADEPT, migration/fusion, formats proposés multiples et sync restent ouverts. Voir docs/CLAVIER-ET-MENU.md et le plan actualisé.

Les notes ci-dessous sont historiques ; le backlog et le plan actuel font autorité.

---

# Core 0.9.7+dev.4 — cards Plugins compactes

Chaque plugin est présenté dans une card compacte. Version et icône ? sont en haut à droite ; aide dans un popover. Le résumé utilise toute la largeur. Les commandes de configuration, diagnostic, rollback et désinstallation sont regroupées dans une seule ligne en bas à gauche ; l’activateur est à droite. Pour les catalogues publics, le menu Identités… de cette ligne donne accès aux activations par identité. Désinstaller et rollback concernent toujours le package complet, même lorsqu’il fournit plusieurs plugins.

```sh
cifereca-update-dev cifereca-dev-0017-core-ui-097.zip --dry-run
cifereca-update-dev cifereca-dev-0017-core-ui-097.zip
```

Le ZIP source cifereca-source-0.9.7-dev.4.zip reprend toute l’arborescence du projet. Les deux ZIP contiennent BACKLOG.md. 332 tests passent ; les nouvelles vérifications exercent les vrais constructeurs de cards avec une arborescence GTK simulée : placement des actions, version/aide, activateur, callbacks et menu identités. Le rendu et les interactions GTK réels restent à vérifier sur bureau ; aucun rendu graphique n’a été certifié ici.

Cette correction reste dans la vague Core 0.9.7 ; providers externes inchangés. Voir BACKLOG.md pour les sujets restant ouverts. Notes historiques ci-dessous.

---

# Core 0.9.7+dev.3

Correction du format de bibliothèque : l’Artifact/local prime sur les droits distants, KEPUB reste une variante et correspond au filtre EPUB. Aide help_text partagée entre Plugins et État du système, cards plus compactes, Paramètres en cinq onglets avec sauvegarde commune. L’action historique Kobo porte explicitement sur les comptes connectés compatibles.

Installation :

```sh
cifereca-update-dev cifereca-dev-0016-core-ui-097.zip --dry-run
cifereca-update-dev cifereca-dev-0016-core-ui-097.zip
```

Un nom seul est cherché dans le dossier courant puis Téléchargements XDG. Ancien launcher 0.9.5 : fournir un chemin complet. Le ZIP source cifereca-source-0.9.7-dev.3.zip est l’état complet du projet ; il sert à consulter/reproduire le travail, le ZIP dev est celui à installer.

328 tests automatisés, dont régressions format local/distant, KEPUB, aide et compatibilité des anciens manifests. Recette GTK/libadwaita non réalisable dans cet environnement : rendu, interactions des onglets et sauvegarde manuelle restent à valider sur bureau.

Voir BACKLOG.md et docs/RELEASE-PLAN-CIFERECA-PREVISIONNEL.md. Recherche EPUB Paris, restitution ADEPT, migration/rescan des répertoires, sync restent ouverts. Les notes ci-dessous décrivent les snapshots précédents.

---

# Itération Core 0.9.7+dev.2

Ce snapshot poursuit la vague Core 0.9.7 : rubriques Pages et Configuration / Technique repliables, sélecteur d’identité réservé aux délégations, Formats EPUB/PDF/audio et plugin intégré État du système. Les dates anciennes et celles d’un premier passage via un ancien updater sont inconnues ; les nouvelles installations et remplacements sont horodatés. Les dates des composants intégrés suivent l’installation du core.

Mise à jour depuis 0.9.7+dev.1 :

```sh
cifereca-update-dev cifereca-dev-0015-core-ui-097.zip --dry-run
cifereca-update-dev cifereca-dev-0015-core-ui-097.zip
```

Le nom seul est recherché dans le dossier courant, puis Téléchargements XDG. Depuis un ancien launcher 0.9.5, fournir un chemin complet. Ce snapshot met à jour le core et son UI intégrée ; les packages providers restent ceux de la vague Plugins 0.9.6.

Validation : 318 tests automatisés. Les modèles, l’installation, les dates et les états sont testés sans GTK. Le rendu et les interactions GTK/libadwaita restent à valider sur bureau. Restitution ADEPT et recherche EPUB Paris : voir le plan actualisé dans docs/RELEASE-PLAN-CIFERECA-PREVISIONNEL.md.

---

# cifereca 0.9.7+dev.1 — Core / helpers d’installation

Cette première livraison de la vague 0.9.7 ajoute les helpers d’installation.
Le core poursuit 0.9.5+dev.2 ; SQLite reste en schéma v9 et les plugins ne sont
pas réinstallés par le snapshot core seul. Le ledger/restitution ADEPT et les
autres lots 0.9.7 restent prévus dans le plan joint.

## Premier passage depuis 0.9.5

Fermer cifereca. L’ancien updater exige encore un chemin complet :

```sh
cifereca-update-dev /chemin/vers/cifereca-dev-0013-core-install-097.zip --dry-run
cifereca-update-dev /chemin/vers/cifereca-dev-0013-core-install-097.zip
cifereca --version
```

Version attendue : `cifereca 0.9.7+dev.1`.

## Commande commune après cette mise à jour

```sh
cifereca-update-dev cifereca-dev-0014-plugins-096.zip --dry-run
cifereca-update-dev cifereca-dev-0014-plugins-096.zip
```

Sans chemin, le ZIP est recherché dans le dossier courant puis dans le dossier
Téléchargements configuré par le bureau dans XDG user-dirs.dirs. Les noms
localisés et espaces sont acceptés. Sans configuration XDG exploitable, le
fallback est ~/Downloads. Un chemin explicite ne déclenche pas de recherche
ailleurs. Une erreur présente les emplacements essayés.
`cifereca-update` utilise la même résolution pour les archives core.

Un snapshot dev peut contenir uniquement le core, uniquement des plugins,
ou les deux. Un snapshot vide est refusé. Les manifests, chemins, doublons et
empreintes fournies sont contrôlés avant modification. Le mode --dry-run ne
remplace aucun composant. Un snapshot de plugins seuls conserve les liens
current/previous du core, ne recrée pas son venv et ne réinstalle pas le core.
Les packages inclus sont sauvegardés puis restaurés si l’installation ou le
smoke échoue ; les packages absents restent intacts. Les versions de package
inactives restent inactives ; les désactivations et configurations plugin
utilisent les mécanismes existants du core.

Le snapshot 0014 contient les cinq ZIP 1.2.0 déjà livrés en 0.9.6, sans changer
leurs octets. Il apporte le parcours d’installation commun à cette livraison.
Fermer l’application avant les mises à jour reste recommandé. --restart réel
reste à certifier sur bureau équipé de GTK dans la suite de 0.9.7.

## Validation

307 tests passent sans échec ni test ignoré : 284 précédents et 23 tests des
helpers. Le runner hors ligne conserve les assertions historiques et adapte
le sous-ensemble pytest utilisé à unittest. Rapports dans docs/.
Les parcours authentifiés/DRM réels et la recette GTK ne sont pas revendiqués.

```sh
python3 scripts/run-tests.py
python3 scripts/run-tests.py --pytest  # si pytest est installé
python3 -m pip wheel --no-deps --no-build-isolation . -w dist
```

Le wheel et ses dépendances sont embarqués dans offline-wheels/ pour
l’installation sans index réseau. Les fixtures de plugins sous tests/fixtures/
et bundled_plugins/ ne sont pas déployées par le manifest du snapshot core.
Le plan prévisionnel est docs/RELEASE-PLAN-CIFERECA-PREVISIONNEL.md.

## Ce qui change

- Widget commun Filtre/Recherche fourni par `core.ui.widgets`, remplaçable par
  priorité dans la catégorie `ui.widgets`.
- Bibliothèque : projection de possession existante, filtre instantané local,
  multi-sources et multi-types, champs Série et Collection éditoriale.
- Recherche : bouton Recherche dans le catalogue SQLite connu, sans appel
  provider ; bouton En ligne explicite. Les facettes filtrent le jeu chargé.
  Les résultats distants successifs sont fusionnés et dédupliqués par source.
- Le bouton Recherche de Bibliothèque transfère les contrôles compatibles et
  lance uniquement la recherche du connu.
- Facettes Auteur/Série/Collection dans les lignes et détails ; clic vers
  Recherche dans le connu. `source_collection` reste une donnée technique.
- Import ACSM depuis Bibliothèque ou barre de titre : dialogue GTK possédé,
  identité capturée à l'ouverture, traitement en tâche de fond, état
  actionnable et reprise via le core existant. L'original est conservé.
- Pages standard, Plugins et Paramètres : contributions `ui.page` du paquet
  intégré `core.ui`. Navigation classée par métadonnées de groupe.
- Actions déclaratives des pages, action implicite Afficher, barre de titre
  `ui.titlebar` remplaçable par priorité et choix des actions dans Plugins →
  Interface cifereca → Barre de titre → Configurer. Le menu reste accessible.
- La page Plugins groupe les contributions par leurs catégories déclarées,
  affiche les réglages globaux et l'activation des providers publics par identité.
- Les fournisseurs publics ne proposent plus de création de faux compte dans
  Paramètres. Les anciennes lignes de config sont conservées pour compatibilité.
- Diagnostic normal/verbose/trace (`detailed` conservé comme alias), expurgation
  des secrets imbriqués, erreurs contenant des URL signées et chemins PNB LOAN.
- Filtres secondaires repliables en plusieurs lignes, minima assouplis,
  focus au changement de page, Ctrl+F, entrée sur sélection et Échap vers liste.

Les types de contenu sont transmis aux providers qui acceptent le paramètre
optionnel `content_types`. Les signatures v1 des plugins 0.9.4 n'acceptent pas
ce paramètre : leur filtrage de type est fait sur les résultats reçus. Une
facette ne provoque jamais de requête distante supplémentaire.
