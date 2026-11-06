# Core P0 #3196 — W5 : import SQLite v2→v3 des états Kobo passifs

La migration historique vers le ledger générique 0.8 appelait
`_mirror_backup_row` pour **tous** les `backup_items` antérieurs.
Ainsi, même un livre explicitement archivé par Kobo ou un `pending`
jamais commencé devenait un `entitlement` actif et une acquisition
générique, sans observation fournisseur indépendante.

Cette coupure du seul **import v2→v3** garde intact chaque
`backup_items` (clé, compte, révision, statut, chemin, SHA, erreurs,
horodatages) mais ne crée aucune projection générique pour :

- `kobo/skipped_archived`, y compris avec chemin ou SHA historique ;
- `kobo/pending` et `kobo/queued` strictement passifs : zéro tentative,
  aucun horodatage de tentative, chemin, SHA, taille, erreur, attention,
  stratégie ou exécuteur.

Dès qu'un `pending/queued` porte **un indice de reprise**, l'ancien
ledger reste temporairement disponible pour W2/W4 : cette tranche
n'efface pas une acquisition interrompue. `complete`,
`reconcile_required/conflict` et les autres fournisseurs sont
inchangés. Migration transactionnelle et répétable, aucun accès
aux fichiers, aucun GET et aucune SourceReference/OriginLink inventée.

L'archive n'est jamais une autorité positive ; l'admission Core v2
requiert toujours un inventaire fournisseur frais, un compte exact
et un reçu de livraison. Les autres imports W5 restent à migrer avant
la fermeture de #3196.

Recette : `tests/test_core_p0_3196_w5_passive_v2_migration.py`
sur vraies bases v2 synthétiques, conservation intégrale des lignes,
aucune projection passive, coexistence Kobo/tiers, cas de reprise
et réouverture idempotente.
