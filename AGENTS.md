# Cifereca — guide des agents

Ce fichier s'applique à tout le dépôt. Il fixe les priorités, les frontières et le mode de collaboration. Les profils sous [.agents/roles/](.agents/roles/README.md) et les AGENTS.md locaux précisent les règles par répertoire. En cas de divergence documentaire, vérifier le code et les contrats actifs avant d'agir.

## Priorités : une seule file

- **P0 — stabilité** : sécurité, régressions bloquantes, intégrité des données, installation, CI et release fiable.
- **P1 — prochain plugin à terminer** : un seul plugin cible, avec ses dépendances et sa recette indispensables.
- **P2 — reste** : autres plugins, ergonomie non bloquante, recherche et idées.

Le label GitHub P0/P1/P2 fait foi et doit être **unique par issue**. Le rôle, l'ancienneté et les anciennes sous-priorités de plugin ne changent pas le rang. La cible P1 est fixée par le backlog, pas par opportunité. Voir [#839](https://github.com/pzia/cifereca/issues/839) pour les critères courants de stabilité.

Avant chaque étape, examiner issues, PR, branches et CI récentes ; éviter les travaux concurrents. Si le premier sujet est bloqué, documenter précisément le blocage et avancer sur un autre sujet de **même priorité**. Ne passer plus bas que si les travaux prioritaires sont réellement bloqués ou indépendants, en laissant cette situation visible.

## Rôles et propriétaires

Chaque **étape** a un rôle actif unique. Ne pas mélanger les responsabilités dans une même modification ; transmettre les dépendances au bon propriétaire.

| Rôle | Livrable et périmètre |
| --- | --- |
| **backlog** | Priorités, critères d'acceptation, déduplication, dépendances et issues actionnables. Pas de code ni de merge. |
| **core** | Core métier, stockage, services, API publique, framework de plugins ; également CoreUI (GTK/libadwaita). Pas de logique métier spécifique à un plugin. |
| **plugins** | Fonctionnalités sous src/plugins/, manifestes et tests de chaque plugin. Consommer uniquement cifereca.plugin_api ; demander une évolution Core si nécessaire. |
| **ux** | Analyse des parcours, simulation fonctionnelle, E2E exploratoires, preuves GTK et tickets UX ; ne corrige pas le produit. |
| **cicd** | Workflows, packaging, tests transverses, vérification des artefacts, résolution des blocages CI et **merge** des PR prêtes. |
| **focus** | **Méta-rôle** : choisit les étapes selon P0 → P1 → P2 et prend successivement les rôles ci-dessus. Ne constitue pas une exception aux frontières. |

Alias acceptés : **merger** = cicd ; **coreui** = core (périmètre UI) ; **simulation** = ux (périmètre simulateurs et recettes). Le rôle core donne priorité au non-UI à **priorité égale**, et cicd aux gates CI avant les merges à **priorité égale**. Ces préférences ne dépassent jamais P0/P1/P2.

## Méta-rôle focus : une étape à la fois

Quand le rôle demandé est focus, ou quand une demande porte sur un enchaînement transversal :

1. **Lire la situation** : état de main, issues P0/P1/P2, PR, CI, dépendances et preuves déjà disponibles.
2. **Choisir une seule action utile** : priorité la plus haute, travail non dupliqué et résultat vérifiable ; annoncer le rôle actif et la référence GitHub.
3. **Agir dans ce rôle** : produire un incrément cohérent (correctif + tests + PR, recette + preuves, triage, ou merge). Un changement de propriétaire implique une nouvelle étape.
4. **Synchroniser** si un état change ou qu'un autre rôle doit intervenir : actualiser l'issue existante ou la PR avec **état, résultat, preuve/SHA, blocage, prochain rôle et action**. Ne pas créer une issue-journal par étape ni publier des commentaires sans information nouvelle.
5. **Réévaluer** les priorités après l'étape ; continuer avec le même rôle ou en adopter un autre. « Next » demande l'étape suivante, **jamais** une release.

Un blocage doit produire un handoff exploitable, pas un faux succès. Focus coordonne ; seul **cicd** réalise un merge, après validation, et une release exige toujours une **commande explicite** (tag, packaging, publication et vérification des artefacts).

## Synchronisation : issues plutôt que journal sur main

- **Issue GitHub = source de vérité** : priorité, propriétaire, périmètre, critères de sortie, blocages, décisions et prochain handoff. Réutiliser le ticket canonique ; créer une nouvelle issue seulement pour un problème distinct.
- **PR = preuve d'implémentation** : diff atomique, tests ciblés, CI, review, lien à l'issue. Préférer une branche et une PR à toute écriture directe dans main.
- **main = produit et documentation validée**, pas journal opérationnel modifié à chaque étape. Réserver un récapitulatif versionné aux décisions durables ou jalons, via PR.

Un push de journal sur main peut déclencher les workflows Core, Plugins et validation de l'installation, créer du bruit et des conflits, sans apporter de meilleure traçabilité qu'une issue. **Choix par défaut : issue/PR**, mise à jour aux changements d'état, et non push direct d'un journal.

## Architecture, tests et preuves

- Core : src/cifereca/. Plugins fonctionnels : src/plugins/<plugin>/. Le framework src/cifereca/plugins/ appartient au Core ; un plugin de production n'importe aucun module privé Core. Voir docs/ARCHITECTURE.md et docs/PLUGIN-BOUNDARIES.md.
- Placer les tests selon **l'invariant testé** : Core et contrats transverses dans tests/ ; assertions plugin dans src/plugins/<plugin>/tests/ ; simulateurs dans simulation/tests/ ; bootstrap GTK/Xvfb dans environnements/. Voir docs/TEST-ORGANIZATION.md.
- simulation/ contient les faux services, datasets et profils fonctionnels ; environnements/ configure les machines, GTK, Xvfb et D-Bus. Ne pas créer de banc parallèle ; éviter le réseau réel dans les tests déterministes.
- Exécuter les tests ciblés puis la suite pertinente. Un pytest lancé à la racine ne couvre pas nécessairement les tests de plugins. Ne pas réintroduire en bloc d'anciens tests _bundled.
- Pour une **preuve UX GTK**, lire environnements/README.md, préparer le profil avec simulation/datasetctl.py, lancer **la vraie application** et capturer son écran et ses logs. Une maquette, un self-test GTK ou une vidéo reconstruite ne prouvent pas un parcours fonctionnel.
- Ne pas versionner caches, environnements virtuels, captures brutes ou artefacts générés sans besoin explicite. Consulter docs/RATIONALIZATION-STATUS.md pour les dettes historiques, en privilégiant le code actuel en cas de contradiction.

## Livraison et garde-fous

- Une PR = un changement cohérent et réversible, avec tests et critères d'acceptation. Corriger un blocage dans le rôle propriétaire ; sinon transmettre une issue ciblée.
- **cicd** vérifie la CI, les reviews, les conflits et les risques avant merge. Un test réellement échoué n'est pas un simple check indisponible. Ne pas rebaser une PR uniquement parce qu'elle est derrière main ; actualiser seulement en cas de nécessité réelle.
- **Aucune release implicite** : un merge, un « next », une PR ou une demande de préparation ne valent pas autorisation de publier.
- Utiliser les skills locaux de .agents/skills/ : cifereca-plugin, cifereca-tests, cifereca-simulation et cifereca-release.

Les consignes spécialisées et détaillées restent dans .agents/roles/ ; ce guide évite de les dupliquer.
