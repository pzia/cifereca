# #8186 — Frontières statiques Core / plugins

Baseline de ce lot : 6f04ccf395eb8bd2b94c4649cf9ad2d9fa8c0493 (#8190 fusionnée).
D01–D06, D11–D13 ; aucun changement des ports métier ou de l'autorité des droits.

- D01/D03/D04 : le scanner de packaging parcourt les sources de production de
  chaque package, sans filtre sur le type de plugin, y compris les modules
  imbriqués. Imports Core statiques et dynamiques privés, chemins littéraux,
  constantes et alias uniques, accès privés via context/ctx/host/broker,
  y compris des alias simples et getattr littéral. Les imports dynamiques
  reconnus dont le chemin ne peut pas être résolu sont rejetés explicitement.
  Les champs privés d'un objet local ne sont pas assimilés à ceux du Core.
- D02/D05 : la gate de couplage existante compare les multisets de signatures
  AST (nœud, propriétaire lexical et branche de contrôle) par fichier, type
  et fournisseur. Les positions et commentaires ne changent pas la signature.
  Un retrait ne compense pas une nouvelle signature ; renommage/déplacement
  d'un propriétaire sont conservativement de nouvelles preuves à recevoir.
  Le format de sortie historique demeure agrégé après comparaison.
- D06 : ownership des modules et identifiants des manifests ; déclaration
  requires/recommends ou contrat de catégorie pour les références inter-package
  détectées. Les imports relatifs et services intra-package restent admis.
  L'unité de qualification statique est le package, pas le descriptor individuel
  lorsque plusieurs entrypoints partagent des modules.
- D11 : les 28 packages recensés ont une qualification dans le graphe CI ;
  les quatre packages maintenance.tool alimentent maintenance-page via le
  broker Core maintenance.page.host, d'où les arêtes explicites. Les autres
  sept packages auparavant non qualifiés ont été parcourus : pas d'import
  inter-package ni de résolution nommée externe relevés ; BiblioAccess est
  couvert comme fournisseur de provider.library par le contrat home-page.
  Les dépendances requires/recommends et contrats existants restent actives ;
  package nouveau/non audité, modification manifest, graphe invalide ou
  absence de tests conservent le repli complet.
- D12 : tous les fichiers Python de Browser Recorder sont soumis à la gate
  HTTP. La navigation Playwright autonome documentée n'est pas une pile HTTP
  Python interdite et ne nécessite aucune exception de package.
- D13 : fixtures négatives et positives de signatures, compensation,
  déplacement, alias/import dynamique, accès privé, dépendances déclarées,
  objet local et navigation Playwright.

Limites : analyse statique bornée, pas de sandbox, pas de résolution générale
des imports calculés, de l'exécution dynamique ou des flux de données Python.
Les alias à écritures multiples ou ombragés ne sont pas inférés ; les appels
de service à identifiant calculé et les chemins de réflexion non littéraux
ne sont pas des preuves d'accès privé. Les signatures portent sur les signaux
AST reconnus, pas sur la reachabilité ni l'intégralité des effets d'une branche.
Les compatibilités et gardes historiques qualifiées en #8184/#8185 sont préservées.
