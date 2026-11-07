# P0 #4858 — D1, aperçu Kobo connecté, source canonique A→B

Lorsqu'un compte Kobo connecté dispose de l'ABI Core complète,
`kobo_backup_preview(refresh_manifest=True)` ne consulte plus les archives
`backup_items`, leurs chemins ou leurs compteurs pour l'état de ce compte.

La lecture à chaud emprunte `read_account_rights_snapshot` (ressources A
authentifiées, compte/borrower exact et source active), puis
`plan_existing_verified_presence` (OriginLink fournisseur et Placement
du **même profil**, présence physique SHA/taille revalidée). Le résultat
reste un diagnostic **sans** consentement d'acquisition ni téléchargement.

- `already_local_verified` exige une vraie preuve Core B (pas un nom
  de fichier, checksum 0.7 ni un rapprochement manuel).
- Candidats/déclarations/incohérences restent `needs_reconciliation`,
  sans devenir un droit ou un veto autonome du moteur canonique.
- `missing_to_download` / `pending` deviennent des **propositions**
  `potentially_missing`, jamais une autorisation de GET.
- L'état inactif ou archivé provient du snapshot/plan actuel, pas d'un
  statut 0.7 conservé. Un rebind de session ou une lecture A indisponible
  produit un refus sans fallback aux archives.
- L'aperçu **offline** et le préflight privé W2 sur ABI incomplète
  conservent leur compatibilité historique, non autoritaire pour A/B.

Les compteurs de l'ancienne API sont conservés pour l'UI. Dans l'aperçu
canonique, `unlinked_local_books=0` signifie **aucun nombre legacy
calculé**, pas une preuve d'absence de fichiers non liés ; l'indicateur
`unlinked_local_books_observed=False` explicite cette limite.

Tests : `tests/test_core_p0_4858_d1_canonical_preview.py`, cas
présent attesté, inactif, archivé, candidat, incomplet, rebind et
A fournisseur indisponible, zéro accès au backup ou GET legacy.

**Ne clôt pas #4858** : les voies ABI incomplètes, la relance au
démarrage D4 et les autres lecteurs W2 doivent encore être migrés.
