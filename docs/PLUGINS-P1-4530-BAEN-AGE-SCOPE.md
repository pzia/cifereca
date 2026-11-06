# P1 #4530 — Baen : descriptif produit sans âge d'édition (06/11/2026)

## Contrat observé dans l'adaptateur

Le parseur de page publique Baen (`BaenSession._detail_metadata`) extrait uniquement l'auteur, la date affichée et les ISBN déclarés. Il **ne qualifie pas de tranche d'âge** pour une édition numérique. La projection `_enrich_item_from_detail` conserve ces faits et leur provenance `baen.public_product_page`, mais un ISBN de page produit ne prouve ni ISBN numérique exact, ni âge de lecture.

Tests synthétiques hors réseau : titres « For Ages 8-12 », « ages 6+ », corps de page « Recommended age: 8 to 12 », « Young Adult, Ages 13+ », « Grade 2 » et ISBN affiché. Aucun `recommended_age`, `youth_age_ranges`, `attested_age_ranges`, `attested_age_status`, `public_age_range_label` ou `isbn_digital` n'est induit.

| Source | Pages Baen réelles examinées pour l'âge | Variantes de page synthétiques | Âge numérique attesté par édition |
| --- | ---: | ---: | ---: |
| Baen Free Library et fiche produit | 0 | 2 | 0 |

**Aucun taux de couverture réel n'a été mesuré.** Le thème, une catégorie, un résumé ou l'âge d'un personnage ne prouvent pas une classe de lectorat. Un échantillon public borné et horodaté, avec correspondance ISBN/support et champ explicite vérifié, est à réaliser séparément avant toute normalisation ou transmission au contrat C #4552. Aucun appel compte, réseau, téléchargement, modification de droits ni achat n'a été effectué.
