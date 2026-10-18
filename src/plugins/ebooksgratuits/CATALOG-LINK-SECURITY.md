# EbooksGratuits — liens catalogues et téléchargements (0.1.16)

Le catalogue Ebooks libres et gratuits est une source HTML publique.
Une carte HTML n'est pas une autorisation d'émettre des requêtes sur
n'importe quel hôte : les liens `details.php`, `newsendbook.php?format=epub`
et `.epub` sont désormais vérifiés **avant leur publication**.

- Origine exacte du catalogue configuré (schéma, hôte et port) ;
  userinfo, fragment, contrôles bruts et URL excessives refusés.
- Fiche : chemin terminé par `/details.php`.
- EPUB : chemin terminé par `.epub`, ou `/newsendbook.php` avec un unique
  `format=epub` (requête bornée et structurellement valide).
- Carte avec destination refusée : non publiée comme offre téléchargeable.
- `download_direct` revérifie l'URL EPUB et le référent de fiche, y compris
  après rechargement d'une notice sauvegardée, **avant** appel à `transport.http`
  et création d'un fichier.
- Une configuration de serveur de simulation `http://127.0.0.1:port/`
  conserve ses liens sur cette origine ; aucun domaine de test n'est codé
  en dur dans la règle.

La validation concerne **les destinations de fiche et de téléchargement**,
pas une promesse de validité juridique ni une attestation de redirections
amont (gérées par le broker HTTP). Les éventuels CDN de téléchargement
externes ne sont pas autorisés sans qualification explicite de leur origine.
La production historique connue utilise des chemins sur l'origine de
catalogue ; une redirection CDN réelle et son contrat devront être attestés
séparément.

Tests hors réseau : `tests/test_catalog_link_origin_p1.py`, et simulation
existante `test_dataset_integration.py`. Aucun changement dans Core/GTK.
