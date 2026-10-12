# Diagnostics — état des preuves UX P0 au 12 octobre 2026 (#1011)

**Périmètre :** vraie interface GTK4, Xvfb/X11 et interactions XTEST ; brokers et données fictifs pour les scénarios isolés. La recette ne valide ni connexion à des fournisseurs réels ni installation de ZIP. Contrat et critères complets : [DIAGNOSTICS_P0_RECETTE_1011.md](DIAGNOSTICS_P0_RECETTE_1011.md).

**Décision de recette : PARTIEL — ne pas clore #1011.** D05 demeure bloquant : avec quatre événements de molette XTEST dans le rapport support, la page externe bouge alors que le rapport peut encore défiler. Le produit doit être corrigé par Plugins/CoreUI sous [#1188](https://github.com/pzia/cifereca/issues/1188) ; le rôle UX doit confirmer le correctif sur GTK réel. L'intégration du test [#1184](https://github.com/pzia/cifereca/pull/1184) le 12 octobre ne prouve **pas** que ce défaut produit est résolu.

## État des critères — ne pas extrapoler les PASS

| Critère | État de recette | Preuve et limite |
| --- | --- | --- |
| D01 — source non alimentée | PASS isolé | Vrai widget, état explicite, pas de fausse santé ni de contrôle automatique : `diagnostics-states-1366`. |
| D02 — aucune anomalie observée | PASS ciblé | Texte prudent avec période et couverture ; [#1199](https://github.com/pzia/cifereca/pull/1199) et [#1200](https://github.com/pzia/cifereca/pull/1200). Aucune garantie de santé globale. |
| D03 — broker indisponible | PASS isolé | Erreur contextualisée et reprise seulement par geste explicite ; `diagnostics-states-1366`. |
| D04 — contrôle lent et navigation | PASS simulé | Vraie `MainWindow`, clic XTEST, navigation Bibliothèque pendant worker, retour et résultat : `diagnostics-navigation-slow-1366`. Pas de serveur réel. |
| D05 — rapport long et défilement | **FAIL produit — P0 #1188** | Scroll par ajustement GTK et expansion vérifiés ; molette physique XTEST fait défiler **simultanément** le rapport et la page externe. Ne pas convertir un PASS de harnais en acceptation produit. |
| D06 — 10 000 événements | PARTIEL | 10 000 événements virtuels, pagination par 50 sans injection de 10 000 widgets ; charge réelle interactive non prouvée. |
| D07 — représentation structurée | PASS ciblé | JSON indenté, types `null`/`false`/`0` et Unicode, valeurs sensibles expurgées : `diagnostics-structured-1366`. |
| D08 — détails techniques longs | PARTIEL | Détail GTK présent, expurgé et borné ([#1199](https://github.com/pzia/cifereca/pull/1199), [#1200](https://github.com/pzia/cifereca/pull/1200)) ; accès visuel de bout en bout et cas longs multiples encore à valider. |
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
- [PR UX #1184](https://github.com/pzia/cifereca/pull/1184) : PR CI #38203030045 et E2E Smoke #38203030060 SUCCESS, molette XTEST D05 et sélection Ctrl+C D13. **Ce SUCCESS est celui des contrôles exécutés, pas un PASS D05** : le défaut [#1188](https://github.com/pzia/cifereca/issues/1188) reste ouvert.

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

1. **#1188 / D05 :** sur le rapport agrandi à 1600 px, envoyer des événements de molette XTEST alors que son ajustement interne n'est ni au début ni à la fin ; mesurer l'ajustement interne **et** celui de la page externe. Exiger `interne > avant` et `externe == avant` dans la tolérance du harnais. Capture et `screen.json` sur le SHA corrigé ; rester FAIL autrement.
2. **#1011 :** une fois D05 corrigé, poursuivre D08 long, D09 multi-domaines, D12 corrélation attestée, D14 fraîcheur/consentement et D16 multi-écrans. Ne pas clore sur les seules fixtures locales.
3. **Handoff :** UX reste propriétaire des preuves et de la recette ; Plugins/CoreUI du produit. Réconcilier toute PR documentaire historique avec l'état actuel de `main` avant fusion.
