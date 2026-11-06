# P1 #4673 — recherche Paris : provenance alignée sur l'identité active

## Cause corrigée

Les chemins `search` (synchrone) et `search_progressive` conservaient auparavant **la première session connectée par `provider_id`**, même si cette session Paris appartenait à un autre profil. La notice reçue depuis Paris B conservait logiquement `source_credential=B` : le bouton Emprunter de A était ensuite **correctement refusé** par les gardes locales, alors que le compte A était connecté et éligible à la recherche. Changer l'ordre de connexion B/A modifiait la provenance. Il ne faut pas masquer ce défaut en supprimant la garde d'emprunt.

## Contrat corrigé

Pour un fournisseur donné, choisir la première session **active dont le credential est explicitement lié à l'identité sélectionnée** (`storage.identity_for_connection`) ; si aucun rattachement actif n'est attesté, conserver le premier fournisseur connecté pour les catalogues publics, **sans réécrire son `source_credential`**. Ne jamais mélanger deux sessions ou accorder l'éligibilité d'un autre compte. Conserver l'ordre fournisseur, les gardes de search lease, les générations de profil et le cache contenant le credential réellement sélectionné.

Le même helper est utilisé avant `_without_redundant_merged` et avant le calcul des clés de cache dans les deux parcours. Un changement de A→B→A produit trois recherches logiquement distinctes, chacune avec sa provenance exacte ; le cache existant partitionne par identité et par session (credential). Un résultat arrivé sous B ne devient jamais une possibilité de prêt A.

## Recette autonome

Tests hors réseau sur deux sessions Paris et deux profils : ordre de connexion inversé, `search` et `search_progressive`, A→B→A, seconde lecture avec cache, disparition de la session A et propriétaire du credential inconnu. Aucun POST, aucun simulateur de permission fournisseur, aucune donnée de compte privé.

**Limites :** la disponibilité affichée reste celle du catalogue sélectionné ; l'admission réelle côté fournisseur (quota, titre, restrictions, prêt en cours) ne peut pas être attestée par le seul `source_credential`. Les parcours d'emprunt interprofils #4662 et la capacité fournisseur compte-spécifique #3120 restent distincts.
