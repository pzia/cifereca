# P0 #4858 W2 — commande globale Kobo sur comptes indépendants

## Rupture du verrou/exécuteur global 0.7

Le point d'entrée `LibraryService.sync_kobo_backup()` (donc la commande
`cifereca kobo-sync`/`kobo-backup`) énumère passivement les sessions Kobo
**connectées** et leurs ABI déclarés, sans appel fournisseur.

- **Aucun ABI complet** : la voie historique avec son verrou et ses gardes
  reste conservée, jusqu'au retrait final W2.
- **Au moins un ABI complet** : aucun appel à la boucle globale
  `kobo_backup.sync_kobo_backup` ; chaque credential Kobo sélectionné passe
  par `sync_kobo_backup_account(credential)`, avec un **verrou d'acquisition
  propre à cette source**.
  - ABI complet : reprise canonique A→B introduite par #4892, consentement
    source explicite, borrower/identity exacts, preuve d'offre/de fichier,
    publication atomique B, aucun `backup_items`.
  - ABI partiel : reprise historique toujours gardée et isolée dans
    l'**autre** transaction ; elle ne peut écrire pour une source A→B
    devenue canonique après sélection.
- Une collision de credential/session est refusée **avant** toute exécution.
  Après sélection, tout changement d'une session classée canonique provoque
  un échec expurgé du compte ; elle ne bascule jamais vers un GET v0.7.
- Les contretemps d'une source ne bloquent pas les comptes indépendants.
  La sortie globale agrège uniquement des métriques numériques bornées et
  états fermés, sans credential/borrower, URL, ISBN, titre ou chemin. Les
  erreurs d'un fournisseur ne sont jamais reflétées.

La CLI `kobo-sync` utilise les mêmes événements `source_done` et `done`,
mais aucune ligne source brute. Les vérifications A→B et la reprise après
interruption sont réévaluées par **chaque** Sync Core au moment de son
propre GET ; l'énumération globale n'est pas un consentement d'acquisition.

## Conditions de clôture

Cette tranche ne supprime **pas** la boucle W2 du cas intégralement ABI
partiel ni les writers 0.7 des compatibilités gardées. Elle ne supprime
aucun historique de restauration, `backup_items` ou miroir W1.

Recette : `tests/test_core_p0_4858_w2_global_canonical_batch.py` :
session unique canonique, mélange moderne/partielle, erreur privée de source
expurgée et tolérée, changement de session entre deux comptes, credential
ambigu et mode tout historique conservé. Les scénarios de droits/GET
canoniques restent couverts par les suites A→B et #4892.
