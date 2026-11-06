# P1 #4530 — Âge par édition dans les réponses SRU BnF

Relevé public du 6 novembre 2026 sur le Catalogue général de la BnF.
Les [extraits XML](../src/plugins/provider-bnf-catalogue/tests/fixtures/bnf-age-sru/)
conservent les enveloppes SRU, les valeurs bibliographiques utiles et les
identifiants tels que renvoyés. Les commentaires critiques et les champs hors
périmètre ont été omis. Source et licence indiquées dans la [fiche SRU BnF][sru].

## Échantillon ciblé

| Fixture | Édition et requête | Observation dans la réponse |
| --- | --- | --- |
| `age-tendre.xml` | [Âge tendre][tendre], ISBN `9782377314652`, UNIMARC | `010$a` concordant, `101$a=fre`, `200$b` imprimé. `333$a=À partir de 13 ans`, `$2=CNLJ`. Deux `329` de critique distincts. |
| `age-tendre-intermarc.xml` | Même ARK `cb465971480`, INTERMARC | `020$a` concordant, deux `833$m=JAg1399`. Le référentiel donne un minimum de 13 ans sans borne haute. |
| `carre-unimarc.xml` | [Carré][carre], ISBN `9791040120971`, UNIMARC | `010$a` concordant, `101$a=fre`, `200$b` imprimé. `333$a=De 0 à 3 ans`, `$2=CNLJ`. |
| `carre-intermarc.xml` | Même ARK `cb47671506m`, INTERMARC | `020$a` concordant, deux `833$m=JAg0003`. Le référentiel donne les bornes 0 et 3. |
| `baby.xml` | [Mon tout petit bébé][bebe], ISBN `9782324036668`, UNIMARC | ISBN, langue et support concordants. Aucune zone `329` ou `333` dans cette réponse, alors que la page HTML affiche 0 à 3 ans. |
| `conflicting.xml` | [Dans ce monde-là][monde], ISBN `9782362901645`, UNIMARC | HTTP 200, un résultat déclaré et l’ARK attendu, avec un diagnostic SRU `1/131` à la place de la notice. Traitement indisponible dans ce format. |
| `conflicting-intermarc.xml` | Même ARK `cb47584428d`, INTERMARC | `020$a` concordant et deux `833$m=JAg0699`, minimum 6. La notice HTML présente aussi une plage 6 à 9 dans la description générale. Conserver ces deux indications et leur provenance distincte. |
| `age-tendre-ebook.xml` | ISBN numérique distinct `9782377315123`, UNIMARC | Zéro résultat à cette requête exacte. L’observation de l’édition imprimée ne peut pas servir de preuve pour cet ISBN. |
| `homonyms.xml` | Recherche de contrôle par titre `Âge tendre`, UNIMARC | Six notices sur 105 et `nextRecordPosition=7`, dont une notice homonyme sous un autre ARK. Aucune des six n’atteste l’ISBN de l’imprimé recherché. |

## Reproduction et lecture

Les requêtes `searchRetrieve` utilisent SRU 1.2 et `maximumRecords=5`
pour un ISBN ou un ARK, avec `bib.isbn adj "<ISBN>"` ou
`bib.persistentid all "ark:/12148/<ARK>"`. Pour `age-tendre.xml`, le
comparateur observé est `all` sur `bib.isbn`. Le contrôle par titre utilise
`bib.title adj "Âge tendre"` et `maximumRecords=6`. Les schémas demandés sont
`unimarcXchange` et `intermarcXchange`. Tous les appels de cet échantillon
ont renvoyé HTTP 200, et les mesures disponibles vont de 3,9 à 6,2 secondes.
Le pilote lit l’UTF-8 annoncé par la BnF et refuse les déclarations
d’entités avant l’analyse XML.

La sélection porte sur l’ISBN **propre** (`010$a` ou `020$a`) et l’ARK exact.
L’âge CNLJ observé est une indication documentaire de public destinataire.
La zone UNIMARC `329` indique un commentaire critique, tandis que les preuves
positives de ce lot sont dans `333$a` avec `$2=CNLJ` et dans `833$m` en
INTERMARC. Deux zones `833` identiques sur le même ARK comptent comme une
observation, avec les deux occurrences brutes disponibles. Garder séparés
le zéro SRU, l’absence de champ dans une notice exacte, le diagnostic de
traitement et une page partielle. Le suffixe `99` de ces codes signifie une
borne ouverte selon le [référentiel BnF][jag].

Les neuf réponses constituent un échantillon de qualification ciblé.
Les trois positifs proviennent de notices sélectionnées pour leurs indices
d’âge. Une mesure de couverture requiert un échantillon distinct par
langue, support et fournisseur. La consolidation du modèle C et le
raccordement d’un enrichisseur restent dans #4552 et #2385.

[sru]: https://api.bnf.fr/fr/api-sru-catalogue-general
[jag]: https://www.bnf.fr/sites/default/files/2018-11/intermarc_ref_litt_jeun_public.pdf
[tendre]: https://catalogue.bnf.fr/ark:/12148/cb465971480
[carre]: https://catalogue.bnf.fr/ark:/12148/cb47671506m
[bebe]: https://catalogue.bnf.fr/ark:/12148/cb485104342
[monde]: https://catalogue.bnf.fr/ark:/12148/cb47584428d


## Qualification numérique **hors Core** — tranche du 06/11/2026

La fonction pure `bnf_age_evidence_probe.qualified_observed_age_fields(xml, isbn)` construit à partir du contrôle d'ISBN **propre** existant une projection **strictement limitée aux valeurs déjà vues dans les extraits SRU publics** : UNIMARC `333$a` avec `$2=CNLJ` : « À partir de 13 ans » → minimum 13, sans maximum, « De 0 à 3 ans » → 0–3 ; INTERMARC `833$m` : `JAg1399` → minimum 13 sans maximum, `JAg0003` → 0–3, `JAg0699` → minimum 6 sans maximum. Ce sont des indications CNLJ de **public bibliographique destinataire**, pas des niveaux scolaires ni une preuve de droit.

La sortie nomme les bornes `audience_age_min/max` pour **éviter** de créer un `YouthAgeRange` canonique. Elle expose explicitement `edition_support_verified=False` et `core_youth_age_range_authorized=False` : les notices positives observées sont des **supports imprimés**, et le résultat zéro sur l'ISBN EPUB distinct interdit de l'attribuer à la variante numérique. Les répétitions identiques sont dédupliquées ; un code non répertorié, une zone de critique `329`, une absence de marqueur d'autorité CNLJ ou des valeurs contradictoires ne sont jamais promus. Les données MARC brutes restent accessibles par `exact_edition_age_fields()`.

**Portée :** qualification bibliographique bornée sur des documents publics déjà enregistrés, pas mesure de couverture ni branchement dans le catalogue, le filtre local ou la consolidation #4552. L'ajout d'autres codes JAg requiert leur vérification documentaire et des réponses exactes enregistrées. Aucune requête réseau, mutation, acquisition, calcul d'âge depuis un résumé ou transfert papier → EPUB.
