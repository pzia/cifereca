# P0 #4858 W2 — reprise Kobo ciblée par compte, moteur A→B

## Parcours désormais exécutable

Le point d'entrée public `LibraryService.sync_kobo_backup_account(credential_id)`
et donc `cifereca kobo-resume --credential …` sélectionnent le **Sync canonique
du Core** si la session Kobo connectée possède **un ABI de livraison complet**
(source A, offre sélectionnée, candidat de livraison).

1. Sélection stricte d'une session connectée, d'un seul `Account` Core de
   credential exact et d'un unique emprunteur identique au scope fournisseur.
   Le propriétaire et le borrower sont également vérifiés dans SQLite.
2. Le consentement `sync.source.acquisition-policy.v1` doit être ON pour
   le `ProviderRightsScope` exact. Un ancien bouton backup/on-start ou un
   `backup_items.status=complete` n'autorise jamais la livraison.
3. L'adaptateur **n'acquiert pas le verrou à l'avance** : il appelle une
   seule fois `LibraryService.synchronize_account_source(account, control=…)`,
   qui possède son propre verrou global et la chaîne `A → offre → GET →
   CompletedDelivery → B`. Aucun worker W2 0.7 ne s'exécute dans cette
   branche, y compris après échec, interruption, rebind ou ABI partiel tardif.
4. Une réponse manquant `canonical_execution` ou ses neuf compteurs
   est **partielle**, jamais un succès inventé. Les compteurs legacy de la
   CLI restent expurgés, dérivés exclusivement de ces compteurs canoniques.
   Reconnexion d'emprunteur, désactivation de la source, révocation de la
   révision de consentement et changement d'époque annulent l'acceptation
   du rapport. Les callbacks `cancelled` sont propagés au moteur Core.
5. Les sessions **sans ABI complet** gardent provisoirement la voie de
   compatibilité existante avec ses gardes, pour permettre une migration
   incrémentale sans casser les reprises historiques.

La tentative `--retry-scope-blocked` de la délégation v0.7 n'est pas
une nouvelle permission A→B : sur ABI complet elle est explicitement
refusée. Une politique source absente/off ne déclenche pas le GET et
n'est pas interprétée comme un succès.

## Exécution

```sh
cifereca kobo-resume --credential <credential-kobo>
```

Pour une seule édition, utiliser la voie introduite par #4878 :

```sh
cifereca kobo-sync-edition --credential <credential-kobo> \
  --item <edition-id> --confirm
```

## Limites et tests

La commande globale sans choix de compte `kobo-sync` et les sessions
à ABI incomplet restent des entrées W2 à migrer avant clôture de #4858.
On-start est déjà séparé des consentements canonique et historique, mais
son cutover doit être prouvé indépendamment. Aucun effacement de
`backup_items` ni de miroir W1 ici.

Test : `tests/test_core_p0_4858_w2_scoped_canonical_resume.py`. SQLite
et session Kobo locales, callbacks réseau interdits, neuf statuts
canoniques, idempotence/réception déjà attestée, absence de fallback W2,
CAS OFF, source ambiguë/désactivée, changement de borrower/session,
cancellation, conservation du cas ABI partiel. La publication réelle
reste prouvée par les suites canoniques A→B existantes.
