# P0 #4858 D3 — portée immuable de l'admission directe Kobo

Le parcours `LibraryService._acquire_direct_item_impl` peut recevoir un
callback de droit actuel `_admission` pour ignorer un ancien veto
`backup_items`. Avant cette correction, il remplaçait directement le
`SearchResult` sélectionné par celui retourné sous verrou, puis
réévaluait seulement le fournisseur et l'ABI. Un callback retournant une
autre édition, révision ou credential — ou révoquant le drapeau
`owned/downloadable` — pouvait donc orienter le GET vers un produit
différent de celui sélectionné, en dehors de son consentement précis.

D3 conserve désormais avant callback les coordonnées
`provider/credential/edition/revision` de l'objet choisi, y compris si
ce même objet mutable est modifié en place. Sous le verrou global
d'acquisition, la nouvelle admission doit restituer un `SearchResult`
exactement cohérent, avec achat `owned=True`, sans refus explicite
`downloadable=False`, et un propriétaire de connexion inchangé.
Échec : `kobo_legacy_direct_admission_changed`, aucun GET.
Le veto préexistant SourceReference typée/ABI complète reste indépendant
et les archives 0.7 restent de simples preuves historiques négatives.

Tests sans réseau : modification du provider, credential, édition, révision,
droit, livrabilité, mutation en place, changement de profil, admission valide
avec métadonnée de titre rafraîchie et un seul dispatch dans le bon profil.

Ce correctif D3 ne constitue pas une preuve CompletedDelivery ni le
remplacement du chemin W2 historique ; #4858/#3196 restent ouverts.
