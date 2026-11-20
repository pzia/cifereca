# Âges typés dans la projection bibliographique native

Le projecteur partagé accepte les `YouthAgeRange` publics existants en plus
des dictionnaires historiques. Il conserve les bornes et la nature explicite
(éditeur, recommandé, lecture, intérêt, inféré), avec les mêmes limites de
présentation jeunesse et de collection. Une déclaration inférée reste libellée
« non attesté » ; aucune interprétation de catégorie ou de texte libre.

`detail_metadata_fields` fournit directement les `DetailField` existants au
constructeur natif. Seule une `Provenance` typée portée par un âge typé est
conservée. Les préfixes Catalogue/EPUB, `source_label`, les dictionnaires de
provenance libres et le DocumentRef ne créent aucune preuve. Le dictionnaire
libre `Provenance.metadata` n'est pas parcouru. Deux valeurs identiques avec
des coordonnées de provenance différentes restent distinctes ; un même âge
et une même déclaration sont dédupliqués. Les anciens consommateurs de
`detail_metadata_items` et `detail_metadata_rows` gardent leurs tuples.

Le passage par `book_detail_presentation` et son échantillon Essentiel
conserve les champs typés au lieu de les reconstruire depuis trois chaînes.
Une ligne Détail sans preuve ne masque pas une déclaration typée identique.
Le rendu des infobulles et descriptions accessibles est celui de #6986 ; ce
lot ne modifie pas le renderer. Aucune requête, filtre de droits, écriture A/B,
nouvelle ABI ou nouvelle autorité documentaire n'est introduite.

Réception partielle #3688/#6366 : cette tranche raccorde les déclarations
d'âge déjà typées. Elle ne produit pas une provenance par champ à partir de
métadonnées sources qui n'en contiennent pas. La couverture des autres champs
et l'inventaire des badges six vues restent ouverts.
