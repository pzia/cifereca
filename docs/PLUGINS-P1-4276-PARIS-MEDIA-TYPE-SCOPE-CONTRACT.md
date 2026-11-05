# P1 #4276 — Paris : support, type documentaire et provenance (préparation P0)

## Périmètre réellement observé

La capture locale du 02/11/2026 (non publiée dans GitHub) montre **deux
réponses CATALOGUE de 50 résultats**, avec les combinaisons suivantes :

| Resource.Type | Resource.Frmt | Resource.RscBase | Nombre | Support démontré | Nature attendue |
| --- | --- | --- | ---: | --- | --- |
| Livre | MONO | SYRACUSE | 39 | physical | book |
| BD | BDTP | SYRACUSE | 30 | physical | comics |
| Musique | AUDI | SYRACUSE | 15 | physical | music |
| Film | AUVI | SYRACUSE | 2 | physical | video |
| Revue | PERI | SYRACUSE | 2 | physical | periodical |
| Livre numérique | MONE | DILICOM | 11 | digital | ebook |
| Livre numérique | MONE | STORYPLAYR | 1 | digital | ebook |

Ces effectifs **ne sont ni un inventaire exhaustif du catalogue, ni un quota,
ni une preuve de disponibilité**. Les tests utilisent uniquement des notices
synthétiques et des noms de champs observés.

## Deux axes distincts

1. **`medium`** : `physical|digital|unknown` qualifie le support matériel,
   indépendamment de la route de recherche. Une vidéo/DVD et un disque musical
   sont des **supports physiques**, mais **pas des livres papier**.
2. **Type documentaire** : un attribut distinct, actuellement proposé sous
   `metadata.document_kind` jusqu'à négociation du modèle Core,
   distingue `book|comics|music|video|periodical|ebook|unknown`. Ne pas
   assimiler `content_type="physical"` au type documentaire. La liste des
   variantes et leur intégration aux filtres CoreUI doivent être contractualisées
   avant généralisation du format de sortie.

`Resource.Frmt` (`MONO/BDTP/AUDI/AUVI/PERI/MONE`) est une **classification
bibliographique Syracuse**, et n'est jamais un codec ou un format téléchargeable.
Les formats `EPUB/PDF/MP3` proviennent uniquement d'une preuve de fichier
explicitement annoncée, séparée de `Frmt`.

`CATALOGUE` et `EBOOKS` ne sont pas des preuves de support : la même
notice peut apparaître dans les deux, y compris une notice numérique dans
CATALOGUE. Les recherches à portée `physical` doivent filtrer les résultats
**par `medium` positif**, et non par provenance `CATALOGUE`. Pour une
portée `digital`, préserver les documents numériques de bases non-DILICOM
sans leur attribuer une capacité de prêt PNB.

La décision UI reste à prendre sur le **libellé** du filtre : si « Physique »
veut dire tous objets matériels, le préciser et offrir une facette documentaire.
Si « Livres physiques » est l'intention, ne pas y classer les DVD/disques ;
toutes les notices restent consultables sous « Tous ». Ne pas convertir
silencieusement les anciens filtres persistés.

## Matrice d'acceptation avant déverrouillage

`tests/test_p1_4276_paris_document_kind_scope_matrix.py` valide
immédiatement la conservation des sept combinaisons observées, le support
numérique STORYPLAYR **sans droit DILICOM implicite**, et l'absence de faux
formats de fichier pour les six codes bibliographiques.

Les assertions de fonctionnalité encore manquante sont marquées
**`xfail(strict=True)`** : classification documentaire autonome, filtre
physique sur liste CATALOGUE mixte, scénario EBOOKS non autorisant, support
inconnu, et conservation des deux provenances sur doublon. Un futur
`XPASS` est une erreur de CI jusqu'à la suppression de la marque et
l'adaptation des assertions, et **n'est jamais une validation terrain**.

**Frontière de coactivité** : PR tests/docs exclusivement ; aucun changement de
`cifereca_paris_formats.py`, `cifereca_paris_client.py`,
`cifereca_paris_normalize.py`, session Paris, Core de convergence, ou
GTK pendant le P0 #3723. Implémentation ultérieure en petites tranches,
validation CI Core + plugins, GTK si le rendu change, installation/rollback
sur HEAD final. La recherche exhaustive et les facettes serveur sont hors
scope de cette préparation (#3415, #1917, #2385).

**Critère vérifiable** : les 2 films et 15 disques de la capture ne seront
pas présentés comme livres papier ; les 12 numériques ne se retrouveront
pas dans un résultat physique par seul effet du scénario ; les 88 physiques
resteront consultables avec leur type documentaire. Les boutons et
permissions d'emprunt resteront gouvernés par des capacités **indépendantes**.
