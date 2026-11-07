# P0 #4858 — Cutover du bouton manuel Kobo vers Core A→B

L'action `prepare_local_materialization` conserve sa confirmation unique,
mais **lorsqu'une session Kobo expose un ABI de livraison canonique complet**
et que la politique persistée de cette source exacte est **ON**, elle ne
consulte plus `backup_manifest()`, `backup_items` ou le worker W2.

- **Préparer (lecture seule)** : `read_account_rights_snapshot` lit les
  ressources attestées du fournisseur, puis
  `plan_existing_verified_presence` examine les SourceReference / OriginLink /
  Placements et les octets actuels du profil. Le plan est informatif, pas un
  droit ni un GET. Son token unique lie le compte, la configuration, le contenu
  du snapshot (éditions/révisions/capacité) et la révision du consentement.
- **Confirmer** : token consommé, nouvel inventaire A, même révision du
  consentement et même scope. Après libération du verrou de prévisualisation,
  une seule invocation du **broker Core `synchronize_source`** prend son
  propre verrou et exécute `qualify_selected_offers` →
  `execute_admitted_convergence`, avec ses contrôles indépendants
  de session/credential/borrower, de révocation avant/après GET et de B.
- **Compteurs** : seul le reçu `canonical_execution` exact du Core peut
  produire un `downloaded` ; un Sync ayant seulement rafraîchi le catalogue
  n'est jamais compté comme livraison.
- **Conservatisme** : consentement inexistant/OFF, ABI partiel, snapshot A
  non vérifiable, nouveau droit/révision, changement de scope ou ancien token
  → aucune délégation au Core ni au worker legacy par erreur.

Les sessions Kobo avec ABI incomplet conservent provisoirement leur voie
legacy contrôlée pour éviter une régression destructive. Cette tranche
**remplace fonctionnellement la voie manuelle** des fournisseurs modernes,
mais ne clôt ni #4858 ni le P0 parent #3196 : W2 compatibilité, D1–D4 et
les lecteurs encore autoritaires doivent être coupés après parité prouvée.

Recette : `tests/test_core_p0_4858_manual_canonical_materialization.py`
(aucun GET/backup legacy, snapshot A, token unique, révocation et renouvellement
de consentement, révision fournisseur modifiée, absence de faux succès B).
