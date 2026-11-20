# #1886 — Réception bornée Numilog public

## Preuves disponibles et limites

- 16/10/2026 : `public_search_jefferson_20261016.html`, 20 cartes sur 242 annoncées, métadonnées et facettes structurées, liens vers d'autres pages. Pas de capture supplémentaire de ce même parcours.
- 17/10/2026 : `observed_search-loup-0.html` et `observed_search-loup-1.html` sont deux extraits DOM réduits de réponses publiques HTTP 200. Même total annoncé 973, une carte distincte conservée par page, marqueurs de page et lien suivant. Ce ne sont pas des pages intégrales.
- 17/10/2026 : `observed_search-empty.html`, marqueur explicite Aucun livre trouvé avec ces critères, sans compteur numérique. Les cas structurels à une carte sont synthétiques, non des résultats uniques observés.

## Recette

`test_p0_1886_observed_public_transport_receipt.py` rejoue ces fixtures via le vrai NumilogPublicSession, le client public et le parseur, transport HTTPX simulé. Navigation 0→1 par lien observé, deux GET simulés, filtre local sans GET supplémentaire, couverture partielle 2/973, droits absents. Enveloppes 0/N, erreurs simulées 401/403/429/500/503 et refus des cinq champs avancés avant transport. Les tests existants conservent les formats, identifiants par produit, preuve 1 synthétique et cas négatifs.

## Limites explicites

Aucun nouveau GET réel, aucune capture avancée, aucune autorisation robots/CGU établie, aucun résultat global filtré. Le fournisseur reste field=all ; les filtres locaux ne sont pas des champs serveur. Les deux extraits loup ne permettent pas de mesurer la stabilité du catalogue ni une pagination complète. La qualification du parcours simple est distincte de la décision sur le reliquat avancé et des preuves GTK. Pas de clôture avant réception des gates et arbitrage des critères restants.
