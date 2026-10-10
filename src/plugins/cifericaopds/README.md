# CifericaOPDS — plugin pilote 0.1.0

**Statut : expérimental, désactivé par défaut.** Dépend du contrat public
\`provider.collection/1\` livré dans la PR Core
[#970](https://github.com/pzia/cifereca/pull/970).
Le serveur CifericaOPDS **v0.2.0 réel n'a pas pu être vérifié** : son fichier
\`docs/api/openapi-mvp-v02.yaml\` n'est pas accessible depuis ce travail.

L'API JSON décrite ci-dessous est **illustrative et non normative**.
Le mode \`candidate_v02\` ne doit pas être activé sur un serveur de production
avant confrontation à l'OpenAPI réellement livrée.

## Ce que fait le pilote

Un seul package statique \`org.cifereca.integration.cifericaopds\` publie
N sources locales \`cifericaopds.<source_id>\` sous la catégorie
\`provider.collection\`. Chaque source fournit un \`ProviderPlugin\` et un
\`ProviderSession\` distincts. La découverte est déclenchée **uniquement**
par \`refresh(explicit=True)\`, jamais par l'attachement Core ou \`snapshot()\`.
La sauvegarde de l'inventaire validé est locale et persiste au redémarrage.

Le pilote ne récupère **aucun code depuis le serveur** et ne dépend d'aucun
module Core privé. Il ne crée pas de droits d'achat, d'emprunt ou de
téléchargement à partir de liens HTTP ; pas de bearer ni de secrets transférés.

## Test hors réseau

Activer explicitement le plugin dans le gestionnaire, laisser
\`mode=fixture\` (valeur par défaut) et déposer
\`fixture-sources.json\` dans le répertoire de données du plugin fourni par
\`PluginContext.data_directory\` :

\`\`\`json
{
  "api_major": 1,
  "discovery_generation": 1,
  "sources": [
    {"source_id":"alpha","revision":"1","kind":"catalog","status":"ready","capabilities":["catalog.search"]},
    {"source_id":"beta","revision":"1","kind":"catalog","status":"ready","capabilities":["catalog.search"]}
  ]
}
\`\`\`

Résultats facultatifs : \`fixture-results.json\` dans le même dossier.

\`\`\`json
{
  "alpha":{"items":[{"id":"1","title":"Livre alpha"}]},
  "beta":{"items":[{"id":"2","title":"Livre beta"}]}
}
\`\`\`

Appeler \`refresh(explicit=True)\` hors thread GTK, via le service parent
renvoyé par le gestionnaire, puis \`LibraryService.refresh_virtual_collections()\`
si le Core n'a pas encore consommé la notification. Le Core reste propriétaire
du registre des providers virtuels.

Pour essayer le faux HTTP local du test, \`mode=candidate_v02\` tente,
**uniquement sur action explicite**, \`GET /api/v1/sources\` ; la recherche
utilise alors \`GET /api/v1/search?source_id=...&q=...\`. **Aucune de ces
routes n'est attestée sur le serveur réel**.

## Sécurité, limites et reprise

- Le mode HTTP refuse les redirections, les URL contenant des identifiants,
  les chemins d'origine, et HTTP en dehors de loopback ; délais et tailles
  bornés, réponse JSON strictement typée.
- \`source_id\` limité au namespace du Core ; \`synthetic.merged\` est un
  **kind**, pas un identifiant avec points. Exemple : source \`merged\`.
- Les secrets, droits, URLs signées et capacités non implémentées ne sont
  jamais projetés dans \`SearchResult\`.
- La recherche peut fonctionner en mode fixture sans serveur. En mode HTTP
  candidat, l'indisponibilité HTTP produit une erreur explicite ; l'inventaire
  validé reste inchangé. Les notices précédemment indexées restent dans Core.
- **Non implémenté :** auth/secret_ref, mapping OpenAPI v0.2 attesté,
  \`POST /search/query\`, SSE/jobs, annulation native amont, champs détaillés.
  Ces points sont bloqués tant que l'OpenAPI serveur n'est pas fournie.

## Installation et développement

Depuis la racine du dépôt :

\`\`\`sh
cd src/plugins/cifericaopds
python -m pytest -q
\`\`\`

Construire le ZIP de plugin via l'outil existant
\`scripts/plugin_packaging.py\` en conservant \`cifereca-plugin.json\`
**à la racine du ZIP**, sans enveloppe de dossier. Aucune release n'est
déclenchée par cette PR.

**Handoff #969 :** avant toute publication réelle, confronter les routes,
paramètres, erreurs, pagination et modèles JSON à
\`docs/api/openapi-mvp-v02.yaml\` du serveur effectivement déployé. Si le
contrat diffère, adapter uniquement ce plugin ; solliciter #968 si nécessaire.
