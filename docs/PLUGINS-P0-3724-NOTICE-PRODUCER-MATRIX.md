# P0 Notices — #3724 : couverture des producteurs et intentions

Inventaire statique du dépôt au 20 novembre 2026. Il décrit les manifestes
`provider.library` distribués sous `src/plugins`, non les plugins effectivement
activés sur une installation. Aucune requête fournisseur ni inspection de
configuration privée. Les capacités de catalogue, prêt et téléchargement
ne sont pas des capacités d'enrichissement documentaire.

## Matrice de réception

- **Partiel** : port ou chemin de données observé dans le code, avec une
  limite de réception indiquée. **Non attesté** : aucune preuve pour le port
  générique d'enrichissement, donc aucun droit d'exécution implicite.
- **Non applicable** : le déclencheur n'est pas déclaré dans le manifeste ;
  cela ne signifie pas que toutes les données bibliographiques sont complètes.
- Les recettes contrôlées liées ci-dessous sont distinctes de preuves réseau
  réelles. « Par défaut » ne vaut pas activation ni consentement.
- Tous les ports génériques achat/emprunt/import/catalogue restent à
  réceptionner sauf réutilisation explicite du cas achat Numilog existant.

| Provider | Observation / association disponible | Statut et reste à qualifier |
| --- | --- | --- |
| `provider.baen` | Recherche/détail et offre téléchargement, singleton public désactivé par défaut. | Non attesté : C durable et association exacte depuis catalogue ; aucun enrichissement auto. |
| `provider.bnf-pret` | Recherche/détail, sync prêts, emprunt ; désactivé par défaut. | Non attesté : association prêt existant → édition publique, avec scope prêteur et réattestation. Un résultat de recherche ne constitue pas un prêt. |
| `provider.biblioaccess.la-rochelle` | Sync prêts et téléchargement, désactivé par défaut. | Non attesté : lien exact prêt/notice catalogue entre les deux ports ; pas de rapprochement par titre seul. |
| `provider.biblioaccess-catalogue.la-rochelle` | Catalogue singleton public, déclaration même appel depuis ancre titre/URL, #7049. | Partiel : recette C SQLite contrôlée ; preuve de lien avec prêt privé et port d'enrichissement non attestés. |
| `provider.paris` | Recherche et détail Syracuse, emprunt et sync prêts ; carte requise. #7117 et #7118. | Partiel : séries et déclaration anonyme contrôlées, chemin anonyme non activé au runtime ; prêt→C et GetRecord non attestés. |
| `provider.reference` | Téléchargement déclaré, désactivé par défaut. | Catalogue et sync non applicables selon manifeste ; enrichissement non attesté. |
| `provider.numilog` | Inventaire privé, achat existant et association publique dans `purchase_enrichment_observation` ; recherche/détail. | Partiel : #1072, chemin achat A après commit et port spécialisé ; preuve terrain et admission générique à qualifier. |
| `provider.numilog.catalog` | Catalogue anonyme singleton désactivé par défaut ; réponse typée exacte, détail public, cache de parsing. | Partiel : #1886 reçu, #1072 reste distinct ; ne pas déduire un achat ou activer le catalogue. |
| `provider.bnf-catalogue` | Singleton public SRU ; déclaration même appel, preuve ISBN/âge et support bornée. | Partiel : recette source C contrôlée ; âge d'édition numérique exact #4530 non reçu, pas de port d'enrichissement générique attesté. |
| `provider.ebooksgratuits` | Singleton public OPDS ; recherche/détail et téléchargement. | Non attesté : port documentaire anonyme exact sans effacer l'offre de téléchargement ; parsing/recettes #6590/#1917 ne prouvent pas cette admission. |
| `provider.kobo` | Recherche, détail, catalogue compte/vault et téléchargement ; déclaration publique distincte sous garde dans session privée. | Partiel : preuve bibliographique publique déclarative, pas de témoin anonyme C automatique ; port achat/association exacte et politique à qualifier, aucun accès Vault supplémentaire. |
| `provider.gallica` | Recherche/détail et téléchargement, bundle audio déclaré. | Non attesté : port documentaire exact distinct de l'offre/bundle ; supporter les manifestations et ne pas convertir un audio/objet papier en EPUB par inférence. |
| `provider.gutenberg` | Singleton public OPDS, contact requis, `search` liste ; langue dcterms et date issued explicites, offre EPUB distincte. | Non attesté : déclaration même appel durable ; ne pas écraser l'offre, attribuer la couverture d'une autre édition comme exacte ou confondre timestamps Atom et publication. |

Sources internes : manifestes sous `src/plugins/*/cifereca-plugin.json` ;
`provider-biblioaccess/cifereca_biblioaccess_catalogue.py`,
`provider-numilog/cifereca_numilog_public_provider.py`,
`provider-numilog/cifereca_numilog.py`,
`provider-bnf-catalogue/cifereca_bnf_catalogue.py`,
`provider-paris/cifereca_paris.py`, `provider-kobo/cifereca_kobo.py`,
`provider-gutenberg/cifereca_gutenberg.py`. Liens de réception :
[#7049](https://github.com/pzia/cifereca/pull/7049),
[#7117](https://github.com/pzia/cifereca/pull/7117),
[#7118](https://github.com/pzia/cifereca/pull/7118),
[#1886](https://github.com/pzia/cifereca/issues/1886),
[#4530](https://github.com/pzia/cifereca/issues/4530),
[#1072](https://github.com/pzia/cifereca/issues/1072).

## Lacune contractuelle prioritaire : occurrence catalogue et offre

Le contrat `PublicSearchNotice` v1 autorise une déclaration distincte pour
une projection publique. Dans `public_search_projection.py`, la déclaration
doit être sans compte, ownership, téléchargement ni URL de livraison.
`search_orchestration._public_rows_from_response` exige en plus pour le
témoin d'admission durable anonyme une session `__public__`, credentials
et comptes vides, collection `public_catalog`, et exactement une déclaration
pointant sur **le même objet** résultat, sans disponibilité de téléchargement.

Exemple vérifié dans le code Gutenberg : `_parse_atom_entries` produit un
résultat avec `source_collection="Project Gutenberg"`, `downloadable=True`
et éventuellement une URL EPUB. Changer ce résultat en notice en lecture
seule détruirait une offre historique ; copier seulement sa bibliographie
dans une déclaration distincte ne donne pas le témoin durable. Il ne faut
pas relâcher la garde Core existante pour faire passer ce résultat.

Tranche suivante proposée, non encore implémentée : qualifier une occurrence
documentaire anonyme distincte de l'offre, explicitement rattachée à
l'observation exacte du même payload (coordonnées et version contrôlées).
Réutiliser la projection et le writer qui existent ; une version du port
facultatif doit préserver v1 et ne jamais faire passer l'URL d'offre dans C.
Ce choix requiert la coordination des writers en vol #2385/#4552/#1023 ;
aucune branche par nom de fournisseur dans Core. Commencer par Gutenberg
avec un seul parsing et une fixture EPUB à vrai lien OPDS, puis second
adaptateur indépendant. Tester que téléchargement reste disponible selon son
contrat et que C reste purement descriptive après éviction/restart ;
refuser un objet copié, un scope privé, une version contradictoire, un lien
signé et une couverture d'édition non attestée.

## Intentions génériques : garde par déclencheur

| Déclencheur | Preuve minimale avant admission, à réattester à l'exécution |
| --- | --- |
| Achat existant | Réutiliser `PurchaseEnrichmentCandidate/Observation` et A active après commit, exactes ; aucun achat déclenché. |
| Emprunt existant | Observation positive native du prêt, compte/emprunteur/session/scope et édition exacts ; révocation/expiration refusent, pas de nouvel emprunt. |
| Import local | Artifact et Placement exacts déjà attestés, révision du fichier et scope de lecture ; pas d'acquisition, déplacement ni transformation. |
| Catalogue à compléter | Source/version/payload attestés et politique de lecture exacte ; pas d'A inventée, ni GET automatique sur l'ensemble du catalogue. |

Le journal #4982 demeure le seul pending store ; #6939 possède le cycle
runtime. Les preuves privées ne doivent pas persister en clair dans les jobs.
À défaut de port qualifié, résultat unsupported et aucune admission.
L'import local n'est pas un provider.library : le vérifier séparément sur
le chemin Core d'origine/Artifact/Placement, sans créer de faux fournisseur.
La recette devra couvrir 0/1/N, ABA/révocation, retry/restart, absence
de droits/placement nouveaux, parsing et GET non dupliqués.

Cette matrice est une réception d'inventaire statique, pas une preuve
d'activation ni une clôture de #3724.
