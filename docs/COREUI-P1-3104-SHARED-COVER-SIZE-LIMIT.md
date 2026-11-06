# CoreUI P1 #3104 — limite des couvertures, commune à toutes les vues

Le composant partagé `CoverLoader`, consommé par `CoverView` /
`BookListCell` dans Bibliothèque, Emprunts, Recherche et Recommandations,
borne désormais les images distantes à `MAX_IMAGE = 8 Mio`, la même limite
que les couvertures intégrées aux EPUB.

- Le GET reste strictement déclenché par les parcours existants ; il lit la
  réponse HTTP par fragments de 64 Kio. Le compteur porte sur les octets
  **décodés** (y compris une réponse compressée), non sur la seule annonce
  `Content-Length`. Une image trop volumineuse n'est ni publiée à GTK ni
  écrite sur disque.
- Un cache-hit dont la taille dépasse cette limite n'est jamais utilisé :
  l'entrée est invalidée et peut être régénérée depuis la même URL.
- Le résultat d'erreur garde la catégorie fermée `invalid_image` ; ni URL,
  ni credential, ni en-tête HTTP n'entrent dans les diagnostics.
- Les limites, placeholders, annulations et connexions restent ceux du
  chargeur existant. Aucun téléchargement supplémentaire en dehors du
  comportement de cache invalide, aucun traitement par fournisseur.

Tests offline : image supérieure à 8 Mio rejetée malgré une signature PNG,
ancien fichier cache surdimensionné invalidé puis remplacé par un petit PNG,
cache-hit valide inchangé. La recette GTK réelle multi-profils et les états
offline restent dans #3104.
