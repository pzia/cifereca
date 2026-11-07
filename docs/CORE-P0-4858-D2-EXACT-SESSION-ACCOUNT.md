# P0 #4858 — D2 : rattachement exact de la session Kobo à son emprunteur

## Défaut

Le broker `prepare_local_materialization` / `materialize_source`
sélectionnait la session via ses champs `provider_id/credential_id`
puis ne vérifiait sur `session.accounts[0]` que `unique_id`.
Un objet `Account` de session dont le provider, le credential ou
l'identité étaient modifiés, mais dont `unique_id` restait identique,
passait ce préflight. Une session déplacée sous une clé arbitraire de
`_sessions` restait également sélectionnable.

Ces chemins sont des décisions d'**aperçu et confirmation** : la
coïncidence d'un seul identifiant d'emprunteur n'est pas la preuve
d'une provenance `ProviderRightsScope` exacte.

## Correctif

Dans `core/source_materialization.py::_source`, en amont de toute
lecture de manifest/observation fournisseur :

- exiger que la session sélectionnée soit exactement celle de la
  clé `service._session_key("kobo", credential_id)` du registre ;
- vérifier `provider_id`, `credential_id`, `unique_id` et
  `identity_id` de l'unique `Account` de session ;
- accepter uniquement le label d'identité supplémentaire
  `"default"` du modèle historique Kobo-Vault, car le véritable
  propriétaire Core demeure vérifié séparément dans SQLite ;
- conserver l'emprunteur Kobo primaire `account_id=""` admis depuis
  #4967, les gates de consentement CAS, le token à usage unique,
  le verrou global et les gardes A→B / W2 existants.

Le rejet préflight est `ambiguous_account`, sauf session sous une
clé de registre périmée : `source_changed`. Aucun droit, GET, backup,
placement, migration ou suppression de fichiers n'est introduit.

## Vérification

`tests/test_core_p0_4858_d2_session_borrower_binding.py` utilise le
vrai broker/service SQLite avec sessions synthétiques. Les mêmes
scénarios sont exercés sur ABI absente (compatibilité W2) et ABI
canonique complète : profil/provider/credential/borrower étrangers,
alias périmé, mutation entre aperçu et confirmation, conservation
de l'alias fournisseur `identity_id="default"` pour un propriétaire
Core nommé. Les assertions excluent les appels provider, les GET et
les écritures avant admission.

Ce jalon sécurise D2 sans constituer le cutover W2 des fournisseurs
à ABI absente/partielle ni la sortie complète D1–D4.
