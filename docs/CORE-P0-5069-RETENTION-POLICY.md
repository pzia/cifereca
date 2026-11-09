# P0 #5069 B1 — Politique pure de rétention universelle

## Sémantique commune

Le module `cifereca.updater_retention` calcule un plan déterministe à partir d'un inventaire déjà qualifié. Il n'accède à aucun système de fichiers et **n'autorise jamais la suppression** : `safe_to_apply=false` jusqu'à implémentation de l'inventaire, du verrouillage, de la revalidation et du GC supervisé.

Pour chaque unité `(store, unit)`, conserver l'union :
- Toutes les générations strictement plus récentes que 24 h.
- Les **deux dernières générations distinctes** (aucun plafond de cinq).
- Tout élément actif/épinglé, tout objet d'ownership non démontrée, toute date absente/invalide/future.
- Toutes les dépendances transitives des éléments conservés, y compris transactions, snapshots, backups et versions de plugins.

À exactement 24 h, la fenêtre expire, mais les autres protections continuent. Une référence absente rend **l'ensemble du plan non éligible à l'application** et conserve tous les objets. La granularité `unit` permet deux versions **par package** plugin et au moins deux transactions par historique, sans les confondre.

## Limites délibérées

La tranche B1 est un **moteur de décision pur**, pas un collecteur ni une commande de maintenance. Elle n'affirme pas que tous les magasins réels de l'Updater ont été qualifiés. La suite #5069 B doit inventorier chaque propriétaire, construire le graphe exact, protéger les objets utilisés par des processus, et seulement ensuite fournir `maintenance inspect/plan/apply` sous verrou, contrôles TOCTOU et audit crash-safe. Aucune purge de données utilisateur, profils, SQLite ou fichiers de bibliothèque.