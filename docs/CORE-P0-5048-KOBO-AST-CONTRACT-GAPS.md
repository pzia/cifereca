# P0 #5048 / #3196 — Audit des contrats pour extinction des AST Kobo

Date : 2026-11-12. Périmètre : Core/CoreUI installé, plugin `provider-kobo` et runtime `runtime.kobo-adept` comme producteurs. Ceci est une **revue de frontières**, non une preuve d'exécution de tous les chemins ni un nouveau contrat publié.

## Synthèse exécutable

**Ne pas créer une ABI Kobo.** Réutiliser les contrats publics neutres ; n'étendre une interface qu'après démonstration d'un manque par un consommateur concret. Le décompte AST est reproductible via `python scripts/ci_kobo_ast_focus.py --json` (PR #5726, fusionnée). Ces lignes sont des *signaux* et non des décisions actives. Les historiques 118 signaux / 206 LOC du 11/11 ne doivent pas être publiés comme une nouvelle mesure du HEAD.

| Priorité | Zone et preuve source | Contrat réel déjà disponible | Écart précis | Décision / propriétaire |
| --- | --- | --- | --- | --- |
| P0 | `services.py` : `kobo_by_identity` | `ProviderDescriptor.max_accounts_per_identity=1` du provider Kobo ; `settings_draft.py` consomme déjà le champ | Le chargement/persistance des comptes répète la règle Kobo et divulgue les credential IDs dans l'erreur | **Aucune nouvelle ABI provider**, mais un problème de bootstrap : `LibraryService.__init__` appelle `_sync_identity_configuration()` avant le chargement des plugins, et `ProviderRegistry` y est vide. Ne pas supprimer la protection sans remplacement effectif. Faire la validation générique après attach du registre avec le descripteur, et garder une garde conservatrice de démarrage/migration avant attachement ; message expurgé ; Core #5056 |
| P0 | `core/provider_sessions.py` : `connect_kobo_preview` | `connect_configured(source_scope=(identity, provider, credential))`, sessions et `ProviderDescriptor` | Le chemin explicite crée/active une session Kobo par une implémentation distincte ; il doit préserver « aucune connexion aux autres comptes, aucune écriture de liaison pour preview » | **Contrat de service Core neutre** (pas ABI du provider) : connexion ciblée read-only de la source exacte ; Core #5056/#3196 |
| P0 | `core/provider_sessions.py` : `connect_kobo_canonical` | `has_complete_delivery_abi`, `provider_scope_account_bound`, epoch, source CAS, `Account` | Matérialisation du seul `Account` admissible après confirmation, aujourd'hui nommée Kobo. `connect_configured` ne prouve pas seul cette admission | **Service Core versionné / helper neutre** `bind_confirmed_source_account` (nom provisoire) pour le tuple exact. Aucune mutation sans attestation du scope ; Core #3196/#5056 |
| P0 | `core/sync_coordination.py` : `canonical_kobo_sources` | Convergence canonique `_run_observed_canonical_convergence`, états `pending/blocked/failed/cancelled` | Deuxième projection uniquement pour résumer l'ancien backup Kobo | **Pas d'ABI provider**. Bilan Core par source et compatibilité CLI déportée ; Core #3196/#5056 |
| P1 | `gui/detail_panel.py` : `_commercial_search_usage` | `SearchResult` porte `source_collection`, `metadata.search_provenance`; `provider-search-ui/1` sert aux états de connexion | L'avertissement « catalogue hors ligne / droits inconnus » dépend encore du littéral Kobo ; l'ABI de statut de connexion ne couvre pas l'état *d'une notice* | **Extension candidate** : projection neutre de notice `search_notice_status` à vocabulaire fermé (ex. `known_catalog_stale`, `commercial_public_unverified`, `library_unverified`), issue des faits d'origine, pas de texte libre de provider |
| P1 | `core/sync_coordination.py` : `_kobo_opening_decision`, trace et metadata v1 | Consentement par source, diagnostics Core, convergence | Projection et clés legacy `kobo_backup_*` persistant malgré retrait W2 | **Pas de nouveau contrat fournisseur**. Projection générique par source avec adaptateur de lecture legacy isolé, puis suppression sur preuve d'absence de consommateurs |
| P1 | `services.py` : `_kobo_adapter`, `kobo_adept_state`, `execute_request` source Kobo | `runtime.kobo-adept`, `drm.ade`, broker `runtime.capabilities` | Dépendance et recette runtime spécifiques dans la façade Core | **Audit ABI runtime avant changement** ; ne pas confondre runtime de DRM avec `provider.kobo`. Candidat à un dispatch de recette typée, pas à un service par nom de fournisseur |
| P2 | `cli.py` : `kobo-sync-edition`, `kobo-relink-canonical` | Exécuteur canonique et scopes exacts existants | CLI historique exposée et chaînes de sortie Kobo | Wrapper compatible sur commande Core générique, sans suppression de l'interface CLI tant que ses consommateurs ne sont pas identifiés |

## Contrats à réutiliser (preuve source)

1. `src/plugins/provider-kobo/cifereca_kobo.py` : `DESCRIPTOR.max_accounts_per_identity=1` ; `rights_observation_snapshot`, `rights_selected_delivery_offer_snapshot`, `rights_selected_delivery_candidate`, `downloadable_resource_snapshot`, `downloadable_selected_offer_snapshot`.
2. `src/cifereca/core/canonical_delivery_abi.py` : les trois signatures reconnues par `has_complete_delivery_abi`, dont les variantes `*_guarded` et `*_runtime_guarded`. Ne pas introduire de fallback vers `backup_items`.
3. `src/cifereca/core/provider_sessions.py` : `connect_configured(..., source_scope=...)` et les vérifications sur session, credential, account, identity et epoch.
4. `src/cifereca/providers/base.py` : `ProviderDescriptor` offre déjà limites de comptes et contributions `search_status_notice_schema`/`search_status_notices`. Ces dernières ciblent le **statut de connexion** (cf. `gui/provider_status_notices.py`), pas la provenance commerciale d'une notice.
5. `docs/PROVIDER-DEVELOPMENT.md` : origin/link/artifact/placement et offre actuelle font foi. L'ancien `right`/Entitlement n'est pas une autorité à reconduire.

## Contrat Core proposé : ouverture + binding source exacte (P0)

Deux étapes explicites sans RPC provider Kobo nouveau :

- **Preview ciblée**, entrée : `(identity_id, provider_id, credential_id)` exacts, configuration activée, provider prêt et capacité attestée. Sortie : session connectée limitée au credential demandé ; aucun autre provider, aucune mutation `Account`/SQLite de liaison, aucun GET de candidate. Si session préexistante : identité et credential identiques sinon refus.
- **Binding confirmé**, en plus : exigence d'ABI complète, liste de comptes stable, `Account` exact, `provider_scope_account_bound` en SQLite, owner/config concordants, epochs immuables, registre inchangé pendant admission. Retour : source et compte admis ou cause fermée. Le Core seul modifie son registre et revalide avant acquisition ; le plugin ne reçoit aucun accès direct à la persistance Core.

**Points de conception à trancher par tests synthétiques** : compte unique obligatoire ou multiplicité explicitement qualifiée par descripteur/branch ; API réutilisable en CLI et Sync ; idempotence ; reconnexion expirée ; refus si session devient stale au milieu du contrôle. Ne pas déduire le choix du compte de la sélection UI.

## Contrat de notice proposé : observation, jamais autorisation (P1)

Projection de consultation, construite à partir des faits déjà attestés :
- état fermé `known_catalog_stale`, `commercial_public_unverified`, `library_unverified`, `not_applicable` ;
- origine `provider_id`, collection, provenance, portée de compte ; validité explicite et absence de droit téléchargeable inféré ;
- texte final produit par CoreUI avec libellés génériques, sans requête réseau. Les notices fournisseurs personnalisables, si nécessaires, ne peuvent ajouter ni capacité ni priorité de route.
- `source_collection=library` et la preuve d'achat réelle conservent leurs règles Core indépendantes de la décoration.

**Ne pas détourner** `cifereca.provider-search-ui/1` pour ce cas : il décrit une indisponibilité de recherche liée au compte. Évaluer une extension neutre de `SearchResult` ou une fonction de projection Core avant d'ajouter un schéma de plugin.

## Ordre de cutover et critères d'acceptation

1. **P0, sans nouvelle ABI provider, avec dépendance de lifecycle** : `LibraryService.__init__` initialise un `ProviderRegistry` vide, puis invoque `_sync_identity_configuration()` **avant** `attach_plugin_manager`. Ne pas appeler naïvement le descripteur au bootstrap. Préserver une garde conservatrice sur les comptes historiques au démarrage, puis appliquer `max_accounts_per_identity` à l'attachement et aux sauvegardes par une validation générique, avec refus si limite violée, sans secrets dans le message. Tests : init registre vide, plugin attaché, plugin absent, réattachement et changement de descripteur.
2. **P0, contrat Core** : prouver par providers fictifs preview et binding confirmé au tuple exact ; tests session concurrente, multi-identités, scope SQLite, ABA/epoch, pas de connexion latérale, aucun GET ni second consentement.
3. **P0/P1, orchestration** : remplacer le tableau `canonical_kobo_sources` par un tableau neutre des sources convergées ; garder un traducteur de sortie Kobo tant que CLI/tests l'exigent ; zéro double GET, zéro autorisation par `kobo_backup.enabled`.
4. **P1, présentation** : déplacer le message de provenance commerciale vers une projection neutre, garder l'avertissement pour l'ancien cache Kobo, refuser la conversion d'une notice en droit.
5. **P1/P2, runtime/CLI** : inventorier les appelants et contrats ADEPT/DRM et supprimer seulement les wrappers sans autorité ; conserver la compatibilité publique ou la déprécier explicitement.
6. **Gate** : `python scripts/ci_kobo_ast_focus.py --json` après chaque cutover, couverture provider fictif et tests GTK ciblés, CI Core + installation/rollback quand nécessaire, revue de la PR et fusion seulement au HEAD exact vert.

**Non-objectifs** : nouvel `KoboContract`, nouveau stockage de droits, code fournisseur dans Core, changement de protocole Kobo-Vault, modification du runtime de déchiffrement, suppression des traces legacy avant inventaire des lecteurs. Réserver les fichiers transversaux avec #5056 et les droits avec #5518 ; les travaux CI flaky restent sous #5376.
