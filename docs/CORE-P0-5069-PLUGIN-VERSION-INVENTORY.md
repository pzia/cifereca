# P0 #5069 B3 — Historique des versions plugins (lecture seule)

`inspect_plugin_version_history(packages_root, now=...)` applique, **par package**, l'union des générations de moins de 24 h, des deux dernières générations distinctes et des versions `active`/`previous` épinglées. Elle recense les versions installées dans le magasin `data/plugins/packages` sans instancier `PackageStore`, sans créer de dossier, sans suivre les liens symboliques et sans exécuter un plugin.

L'ownership d'une génération n'est qualifié qu'en présence d'un `cifereca-plugin.json` dont l'identité correspond au répertoire et d'un reçu `.cifereca-install.json` valide (SHA-256 et horodatage timezone-aware). Métadonnées illisibles, absence de pointeur, staging `.replace-`, version symlinkée ou référence `previous` manquante sont signalés comme obstacles. Les identifiants publics sont hachés ; ni le package ID ni les chemins ne sortent du résultat.

**Important :** `safe_to_apply=false` **en toutes circonstances**. L'inventaire couvre les versions mais **pas encore les références aux transactions Core, aux sauvegardes DevUpdater et au staging persistant** ; le blocker `cross_store_references_unqualified` est permanent. Même si `plan.candidates > 0`, il ne constitue **aucune autorisation de suppression**. Le mécanisme de purge universel et l'interface `maintenance inspect/plan/apply` restent hors de ce lot. Tests hors ligne, aucun profil réel ni fichier utilisateur modifié.

Liens : #5069, #5159 (moteur de politique), #5160 (inventaire Core/transactions).

La fonction `inspect_updater_retention_readonly(runtime_root, packages_root, now=...)` fournit une projection agrégée des familles Core, transactions, snapshots et plugins. Elle garde **deux plans distincts** et affiche explicitement les magasins/proofs encore à qualifier : elle ne fabrique aucun graphe de dépendances inter-magasins ni aucun plan d'effacement global.
