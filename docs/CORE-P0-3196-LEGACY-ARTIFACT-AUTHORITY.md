# Core P0 #3196 — Artifact legacy réparable uniquement après preuve des octets

Le miroir Kobo 0.7 `Storage._mirror_backup_row()` garde temporairement ses placeholders historiques dans `artifacts`, `placements` et `acquisition_ledger`. Cette compatibilité demeure nécessaire au parcours explicite, hors ligne et réversible de rapprochement Kobo (`#1345`), qui compare des snapshots SQL sans écriture lors du preview. **Un placeholder `{"imported_from":"backup_items"}` n'atteste pas les octets**, le droit ni une origine provider.

## Contrat de promotion

Après téléchargement autorisé et vérification indépendante du fichier final dans son profil, le seul publisher Core `publish_completed_acquisition` peut corriger dans **sa transaction atomique** le format et la taille d'un Artifact dont l'empreinte SHA est déjà correcte, mais que la projection 0.7 avait renseigné avec des données fausses.

La correction est **strictement bornée** aux placeholders marqués exactement `{"imported_from":"backup_items"}`, sans Placement autre que `unverified`, sans OriginLink référent et sans variante utilisant le SHA comme source ou résultat. Tous les autres désaccords restent `canonical_artifact_collision`. À l'issue, métadonnées legacy retirées au profit du SHA, format et taille physiquement vérifiés. Aucun nouveau droit n'est inféré : seuls le SourceReference exact et l'OriginLink issu du témoin de livraison attestée sont créés par le publisher.

## Régressions

`tests/test_legacy_backup_canonical_write_fence.py` couvre le rapprochement compatible, la publication réelle d'un PDF après fausse taille/extension EPUB v0.7, la conservation de l'ancien historique, et l'interdiction de réécrire un Artifact déjà lié à un OriginLink réel même en présence du marqueur legacy.

L'alternative de supprimer immédiatement toute création de placeholder v0.7 a été rejetée par la CI : elle bloquait le preview/undo Kobo existant (41 régressions). Cette adaptation conserve cette compatibilité tout en restaurant la priorité des octets vérifiés au moment de la publication. La migration complète 0.7/0.8 et le retrait des writers/consommateurs `backup_items` restent à faire sous #3196. Aucun P2.
