# P1 #4640 — Kobo K1/K2/K3 : corrélation du status existant (Cifereca uniquement)

> **Mise à jour opposable — 07/11/2026, décision propriétaire [#4640](https://github.com/pzia/cifereca/issues/4640)** : les interdictions historiques de normaliser `unknown/flepub` et de préférer EPUB à PDF ci-dessous ne s'appliquent plus. K1 choisit l'unique EPUB **déjà admissible** parmi des offres plain EPUB/PDF ; deux EPUB restent ambigus. K2/K3 : Cifereca seul normalise **uniquement** `media_format=unknown` + `source_format=flepub` (casse/espaces tolérés) pour `http/kobo_kdrm/library_sync` et DRM KDRM reconnu ; le descripteur KVault reste brut. L'ID d'offre, le sélecteur exact, la recette runtime et le diagnostic utilisent la même normalisation ; K3 à deux candidats demeure `pending`. `candidate_shape_counts` conserve les formats bruts, `candidate_kdrm_flepub_normalized_count` indique uniquement le nombre normalisé, et `candidate_normalized_media_format=epub` est une catégorie fermée. Ni autorisation d'acquisition, ni preuve d'octets, ni téléchargement réseau supplémentaire. Le texte restant documente la phase d'investigation antérieure à la décision.


## Problème et portée

Les trois offres réelles sont encore `pending` sur la dernière preuve terrain :
K1 deux candidats admissibles et ambiguës ; K2 un `library_sync/KDRM`
`unknown/flepub` ; K3 deux paires KDRM rejetées. Les observations de
06/11/2026 précèdent l'instrumentation de #4728/#4729/#4733.

**Aucun changement KVault n'est requis ni autorisé pour #4640.** Le client
Cifereca a **déjà** interrogé `GET /api/v2/status` à `connect()` et
mémorise `self.vault_release` et `self.api_version`. Les valeurs sont
filtrées par `_vault_public_version` : un label malformé devient vide.

## Événement produit sans réseau supplémentaire

Quand le préflight exact termine en
`reason=descriptor_candidate_unqualified`, le même
`EVENT kobo selected_offer` inclut désormais :

- `kvault_status_release_at_connect=<version|unavailable>`
- `kvault_status_api_at_connect=<version|unavailable>`

Les deux champs sont ajoutés aux diagnostics **K1, K2 et K3** sans appel HTTP
ou nouvelle divulgation de payload. La version provient de la connexion
**antérieure**, pas du POST du descripteur. Elle **ne démontre pas** quelle
version du Worker a effectivement servi un POST ultérieur : une bascule de
déploiement, une route différente ou l'absence de `release` restent possibles.

Ne pas assimiler ce champ à `producer_contract` ni à un identifiant de
build. Le refus `unknown/flepub`, la sélection prudente de K1 et les profils
de rejet de K3 restent inchangés.

## Vérification ciblée à la prochaine observation autorisée

- **K1** : `candidate_route_comparison`, `candidate_shape_counts`, version
  de status au connect. Ne sélectionner aucune route par ordre ou similarité.
- **K2** : `candidate_kvault_flepub_contract`,
  `candidate_kvault_format_alias`, version de status au connect, puis
  vérifier **sans supposition** l'origine de `media_format=unknown`. Le
  contrat KVault de référence `epub/FLEPUB` ne justifie pas de coercition.
- **K3** : `candidate_rejection_profile_counts`,
  `candidate_shape_counts`, version de status au connect. Aucun GET.
- Une valeur `unavailable` signifie seulement qu'aucune version publique
  syntaxiquement sûre n'a été mémorisée ; pas preuve d'un vieux Worker.

Ne pas redemander le ZIP 06/11 déjà interprété. Pas de synchronisation
massive, pas de nouveau GET, pas d'évolution ni déploiement KVault. Les tests
synthétiques de la tranche couvrent les trois pending, les versions
malformées et l'absence de secret dans le journal.
