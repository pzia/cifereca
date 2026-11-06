# P1 #4761 — facette Format commune Recherche / Recommandations

Le filtre local de la page Recommandations réutilise le `MultiSelect`
« Format » de `QueryControls` (déjà présent dans Recherche), sans nouveau
composant GTK de plugin. Les choix sont recalculés sur les résultats de
recommandation **déjà chargés**. Aucune requête fournisseur, calcul de
recommandations, lecture de droits, téléchargement ou mutation n'est
déclenché par un changement de filtre.

- Sources, langues et formats sont sélectionnables séparément ou ensemble.
  Plusieurs valeurs d'une même facette sont combinées en OU, et les
  différentes facettes en ET.
- Seuls des formats **explicitement attestés** via les champs
  `format` / `media_format` d'un candidat participent au filtre. Le
  vocabulaire initial est EPUB, PDF, MP3, M4A, OGG, OPUS, FLAC, WAV.
  Les offres mixtes ambiguës, formats inconnus et MIME non normalisés ne
  deviennent pas des preuves de variante, d'acquisition ni de DRM.
- « Tous » signifie aucun filtre, y compris les formats inconnus. Dès
  qu'un format est sélectionné, un format inconnu n'est pas supposé égal
  à ce format.
- Les mises à jour de facettes gardent les sélections survivantes, écartent
  celles devenues impossibles, **sans relancer le calcul**.
- Le port facultatif `UiLocalQueryControls` offre
  `selected_formats()` et `set_choices(formats=...)`. Le plugin garde le
  contrat ancien sans filtre Format si un Core précédent ne l'expose pas.

Il reste dans #4761 la convergence complète des autres facettes,
des états, compteurs et de l'UX GTK réel (1024/1366/1600 px). Aucun
droit ni action métier n'est ajouté ici ; voir #4664.
