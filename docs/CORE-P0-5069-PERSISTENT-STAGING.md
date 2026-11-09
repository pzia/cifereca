# P0 #5069 B7 — Staging persistant connu, sans purge

`cifereca-update --retention-inspect --json` publie maintenant
`plans.known_persistent_staging` : repérage strictement en lecture seule des
enveloppes de staging transactionnel Core, pré-activation Core et installations
de versions de plugins (`tempfile.mkdtemp` sous un package).

L'inspecteur n'ouvre jamais un fichier de staging, n'inspecte pas les octets
SQLite/configuration/plugins, ne suit aucun lien symbolique et ne crée pas de
répertoire XDG. Les noms des générations et les IDs de packages sont remplacés
par des empreintes opaques. Les chemins inconnus ne sont pas prétendus
appartenir à l'Updater.

Les stagers non finalisés ne possèdent **ni reçu d'ownership durable, ni
horodatage de génération fiable** : ils restent donc tous
`owned_by_updater=false`, `pinned=true`, `installed_at=null` et
`safe_to_apply=false`. La politique 24 h + minimum 2 ne donne pas le droit
de les supprimer à partir d'un mtime ou d'une forme de nom. Le compteur des
candidats est toujours zéro.

`pending_stores` conserve `persistent_staging` et
`cross_store_reference_proofs` : l'inventaire B7 est partiel et diagnostique,
pas une preuve des processus en cours ou des références de rollback.
Les autres scratch temporaires hors magasins persistants relèvent de leur
producteur et ne sont pas balayés en dehors du store de l'Updater.
