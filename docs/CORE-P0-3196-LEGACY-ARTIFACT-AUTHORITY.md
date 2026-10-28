# Core P0 #3196 — SHA legacy ≠ Artifact vérifié

`Storage._mirror_backup_row()` maintient l'historique Kobo 0.7 (`backup_items`, `entitlements`, `acquisition_ledger`) pendant la transition. La présence d'un champ `sha256` ou `size` dans cet historique n'atteste **pas** les octets finaux. Le miroir ne pré-insère désormais **aucun** enregistrement `artifacts` à partir de cette seule déclaration.

## Invariant

Les anciennes références `sha256:<digest>` peuvent subsister dans un `Placement` **unverified** et un checkpoint de ledger pour la compatibilité et les diagnostics, mais elles ne sont ni un `OriginLink(provider)`, ni un Artifact final validé, ni une preuve de satisfaction locale. Seul le scan physique vérifié ou le publisher canonique Core post-GET produit la description autoritative `Artifact(SHA, taille, format)`.

Ainsi, une ancienne ligne déclarant la bonne empreinte mais une taille ou un format faux ne peut plus bloquer une nouvelle acquisition, même si le nouvel Artifact a exactement le même SHA. Une source/compte distinct conserve ses propres SourceReference/OriginLink et Placements.

## Régression

`tests/test_legacy_backup_canonical_write_fence.py` démontre l'absence d'Artifact fantôme pour un fichier absent, puis l'acceptation d'une publication atomique réelle du PDF même quand la projection 0.7 avait mensongèrement déclaré un EPUB et une taille incompatible. Les protections contre l'écrasement de Placements vérifiés et la conservation des receipts historiques restent testées.

Ce lot n'abandonne pas encore les anciens Placements unverified, le catalogue de compatibilité, le writer `backup_put` ni la migration de 0.7/0.8 : #3196 demeure ouvert. Aucune suppression de données, aucun appel fournisseurs live et aucun P2.
