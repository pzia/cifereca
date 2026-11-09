# P0 #5069 B9 — Aperçu de maintenance sans purge

`cifereca-update --retention-plan --json` réutilise **exactement** l'inventaire de `--retention-inspect`, sans créer de répertoire, ouvrir un plugin, contacter GitHub ou écrire un état. Le nouveau mode est exclusif de l'installation, du rollback, de `--recovery-reconcile`, de `--acknowledge` et des autres modes de diagnostic.

La sortie `cifereca.updater.maintenance-plan/1` présente, pour chaque magasin connu, `total`, `candidates`, `retained`, `known`, `apply_allowed=false`, les magasins encore non qualifiés et des codes de blocage bornés. Les identités des packages, chemins utilisateur, noms de livres et credentials ne sont pas projetés. `plan_sha256` est l'empreinte canonique de l'inventaire complet, y compris les références, les épingles et les blocages ; **ce n'est pas un jeton d'autorisation**, et aucun point d'entrée ne peut l'utiliser pour supprimer des objets.

**Interprétation** : `candidates > 0` est une information théorique du moteur de rétention 24 h + minimum 2, jamais une autorisation de GC. Tant que le graphe inter-magasins, les preuves d'appartenance à l'Updater, les bornes de concurrence et la revalidation TOCTOU ne sont pas complets, l'ensemble reste `safe_to_apply=false` / `apply_supported=false`.

Travail restant avant clôture #5069 : commande d'application supervisée sous verrou partagé, plan de suppression atomique avec quarantine/journal durable, reprise après crash, contrôle anti-symlink et anti-hardlink, gestion des générations référencées, recettes 7+1 par magasin et démonstration de non-suppression de données utilisateur.
