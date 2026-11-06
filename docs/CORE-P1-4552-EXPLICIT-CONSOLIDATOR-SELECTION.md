# P1 #4552 — sélection de consolidateur explicitement configurée

La fonction pure `select_consolidator` n'est **ni un moteur de plugin ni une autorisation d'exécution**. Elle vérifie un registre déjà contrôlé par le Core et choisit un `plugin_id + version` **exactement configuré** pour un couple `canonical_type + scope`, avec un `schema_id` de sortie déclaré.

Règles de sécurité :
- Aucun candidat choisi implicitement, **même si un seul est installé**.
- Pas de fallback au plus récent, à un autre profil/portée, à un autre type ni à une autre version. Sélection manquante ou plugin absent → `None` (inconnu).
- Deux déclarations répondant au même plugin/version/type/portée → échec, y compris si identiques : la configuration ne suffit plus à prouver l'unicité.
- Registre borné (128 déclarations), identifiants contrôlés, aucune valeur fournisseur brute ni URL signée dans la sélection.
- Types canoniques libres : pas de logique `age`, `genre`, `series` codée dans Core.

**Préconditions d'intégration** : le Core doit authentifier les plugins installés, leurs manifests et la portée, charger un unique exécuteur sous isolation, vérifier le schéma de sortie et journaliser un résultat versionné ; ce module ne remplace aucune de ces garanties. Le choix explicite devra être persisté par portée et invalidé au changement de version, de plugin ou de type. Il n'effectue ni SQLite, réseau, mutation de source ni consolidation.

La tranche #4724 distingue déjà les faits exacts des hérités ; cette tranche complète le contrat d'admission sans brancher prématurément le worker asynchrone.
