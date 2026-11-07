# P0 #4858 — D4 : session Kobo on_start sous clé de registre exacte

## Écart

Le planificateur D4 `_configured_binding` vérifiait le propriétaire
SQLite, le compte Core, les consentements CAS et l'unique compte
fournisseur, mais parcourait seulement les **valeurs** de
`LibraryService._sessions`. Une ancienne session Kobo déplacée sous
une clé de registre périmée ou arbitraire satisfaisait toujours ces
coordonnées ; son bit d'autorisation `on_start` pouvait lancer le
coordinateur de synchronisation, incrémenter l'horloge d'essai
persistée et être ensuite rejeté par W2 (qui exige la clé exacte).

Cette divergence est un faux déclenchement et une décision D4 positive
fondée sur un enregistrement de session désormais détaché.

## Correctif

Pour le vrai `LibraryService`, D4 exige désormais que l'objet
Kobo choisi sous `_sessions` soit strictement celui indexé par
`_session_key("kobo", credential_id)`, exactement comme les
préflights W2 et D2. Les autres barrières sont conservées, dont
`is_primary is True` pour `account_id=""` et le veto CAS ON/OFF/invalid.
Les doublons de session restent refusés.

Les façades minimales de tests sans méthode `_session_key` utilisent
leurs contrôles historiques de coordonnées ; il ne s'agit pas d'une
permission supplémentaire dans le runtime de production.

## Recette

`tests/test_core_p0_4858_d4_startup_exact_session_registry.py` :
avec SQLite et service réels, retirer la session de sa clé exacte et
la replacer sous un alias, après puis avant `on_start` :

- aucune session éligible, aucun GET W2, aucune horloge consommée ;
- préférence historique `on_start=1` conservée sans migration ;
- nouvelle programmation refusée sur alias ;
- réinscription du même propriétaire sous la clé exacte de nouveau
  éligible, sans transférer de droit entre emprunteurs.

Ce correctif ne désactive pas la compatibilité W2 ABI partielle et ne
constitue pas la clôture du cutover #3196.
