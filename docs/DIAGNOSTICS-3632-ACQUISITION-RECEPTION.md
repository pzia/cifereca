# Réception Diagnostics des acquisitions — #3632 / #2888

## Périmètre et contrat

Audit sur `main` 1fcdcdcd1. Ce correctif ne déclenche aucune acquisition,
aucun appel fournisseur, aucun traitement ADEPT. Les fichiers acquisition,
Sync, SQLite et Plugins restent aux agents A/B. Le journal canonique demeure
la source du broker, de la recherche et de l'export.

Consigne spécifique #3632 : les causes, identifiants métier ordinaires, chemins
et URLs ordinaires restent visibles ; seules les valeurs de credentials sont
retirées des observations d'acquisition reconnues avant leur persistance.
Cette consigne prime ici sur le contrat historique de journal brut de #3132.
Elle ne réécrit pas les anciens journaux ni les autres familles de traces.

L'adaptateur reconnaît exclusivement les templates WARNING/ERROR du logger
Core `cifereca`, sans deuxième émission. Il conserve l'exception originale et
sa chaîne, le message et la phase réellement fournis. Refus/échecs sont ERROR
comme leur terminal typé ; `observed_severity` conserve le niveau émetteur.
Les autres TRACE ne deviennent pas publics par proximité ou par mot-clé.

## Matrice d'audit

| Observation émise | Cause réellement disponible | Identifiants et phase | Réception après correctif |
| --- | --- | --- | --- |
| `download_loan/1` Core | Code fermé ; cause optionnelle typée si fournie | `provider_id` attesté par registre ; `task_ref` du worker courant ; `loan_id` optionnel fourni ; admission/terminal | Mémoire, journal, recherche et export conservent les champs validés ; refus ERROR dans les deux projections |
| Explicit recovery : admission refusée | `exc_info`, code et chaîne originaux | `observed_provider_id`, `loan_id`, admission | Conservée au niveau normal ; corrélée au terminal uniquement par le vrai worker |
| Explicit recovery : provider/unexpected failure | `exc_info` et chaîne originaux | `observed_provider_id`, `loan_id`, terminal | Conservée au niveau normal, cause consultable et recherchable |
| Explicit recovery : attention requise | `exc_info` original | `observed_provider_id`, `loan_id`, terminal | WARNING conservé ; aucune conversion arbitraire en panne réseau |
| Automatic admission blocked | Raison fermée fournie ; pas d'exception | `request_ref` ou `loan_ref`, admission | Refus conservé, références non requalifiées en identifiant de prêt ou de tâche |
| Inner download provider error | Code `provider_or_artifact_failure` seulement | `loan_ref`, phase non fournie | Conservé avec `cause_state=not_supplied`, statut inconnu ; handoff A pour cause perdue |
| BnF automatic local evidence/receipt check | `exc_info` fourni | IDs fournis ; étape exacte si fournie | `local_evidence_cause`, statut inconnu, aucune acquisition/GET inventée |
| BnF automatic delivery/import failure | `exc_info` fourni | IDs fournis, étape delivery_or_import | Échec observé ; aucune attribution réseau ou ADEPT inférée |
| Annulation explicite | Terminal cancelled ; émetteur sans cause | Provider attesté, vrai worker, terminal | Annulation conservée, signal propagé ; aucune exception inventée |
| Inventaire partiel / absence de tentative | Événements d'inventaire existants / aucun événement d'acquisition | Provenance inchangée | Inventaire jamais requalifié en téléchargement ; lecture d'éligibilité seule sans événement d'acquisition |

Une cause et un terminal sont deux occurrences distinctes, pas deux tentatives.
Leur corrélation utilise seulement `task_ref` réellement produit dans le contexte
du worker. Sans contexte, aucune référence n'est créée par rapprochement temporel.
Un `observed_provider_id` de message n'est jamais promu en provider attesté.
Un texte contenant « 403 » ne suffit pas à créer un `http_status` : ce champ
optionnel doit être explicitement fourni, entier 100–599.

## Protection ciblée

Messages, causes, chaînes, piles et champs optionnels reconnus retirent les
valeurs explicitement marquées password/passphrase, tokens, API/client secrets,
Authorization/Cookie/Set-Cookie, mots de passe d'URL, signatures de query,
clés privées PEM et éléments ADEPT privateKey/licenseKey/activationToken/pkcs12.
Les noms de champs, usernames ordinaires, IDs et autres paramètres restent.
Le filtre est idempotent ; l'exception originale n'est jamais réappendue après
le JSON protégé. Les arguments malformés ne provoquent pas de repli brut.

Limite explicite : une suite arbitraire non étiquetée ne peut être reconnue
comme secret. Les producteurs ne doivent pas concaténer des secrets non marqués
dans leurs exceptions. Les dumps HTTP historiques hors de ces templates ne
sont pas couverts par cet adaptateur ; ne pas en activer pour cette recette.
Le journal et ses rotations gardent les permissions privées existantes (0600).

## Preuves synthétiques reproductibles

`tests/test_diagnostics_acquisition_fidelity.py` exerce le vrai wrapper Core
explicite, le formatter/filtre réels, un journal temporaire, le broker réel,
ses alias de source, la recherche, l'export JSON/NDJSON et la transformation
d'affichage Core. Les appels internes sont simulés : ce n'est pas un E2E
fournisseur, ni une validation visuelle GTK.

Les assertions couvrent cause chaînée, refus avant appel, annulation, inventaire
partiel, absence de tentative, erreurs sans cause, références concurrentes de
deux workers, nettoyage du contexte, secrets synthétiques absents de fichier et
console identiques, identifiants présents/recherchables, sévérités cohérentes,
champs invalides rejetés, tailles Unicode et absence de mutation du LogRecord.

Validation locale : 38 nouveaux tests ; 236 tests ciblés #2888/#3132/#3133 et
conservation des diagnostics réussis. Suite Diagnostics élargie : 889 réussis,
10 xfailed (`PYTHONPATH=src`, sélection des tests diagnostic/logging/#2888/#3132/#3133).
La PR et le commentaire de réception
#3632 consignent le SHA exact, la CI et le verdict de la suite élargie.

```sh
python -m pytest -q tests/test_diagnostics_acquisition_fidelity.py \
  tests/test_core_p1_2888*.py tests/test_public_diagnostic_log_retention.py \
  tests/test_core_p1_3132*.py tests/test_core_p1_3133*.py
```

## Handoffs Core A et recette réelle #2888

Émetteurs à compléter par A, sans modification dans cette PR :

- `services.py`, inner download : `TRACE download provider error` ne fournit
  pas `exc_info`. Les retours executor/retryable ne fournissent pas toujours la
  cause du processus. Diagnostics ne peut pas reconstruire ces informations.
- `core/bnf_explicit_acsm.py` : conversion de l'exception fournisseur par
  `_refuse(provider_reason(exc))` perd le détail original.
- Branche `Cancelled` du wrapper : terminal conservé, cause originale non émise.
  Fournir une cause observée si elle est nécessaire, sans inventer une panne.

Ces constats sont à reprendre dans le suivi A/#2888. Le correctif Diagnostics
est recevable indépendamment ; #2888 reste ouvert jusqu'à recette autorisée.

Recette Paris puis BnF, uniquement après autorisation explicite des opérations :

1. Relever version/SHA, fournisseur et identité de connexion, inventaire complet
   ou partiel, disponibilité réelle de l'action, et état du prêt initial.
2. Pour un refus avant appel, vérifier phase admission, code et cause, absence
   d'appel acquisition constatée par instrumentation autorisée ; ne pas affirmer
   « aucun GET » sur la seule absence de ligne réseau.
3. Pour une acquisition explicitement autorisée, relever le vrai `task_ref`,
   les IDs métier attestés, les phases et l'unique terminal. Consigner seulement
   les appels/HTTP effectivement observés ; vérifier le résultat local réel.
4. Pour un échec ou une annulation autorisés, vérifier cause disponible, statut
   exact, absence de faux succès et signal d'annulation ; si cause non fournie,
   rattacher au handoff émetteur au lieu de fabriquer un diagnostic.
5. Comparer les mêmes occurrences dans journal, recherche par ID/task, affichage
   des détails et export confirmé. Relever origine/position, phase, code, cause,
   provider et IDs ; seule la pseudonymisation visuelle login/password diffère.
6. Vérifier l'absence des credentials réels dans les nouvelles observations
   reconnues sans les copier dans un ticket ; stocker les preuves privées selon
   la politique existante. Publier uniquement les résultats et références sûres.
7. Joindre à #2888 une matrice datée PASS/FAIL/non exécuté Paris/BnF et les
   limites amont. Un test synthétique, un bouton indisponible ou un inventaire
   seul ne constituent jamais la preuve d'une acquisition réelle réussie.

Aucune étape fournisseur de cette liste n'a été exécutée pour #3632.

## 29 octobre 2026 — corrélation canonique des terminaux worker (#2888)

Dans `cifereca-journal-brut4(1).zip`, la fenêtre du 29 octobre
06:59:23–07:02:44 Europe/Paris représente **434 occurrences physiques**
distinctes. Quatre lignes Core `TRACE task correlation` possèdent un
`task_ref` de 12 hexadécimales et un `status` exact, mais le lecteur canonique
ne renseignait pas ces **champs déjà observés** : `task_ref` manquait et
`status` était `unknown`. La page Diagnostics ne pouvait donc pas joindre
la terminaison worker aux événements `source_sync` par preuve de référence.

Le lecteur canonique reconnaît désormais uniquement la ligne entière
`TRACE task correlation task_ref=<12hex> status=success|failed|cancelled`,
émise sous le logger exactement `cifereca`, avec les niveaux respectifs
`INFO`/`ERROR`/`WARNING`. Une ligne tronquée, une référence étrangère,
un suffixe libre, un autre logger ou une gravité incohérente ne constituent
**pas** une preuve de tâche. Cette projection n'ajoute **ni événement, ni
opération, ni provider, ni GET, ni statut métier** : elle restitue le terminal
du worker et son identifiant physique original. Les résultats métier Core
restent séparés ; `worker=success` ne signifie pas `rights=success`.

Les tests `tests/test_core_p0_2888_journal_exact_worker_scope.py` et
`src/plugins/diagnostics-page/tests/test_p0_2888_canonical_worker_groups.py`
exercent la lecture physique, les rotations, le rejet des faux positifs et
le regroupement GTK sur cette référence exacte sans inférer de causalité
par heure/message. Cette réparation est **synthétique** : le P0 #2888
reste ouvert pour la recette fournisseur réelle explicitement autorisée.

## 29 octobre 2026 — filtre fournisseur des avis de prêts historiques (#2888)

La fenêtre **29 octobre uniquement** du ZIP d'investigation contient trois
occurrences `ACQUISITION / loan sync` émises par le Core : leurs données
`detail.provider=paris` et `detail.status=attention_required` sont
effectivement enregistrées ; l'ancien lecteur ne filtrait que
`detail.provider_id` et les perdait lors de `provider_id=paris`.
Les deux refus automatiques `cross_identity_loan` observés sont une famille
distincte, sans fournisseur canonique attesté par leur seule trace.

Le journal conserve le détail original et indexe **seulement** l'ancien
`ACQUISITION/loan sync` du logger exact `cifereca`, avec la cohérence
`warning/attention_required` ou `error/retryable_error`, un code et un
identifiant de prêt réellement présents, et `provider` égal au slug
`paris` ou `bnf-pret`. Le champ ajouté
`provider_id_origin=legacy_opening_loan_sync` évite de présenter cette
observation historique comme une nouvelle attestation de droit ou une
identité de compte. Les messages libres, faux logger, opération différente,
code invalide, état incohérent, ADEPT et causes d'admission automatiques
n'acquièrent aucun fournisseur par ressemblance.

`query_journal(provider_id=...)`, le broker, la recherche, les détails et
les exports réutilisent **la même occurrence physique**, jamais un second
événement ni un GET. Tests synthétiques :
`tests/test_diagnostics_p0_2888_legacy_loan_provider.py`.
Aucune expérience fournisseur réelle n'est revendiquée : #2888 demeure
ouvert pour la recette explicite Paris/BnF.
