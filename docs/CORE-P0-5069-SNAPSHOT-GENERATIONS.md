# P0 #5069 B12 — politique commune des snapshots de transaction

L'inventaire initial utilisait `unit="transaction"` pour chaque snapshot de `transactions/<id>/plugins/`. Puisque chaque transaction ne porte qu'un snapshot, la politique `minimum=2` retenait systématiquement la seule génération disponible **pour chaque transaction**, donc *tous les snapshots historiques* indéfiniment.

La projection de rétention traite désormais ces snapshots comme les générations distinctes d'un **même historique Updater** (catégorie `transaction_snapshots`, `unit="updater"`). Ainsi, sept snapshots âgés de plus de 24 h exposent cinq candidats théoriques ; sept snapshots âgés de moins de 24 h sont tous retenus. Un journal transactionnel conservé garde son snapshot via `references`, y compris quand il est en restauration incomplète ; les anciennes transactions restent elles-mêmes soumises au minimum 2.

**Aucune suppression :** l'inventaire et les plans restent `safe_to_apply=false`. Ce regroupement corrige seulement la projection des minima ; la fermeture de #5069 impose encore la qualification exhaustive du graphe inter-magasins, l'ownership et la maintenance supervisée sous verrou et reprise. Les snapshots inconnus, symlinkés et les références manquantes restent des obstacles à la purge.
