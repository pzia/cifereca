# Recette UX P0 — Diagnostics (#1011)

Statut : contrat UX, à exécuter sur l'application GTK réelle ; **aucune validation E2E revendiquée ici**.  
Référence : branche `main`, environnement GTK sous `environnement/` du dépôt. Les ZIP GTK joints aux conversations sont obsolètes.  
Propriétaire : UX (spécification et validation) ; Plugins/CoreUI (implémentation). Pas de modification CI/CD.

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
