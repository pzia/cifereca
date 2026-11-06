# P1 #4530 — Numilog : contrôles négatifs et EAN multi-format (06/11/2026)

Échantillon public **dirigé et non représentatif**. Sources : pages produit Numilog accessibles sans authentification le 06/11/2026. Données minimisées dans `src/plugins/provider-numilog/tests/fixtures/public_age_controls_20261106.json`, assertions hors réseau dans `test_p1_4530_numilog_public_age_controls.py`.

| Produit public | Signal d'âge de lecture dans collection/résumé examinés | EAN papier | EAN EPUB | EAN PDF |
| --- | --- | --- | --- | --- |
| [Les petits meurtres de Loch Down Abbey](https://www.numilog.com/1889226/les-petits-meurtres-de-loch-down-abbey.ebook) | Non observé ; collection « Policier » | 9782290425718 | 9782290425725 | 9782290425756 |
| [La Colline](https://www.numilog.com/1873253/la-colline--grand-prix-des-lectrices-de-elle-policier-2026.ebook) | Non observé ; « dix-sept ans » est **l'âge d'un personnage**, pas du lectorat | 9782021602302 | 9782380200751 | — |
| [Fleur-de-Crime](https://www.numilog.com/954158/fleur-de-crime.ebook) | Non observé ; collection « Policier » | 9782346138913 | 9782346138913 | — |

La première fiche prouve qu'un même produit peut afficher deux **EAN numériques de format différents** (EPUB/PDF) plus un EAN papier ; la troisième montre que **papier et EPUB peuvent porter exactement le même EAN**. Les identifiants doivent rester associés à leur **rôle d'origine** ; il est erroné d'imposer une différence papier/numérique ou d'attribuer automatiquement les deux formats à une édition achetée. Le contrôle avec fausse URL produit et faux titre doit être rejeté.

**Absence observée ≠ preuve d'absence universelle :** seul le nom de collection et le résumé éditorial visibles ont été examinés ici. Le JSON-LD `Book.typicalAgeRange`, les données BnF, le contenu des livres et les champs d'autres éditions ne sont pas qualifiés par ce relevé.

## Résultat cumulé de l'échantillon Numilog intentionnel

La tranche [#4713](https://github.com/pzia/cifereca/pull/4713) portait sur **quatre fiches choisies parce qu'elles affichaient déjà un âge** : deux collections « 12-15 ans » et deux résumés « À partir de 8 ans ». Cette tranche ajoute **trois contrôles sans âge de lecture observé** dans les zones examinées.

| Strate **sélectionnée** | Pages publiques FR | Âge éditorial observé (collection ou résumé) | Aucun âge éditorial observé dans ces zones | EAN EPUB indiqué |
| --- | ---: | ---: | ---: | ---: |
| Positifs choisis #4713 | 4 | 4 | 0 | 4 |
| Contrôles dirigés (présente tranche) | 3 | 0 | 3 | 3 |
| **Total sélectionné** | **7** | **4** | **3** | **7** |

Ces proportions **ne sont pas des taux de couverture du catalogue** : la méthode sélectionne les positives et les contrôles séparément, sans tirage aléatoire ni distribution représentative par langue, support ou fournisseur. Aucun âge numérique canonique n'est attesté par la seule mention commerciale. Le test HTML est une **reconstitution synthétique** des libellés et non une capture de code source du site.

Aucune connexion, requête de suite en CI, acquisition ou mutation de droits ; le contrat de consolidation C est suivi séparément en #4552.
