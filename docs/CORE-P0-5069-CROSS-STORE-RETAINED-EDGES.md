# P0 #5069 — B9 : qualification des arêtes transaction → version plugin

Le diagnostic officiel `cifereca-update --retention-inspect --json` joint
désormais le plan de conservation Core aux références de versions plugins
déduites des journaux transactionnels bornés et lus sans suivi de symlink.

- `retained_transaction_edges` : nombre de versions distinctes référencées
  par au moins une transaction dont le plan indique `retain`.
- `candidate_transaction_edges` : nombre de versions distinctes référencées
  par au moins une transaction indiquée `candidate`. Une même version peut
  figurer dans **les deux ensembles** ; ces compteurs ne s'additionnent pas.
- `retained_unresolved_edges` : références des transactions conservées
  impossibles à résoudre dans l'inventaire des versions qualifiées.
- Si le plan Core est absent, malformé, dupliqué, ou ne couvre pas exactement
  les transactions lues, les trois champs valent `null` et la couverture
  croisée est refusée. Les identités de journal restent opaques.

Ces champs sont **de l'observation uniquement** ; un statut `candidate`
ne confère **aucun droit de suppression**. La conservation reste fondée sur
l'union 24 h + 2 générations + épingles. Le graphe des releases, snapshots,
backups et staging doit encore être qualifié avant tout `maintenance apply`.
`safe_to_apply=false` et `cross_store_reference_proofs` restent bloqués.

Tests synthétiques : preuve retenue/candidate, référence retenue absente,
plans forgés ou incomplets et version partagée.
