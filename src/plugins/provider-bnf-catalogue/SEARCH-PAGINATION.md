# Catalogue BnF — pagination SRU (#1917)

Version plugin 1.0.11 : la pagination est **zéro-indexée** et bornée aux
pages entières `0..100` (maximum 101 pages annoncées dans
`ProviderSearchCapabilities.max_pages`). Avec 50 notices par page par défaut,
`startRecord = page * page_size + 1` ; `page_size` est configurable de 1 à
100 notices par page.

Le provider refuse désormais par `ProviderCapabilityError('BnF : invalid_page')`
les valeurs négatives, flottantes, booléennes, chaînes, nulles et les pages
supérieures à 100 **avant toute requête**. Aucun paramètre invalide n'est
réinterprété silencieusement comme page 0. Les erreurs et avertissements de
recherche SRU restent inchangés ; un rejet vide les avertissements précédents.

Cela borne les requêtes et les tests hors réseau, sans prétendre offrir une
exhaustivité BnF, un accès aux prêts numériques ou un autre endpoint API.
