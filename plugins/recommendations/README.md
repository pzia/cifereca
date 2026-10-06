# Recommandations CIFERECA 0.2.1

Première version fonctionnelle pour **Cifereca Core 0.9.44-dev.1 / Plugin API 1.4**.


## 0.2.1

- barre de progression déterministe : lecture bibliothèque, requête courante, catalogues terminés, résultats, classement final ;
- utilisation de `catalog.search_progressive()` : fournisseurs interrogés en parallèle par le Core ;
- `force=False` conservé : réutilisation du cache de recherche Core ;
- affichage des lots servis depuis le cache et des lots réseau ;
- `metadata.library_entries(..., scan=False)` : aucune rescan du filesystem pendant le calcul.

Le Core 0.9.44 conserve par défaut les recherches 21 600 s (6 h). La clé de cache inclut requête, fournisseurs, scope, champ, types et préférences de lecture.

## Fonctionnement

- lit uniquement `metadata.library_entries(identity_id=...)` ;
- interroge `catalog.search_progressive(...)` avec repli compatible sur `catalog.search(...)` ;
- construit jusqu'à 8 recherches à partir des auteurs, séries et sujets dominants ;
- fusionne les rangs avec RRF et un score de contenu explicable ;
- retire les livres déjà présents (ISBN, sinon titre+auteur) ;
- déduplique les mêmes notices issues de plusieurs catalogues ;
- applique une diversification MMR réglable ;
- affiche le résultat sur une seule page avec filtres profil, catalogue, texte et score.

Aucun accès privé au Core, aucune acquisition, aucun téléchargement.

## Installation

```bash
cifereca-cli plugin install cifereca-recommendations-0.2.1.zip --replace
```

Puis redémarrer Cifereca si une ancienne version du module était déjà chargée.
