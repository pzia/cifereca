# P1 #2665 — Versions Core/plugins dans « État du système »

Les deux surfaces, l'onglet Diagnostics et la page de secours Core, présentent
en tête **Core Cifereca · vX**, puis **tous les plugins inventoriés**, y compris
les plugins désactivés ou bloqués. Le filtre « À examiner » ne masque plus
l'inventaire des versions dans le secours ; il continue seulement de filtrer
les détails des anomalies.

Le bouton **Copier les versions**, indépendant des actions « Copier le
diagnostic support » et « Exporter », utilise **uniquement le dernier
instantané affiché**, fourni par le broker public :
`diagnostics.system_status()` pour le Core et
`diagnostics.inventory()` pour les plugins.

Chaque ligne indique le nom, l'ID de manifeste, la **version de
l'extension**, la version du **paquet actif**, la version **attendue**
lorsque le Core la connaît, l'état et l'activation. Une divergence
actif/attendu est annoncée explicitement. Les champs absents/malformés
sont indiqués comme inconnus ; l'outil ne suppose jamais que la version
du dépôt est celle de l'installation. L'inventaire est trié de manière
stable, anomalies en tête, et borné à 300 entrées avec mention de la
limite atteinte. Le texte copié correspond à cette même projection.

Les champs du rapport support, les sources, chemins, identifiants
d'identité/compte, URL et secrets ne sont **jamais** lus par le
projecteur. Les IDs/versions suivent une grammaire fermée et les noms
sont bornés/filtrés ; la copie n'ouvre pas de nouvelle lecture broker,
n'installe rien et ne contacte aucun fournisseur. Un rafraîchissement
remplace l'instantané ; en cas d'échec de lecture, le bouton reste
désactivé ou refuse la copie. L'absence de presse-papiers ou l'échec
de `set_content` ne déclenchent aucun faux message « Versions copiées ».

## Recette

- Core et Browser Recorder avec version extension 0.1.3,
  paquet actif éventuellement différent, paquet attendu éventuellement
  différent : vérifier que les trois valeurs restent distinctes.
- Plugins sains, bloqués et désactivés : tous présents sans changer
  le filtre « À examiner ».
- Modifier l'inventaire simulé puis rafraîchir : la liste et la copie
  reflètent la même nouvelle génération.
- Injecter des valeurs sensibles dans `support_report`, `path`,
  `account_id`, `token` et des champs inconnus : aucune ne doit
  apparaître dans la liste ou la copie.
- Contrôler en GTK à 1280/1366 px le clavier, les labels et le
  message d'échec du presse-papiers.

Aucune preuve de correction de l'installation Browser Recorder ni de
résolution de #2628/#2663 ne découle de ce changement d'affichage.
