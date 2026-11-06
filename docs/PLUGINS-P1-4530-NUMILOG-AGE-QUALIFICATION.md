# P1 #4530 — Numilog : preuves d’âge publiques (06/11/2026)

## Contrat observé

Le parseur public Numilog lit `Book.typicalAgeRange` pour une unique URL canonique, puis stocke le littéral dans `public_age_range_label`. La provenance garde `source_url`, `source=public_product_jsonld_Book` et `edition_proven=false`. Le libellé n’est **pas** une borne d’âge attestée pour l’édition numérique.

Tests synthétiques hors réseau : « 12-15 », « À partir de 13 ans », « Young adult », « 8+ », « 13-99 » restent des libellés; aucun `recommended_age`, `youth_age_ranges`, `attested_age_ranges` ou `isbn_digital` n’est produit, y compris après projection vers un achat. Les valeurs absentes, structurées ou non textuelles restent inconnues. Deux produits homonymes aux URL distinctes conservent des preuves distinctes ; une provenance de l’autre URL est rejetée.

## Couverture et limites

| Source | Dénominateur | Âges littéraux | ISBN numérique exact prouvé | Âge numérique par édition |
| --- | ---: | ---: | ---: | ---: |
| JSON-LD synthétique, à partir de HTML de recherche enregistré | 5 libellés et cas négatifs | 5 cas positifs | 0 | 0 |
| Pages Numilog réelles vérifiées pour ce champ dans cette tranche | 0 | inconnu | inconnu | inconnu |

Ces chiffres **ne mesurent pas la couverture du catalogue Numilog**. Aucun GET supplémentaire, login, achat, prêt, téléchargement ni échantillonnage réel n’a été exécuté. Les libellés de test sont en français et en anglais ; aucune conclusion par langue n’est permise. La date qualifie les tests, non les notices sources.

Étape suivante de #4530 : échantillon réel borné, horodatage et URL par produit, `isbn`/`gtin13`, support et édition exacts, présence/absence du champ, dénominateur et contradictions. Ne pas transférer l’âge par titre/auteur, genre ou ISBN papier ; ne pas interpréter 99 comme une borne réelle. La normalisation et la consolidation restent soumises à #4552.
