# Bibliothèques de Paris — métadonnées bibliographiques conditionnelles

## Audit P2 #764 — 11 octobre 2026 — package 1.2.25

### Preuves sur le site officiel

Les fiches publiques des bibliothèques de Paris affichent des champs
bibliographiques encore insuffisamment projetés par le provider :

- [Les secrets de la cuisine — SYRACUSE/586342](https://bibliotheques.paris.fr/2024/doc/SYRACUSE/586342/les-secrets-de-la-cuisine?_lg=fr-FR) :
  **Collection** « La Petite encyclopédie Youpi des grands curieux »,
  **Sujet** « Cuisine — ouvrages pour la jeunesse »,
  **Contributeurs** dont Rémi Saillard, **Public visé** « Jeunesse »,
  langue et **ISBN/EAN**.
- [Des vaches — SYRACUSE/705255](https://bibliotheques.paris.fr/Default/doc/SYRACUSE/705255/des-vaches?_lg=fr-FR) :
  sujet « Vaches — ouvrages pour la jeunesse », collection, contributeurs,
  **Public visé** « Jeunesse » et **note** « Bons lecteurs (à partir de
  9 ans) ». Cette note n'est pas une preuve du niveau de lecture de
  toutes les éditions.
- [Faut pas prendre les cons pour des gens. 01 — SYRACUSE/1453423](https://bibliotheques.paris.fr/doc/SYRACUSE/1453423) :
  **Collection** et numéro de tome, **Public visé** « Adulte », ISBN et EAN.

**Écart certain :** le provider `normalize_search_result` ne
projetait aucun des champs `subjects`, `collections`, `series`,
`contributors` ou `audience`, même si une réponse de recherche
contenait explicitement les clés correspondantes.

**Limite de preuve amont :** les pages HTML publiques attestent
les *concepts*, **pas les noms de champs du JSON Syracuse** transmis par
l'API de recherche. Aucun JSON authentifié brut / aucune capture
d'endpoint de recherche n'a été obtenu. Les clés testées ci-dessous sont
des variantes **conditionnelles**, et leurs valeurs ne sont pas
garanties dans la production. Les tests sont **synthétiques**, non des
captures réelles.

### Correction strictement conditionnelle

Dans `cifereca_paris_bibliography.py`, seule une petite liste fermée de
clés documentées par notre adaptateur, si elles sont **déjà présentes**
dans `Resource` ou `AdditionalProperties`, fournit des données :

- `Subject/Subjects/Sujet/Sujets/SubjectHeadings` → `subjects`
- `Collection/Collections` → `collections`
- `Serie/Série/Series/SeriesTitle` → `series_names` et `series` seulement
  si une **série unique** est attestée.
- `Contributor/Contributors/Contributeurs` → `contributors`
- `Audience/PublicVise/PublicVisé/Public` → `audiences`, uniquement
  pour les catégories explicites « Jeunesse », « Adulte » et « Tout
  public » ; `audience` si une seule catégorie est attestée.

Chaque facette conserve `bibliography_evidence` avec les chemins de
champs source `Resource.*` / `AdditionalProperties.*`. Déduplication
non destructive des valeurs, limite de 12 éléments/240 caractères,
refus des objets imbriqués, texte HTML rendu par l'utilitaire public
`description_text`. Aucune nouvelle requête réseau, aucune
identité personnelle traitée, aucun raccourci vers une URL d'emprunt.

**Ce qui n'est PAS inféré :** un public « Jeunesse » n'est pas un
âge recommandé. « À partir de 9 ans » peut apparaître comme une note
d'édition, mais n'est pas converti automatiquement en `age_min`.
Un ISBN affiché sur une fiche imprimée n'est pas projeté comme ISBN
d'une édition numérique PNB ; l'identifiant doit être vérifié au niveau
de l'édition. Les codes MONE/MONO/EXTL ne deviennent pas des formats
EPUB/PDF. Les champs absents et les séries contradictoires restent
absents/ambiguës.

Tests : `tests/test_bibliography_evidence_p2.py`, offline, sans
HTTP ni fixtures contenant de données personnelles.

### Handoff / recette nécessaire

Avant de considérer l'exploitation des métadonnées comme complète :
obtenir **avec autorisation** deux réponses de l'API Syracuse
(`CATALOGUE`, `EBOOKS`) expurgées de cookies, identifiants lecteurs
et URLs signées ; vérifier les noms/titres/provenances des clés et leurs
structures pour des fiches contenant explicitement collection et
sujet, et un cas négatif. Ne pas scraper ces pages à chaque recherche :
préférer les données JSON déjà retournées ou un broker/cache public
approuvé. La projection d'ISBN/âge par édition exigera ses propres
preuves, ce n'est pas une fonctionnalité livrée par ce patch.
