# #6560 — qualification bornée des témoins et des admissions

## Références exactes (pas la recette finale)

### Qualification du harnais reconvergé après correction Notices

#6671, HEAD `3dcc355f1ca312e980218eaca46a8d3bf4acf991` :
[baseline 44291459356](https://github.com/pzia/cifereca/actions/runs/44291459356)
et [PR CI 44291459376](https://github.com/pzia/cifereca/actions/runs/44291459376)
SUCCESS. Les six lignes de synthèse des jobs portent toutes le même SHA.

| Suite | Collectés / tentés | Call exécuté | Réussis | Skips | Inconnus (≥ 1 axe) |
|---|---:|---:|---:|---:|---:|
| Core | 23406 | 23395 | 23384 | 22 | 22795 |
| Paris | 831 | 831 | 831 | 0 | 831 |
| Kobo | 1604 | 1604 | 1604 | 0 | 1596 |
| Numilog | 2103 | 2103 | 2103 | 0 | 2103 |
| BiblioAccess | 396 | 396 | 396 | 0 | 396 |
| BnF-Prêt | 691 | 691 | 691 | 0 | 691 |
| Total | 29031 | 29020 | 29009 | 22 | 28412 |

Zéro failed/error/xfailed/xpassed. Les outcomes et la phase call ne sont
pas le même compteur : 11 des 22 skips ont une phase call. Les inconnus
sont des limites de classification sémantique, pas des tests échoués.
Les jobs Core et BnF-Prêt autrefois annulés ont donc deux qualifications
complètes à des SHA plus récents, dont la précédente (HEAD `4391ad6a`,
run [44263459606](https://github.com/pzia/cifereca/actions/runs/44263459606))
avec 28957 collectés et 28935 réussis. Sa PR CI avait quatre échecs Notices
Gtk sur la fusion de test, corrigés par #6711. Ne pas relancer les anciens
jobs annulés pour compléter ce qui n'est plus le HEAD du harnais. La recette
finale et les six mutations restent à exécuter sur le SHA stabilisé de main
après réception des cutovers ; aucun de ces chiffres n'est cette recette.

### Historique de la première qualification incomplète

- Harnais #6671, HEAD `8529c88f0dd21c50b3924cdc5d9133b2d605e0cc`, run [44246838616](https://github.com/pzia/cifereca/actions/runs/44246838616). Résultats lus dans les journaux des jobs, après contrôle collecte/exécution et égalité des nodeids par le harnais.

| Suite | Collectés | Exécutés | Réussis | Inconnus sémantiques (au moins un axe) |
|---|---:|---:|---:|---:|
| Paris | 831 | 831 | 831 | 831 |
| Kobo | 1604 | 1604 | 1604 | 1596 |
| Numilog | 2101 | 2101 | 2101 | 2101 |
| BiblioAccess | 396 | 396 | 396 | 396 |
| Sous-total des quatre suites | 4932 | 4932 | 4932 | 4924 |
| Core | inconnu | inconnu | inconnu | inconnu |
| BnF-Prêt | inconnu | inconnu | inconnu | inconnu |

Les quatre suites terminées ont zéro échec et zéro skip. Core et BnF-Prêt
sont annulés, pas des suites vertes ni des échecs de tests établis. Le log
Core est incomplet et le log BnF indisponible : cause non démontrée.
La somme ne représente pas le dépôt entier, ni HEAD main après cutovers.
L'annulation reste un fait historique dont la cause n'est pas établie ;
la qualification du harnais au nouveau HEAD est documentée ci-dessus.

- Inventaire d'admission #6680 au HEAD `f893a69a9822cfca376e4a5dac97939a5d543bc4`,
  run [44251708224](https://github.com/pzia/cifereca/actions/runs/44251708224),
  819 fichiers Python suivis, 117 sites journalisés, 25 inconnus lexicaux,
  zéro signal direct de navigation dans les workers résolus. Zéro signal
  n'est PAS une preuve d'absence de dépendance indirecte. Le rapport
  identifie aussi des appels de garde à qualifier. Les sites incluent
  références/alias et submit génériques (pas 117 mutations distinctes).

## Témoin et disposition au main de revue `4f60cd3b2209bb167a8fac4878442b0f9221785b`

| Fichier (relatif à tests sauf chemin plugin) | Contrat principal effectivement lu | Disposition |
|---|---|---|
| test_core_p0_3197_biblioaccess_convergence.py | Vrai producteur, SQLite, CAS ; ouverture/Sync, refus/révocation, veto ADEPT durable et interruption du reçu ; HTTP/Adobe synthétiques | Conserver integration Core |
| test_core_p1_3197_opening_legacy_partial.py | Adaptateur v1 réel ; liste positive partielle, aucun last_loan_errors, absence non probante | Conserver integration Core |
| test_core_p0_2847_operator_session.py | Sélection, preview privée, confirmation, rejet preview périmée et lock, non-rejeu ; SQLite/FS réels | Conserver (avant admission #6692) |
| test_core_p0_2847_relocation_transaction.py | SHA de destination, absence, dépendances, CAS et rollback catalogue/placement | Conserver integration Core |
| test_core_p0_3171_atomic_publication_v14.py | Publication atomique Artifact/Placement/SourceReference/OriginLink, scope exact, hash et échec rollback | Conserver integration Core |
| test_core_p0_3719_consent_legacy_dispatch.py | Ancien ON ne vaut pas CAS, consentement source et anti-fallback | Conserver integration Core |
| src/plugins/provider-paris/tests/test_p0_3723_downloadable_v2.py | Adapter v2, offre exacte, capacité/lease, rebind et élimination staging, aucune publication Core | Conserver contrat plugin |

Le fragment `6560-cutover-witnesses.json` n'attribue que ces sept modules
d'après leur contrat principal. Les fichiers mixtes (y compris certains
tests de lease ou CoreUI) restent non qualifiés si un classement au niveau
du fichier serait trompeur. Aucun nouveau nombre de tests collectés ou
exécutés n'est inféré du manifeste. Ni suppression de témoins, ni
modification des tests, ni recette locale.

## Qualification d'admission : points effectivement relus

- `loans_controller._renew` (HEAD #6685) : worker non résolu par AST car
  alias lambda/admission ; capture à la commande et résultat filtré par
  la vue. Le fallback de shim sans capture reste un chemin de compatibilité,
  ne prouve pas la sécurité de tout tiers.
- `local_book_action_controller.add_local_book_actions.route` : sous Core
  courant, le worker est issu de `routing.admit_transfer`, pas d'une lecture
  tardive de sélection. Ne pas compter submit et alias comme deux actions.
- `main_window.download_direct_item` : le garde current() du worker ne
  s'applique qu'au chemin sans admission ; les offres privées/legacy
  capturées passent par leurs exécuteurs Core dédiés. Le fallback reste
  réservé au shim sans les ports de capture ; le LibraryService natif impose
  la capture legacy lorsque le bail privé est absent, sinon refuse avant
  submit. Cela ne qualifie pas toutes les entrées directes de l'API.
- `sync_controller._refresh_sync_page.prepare_download` : confirmation,
  source et token privés ; annulation explicite vise un Event. Le worker
  n'interroge pas current() ; la projection de résultat est conditionnelle.
  L'autorité du token et du grant doit être attestée par les tests Core.
- `library_inspection_view` : les réparations de placements manquants
  restent le point à raccorder au port d'admission #6692 après #6678.
  Les anciens chemins de placement manuel ne doivent pas être réactivés.

Le reste des 25 résolutions lexicales inconnues doit demeurer explicite
tant que le contrat transitif n'est pas revu. Les travaux Notices relèvent
de #6366 ; ne pas les modifier au titre de cette qualification.


## Compléments de qualification — main `bd603180b81e2d58917444bbd4c537cef75d42fa`

Les résolutions suivantes sont revues par site, sans remapper le compteur
AST historique ni extrapoler une preuve transitive aux autres consommateurs.

| Site | Disposition |
|---|---|
| `gui/task_runner.py::submit` | Ordonnanceur générique : widget seulement à l'admission ou publication ; worker appelle la fermeture. Pas d'autorité de sélection injectée. |
| `task_runtime.py::_ScopedTaskBroker.submit / TaskBrokerImpl.submit / _submit` | Alias et exécuteur génériques, fermeture et TaskControl. Révocation explicite ou fermeture Core, pas de sélection UI à l'exécution. |
| `gui/settings_chooser_controller.py::choose_adept_profile` | Choix Core capturé, garde visuelle en retour. |
| `gui/settings_commit_actions.py::bind_settings_commit` | Méthode de sauvegarde liée, valeurs des widgets et draft capturés avant enqueue ; garde page en publication. |
| `gui/startup_coordinator.py::connect_configured_sources` | Callable Core et caller_category capturés ; parcours global, non source choisie par GTK. Résidu réel dans la relecture Core après connexion traité par #6718 (3 gates SUCCESS, HEAD ab95c0d0). |
| `core/provider_sessions.py::_queue_virtual_collection_refresh` | Coalescence de notifications globales : relire l'inventaire publié le plus récent est intentionnel, non un choix d'emprunteur. |
| `gui/cross_profile_borrow_controller.py` | Deux confirmations, capture Core du prêteur, epoch source en worker ; current() filtre la publication et l'admission. |
| `gui/reader_usb_execute_actions.py` | Second clic : callable Core admis, garde visuelle avant admission et sur retour ; port USB livré #6662/#6669, préparation read-only distincte. |
| `gui/library_inspection_view.py` — ancien rattachement / undo | Les anciens previews/confirmations n'autorisent plus aucune écriture : `library_local_inspection.py` refuse canonical_source_validation_required ou unavailable. Ne pas réactiver ces writers pour réduire l'AST. |
| `gui/library_inspection_view.py` — réparation manquante | Reste le raccordement des trois confirmations réelles, après réception #6678/#6692. |
| `gui/history_cover_resolver.py::request` | Résidu réel : méthode synchrone appelée au démarrage du worker relaie current_account ; #6721 HEAD 5dce5a79 capture source avant enqueue et revalide lease avant/après I/O ; gates en cours au relevé. Cache visuel natif GTK ne rejoue plus une URL privée sans revalidation worker. |

Le maintien d'un shim ancien n'est pas une nouvelle autorité headless.
L'audit offline privé LocalInspection reste une vue de preuves révocable
et non un mandat mutateur durable ; le broker d'audit redacted historique
restreint encore explicitement au lecteur actif. Ne pas le présenter
comme une API headless multi-profils : distinguer sa compatibilité
restrictive, ses consommateurs réels, et le mandat de réparation.
Les sites non résolus dans cette revue et les consommateurs Notices
restent explicitement inconnus tant qu'ils n'ont pas été qualifiés.
Aucun objectif de refonte de l'exécuteur ou de migration globale des tests.


## Réception intégrée — 18 novembre 2026, main `c3d089f694f83fa98c092a81a422756014d7a26b`

#6732 (a4c52f21) et #6734 (c3d089f6) intégrés : CI/E2E/AST/install applicables
SUCCESS au HEAD de chaque PR ; #6734 n'a pas déclenché d'E2E spécifique.
La recette commune a été exécutée exactement sur le SHA ci-dessus via #6737,
brouillon fermé sans fusion. [Baseline 44313990355](https://github.com/pzia/cifereca/actions/runs/44313990355),
[mutations 44313990366](https://github.com/pzia/cifereca/actions/runs/44313990366),
[inventaire admissions 44313990363](https://github.com/pzia/cifereca/actions/runs/44313990363)
SUCCESS. PR CI 44313990300 distincte et SUCCESS.

| Suite | Collectés / tentés | Call | Réussis | Skips | Inconnus sur ≥1 axe |
|---|---:|---:|---:|---:|---:|
| Core | 23535 | 23524 | 23513 | 22 | 22843 |
| Paris | 831 | 831 | 831 | 0 | 816 |
| Kobo | 1604 | 1604 | 1604 | 0 | 1596 |
| Numilog | 2106 | 2106 | 2106 | 0 | 2106 |
| BiblioAccess | 396 | 396 | 396 | 0 | 396 |
| BnF-Prêt | 691 | 691 | 691 | 0 | 691 |
| Total | 29163 | 29152 | 29141 | 22 | 28448 |

Zéro failed/error/xfail/xpass. Six artefacts présents (retention 2 jours) ;
ZIP Core 14431733355, Kobo 14432175608, mutations 14432390291 effectivement lus.
Les 6 mutants ont baseline_exit=0, mutant_exit=1, killed=true au SHA exact ;
matrice des six témoins dans CI-P0-5376-MUTATION-WITNESSES.md.
Il ne s'agit pas d'un score de qualité général ni d'une recette fournisseur live.

### Graphe exact #5376 : disposition des arêtes attestées

Core-plugin-consumers.json : 2012 fichiers tests racine, 779 fichiers Python
de tests/helpers plugins, 1474 arêtes root→root, 223 imports locaux plugins,
21 arêtes helpers plugins, **0** plugin→root direct/transitif/via helpers,
0 ambiguïtés/missing, 0 parse errors. 95 sites dynamiques/pytest_plugins
non résolus (50 racine, 45 plugins), ni assimilés à des arêtes prouvées ni
supprimés. Sélection conservatrice : `plugin_replay_required` et
`has_test_consumers` ; changement de test imbriqué, source ambiguë, erreur
de parsing, suppression/renommage ou loader inconnu concerné → replay.
#5994/#5999/#6004/#6006/#6009 ont extrait les helpers accidentels et amélioré
la sélection ; #6617 corrige la suppression de la dépendance transitive,
mutation finale détectée. Témoins réels au SHA : 25/25
test_ci_test_core_consumer_graph.py, 6/6 test_ci_test_nested_core_consumers.py,
26/26 test_ci_core_test_selection.py, 27/27 test_ci_pr_workflow_scopes.py,
71/71 test_ci_test_dependency_guard.py. Aucun faux vert de sélection connu
non corrigé dans ce périmètre ; un loader inconnu reste à qualifier si
sa sélection effective devient litigieuse, pas une preuve d'exhaustivité.

Core-test-provider-imports.json : 2012 modules racine, 70 modules provider
indexés, 15 modules de tests et 27 arêtes directes, zéro symbole privé
importé (critère underscore), zéro ownership ambigu, zéro parse/I/O error,
un loader dynamique inconnu (`test_core_p0_6560_loan_producer_inventory.py:50`,
lecture de classes issues des manifestes). Qualification après lecture des
15 fichiers, conservés : intégration réelle en environnement synthétique,
contrats ou conformité d'installables. Le chemin/nom provider ne rend pas
un test illégitime. Les imports publics ne garantissent pas à eux seuls
la stabilité ABI de ces modules.

| Fichier tests/ (préfixe test_ omis) | Arêtes | Cas passés | Disposition |
|---|---:|---:|---|
| core_p0_2055_bnf_action_outcomes.py | 1 | 26 | préserver refus et diagnostic |
| core_p0_2055_bnf_real_provider_dispatch.py | 3 | 4 | préserver chaîne UI/Core/HTTP |
| core_p0_3171_six_resource_recovery.py | 1 | 1 | référence synthétique, récupération |
| core_p0_3197_biblioaccess_convergence.py | 1 | 18 | A/B et publication, HTTP synthétique |
| core_p0_3719_v2_execution.py | 2 | 23 | référence neutre, v2/CAS |
| core_p0_4982_numilog_fetch_integration.py | 3 | 6 | Notices, transport exact/journal |
| core_p0_4982_numilog_replay_integration.py | 3 | 3 | Notices, parse/replay sans GET |
| core_p0_6366_observed_c_first_progressive.py | 2 | 2 | Notices, recherche progressive |
| core_p1_3122_paris_bnf_snapshots.py | 2 | 10 | inventaires partiels et isolation |
| core_p1_3122_real_manifest_lending_conformance.py | 4 | 5 | conformité manifests/DTO |
| core_p1_4281_actual_opening_two_branch_gets.py | 1 | 1 | budget GET du vrai opening |
| core_p1_4281_paris_bnf_opening_comparison.py | 1 | 4 | budget comparatif, pas mesure de latence |
| core_p1_4281_paris_opening_observer_cost.py | 1 | 5 | lectures indépendantes et panne |
| p1_4157_paris_complete_inventory_core.py | 1 | 6 | absence attestée et préservation FS |
| paris_manual_borrow_dispatch.py | 1 | 53 | réémission interdite, source exacte |

Les trois témoins Notices (8 arêtes) sont hors refonte #6560 ; leur
intégration légitime est conservée. Aucune arête avérée à corriger dans cette
photographie. Les 95+1 inconnus sont des limites des deux analyses, pas
nécessairement des sites distincts. Dette P2 non bloquante : BACKLOG.md.

### Admission, réception #5833 et frontière #5851

Inventaire exact : 823 fichiers, 121 sites submit/référence, 32 non résolus
lexicalement, 0 signal direct dans les workers résolus. Les dispositions
antérieures restent valables pour les mêmes contrats. Compléments (les
comptes AST ne sont pas une preuve de reachability complète) :

- Search : request_scope porte identité et source_epoch, annulation worker
  sur vraies révocations ; current() UI sur livraison. Holds avec contexte
  explicite, garde de vie Core. #6366 public web action : storage/document/epoch
  fixés, navigateur uniquement au callback du clic courant.
- EpubCoverPhysicalCache et CachedDetailCollector : source/chemin/BookRef
  fixés et contrôle des octets ou du résultat ; lecture passive.
- Notice pump/runtime et search executor : refs de journal/registrations,
  sessions et scopes explicites ; Gallica branches publiques indépendantes,
  pas de sélection UI. Ces chemins Notice ne sont pas remaniés ici.
- Déclaration locale et retrait : admit_declared_link et
  admit_history_withdrawal, commande consommée au clic. Trois réparations
  manquantes : #6732, même séparation. Historique : #6721, bail à l'admission,
  aucun cache visuel privé rejoué sans contrôle.
- Optimiseur #6734 : admit_transform avec identité/placement/source/transformer
  exacts ; garde de transaction et non-rejeu ; anciens Core gardent le
  fallback restrictif. Révocation tardive après publication FS peut laisser
  un orphelin selon protocole de reprise ; aucune publication SQL partielle.
- Constellations et Critiques : paramètres de lecture figés, publication
  conditionnée à la vue. ADEPT profils : valeurs des widgets capturées avant
  submit, annulation en cas de désactivation du plugin. USB : préparation
  read-only distincte du second consentement mutateur ; exécution admise.
- Anciens ports d'audit local et de préférence de variante : compatibilité
  restrictive avec contrôle du profil actif. Ne pas les présenter comme
  ports headless concurrents. Aucun consommateur installable de la préférence
  détecté par la recherche de `set_preferred_variant` ; la mutation directe
  plan/run garde cependant une dépendance à la navigation, critère #5851 à
  trancher/traiter sur son contrat, pas à supprimer par principe.

Témoins issus des items d'exécution, non seulement d'une collecte :
history_live_source_lease 26/26, core_account_scoped_secondary_async 15/15,
native_sync_action_scope 23/23, core_ui_1347_sync_action_scope 26/26,
private_search_obtain_admitted_source 33/33, search_controller_state 22/22,
queued_loan_epochs 4/4, history_cover_admission 11/11,
library_declared_link_inspection 82/82, inspection_binding 9/9,
exact_placement_transform 11/11, scoped_holdings_read 9/9,
headless_bootstrap 3/3, cli_headless_composition 5/5.
#6732 E2E 44310248433 et #6734 CI 44312249685 (130 tests optimizer) verts.
Les vérifications CI/SQLite/HTTP synthétiques ne prouvent pas un fournisseur
authentifié ni des plugins tiers non installés.
