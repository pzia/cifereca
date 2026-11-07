# P0 #4858 — D4 : une seule autorité d'acquisition automatique Kobo

L'ancien réglage `sync.source.materialization.on_start.<source>` est
**une permission distincte** du consentement canonique CAS par compte-source
`sync.source.acquisition-policy.v1.<hash>`. Un ancien `on_start=1`
ne peut pas autoriser un deuxième GET si une politique source canonique
existe, même si celle-ci a été mise **OFF**, corrompue, révoquée, ou si
le fournisseur a temporairement perdu une partie de son ABI A→B.

`core/source_materialization_schedule.py::_configured_binding` refuse
désormais l'ancien planificateur W2 dès que
`source_sync_consent.read_policy` retourne autre chose que `unset`.
Une lecture du CAS indisponible provoque aussi un refus : une exception
n'est jamais interprétée comme « aucune politique ».

- **CAS ON + ABI complet :** seul le moteur canonique de Sync décide,
  sous la révision CAS et les preuves A→B de chaque offre.
- **CAS OFF ou invalide :** aucune acquisition Kobo automatique W2,
  même si l'ancien bit `on_start` reste visible dans les préférences.
- **CAS ON + ABI incomplet :** pas de fallback 0.7 automatique. Les
  offres non prouvées restent en attente de reprise explicitement sûre.
- **CAS absent :** l'ancien `on_start` conserve pour l'instant sa
  compatibilité stricte sur compte unique et session liée, sans
  requalification implicite en consentement canonique.

Ce refus ne supprime pas l'ancien bit, ne modifie pas la cadence,
n'avance pas le compteur d'essais et ne consulte aucun fichier ni
`backup_items` ; il intervient avant le dispatch W2.

Tests : `tests/test_core_p0_4858_d4_cas_schedule_no_legacy_get.py`
(portée exacte, OFF, ON, invalide, indisponible, compatibilité absent,
aucun GET W2 ni avancement d'horloge).

**Le cutover D4 n'est pas terminé** : tant qu'une session Kobo sans
politique canonique utilise encore W2, son checkpoint 0.7 reste
décisionnel pour l'ancienne reprise. #4858/#3196 restent P0 ouverts.
