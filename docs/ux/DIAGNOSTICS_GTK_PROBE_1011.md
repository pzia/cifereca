# Diagnostics — état des preuves UX P0 au 12 octobre 2026 (#1011)

**Périmètre :** vraie interface GTK4, Xvfb/X11 et interactions XTEST ; brokers et données fictifs pour les scénarios isolés. La recette ne valide ni connexion à des fournisseurs réels ni installation de ZIP. Contrat et critères complets : [DIAGNOSTICS_P0_RECETTE_1011.md](DIAGNOSTICS_P0_RECETTE_1011.md).

**Décision de recette : PARTIEL — ne pas clore #1011.** **D05 corrigé et PASS sur GTK/XTEST** après fusion de [#1232](https://github.com/pzia/cifereca/pull/1232) et fermeture de [#1188](https://github.com/pzia/cifereca/issues/1188) ; [E2E #38212934948](https://github.com/pzia/cifereca/actions/runs/38212934948), [artefact #11730172461](https://github.com/pzia/cifereca/actions/runs/38212934948/artifacts/11730172461), scénario `diagnostics-large-1600` sur le merge-test SHA `8bd33453c8f0c8cab07a2b807acad869d976e378`. Sous quatre événements physiques de molette, **rapport 0 → 271,76 px**, **page 1 331 → 1 331 px** ; assertion après stabilisation également PASS, `unmet_criteria=[]`. **D08 PASS ciblé sur GTK/XTEST, [PR UX #1234 fusionnée](https://github.com/pzia/cifereca/pull/1234)** : [E2E #38211972355](https://github.com/pzia/cifereca/actions/runs/38211972355), [artefact #11730345407](https://github.com/pzia/cifereca/actions/runs/38211972355/artifacts/11730345407), `Ctrl+Fin` XTEST avec défilement interne 668/668 px et texte expurgé de bout en bout. D09, D12, D14 et D16 conservent des limites de preuve.


## État des critères — ne pas extrapoler les PASS

| Critère | État de recette | Preuve et limite |
| --- | --- | --- |
| D01 — source non alimentée | PASS isolé | Vrai widget, état explicite, pas de fausse santé ni de contrôle automatique : `diagnostics-states-1366`. |
| D02 — aucune anomalie observée | PASS ciblé | Texte prudent avec période et couverture ; [#1199](https://github.com/pzia/cifereca/pull/1199) et [#1200](https://github.com/pzia/cifereca/pull/1200). Aucune garantie de santé globale. |
| D03 — broker indisponible | PASS isolé | Erreur contextualisée et reprise seulement par geste explicite ; `diagnostics-states-1366`. |
| D04 — contrôle lent et navigation | PASS simulé | Vraie `MainWindow`, clic XTEST, navigation Bibliothèque pendant worker, retour et résultat : `diagnostics-navigation-slow-1366`. Pas de serveur réel. |
| D05 — rapport long et défilement | **PASS GTK/XTEST — #1232 fusionnée** | Rapport intérieur 0→271,76 px ; page externe stable à 1 331 px, y compris contrôle différé de l'ajustement ; vrai geste physique et PNG `09b-report-physical-wheel-1600.png` (E2E #38212934948, artefact #11730172461). Bornes produit couvertes aussi par tests unitaires, pas par une campagne matérielle multi-périphérique. |
| D06 — 10 000 événements | PARTIEL | 10 000 événements virtuels, pagination par 50 sans injection de 10 000 widgets ; charge réelle interactive non prouvée. |
| D07 — représentation structurée | PASS ciblé | JSON indenté, types `null`/`false`/`0` et Unicode, valeurs sensibles expurgées : `diagnostics-structured-1366`. |
| D08 — détails techniques longs | **PASS GTK/XTEST ciblé — PR #1234 fusionnée** | Vrai détail GTK de 40 étapes anonymes, expurgation, focus `TextView` et `Ctrl+Fin` XTEST atteignant 668/668 px ; capture `04c-kobo-detail-bottom-xtest.png`, artefact #11730345407. Recette revalidée après réconciliation avec `main` : [E2E #38214724262](https://github.com/pzia/cifereca/actions/runs/38214724262) SUCCESS, [PR CI #38214724314](https://github.com/pzia/cifereca/actions/runs/38214724314) SUCCESS. Cas longs multi-sources à compléter. |
| D09 — provenance multi-domaines | PARTIEL | Filtres source/module/période sur événements synthétiques ; couverture métier réelle Core, Paris, Kobo, ADEPT, plugins non attestée globalement. |
| D10 — erreur ADEPT code 10 | PASS sur fixture | Absence de traces signalée sans inventer de diagnostic Adobe. |
| D11 — retour Paris | PASS sur fixture | « Retour demandé » non transformé en « prêt disparu ». |
| D12 — corrélation inter-identités | NON VALIDÉ | Deux profils fictifs distingués ; preuve de corrélation métier par broker encore à établir. |
| D13 — copie et export | PASS ciblé | Sélection + Ctrl+C XTEST et copie intégrale ([#1193](https://github.com/pzia/cifereca/pull/1193)), export vers fichier via dialogue GTK ([#1199](https://github.com/pzia/cifereca/pull/1199)) ; pas de données réelles. |
| D14 — rapport / vérification / IA | PARTIEL | Sections distinctes, IA désactivée sans consentement ; fraîcheur comparative du rapport, de l'analyse et provenance réelle encore à qualifier. |
| D15 — clavier et fermeture | PASS ciblé | Tab, Shift+Tab, activation et Escape ciblés, réduction du rapport via [#1197](https://github.com/pzia/cifereca/pull/1197) ; pas d'attestation d'accessibilité exhaustive. |
| D16 — dimensions | PARTIEL | 1366 × 900, 1600 × 1000 et redimensionnement GTK ; plusieurs écrans et tous contrôles essentiels non recettés exhaustivement. |

Les statuts « PASS ciblé » et « PASS isolé » ne valent jamais validation intégrale de l'application.

## Preuves E2E déjà obtenues

- [E2E Smoke #38132474643](https://github.com/pzia/cifereca/actions/runs/38132474643), [artefact #11699662411](https://github.com/pzia/cifereca/actions/runs/38132474643/artifacts/11699662411) : widget et navigation MainWindow GTK à 1366 px, 6 checks et 4 PNG par scénario sur la révision recettée.
- [E2E Smoke #38137558187](https://github.com/pzia/cifereca/actions/runs/38137558187), [artefact #11701632376](https://github.com/pzia/cifereca/actions/runs/38137558187/artifacts/11701632376) : D01–D04 sur services simulés, navigation pendant contrôle et captures après fin du fondu Gtk.Stack.
- [E2E Smoke #38141639752](https://github.com/pzia/cifereca/actions/runs/38141639752), [artefact #11702993418](https://github.com/pzia/cifereca/actions/runs/38141639752/artifacts/11702993418) : volume virtuel 10 000 événements et filtres.
- [PR UX #1200](https://github.com/pzia/cifereca/pull/1200) : PR CI #38172683145 et E2E Smoke #38172683182 SUCCESS, assertions D02/D08 sur la révision de cette PR.
- [PR UX #1184](https://github.com/pzia/cifereca/pull/1184) : PR CI #38203030045 et E2E Smoke #38203030060 SUCCESS, molette XTEST D05 et sélection Ctrl+C D13. **Historique seulement :** ce SUCCESS antérieur ne prouvait pas D05 ; le défaut #1188 a été corrigé ultérieurement.
- **D05 résolu après correction produit :** [PR #1232 fusionnée](https://github.com/pzia/cifereca/pull/1232), [E2E #38212934948](https://github.com/pzia/cifereca/actions/runs/38212934948) et [artefact #11730172461](https://github.com/pzia/cifereca/actions/runs/38212934948/artifacts/11730172461), `diagnostics-large-1600/screen.json` : `status=PASS`, `D05_xtest_wheel_keeps_outer_page_fixed=true`, `wheel_outer_before=wheel_outer_after=1331`, `wheel_inner_after=271.76`, `unmet_criteria=[]`. Le SHA dans `screen.json` correspond au merge-test CI, non directement au HEAD de la PR.
- **D08 recette ciblée historique :** [PR #1234](https://github.com/pzia/cifereca/pull/1234) HEAD `843253491572a490c9318804ebd670b10509727a`, [PR CI #38211972357](https://github.com/pzia/cifereca/actions/runs/38211972357) + [E2E #38211972355](https://github.com/pzia/cifereca/actions/runs/38211972355) SUCCESS ; [artefact #11730345407](https://github.com/pzia/cifereca/actions/runs/38211972355/artifacts/11730345407), `04c-kobo-detail-bottom-xtest.png`, maintien du secret expurgé et absence de requête broker additionnelle. **#1234 désormais fusionnée :** commit [`74c816d5`](https://github.com/pzia/cifereca/commit/74c816d5ae83e8cecc1df69e9430cc0c64fcc37d) après réconciliation ; [E2E #38214724262](https://github.com/pzia/cifereca/actions/runs/38214724262) et [PR CI #38214724314](https://github.com/pzia/cifereca/actions/runs/38214724314) SUCCESS.

Vérifier toujours le `git_sha`, le `status`, les `checks`, les éventuels `unmet_criteria` et les empreintes des PNG dans chaque `screen.json`. Une mesure GTK programmatique de l'ajustement ne remplace **jamais** une molette XTEST.

## Exécution ciblée, sans ajouter de workflow CI/CD

Depuis le dépôt, avec l'environnement GTK4/Xvfb/XTEST décrit dans [E2E-UX.md](../../environnements/E2E-UX.md) :

```bash
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-1366
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-states-1366
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-navigation-slow-1366
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-structured-1366
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-volume-1366
dbus-run-session -- /usr/bin/python3 e2e/run.py --scenario diagnostics-large-1600
```

Le workflow `E2E Smoke` **existant** suffit pour la vérification distante. Pas de relance manuelle systématique, d'appel réseau/LLM implicite, de secret ou de fixture personnelle. Les archives GTK autonomes peuvent servir à vérifier le banc d'essai, mais ne remplacent pas une preuve de l'interface Cifereca.

## Prochain gate UX P0

1. **D05 — surveillance de régression :** garder le test XTEST bloquant sur `diagnostics-large-1600`, sans déclasser le FAIL si le panneau et la page bougent à nouveau. #1188 est **clos** après preuve réelle ; ne pas rouvrir sur une affirmation historique.
2. **D08 — non-régression :** [#1234](https://github.com/pzia/cifereca/pull/1234) est fusionnée et les contrôles GTK sont verts après réconciliation avec `main` ; maintenir la recette Ctrl+Fin XTEST, et élargir ensuite aux détails longs multi-sources.
3. **#1011 — reste P0 ouvert :** D09 provenance métier multi-sources, D12 corrélation de prêt entre identités attestée côté broker, D14 temporalité IA/rapport/contrôle actif, D16 tailles et contrôles essentiels sur vrais parcours MainWindow ; couvrir les parcours sans inventer de résultat.
4. **Handoff :** UX pilote recettes et captures ; Plugins/CoreUI développe les éventuelles corrections produit ; CI/CD vérifie gates/merges. Aucun nouveau workflow.
