# Découplage Core — suivi d’intégration D

Tickets [#5048](https://github.com/pzia/cifereca/issues/5048), [#5056](https://github.com/pzia/cifereca/issues/5056), [#5518](https://github.com/pzia/cifereca/issues/5518) et [#5851](https://github.com/pzia/cifereca/issues/5851).

## Mesure du 13 novembre 2026

| `main` | A | B | C, Core strict | CoreUI | Transversal | Total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `94371729a97c7722a2fb26027180237811fae5b5` | 6 | 29 | 12 | 12 | 12 | 71 |
| `c0d4140` / `91c651a` | 6 | 28 | 12 | 12 | 12 | 70 |
| `1a44dcb` / `2cfcd60f81a5b4703a995e8d8d3934b1129b6de7` | 6 | 27 | 12 | 12 | 12 | 69 |

Zéro erreur de parsing. La partition A reprend les six chemins du [handoff](https://github.com/pzia/cifereca/issues/5048#issuecomment-6853250234), dont quatre portent des signaux. Elle requalifie le partage historique de 2 pour A et 16 pour C. La baisse de deux lignes appartient à B (`core/sync_coordination.py::_opening_sync_locked` et `core/provider_sessions.py::provider_diagnostic_state`). Aucune hausse de signature AST entre les deux bornes. Le rapprochement s’effectue par fichier, fonction, catégories AST et familles fournisseur, sans dépendre du numéro de ligne. Un remplacement de même signature et un dispatch indirect peuvent échapper au différentiel. Les modules fournisseur dédiés sont hors périmètre du worklist. La revue ci-dessous qualifie les signaux statiques d’après leur effet dans le code.

[Workflow d’inventaire du HEAD `2cfcd60`](https://github.com/pzia/cifereca/actions/runs/43501959945).

```sh
# À exécuter à la racine, après fusion de l’outil de suivi.
git worktree add --detach /tmp/cifereca-ast-base 94371729a97c7722a2fb26027180237811fae5b5
git worktree add --detach /tmp/cifereca-ast-head 2cfcd60f81a5b4703a995e8d8d3934b1129b6de7
python -m scripts.ci_provider_ast_function_worklist --root /tmp/cifereca-ast-base --json > /tmp/cifereca-base.json
python -m scripts.ci_provider_ast_function_worklist --root /tmp/cifereca-ast-head --json > /tmp/cifereca-head.json
python -m scripts.ci_agent_d_ast_progress /tmp/cifereca-head.json --baseline /tmp/cifereca-base.json
python -m scripts.ci_provider_core_coupling_report --root /tmp/cifereca-ast-head --json > /tmp/cifereca-loc.json
```

## Interfaces entre propriétaires

| Producteur | Consommateur | Invariant à vérifier après fusion |
| --- | --- | --- |
| B, profils et lease d’inventaire ADEPT dans `services.py` | A, `adept_inventory_reconciliation.py` | Portée persistée, profil exact, méthode réellement associée à la capacité et révocation avant/après lecture. Écart reproduit et [transmis à B](https://github.com/pzia/cifereca/issues/5851#issuecomment-6864068679). |
| B, résolution et admission par la façade | A, `adept_borrower`, `loan_acquisition_admission`, `loan_adept_profile_guard` | Identité, connexion, credential et emprunteur explicites. Un refus ferme reste fermé après retrait du dispatch. |
| B, `plugin_api` et brokers recherche/acquisition | C, `search_orchestration` et consommateurs CoreUI | Notice publique attestée dans la réponse courante, sans autorité d’acquisition. Le compte exact reste requis pour les actes privés. |
| B, broker et coordination Sync | C, lecture canonique, stockage et publication | La portée du compte reste identique entre inventaire et persistance. Une preuve `OriginLink` et le placement d’un profil ne se propagent pas à l’autre. |
| C, préflight de rattachement | C, inspection locale et consommateurs UI | Conserver le refus en lecture seule sur prêt inactif, même après retrait de la coordonnée fournisseur. [Conflit #5912/#5895 reproduit et transmis](https://github.com/pzia/cifereca/issues/5056#issuecomment-6864069426). |
| C, diagnostics structurés | B, état des sessions et CLI/CoreUI consommateurs | Préserver l’origine attestée. La disparition de `providers.kobo_adept` dans le JSON `diagnose` est le changement explicite de [#5921](https://github.com/pzia/cifereca/pull/5921). |

## Qualification des 24 lignes CoreUI et transversales

La colonne « relais » désigne le propriétaire recommandé pour la suite. Les numéros de ligne sont ceux du HEAD `2cfcd60`.

| Fichier et lignes | Nombre | Qualification | Relais |
| --- | ---: | --- | --- |
| `gui/detail_model.py:84` | 1 | Décision de présentation. Affiche un état de catalogue spécifique sans accorder de droit. | C/CoreUI |
| `gui/detail_panel.py:23` | 1 | Signal indirect. Import du modèle de présentation déjà compté à sa définition. | C/CoreUI |
| `gui/detail_panel.py:775,813` | 2 | Décisions d’affordance ACSM et renouvellement. Les deux chemins appellent un contrôle dédié avant action. | C/CoreUI, contrat B si requis |
| `gui/library_inspection_view.py:161,1675` | 2 | Décisions de libellé du préflight et du rattachement. Vérifier l’effet réel de la coordination C. | C/CoreUI |
| `gui/search_results.py:298` | 1 | Décision fournisseur. Masque l’action de téléchargement pour deux IDs même si un broker est disponible. | C/CoreUI, contrat B |
| `gui/settings_draft.py:524` | 1 | Compatibilité active du réglage de connexion Paris principale. Le garde protège l’identité et la connexion exactes. | C/CoreUI, contrat B |
| `gui/settings_editor_actions.py:167,213,239,267` | 4 | Références légitimes au réglage existant d’association ADEPT. Les noms fournisseur sont des candidats au retrait avec le contrat. | C/CoreUI, contrat B |
| `acsm_cache.py:243` | 1 | Décision d’admission sur provenance Paris/DILICOM. Exige un remplacement par des preuves équivalentes. | C/Core |
| `config.py:152` | 1 | Compatibilité de migration des anciens profils Kobo. L’obsolescence dépend des configurations encore prises en charge. | C/Configuration |
| `diagnostics.py:117` | 1 | Décision d’attribution du contexte worker à un événement HTTP BnF. Remplacer par la provenance producteur si disponible. | C/Diagnostics |
| `plugins/manager.py:699` | 1 | Référence légitime à une projection ADEPT, bornée par les IDs attestés du producteur et de son package. | C/Framework plugins |
| `storage.py:671` | 1 | Compatibilité de migration du schéma. Préserver les bases historiques prises en charge. | C/SQLite |
| `storage.py:1711,2154,2185,2314,2315` | 5 | Compatibilité active du miroir/backup et de ses gardes de propriétaire. Vérifier les writers et lecteurs sous #3196. | C/SQLite |
| `storage.py:2650,2669` | 2 | Décisions de relocalisation dans le bridge legacy. Préserver les preuves et les protections interprofils lors de la migration. | C/SQLite |

Aucune compatibilité de cette liste n’est démontrée obsolète au HEAD audité. Aucun signal n’a été retiré dans le périmètre de D.
