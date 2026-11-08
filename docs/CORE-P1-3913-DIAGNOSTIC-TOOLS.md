# P1 #3913 — registre opératoire des outils de diagnostic

**Référence vérifiée sur `main` au 01/11/2026.** L'entrée opérateur recommandée est
`cifereca-cli` ; les analyseurs spécialisés restent autonomes pour les données
déjà collectées. Ce document distingue **l'effet métier** du **démarrage
technique de Core** : même une commande d'observation pouvant appeler
`CiferecaCore.open()` ne garantit pas *zéro* écriture de configuration,
SQLite ou journal à l'initialisation. Les outils totalement indépendants de
Core sont identifiés séparément.

**Périmètre de vérification :** déclarations `[project.scripts]` dans
`pyproject.toml`, parseur et branches de `src/cifereca/cli.py`, modules de
production cités ci-dessous, scripts autonomes découverts par leurs
`ArgumentParser` et/ou `__main__`, et distinction des scénarios CI/E2E.
Le dépôt ne contient pas de répertoire `tools/` à cette date. Les utilitaires
CI, packaging et simulation sont classés mais **ne sont pas des points
d'entrée d'audit de la bibliothèque réelle**. Ce registre n'est pas une
preuve d'exécution d'un compte fournisseur authentifié.

## Légende : effets réels, pas étiquettes marketing

| Classe | Sens | Effet à anticiper |
|---|---|---|
| **O** | Observation métier locale | Lecture SQLite/FS ; démarrage Core possiblement initialisant des fichiers techniques. Pas de connexion fournisseur par le chemin décrit. |
| **F** | Observation de fichiers *autonome* | Aucun Core, SQLite fournisseur ni réseau ; lecture d'un fichier déjà fourni, sortie standard. |
| **E** | Export local explicite | Lecture et création d'un **nouveau** fichier à l'emplacement demandé, pas d'écrasement. |
| **N** | Requête fournisseur | Peut envoyer HTTP/authentifier ; ne vaut jamais permission de téléchargement ni preuve de droit. |
| **M** | Modification | Peut muter SQLite/fichiers, télécharger, installer, nettoyer ou supprimer. Confirmation et portée exactes requises quand prévues. |
| **T** | Test, benchmark, simulation, CI | Peut créer des données temporaires, exécuter des processus, et/ou modifier des artefacts de test ; pas un audit de l'utilisateur. |

`--json` change le format de présentation seulement. Le format des rapports
**fermés** (diagnostic ciblé / comptages) n'est pas celui du journal **brut** :
les ZIP de trace HTTP ou de journal peuvent contenir des credentials, cookies
et URLs signées. Aucune collecte ou publication automatique.

## Contrat public `cifereca-cli` — diagnostic et actions associées

Toutes les commandes ci-dessous proviennent de `src/cifereca/cli.py`.
Les sélecteurs facultatifs de source sont les identifiants **locaux** exacts ;
ne jamais choisir arbitrairement un compte dans une identité tierce.

| Commande / options attestées | Classe | Sortie, preuve et limites |
|---|---|---|
| `doctor [--json] [--verbose]` | O | Bilan Core/runtime/DB/config/plugins ; lecture locale, pas de sync explicite. Ouvre Core ; pas de garantie « aucun changement de fichier technique ». |
| `plugin list` ; `plugin inspect PLUGIN_ID` ; `plugin diagnose [PLUGIN_ID]` ; `plugin packages` | O | JSON des manifests, versions, états de plugin/erreurs de chargement. Ne prouve aucune livraison fournisseur. |
| `plugin config-get PLUGIN_ID [--scope-id ID]` | O | Valeurs locales de configuration ; **peut inclure des credentials** selon le plugin : ne pas diffuser sans contrôle. |
| `sync list [--provider P] [--identity I] [--credential C] [--account-id A] [--json]` | O | Liaisons persistées et statut ON/OFF, sans GET de fournisseur. Ne confondre ni source absente ni preuve d'absence de livres. |
| `acquisition-audit --provider P --identity I [--credential C] [--account-id A] [--verify-files] [--json] [--output FICHIER.json]` | O ; E avec `--output` | Audit canonique local scoped 0/1/N comptes. `--verify-files` lit et hache des fichiers existants ; `--output` publie un JSON neuf 0600 ; ni réparation ni HTTP. Scopes ambigus refusés. |
| `sync run --provider P --identity I [--credential C] [--account-id A] --confirm [--json]` | **N+M** | Exécute la synchronisation d'une unique source active persistée ; `--confirm` obligatoire, source OFF non contournée ; 0/plusieurs comptes refusés avant réseau. **Ne jamais suggérer comme simple diagnostic passif.** |
| `sync run ... --report RAPPORT.zip [--verify-files]` | **N+M+E** | ZIP privé 0600 : `manifest.json`, `sync-result.json`, audits avant/après, `events.jsonl` et `summary.txt`. Phases CLI, compteurs et observations SQLite ; SHA bornés sur demande. `summary.txt` distingue audit avant/après disponible ou non, classe les références et reçus locaux observés et signale les échantillons tronqués. **Les variations entre snapshots ne prouvent pas la causalité du run ni la finalisation d'un Placement ou OriginLink.** Le manifeste signale `event_coverage=cli_milestones_only` : **pas** de journal Core corrélé complet ni preuve HTTP. |
| `sync run ... --trace-export TRACE.zip --confirm-trace-export` | **N+M+E** | ZIP **HTTP brut** capturé sur la sync sélectionnée, séparation obligatoire d'avec `--report` ; confirmation supplémentaire. Peut contenir les secrets de session. |
| `statistics [--identity I]` ; `catalog-list [--state local|missing]` ; `acquisition-ledger [--status discovered|pending|acquiring|complete|retryable_error|attention_required]` | O | JSON de compteurs, catalogue et ledger locaux ; le ledger seul ne certifie pas SHA/Placement/OriginLink. |
| `kobo-sync-errors` ; `kobo-backup-errors` | O | Rapport local d'erreurs de backup. Les deux noms sont alias historiques ; ne déclenchent pas une sync. |
| `kobo-audit [--credential C] [--plan]` | O | Audit local Kobo et, si demandé, plan de vérifications manuelles, sans téléchargement ni mutation de bibliothèque. |
| `kobo-resume-preview --credential C [--verify-files]` | O | Preview à partir des données locales ; option SHA/FS. |
| `kobo-resume-preview --credential C --refresh-manifest` | **N** | **Exception** : lecture explicite du manifeste distant ; ne plus classer cette variante « hors réseau ». Pas de téléchargement de livre par la seule preview. |
| `paris-adept list [--credential C]` ; `paris-adept audit [--credential C]` | O | Liaisons et prêts historiques locaux ; aucune autorité Adobe déduite d'une coïncidence. |
| `paris-adept audit-origin --loan-id L --item-id B` ; `audit-canonical --loan-id L --item-id B` ; `audit-transaction --loan-id L --item-id B` | O | Constat local exact des preuves historiques/canoniques ; ni retour Adobe ni transfert de permission. Les sous-commandes prennent le préfixe `cifereca-cli paris-adept`. |
| `paris-adept validate-resolution-snapshot --loan-id L --item-id B --fingerprint SHA256` | O | Contrôle local d'un snapshot de preview ADEPT sans mutation ni attestation Adobe serveur ; une empreinte égale ne crée pas d'autorisation. Fusion #3826. |
| `paris-adept preview-resolution --loan-id L --item-id B --source-reference-id R --acquisition-ref H` | O | Preview sans mutation, identités et références exactes requises ; revalidation locale, pas de « droit Adobe » nouveau. |
| `paris-adept accounts` | **N** | Connexion Paris configurée avant restitution des comptes visibles ; ne pas présenter comme lecture SQLite seule. |
| `diagnose` ; `loans` ; `history` ; `search TERME [--scope all|physical|digital|unknown] [--provider P] [--field all|title|author|isbn|subject]` | **N** | Parcours historiques susceptibles de `connect_configured()` / rafraîchissement ou recherche réseau. **Préférer** `doctor` / `sync list` / audit ciblé pour diagnostic local. |
| `catalog-scan [--refresh-covers]` | **M** | Scan et réconciliation du catalogue local : écritures possibles ; ne pas confondre avec `catalog-list`. |
| `acquisition-reconcile` | **M** | Réconciliation des acquisitions interrompues/prêts/catalogue ; pas un outil de lecture seule. |
| `kobo-sync [--quiet]` ; `kobo-backup [--quiet]` | **N+M** | Backup/acquisition Kobo, alias `kobo-backup` conservé. |
| `kobo-resume --credential C --confirm-downloads [--retry-scope-blocked --confirm-reauthorized]` | **N+M** | Reprise ciblée Kobo, peut télécharger ; confirmations avant connexion. |
| `kobo-reconcile-local --credential C --account-id A --item B --sha256 H [--identity I] [--path F] [--undo TOKEN] [--confirm TOKEN]` | **M** si confirmé, O pour aperçu | Rapprochement local atomique/reversible selon garde du token ; `--confirm` conditionne l'écriture. |
| `paris-adept bind --credential C --borrower A --identity I --confirm-ownership` ; `paris-adept unbind --credential C --borrower A` | **N+M** pour bind, **M** pour unbind | Changement explicite de liaison ADEPT : jamais un diagnostic passif. |
| `identity-directory [identity_id] [directory_name] [--dry-run] [--abort]` | **M** sauf aperçu `--dry-run` | Migration, rollback/rescan des répertoires ; ne pas employer pour rechercher un fichier manquant. |
| `plugin install ZIP [--no-activate]` ; `plugin enable|disable PLUGIN_ID` ; `plugin remove PACKAGE_ID [--version V]` ; `plugin rollback PACKAGE_ID` ; `plugin priority PLUGIN_ID N` ; `plugin config-set PLUGIN_ID JSON [--scope-id ID]` ; `plugin deps-repair` | **M** | Installation/suppression/configuration et dépendances locales ; hors diagnostic. |

### Analyseurs autonomes **sans** Core fournisseur

| Point d'entrée exact (sans install spécifique) | Entrée / options exactes | Classe et résultat |
|---|---|---|
| `python3 src/plugins/provider-numilog/numilog_delivery_diagnose.py JOURNAL.zip [--task-ref REF] [--json]` | ZIP journal canonique **à 3 membres** (`cifereca-journal-brut.json`, `manifest.json`, `README.txt`) ou ZIP mono-JSON historique | **F** ; catégories des acquisitions/preflights Numilog et sauts HTTP, pas de placement final. Depuis PR #3991, validation bornée du manifeste/SHA/compte d'événements ; recette producteur/lecteur #3994. |
| `python3 scripts/numilog_html_shape.py --context {unknown,meslivres,book-detail,readbook,confirmation} HTML_LOCAL` | HTML enregistré préalablement, 4 Mio max | **F** ; JSON de formes DOM et compteurs, pas de HTML/URL. Le **fichier d'entrée** peut contenir des credentials ; ne pas le publier. |
| `python3 scripts/bnf_borrow_dom_probe.py --page {profile,detail,confirmation} < PAGE_BNF_PRIVEE.html` | HTML BnF fourni exclusivement par **stdin**, borné par `MAX_HTML_BYTES` ; aucune URL ni compte demandé | **F** ; JSON fermé de signaux DOM structuraux, noms de balises uniquement. Pas de requête HTTP, pas d'écriture de fichier, pas d'emprunt, pas de preuve de quota, de permission ou de disponibilité. Entrée HTML **privée** à ne jamais joindre à un ticket ; codes retour 0 si reconnu, 2 sinon. | 
| `python3 src/plugins/recommendations/trace_report.py [LOG|-]` | Fichier UTF-8 de traces Recommendations ou stdin (`-`, défaut) | **F** ; texte de statistiques/états de cache, sans fournisseur ni modification du journal. |
| `python3 scripts/recette-doctor.py [--require-apparmor] [--kernel-audit] [--profile FICHIER]` | Capacités Linux/Bubblewrap, profil AppArmor facultatif | **T** ; JSON de prérequis avec sondes confinées temporaires, aucune inspection de la bibliothèque. `--kernel-audit` lit les événements du noyau accessibles. |
| `python3 scripts/benchmark_diagnostics.py [--blocks N] [--iterations N]` | Données synthétiques | **T** ; mesures de microbenchmark, **pas** latence de l'IHM réelle. |
| `python3 scripts/benchmark_task_admission.py [--rounds N] [--jobs-per-pool N] [--io-ms N]` | Attentes synthétiques | **T** ; mesure concurrence de pools, pas diagnostic terrain des syncs. |
| `cifereca-plugin-test PACKAGE [--tests CHEMIN]` | Manifest + scan imports privés ; éventuellement exécute pytest dans XDG temporaire | **T** ; résultat JSON de contrat plugin/test, pas acquisition réelle. |

**Validation privée autonome de `--capture` (P1 #4093) :**
`python3 -m cifereca.core.capture_session_export --validate /chemin/capture.zip --json`
— **F**, lecture seule, hors réseau, **sans démarrage Core** ni ouverture
de compte. Verdict fermé `valid`/`invalid`, membres et couverture si valide,
code retour `0`/`2` ; aucun chemin, contenu HTTP/HTML, jeton, ligne de journal
ou exception dans la sortie. La commande ne crée **aucun ZIP** : la capture
elle-même se déclenche explicitement avec `cifereca --capture`, active Trace
pour cette session, et publie après fermeture une archive brute 0600 dans
Téléchargements XDG (ou `--capture-dir DIR`). La GUI reste une application
interactive : les actions utilisateur peuvent être **N+M** ; la validation
séparée du fichier n'est jamais une commande de synchronisation ou une preuve
de prêt. Cf. `docs/CORE-P1-4093-GUI-CAPTURE.md`.

**Accès spécialisé** : `cifereca-cli paris-adept ...` est la façade audit
Paris/ADEPT ; `kobo-audit` et `kobo-resume-preview` sont les entrées Kobo
historiques. Ces noms ne créent pas de deuxième moteur générique de Sync.

**Analyseur externe Kobo (#3252, script hors Core)** : `scripts/kobo_final_verdict_summary.py` (O — lecture locale uniquement). Depuis la racine du dépôt, lancer `python -m scripts.kobo_final_verdict_summary CHEMIN-PRIVE.zip` pour extraire les compteurs fermés des rejets finaux. N'ouvre pas le Core, ne modifie aucune donnée, ne déclenche aucune connexion ni acquisition et ne publie aucun contenu brut du ZIP. Le dossier `scripts/` ne fait pas partie du package Core installable.

## Expert : réconciliation de bibliothèque hors réseau

**Point d'entrée Core non installé comme commande shell :**
`python3 -m cifereca.core.library_reconciliation --database /chemin/library.sqlite3 --root 'IDENTITE=/chemin/EBooks'`
(plusieurs `--root IDENTITE=REPERTOIRE_ABSOLU` permis).
**Classe O**, sans ouverture des sessions fournisseurs, sans réparation ni
acquisition ; effectue un scan FS explicitement autorisé, inspecte un
instantané SQLite local (y compris capture WAL isolée lorsque disponible)
et renvoie sur stdout un JSON **fermé** : inventaire, groupes de preuves,
chemins persistés et candidats bibliographiques classés. Une absence issue
d'un inventaire incomplet n'atteste pas un fichier définitivement manquant.

L'option **`--private-report /chemin/rapport.json`** passe en **O+E** :
création exclusive 0600 d'un JSON local contenant **chemins, titres,
identifiants et SHA**, qui ne doit pas être publié automatiquement. Ce
fichier est distinct du ZIP fermé `sync run --report` et du ZIP du journal.
Le module `src/cifereca/core/library_wal_image.py` est un **enfant
technique interne** de capture WAL (arguments positionnels internes et octets
privés sur stdout) : **ne pas l'invoquer comme outil opérateur**. Il peut
ouvrir des descripteurs et prendre des verrous de capture éphémères mais ne
répare pas la base de la bibliothèque.

**Préférence :** commencer par `cifereca-cli acquisition-audit` pour une
source connue et exacte. Mobiliser l'exploration experte seulement pour une
analyse transversale des origines / fichiers orphelins nécessitant des
racines explicitement autorisées ; ne jamais déduire une permission Adobe ou
un OriginLink du seul titre/SHA.

## Autres points d'entrée : hors cible du diagnostic de bibliothèque

Les **cinq** scripts installés dans `pyproject.toml` sont `cifereca`
(interface application), `cifereca-cli` (diagnostic/administration),
`cifereca-update` (mise à jour), `cifereca-update-dev` (snapshot de
développement), `cifereca-plugin-test` (contrôle de package).
Les deux `update` modifient l'installation : **M**, jamais une lecture seule.

Scripts autonomes identifiés par recherche du code (`__main__` /
`ArgumentParser`), regroupés par responsabilité :

| Famille | Chemins de scripts observés | Classe / raison d'exclusion |
|---|---|---|
| Packaging/release | `scripts/package-plugin.py`, `package-all-in-one.py`, `package-dev-snapshot.py`, `package_partial_plugins.py`, `prepare-admitted-wheelhouse.py`, `attest-wheelhouse.py`, `check_release_inventory.py`, `verify-packaging.py` | **T/M** : créent/vérifient des artefacts, pas de preuve d'acquisition réelle. |
| CI / qualité | `scripts/ci_bnf_reader_gate.py`, `ci_lending_conformance_gate.py`, `ci_numilog_core_release_gate.py`, `ci_plugins_only_receipt_gate.py`, `ci_plugin_httpx_boundary.py`, `ci_plugins_release_request.py`, `ci_plugins_release_target.py`, `ci_core_release_preflight.py`, `ci-full-release-install-gate.py`, `ci_select_core_push.py`, `ci_select_plugin_tests.py`, `ci-select-core-tests.py`, `ci-select-e2e.py`, `ci-export-e2e-summary.py`, `ci-validate-gtk-relay-cache.py` | **T** : gates, sélecteurs et tests de distribution. Pas de commande de triage utilisateur. |
| Recette jetable | `scripts/recette.py` | **T/M** sur environnement jetable : `verify`, `destroy`, `_run`, `_stop` ; peut créer/détruire son sandbox. Ne jamais confondre avec `recette-doctor.py`. |
| Administration des branches Git | `scripts/nettoyer_branches_git.py`, `scripts/nettoyer_branches_git_stream.py` | **M potentiellement destructrice** : `--apply`, `--yes` / `--force-divergent` et garde des branches ; jamais des outils de diagnostic applicatif. |
| Simulation / E2E | `simulation/datasetctl.py`, `simulation/e2e_fixtures.py`, `simulation/plugin_coverage.py`, `simulation/plugin_scenarios.py`, `simulation/adept/fake_kobo_adept_cli.py`, `simulation/Epub/gen_fake_epubs.py`, `e2e/run.py` et `e2e/*_probe.py` | **T** : fixtures/services fictifs, captures et recettes GTK ; isolation des données production obligatoire. |
| Transformation de livres | `src/plugins/epub_optimizer/cifereca_epub_optimizer/cli.py` (`input`, `-o/--output`, `--profile`, `--max-uncompressed-mb`, `--json`) | **M** sur fichier de sortie ; conversion, pas diagnostic d'acquisition. |

## Points d'entrée directs dans les packages plugins — inventaire statique

Le scan des `src/plugins/**/*.py` repère les vrais points d'entrée par garde
AST `if __name__ == "__main__"` et/ou construction d'un parseur
`argparse.ArgumentParser`, **sans importer ni exécuter les plugins**.

Pour vérifier un **checkout source** (agent backlog ou développeur) :

```sh
python3 -m cifereca.core.plugin_entrypoint_inventory --root /chemin/vers/cifereca --json
```

**Classe O/T** : parcourt et analyse uniquement les sources Python présentes sur
disque ; ne déclenche aucune commande provider, requête réseau, opération SQLite,
optimisation EPUB ou plugin runtime. Le rapport comporte uniquement les chemins
**relatifs**, la classe de risque et la raison connue : `status=complete`
(code 0) lorsque tous les points d'entrée sont classés ; `needs_review`
(code 2) avec `unclassified` ou `removed` si un nouvel exécutable est apparu
ou qu'un précédent a disparu. L'absence de checkout donne `unavailable`
(code 2). **Ce rapport ne prouve pas les effets d'une future implémentation** :
un humain doit examiner le code et réviser le registre.
Les modules `tests/test_*.py` n'établissent pas à eux seuls une commande
opérateur. Les cinq exécutables ci-dessous sont classés par leurs effets,
indépendamment de leur présence dans une installation donnée :

| Chemin effectif | Commande / paramètres | Effet attestable et précautions |
|---|---|---|
| `src/plugins/provider-numilog/numilog_delivery_diagnose.py` | `python3 src/plugins/provider-numilog/numilog_delivery_diagnose.py JOURNAL.zip [--task-ref REF] [--json]` | **F** : analyse hors réseau d'une archive **déjà créée**, aucun placement EPUB ni résultat d'emprunt attesté. Les archives sources peuvent contenir des secrets. |
| `src/plugins/recommendations/trace_report.py` | `python3 src/plugins/recommendations/trace_report.py [LOG|-]` | **F** : lecture de logs de performance (stdin `-` par défaut), statistiques ; **ne pas publier les lignes brutes**. |
| `src/plugins/epub_optimizer/cifereca_epub_optimizer/cli.py` | `python3 -m cifereca_epub_optimizer.cli INPUT [-o OUTPUT] [--profile ID] [--max-uncompressed-mb N] [--json]` (dans le venv où **cette distribution plugin** est installée) | **M** : lit l'ouvrage d'entrée et écrit un EPUB transformé, avec capacité de consommation CPU/mémoire ; `--json` ne rend **pas** l'opération passive. Aucun prêt/sync déclenché. |
| `src/plugins/epub_optimizer/tests/benchmark_optimizer_cpu.py` | `python3 src/plugins/epub_optimizer/tests/benchmark_optimizer_cpu.py [--repetitions N] [--heavy-images N] [--width N] [--height N] [--baseline-root DIR] [--baseline-ref REF]` | **T/M** : génère des EPUB/PDF synthétiques, lance des transformations et peut écrire un baseline ; dépendances `Pillow`/`PyMuPDF`. Benchmark destructeur **sur son espace de travail jetable**, pas un audit d'une bibliothèque réelle. `--sample`/`--workers` sont des paramètres internes de processus enfant. |
| `src/plugins/epub_optimizer/tests/gtk_cpu_responsiveness.py` | `python3 src/plugins/epub_optimizer/tests/gtk_cpu_responsiveness.py` sous le harnais GTK4/Xvfb déjà installé (`CIFERECA_GTK_PACKAGE_ROOT`, `CIFERECA_GTK_BASELINE` optionnels) | **T/M** : démarre une **vraie fenêtre GTK** et un TaskBroker ; crée/transforme des EPUB/PDF **synthétiques** en répertoire temporaire, teste 1/2/4 workers et la réactivité. Pas de compte, pas de preuve de prêt/Sync ; **ne pas lancer depuis le rôle backlog** ni sur une session graphique utilisateur non isolée. |

**Garde de dérive :** `tests/test_core_p1_3913_diagnostic_tool_inventory.py`
compare les points d'entrée réellement détectés dans les plugins à ce
registre et exige une classification lors de toute addition/suppression.
Les nouveaux modules `__main__` doivent être revus pour leur impact réel,
sans jamais être exécutés automatiquement par le rôle backlog.

## Scripts shell et wrappers : inventaire des points d'entrée non Python

Les scripts bash ci-dessous sont **outillage de recette ou d'environnement**,
pas des commandes de diagnostic du catalogue utilisateur. Les seuls exemples
réutilisables en lecture seule depuis les aides ci-dessus sont `doctor`,
`sync list`, `acquisition-audit`, les analyseurs de fichiers explicitement
identifiés et l'audit expert SQLite. Aucun wrapper ci-dessous n'accorde une
autorisation d'acquisition.

| Script / wrapper | Effet et garde |
|---|---|
| `scripts/ci-smoke-installed-wheel.sh` | **T/M** : crée un venv, installe un wheel, exerce les commandes installées, nettoie ; `CIFERECA_SMOKE_VENV` peut désigner un venv existant **effacé**. |
| `scripts/ci-install-gtk-e2e-runtime.sh` | **M hôte privilégiée** : peut appeler `sudo apt-get install/update` ; **interdit comme simple contrôle de bibliothèque** ou sur VM sans autorisation d'installation. |
| `scripts/ci-plugins-only-runtime-gate.sh` | **T/M** sur XDG jetable : `rm -rf` de sa racine de gate, véritable installation Core/update ; `CIFERECA_P0_GATE_ROOT` à garder isolée. |
| `scripts/ci-plugins-only-historical-core-gate.sh` | **T/M** sur XDG jetable et tag historique ; détruit/recrée sa racine et installe Core de test, sans effet sur données utilisateur si le gate reste isolé. |
| `scripts/ci-run-plugin-tests.sh` | **T** : lance pytest sur les plugins ou cibles fournies ; tests susceptibles de créer des artefacts jetables. |
| `environnements/chatgpt-work/work` | **T/M** : bootstrap d'un venv de travail ou exécution d'un outil ; dépend de `env.sh` et de l'environnement isolé. |
| `environnements/chatgpt-work/run-cifereca` | **N/M possibles** : lance la **vraie application** sous HOME jetable, suivant sa configuration et `CIFERECA_CMD`; n'est pas un audit passif. |
| `environnements/openai-chat-debian/headless` | **T** : wrapper du lanceur GTK Python pour recettes headless. |
| `environnements/openai-chat-debian/gtk-python` | **T** : adapte l'environnement Python/GTK avec `env.sh` ; exécute les arguments passés, dont les effets dépendent de l'exécutable. |
| `environnements/openai-chat-debian/capture-app` | **T** : lance capture GTK/fixtures ; captures et artefacts de test restent à protéger selon leur contenu. |
| `environnements/openai-chat-debian/run-cifereca` | **N/M possibles** : lance Cifereca sous HOME de runtime, sans transformer ses actions en lecture seule. |

Les fragments shell intégrés aux **workflows** CI ne sont pas une interface
CLI stable : leurs permissions et effets sont définis par chaque job et par
ses secrets, non par un simple `--help`. Les scripts d'administration de
branches Git restent classés **destructeurs** dans la table précédente.
Les wrappers environnementaux ne doivent pas être exécutés par le rôle backlog.

## Matrice de triage : une commande, un type de preuve

| Symptomatique | Première étape recommandée | Preuve nécessaire ensuite, si l'étape ne suffit pas |
|---|---|---|
| Core, DB, plugins ne démarrent pas | `cifereca-cli doctor --json` | Rapport technique + version/commit ; **ne pas** déclencher de sync comme sonde. |
| Fournisseur ou compte invisible / source OFF | `cifereca-cli sync list --provider P --identity I --json` | Statut de liaison et de consentement. Aucun GET ni preuve de catalogue complet. |
| Fichier déclaré local mais manquant | `cifereca-cli acquisition-audit --provider P --identity I --verify-files --json` | Classification exacte et SHA/Placement/OriginLink, sans réparation automatique. |
| Fichiers orphelins ou incohérence **entre** plusieurs sources/profils | `python3 -m cifereca.core.library_reconciliation --database BDD --root 'I=/chemin/autorisé'` | Observation experte hors réseau : JSON fermé. `--private-report FICHIER.json` ajoute un export privé contenant chemins/titres/SHA ; ne pas publier. Jamais de réparation implicite. |
| Sync réellement demandée ; bilan avant/après | `cifereca-cli sync run --provider P --identity I --confirm --report ./sync.zip --json` | ZIP de rapport **fermé**. Demander seulement le rapport nécessaire, pas de trace brute par défaut. |
| Refus HTTP persistant identifié sur une source choisie | Refaire **uniquement avec consentement**, ajouter `--trace-export ./http.zip --confirm-trace-export` à `sync run` | ZIP HTTP brut local privé : **ne pas transmettre** avant vérification des credentials/URL signées et décision de partage. La trace HTTP ne certifie pas la finalisation. |
| Numilog : refus ou preflight ancien dans un journal déjà disponible | `python3 src/plugins/provider-numilog/numilog_delivery_diagnose.py JOURNAL.zip --json` | Codes et actions de livraison ; poursuivre avec `acquisition-audit` pour les fichiers physiques. |
| Numilog : structure HTML inconnue, capture **déjà disponible** | `python3 scripts/numilog_html_shape.py --context book-detail CHEMIN.html` | JSON de forme ; jamais la page privée brute en issue publique. |
| BnF-Prêt : ACSM obtenu mais livre absent | `cifereca-cli acquisition-audit --provider bnf-pret --identity I --verify-files --json` | Distinguer reçu/staging, transfert ADEPT, Placement final, OriginLink. Statut partiel des prêts ≠ retour attesté. |
| BnF-Prêt : signal DOM d'emprunt introuvable dans une capture **déjà disponible** | `python3 scripts/bnf_borrow_dom_probe.py --page detail < PAGE_PRIVEE.html` (ou `--page profile`) | Seulement la structure DOM et le statut de reconnaissance ; **ne qualifie jamais le consentement, le quota, l'éligibilité ou l'autorisation de POST**. Ne pas publier le HTML privé. |
| Kobo : état local ou rattrapage envisagé | `cifereca-cli kobo-audit --plan` puis `kobo-resume-preview --credential C` | Aucun téléchargement sans commande de reprise explicitement consentie. |
| Paris : prêt/ADEPT mal attribué | `cifereca-cli paris-adept audit-transaction --loan-id L --item-id B` | Comparer les sources exactes, sans interpréter ni muter les droits Adobe. |
| Échec d'install d'un plugin | `cifereca-cli plugin diagnose PLUGIN_ID` | Version/manifest et diagnostic plugin ; `plugin deps-repair` est une mutation, pas une sonde. |

## Cas limites et responsabilités

1. **0/1/N sources** : choisir par `sync list` ; `sync run` et `acquisition-audit` refusent les ambiguïtés. La seule identité n'autorise jamais une source étrangère.
2. **Lecture d'une source ≠ exhaustivité** : inventaire partiel, une page HTML, un HTTP 200, un ACSM ou un fichier de staging n'attestent ni droit complet, ni EPUB vérifié, ni OriginLink.
3. **Rapport ZIP ≠ journal brut ZIP ≠ trace HTTP ZIP** : producteur et schéma différents. Le lecteur Numilog accepte spécifiquement **le journal brut canonique**, **pas** `sync --report` ni `--trace-export`.
4. **Backlog ne lance rien** : il choisit l'outil déjà livré, fournit la commande et les précautions au propriétaire/à l'utilisateur, puis qualifie les preuves reçues. Un outil de correction ne devient pas un audit par son nom.
5. **Frontière de clôture #3913** : les scripts Python directs de `scripts/`, les wrappers shell CI/environnements identifiés et les points d'entrée installés sont classés et couverts par un garde de dérive. Le contrat ZIP v1 producteur/lecteur est partagé depuis #4003 et #4004 ; le test end-to-end #4009 vérifie sa délégation réelle. La **preuve métier** post-staging → Placement → OriginLink relève du **ticket P0 #3909**, qui demeure ouvert et interdit d'annoncer une publication sans attestation. Aucune suppression de wrapper historique ni changement de moteur par ce registre.

**Code de référence :** `src/cifereca/cli.py`, `src/cifereca/support_diagnostic.py`,
`src/cifereca/core/{acquisition_audit,sync_report,diagnostic_journal_zip,library_reconciliation,library_wal_image}.py`,
`src/plugins/provider-numilog/numilog_delivery_diagnose.py`,
`scripts/numilog_html_shape.py`, `src/plugins/recommendations/trace_report.py`,
`pyproject.toml`. Requalifier la classe d'effets après toute mutation des
parcours, pas seulement après changement de documentation.

## Interpréteur Python du Core actif

`cifereca-python` est publié dans le même répertoire de commandes que `cifereca` par l'Updater Core. Il exécute `current/.venv/bin/python` à chaque invocation et suit les mises à jour et rollbacks, sans se rabattre sur le Python système. Exemples :

```sh
cifereca-python --version
cifereca-python -m cifereca.core.missing_placements_cli --help
cifereca-python -c 'import sys, cifereca; print(sys.executable)'
```

Attention : `cifereca-python` est un interpréteur complet, **pas** une commande de diagnostic intrinsèquement non mutatrice. `missing_placements_cli` est en lecture seule ; d'autres modules peuvent lancer du réseau ou des mutations. Ne pas copier sorties contenant chemins privés ou identifiants dans les tickets. `pytest` exige que les tests et la dépendance soient présents, ce qui n'est pas garanti dans une installation.
