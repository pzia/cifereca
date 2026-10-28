# Cifereca — guide des agents

## Architecture canonique (28/10/2026)

**Toute évolution Core/Plugins/UX de synchronisation doit suivre [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) et la décision [#3171](https://github.com/pzia/cifereca/issues/3171).** Une seule Sync automatique inventorie les droits exacts A, vérifie OriginLink→SHA→Placement dans le profil B, puis acquiert les fichiers non satisfaits selon droits/consentements existants ; Notice C reste descriptive. Le rattrapage manuel #2797 est une exception, jamais une deuxième boucle obligatoire. La documentation cible **ne signifie pas que le moteur soit livré** ; #3528 est un lecteur Core **fusionné**, #3523 un plan en mémoire **ouvert** ; les migrations #3196/#3275 restent nécessaires. Les consignes de journal brut et de credentials ci-dessous **prévalent** pour leur périmètre.


## Consigne supérieure Diagnostics (25/10/2026, #3132)

**Journaliser les faits observés sans expurgation générale.** Core, plugins, journal local unique, miroir console, broker et exports bruts conservent les valeurs **exactement telles qu'émises**, y compris URL, chemins, titres, identifiants, tokens, cookies, en-têtes et détails d'erreur. Aucune valeur ne doit être remplacée par `[opération masquée]` pour seule raison de confidentialité générale. Ne pas créer de nouveaux dumps HTTP/authentification en dehors d'une instrumentation métier explicitement demandée.

**Uniquement dans l'IHM Diagnostics**, pseudonymiser **exclusivement les valeurs des champs login et mot de passe** (`login-1`, `mot-de-passe-1`, stables pendant la session). Tout le reste demeure visible. Recherche sur les données brutes ; copie du rendu pseudonymisé ; export brut non transformé après confirmation, permissions de fichier 0600, jamais d'envoi automatique. En cas de contradiction avec les anciennes politiques de masquage de logs, **cette règle prévaut**. Voir [journal canonique](docs/DIAGNOSTICS_CANONICAL_JOURNAL.md), [contrat transversal](docs/LOGGING-CONTRACT.md), [skill de journalisation](.agents/skills/cifereca-logging/SKILL.md), [Core](.agents/roles/core.md), [Plugins](.agents/roles/plugins.md).



## Doctrine de confidentialité — credentials uniquement (26/10/2026)

**Seuls les credentials nécessitent une protection de confidentialité spécifique.** Sont concernés les secrets qui permettent de s'authentifier ou d'accéder à une ressource : mots de passe, jetons d'accès/rafraîchissement, cookies de session authentifiants, clés API privées, clés privées et URL signées qui font office de jetons. Un `credential_id`, un identifiant de compte ou un login *sans secret d'authentification* ne constitue pas en soi un credential secret.

**Tous les autres éléments sont utilisables tels quels à des fins de diagnostic et de preuve** : exceptions, traces, corps d'erreur, statut/échanges HTTP, chemins, titres, identifiants de compte ou de livres, noms, emails, profils, informations métier et captures sans credential. Ils ne justifient **aucun masquage, anonymisation, expurgation, collecte de preuves de confidentialité, audit additionnel ou ticket spécifique**. Ne pas bloquer un P0/P1, une PR, une recette ou une clôture sur leur seule présence ; ne pas inventer de travaux de confidentialité accessoires.

Pour les credentials, conserver les protections existantes : stockage local à permissions restrictives, absence de publication/transfert automatique des secrets et avertissement/confirmation avant export volontaire des journaux bruts. **Le contrat de journalisation reste intégral et non expurgé**, y compris dans les exports bruts déclenchés par l'utilisateur ; **seuls login et mot de passe sont pseudonymisés dans le rendu de l'IHM Diagnostics**, selon le contrat canonique. La protection des credentials n'autorise pas une expurgation générique des données de diagnostic.

Cette règle prévaut sur les anciennes consignes générales de « données privées » ou « logs/captures expurgés » en matière de **confidentialité** ; elle ne supprime ni les invariants de droits, d'intégrité, de sécurité des opérations, ni les limitations techniques propres à des artefacts ou à une autorisation fournisseur.

Ce fichier s'applique à tout le dépôt. Il fixe les priorités, les frontières et le mode de collaboration. Les profils sous [.agents/roles/](.agents/roles/README.md) et les AGENTS.md locaux précisent les règles par répertoire. En cas de divergence documentaire, vérifier le code et les contrats actifs avant d'agir.

**Agents UX / GTK4 sur VM Debian 13 sans réseau APT :** avant de créer un relais ou de conclure que GTK est indisponible, suivre le [cache GTK4 préassemblé sur GitHub Releases](environnements/openai-chat-debian/GITHUB_RELEASE_CACHE.md). La recette rootless y vérifie SHA-256 et la plateforme, puis exerce la vraie IHM via [E2E-UX](environnements/E2E-UX.md). Ce cache binaire n'appartient pas au Git et n'est pas compatible Ubuntu.

## Priorités : une seule file

La priorité exprime **l'urgence et l'engagement dans la réalisation**, non le rôle, le composant ou la catégorie technique du ticket.

- **P0 — bloquant ou urgent** : défaut ou décision nécessitant un traitement immédiat, **le plus souvent à l'initiative du propriétaire du projet**. Un agent peut signaler une urgence étayée (sécurité, perte de données, panne bloquante, etc.) au backlog ; il ne transforme pas par précaution toutes les anomalies techniques en P0.
- **P1 — chantier actif important** : sujet important **sur lequel on travaille actuellement**, quel que soit le rôle exécutant (`core/coreui`, `plugins`, `cicd/merger`, etc.). **P1 n'est pas réservé au prochain plugin.**
- **P2 — backlog fonctionnel** : ticket **précis et actionnable**, non directement lié au sujet du moment. Le conserver au backlog sans réalisation opportuniste ; sa promotion en P1 traduit la décision de l'engager comme chantier actif.
- **P3 — idées** : concepts, pistes ou propositions **non retenus pour implémentation**. Les conserver sans créer de branche/PR ni lancer de développement jusqu'à décision explicite, puis requalification en P1/P2 selon le cas.

Le **label GitHub unique P0/P1/P2/P3** fait foi sur chaque issue ; le rôle, l'ancienneté et la seule existence d'une implémentation possible n'autorisent aucune promotion automatique. Le propriétaire peut réviser la priorité à tout moment ; `backlog` documente les changements de périmètre, l'arbitrage et le lien avec le chantier actif. Voir [#839](https://github.com/pzia/cifereca/issues/839) pour les critères de stabilité, qui ne remplacent pas cette classification produit.

Avant chaque étape, partir de `main`, des issues actives (P0/P1 et tout ticket explicitement engagé), des PR ouvertes et de leur CI ; éviter les travaux concurrents. **Ne pas inventorier toutes les branches par défaut** : examiner une branche hors PR uniquement si une issue, un handoff ou un indice concret de travail concurrent la signale. L'audit exhaustif des branches est une opération dédiée, pas un prérequis à chaque étape. Un **P0 réel** interrompt les travaux moins urgents lorsque c'est nécessaire à son traitement. Hors P0, avancer sur le **P1 actif** et ses dépendances indispensables. Si le sujet actif est bloqué, documenter précisément le blocage et poursuivre un autre travail P0/P1 autorisé, **pas un P2 ou P3 opportuniste**. Un P2 n'entre en réalisation que sur demande explicite ou promotion décidée ; un P3 exige une décision produit explicite.

## Périmètre strict du ticket et restitution par défaut

Cette règle s'applique à **tous les rôles** : backlog, core/coreui, plugins, ux/simulation, cicd/merger et focus.

- **Résoudre uniquement le ticket visé**, avec une implémentation robuste : contrat attendu, cas limites directement pertinents, erreurs, concurrence, sécurité des données et tests de non-régression nécessaires. Aucun refactoring opportuniste, amélioration adjacente ou correctif d'un défaut indépendant.
- Si une anomalie distincte apparaît, **ne pas la corriger dans la même PR** : signaler un blocage directement pertinent et transmettre au rôle propriétaire ; sinon la laisser hors périmètre. Une panne CI préexistante ne justifie pas d'étendre le correctif.
- **Restitution concise dans le chat par défaut** : ticket/PR, résultat, validations, blocage et prochaine action, avec liens GitHub utiles. **Ne pas générer ni joindre de rapport détaillé sous forme de fichier** (Markdown, PDF, ZIP, etc.) sauf demande explicite ou preuve spécifique indispensable. Les artefacts CI demeurent accessibles sur GitHub.

## Rôles et propriétaires

Chaque **étape** a un rôle actif unique. Ne pas mélanger les responsabilités dans une même modification ; transmettre les dépendances au bon propriétaire.

| Rôle | Livrable et périmètre |
| --- | --- |
| **backlog** | **Analyse et ticketing exclusivement** : triage, priorités, critères d'acceptation, déduplication, dépendances, issues et handoffs. Aucune implémentation, aucun test exécuté, aucune branche/PR/merge. Le rôle reste actif jusqu'à changement **explicitement demandé par l'utilisateur**. Voir [profil Backlog](.agents/roles/backlog.md#frontière-de-rôle-impérative-par-défaut-sans-exception-implicite). |
| **core** | Core métier, stockage, services, API publique, framework de plugins ; également CoreUI (GTK/libadwaita). Pas de logique métier spécifique à un plugin. |
| **plugins** | Fonctionnalités sous src/plugins/, manifestes et tests de chaque plugin. Consommer uniquement cifereca.plugin_api ; demander une évolution Core si nécessaire. |
| **ux** | Analyse des parcours, simulation fonctionnelle, E2E exploratoires et preuves GTK ; **propositions numérotées courtes à valider avant création des nouveaux tickets UX**. Ne corrige pas le produit. |
| **cicd** | Workflows, packaging, intégration des E2E ciblés, contrôles, **merge** des PR prêtes et vérification de la clôture des issues. |
| **focus** | **Méta-rôle** : choisit les étapes selon P0 urgent → P1 actif ; P2 seulement sur demande/promotion, P3 en idées ; adopte `backlog`, `core`, `plugins` ou `cicd`. Le rôle `ux` reste indépendant et **exclu de focus**. |

Alias acceptés : **merger** = cicd ; **coreui** = core (périmètre UI) ; **simulation** = ux (périmètre simulateurs et recettes). Le rôle core donne priorité au non-UI à **priorité égale**, et cicd aux gates CI avant les merges à **priorité égale**. Ces préférences ne dépassent jamais P0/P1/P2/P3.

## Coordination anti-concurrence : un propriétaire par résolution (#889)

**Le rôle se détermine par le comportement à corriger et la responsabilité sur le fichier, pas par qui a découvert l'anomalie.** Une issue peut avoir plusieurs intervenants successifs, mais **un seul pilote actif de la même résolution** ; y compris quand plusieurs agents portent le même rôle. Les frontières restent applicables aux priorités P0–P3 ; une urgence P0 ne dispense pas de consulter les travaux déjà engagés.

| Périmètre partagé | Propriétaire de la modification | Contribution sans prendre le même correctif |
| --- | --- | --- |
| `src/cifereca/**` ; API publique, contrats Core et widgets CoreUI | `core/coreui` | `plugins` décrit le besoin de contrat ; `ux` apporte la reproduction |
| `src/plugins/**`, UI et protocole d'un plugin, manifestes/compatibilité du package | `plugins` | `core` fournit séparément une primitive publique si nécessaire |
| `tests/**` | Propriétaire de **l'invariant** : `core` (Core), `plugins` (contrat plugin), `cicd` (runner/découverte/chaînage), `ux` (recette exploratoire seulement) | Ne pas reprendre en double les mêmes assertions |
| `e2e/**` | Assertions d'acceptation : `core` / `plugins` ; parcours **exploratoires** et captures : `ux` ; **harnais** (`e2e/run.py`, bootstrap de scénario, intégration/sélection et gates) : `cicd` | Échanger le scénario avec `cicd` si sa promotion en gate impose de changer le harnais |
| `simulation/**`, datasets/faux services et profils fonctionnels de recette | `ux/simulation` | `plugins` écrit ses fixtures de contrat sous son package ; `cicd` ne réécrit pas le simulateur pour débloquer un runner |
| `environnements/**`, runners, bootstrap Xvfb/GTK/D-Bus | `cicd` pour l'infrastructure reproductible ; `ux` pour **recettes/captures exploratoires sans changer le bootstrap partagé** | Handoff à `cicd` si la recette exige une modification du runtime |
| `.github/workflows/**`, scripts et sélecteurs CI, assemblage/publication de releases | `cicd` | `core` possède les contrats de mise à jour, `plugins` les manifestes ; release uniquement sur ordre explicite |
| Issues GitHub, priorisation, déduplication, arbitrage des rôles | `backlog` | `ux` ne crée ses nouveaux tickets exploratoires **qu'après validation utilisateur** ; CI/CD ferme les tickets livrés après merge |

**Vérification avant de commencer une résolution :** consulter `main`, l'issue canonique et ses `blocking/blocked-by`, les PR ouvertes **et leurs fichiers modifiés** (pas seulement les titres), les HEAD des PR pertinentes et leur CI ; rechercher une PR du **même ticket** ou modifiant les **mêmes fonctions/fichiers**. Un chevauchement de chemin est un **risque à coordonner**, pas une preuve automatique de conflit. Si deux agents visent le même correctif, conserver un seul pilote ; l'autre transmet preuves et dépendances ou prend une tranche réellement indépendante. Noter rôle pilote, état (`ready/doing/blocked/review`), PR/HEAD et prochain handoff dans les objets GitHub existants, sans verrou durable, commentaire-journal ni branche partagée permanente. Reprendre un travail inactif seulement après contrôle de son état et des autres PR.

**PR croisées** : une PR atomique par propriétaire et responsabilité ; si une étape exige une API Core avant adaptation Plugin, créer **des tranches et PR liées**, pas deux implémentations concurrentes du contrat. Avant de modifier un fichier partagé par une autre PR, coordonner l'ordre de livraison. `cicd/merger` tente la résolution **Git mécanique** (rebase ciblé si justifié), sous permissions, `--force-with-lease` si autorisé et tests/checks sur le nouveau SHA ; s'il faut choisir un comportement métier ou architectural, le pilote Core/Plugins tranche. Ne pas pousser concurremment sur une branche que `cicd` réconcilie. La clôture appartient à `cicd` après merge et vérifications ; aucune recette utilisateur IHM implicite.

**Exemples de handoff concrets :**

1. **Plugin → API Core** : le provider Kobo a besoin d'un contrat de synchronisation générique ([#1346](https://github.com/pzia/cifereca/issues/1346)) : `plugins` définit les besoins, `core` réalise la primitive dans sa PR, puis `plugins` branche le provider sur l'API publique. Un seul propriétaire par PR.
2. **UX → CI/CD** : une exploration Jeunesse identifie un scénario manquant ; `ux` propose les tickets selon la validation #1449, conserve sa recette exploratoire, puis passe à `cicd` la modification nécessaire du runner ou de la sélection, avec scénario/assertions fournis par `core` ou `plugins` si l'objectif est l'acceptation.
3. **Deux PR sur un fichier** : [#1044](https://github.com/pzia/cifereca/pull/1044) et [#1008](https://github.com/pzia/cifereca/pull/1008) touchent toutes deux `youth_page.py` et le manifeste Jeunesse : vérifier les hunks et HEAD, ordonner les merges et confier la réconciliation Git à `cicd` ; ne pas lancer une troisième correction redondante ou conclure à un conflit uniquement sur les chemins.

**Suivi léger par `backlog`** : lors d'un audit, relever à date **PR ouvertes**, **paires de PR avec chemins communs**, **conflits Git attestés**, **merges refusés vérifiés** et **rebases jugés inutiles** ; comparer à un audit ultérieur. Une PR documentaire seule ne prouve pas une baisse des conflits. Référence : [#889](https://github.com/pzia/cifereca/issues/889).

## Méta-rôle focus : une étape à la fois

Quand le rôle demandé est focus, ou quand une demande porte sur un enchaînement transversal **sans rôle classique déjà actif** : **focus ne prend jamais le rôle `ux` (ni son alias `simulation`)**. Il transmet les recettes visuelles et simulations UX au propriétaire UX indépendant, même pour un P0, puis avance sur un autre travail autorisé.

1. **Lire la situation** : état de main, issues P0/P1/P2/P3, PR, CI, dépendances et preuves déjà disponibles.
2. **Choisir une seule action utile** : priorité la plus haute, travail non dupliqué et résultat vérifiable ; annoncer le rôle actif et la référence GitHub.
3. **Agir dans ce rôle** : produire un incrément cohérent (correctif + tests + PR, recette + preuves, triage, ou merge). Un changement de propriétaire implique une nouvelle étape.
4. **Synchroniser** si un état change ou qu'un autre rôle doit intervenir : actualiser l'issue existante ou la PR avec **état, résultat, preuve/SHA, blocage, prochain rôle et action**. Ne pas créer une issue-journal par étape ni publier des commentaires sans information nouvelle.
5. **Réévaluer** les priorités après l'étape ; continuer avec le même rôle ou en adopter un autre. « Next » demande l'étape suivante, **jamais** une release.

Un blocage doit produire un handoff exploitable, pas un faux succès. Seul **cicd** réalise un merge, **y compris focus lorsqu'il assume explicitement ce rôle** (alias `merger`). Focus peut alors vérifier, fusionner et clore lui-même, sans transfert obligatoire à un autre fil, en respectant le profil cicd : permissions, reviews, protections, contrôles CI et E2E ciblés, vérification du SHA et tentative de résolution mécanique des conflits ; les arbitrages métier ou architecturaux restent au propriétaire. Une étape `core` ou `plugins` ne fusionne pas : la terminer puis annoncer une nouvelle étape `cicd` avant le merge, ou transmettre à un autre cicd si utile. Une release exige toujours une **commande explicite** (tag, packaging, publication et vérification des artefacts).

## Livraison asynchrone, preuves et clôture

**Politique canonique : [docs/AGENT-DELIVERY-POLICY.md](docs/AGENT-DELIVERY-POLICY.md).**
Elle s'applique à tous les rôles et aux fils ChatGPT qui avancent en parallèle.

- **Pas de validation IHM humaine obligatoire par défaut** : l'absence d'une recette utilisateur n'empêche ni une PR vérifiée d'être fusionnée, ni un ticket livré d'être fermé.
- **Preuve proportionnée au risque** : tests unitaires et intégration ciblés en premier ; E2E GTK/XTEST ciblé pour les parcours visuels ou sensibles. `cicd` possède l'orchestration/gates E2E existants ; Core et Plugins écrivent les assertions de leurs comportements ; UX conserve des recettes exploratoires indépendantes.
- **Clôture après livraison** : `cicd` vérifie le SHA, les contrôles obligatoires et les critères du ticket, merge puis confirme la fermeture, avec liens vers les preuves. Aucun check réellement rouge ou requis inconnu n'est réputé réussi.
- **Vérification terrain différée** : `backlog` identifie les livraisons à contrôler en priorité ; `ux` peut explorer un parcours précis. Une observation manuelle non effectuée reste **non vérifiée**, pas « en échec » ni « blocante » par défaut.
- **Approbation préalable des nouveaux tickets UX** : après exploration, `ux` présente **une courte liste numérotée de propositions** (impact, priorité et rôle), attend l'accord explicite de l'utilisateur, y compris partiel, puis crée seulement les tickets acceptés. Ce contrôle ne s'applique **pas aux merges/clôtures**, qui suivent les preuves techniques et les gates. Voir [UX](.agents/roles/ux.md#validation-utilisateur-avant-création-de-tickets-ux-p0-1449).
- **Défaut ultérieur** : réouvrir le même ticket si le contrat livré est défaillant, créer une nouvelle issue pour un problème distinct ; documenter le fait observable.
- **Coordination des fils** : partir de `main`, des issues actives et des PR ouvertes pertinentes (diffs, HEAD SHA, CI) avant toute prise de travail ; ne rechercher d'autres branches que sur référence explicite ou indice concret ; garder un seul rôle pilote et un handoff GitHub exploitable, sans réservation tacite ni commentaires de simple progression.
- **Exception de sécurité** : ne pas repousser après merge une preuve indispensable pour opérations destructives, secrets, intégrité ou installation. Si vraiment impossible, signaler un blocage réel et solliciter une décision explicite ; jamais inventer un PASS.

## Synchronisation : issues plutôt que journal sur main

- **Issue GitHub = source de vérité** : priorité, propriétaire, périmètre, critères de sortie, blocages, décisions et prochain handoff. Réutiliser le ticket canonique ; créer une nouvelle issue seulement pour un problème distinct.
- **PR = preuve d'implémentation** : diff atomique, tests ciblés, CI, review, lien à l'issue. Préférer une branche et une PR à toute écriture directe dans main.
- **main = produit et documentation validée**, pas journal opérationnel modifié à chaque étape. Réserver un récapitulatif versionné aux décisions durables ou jalons, via PR.

Un push de journal sur main peut déclencher les workflows Core, Plugins et validation de l'installation, créer du bruit et des conflits, sans apporter de meilleure traçabilité qu'une issue. **Choix par défaut : issue/PR**, mise à jour aux changements d'état, et non push direct d'un journal.

## Tests non bloquants pour l'agent : passer à l'opération suivante

**Règle d'exécution : ne pas attendre une exécution de tests et passer à l'opération suivante.** Dès qu'une suite de tests ou un workflow CI/E2E a été lancé et peut continuer de façon autonome, enregistrer **PR/ticket, HEAD SHA, lien du run, tests concernés et état `en cours`**, puis avancer vers la **prochaine opération utile et indépendante autorisée** (autre PR, autre ticket prioritaire ou préparation non conflictuelle). Ne pas immobiliser un agent en sondant une CI en boucle, ni relancer pour accélérer ou déclencher un résultat.

À la prochaine revue de la PR ou avant une opération **qui dépend du résultat** (notamment fusion, clôture, release, déploiement ou action touchant secrets/intégrité), revenir consulter **les résultats effectifs sur le bon SHA**. **Un test en cours, absent ou inconnu n'est jamais un PASS** : ne pas merger/fermer une issue dont les contrôles requis restent en attente ou échouent ; consigner l'attente puis avancer ailleurs. Les protections GitHub, permissions, reviews et exigences de sécurité restent intégralement applicables.

Cette règle concerne **le temps d'attente de l'agent**, pas une dispense de tests ni l'autorisation de prendre une opération dépendante de résultats non connus. Si aucune exécution non bloquante n'est disponible, ne pas prétendre l'avoir déclenchée ; utiliser le moyen de test réellement disponible et restituer honnêtement la preuve obtenue.

## Frugalité des GitHub Actions : règle commune

**Chaque minute de CI doit apporter une preuve nécessaire.** Cette discipline s'applique à tous les rôles, y compris `focus`, et ne diminue jamais les contrôles obligatoires.

- **Avant de déclencher** : consulter le SHA, le diff, les checks et runs déjà disponibles. Ne pas relancer un workflow pour simple suivi d'état, ni répéter des tests sans changement pertinent ou hypothèse de panne documentée. Un run vert ne vaut que pour son SHA et son périmètre.
- **Cibler au plus juste** : exécuter d'abord les vérifications locales et les tests du composant modifié. En CI, privilégier les chemins affectés et leurs dépendances ; documentation, notes et gouvernance seules ne justifient aucune suite lourde. Ne réserver les matrices Python complètes et GTK/E2E exhaustifs qu'aux changements qui les nécessitent, aux gates obligatoires et aux publications.
- **Éviter les doublons** : ne pas lancer manuellement ce que les checks de la PR valident déjà ; regrouper les vérifications compatibles, utiliser les caches pertinents et ne pas repousser inutilement un nouveau commit pour obtenir un run. Les essais PR devenus obsolètes peuvent être annulés ; **jamais** une publication en cours.
- **Respecter le budget** : surveiller les minutes Actions, limiter les tentatives et la conservation d'artefacts au nécessaire. Si le quota ou les runners bloquent les jobs, consigner le diagnostic et **arrêter les relances** ; à la reprise, commencer par une seule vérification ciblée, pas une batterie complète.
- **Ne pas fabriquer de déclencheurs** : privilégier issues et PR pour les handoffs, jamais des commits de journal sur `main` ni une branche partagée de triggers. Toute release exige une autorisation explicite et conserve ses validations exhaustives, empreintes, provenance et rollback ; aucun raccourci budgétaire sur la sécurité.
- **Responsabilité** : `cicd` est propriétaire du routage, du coût des workflows et des merges ; les autres rôles lui transmettent un besoin de validation coûteuse plutôt que de multiplier les déclenchements. Un check obligatoire rouge ou inconnu n'est jamais réputé réussi pour économiser la CI.

Le plan opérationnel et les tests de sélection sont suivis dans [#856](https://github.com/pzia/cifereca/issues/856).

## Architecture, tests et preuves

- Core : src/cifereca/. Plugins fonctionnels : src/plugins/<plugin>/. Le framework src/cifereca/plugins/ appartient au Core ; un plugin de production n'importe aucun module privé Core. Voir docs/ARCHITECTURE.md et docs/PLUGIN-BOUNDARIES.md.
- Placer les tests selon **l'invariant testé** : Core et contrats transverses dans tests/ ; assertions plugin dans src/plugins/<plugin>/tests/ ; simulateurs dans simulation/tests/ ; bootstrap GTK/Xvfb dans environnements/. Voir docs/TEST-ORGANIZATION.md.
- simulation/ contient les faux services, datasets et profils fonctionnels ; environnements/ configure les machines, GTK, Xvfb et D-Bus. Ne pas créer de banc parallèle ; éviter le réseau réel dans les tests déterministes.
- Exécuter les tests ciblés puis la suite pertinente. Un pytest lancé à la racine ne couvre pas nécessairement les tests de plugins. Ne pas réintroduire en bloc d'anciens tests _bundled.
- Pour une **preuve UX GTK**, lire environnements/README.md, préparer le profil avec simulation/datasetctl.py, lancer **la vraie application** et capturer son écran et ses logs. Une maquette, un self-test GTK ou une vidéo reconstruite ne prouvent pas un parcours fonctionnel.
- Ne pas versionner caches, environnements virtuels, captures brutes ou artefacts générés sans besoin explicite. Consulter docs/RATIONALIZATION-STATUS.md pour les dettes historiques, en privilégiant le code actuel en cas de contradiction.

## Livraison et garde-fous

- Une PR = un changement cohérent et réversible, avec tests et critères d'acceptation. Corriger un blocage dans le rôle propriétaire ; sinon transmettre une issue ciblée.
- **cicd** vérifie la CI, les reviews, les conflits et les risques avant merge. **Il tente de résoudre les conflits de PR**, de préférence par rebase Git natif si nécessaire et autorisé : résolution mécanique conservant les changements pertinents, diff revu, tests/gates à nouveau vérifiés sur le nouveau SHA, puis merge. Les arbitrages métier ou architecturaux reviennent au rôle propriétaire avec un diagnostic concret ; ne pas se contenter de déclarer un conflit bloquant sans tentative réalisable. Un test réellement échoué n'est pas un simple check indisponible. Ne pas rebaser une PR uniquement parce qu'elle est derrière main ; actualiser seulement en cas de nécessité réelle. Voir [le profil cicd](.agents/roles/cicd.md#responsabilité-active-de-résolution-des-conflits).
- **Aucune release implicite** : un merge, un « next », une PR ou une demande de préparation ne valent pas autorisation de publier.
- Utiliser les skills locaux de .agents/skills/ : **cifereca-logging** (Core et plugins), cifereca-plugin, cifereca-tests, cifereca-simulation et cifereca-release.

Les consignes spécialisées et détaillées restent dans .agents/roles/ ; ce guide évite de les dupliquer.
