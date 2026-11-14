# P0 #5376 — qualification des suites de mesure et de transport

Les manifestes `ci-accounting.json` et `security-transport.json`
attribuent 6 fichiers effectivement examinés (14/11/2026). Ils sont ajoutés
aux 17 fichiers déjà classés dans le dépôt, sans supposer que les milliers de
cas restants sont unclassified par nature.

## Critères d'attribution vérifiés

- `test_ci_test_collection_inventory.py` : assertions sur validation du
  manifeste, comptage de collecte, publication atomique et refus d'une
  collecte partielle. **Contrat CI**, sans exécution de domaine métier.
- `test_ci_test_execution_inventory.py` : teste les hooks pytest, les rapports
  et une véritable exécution pytest enfant **sur données synthétiques**.
  **Intégration CI** ; ne mesure pas une recette métier réelle.
- `test_ci_test_execution_aggregate.py` : rapprochement des nodeids, SHA et
  observations synthétiques, contrôle du rejet des preuves incohérentes.
  **Contrat CI**.
- `test_ci_security_mutation_campaign.py` : exécute deux mutations de sûreté
  dans des sous-processus pytest, sur sockets/DNS fictifs. **Intégration
  d'outillage CI**, non réseau réel.
- `test_public_httpx_transport.py` : exerce le transport HTTPX du Core avec
  DNS/socket factices, refus des réseaux privés/mélangés et contrôle de pair.
  **Intégration du contrat transport Core→plugins**, pas une preuve Internet.
- `test_core_p0_3867_attested_https_delivery.py` : exerce les redirections,
  autorisations et publication/staging par `httpx.MockTransport` et FS temporaire.
  **Intégration acquisition**, pas une livraison authentifiée fournisseur.

Les cinq premiers fichiers CI ou réseau conservent leurs protections ; aucune
suppression de test, aucune règle fournisseur ajoutée dans Core. La
classification est par **fichier** et niveau primaire ; elle ne prouve ni que
tous les cas sont pertinents, ni leur couverture des invariants. Les tests
source-only, les simulations, les mesures de mutation et les recettes GTK
restent séparés dans les preuves de fermeture de #5376.
