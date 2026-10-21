# Core P1 #2431 — admission locale, première tranche réversible

## Contrat livré

- `PriorityExecutor(max_pending=N)` limite les **tâches en attente**, pas les tâches déjà lancées. Admission sans attente : file pleine → `RuntimeError("executor pending queue full")`. Aucun accès au réseau ni à GTK pendant le contrôle d'admission.
- `max_pending=None` est la valeur par défaut, **comportement historique conservé**. Aucun nombre de threads supplémentaires, aucune limite active imposée sans activation explicite.
- L'option est propagée facultativement par `TaskRunner` (actions GTK) et `TaskBrokerImpl` (opérations Core/plugins), à configuration indépendante. Le `TaskRunner` existant transforme les refus `RuntimeError` en retour UI générique et libère contrôle, réservation et compteur.
- `admission_snapshot()` retourne exclusivement `pending`, `active`, `admitted`, `rejected`, `max_pending`, `closed`, jamais les libellés, clés de coalescence, propriétaires, titres, URLs ni identités. Aucun export de métriques persistantes implicite.
- Une tâche annulée dans la file ne sera pas exécutée. Son entrée occupe un emplacement *physique* jusqu'à son retrait par un worker ou le shutdown : la mémoire de la file reste bornée en mode plafonné, sans compaction risquée de heap.
- `shutdown(cancel_futures=True)` supprime les entrées restantes ; les callbacks de `Future.cancel()` s'exécutent **hors verrou** pour ne pas bloquer l'observation ou une tentative de nouvelle soumission.

## Limites intentionnelles

Ce budget est **local au pool**, pas encore une admission globale I/O/CPU, ni un quota par fournisseur/compte/hôte. Le cap n'est pas activé globalement par défaut, faute de seuil p95/charge mesuré sur le poste réel et de règle d'éviction donnant priorité aux actions utilisateur. La priorité des éléments *admis* reste intacte. Une action utilisateur arrivant après saturation est rejetée ; il reste à définir une politique de réservation/éviction avant de l'activer en production.

Il reste à #2431 : gouvernance partagée entre pools, anti-famine, budgets par source, comparatif p50/p95 CPU/RSS et activation progressive ; coordination #1741. Aucun gain chiffré supposé.

## Vérifications

Les tests Core vérifient saturation réelle d'un worker bloqué, priorité de lecture de la file, absence d'exécution après annulation, refus sans fuite dans le broker, callbacks de shutdown réentrants et comportement non borné par défaut. Un passage CI Core est requis avant fusion.

## Sous-lot observabilité des refus (#2431 / #1741)

`SharedTaskAdmission.snapshot()` complète le compteur historique `rejected`
par **cinq compteurs entiers agrégés**, cumulatifs pour la durée de vie du
budget local :

- `rejected_global` : tous les emplacements en vol occupés ;
- `rejected_reserved` : refus d'une tâche de fond sur la réserve dédiée à
  une action utilisateur, avant saturation complète ;
- `rejected_scope` : quota d'une portée privée saturé ;
- `rejected_user` / `rejected_background` : classe du demandeur refusé.

Pour toute demande refusée, une seule des trois causes
`global` / `reserved` / `scope` est incrémentée, de même qu'une seule
classe `user` / `background`. On a donc toujours
`rejected = rejected_global + rejected_reserved + rejected_scope`
et
`rejected = rejected_user + rejected_background`.
La priorité des causes en cas de saturation simultanée est
**global > réserve > portée**. Les compteurs ne révèlent pas quel compte,
quel fournisseur ou quel plugin a consommé les emplacements.

**Aucun changement** de budget, d'ordonnancement, d'autorisation, de rejet,
de stockage ou d'API externe. Les compteurs sont process-local, monotones,
sans traces de la requête et ne forment pas un quota réseau inter-processus.
Leur observation n'attribue pas à elle seule une attente ou un gain de
performance ; le diagnostic p50/p95, CPU/RSS et l'activation globale restent
à étudier dans #1741.
