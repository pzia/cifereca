# Numilog — provenance des URL de livres (0.1.16)

## Audit P2 #764 — 11 octobre 2026

**Preuves sur les pages publiques officielles**

- [Blackbone — Coltan Song, tome 1](https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook)
  fournit une indication « Roman ado dès 15 ans », résumé, auteurs,
  marque, parution, langue, EAN papier et EAN EPUB distincts.
- [Le Piège de Sainte-Soline](https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook)
  publie également résumé, éditeur, parution, ISBN/EAN séparés par
  format et genre. **Les notices privées « Mes Livres » ne fournissent
  pas nécessairement ces champs.**

**Écart** : le provider authentifié Numilog ne projette actuellement
que titre, couverture éventuelle, identifiant de compte et référence de
lien public ; aucun auteur/ISBN/âge issu du site public n'est récupéré.
Associer une fiche publique à un livre possédé exige une preuve
bibliographique solide sur **la même édition numérique**, et pas un
simple rapprochement de titre ou d'ISBN papier.

**Correctif strict de cette PR** : protéger la provenance des liens
publics issus de « Mes Livres » :

- URL reconnue `/<identifiant>/<slug>.ebook` sur origine Numilog, ou
  anciennes formes compatibles `/livres/<slug>` et `/ebooks/<slug>`.
- Exclure URL de compte ou d'acquisition, hôte externe, userinfo, port
  détourné, fragment, paramètres contenant des jetons, chemins ambigus
  et traversées. Le seul paramètre toléré par compatibilité est
  `ref=library` pour une URL héritée `/ebooks/`.
- Appliquer la validation lors de l'extraction HTML **et** de la
  projection d'une ancienne entrée persistée. Stocker uniquement
  l'attestation `public_url_evidence=numilog.library_title_link`
  lorsqu'un lien est accepté.
- Aucun ajout d'HTTP, aucune altération du lien privé de téléchargement,
  de l'identité propriétaire, ni des métadonnées d'âge.

Tests hors réseau : `tests/test_public_book_link_guard_p2.py`.

**Suite distincte, non livrée** : capture réelle expurgée de « Mes
Livres », vérifier la redirection vers la bonne édition publique,
comparer l'EAN de l'EPUB puis qualifier ses données HTML/JSON-LD. Cette
qualification est nécessaire avant tout enrichissement de bibliothèque
ou filtrage Jeunesse depuis les pages commerciales.


## Connexion déclarée perdue après le démarrage (#1884)

Depuis 0.1.19, le log applicatif expose `TRACE numilog session` avec un
booléen `connected` et une raison fixe : connexion commencée/réussie/échouée,
fermeture explicite, opération refusée car déjà déconnectée, redirection hors
bibliothèque, structure de bibliothèque invalide ou panne transport. Ces
événements existent même sans collecteur Diagnostics ; aucune URL, identité,
credential, donnée HTML, cookie ou exception brute n'est journalisée.
`library_redirect` décrit la décision du provider de déconnecter après une
redirection ; ce code ne prouve pas à lui seul une expiration côté fournisseur.
Une erreur de structure ou de transport ne change pas l'état connecté.

Le log utilisateur du 16 octobre confirme une connexion initiale réussie,
mais sa tâche de téléchargement n'expose pas la cause de l'échec. Les notices
en cache ne prouvent pas une connexion active. Cette instrumentation ne
prétend pas corriger une cause amont non observée : aucune reconnexion,
requête supplémentaire ou modification des gardes d'acquisition.
