# #4552 — Déclaration inerte d'un consolidateur texte V1

Le port Core `decode_consolidator_manifest` reçoit les lignes déclaratives et les octets des membres issus du **même snapshot de package authentifié**. Il ne lit aucun fichier, ne charge aucun module, n'exécute aucun point d'entrée et ne choisit aucun consolidateur.

Chaque ligne contient exactement : `plugin_id`, `version`, `canonical_type`, `scope`, `schema_id`, `source_member`, `source_sha256`, `max_bytes`, `inherited_allowed`, `allowed_values`. Le membre est un fichier `consolidators/<nom>.py` sans traversée de chemin ; son SHA-256 et son encodage UTF-8 sont vérifiés. Le registre est borné à 128 déclarations ; une ambiguïté, un champ inconnu, un membre manquant ou un schéma invalide refuse tout le registre.

La sortie associe une déclaration, un schéma et une liaison de code installé. Elle alimente le propriétaire de configuration explicite, jamais un chargement de module Python. La confiance du package, l'association au descripteur installé exact, la capture atomique des membres et le raccordement au PluginManager restent des préconditions de production. Cette tranche ne modifie ni le format de manifeste actif ni la politique d'activation du gestionnaire.
