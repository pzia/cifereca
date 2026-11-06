# P1 #4530 — Numilog : quatre fiches publiques réelles, âge et EAN édition

**Observation ciblée du 6 novembre 2026.** Sources : quatre pages produit publiques `www.numilog.com`, sans session ni action d'achat. Relevé minimal réutilisable dans `src/plugins/provider-numilog/tests/fixtures/public_age_sample_20261106.json` et validation par `test_p1_4530_numilog_observed_public_age_sample.py`. Les pages ne sont pas copiées intégralement, seuls les faits nécessaires sont conservés.

| Produit (ID) | Signal d'âge **tel que publié** | Origine du signal | ISBN/EAN **papier** | EAN **EPUB** |
| --- | --- | --- | --- | --- |
| [La Lignée](https://www.numilog.com/1834309/les-loups-garous-de-thiercelieux--la-lignee.ebook) (1834309) | 12-15 ans | **Nom de collection** Castelmore | 9782387034915 | 9782387031594 |
| [La Confrérie du Loup](https://www.numilog.com/1669757/les-loups-garous-de-thiercelieux--la-confrerie-du-loup--roman-officiel.ebook) (1669757) | 12-15 ans | **Nom de collection** Castelmore | 9782362316630 | 9782362317378 |
| [Tous Champions ! Lamine Yamal](https://www.numilog.com/1891837/tous-champions--lamine-yamal.ebook) (1891837) | À partir de 8 ans | **Texte descriptif** Hugo Jeunesse | 9791042904487 | 9791042909062 |
| [Tous Champions ! Charles Leclerc](https://www.numilog.com/1891796/tous-champions--charles-leclerc.ebook) (1891796) | À partir de 8 ans | **Texte descriptif** Hugo Jeunesse | 9791042904470 | 9791042909079 |

## Dénominateur et résultats

- **4 fiches produit réelles**, françaises, sélectionnées **intentionnellement** pour avoir des mentions positives d'âge : ce n'est **pas** un tirage aléatoire du catalogue.
- **4/4** fiches affichent un EAN EPUB explicite, distinct de l'EAN papier ; les huit EAN passent la validation ISBN-13. Cette correspondance concerne le **produit numérique affiché**, pas la preuve que son texte descriptif est un âge attesté pour l'édition.
- **2/4** signaux sont des noms de collection ; **2/4** sont des phrases de description éditoriale. **0/4** peut être promu comme champ numérique attesté à partir de cette seule observation. « 12-15 ans » n'est pas une preuve d'un âge maximum ; « À partir de 8 ans » est un conseil éditorial, pas une permission d'achat/emprunt.
- La présence ou l'absence de `Book.typicalAgeRange` dans le JSON-LD **n'a pas été vérifiée par les pages textuelles utilisées** ; ne pas déclarer ce champ absent. Ce constat est distinct du test JSON-LD synthétique de la tranche #4697.

## Implications

Le fournisseur peut déjà publier **sur la même page produit** un EAN numérique exact et un signal d'âge éditorial. Ce sont deux preuves de **rôles différents**, pas une association autorisant le calcul de `youth_age_ranges`. Une collecte structurée future doit enregistrer `source_url`, champ, rôle/portée, langue, date, support/ISBN et la possibilité de contradictions, puis exiger une qualification explicite avant toute promotion canonique.

Ne pas généraliser les proportions de cet échantillon : les notices sans indication d'âge, autres langues et formats n'ont pas été échantillonnés. Aucune requête par résultat, connexion, téléchargement ni mutation n'a été ajouté ; consolidation C sous #4552.
