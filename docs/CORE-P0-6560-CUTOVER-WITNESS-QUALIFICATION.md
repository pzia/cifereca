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
