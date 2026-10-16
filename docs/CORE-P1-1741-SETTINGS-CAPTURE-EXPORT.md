# P1 #1741 — capture réelle opt-in depuis les Paramètres

## Parcours utilisateur

Dans **Paramètres → Diagnostic**, la section « Performances · capture volontaire » expose :

1. **Démarrer la capture détaillée** : active le moniteur Core **déjà présent**
   (`window.ui_perf.enable_detailed_capture(True)`). L'ouverture des Paramètres,
   le rafraîchissement du diagnostic et les autres actions n'activent jamais la
   capture à la place de l'utilisateur.
2. Naviguer vers Bibliothèque / Récents / autres opérations afin d'observer
   de vrais événements. La capture se poursuit volontairement même lorsque
   l'onglet Paramètres est masqué ; elle cesse à l'arrêt explicite ou à la fin du
   processus. Elle n'est **ni persistée dans la configuration ni relancée**.
3. Revenir dans Diagnostic : les boutons et le statut lisent le même monitor
   (`detailed_capture_enabled`), même si la page a été reconstruite.
4. **Arrêter la capture détaillée** : suspend les nouveaux événements, mais
   conserve temporairement le dernier historique jusqu'au prochain démarrage
   de capture. Les agrégats passifs restent présents.
5. **Exporter les mesures JSON** : enregistre un instantané local structuré
   `cifereca.performance.phases/1` via une boîte de dialogue GTK native.
   Le choix de destination et l'écriture sont **explicites** ; aucun upload,
   requête fournisseur, IA, tâche périodique ou analyse automatique.

## Garanties et limites

- **Export expurgé par construction** : catégories et phases énumérées, durées,
  `operation_id` numérique éphémère, origine observée/simulée, statistiques
  agrégées et événements bornés. Aucun texte d'exception, titre de livre,
  libellé de compte, chemin, identifiant fournisseur ni log libre.
- **Séparation des origines** : `measurement_origin="simulated"` désigne
  les fixtures et tests ; `observed` les opérations réellement observées,
  mais ne garantit **pas** qu'il s'agisse d'une installation utilisateur.
  La présence de mesures n'est pas une preuve de non-régression des performances.
- **Absence honnête de données** : sans échantillon, le statut indique
  `0 échantillon(s)`; les phases non instrumentées (réseau fournisseur,
  frame effectivement peinte, CPU/RSS) ne sont jamais déduites d'autres séries.
- **Surcharge** : le suivi détaillé reste opt-in, le moniteur borne les
  échantillons et événements et déclare les événements évincés. La présente
  PR ne mesure pas encore quantitativement le coût de la sonde sur installation
  réelle, ni l'impact plugin Diagnostics activé/désactivé.
- **Vie des widgets** : fermer/recréer les Paramètres invalide les boutons et
  toute réponse tardive du sélecteur de fichier ; la fermeture de la fenêtre
  empêche aussi les actions. Aucune mutation de `diagnostics.enabled` ou
  `diagnostics.level`, de configuration fournisseur ni de DRM.
- **Coordination** : les probes GC supplémentaires sont dans la PR #1804.
  Les futurs écrans Plugins Diagnostics doivent consommer
  `UiPerfMonitor.performance_report()` et ne pas créer un second collecteur.

## Protocole de qualification restant

Pour comparer OFF et ON, exécuter sur **la même installation/machine** des
passes alternées après chauffe (Bibliothèque 563 notices, Récents, changement
d'identité), en consignant n, p50/p95/max, GC, CPU, frame et échantillons
effectifs avec les deux niveaux de capture. Séparer E2E simulé et installation
utilisateur ; ne pas publier un PASS temporel avant comparaison reproductible.
