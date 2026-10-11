# Baen — catalogue Free Library (0.1.10)

## Correctif de découverte P2 #764

**Observation publique datée du 11 octobre 2026 :**
- la page `https://www.baen.com/allbooks/category/index/id/2012`,
  que la version antérieure de Cifereca utilisait, était affichée
  « Showing 0-0 of 0 / No results » dans l'extraction publique ;
- `https://www.baen.com/catalog/category/view/s/free-library/id/2012`
  présentait « Items 1-12 of 82 », avec des produits dont **1632** ;
- un lien de produit observé mène à
  `https://www.baen.com/1632.html` et une seconde fiche à
  `https://www.baen.com/a-desert-called-peace.html` ;
- un paramètre `p=2` existait sur la pagination du catalogue actuel.
  Une page à `product_list_limit=36` était publiquement accessible
  sur le parcours Free Library.

Références : [catalogue actuel](https://www.baen.com/catalog/category/view/s/free-library/id/2012),
[catalogue historique](https://www.baen.com/allbooks/category/index/id/2012),
[fiche 1632](https://www.baen.com/1632.html),
[fiche A Desert Called Peace](https://www.baen.com/a-desert-called-peace.html).

Le fournisseur utilisait des liens contenant `/allbooks/` comme critère
de livre ; les liens de produit `/<slug>.html` observés ne pouvaient donc
pas être sélectionnés. Désormais le parseur ne retient que des liens
de carte explicitement marqués `product-item-link` dans le même domaine,
avec chemin produit simple, HTTPS et sans identifiants de connexion,
fragment ou query string.

**Attention :** cette correspondance avec le balisage CSS Magento reste
une hypothèse raisonnable **non validée par une capture HTML brute**.
Les extraits publics ci-dessus ne permettent pas de certifier les
attributs/classes HTML. Les fixtures de
`tests/test_public_catalog_recovery_p2.py` sont explicitement
**synthétiques**, jamais présentées comme une réponse réseau attestée.
Si le HTML réel a changé, le plugin refusera la page au lieu d'inventer
des résultats. Recette nécessaire avant la promotion en production :
capture autorisée, nettoyage des contenus personnels, une page 1, page 2
et une fiche du lien réellement trouvé, puis test de parsing sur fixture
DOM expurgée.

Le nouveau paramétrage public est `p` + `product_list_limit` (max 36).
Le filtre texte est **local à chaque page de la catégorie** : une page
sans correspondance **ne signifie pas** que le livre n'existe pas sur
les autres pages ou dans tout Baen. Aucune recherche serveur fédérée
ou enrichissement automatique n'est revendiqué.

Les fiches ne sont pas téléchargeables par leur seule présence dans le
catalogue. Le téléversement/download direct via transport.http conserve
ses contrôles, sur action explicite. Aucune API privée de compte,
aucun mot de passe, aucun accès `My Books` n'a été ajouté.
