# Recette UX P0 — Diagnostics (#1011)

Statut : **recette E2E partielle PASS au 11/10/2026**, compléments D01–D16 à exécuter avant clôture.  
Référence : `main` pour le contrat, [PR #1146](https://github.com/pzia/cifereca/pull/1146) pour les nouveaux scénarios, [guide environnement E2E](../../environnements/E2E-UX.md). Les ZIP GTK historiques ne sont pas des fixtures.  
Propriétaire : UX (spécification et validation) ; Plugins/CoreUI (implémentation). Pas de modification CI/CD hors passage dans le workflow E2E existant.

## Validation GTK effective, avec limites

Le workflow existant [E2E Smoke #38132474643](https://github.com/pzia/cifereca/actions/runs/38132474643)
sur **Ubuntu 24.04 / GTK4 / Xvfb** est **SUCCESS**. Rapports et 8 PNG
dans [l'artefact #11699662411](https://github.com/pzia/cifereca/actions/runs/38132474643/artifacts/11699662411),
avec `status=PASS`, commit de test `8e7c4fa9ca2de4a11a72826d5070bb3f6328de50`
et SHA-256 concordants. Voir [la fiche de preuves GTK](DIAGNOSTICS_GTK_PROBE_1011.md).

| Sous-recette | Preuve acquise | Limite |
| --- | --- | --- |
| Widget `diagnostics-1366` | 6 checks PASS, 4 PNG ; lecture rapport, agrandissement, JSON expurgé, copie clavier XTEST/presse-papiers, pagination 50→15, aucune activation IA/check implicite | widget isolé et brokers simulés ; pas d'export fichier, scroll extrême ni batterie clavier complète |
| Navigation `diagnostics-navigation-1366` | 6 checks PASS, 4 PNG ; vraie `MainWindow`, clics XTEST Bibliothèque → groupe Configuration → Diagnostics → Bibliothèque ; route, titre et contenu visibles | contribution réelle montée avec doubles de gestionnaire de plugins et brokers ; pas d'installation ZIP ni de profil utilisateur réel |
| Grand écran `diagnostics-large-1600` | **10 checks PASS, 9 PNG** ; rapport long 250 lignes, Space/Tab/Shift+Tab XTEST, scroll rapport 3724/3724 px, page externe stable à 1286 px, redimensionnement GTK 1560→1240→1560 px, sans débordement horizontal | widget isolé, molette non testée ; **Escape et export fichier observés non satisfaits** ([#1158](https://github.com/pzia/cifereca/issues/1158)) |
| États `diagnostics-states-1366` | 6 checks PASS, 6 PNG ; sources non alimentées, source disponible sans événement, broker indisponible, retry explicite et contrôle lent réactif | D02 : synthèse zéro anomalie datée absente ([#1161](https://github.com/pzia/cifereca/issues/1161)) ; widget isolé, brokers simulés |
| Navigation lente `diagnostics-navigation-slow-1366` | 10 checks PASS, 7 PNG ; vraie MainWindow, 7 gestes XTEST, lancement volontaire d'un contrôle puis navigation Bibliothèque et retour avec résultat visible | test asynchrone simulé, sans serveur distant ni installation ZIP |

**Avancement D01–D16 :** preuves partielles pour **D05** (rapport
multipage et scroll interne jusqu'au bas, mais pas focus de retour complet),
**D07** (un JSON expurgé, pas tous les types limites), **D13**
(copie presse-papiers, mais pas sélection `Ctrl+C` ni export disque),
**D14** (aucune IA ou vérification active implicite, pas tous les statuts),
**D15** (Space/Tab/Shift+Tab physiques au niveau du rapport mais
`Escape` n'abandonne pas l'agrandissement : **défaut produit #1158**)
et **D16** (vrai redimensionnement 1560×870 → 1240×800 → 1560×870
sans débordement global, mais autres géométries et interactions non exercées).
**D01** et **D03** disposent maintenant de preuves GTK sur des brokers
simulés. **D02** reste **partiel**, car l'absence d'erreur observée sur
une période n'est pas explicitement affichée ([#1161](https://github.com/pzia/cifereca/issues/1161)).
**D04** couvre la réactivité et le retour au résultat via une vraie
MainWindow avec tâches simulées et navigation XTEST, pas un runtime externe réel.
Les **D06 et D08–D12**, ainsi que les parties manquantes des autres
critères, restent à exercer. Ne pas cocher l'ensemble de la matrice,
ni clôturer #1011 sur ces seuls scénarios.

**Run le plus récent :** [E2E Smoke #38135244581](https://github.com/pzia/cifereca/actions/runs/38135244581)
**SUCCESS**, [artefact #11701076489](https://github.com/pzia/cifereca/actions/runs/38135244581/artifacts/11701076489) :
trois rapports PASS, 14 PNG avec SHA-256 vérifiés. CI ordinaire du
même head **FAIL** sur `XPASS(strict)` d'un test explorateur
([#1013](https://github.com/pzia/cifereca/issues/1013)), relevant de
son propriétaire ; un PASS GTK ne l'autorise pas à être ignoré.

**Intégration :** la PR [#1146](https://github.com/pzia/cifereca/pull/1146)
est toujours soumise au CI ordinaire ; l'inventaire de plugins non classés
est suivi dans [#1148](https://github.com/pzia/cifereca/issues/1148).
Le PASS E2E **ne** permet **pas** de fusionner une PR dont le CI requis
est rouge.

## Nouvelle campagne D01–D04 du 11 octobre 2026

[E2E Smoke #38137261745](https://github.com/pzia/cifereca/actions/runs/38137261745)
**SUCCESS global**. [Artefact #11701626785](https://github.com/pzia/cifereca/actions/runs/38137261745/artifacts/11701626785).
`diagnostics-states-1366` : 6 checks PASS, 6 captures ; 
`diagnostics-navigation-slow-1366` : 10 checks PASS, 7 captures.
Les **13 SHA-256 sont conformes** et les trois scénarios Diagnostics
antérieurs restent PASS. Aucun accès réseau/LLM implicite n'est exercé.

Le CI général sur ce même HEAD est encore **FAIL** en raison du
`XPASS(strict)` du test #1013
([run #38137261748](https://github.com/pzia/cifereca/actions/runs/38137261748),
1862 tests réussis). **Aucun contournement de ce gate.**

## Dernier verdict GTK — 11 octobre 2026

Le [run E2E #38136016404](https://github.com/pzia/cifereca/actions/runs/38136016404)
est **SUCCESS**, [artefact #11700768842](https://github.com/pzia/cifereca/actions/runs/38136016404/artifacts/11700768842) :
`diagnostics-large-1600` **10 checks PASS**, 9 captures avec SHA-256,
et `diagnostics-1366` ainsi que `diagnostics-navigation-1366`
toujours PASS (17 PNG Diagnostics au total).

Le rapport grand écran **distingue** ses validations passantes et
`unmet_criteria` : **D13 export du rapport expurgé sur disque absent**,
**D15 Escape n'abandonne pas l'agrandissement**.
Ces deux écarts sont décrits dans le ticket Plugins/CoreUI
[#1158](https://github.com/pzia/cifereca/issues/1158).
Ils **interdisent de considérer D13/D15 clos**, malgré le succès du
harnais. La PR #1146 reste soumise aux gates CI ordinaires.

## Architecture d'information

1. **Synthèse** : date de dernière collecte, statut de disponibilité et nombre d'anomalies par gravité ; états distincts « pas d'erreur observée », « non alimenté », « indisponible », « actualisation nécessaire », « contrôle en cours ». Ne jamais qualifier un module de sain faute d'échantillons.
2. **Sources** : composant, dernière observation, couverture, volume d'événements, disponibilité. Un filtre « Toutes les sources » n'implique **aucune collecte**.
3. **Journaux** : exploration locale en lecture seule, filtres module / niveau / période / texte, chronologie et référence de provenance. Vue vide contextualisée, pas de tableau vide silencieux.
4. **Détail** : champs synthétiques visibles, contenu long dans un volet avec scroll propre, vue lisible/JSON indenté/brut expurgé si applicables (#1012).
5. **Rapport support** : panneau dédié, distinct de la vérification active et du résultat IA ; lecture, sélection, copie, export expurgé. Analyse IA (#1009) identifiable comme inférence et jamais confondue avec les données attestées.

## Comportements attendus

- Le panneau long a une hauteur utilisable, ne pousse pas tous les contrôles hors de la fenêtre, accepte le scroll interne et offre une commande « Agrandir / Réduire » ou un séparateur ajustable ; le scroll de page et celui du panneau restent prévisibles.
- Les textes sont sélectionnables au clavier et à la souris ; `Ctrl+C` copie seulement la sélection ; une action « Copier le bloc » copie le contenu expurgé du panneau courant ; « Exporter » requiert un geste explicite et signale le périmètre.
- Les valeurs longues (chemins, erreurs, UUID pseudonymisés) peuvent être tronquées **uniquement** en présentation ; leur contenu complet expurgé reste accessible via détail/copier. Pas de libellé reposant uniquement sur couleur ou icône.
- `Tab` parcourt titre/actualiser/filtres/liste/détail/actions dans l'ordre visuel ; `Shift+Tab` revient ; `Esc` ferme l'agrandissement sans perdre la sélection courante ; focus visible.
- Aucune synchronisation, analyse IA ni requête réseau n'est déclenchée par ouverture, navigation, sélection, expansion ou export. Les vérifications actives exigent un clic explicite, indiquent leur progression et ne doivent pas bloquer GTK.
- Les données passées au rendu, à la copie et à l'export sont déjà expurgées par le broker Core ; l'UI ne doit jamais contourner les droits de lecture bornée (#1010).
- Résolution de référence **1366 px et supérieures prises en charge par le projet** ; ne pas réintroduire les scénarios à très basse résolution supprimés.

## Matrice de recette sur GTK réelle

| ID | Données / état | Parcours | Résultat attendu |
| --- | --- | --- | --- |
| D01 | aucune source alimentée | ouvrir Diagnostics | « Non alimenté » visible, pas de fausse santé verte ni de demande réseau |
| D02 | source disponible, 0 anomalie | consulter synthèse | « Pas d'erreur observée » avec fenêtre temporelle, non « données indisponibles » |
| D03 | broker indisponible | ouvrir / réessayer | erreur contextualisée, action volontaire, fenêtre toujours réactive |
| D04 | contrôle actif lent | déclencher puis naviguer | progression et annulation/retour possible, pas de gel GTK |
| D05 | rapport support multi-pages | agrandir, scroller, réduire | scroll interne, fin atteignable, pas de perte de focus |
| D06 | 10 000 événements simulés expurgés | filtrer, faire défiler | chargement borné, navigation réactive, ordre stable |
| D07 | JSON profond, Unicode, `null`, `false`, `0`, vide | changer de représentation | distinctions conservées, indentation, pas de `str(dict)` illisible |
| D08 | erreur et chemin très longs | liste puis détail | mise en page intacte, détail complet expurgé consultable |
| D09 | sources Core, Paris, Kobo, ADEPT, plugins | filtrer module/période | nombre de résultats et provenance cohérents |
| D10 | erreur ADEPT code 10 + traces libgourou manquantes | consulter événements | absence de preuve explicite, aucune cause Adobe inventée |
| D11 | retour Paris accepté sans confirmation | lire chronologie | « retour demandé » distinct de « prêt disparu » |
| D12 | deux identités, prêt commun | inspecter corrélation | références pseudonymisées, sans confusion des profils |
| D13 | bloc expurgé | sélectionner, `Ctrl+C`, copier/exporter | contenu sélectionné et fichier expurgés, provenance indiquée |
| D14 | rapport + active_checks + IA indisponible | parcourir sections | 3 statuts clairement distincts ; aucun envoi LLM |
| D15 | clavier uniquement | Tab, Shift+Tab, Entrée, Esc | focus visible, commande des filtres et retour à l'élément initial |
| D16 | fenêtre 1366 et grand affichage | redimensionner | aucun contrôle essentiel hors champ ; pas de scroll horizontal global |

## Preuves requises pour clôture

- Captures de **la vraie application GTK** dans les cas D01, D05, D08 et D16 ; indiquer taille de fenêtre et version/commit.
- Journal de parcours clavier et actions copie/export ; comparer le fichier exporté au texte affiché et confirmer l'expurgation.
- Relevé d'absence de requête réseau implicite à l'ouverture ; mesures qualitatives de latence/absence de gel.
- Cas négatifs obtenus par fixtures anonymisées ; jamais reproduits avec secrets réels.
- Compte rendu PASS/FAIL par scénario, lien vers commits Plugins/CoreUI corrigés et limitations restantes.
- Exécuter dans l'infrastructure E2E existante : **aucun nouveau workflow GitHub Actions**.

## Dépendances / ordre

`#1010` broker et provenance sûrs → `#1012` formatage → `#1013` exploration → `#1011` disposition et recette GTK → `#1009` IA opt-in.  
Ne pas déclarer #1011 clos tant que les critères D01–D16 n'ont pas été exercés sur GTK réelle.
