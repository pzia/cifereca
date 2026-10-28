# Simulation fonctionnelle CIFERECA

**État réel au 28/10/2026 : le répertoire simulation/ EXISTE et reste le banc fonctionnel de référence.** Voir [ARCHITECTURE.md](ARCHITECTURE.md) et la cible Core [#3171](https://github.com/pzia/cifereca/issues/3171). Il n'y a aucune migration « simulation/ → simulation/ » à effectuer.

## Fonction actuelle

simulation/ contient les faux services BnF, Kobo, Paris, Numilog et les sources publiques/OPDS, des livres et données déterministes, des profils TOML, datasetctl.py (services, XDG isolé, health checks) et ses propres tests.

Ce banc permet de vérifier les scénarios A→B de synchronisation **sans toucher aux comptes réels** ; il ne représente pas l'autorité métier : les règles et l'admission sont dans le Core, les protocoles dans les plugins. Le scénario A→B→C, huit OriginLink et zéro droit transmis est un **critère de test à réaliser dans #3171/#3275**, non une simulation actuellement prouvée et pas un P2 à développer opportunistement.

## Responsabilités et frontières

Le banc peut fournir faux services versionnés, états de fournisseur, jeux déterministes et fichiers, profils, erreurs/latences/volume et mesures des scénarios. Il ne contient ni bootstrap GTK/dépendances OS, ni écriture métier de remplacement du Core, ni protocoles réels des fournisseurs.

environnements/ prépare la machine (par exemple GTK/Xvfb/D-Bus sur Debian) ; simulation/ prépare les fournisseurs et profils. Un E2E installe l'environnement, démarre les faux services et données isolées, lance Cifereca, puis vérifie le parcours et l'absence de mutation non autorisée.

## Maintenance

Conserver simulation/ comme **référence unique** ; ajouter/retirer des fixtures seulement pour une recette explicitement engagée. Aucun déplacement générique ni deuxième arborescence ne sont prescrits par ce document. La rationalisation des faux services obsolètes relève d'une tâche dédiée, et les P2 non engagés restent au backlog.
