# Kobo-Adept — simulation des profils (sans DRM)

Ce simulateur ne contacte aucun compte Adobe, aucun profil système, aucune
liseuse et aucun service extérieur. Il ne stocke aucun identifiant réel.
Les cinq profils synthétiques sont générés par
`simulation/plugin_scenarios.py` dans `adept-profiles.json`.

| État | complete | fetch_capable | acsm_fetch_ready | Liseuse |
| --- | --- | --- | --- | --- |
| nouveau | non | non | non | aucune |
| incomplet | oui | non | non | aucune |
| pret | oui | oui | oui | fictive |
| sans-liseuse | oui | oui | oui | aucune |
| expiration | oui | non | non | aucune |

La capacité ACSM est validée par les **trois indicateurs** ; l'association
d'une liseuse n'est pas inférée comme une condition universelle de
préparation. Une fiche créée mais non provisionnée **ne doit jamais** être
présentée comme prête.

```sh
python3 simulation/plugin_scenarios.py --out /tmp/cifereca-ux-adept
python3 simulation/adept/fake_kobo_adept_cli.py \
    --fixture /tmp/cifereca-ux-adept/adept-profiles.json profile list --json
python3 -m unittest discover -s simulation/tests -p 'test_adept_profile_simulation.py' -v
```

La CLI de test fournit uniquement le contrat v8, la liste des profils,
la fiche, les capacités et des résultats **non mutatifs** de création,
association et suppression. Un profil nouvellement créé reste incomplet ;
la suppression répond explicitement `removed=false`. Elle rejette les
autres commandes : **pas d'ACSM, fulfillment, DRM ou prêt**.

Les tests font effectivement exécuter `KoboAdeptRuntime` sur cette CLI via
un sous-processus, vérifient les erreurs ainsi que l'absence de `--force`
et de secrets dans le journal local. Aucune recette GTK ou matérielle
n'est attestée. Les opérations sont strictement réservées à la simulation.
