# Baen P1 #2333 — catalogue public / JSON observé (qualification partielle)

## Évidence et portée

Captures Browser Recorder privées du 11/11/2026 (voir l'issue #2333, **aucune
archive conservée dans Git**). Route observée dans la navigation Free Library :

- `GET https://www.baen.com/allbooks/category/index/id/2012` : HTTP 200,
  HTML **non capturé** dans les preuves, ne pas inférer sélecteur DOM.
- `POST https://www.baen.com/allbooks/category/getBooks` : **opération de
  lecture de catalogue**, réponse 200 JSON capturée sur **page 2**.
  Corps x-www-form-urlencoded :
  `categoryId=2012&page=2&pageSize=20&filter=release_date&dir=DESC&hideBundles=0&hideEarcs=0&showAvailable=false`.
- Réponse observée : `returnCode="single cat"`,
  `categoryId="2012"`, `totalBooks=82`, `numOfBooks=20`,
  `productData` tableau de 20 objets. Valeurs 82/20 **propres à cette
  observation**, ni total global ni garantie permanente.
- Une route de livraison `/download/index/owned/id/...` a donné **302 vers
  login** : cela ne démontre ni un EPUB public, ni une acquisition anonyme.

## Comportement implémenté (Baen 0.1.20)

- `search(query="")` invoque **une** lecture catégorie AJAX de `page+1`
  (0-index Core, 1-index site), pageSize **20**, sans `q`, cookies importés
  ni credentials. Les paramètres de pagination correspondent à la preuve.
- Le JSON doit être de type `application/json`, de taille inférieure ou égale
  à 256 KiB, de catégorie 2012 et de structure bornée ; réponse non reconnue,
  302, 403, 429, 5xx ne deviennent **jamais** un faux catalogue vide.
- Un produit `isFree is True`, avec nom textuel valide et URL canonicalisée
  `https://www.baen.com/<slug>.html`, devient une **notice publique** :
  `owned=False`, `downloadable=False`, aucune URL de téléchargement,
  auteur/ISBN/droit non inférés. Une ligne malformée est rejetée et signale
  des résultats partiels.
- Le champ optionnel `imageUrl` fournit une vignette **bibliographique
  uniquement** quand il désigne une image HTTPS non signée du répertoire
  public `www.baen.com/media/catalog/product/` (`jpg/jpeg/png/webp`,
  sans query, fragment, identifiant, chemin encodé ni segment `..`).
  Sinon l'image reste absente **sans supprimer la notice**. Cette validation
  syntaxique n'atteste pas qu'un hôte distant serve réellement l'image,
  et ne crée aucune acquisition, possession ou droit.
- `search(query non vide)` conserve la route historique GET avec `q`,
  toujours **non qualifiée**. Aucun filtrage global de recherche libre ne
  peut être revendiqué depuis les pages Free Library. Les paramètres,
  résultats et éventuels zéros restent partiels.
- Une page JSON même structurellement correcte n'est **pas** preuve
  d'exhaustivité serveur ou de disponibilité réelle d'un lien EPUB. Les
  notions `isOwned`, prix `Free` et bouton `Download` ne confèrent
  aucune permission.
- Si un lien EPUB public explicite est ultérieurement confirmé sur une
  fiche, la requête de livraison via `transport.http` impose
  `redirect_policy=same_origin` et `overwrite=False`. Une destination
  préexistante différente n'est jamais écrasée ; un contenu identique peut
  être conservé suivant le contrat d'artefact du transport. Aucun chemin
  observé vers `/download/index/owned/` n'est une permission anonyme.
- Le plugin reste **désactivé par défaut**.

## Limites et validation

Tests `httpx.MockTransport` et payloads synthétiques pour page 2 à 20
notices, champ `isFree`, hôtes/URLs, JSON incomplet, réponse 200 ambiguë,
redirection login, 403/429/5xx, séparation navigation/recherche, et absence
de téléchargement implicite. **Ces tests ne prouvent pas la reproduction
HTTPX publique réelle du POST**. Pour clôturer #2333, il reste à observer
avec accès autorisé le POST depuis `HTTPX_TRANSPORT` (sans compte et sans
contournement) et confirmer le comportement du détail/téléchargement. En cas
de refus HTTPX, conserver une indisponibilité explicite, jamais une fausse
liste complète ; ne pas solliciter une nouvelle capture privée pour engager
les travaux.
