# Agent B — Audit P0 contract v2 / Numilog et CoreUI (31 octobre 2026)

Référence: `main`, ABI `cifereca.downloadable-resource/2`. Tickets: #3848, #2797, #3252, #3722, #3723. Frontière Core: #3171, #3719, #3882.

## Constat vérifié dans les sources (pas une recette réelle)

| Adapter | Inventaire authentifié v2 | Qualification d'offre exacte | Candidat staging v2 | Limite de preuve |
| --- | --- | --- | --- | --- |
| Numilog | `downloadable_resource_snapshot` via `rights_observation_snapshot`; `meslivres` | `downloadable_selected_offer_snapshot` cible compte + achat | `downloadable_selected_delivery_candidate` retourne `SourceReference`, offre et `Path` | Inventaire `partial` / `delivery=unknown` avant préqualification ciblée ; pas de preuve de publication |
| Kobo | mêmes trois points d'entrée v2, snapshot Vault | qualification liée au compte et au product ID | staging Core choisi | aucune preuve de placement réel sans exécution Core |
| Paris | mêmes trois points d'entrée v2, preuves des prêts DILICOM | sélection d'un prêt actif exact | candidat ACSM/EPUB, scope épinglé | droits et disponibilité d'emprunt distincts de la copie locale |
| BnF-Prêt | mêmes trois points d'entrée v2, prêts HTML partiels | qualification du prêt/jeton exact | candidat ACSM sous staging Core | inventaire HTML partiel ; ADEPT et publication Core non attestés |
| Reference | `provider-reference`, fixtures déterministes | positif / inconnu / archivé, multi-comptes | EPUB synthétique seulement | preuve synthétique, pas recette fournisseur |

### Numilog — chaîne attestée / chaîne à prouver

1. `rights_observation_snapshot` produit un identifiant d'achat `numilog:<scope>:<lid>:<variant-hash>` et une référence de provenance `numilog.meslivres`. Il retourne `partial` avec `pagination_complete=False` et `delivery=unknown` ; aucune capacité GET ne doit être déduite de `state=active` ou de `SearchResult.downloadable=True`.
2. `rights_selected_delivery_offer_snapshot` revalide l'identité/compte sous session et la carte MesLivres cible ; l'offre n'est `available` qu'après un identifiant opaque de livraison univoque. `canonical_selected_offers.py` fournit `explicit_user_action=True` sur le chemin automatique après contrôle Core : ce booléen n'est pas la cause autonome du refus.
3. `rights_selected_delivery_candidate` et la passerelle v2 retournent un fichier staging Core, sans `OriginLink`, `Artifact`, reçu ni `Placement`. Cela respecte la frontière A/B ; une copie staging seule ne doit pas être considérée comme une publication.
4. `_download` a encore une logique hybride : destinations navigables `checked_url` sous liste `ALLOWED`/origines ; redirections à partir d'un lien signé via `_checked_attested_network_url` à politique différente. Risque fonctionnel d'interruption des redirections Dilicom/Eden depuis une navigation `readBook` non qualifiée comme chaîne signée ; **ne pas ouvrir aveuglément toutes les redirections**. Le contrôle DNS préalable ne constitue pas une protection suffisante contre le rebinding pendant la connexion : aligner la migration sur le transport HTTP du Core (#3171/#3719).
5. La projection CoreUI présentait le bouton « Télécharger » pour une ligne privée `downloadable=True`, alors que son offre v2 n'était pas nécessairement qualifiée. Cette PR dit « Vérifier et télécharger » et « offre à vérifier » sans ajouter de GET sur le rendu et en préservant l'action existante.

### Critères de sortie non satisfaits (à valider par #3882)

- Mesurer pour **chaque** acquisition les étapes `inventory -> selected_offer -> GET -> staging -> verified_bytes/SHA -> Placement/Artifact/OriginLink -> projection Bibliothèque`.
- Le statut `source_sync_partial` doit être ventilé par phase : la couverture volontairement partielle de MesLivres ne prouve ni l'échec ni le succès de publication.
- Pour le fichier Numilog déplacé : constater son absence exacte dans la portée du profil (sans modifier SQLite/reçus/staging), puis vérifier reprise idempotente et liaison à la bonne provenance.
- Six fiches d'achats Numilog et six staging ne doivent devenir six livres locaux reliés que **par des clés sources et placements vérifiés**, jamais par titre/ISBN seul. Aucun effacement des cinq fichiers staging.
- Le compteur « achats Numilog » exige un agrégat attesté fournisseur/compte ; `identity_statistics().sources` dénombre les origines des placements, pas les achats. Agent A expose la donnée attestée, Agent B la rendra dans CoreUI.
- L'action manuelle actuelle passe par `can_direct_download -> obtain_search_item` (parcours de compatibilité). Elle doit rejoindre le moteur Core canonique, sous le même consentement, avec ciblage exact et pas de second moteur de publication.

### Suites CI ciblées

- `pytest -q tests/test_core_ui_p1_1867_numilog_direct_download_scope.py`
- `pytest -q tests/test_core_p0_3719_v2_execution.py tests/test_core_p0_3719_six_v2_delivery_convergence.py`
- `pytest -q src/plugins/provider-numilog/tests/test_p1_3252_downloadable_v2.py src/plugins/provider-numilog/tests/test_p0_2797_six_readbook_selected_candidates.py`
- `pytest -q src/plugins/provider-kobo/tests/test_p1_3252_downloadable_v2.py src/plugins/provider-paris/tests/test_p0_3723_downloadable_v2.py src/plugins/provider-bnf-pret/tests/test_p0_3723_downloadable_v2.py`
- `pytest -q src/plugins/provider-reference/tests/test_reference_v2_selected_candidate_3722.py`

**Verdict :** ABI v2 présente chez les cinq adaptateurs examinés. Conformité d'exécution, redirections sécurisées, compteur achats attestés, rattachement visible et publication Numilog **non validés** par ce seul audit. Le journal ZIP reçu est antérieur au déploiement terrain 31/10 16h10.
