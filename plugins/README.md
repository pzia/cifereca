# Cifereca plugins

Les sources des plugins sont versionnées directement sous `plugins/<plugin>/`.

Chaque répertoire contient son `cifereca-plugin.json`, son code Python,
ses tests et, lorsqu'ils existent, ses documents propres.

Les ZIP/tar de livraison et les dépendances binaires tierces sont générés
au build/release et ne constituent pas la source de vérité Git.

## Tests

Les suites portent toutes le package Python `tests`. Dans ce monorepo,
exécuter les tests plugin par plugin :

```bash
for p in plugins/*; do
  if [ -f "$p/cifereca-plugin.json" ] && [ -d "$p/tests" ]; then
    (cd "$p" && python -m pytest -q)
  fi
done
```

## Catalogues publics

Cette branche inclut les évolutions validées de Baen, Ebooks libres et
gratuits, Gallica et Project Gutenberg depuis
`plugins/public-catalogs-043`. BnF reste compatible avec les surfaces
publiques Core disponibles.
