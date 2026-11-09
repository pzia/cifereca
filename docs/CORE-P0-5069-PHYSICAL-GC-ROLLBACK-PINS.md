# P0 #5069 — protéger les générations Core référencées avant le GC physique

Le GC Core déjà actif (`updater._gc`) applique 24 h glissantes + minimum deux générations et connaissait les pointeurs `current/previous`, ainsi que `before_previous` de la transaction associée à `current`. Il pouvait supprimer une ancienne release référencée par une autre transaction encore retenue.

Avant toute suppression, `_gc` réutilise l'inspection Core des transactions et son plan commun de rétention : les transactions des dernières 24 h, les deux dernières au-delà de la fenêtre et les transactions épinglées déterminent les références Core transitives. Chaque release portant le motif `referenced` est protégée du GC physique.

Une preuve de transaction manquante, contradictoire ou une référence disparue suspend entièrement ce GC plutôt que d'effacer des dépendances. Une transaction en cours diffère toujours les suppressions.

Tests synthétiques : sept transactions récentes protégeant chacune une release ancienne ; sept transactions anciennes dont les deux dernières conservent leurs releases ; disparition manuelle de `before_previous` refusée.

Limites : ce lot ne purge pas transactions, versions plugins, snapshots, backups ni staging. Ces magasins réclament encore un graphe de références complet et une maintenance sous lease/revalidation/audit/crash-recovery. #5069 demeure ouvert tant que ces critères ne sont pas satisfaits.
Exception strictement attestée : un `before_previous` absent n'est plus une dépendance restaurable si et seulement si la transaction porte `reconciled_degraded` et le reçu privé de réconciliation est lisible sans suivre de lien, cohérent avec l'original et la validation explicite. Toute preuve absente, falsifiée ou liée symboliquement diffère le GC. Le reçu original reste intact dans l'audit.
