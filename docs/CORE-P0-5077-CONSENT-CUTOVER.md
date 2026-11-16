# P0 #5077 — extinction du pont de consentement automatique (hors AST)

Date de l'audit : 2026-11-12. Dépôt : `pzia/cifereca`, référence `main`.
Ce document est un **plan de cutover vérifiable**, pas une migration exécutée.
Parent #5048 ; coordination #3171, #3719, #5054, #5055, #5518.

## Phase A — commande de diagnostic en lecture seule

L'outil `tools/audit_source_consent_cutover.py` fournit une première mesure
**agrégée**, sans valeurs ni identifiants de compte :

```bash
python3 tools/audit_source_consent_cutover.py /chemin/prive/vers/cifereca.sqlite3
```

Sortie JSON `cifereca.source-consent-inventory/1` : compteur agrégé et classification `legacy_modes` (`on_with_valid_revision`, `on_missing_or_invalid_revision`, `not_on`) ; ces catégories ne constituent **jamais** une preuve de droit ni une instruction de migration ON. Nombres de clés legacy
Numilog achat, Numilog epoch, BnF-Prêt, Kobo achat et CAS. Les lignes
`.revision` ne sont pas comptées comme toggles BnF/Kobo. L'outil ouvre
SQLite avec `mode=ro`, `query_only=ON` et un snapshot de lecture ;
il ne migre rien et ne produit aucun consentement. Sur base absente,
illisible ou schéma non reconnu, code retour 2 et diagnostic constant
sans divulgation du chemin. Exécuter sur une copie cohérente de la base
si un service utilise simultanément SQLite/WAL. Les compteurs seuls
ne prouvent ni l'opt-in exact ni l'éligibilité d'une migration.

## Invariants non négociables

1. La préférence d'inventaire ON ne devient **jamais** consentement d'acquisition ; ni catalogue, ni ancien droit, ni fichier local ne donnent une permission.
2. Une politique générique `source_sync_consent.policy_key(scope)` configurée, y compris `off` ou `invalid`, prime sur tout legacy. Une lecture impossible implique un refus fermé, jamais un fallback sur l'ancien ON.
3. Scopes exacts `identity_id/provider_id/credential_id/account_id`, plus `kind` et `SourceKey` de l'offre lors de la livraison ; pas de promotion entre comptes, types d'achat/prêt, identités ou comptes primaires.
4. Révision épinglée avant premier GET, revalidée juste avant GET et au `BEGIN IMMEDIATE` final. Un ON→OFF→ON donne une nouvelle révision ; la livraison ancienne échoue.
5. Un emprunt/GET potentiellement mutateur conserve son claim durable one-shot ; aucune reprise ne réémet l'action après résultat incertain.
6. Les collisions du compte `provider:credential:primary` et de l'ID primaire vide sont refusées sans réidentification heuristique.

## Contrats et consommateurs constatés

| Élément | Lecteur/écrivain | Usage restant | Condition de retrait |
| --- | --- | --- | --- |
| `_POLICIES` dans `core/source_auto_consent_bridge.py` | `toggle_state`, `_consent_generation_key`, `legacy_source_auto_policy` | Convention Numilog achat, Kobo achat, BnF-Prêt emprunt | Tous les scopes anciens traités, aucune autorisation implicite |
| `toggle_state(db, source)` | `core/source_acquisition_consent.py` | Admission par clé source sous snapshot SQLite | Lecture CAS générique + refus fermé si clé absente/invalide |
| `consent_generation` et `consent_generation_in_transaction` | `core/sync_coordination.py` | Generation pre-GET / fence de publication | Résolution CAS unifiée et même révision à chaque frontière |
| `legacy_source_auto_policy/current/in_transaction` | `core/sync_coordination.py` | Snapshot d'emprunt legacy + révision exacte | Migration sûre des préférences legacy de prêt, puis suppression du chemin |
| `_consent_generation_key(scope)` | `brokers.py` | Ecriture atomique du toggle d'inventaire et d'une revision ancienne | Tous toggles passent par la politique CAS de scope exact |
| `policy_key`, `read_policy`, `policy_in_transaction` | `core/source_sync_consent.py` | Contrat générique `cifereca.source-sync-consent/1` | **Conserver** ; ne pas déplacer l'autorité hors du Core |

### Préférences legacy explicites

- Numilog, achat : `sync.source.numilog.materialization.consent.v1.`, valeur ON `on_start:v1`, epoch `sync.source.numilog.materialization.epoch.v1.`.
- BnF-Prêt, emprunt : `sync.source.bnf_auto.`, ON `1`, révision `<prefix><source_id>.revision`.
- Kobo, achat : `sync.source.materialization.on_start.`, ON `1`, révision `<prefix><source_id>.revision`.

Le suffixe historique provient de `legacy_source_id` ; ne pas le recalculer avec l'encodage SHA-256 de `policy_key(scope)`. Les deux schémas sont différents.

### Contrat CAS existant

`source_sync_consent._decode` distingue `unset`, `invalid`, `on`, `off`. Le contenu porte `schema`, `scope`, `enabled`, `revision` ; une révision est 32 hex minuscules. `set_source_enabled` effectue une transaction `BEGIN IMMEDIATE`, écrit toggle d'inventaire et politique avec une nouvelle révision ; un inventaire réactivé ne réactive pas automatiquement les acquisitions.

## Migration proposée, ordonnée

**Phase A — lecture seule / preuve d'inventaire.**
Pour chaque installation test (anciennes lignes ON/OFF/invalides), inventorier les clés sans exporter ni logger les valeurs sensibles. Qualifier séparément le compte primaire vide, son alias littéral, les comptes désactivés et les scopes encore inconnus. Etablir quels producteurs écrivent aujourd'hui les anciens toggles.

**Phase B — code générique préalable.**
Ajouter un chemin d'upgrade transactionnel unique qui n'accepte que le scope exact et une provenance legacy explicitement reconnue ; aucune politique CAS `invalid`/`off` ne peut être remplacée par ON. Une ancienne absence de clé reste `unset`, pas ON. Une migration ne doit pas déplacer simplement `_POLICIES` dans un autre fichier Core. Si les anciennes installations ne permettent pas de prouver l'opt-in exact, conserver un refus et exiger une nouvelle action explicite.

**Phase C — bascule des consommateurs.**
Migrer d'abord `brokers.py` pour que la bascule de source écrive uniquement l'état générique, puis `sync_coordination.py` pour supprimer la promotion loan legacy, puis `source_acquisition_consent.py` pour interroger la politique CAS, chaque fois avec relecture exact-scope et révocation sous transaction. Préserver la différence entre opt-in de source et permission individuelle.

**Phase D — destruction de la compatibilité.**
Après preuves de couverture et rollback : retirer `_POLICIES`, `_consent_generation_key`, les `legacy_source_auto_*` sans consommateurs, puis le bridge si ses fonctions restantes sont intégralement remplacées. Garder les anciennes clés SQLite comme données inertes tant que le plan de migration de base/rollback ne prouve pas leur purge sûre.

## Matrice de tests à verrouiller avant fusion

| Cas | Résultat exigé |
| --- | --- |
| Legacy ON exact + epoch valide + CAS unset | Aucun droit inventé ; migration seulement si preuve exacte et transactionnelle |
| Legacy ON sans epoch, epoch invalide / lecture en erreur | Refus, zéro GET |
| CAS OFF, CAS invalid, CAS illisible + legacy ON | Refus, aucun fallback |
| CAS ON puis OFF puis ON pendant livraison | Ancienne révision rejetée avant GET/publication |
| Inventaire OFF puis ON | Pas de consentement d'acquisition créé automatiquement |
| Compte A / B, identity A / B, credential A / B | Aucune admission croisée |
| Emprunt BnF partiellement observé | Aucune clôture ni promotion de permission |
| `account_id=""` vs ID réel `provider:credential:primary` | Refus de la préférence ambiguë |
| Crash entre claim, GET et publication | Reprise sans doublon de mutation |
| Politique absente, fournisseur inconnu ou descripteur incomplet | Refus fermé et diagnostic assaini |
| Upgrade puis rollback | Pas de résurrection de l'ancien ON ; aucun fichier utilisateur supprimé |

## Gates

- Tests ciblés : `tests/test_core_p0_3171_source_auto_consent_bridge.py`, `tests/test_core_p0_3171_kobo_consent_aba.py`, `tests/test_core_p0_3719_legacy_source_toggle_aba.py`, `tests/test_core_p0_3719_primary_legacy_consent_collision.py`, `tests/test_core_p0_5077_generic_legacy_epoch.py`, `tests/test_core_p0_5077_transactional_cas_refusal.py`.
- Exécution réelle des tests, CI Core, installation/rollback au **HEAD exact** de chaque PR de code. `pytest --collect-only` ne vaut pas exécution.
- Afficher les fonctions/consommateurs supprimés et les chemins de compatibilité résiduels ; ne pas déclarer le ticket clos sur un compteur lexical ou un relevé AST.

## Cutover exécuté — décisions de consentement legacy **prêt** (16/11/2026)

Référence : P0 #5975 / P-1 #5077. Le chemin actif
`sync_coordination._run_observed_canonical_convergence` n'admet plus le
`legacy_source_auto_policy(..., "loan")` comme consentement, même en présence
d'un ON historique et de sa révision valide. Il ne construit plus de liste de
droits admissibles depuis cet ancien interrupteur, et ne conserve aucun
appel de revalidation `legacy_source_auto_current/in_transaction` pour le prêt.

La frontière transactionnelle `source_auto_consent_bridge.toggle_state`
interprète désormais un interrupteur historique de prêt **présent** comme
`off` en l'absence de politique CAS explicite, y compris si l'ancienne
valeur était ON et que son epoch est bien formé. Les anciens helpers
`legacy_source_auto_current` et `legacy_source_auto_in_transaction`
refusent également tout `SourceKey(kind="loan")`. Ils restent disponibles
uniquement pour les anciens achats, sous leur compatibilité préexistante.

Un consentement d'acquisition source CAS `cifereca.source-sync-consent/1`
saisi explicitement par l'utilisateur autorise de nouveau le parcours,
sous réserve des preuves de source, d'offre, de compte, du claim one-shot
et du contrôle `BEGIN IMMEDIATE`. La révision CAS est épinglée avant
GET ; une séquence OFF→ON invalide les tentatives précédentes. L'ancien
ON ne crée jamais le nouveau CAS. Les grants exacts préexistants sont
conservés, mais une ancienne politique de prêt explicite les bloque
jusqu'à ce nouvel opt-in pour éviter l'ambiguïté entre autorisations.

**Compatibilité maintenue** : les clés SQLite historiques, les migrations,
les lecteurs de diagnostic et le retrait atomique ON→OFF restent conservés.
Leurs lignes ne sont pas effacées et ne deviennent pas un nouveau droit.
Le pont legacy **achats** Numilog/Kobo et les rotations d'epoch legacy
d'inventaire demeurent des tranches #5077 distinctes : cette PR ne
revendique ni extinction de ce second parcours ni réduction AST non mesurée.

**Recette ciblée** : refus du prêt BnF avec ON historique versionné, succès
après opt-in CAS exact, refus au changement de CAS OFF / OFF→ON pendant la
livraison, absence de nouvelle publication ou double requête, CAS corrompu
et prêts à provenance inconnue. CI Core/provider/AST + installation/rollback
au HEAD exact requis avant intégration.

## Cutover exécuté — extinction de l'autorité legacy **achat** (16/11/2026)

**Dépendance d'intégration** : PR P0 prêt automatique #6462, puis la présente
tranche achat. Elles partagent les frontières `source_auto_consent_bridge`
et les tests de #3171 ; intégrer dans cet ordre ou rebaser explicitement.

Le chemin actif de publication source A→B n'accepte plus `ON` depuis
les clés `sync.source.numilog.materialization.consent.v1.*` ou
`sync.source.materialization.on_start.*`, même lorsque l'ancienne révision
est valide. `toggle_state` retourne `off` sur toute ancienne clé
source-wide présente, quel que soit son état ou sa catégorie
(`purchase`/`loan`). Le CAS source exact explicite prend priorité,
sinon seuls les consentements individuels exacts non frappés d'un veto
historique restent possibles.

Les ABI `legacy_source_auto_current` et
`legacy_source_auto_in_transaction`, désormais sans appelant productif,
renvoient toujours False. `legacy_source_auto_policy` demeure un
**lecteur descriptif** de l'ancienne valeur, non une permission. Ce retrait
ne s'effectue pas en déplaçant simplement le dispatch vers un autre module ;
le flux courant ne tire plus de consentement positif des anciens noms de
fournisseurs.

Une politique `cifereca.source-sync-consent/1` CAS ON, émise par le choix
explicite de l'utilisateur pour le fournisseur/credential/compte exact,
autorise à nouveau la convergence uniquement après preuves d'offre, de
source, de connexion et de destination ; la révision CAS protège le GET et la
publication atomique. OFF→ON exige une nouvelle révision et invalide les
opérations démarrées avant la révocation.

Aucune clé, ancienne révision, migration, artefact ou possession privée n'est
effacée ; la compatibilité des toggles d'inventaire est conservée pour les
installations anciennes et leur rollback, sans réouverture des vieux GET.
Les helpers d'inventaire/génération `_POLICIES` et
`_consent_generation_key` subsistent pour une coupe ultérieure du **lecteur
historique et des écritures d'epoch**, après vérification des upgrades.
Aucune réduction AST n'est revendiquée sans inventaire au HEAD exact.

Les tests Kobo/Numilog/BnF contrôlent ON legacy conservé mais inerte,
consentement CAS ON explicite, compte primaire vs alias littéral,
CAS corrompu/non lisible, et ABA après callback avant publication. La suite
CI + AST + E2E + installation/rollback demeure obligatoire avant merge.
