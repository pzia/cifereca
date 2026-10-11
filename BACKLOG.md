# Backlog CIFERECA — index courant

> **Source de vérité :** [issues GitHub de `pzia/cifereca`](https://github.com/pzia/cifereca/issues), avec leurs labels P0/P1/P2/P3 et leurs liens aux PR. Ce fichier est un **index de navigation**, pas une seconde liste de tickets à tenir manuellement.

## Priorités et chantiers actifs

- **P0 — stabilisation :** sûreté Paris/ACSM/ADEPT [#976](https://github.com/pzia/cifereca/issues/976), [#977](https://github.com/pzia/cifereca/issues/977), [#982](https://github.com/pzia/cifereca/issues/982), [#984](https://github.com/pzia/cifereca/issues/984), et parcours normal [#1063](https://github.com/pzia/cifereca/issues/1063).
- **P0 — observabilité/Diagnostics :** [#907](https://github.com/pzia/cifereca/issues/907), [#1009–#1013](https://github.com/pzia/cifereca/issues/1010). La recette UX n'est pas présumée acquise du seul fait d'une PR fusionnée.
- **P0 — modularisation :** [#1049 Core](https://github.com/pzia/cifereca/issues/1049) et [#1051 CoreUI](https://github.com/pzia/cifereca/issues/1051) sont les épics ; leurs sous-tickets [#1116–#1132](https://github.com/pzia/cifereca/issues?q=is%3Aissue+is%3Aopen+label%3AP0) constituent des lots ordonnés, **pas des travaux simultanés**.
- **P1 — Jeunesse et dépendances prioritaires :** [#741](https://github.com/pzia/cifereca/issues/741), [#922](https://github.com/pzia/cifereca/issues/922), [#944](https://github.com/pzia/cifereca/issues/944) et autres tickets étiquetés P1 ; voir les [issues P1](https://github.com/pzia/cifereca/issues?q=is%3Aissue+is%3Aopen+label%3AP1).
- **P2 — reste du produit et recherche :** tickets étiquetés P2, dont Constellations [#1042](https://github.com/pzia/cifereca/issues/1042), recherche [#1023](https://github.com/pzia/cifereca/issues/1023) et synchronisation [#981](https://github.com/pzia/cifereca/issues/981).
- **P3 — idées non exécutables :** uniquement sur décision explicite ; exemple [#703](https://github.com/pzia/cifereca/issues/703).

Le détail des décisions, critères de recette, dépendances et statuts doit être lu **dans les tickets GitHub**, jamais déduit d'une case historique `[x]`.

## Architecture, environnements et validation

- [Architecture réelle et rationalisation](docs/ARCHITECTURE.md) et [état de rationalisation](docs/RATIONALIZATION-STATUS.md).
- [Frontières Core/plugins](docs/PLUGIN-BOUNDARIES.md) ; plugins fonctionnels sous `src/plugins/`.
- [Organisation des tests](docs/TEST-ORGANIZATION.md) ; banc fonctionnel canonique `simulation/`.
- [Environnements GTK et UX](environnements/README.md) ; recette UX réelle distincte des contrôles CI.
- [README général](README.md) pour l'installation, l'utilisation et les historiques de versions.

## Archives et traçabilité

L'ancien `BACKLOG.md` de **144 983 octets** a été conservé **dans l'historique Git**, figé au commit [`d7dbc9bb` — BACKLOG historique](https://github.com/pzia/cifereca/blob/d7dbc9bb0ef9f4239addf34ef42d79f89b212a0c/BACKLOG.md). Il conserve les livraisons anciennes et les décisions des vagues précédentes, mais **n'est pas normatif pour l'état actuel**.

Pour obtenir l'archive localement : `git show d7dbc9bb0ef9f4239addf34ef42d79f89b212a0c:BACKLOG.md`.

Toute suppression future de documentation ou de code nécessite vérification de ses consommateurs et des contrats de release ; les références historiques sont conservées dans Git.
