# Core P0 #3196 — W5 : import SQLite v2→v3 des états Kobo passifs

> **Mise à jour #6152 (15/11/2026) — contrat actuel, tous fournisseurs.**
> Le v2→v3 est maintenant une **migration SQL de schéma uniquement** :
> elle conserve chaque `backup_items` historique, sans convertir aucun
> fournisseur (Kobo, Paris, Numilog, inconnu) en `entitlements`,
> `acquisition_ledger`, `artifacts`, `placements` ou `catalog_entries`.
> Aucun indice de reprise, statut `complete`, SHA ni chemin non vérifié ne
> peut constituer une attestation A ou B. Le mécanisme est sous savepoint :
> un échec de version laisse l'ancien v2 intact, et un nouveau démarrage
> reprend sans importer de droits. Test 4 fournisseurs × 4 statuts,
> conservation SQL de l'archive et réouverture. La suite du document décrit
> **l'ancienne coupure partielle** ; la phrase « autres fournisseurs
> inchangés » n'est plus valable à partir de #6152. La migration historique
> Config 0.8.1 et la réparation d'identités SQLite v5 restent distinctes.
> Aucun fichier utilisateur ni ligne d'archive n'est supprimé.


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
  stratégie, exécuteur, job, profil ADEPT, emprunt ni échéance ; cette
  classification s'applique uniquement à une ligne `owned`.

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
