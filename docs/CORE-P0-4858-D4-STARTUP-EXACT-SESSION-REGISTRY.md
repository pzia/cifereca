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

Les fausses sessions `SimpleNamespace` de l'ancienne suite
`test_kobo_sync_modes` sont rangées sous une **clé tuple**
`("kobo", credential)`, malgré le `LibraryService` réel qui utilise
une clé normalisée en chaîne. Elles conservent cette voie de test
explicitement bornée. En revanche une véritable sous-classe
`ProviderSession` exige **toujours** la clé officielle, même si elle
se trouve sous cette clé tuple historique ; les alias arbitraires
restent refusés dans les deux formes. Les façades minimales sans méthode
`_session_key` gardent leurs gardes existantes de compte/credential.

La première CI (HEAD `1835a5e`) a échoué sur 17 tests existants et 2
tests ajoutés, parce qu'elle exigeait aussi la clé chaîne des doubles
`SimpleNamespace`. Le HEAD courant restaure cette compatibilité de
test sans assouplir les sessions `ProviderSession` réelles.

## Recette

`tests/test_core_p0_4858_d4_startup_exact_session_registry.py` :
avec SQLite et service réels (doubles `SimpleNamespace`), retirer la
session de sa clé tuple attendue et la replacer sous un alias, après
puis avant `on_start` :

- aucune session éligible, aucun GET W2, aucune horloge consommée ;
- préférence historique `on_start=1` conservée sans migration ;
- nouvelle programmation refusée sur alias ;
- réinscription du même propriétaire sous la clé attendue de nouveau
  éligible, sans transférer de droit entre emprunteurs ;
- une vraie session `ProviderSession` sous sa clé normalisée est
  admissible, mais replacée sous une clé tuple ne l'est **jamais**.

Ce correctif ne désactive pas la compatibilité W2 ABI partielle et ne
constitue pas la clôture du cutover #3196.
