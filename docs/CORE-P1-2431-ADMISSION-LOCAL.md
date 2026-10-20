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
