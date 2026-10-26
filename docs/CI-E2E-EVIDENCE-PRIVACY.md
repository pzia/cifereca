# Publication des résultats E2E dans GitHub Actions (#1143)

> **Doctrine de confidentialité actuelle (26/10/2026) : seul le risque d'exposition involontaire de credentials d'authentification** (mots de passe, tokens, cookies de session, secrets API, clés privées, URL signées d'accès) justifie un travail spécifique. Des captures, erreurs, chemins, comptes ou traces **sans credential** ne demandent pas d'audit, d'expurgation, de ticket ou de gate de confidentialité supplémentaire. La politique de publication bornée décrite ci-dessous est **l'état technique du pipeline**, pas une obligation générale de masquer les autres données. Ne pas modifier les workflows sans ticket fonctionnel explicite ; continuer à éviter toute publication automatique de credentials.


Le workflow `.github/workflows/e2e-smoke.yml` produit toujours les captures PNG,
logs stdout/stderr, traces JSONL et rapports de probes dans le runner isolé.
**Ces fichiers bruts ne sont plus téléchargés ni recopiés dans le résumé Actions.**

Avant publication, `scripts/ci-export-e2e-summary.py` lit uniquement le
résumé agrégé local sous une limite de 1 Mio et 128 scénarios. Il vérifie les
identifiants contre les scénarios définis dans le dépôt, les codes de sortie
entiers et les ratios bornés. Il crée un répertoire de publication neuf dans
`RUNNER_TEMP`. Aucune chaîne arbitraire provenant des probes n'est publiée :
seuls `scenario`, `status`, `returncode` et un ratio numérique optionnel
survivent. Toute erreur refuse l'export, **y compris lors d'un échec E2E**.

Les pixels PNG ne sont pas suffisamment inspectables automatiquement pour
attester l'absence de données privées : **pas de PNG, logs, traces, rapports
détaillés ou archives XDG dans les artefacts publics**. Les preuves visuelles
utiles restent consultables seulement à l'intérieur du job pendant son
exécution. L'artefact `e2e-core-summary` est conservé deux jours.

Ceci protège exclusivement **les nouvelles exécutions E2E Smoke**.
Les anciens artefacts ne sont pas supprimés sans accord explicite. Les autres
workflows qui publient encore des bundles (GTK manuel, preview, qualifications
live) doivent être audités séparément. Les fixtures E2E doivent rester
synthétiques ; l'export borné n'autorise aucune capture de compte réel.
