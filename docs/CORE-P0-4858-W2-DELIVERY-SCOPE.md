# P0 #4858 — portée W2 du livre normalisé et du résultat reçu

## Défaut

Le chemin de compatibilité Kobo 0.7 utilisait le manifeste OWN de la session
active pour choisir un produit, mais `_acquire_manifest_item` ne recoupait
pas systématiquement les coordonnées du `SearchResult` normalisé avant
l'appel exécuteur, ni celles du résultat reçu avant `backup_items.complete`.
Le code `result.account_id or account_id` pouvait attribuer à la session
courante un reçu portant explicitement un autre emprunteur, ou traiter
`account_id=""` (compte primaire Kobo exact) comme une valeur absente.

Un résultat de GET ne prouve **jamais**, par simple présence d'octets, le
droit d'un autre profil, d'une autre édition, d'une autre révision ou d'un
emprunt à être enregistré comme achat.

## Nouvelle clôture de portée avant et après GET

Sous l'admission W2 existante et le verrou unique, avant le GET :

- le produit normalisé doit porter exactement `source_provider="kobo"`,
  le `credential_id` de session, le `record_id` du manifeste et sa
  `revision_id` (y compris lorsqu'elle vaut `None`);
- un `owned=False` ou `downloadable=False` explicite est un refus ;
- tout changement de session/epoch/owner reste un veto indépendant.

Après le GET et **avant la lecture du fichier et la publication du reçu** :

- relecture de l'époque/session/compte du Core ;
- les champs source du résultat, lorsqu'ils sont fournis, doivent correspondre
  exactement à Kobo, au credential, au produit et à l'emprunteur sélectionnés ;
  une chaîne vide explicite n'est pas assimilée à un champ absent ;
- `loan_id` non nul est refusé pour la branche W2 achat ;
- une révision explicitement fournie par les métadonnées du résultat doit
  correspondre au manifeste. L'absence du borrower dans l'ancien format de
  résultat peut seulement reprendre **le borrower déjà épinglé**, jamais
  l'inventer depuis le résultat.

Tout conflit est un refus fermé `_KoboLegacySessionChanged` : le
checkpoint `acquiring` reste conservé pour reprise supervisée, **aucun**
`backup_items.complete` / `downloaded` / `OriginLink` n'est créé par ce
rattrapage, les octets reçus ne sont jamais supprimés, et un second GET n'est
pas lancé automatiquement. Les erreurs publiques restent génériques.

## Limites

Cette tranche ne convertit pas le GET 0.7 en `CompletedDelivery` canonique,
ne retire pas W1 des sessions Kobo actives et ne remplace pas le pipeline
W2 des ABI partielles. Elle renforce seulement les barrières de cohérence
**pendant le reliquat de migration** ; #3196 / #4858 restent P0.

Tests : `tests/test_core_p0_4858_w2_delivery_result_scope.py`, témoin de
rebind existant `tests/test_core_p0_3196_w2_item_session_scope.py`.
