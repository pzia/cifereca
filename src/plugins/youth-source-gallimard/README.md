# Plugin Jeunesse — Gallimard

Prototype pour [#951](https://github.com/pzia/cifereca/issues/951), conforme au contrat des sources jeunesse `youth.source` employé par Milan et l'École des loisirs.

## HTML fourni

Recherche « loup », avec les filtres **11 ans** et **numérique** :
`https://www.gallimard-jeunesse.fr/catalogue.html?age%5B0%5D=11&format=numerique&text=loup`.

18 résultats / 18 cartes `.BookItem`, chacune avec titre, EAN, âge et contributeurs. ISBN, âge et lien sont validés **dans la même carte**. Les couvertures réécrites localement par le navigateur ne sont jamais publiées comme URL. Seules les images HTTPS du domaine officiel sont admises.

## Contrat

`search(query, cursor=None, limit=40)` et `browse(text_filter='', cursor=None, limit=40, age=None, themes=())`, plus `browse_with_cancellation`. Les recherches distantes sont **strictement explicites** ; `limit` borne les notices sorties, 1 appel HTTP par page, pas de consultation automatique des fiches. Une page suivante n'est proposée que si un lien réel vérifiable est présent (aucun dans la capture).

Les réponses portent `items`, `next_cursor`, `fresh_for_seconds`, `collection_request_count`, `collection_pipeline`. Source `gallimard-jeunesse`. Âges qualifiés `age_kind=publisher`, étiquette originelle préservée. Deux éditions au même titre restent distinctes par ISBN. Aucune langue, format produit, résumé ou couverture publique n'est inféré depuis les filtres, URLs ou images sauvegardées.

## Sécurité / état

- Désactivé par défaut ; l'accès HTTP automatisé n'est **pas qualifié**. Un HTML produit par navigateur n'est pas une preuve d'accès réseau programmatique.
- HTTPS officiel uniquement, sans redirections, `trust_env=False`, timeout, limite 2 Mo, pagination attestée et bornée, aucune API privée, aucun contournement des protections.
- Les HTML sources utilisateurs peuvent contenir scripts anti-bot et télémétrie : seule une fixture purifiée figure dans le paquet.
- À valider dans une vraie installation Cifereca et dans son pipeline cache / GTK avant activation.

## Tests

`python -m unittest discover -s tests -v` dans ce répertoire (`httpx` nécessaire). 10 tests réussis hors réseau via `httpx.MockTransport`.
