# Core P0 #3196 — W3 retiré : aucun prune des archives Kobo 0.7

**Cutover W3 du 06/11/2026.** Le manifeste Kobo, même complet et admis,
n'est jamais une autorité de suppression de la projection historique
`backup_items`. Le synchroniseur ne déclenche plus
`_prune_successful_manifest` : la fonction et le plan de prune Kobo ont
été retirés. `Storage.backup_prune_missing("kobo", credential, seen_ids)`
reste une API de compatibilité **strictement sans lecture ni écriture
SQLite**, avec résultat `pruned=0, local_preserved=0,
entitlements_inactivated=0`, pour tout manifeste valide.

Les lignes v0.7 sont désormais **des archives / checkpoints de reprise**
tant qu'elles n'ont pas été migrées sur une provenance Core exacte,
transactionnelle et rejouable. Cette conservation inclut les lignes
`pending` sans chemin, les reçus `complete`, les interruptions,
`reconcile_required/conflict`, les SHA/taille sans chemin, les révisions
anciennes, les 403 de délégation et les 409 archivés. Une absence au manifeste
ne garantit ni disparition des octets, ni révocation, ni révision identique,
ni identité/compte courant. Aucune copie et aucun droit ne sont créés par ce
retrait. Les statistiques de synchronisation `legacy_pruned` et
`legacy_local_preserved` restent présentes, à zéro, pour compatibilité UI.

## Frontières conservées

- La validation des IDs soumis à `backup_prune_missing` reste fermée :
  `set/list/tuple`, 100 000 entrées au maximum, chaînes non vides,
  1 024 caractères au maximum, aucun NUL/CR/LF ; un payload invalide
  lève `ValueError` **avant tout accès SQLite**.
- Pour les **autres fournisseurs**, le prune historique reste opérationnel
  sous `BEGIN IMMEDIATE`, uniquement sur `backup_items` ; les droits,
  `acquisition_ledger`, `artifacts`, `origin_links` et `placements`
  ne sont pas modifiés. Leur comportement antérieur n'est pas retiré.
- La lecture des archives Kobo, les gardes contre un second GET ADEPT et
  la récupération explicite des acquisitions non migrées restent actives
  pendant les prochains cutovers W1/W2/D1–D5 et W4/W5.
- Aucun `DROP TABLE`, retrait physique de fichier, inférence de droit,
  ou réécriture arbitraire de la révision historique n'est autorisé.

## Preuves et portée

`tests/test_core_p0_3196_w3_prune_retired.py` vérifie le non-accès
SQLite par cette API et l'absence d'appel au prune dans la synchronisation.
Les tests W3 existants vérifient maintenant que même les lignes
apparemment obsolètes survivent aux manifestes complets et partiels.
`tests/test_core_p0_3196_prune_exact_right_scope.py` couvre le
comportement préservé des autres fournisseurs.

Ce retrait constitue **une véritable coupure W3**, mais **pas** la fermeture
du P0 #3196 : `Storage.backup_put` et l'exécuteur 0.7 restent actifs
pour les sessions sans ABI canonique complète ; le rapprochement W4 et la
relocalisation W5 requièrent encore migration, reprise et preuve exacte.
