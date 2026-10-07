# Tests E2E GTK

Le runner canonique est :

```bash
python e2e/run.py
```

Il lance la vraie application CIFERECA sous Xvfb, avec HOME/XDG isolés, puis attend
l'apparition d'une surface GTK non vide. L'attente est conditionnelle et bornée ;
les scénarios ne doivent pas utiliser de `sleep` arbitraire pour synchroniser l'IHM.

## Ciblage

```bash
python e2e/run.py --scenario startup
python e2e/run.py --plugin core
```

Chaque scénario déclare son domaine/plugin. Les futurs scénarios provider doivent
utiliser l'identifiant du plugin comme filtre.

## Artefacts

Les captures et rapports JSON sont écrits dans `e2e-artifacts/` par défaut.
Ce dossier est généré et ne doit pas être versionné.

## Scénario de référence

`startup` valide un parcours minimal mais réel : lancement de CIFERECA avec le
Core seul, maintien du processus et apparition effective d'une surface GTK rendue.

## Dépendances Linux

Le runner exige `xvfb-run`, GTK4/libadwaita, PyGObject, Pillow, `libXtst` et
les dépendances Python du Core. La CI installe ces dépendances explicitement.

Les scénarios doivent rester déterministes : aucun service réseau réel, aucun
compte utilisateur réel et aucun état réutilisé entre deux exécutions.
