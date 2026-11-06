# P1 #4552 — clé de révision des seules preuves descriptives

`descriptive_lineage_key(selection, admitted)` retourne une empreinte opaque `notice-c-desc-v1:<sha256>` calculée **sans I/O** à partir du type canonique, de la portée, du plugin+version explicitement choisi, du `schema_id`, de l'édition cible, et **de toutes les preuves descriptives admissibles** exactes ou liées fortement (source/édition/ID de preuve/révision/valeur brute/preuve de lien/indicateur hérité).

Le format de sérialisation est un tableau JSON structuré et stable : aucune concaténation ambiguë de segments, aucun ordre de réponse fournisseur ne détermine la clé. Les doublons identiques sont déjà dédupliqués par `admit_descriptive_facts` ; les doublons de preuves dans une entrée falsifiée sont refusés ici. Changer une preuve descriptive, une relation forte attestée, la version du consolidateur, sa portée ou le schéma produit une autre clé (ou un rejet de contexte).

**Limites strictes :** cette clé **n'autorise pas** la réutilisation de permissions, de prêts, de sessions, de quotas, d'actions, d'offres ou de données personnelles. Un cache futur doit conserver des objets descriptifs Core-only, vérifier la validité de son plugin/ABI, le contexte utilisateur quand applicable, les générations de source, et appliquer une politique de stockage/invalidation. Le hash seul n'est ni une preuve de sécurité ni un droit. Les éventuelles informations de source ou de profil non admises ne sont jamais incorporées.

Cette tranche n'installe aucun cache, n'exécute aucun plugin et ne publie pas de notice C. Elle prépare une invalidation déterministe du travail de consolidation *descriptif* ; le registre de types, la persistance, le worker asynchrone et la recette restent dans #4552.
