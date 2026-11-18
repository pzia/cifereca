# #4982 — contrat de maintenance du journal

Statut : port explicite, non activé. Le journal Notice C ne stocke
ni notices, ni corps HTTP, ni droits. Le plafond reste 2048 clés.
La maintenance retire au plus 32 lignes par appel, dans la base
canonique, après un délai minimal de 24 h plus la durée maximale
d'une lease (300 s) depuis le dernier claim. Ce délai est une décision
conservatrice de rétention, non un SLA.

## Invariants

- Uniquement états durables current/stale/error, sans lease active,
  et aucune référence exacte (clé + génération) depuis une autre ligne,
  y compris terminale. Une suppression de feuille peut libérer ses
  dépendances à l'appel suivant. Pas de suppression de pending ;
  l'ordonnanceur doit d'abord résoudre les leases périmées/dépendances.
- Première suppression : initialisation atomique du compteur global
  dans la table metadata, hors du préfixe des jobs. Avant cette
  transition, aucune suppression via le journal n'est admise.
- Les générations existantes continuent à avancer par clé. Toute
  insertion d'une clé absente après transition prend une génération
  strictement supérieure à toutes les générations émises jusque-là.
  Le CAS de demande sur clé absente reste expected_generation=0,
  mais la génération retournée n'est plus nécessairement 1.
- Le compteur ne baisse jamais, y compris après retrait de toutes
  les lignes. Aucun tombstone par clé. Compteur et publication du job
  sont atomiques ; compteur d'initialisation et suppressions aussi.
  En cas de corruption ou overflow, refus sans suppression.
- Après retrait, une ancienne JobRef ne peut plus être lue ni
  compléter une lease. Re-demander une cible absente nécessite une
  nouvelle admission, et peut entraîner une recomposition idempotente.
  La déduplication durable ne porte que sur les entrées retenues.
- Aucun DELETE des sources, C, N2/N3, cache HTTP, fichiers ou droits.
  Aucun fetch de secours, timer, worker ou callback.

## Convergence et exploitation

Le port interne `_allocate_generation_txn(db, previous)` doit rester
dans l'unique writer de demande, y compris lors de la convergence du
refactoring `_request_job_txn` de #6567 et des PR consommatrices.
Ne pas remettre `generation + 1` en place pour une clé absente.
Ne pas supposer génération=1 pour une création après maintenance.
La maintenance n'est pas activée par défaut, ni au démarrage :
un futur tick explicitement autorisé doit la soumettre au même budget
maintenance de fond, hors GTK. Mesurer le coût de lecture/validation
de 2048 lignes et le traitement des DAG profonds avant activation.

**Compatibilité :** la migration est additive et sans conversion des
JobRef présentes, mais la suppression rend incompatible tout ancien
writer ignorant le compteur. Pas de déploiement mixte ancien/nouveau
sur la même base après premier recycle, ni de downgrade sans traitement
préalable ; la restauration d'un snapshot antérieur n'est pas une
garantie anti-ABA au-delà du snapshot. La corruption ou la suppression
manuelle des métadonnées n'est pas une procédure de récupération.
La recette de charge sur base intégrée et la réception six vues
restent nécessaires avant production.
