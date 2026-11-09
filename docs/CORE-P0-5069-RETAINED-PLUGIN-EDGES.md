# P0 #5069 — B9 : dépendances plugins des transactions retenues

Le diagnostic `cifereca-update --retention-inspect --json` croise désormais le plan pur de rétention Core (24 h glissantes, minimum deux générations, épingles et dépendances) avec les références `packages.before/after.versions` des journaux transactionnels NOFOLLOW.

`cross_store_reference_inspection` expose uniquement des compteurs et des preuves d'intégrité :
- `retention_edges_qualified` indique si **chaque** transaction observée correspond à une décision de rétention issue du plan Core.
- `retained_transaction_plugin_edges` / `resolved_retained_edges` / `unresolved_retained_edges` comptent les versions distinctes référencées par les transactions **retenues**.
- `candidate_transaction_plugin_edges` compte les versions distinctes des transactions classées candidates (qui peuvent aussi être référencées par des transactions retenues).

Une transaction manquante, un doublon de décision, un journal malformé ou une identité incohérente annule la qualification et remplace les compteurs concernés par `null`. Les références non résolues restent des bloqueurs, non des autorisations d'effacement. Aucun nom de package, chemin ou contenu de sauvegarde n'est projeté.

## Limites explicites

Il ne s'agit **pas** d'une preuve exhaustive des références ni d'un plan d'effacement : les releases, snapshots, sauvegardes et staging ainsi que les chemins de crash/reprise ne sont pas encore reliés en un graphe clos ; les déclarations d'ownership et la revalidation atomique sous lease ne sont pas suffisantes pour l'apply. `safe_to_apply=false` et `cross_store_reference_proofs` restent inchangés. Ne pas supprimer de versions plugins sur la seule base de ces compteurs.


## B10 — conflit entre les plans locaux et le rollback

Le champ `retained_plugin_candidate_conflicts` dénombre les versions distinctes
qu'un **plan local de versions plugins** classe candidates alors qu'elles restent
référencées par une transaction **retenue** au titre de la politique Core.
Un conflit positif indique qu'appliquer le plan local isolément risquerait de
casser les preuves de restauration ; il ne constitue pas une liste de purge.
Le champ vaut `null` si la classification intégrale des transactions ou
des versions n'est pas démontrée. Même à zéro, d'autres dépendances restent
hors du graphe : aucune suppression n'est autorisée.
