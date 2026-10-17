# Core P1 #1916 — provenance du repli catalogue public

Lors d'une panne technique de recherche, `known_public_fallback` lit uniquement
les notices déjà indexées pour l'identité et la source demandées. Une notice
`remote_catalog` ou `owned=False` n'atteste pas à elle seule une visibilité
publique : une notice personnelle non possédée peut avoir ce même état.

Le repli exige une déclaration `metadata.source_collection` de type chaîne,
égale à `catalog`, `public` ou `public_catalog`, après normalisation des espaces
externes et de la casse. Cette valeur vient du `SearchResult.source_collection`
conservé par le miroir Core. Les chaînes absentes, vides, libres ou inconnues,
ainsi que les collections personnelles et les valeurs malformées, ne sont pas
transformées en preuve publique. Un titre, un nom de fournisseur, l'absence de
droit ou un lien distant ne complètent pas cette preuve.

Les autres gardes restent applicables : identité/source exactes, état distant
admissible, absence d'artefact/placement, fraîcheur bornée, requête et filtres
courants. Les notices admises gardent `known_catalog_stale`, `owned=False`,
`downloadable=False`, sans URL ni credential privés. Le repli ne rafraîchit
pas le miroir et n'est pas mis en cache comme réponse réseau réussie.

Si seule une notice personnelle ou non qualifiée est connue, la source reste
indisponible avec l'avertissement « aucun résultat connu ». Ce n'est pas un
zéro confirmé par le fournisseur. Les recherches en ligne continuent selon
leurs capacités habituelles.

Compatibilité Plugins #1917 : une collection dont la valeur est un nom libre
(par exemple un nom de catalogue), ou dont la provenance n'est pas déclarée,
reste utilisable en recherche live mais ne fournit pas de secours public.
Le propriétaire Plugins doit qualifier sa visibilité avant de déclarer une
des valeurs publiques ci-dessus ; le Core n'introduit pas de correspondance
spécifique à un fournisseur ni de migration devinant les données historiques.
La qualification Numilog conserve ses gardes spécifiques existantes.
