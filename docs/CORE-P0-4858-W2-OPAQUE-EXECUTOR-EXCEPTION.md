# Core P0 #4858 W2 — Exception sans retour de l'exécuteur opaque

## Défaut

Le verrou W2 `acquiring` est persisté **avant** l'appel au fournisseur
Kobo/ADEPT. Une exception levée **pendant** cet appel ne prouve pas que le
GET n'a pas livré de contenu : l'exécuteur peut écrire le fichier et échouer
ensuite avant de retourner un `Acquisition`. L'ancien traitement
`except` enregistrait `backup_items.status=error`, autorisant la
réexécution automatique du GET au manifeste suivant.

## Invariant livré

- Avant l'entrée dans `_execute_kobo_acquisition` : une erreur de
  normalisation/garde laisse le comportement historique `error` ; aucun GET
  n'a été appelé.
- Dès l'entrée dans l'exécuteur : une exception **ambiguë** laisse le
  checkpoint SQLite `acquiring` inchangé, rapporte un échec et une
  réconciliation obligatoire, sans créer de SHA, OriginLink, Placement ni
  droit canonique fictif.
- Les signaux fournisseur de refus explicite
  `missing_delegation_scope` (403 délégation) et `archived_book` (409)
  gardent leur traitement antérieur : blocage de scope / archivé, sans
  acquisition d'octets. `result_unparseable_artifact_present` conserve
  son chemin de rapprochement existant.
- La reprise après redémarrage reconnaît `acquiring` et n'émet aucun
  second GET ; une preuve de livraison ou une décision supervisée est
  nécessaire pour dénouer l'incertitude.

Tests : exception après écriture réelle d'un EPUB, exception sans fichier,
erreur fournisseur 503, redémarrage SQLite, absence de liens canoniques
inventés et normalisation invalide avant appel de l'exécuteur.

Ce verrou privilégie l'**at-most-once** à une rétentative implicite sur
timeout fournisseur non attesté. Il reste un palliatif W2 pour ABI
incomplète, et ne clôt ni #4858 ni le cutover global #3196.
