# Handoff — plugin Recommandations

État au 2026-10-06.

## Version courante

Plugin : `org.cifereca.recommendations`  
UI plugin : `ui.page.recommendations`  
Version : **0.2.2**  
Emplacement : `plugins/recommendations/`  
Core cible : **0.9.44-dev.1 / Plugin API 1.4**

Le plugin est mergé dans `main`.

## Objectif fonctionnel

À partir des livres d'un profil, produire une liste ordonnée de recommandations à partir des catalogues connectés, sur une page dédiée avec filtres.

Le plugin :
- lit la bibliothèque du profil via le broker public `metadata` ;
- interroge les catalogues via le broker public `catalog` ;
- exclut les livres déjà présents ;
- déduplique les notices multi-catalogues ;
- classe les candidats par affinité ;
- diversifie le résultat ;
- n'effectue ni emprunt, ni acquisition, ni téléchargement.

## Architecture

Pipeline actuel :

```
profil
  -> extraction auteurs / sujets / séries / termes
  -> construction de signaux de recherche
  -> catalog.search_progressive()
  -> résultats provisoires légers
  -> fusion RRF
  -> score de contenu
  -> déduplication
  -> MMR
  -> rendu final
```

Algorithmes :
- score de contenu : auteur, sujets, série, termes titre/description ;
- RRF pour fusionner les recherches issues des différents signaux ;
- MMR pour diversifier le classement ;
- exclusion par identifiant bibliographique ou couple titre+auteur.

Les pistes ML/embeddings n'ont volontairement pas été intégrées à cette version.

## Performance et réactivité UI

La version 0.2.2 a été conçue pour ne pas bloquer GTK.

### Threading

Les opérations de recherche et de classement tournent hors du thread GTK.

Les callbacks catalogue ne manipulent jamais GTK directement. Toute mise à jour UI repasse par `GLib.idle_add()`.

### Restitution progressive

Les résultats sont affichés avant la fin du calcul :
- chaque `CatalogSearchBatch` alimente un classement provisoire ;
- les snapshots sont coalescés environ toutes les 180 ms ;
- le classement provisoire utilise uniquement le score de contenu ;
- RRF + MMR complets ne sont calculés qu'une fois à la fin.

### Rendu GTK

Le rendu final est coopératif :
- ajout de 8 lignes par passage idle ;
- effacement par lots de 20 ;
- une nouvelle génération invalide les rendus précédents.

Cela évite de construire jusqu'à 120 lignes GTK d'un seul coup.

### Annulation

Un changement de profil ou un recalcul :
- annule le `TaskControl` précédent ;
- invalide les callbacks des générations obsolètes ;
- évite que les anciennes recherches continuent à consommer inutilement CPU/réseau.

## Hotspot MMR corrigé

La version 0.2.1 recalculait les caractéristiques des candidats à chaque comparaison MMR.

La 0.2.2 :
- pré-calcule les caractéristiques une fois ;
- conserve la similarité maximale courante ;
- limite le pool MMR aux candidats plausibles du haut de classement.

Mesures synthétiques obtenues pendant le développement :
- 120 candidats / 60 sorties : ~8,8 s -> ~0,076 s ;
- 400 candidats / 120 sorties : ~0,23 s avec la nouvelle implémentation ; l'ancienne dépassait 2 minutes sur ce scénario.

Ces chiffres servent à identifier le hotspot, pas de SLA.

## Cache

Le plugin appelle les recherches avec `force=False`.

Core 0.9.44 :
- cache des recherches catalogue : **21 600 s / 6 h par défaut** ;
- la clé inclut notamment requête, fournisseurs, scope, champ, types et préférences de lecture ;
- `CatalogSearchBatch.cached` permet de distinguer cache et réseau dans le chemin progressif.

La bibliothèque est lue avec :

```python
metadata.library_entries(identity_id=..., scan=False)
```

Donc aucun rescan filesystem n'est déclenché par le plugin.

## Fichiers principaux

- `plugins/recommendations/cifereca-plugin.json`
- `plugins/recommendations/recommendations_v022.py`
- `plugins/recommendations/recommendation_service_v022.py`
- `plugins/recommendations/recommendation_engine_fast.py`
- `plugins/recommendations/recommendation_engine.py`
- `plugins/recommendations/tests/test_performance_streaming.py`

Les versions `recommendations.py` et `recommendations_v021.py` restent présentes comme historique/compatibilité interne, mais l'entrypoint actif est `recommendations_v022:PLUGIN`.

## Tests importants

Couvrir en priorité :
- chargement du plugin et page unique ;
- `scan=False` ;
- `force=False` ;
- propagation de `TaskControl` à `search_progressive` ;
- restitution partielle ;
- annulation avant démarrage catalogue ;
- nombre borné d'extractions de caractéristiques dans le MMR ;
- absence d'import privé Core.

## Points de vigilance

1. Ne jamais faire de GTK depuis les callbacks du broker catalogue.
2. Ne pas recalculer RRF/MMR à chaque batch.
3. Ne pas réintroduire un rendu synchrone massif de la liste.
4. Garder `force=False` sauf action utilisateur explicite de vidage/recalcul sans cache.
5. Si le nombre de catalogues/signaux augmente fortement, mesurer la fréquence réelle des snapshots avant de réduire le coalescing.
6. Si un futur broker expose une recherche explicitement scoping par identité, utiliser cet API plutôt que de contourner le contrat public.

## Pistes futures

Ordre recommandé :
1. test GTK réel avec gros dataset et interaction simultanée avec d'autres pages ;
2. instrumentation légère temps total / réseau / classement / rendu ;
3. bouton Annuler visible pendant le calcul ;
4. cache plugin éventuel uniquement pour le classement dérivé, sans dupliquer inutilement le cache catalogue Core ;
5. amélioration de pertinence : clustering des goûts / embeddings optionnels ;
6. éventuellement Item2Vec/EASE si suffisamment de données comportementales deviennent disponibles.

## Références Git

Merge performance/restitution progressive :
- PR #5
- merge commit `5b74b8ab3fe218d0f21164f766e72bbbe5979bce`

Merge initial 0.2.1 :
- PR #4
- merge commit `5675f7c45d0ce5dd9b901264372814cf0dd1efdf`
