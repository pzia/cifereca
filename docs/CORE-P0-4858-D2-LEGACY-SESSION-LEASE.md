# P0 #4858 — D2 : jeton de confirmation manuel lié à la session Kobo

## Écart identifié sur `main`

Le parcours canonique D2 liait déjà son jeton à la session exacte, à
l'époque Core et à l'époque fournisseur. Le parcours manuel W2 legacy,
lui, ne stockait dans la confirmation que `configuration` et une empreinte
de manifeste/états SQLite. Si Kobo se reconnectait sous la même portée
`identity/provider/credential/account` et renvoyait les mêmes révisions,
le jeton émis pour l'ancienne session pouvait être exécuté sur la nouvelle
session avec un second accès aux contenus de l'exécuteur 0.7.

Ce n'est pas une décision typée A→B : `backup_items` ne prouve ni le
consentement ni les octets d'une session qui a changé.

## Contrat corrigé

Pour la **voie legacy** uniquement :

- le jeton one-use/600 s mémorise, en RAM, l'objet session exact et les
  époques `source_context_epoch(service)` / `session._epoch` ;
- après le manifeste préparatoire et la lecture bornée SQLite, la session
  et les deux époques sont revérifiées **avant** d'émettre le jeton ;
- au début de la confirmation, la session/les époques sont revérifiées
  **avant** le second appel de manifeste, le scanner et tout GET ;
- le `guard()` W2 de chaque item continue à refuser toute mutation
  après confirmation, même quand les coordonnées d'emprunteur ne changent
  pas ;
- un `account_id=""` ne peut être préparé que si le Core **et** la session
  fournisseur indiquent un compte `is_primary is True`. La source primaire
  vide légitime reste recevable.

Aucun jeton préexistant n'est persisté, aucune session ou donnée
historique n'est migrée, aucun autre plugin n'est concerné. La voie
canonique D2 et les gates CAS ne changent pas.

## Régressions

`tests/test_core_p0_4858_d2_legacy_confirmation_session_lease.py` :
3 transitions (session remplacée même compte/manifeste, époque fournisseur,
époque Core) **entre** préparation et confirmation, et **pendant**
l'observation du manifeste de préparation. Pour chaque refus, aucun GET,
aucune écriture archive/acquisition/placement/SourceReference/OriginLink.
Deux tests montrent qu'un compte secondaire à ID vide ne peut pas
préparer une confirmation avec la permission du primaire.

La suite de confirmation historique existante couvre le chemin nominal,
le token one-use, l'idempotence du second GET et les changements de
manifeste. Cette tranche est une réduction du risque D2, **pas** la
migration globale W2 ABI incomplète ou le cutover #3196.
