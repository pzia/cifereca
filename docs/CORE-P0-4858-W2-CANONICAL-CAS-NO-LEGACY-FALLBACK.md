# P0 #4858 / W2–D2 — un consentement canonique éteint le second worker Kobo 0.7

## Écart vérifié

Le planificateur `on_start` neutralisait déjà le vieux worker
`backup_items` lorsque `sync.source.acquisition-policy.v1` était
configuré, y compris OFF ou invalide. Mais les entrées **manuelles**
`_sync_kobo_backup_locked` et confirmation D2 (ABI partielle)
pouvaient encore être déclenchées sous leur ancienne autorisation et
consommer manifeste, ADEPT ou GET. Une bascule CAS ne doit pas donner
au vieux chemin une permission de substitution lorsque le Core A→B
n'est plus exécutable.

## Tranche réalisée

- **Avant I/O** : le batch legacy entier vérifie le `ProviderRightsScope`
  exact `(identity, kobo, credential, account)` issu de la connexion
  SQLite et de l'unique compte de session. Une politique canonique
  `on`, `off`, `invalid` ou illisible refuse le chemin W2 avant
  scan local, ADEPT, manifeste ou écriture d'archive. L'ABI complet
  reste réservé au Core A→B existant.
- **Pendant le batch** : la validité du consentement est relue à
  chaque `manifest_source_current()` avant un GET et après les
  callbacks, et au dernier writer `_guarded_kobo_legacy_put`
  (y compris sans callback de session). Si la politique change,
  la reprise conserve ses checkpoints historiques non promus et
  ne publie pas un `complete` inventé.
- **D2** : l'aperçu/confirmation du vieux manifeste est refusé
  `canonical_source_policy_configured` avant de contacter le
  fournisseur. Un token obtenu auparavant ne neutralise pas la
  nouvelle politique.
- **Compatibilité** : lorsque le CAS exact est `unset`, l'ancien
  chemin reste utilisable sous ses gardes existants. Un compte
  primaire Kobo `account_id=""` est traité comme borrower exact
  et non comme un wildcard. Aucun autre fournisseur n'est modifié.

## Tests et limites

`tests/test_core_p0_4858_w2_legacy_cas_no_fallback.py` couvre
ON/OFF/invalid, entrée globale et ciblée, absence d'I/O avant refus,
bascule lors d'un callback de progression, dernier writer sans
callback, validation du token D2 et maintien du CAS unset.

Ce garde **ne constitue pas le cutover fonctionnel** des sessions
Kobo à ABI incomplète, ni une migration automatique des archives :
le moteur W2 peut toujours s'exécuter si aucune politique canonique
n'est configurée. Les P0 #4858 et #3196 demeurent ouverts.
