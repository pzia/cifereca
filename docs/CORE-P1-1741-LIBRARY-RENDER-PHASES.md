# Core P1 #1741 — phases de rendu Bibliothèque/Récents (M2)

## Pourquoi et ce qui est effectivement mesuré

Le code produit `gui/library_render_controller.py` affiche déjà la durée
**synchrone totale** `render_library` / `render_recent` via
`UiPerfMonitor.record`. Ce lot ajoute une observation plus fine **uniquement
si** `ui_perf.enable_detailed_capture(True)` a été explicitement activé :

| Série `performance_report().stats` | Début → fin, horloge `perf_counter` | Limite |
| --- | --- | --- |
| `library.row_projection` | Entrée contrôleur → projection des lignes filtrées | Inclut création `BookListItem`, pas seulement Python pur |
| `library.facets` | Appel `_attach_row_facets` | Peut provoquer des callbacks synchrones |
| `library.model_update` | `library_page.show_rows` | Inclut `Gio.ListStore`, signaux/callbacks synchrones, **pas** la frame |
| `library.selection` | Restauration de sélection, éventuel détail vide | Dépend du contexte/sélection ; durée non toujours comparable |
| `recent.row_projection`, `recent.facets`, `recent.model_update` | Même découpage adapté à Récents | Pas d'étape de sélection dans ce contrôleur |

Ces durées sont **observées réellement** au moment de l'opération et non
extrapolées du benchmark `#1129`. Les points observés ne disent pas encore
si une frame a été compositée ou présentée à l'utilisateur. Ne pas les nommer
« latence à l'écran ». Les durées de phases sont disjointes par cette sonde,
mais les callbacks des sous-phases peuvent être inclus dans une autre étape ;
l'instrumentation et l'ordonnanceur ont leurs propres coûts. Les totaux
historiques demeurent indépendants ; **ne pas sommer les chronomètres pour
prouver la causalité ou un budget**.

## Politique de coût et de confidentialité

- **Capture inactive par défaut :** les deux horodatages historiques et leur
  rendu restent inchangés ; aucune allocation de séries de phase.
- **Activation explicite :** seuls les libellés techniques fermés, la durée et
  le mode `measurement_origin` du moniteur sont enregistrés. Aucune ligne,
  identité, titre, URL, provenance privée ou miniature n'est stockée.
- **Capacité/épuration :** réutilisation du budget maximal de
  `UiPerfMonitor.performance_report` et de son journal de détails opt-in.
- **Défaillance du moniteur :** ne bloque jamais la projection ni le rendu ;
  le chronométrage historique reste exécuté.
- **E2E / terrain :** injecter `measurement_origin="simulated"` aux essais
  synthétiques ; l'origine `observed` ne constitue pas à elle seule une
  preuve que la mesure vient d'une installation utilisateur.

## Recette / suite

Les tests headless comparent la séquence d'appels métier et le nombre de
lectures d'horloge sans capture, puis les phases exactes avec une horloge
injectée. Les tests E2E GTK d'intégration vérifient l'absence de régression
fonctionnelle, **pas** la latence jusqu'à la prochaine frame.

Restent pour fermer #1741 : capture réellement GTK de fin de frame, analyse
de l'impact de sonde et du GC, décomposition GObject/GTK et CPU/RSS sur
installation réelle, ingestion dans le plugin Diagnostics, comparaison avec
des distributions de taille suffisante et arbitrage d'un budget explicite.
