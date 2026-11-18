# #6366 / #4982 — point de convergence et recette de charge

Statut : **en cours, pas de réception ni activation**, 17/11/2026.
Les PR listées sont des références de travail, non des fonctionnalités
présumées présentes sur main. Les verdicts doivent être revérifiés sur
le HEAD effectif après convergence, par CI/CD.

## Socle autonome

Le journal sur main accepte les quatre types fermés et leurs dépendances
de générations exactes : fetch → reparse → relink → recompute. Le test
`tests/test_core_p0_4982_journal_load.py` utilise des écritures de journal
synthétiques, pas des résultats fournisseur ni des publications C.
Il exerce 24 DAG (96 clés), quatre propriétaires Storage du même SQLite,
96 requêtes redondantes et 96 tentatives de claim concurrentes par rang,
une seule lease par clé, versions concurrentes puis retour ABA, refus de
saturation à plafond réduit pour la recette, durabilité après fermeture,
refus des deux scopes privés et mutualisation du public. Aucune donnée
privée réelle, transport, nouveau pool de production ni modification de
droits. Les tests ne sont pas encore exécutés au moment de cette rédaction.

## Convergence des lots d'exécution

| Fonction | PR de référence | Justification restant à établir |
|---|---|---|
| Journal / lecture bornée / admission | #6558, #6559, #6566, #6573 | Dédup, propagation CAS, quota partagé et coût scan après convergence |
| Recomposition exacte, multi-source | #6557, #6561, #6563, #6567 | Writer et relation revalidés ; dépendances dirty-C, erreurs et saturation |
| Notification N2 | #6569 | Pas de churn ni invalidation étrangère ; relecture après notification perdue |
| Reparse | #6576, #6578, #6580, #6581, #6583, #6585 | Input réel retenu, cache-only, expiration et lease ; recette fixture, production inactive |
| Relink | #6574, #6587 | Extension forte OriginLink, overlap et ancre conservée ; pas de split implicite |
| Fetch | #6589, #6592, #6595, #6596 | Autorité éphémère, départ/commit protégés ; fixture HTTP, politique réelle non branchée |

Les copies de fichiers de PR parentes présentes dans les branches
d'intégration sont des dépendances de convergence, pas de nouveaux
composants à fusionner deux fois. CI/CD vérifie les diff et choisit
l'ordre de merge exclusivement au HEAD qualifié. Aucun besoin de
nouveau workflow, second stockage documentaire ou exécuteur.

## Protocole intégré à réaliser après convergence

- Sur une seule instance de broker et journal, utiliser les vrais ports
  et writers, avec réponses HTTP enregistrées et graphes v14; disposer
  au moins deux cohortes de sources indépendantes, quatre types
  éligibles et des générations obsolètes. Compter les exécutions réelles
  par type (pas seulement les claims); vérifier absence de GET pour
  reparse, relink et recompute, au plus un départ pour chaque consentement
  fetch; aucun replay du GET après crash.
- Réserver les slots de fond et injecter des tâches interactives dans
  le même budget. Au moins 3 workers et 1 réservation utilisateur.
  Capturer maximum queued/running/inflight, refus, progression de toutes
  les cohortes, nombre de tours de scan, latence d'attente et temps de
  transaction (p50/p95/max). Assertions déterministes sur ordre,
  cardinalité et progression; timings comme mesures, pas de seuil SLA
  inventé ni dépendance aux sleeps.
- Prolonger le flux d'une cohorte et injecter un travail d'une autre
  derrière et devant le curseur. Mesurer la progression sous créneaux
  libérés ; la cyclicité des clés n'est pas l'équité par fournisseur,
  compte ou classe. Vérifier qu'un candidat refusé ne fait pas perdre
  ceux qui suivent. Les dépendances en attente ne consomment pas de
  worker. Simuler les erreurs et remplacements pendant les commits.
- Croiser expiration/révocation, arrêt et réouverture de Storage, lease
  récupérée, annulation avant/après claim, navigation rapide A→B→A;
  garder la dernière C bonne. Aucune notification de C avant commit ;
  aucun rafraîchissement de N3 ou de C voisine sans changement.
- Exécuter ensuite la recette six vues liste/détail sur main intégré,
  online/offline et reboot, conformément à #6366. Vérifier séparation
  effective des profils et comptes ; un refus privé dans le journal
  public ne constitue PAS la recette du scheduler privé. Aucun profil
  privé ne doit être déduit d'un DocumentRef public ou d'un résultat
  Search mis en cache.

## Bloquants spécifiques à traiter

1. **Capacité à durée de vie finie.** Le journal existant limite à 2048
   clés logiques mais ne recycle pas ses lignes terminales. Les
   remplacements d'une clé existante restent possibles; les nouvelles
   clés finissent donc par être refusées durablement, y compris après
   reboot. Le test réduit le plafond à 96 pour reproduire cette
   propriété, pas pour simuler le débit à 2048. Ne pas activer un
   ordonnanceur continu ainsi. Concevoir un recyclage borné préservant
   les références de dépendance exactes et la monotonie anti-ABA y compris
   après éviction/recréation (par exemple un allocateur monotone durable
   commun avec migration explicite du contrat de génération). Toute
   suppression naïve de ligne terminale est interdite : elle pourrait
   ressusciter une ancienne lease ou génération. Mesurer le coût et
   revalider les CAS de toutes les PR consommatrices avant choix.
2. **Équité et priorité.** Le curseur de #6566 fournit une rotation
   de clés publiques, pas le round-robin par scope/âge du contrat.
   Établir la politique et la mesure après convergence. L'admission
   actuelle est process-local; la lease SQLite empêche double publication
   mais ne garantit pas un quota interprocessus.
3. **Garde fournisseur et transactions.** Les scans OriginLink et
   overlaps peuvent aller jusqu'aux bornes prévues; sous garde de
   session ces scans peuvent retarder close/reconnect. Mesurer le
   parcours aux tailles représentatives, distinguer calcul,
   verrouillage, attente et écriture. Ne pas masquer ce risque par un
   timeout arbitraire ou par une assertion sur matériel CI.
4. **Ports réellement activables.** Le cache brut Numilog est limité
   à la session/TTL mémoire ; pas de reparse après perte des octets.
   Fetch est à usage unique sur page déjà attestée, pas une politique
   de refresh 24 h ou après redémarrage. Les autres fournisseurs et
   scopes privés ne sont pas couverts par cette recette.
5. **Réception et ABI.** Les six vues physiques et les ports
   fournisseurs/stockage doivent être reçus avant activation; les
   fixtures et des gates verts sur branches ne sont pas cette preuve.
   Garder les tickets ouverts jusqu'aux preuves propres et au résultat
   fusionné. Pas de transfert #5975 ni de nouvelle issue.

Les métriques doivent être bornées, agrégées, sans identifiant de
compte, URL, query, token, corps ni chemin local. Remonter les valeurs
constatées, configurations de charge et SHA/run exacts, sans inventer
de chiffres ni publier une réussite sur main non observée.
