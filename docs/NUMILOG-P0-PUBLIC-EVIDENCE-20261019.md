# Numilog P0 — qualification publique observable (19 octobre 2026)

Tickets : [#1886](https://github.com/pzia/cifereca/issues/1886)
(catalogue commercial) et [#1072](https://github.com/pzia/cifereca/issues/1072)
(enrichissement des achats). **Observation publique anonyme sans compte,
sans soumission de formulaire ni déclenchement de téléchargement.**

## Ce qui est effectivement visible aujourd'hui

| Surface officielle consultée | Observation | Ce qu'elle **ne** prouve **pas** |
| --- | --- | --- |
| [Recherche avancée](https://www.numilog.com/Pages/Recherche/RechercheAvancee.aspx) | Le formulaire public présente les champs **Titre, Auteur, Format, Thèmes, Éditeurs, Ean** et l'action « Rechercher ». | Paramètres HTML `name`, méthode POST/GET, action de formulaire, acceptation de requêtes anonymes par champ, pagination/tri du résultat, robots/CGU : **non inspectés/attestés**. Une interface ne prouve pas un contrat HTTP automatisable. |
| [Blackbone, produit 1171334](https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook) | Fiche visible : NATHAN, publication **2020-04-16**, **228 pages**, langue « Français », **EAN papier 9782092591086** distinct du **EAN EPUB sans DRM 9782092591093** ; mention éditoriale « Roman ado dès 15 ans ». | Aucune identité d'acheteur, format effectivement acquis, ou politique d'âge machine n'est établie par le texte de la fiche. |
| [Le Piège de Sainte-Soline, produit 1956345](https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook) | Produit public distinct, date **2026-10-01**, marque Massot éditions. La présentation contient des références **ISBN papier / numérique** différentes. | La page publique ne prouve pas que cet ouvrage existe dans un compte Numilog, ni que tous les identifiants du texte sont des EAN structurés avec provenance par format. |

Ces observations **ne sont pas des fixtures HTML brutes**. Les fichiers
`src/plugins/provider-numilog/tests/fixtures/observed_metadata_*.html`
et `public_search_jefferson_20261016.html` ont déjà une traçabilité
distincte. Ne jamais les remplacer par un rendu textuel sans sélecteurs.

## Contrat d'implémentation prudent

1. **Recherche multi-attributs distante** : prendre un relevé autorisé du
   `<form>` public complet (attributs, champs cachés et action) puis de
   la **réponse** à une soumission anodine ; répertorier les paramètres,
   route, pagination, erreur/0 résultat et règles d'accès. Ne jamais inventer
   `field=author` ou un endpoint d'API parce que le formulaire contient
   « Auteur ».
2. **Contenus publics** : comparer les champs visibles des deux produits
   avec JSON-LD `Book`, libellés de la fiche, variantes EAN/ISBN et
   éventuelles contradictions. Un EAN de papier n'est jamais un ISBN EPUB.
   Les mentions d'âge dans une **description** restent des textes éditoriaux
   tant qu'aucun champ d'âge qualifié et sa variante ne sont établis.
3. **Achats privés** : seule une lecture autorisée et expurgée de
   `Mes Livres` peut établir un lien exact d'une acquisition à la
   fiche commerciale. Un ID privé `lid`, un titre ou une couverture ne
   suffisent pas. Ne copier ni cookies ni URI de livraison signée.
4. **Transport** : respecter les règles officielles, bornes réseau,
   403/429, cache et consentement. L'état du fichier `robots.txt` n'a
   pas été confirmé par cet audit : **aucune autorisation de crawler** ne
   peut en être inférée. Pas d'interception de CAPTCHA ou de paiement.

## État de la couverture

- **Attesté visuellement dans les pages publiques** : interface avancée,
  textes éditoriaux, labels auteur/date/éditeur/langue/pages et deux types
  d'EAN publiés sur Blackbone.
- **Déjà implémenté sous preuve HTML et tests offline** : recherche simple
  publique avec scope distinct, filtres locaux de pages, extraction bornée
  des données publiques, champs de fiche enrichis à la demande et
  séparation achat/offre commerciale.
- **Bloquant pour clôturer #1886** : formulaire réellement soumis et
  endpoints vérifiés, limites d'accès qualifiées, assertions 0/1/N et
  pagination avancée avec réponse brute structurée et fixture expurgée,
  puis intégration Core/GTK.
- **Bloquant pour clôturer #1072** : liaison à **l'édition achetée exacte**
  attestée par compte, famille de formats, variante et affichage partagé
  vérifiés sur un environnement contrôlé.

Aucun acte commercial ni nouvelle requête réseau de production n'a été
effectué pour cette note.
