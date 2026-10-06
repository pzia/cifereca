# Recommandations CIFERECA 0.2.2

Version fonctionnelle pour **Cifereca Core 0.9.44-dev.1 / Plugin API 1.4**.

## 0.2.2 — performance et restitution progressive

- recherche catalogue exécutée hors thread GTK ;
- annulation réelle du calcul précédent via `TaskControl` ;
- premiers résultats affichés au fil des `CatalogSearchBatch` ;
- snapshots partiels coalescés toutes les ~180 ms pour éviter une rafale d'updates GTK ;
- rendu de la liste par petits lots : 8 lignes ajoutées par passage idle, effacement par lots de 20 ;
- classement provisoire léger pendant le réseau ; RRF + MMR complets une seule fois à la fin ;
- profil de bibliothèque calculé une seule fois ;
- MMR optimisé : caractéristiques pré-calculées, similarité maximale incrémentale et pool borné ;
- `force=False` conservé : réutilisation du cache Core ;
- `metadata.library_entries(..., scan=False)` : aucun rescan filesystem.

Le Core 0.9.44 conserve par défaut les recherches **21 600 s (6 h)**. La clé de cache inclut requête, fournisseurs, scope, champ, types et préférences de lecture.

### Mesure synthétique

Sur 120 candidats / 60 sorties : environ **8,8 s → 0,076 s** pour le classement MMR sur l'environnement de test utilisé pendant le développement.  
Sur 400 candidats / 120 sorties : la version optimisée termine autour de **0,23 s** ; l'ancienne implémentation dépassait 2 minutes sur le même scénario plus large.

Ces mesures servent à caractériser le hotspot CPU ; elles ne constituent pas un SLA.

## Fonctionnement

Le plugin construit jusqu'à 8 recherches à partir des auteurs, séries et sujets dominants, interroge les catalogues en parallèle via le Core, fusionne les rangs avec RRF, retire les livres déjà présents et applique une diversification MMR.

Pendant la recherche, les recommandations provisoires sont fondées sur le score de contenu uniquement afin de garder le streaming peu coûteux. Le classement final complet remplace ensuite ces résultats.

Aucun accès privé au Core, aucune acquisition, aucun téléchargement.

## Installation

```bash
cifereca-cli plugin install cifereca-recommendations-0.2.2.zip --replace
```

Puis redémarrer Cifereca si une ancienne version du module était déjà chargée.
