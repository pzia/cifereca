# #6560 — qualification bornée des témoins et des admissions

## Références exactes (pas la recette finale)

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
Relancer les deux jobs manquants au même SHA via CI/CD, puis refaire la
recette commune au SHA de main arrêté.

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
  distinct et à qualifier ; aucun faux classement global « autonome ».
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
