# Core P0 #4858 W2 — Édition brute exacte avant première acquisition

L'ancienne boucle W2 convertissait `product_id` / `id` en chaîne puis
supprimait les espaces **avant** la première écriture `backup_items`.
Un identifiant numérique, booléen, tronqué, avec espace de bord ou contrôle
pouvait ainsi devenir une autre édition dans la clé 0.7 et produire une
projection `owned` W1 sans correspondance exacte avec la source Kobo.

La garde W2 admet désormais chaque alias fourni uniquement s'il s'agit
d'une chaîne non vide, déjà normalisée, de 1 à 1024 caractères, sans NUL,
CR ni LF. Deux alias présents doivent être **strictement identiques**.
Un alias absent reste compatible avec les anciennes réponses Vault 2.x ;
un alias présent mais invalide ne peut plus être substitué silencieusement.

L'admission précède `_refresh_manifest_record` et toute écriture ou
acquisition. Un rejet marque le manifeste incomplet/non fiable, ne fabrique
aucun droit ni preuve canonique et ne déclenche pas de GET. Tests paramétrés
sur les clés ambiguës, numériques, booléennes, délimitées, contrôles,
sur-longues et deux alias exacts.

Il s'agit d'une protection de compatibilité W2, pas de la migration
fonctionnelle A→B : les issues P0 #4858 et #3196 restent ouvertes.
