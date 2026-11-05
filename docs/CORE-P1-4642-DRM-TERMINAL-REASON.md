# P1 #4642 — motif terminal distinct pour un EPUB encore protégé

Suite technique non bloquante de l'incident **#3252**, clos sur preuve terrain le 05/11/2026 (515 présents attestés, 0 failed, 3 pending). Ce lot n'a pas causé la résolution des trois acquisitions Kobo.

## Décision propriétaire 1A (05/11/2026)

Un **EPUB structurellement valide**, d'un format attendu, validé par le
catalogue, mais encore signalé `inspection.protected=True` après la
livraison Core (même si l'exécuteur KDRM a retourné `ok=True`) est une
**livraison échouée pour `drm_unresolved`**, et non un téléchargement
attesté. Il demeure `failed` dans `canonical_execution` ; il ne crée
ni `CompletedDelivery`, ni OriginLink, ni Placement, ni droit.

Le diagnostic local de l'ancienne erreur
`canonical_delivery_final_invalid` reste émis, avec les champs
`inspection_*`, `catalog_*` et `encryption_profile` issus de la
validation du fichier. Son erreur Python reste un `ValueError` compatible,
mais avec un type Core `CanonicalDrmUnresolved` utilisé **uniquement**
lorsque les quatre prédicats précédents sont satisfaits. L'exécuteur
A→B convertit ce type interne en motif fermé `drm_unresolved` dans
l'événement terminal `TRACE canonical_flow`.

Pour les refus réseau, runtime, candidats invalides, format incorrect,
inspection structurelle invalide ou catalogue invalide, le motif terminal
historique `canonical_delivery_failed` demeure. Il ne doit pas être
reclassé en DRM à partir d'un retour runtime `0`, d'un suffixe EPUB ou
de métadonnées fournisseur. Les éventuels statuts d'affichage plus
granulaires, notamment un libellé explicite `Échec de téléchargement`
dans CoreUI, constituent la suite du même ticket, **non livrée par cette
tranche Core** ; ne pas promettre une ventilation du compteur public.

## Recette de non-régression

- Deux EPUB réels synthétiques avec marqueurs AES et ADEPT, livrés via
  une route runtime attestée par le callback Core ; un EPUB AES livré
  par une route directe HTTPS simulée ; les trois doivent produire
  `canonical_execution.failed=1` et
  `TRACE canonical_flow ... phase=terminal ... reason=drm_unresolved`.
- Un échec de transport simulé reste
  `reason=canonical_delivery_failed`.
- Aucune nouvelle autorisation ni GET n'est inférée de la classification ;
  staging de tentative nettoyé, zéro preuve B, zéro référence catalogue.
  Les tests de livraison EPUB lisible/idempotente déjà livrés par #4636
  restent les critères positifs.

**Limite terrain :** les trois éditions Kobo historiques ont été
livrées avec succès depuis, et une seconde Sync a confirmé 515 présents et
0 échec. Leur `encryption_profile` au moment des anciens refus n'est pas
connu ; cette amélioration n'est **pas** une correction causale de ces
éditions. Les trois `pending` demeurent des offres distinctes et ne sont
pas autorisées au téléchargement forcé.
