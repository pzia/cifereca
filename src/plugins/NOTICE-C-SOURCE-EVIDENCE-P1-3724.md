# Notice C — inventaire des preuves Plugins (#3724)

État au 08/11/2026. Ce tableau décrit les **observations déjà disponibles** dans les parseurs ; il ne définit **ni schéma Notice C, ni capacité `metadata.consolidator`**, ni écriture Core. Les droits A/B, chemins et profils restent distincts des faits bibliographiques. L'activation dépend de l'API publique Core suivie par #2385 et #4981.

| Fournisseur / entrée existante | Identité / frontière de provenance | Observation exploitable sans nouveau GET | Ce que l'observation **ne prouve pas** |
| --- | --- | --- | --- |
| Paris/Syracuse — `provider-paris/cifereca_paris_normalize.py::normalize_search_result` | `Resource.RscBase` + `Resource.RscId`, scénario original | Titre, auteur, éditeur, date, description, facettes `Resource` ; série seulement si liée à la notice par `project_attested_search_series` | Facettes globales de page attribuées à chaque livre, possession, édition numérique, âge lecteur |
| Numilog public — `provider-numilog/cifereca_numilog_public_identifiers.py::extract_public_identifiers` | URL canonique du **produit public** ; preuve `observed_public_label`, `source_url`, `observation_scope` | ISBN/EAN validé par rôle **`paper`, `epub`, `pdf`, `digital_unspecified`** sur la fiche produit explicitement chargée | ISBN générique JSON-LD = ISBN numérique ; couverture/format effectivement possédé ; droit d'acquisition ou catalogue exhaustif |
| Kobo — `provider-kobo/cifereca_kobo_catalog.py::normalize_item` | `source_provider`, `source_credential`, `source_collection`, `record_id` ; révision typée distincte de l'ID | Auteur, langue contrôlée, prix, format, lien produit public, miniature filtrée ; distinction entre catalogue public et manifeste personnel | Achat depuis simple présence catalogue ; âge numérique depuis clés fournisseur non qualifiées ; référence de couverture = preuve d'édition |
| BnF SRU — `provider-bnf-catalogue/bnf_age_evidence_probe.py` | ISBN propre à l'édition et enregistrement SRU MarcXchange valide | Témoins bruts 329/333/833 et témoins CNLJ qualifiés, avec provenance du champ | Âge numérique pour édition voisine, droit de lecture, consolidation automatique d'une notice sans Core |

## Corpus de non-régression existant

- **Numilog** : `provider-numilog/tests/test_numilog_public_digital_isbn_local_refinement_p0.py` — papier distinct d'EPUB/PDF ; une absence d'étiquette reste inconnue ; pas de GET N+1 implicite.
- **BnF** : `provider-bnf-catalogue/tests/test_bnf_age_evidence_probe_p1.py` — réponses SRU publiquement observées, ISBN/édition exacts, champs et diagnostics contradictoires.
- **Kobo** : `provider-kobo/cifereca_kobo_catalog.py` — clés d'âge non autorisées écartées, `owned` et `downloadable` typés, URL de couverture filtrée.
- **Paris** : `provider-paris/cifereca_paris_normalize.py` — facettes de ressource et série attachée à la notice, séparées des facettes globales.

## Contrat à attendre avant raccordement

1. **Core #2385/#4981** : structure publique et validation d'une observation C (édition exacte, provenance, source, période, priorité et conflit). Ne pas inventer ce DTO dans les plugins.
2. **Plugins #3724** : convertir **seulement** des valeurs déjà parsées et attestées vers le port public final ; conserver la distinction papier/numérique, la preuve de source et l'état « inconnu ».
3. **Core #4982** : ordonnancement et persistance ; aucun accès direct SQLite ni crawl automatique depuis l'adaptateur.
4. **Tests de raccordement** : deux comptes avec même `record_id`, deux éditions homonymes, rôles ISBN contradictoires, réponse partielle, annulation/epoch et replay ; tester les frontières par fournisseur, sans déduire droits ou préférences.

Les exemples/tests existants sont des **preuves de parsing ciblées**, pas une validation du port C ni une recette réelle de tous les fournisseurs.
