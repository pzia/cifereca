# Diagnostics P0 #907 — rendement des sources Jeunesse

Les deux sources d'âge actuellement prises en charge (Milan et École
des loisirs) sont visibles dans l'état cache-only, même en l'absence de
plugin installé. **BookTrust et Usborne sont retirés (#957)** : le diagnostic
ne les réintroduit ni dans la liste des sources connues, ni dans les plugins
installables. Open Library demeure une famille **bibliographie** distincte
de l'attestation d'âge. L'état activé d'un plugin ne prouve pas la santé du site.

Chaque collecte **explicitement demandée** enregistre déjà son état
`record_collection` ; le diagnostic Jeunesse ajoute une cause de rendement
déduite **uniquement des compteurs structurés attestés** :
HTTP/transport, réponse invalide, ancres sans fiches, candidats sans
ISBN/âge attesté, perte après qualification ou vide non vérifié.
Les causes de perte non directement attestées sont marquées
« hypothèse, à vérifier ».

Le bouton « Copier le diagnostic des sources (expurgé) » est local,
n'effectue aucun GET et n'inclut jamais l'identité, les titres, ISBN,
liens signés, HTML, cookie ou secret : seulement source publique, état du
plugin, cache disponible, compteurs bornés, mode de recherche, limite,
empreinte HTML tronquée et causes typées. Un événement agrégé est émis
par `context.report_diagnostic` après une collecte explicite, sans nom de
lecteur, identifiant de prêt ni requête utilisateur.

**Limites :** en absence de compteurs détaillés publiés par l'adaptateur,
le diagnostic affiche `vide_non_verifie` ou `non_teste`, jamais « site
vide » ou « succès ». Ce lot n'effectue ni réécriture du parseur
École des loisirs #944, ni collecte en arrière-plan, ni création de
fixture HTML réseau en CI. Une recette réelle hors CI reste requise.
