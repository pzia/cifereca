# #1886 P0 — Numilog : surface officielle de recherche avancée (qualification partielle)

Observation du **19 octobre 2026**, fondée exclusivement sur la page
**publique** [Recherche avancée](https://www.numilog.com/Pages/Recherche/RechercheAvancee.aspx).
Il ne s'agit ni d'un contrat d'API, ni d'une capture de POST formulaire,
ni d'une autorisation de scraping. Aucune requête de recherche avancée
n'a été exécutée par ce travail.

## Champs visibles, limites de preuve

| Libellé visible sur la page officielle | Preuve à ce stade | Ce qui reste NON prouvé |
| --- | --- | --- |
| Titre | Champ de formulaire présenté sous « Recherche Avancée » | Paramètre, verbe HTTP, filtrage exact, pagination |
| Auteur | Champ de formulaire présenté | Recherche par auteur/co-auteurs, précision et couverture serveur |
| Format | Sélecteur présenté | Options/valeurs stables et correspondance EPUB/PDF, côté serveur |
| Thèmes | Sélecteur présenté | Catégories disponibles, codes, pagination/éditions |
| Éditeurs | Sélecteur présenté | Identifiants éditeur, comportement multi-sélecteur |
| Ean | Champ de formulaire présenté | EAN **numérique** vs papier et comportement en recherche |
| Recherche globale | Champ global « titre, auteur, éditeur, thème » visible dans l'en-tête | Sémantique de chaque attribut dans la route `brSimple` |

La page annonce une recherche par « titre, auteur, ISBN », mais elle
n'expose **pas dans le texte public consulté** les noms exacts des
paramètres réseau, la méthode de soumission, le schéma de réponse
ou la qualification d'ISBN d'édition EPUB/PDF.

## Contrat actuel du plugin

- `provider.numilog.catalog` ne déclare **que `field=all`** :
  recherche `brSimple` et liens de facettes réellement issus des pages
  observées dans les fixtures. Aucun filtre auteur, ISBN ou éditeur serveur
  ne peut être promis sur la seule présence du formulaire avancé.
- `NumilogPublicSession.refine_loaded_page(s)` effectue des **filtres
  locaux** sur les pages publiques préalablement chargées. Un ISBN
  numérique positif demande une fiche produit explicitement enrichie
  et un libellé EPUB/PDF/numérique attesté. Ces filtres ne sont jamais
  des facettes serveur ni des recherches exhaustives.
- `provider.numilog` (« Mes achats ») conserve **seul** l'autorité sur
  les droits du compte ; aucune donnée du catalogue commercial n'atteste
  `owned`, `downloadable` ou une acquisition.

## Qualification toujours requise avant implémentation serveur

1. Vérifier les règles **robots**, CGU et conditions d'accès applicables
   aux GET/POST de recherche ; la lecture de la page officielle et des
   [conditions de services](https://www.numilog.com/Pages/Aide/faq.aspx?idCategorieActive=16)
   ne vaut **pas** autorisation d'automatiser les formulaires.
   `robots.txt` n'a pas pu être lu via l'outil de consultation utilisé :
   **statut inconnu**.
2. Capturer **dans un parcours autorisé**, sans cookies/identifiants
   privés, l'action réelle « Rechercher » et ses paramètres structurés
   avec tests de zéro, plusieurs pages, EAN papier distinct de l'EAN
   numérique, 403/429 et mode dégradé. Expurger toute donnée sensible.
3. Qualifier les réponses serveur et leurs valeurs avant de modifier
   `ProviderSearchCapabilities(fields=...)` ou le chemin HTTP du plugin.
   Une absence de preuve conduit au maintien de `all` uniquement.

**Décision provisoire :** surface UI officielle attestée ;
**contrat d'interrogation avancée, permission d'automatisation et
recette live non attestés**. Ne pas fermer #1886 sur cette seule preuve.
