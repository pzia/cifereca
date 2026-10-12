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

- Le JSON de la route HTTP candidate est soumis au même parseur strict
  que les fixtures : UTF-8, pas de clés dupliquées ni de constantes/flottants
  non finis, taille et type MIME bornés. La configuration du délai rejette
  les valeurs invalides ou non finies. Les exceptions de transport ne sont
  pas chaînées dans les erreurs publiques afin de ne pas exposer de requêtes
  ou paramètres sensibles. Le dernier inventaire sain est préservé.
- Le transport candidat ignore les proxies issus de l'environnement et
  du système afin de ne pas relayer par accident les requêtes du serveur
  local. Les deux seules routes HTTP GET permises sont `/api/v1/sources`
  sans paramètre et `/api/v1/search` avec `source_id` validé et `q`
  borné ; toute autre route ou clé est refusée localement, sans réseau.
- Le mode HTTP refuse les redirections, les URL contenant des identifiants,
  les chemins d'origine, et HTTP en dehors de loopback ; délais et tailles
  bornés, réponse JSON strictement typée.
- Le cache conserve l'inventaire **complet** et validé du serveur,
  tandis que la vue publiée est filtrée localement par `show_synthetic`
  (désactivé par défaut). Changer cette option puis redémarrer ne nécessite
  pas d'accès HTTP ni d'incrément de génération côté serveur ; les sources
  synthétiques masquées ne sont pas republiées. Un ancien cache qui avait
  déjà effacé ses sources synthétiques peut nécessiter une nouvelle
  génération de découverte pour les reconstituer.
- \`source_id\` limité au namespace du Core ; \`synthetic.merged\` est un
  **kind**, pas un identifiant avec points. Exemple : source \`merged\`.
- Les secrets, droits, URLs signées et capacités non implémentées ne sont
  jamais projetés dans \`SearchResult\`.
- L'activation est transactionnelle : lecture et validation du cache sont
  préparées hors verrou, puis publiées uniquement si aucune activation ou
  désactivation plus récente n'est intervenue. Les anciens abonnements,
  sessions et snapshots sont invalidés à chaque changement de cycle de vie.
  Après désactivation, `snapshot()` ne republie jamais l'ancien inventaire ;
  une nouvelle activation peut toujours le relire depuis le cache local.
- Les sessions existantes sont invalidées lorsque leur source passe à
  `disabled`, `removed`, perd `catalog.search`, ou change de révision ou
  génération de catalogue. Même une réapparition sous la même révision exige
  une nouvelle session. Les autres sources dont le contrat reste identique
  ne sont pas interrompues. L'inventaire validé reste stocké, sans purger
  les notices Core déjà indexées.
- Le garde de session examine uniquement l'inventaire local, avant l'appel
  de recherche et de nouveau avant d'en publier les résultats. Une réponse
  devenue périmée pendant une requête HTTP en cours est jetée ; ceci ne
  constitue pas une annulation réseau native.
- En mode fixture, la lecture des JSON locaux est limitée à **1 Mio par
  fichier**, refuse liens symboliques, formats invalides, encodages non UTF-8,
  valeurs numériques JSON non standard et fichiers spéciaux. Les erreurs
  sont typées sans recopier chemins ni contenu. Un cache d'inventaire
  endommagé ne bloque pas l'activation ; un échec de `refresh()` préserve
  le dernier inventaire sain.
- L'inventaire accepté est écrit dans un fichier temporaire aléatoire privé
  (permissions restreintes), puis publié atomiquement. Une erreur d'écriture
  ne remplace ni le cache précédent ni le snapshot en mémoire. Les fixtures
  de résultats manquantes restent volontairement équivalentes à zéro résultat.
- La recherche peut fonctionner en mode fixture sans serveur. En mode HTTP
  candidat, l'indisponibilité HTTP produit une erreur explicite ; l'inventaire
  validé reste inchangé. Les notices précédemment indexées restent dans Core.
- Les identifiants de notices du schéma candidat doivent être opaques et sans
  chemin/URL ; les titres et auteurs contenant des URL, données de compte,
  secrets ou caractères de contrôle sont refusés avant normalisation.
  Les recherches sont bornées à 300 caractères sans caractères de contrôle.
  Ces contrôles ne qualifient pas le contrat distant v0.2.
- Les notices issues du schéma candidat projettent uniquement les faits
  bibliographiques explicitement publiés : éditeur, type, format, date
  validée, langue et ISBN-13 Bookland contrôlé. Les données libres de détail,
  URLs, tokens, droits et liens d'acquisition restent exclus. Ceci **ne
  certifie pas** que le serveur v0.2 produit ces champs.
- La fixture ne possède qu'une page (`page=0`) : les pages suivantes
  rendent une liste vide sans répéter les résultats. En mode HTTP candidat,
  une demande `page>0` est refusée explicitement tant que le contrat de
  pagination du serveur n'est pas attesté.
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
