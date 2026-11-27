# #8183 / #8185 — statut descriptif du runtime installé

## Contrat et compatibilité (D07, D09)

Plugin API 1.4.9 ajoute le port optionnel `RuntimeStatusReader.runtime_status(full=False)`
et le DTO `RuntimeStatusSnapshot`, schéma `cifereca.runtime.status/1`.
Le runtime Kobo-ADEPT 1.2.38 traduit son propre protocole et réutilise la même
projection de capacités que son lecteur descriptif d'acquisition, sans second
probe. Le Core valide le DTO, conserve le cache et adapte les noms des champs
de l'ancienne façade `LibraryService.kobo_adept_state` (ABI inchangée).
Un port natif invalide ne donne aucun résultat positif et ne retombe pas sur
l'ancien lecteur. Une absence ou réponse invalide ne bloque pas la prochaine
lecture valide dans le cache.

Ce statut ne constitue jamais une autorité de droit, de sélection de source,
d'identité, de consentement, de transfert ou de publication. Les brokering
d'acquisition et mutations restent indépendants. Le probe normal conserve la
projection filtrée existante ; seul `full=True` demande le probe complet et
rend une erreur de configuration de statut bloquante.

Les packages plugins sont installables indépendamment : un runtime pré-1.2.38
peut encore exposer seulement `probe_core` ou `probe`. La compatibilité
historique de lecture est isolée dans `core/runtime_status.py::_read_legacy`,
uniquement derrière la façade de runtime déjà résolu, en l'absence du nouveau
port. Elle ne s'applique pas à un port natif non conforme. Le témoin
`test_old_installed_runtime_retains_read_only_status` et les tests historiques
de cache/probe la couvrent. Ne pas supprimer ce pont avant preuve que ces
installations ne sont plus supportées ; aucune autorité métier ne dépend de
cette compatibilité.

## D08 / D10 — compatibilités conservées

Le rapport #8184 (SHA 1489223c55c78c92fe62ac9efcf9e75ffb36f542) qualifie les
gardes restantes : v15 ADEPT négatif exact source / v16 autorité positive,
reçu ACSM v1 transitoire, retour historique en lecture seule, admission
legacy directe, adaptateurs de droits Paris/BnF et projections Kobo/Numilog.
Ni remplacement natif équivalent de tous les parcours ni expiration déployée
des reçus uniques n'est démontré. Les gardes ne sont donc pas retirées, et aucun
writer historique n'est réactivé. Pas de renommage lexical, migration SQL ou
changement des chemins d'acquisition.
