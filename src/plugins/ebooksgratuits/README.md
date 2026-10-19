## #1917 — qualification des entrées Atom EPUB/PDF (0.1.18, 19/10/2026)

Un `<entry>` OPDS n'est plus considéré comme un livre sur la seule
présence d'un `rel=.../acquisition`. Une offre admissible doit désormais
déclarer un type de fichier reconnu (EPUB, PDF, Mobipocket) **et** un lien
d'acquisition direct répondant aux mêmes restrictions d'origine et de
chemin public `/index.php/fetch` que le téléchargement réel ; un
`rel=alternate` EPUB doit satisfaire les mêmes restrictions.

Cela empêche une entrée provenant d'un autre hôte, d'une URL
`/account/`, d'un MIME HTML ou d'un lien alternatif privé de se
faire passer pour un livre et de détourner la navigation vers le
catalogue global. Les sous-catalogues de navigation ne sont plus
projetés comme de fausses notices de livres.

Sur un flux mélangeant des entrées d'acquisition valides et des
sous-catalogues, le plugin affiche `partial_results` : seules les
notices directes sont couvertes, aucun parcours automatique de la branche
voisine n'est ajouté. Une fiche **PDF seulement** reste visible, mais
`downloadable=False` et sans lien EPUB. Le résultat est borné au flux
reçu, sans promesse d'exhaustivité ni nouveaux appels HTTP de recherche.

Tests hors réseau `test_opds_public_book_entry_integrity_p1.py`
avec `httpx.MockTransport` : origines étrangères, liens privés,
MIME trompeur, page mixte, PDF-only et racine OPDS. La réponse réelle
`opds2.ebooksgratuits.org` **n'a pas été vérifiée en direct** par ces
tests ; la recette hors-cache dans le Core/UI et la qualification réseau
restent ouvertes dans #1917 et #2361.

---

## #1917 — navigation OPDS racine et preuve de couverture (19/10/2026)

En mode OPDS Calibre/COPS (par défaut), `search("")` consulte la racine
et choisit la branche « All books / Tous les livres » lorsqu'elle est
**identifiable de manière unique**. Si la racine ne contient que des sous-
catalogues thématiques (« Authors », « Recent », etc.), le plugin peut en
visiter **un seul**, mais signale désormais `partial_results` même lorsque
cette branche répond avec zéro livre. Deux branches « All books » distinctes
sont également signalées comme une couverture partielle ; aucun crawl ou
concaténation spéculative n'est ajouté.

Cette correction ne prouve **pas** que le serveur distant est disponible ni
que la recherche OpenSearch est intégrée au moteur fédéré. Le succès d'un
téléchargement depuis le cache local est indépendant. Tests hors réseau :
`tests/test_opds_root_partial_1917.py`. Recette live expurgée et GTK :
suivis par #1917 et le contrat Core #1916.

# Acquisition Calibre OPDS — #1084 (0.1.17)

Production : `https://opds2.ebooksgratuits.org/index.php/feed` (COPS, OPDS Atom/XML 1.x, **pas** OPDS JSON 2). Le site officialise cette URL : https://www.ebooksgratuits.com/torrent.php. Recherche via le lien OpenSearch annoncé par la racine ; navigation via liens Atom et pagination par `rel=next` uniquement. Limite de 101 pages demandables ; aucune exploration ou acquisition implicite. Le catalogue Calibre contient aussi des ouvrages de partenaires, donc éditeur renseigné seulement lorsque la notice l'atteste. Titres, auteurs, catégories, genres, mise à jour et liens EPUB proviennent de chaque notice ; pas d'âge inféré depuis `Jeunesse` ni de jugement territorial ou date historique déduits. Les champs spécialisés filtrent localement la page de recherche et annoncent une couverture partielle. La présence d'une offre EPUB est vérifiée par son lien et type MIME, le transport valide le fichier ; des notices sans EPUB restent non téléchargeables.

`acquisition_backend=opds` est le défaut. `opds_feed_url` permet un catalogue de test autorisé. `acquisition_backend=html` conserve l'ancien parseur uniquement pour les tests de régression et les simulateurs locaux ; `base_url` ne pilote que ce mode. Aucun basculement automatique vers HTML si l'OPDS est indisponible : erreur explicite. Recette hors ligne par `test_opds_calibre_1084.py`. Recette live sur le flux tiers non revendiquée ; si le site modifie son contrat, qualifier un échantillon réel avant tout correctif.

---

## Notes historiques du parseur HTML (mode simulation)
# Ebooks libres et gratuits — provenance des dates et droits (0.1.12)

## Audit des métadonnées amont #764 — 11 octobre 2026

Source publique officielle : [liste des ebooks](https://www.ebooksgratuits.com/ebooks.php),
[fiches Nouveaux contes du Far West](https://www.ebooksgratuits.com/details.php?book=3500)
et [Le Hussard bleu](https://www.ebooksgratuits.com/details.php?book=3498).

**Preuves sur les pages publiques** : les fiches et les cartes de catalogue
affichent **« Parution le 31/08/2026 »** pour les nouveautés (date de
mise à disposition de l'**édition numérique Ebooks libres et gratuits**)
et une liste de plusieurs formats (ePub, PDF, HTML, etc.) avec tailles.
La fiche du *Hussard bleu* avertit explicitement :
**« téléchargement non autorisé dans certains pays »** et affiche
la date de décès de l'auteur. Ni la date d'origine de l'œuvre, ni les
droits applicables dans le pays de l'utilisateur ne peuvent être déduits
de ces éléments.

### Implémentation conditionnelle

L'ancien parseur conservait titre, auteur, description, image, fiche et
lien EPUB ; il **ne projetait ni la date de parution de l'édition sur ce
site ni son avertissement territorial**, même lorsqu'ils apparaissaient
dans la liste déjà téléchargée.

Désormais, sans aucun nouvel appel HTTP et pour **chaque carte isolément** :

- `metadata.ebook_release_date = YYYY-MM-DD` uniquement si le texte
  contient un libellé explicite `Parution le JJ/MM/AAAA` et si la date
  est valide. La preuve `ebook_release_evidence =
  ebooksgratuits.catalogue.parution` accompagne la valeur.
- `SearchResult.published_at` **reste absent** : ce champ ne doit pas
  confondre la parution numérique et la publication bibliographique
  originale ou l'édition imprimée.
- `metadata.territorial_rights_warning = true` uniquement pour
  l'avertissement textuel explicite que le téléchargement n'est pas
  autorisé dans certains pays ; preuve
  `territorial_rights_evidence = ebooksgratuits.catalogue.warning`.
  Le champ `license_note` affiche un message non ambigu.
- **Aucune décision juridique automatique** : le plugin n'affirme ni
  que le livre est libre de droits dans un pays donné, ni qu'il y
  est interdit. Le téléchargement reste **sur action explicite**,
  soumis aux restrictions légales et au transport existant.
- Aucune date de décès, aucun âge recommandé, aucun ISBN, aucun contenu
  de commentaire ou script n'est transformé en métadonnée. Les textes
  de carte sont utilisés transitoirement et **ne sont pas sauvegardés**
  dans `metadata`. Analyse bornée à 12 000 caractères de texte/carte.

Les filtres publics attestés Genre/Catégorie et la sélection du format
EPUB existants restent inchangés.

### Tests et limites

`tests/test_release_rights_provenance_p2.py` couvre les trois états
**date valide / date invalide / date absente**, l'alerte ciblée par livre,
la distinction entre date de l'œuvre et date de l'édition, le texte HTML
fragmenté et l'absence de fuite des textes de commentaires/scripts.

Les fragments HTML de tests sont **synthétiques**. Les pages officielles
consultables établissent l'existence et le sens des libellés, **mais pas
la structure HTML brute exacte**. Une capture DOM publique expurgée
reste nécessaire pour une recette bout en bout du nouveau parseur.

### Autres champs amont observés (non livrés)

Les pages affichent aussi des tailles pour plusieurs formats, des
catégories, genres et des liens vers une page de droits.
Une extension de ces champs exigerait la vérification des ancres
effectives et de leur correspondance dans le HTML de catalogue ;
**ne pas** attribuer une catégorie ou une date de publication
historique sur la simple base du titre.


## Couverture des sélecteurs de recherche (#1917, 0.1.13)

Lorsqu'un texte auteur, catégorie ou genre correspond à plusieurs options
**également classées dans le même sélecteur** (ex. `Jean Alpha` et
`Jean Beta` pour `jean`), la recherche conserve son choix déterministe
mais annonce `partial_results : plusieurs options du filtre ; une seule interrogée`.
Une option exactement égale au texte demandé prime sans fausse ambiguïté.
En mode sujet, la perte d'une deuxième voie catégorie/genre reste signalée
indépendamment. Aucun scan réseau supplémentaire, aucune revendication de
recherche exhaustive, et aucun nouveau filtre serveur n'est introduit.


## Pages de recherche bornées (#1917, 0.1.14)

Les recherches titre, auteur, catégorie, genre et sujet utilisent un index de
page **entier** `0..100`, déclaré dans les capacités de recherche
(`max_pages=101`). Les index négatifs, booléens, chaînes, fractions et pages
hors limite sont refusés avant tout GET/POST avec le code public
`invalid_page`, au lieu de réinterpréter la demande en page zéro.
Les avertissements de recherche sont réinitialisés pour les requêtes
refusées ; aucun scan des pages suivantes n'est déclenché.


## Valeurs globales des sélecteurs (#1917, 0.1.15)

Les choix par défaut du formulaire HTML (`id_auteur=0`,
`id_categorie=1`, `id_genre=1`) signifient **Tous/Toutes**, et ne sont
pas des valeurs attestant un auteur, une catégorie ou un genre. Leur
libellé ne participe plus à la résolution d'une recherche spécialisée :
la requête `author=Tous` ne lance plus une recherche globale présentée
comme filtrée. Un sélecteur ne contenant que cette option de défaut
est traité comme `field_route_unavailable`, et non comme un résultat
vide obtenu par interrogation réelle d'un champ. Aucun appel HTTP
supplémentaire ni changement d'acquisition n'est introduit.

Tests offline : refus des trois valeurs globales et des sélecteurs
contenant seulement un choix par défaut. Les identifiants sont ceux
observés par l'adaptateur actuel, sans hypothèse sur de futurs schémas.
