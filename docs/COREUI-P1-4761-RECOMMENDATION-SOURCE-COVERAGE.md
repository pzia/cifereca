# P1 #4761 — États des catalogues en Recommandations

La facette **Sources** reste une facette *des recommandations classées* :
elle ne doit proposer que des fournisseurs présents dans les candidats
finaux. Un catalogue connecté avec zéro recommandation ne doit pas devenir
un filtre sélectionnable vide, ni être faussement déclaré désactivé.

Une **ligne d'état distincte**, sous le statut du calcul, expose :

- catalogues annoncés comme disponibles par le broker Core ;
- fournisseurs ayant effectivement émis au moins un lot progressif ;
- sources avec des notices reçues mais non retenues dans le classement ;
- réponses vides, réponses en erreur, absences de lot ;
- sources ayant contribué aux recommandations classées.

L'infobulle identifie les catalogues (labels publics déjà bornés), avec
un statut fermé. Une absence de lot n'est jamais interprétée comme une
réponse vide. Pour les anciens brokers synchrones sans événements par
fournisseur, l'UI indique **« réponses individuelles non détaillées »** :
elle n'invente aucun statut. En absence de signaux de bibliothèque, l'état
indique qu'aucune recherche n'a été déclenchée.

La projection **n'affiche jamais** les erreurs brutes du fournisseur, les
requêtes, les credentials, les comptes, les liens de téléchargement ou les
droits de possession. Les observations sont des compteurs de lots Core
agrégés par fournisseur, pas des preuves de droits. Les noms d'origine
malformés ne sont pas rendus. Le statut est effacé immédiatement au
recalcul/changement de profil, et remplacé à la fin du calcul admissible.

Tests hors réseau : 4 fournisseurs synthétiques couvrant réponse classée,
réponse non retenue, réponse vide et erreur ; provider sans lot, broker
historique sans ventilation, données malformées et effacement du label.

**Limites :** les événements progressifs ne sont pas encore affichés comme
un tableau vivant par fournisseur pendant le calcul ; cette tranche ajoute
la synthèse **finale**, sans changer le ranking ni les facettes. Aucun accès
fournisseur réel ou test GTK de bout en bout n'est revendiqué ici (#774).
L'alignement des widgets Recherche/Recommandations et les actions métier
exactes restent suivis en #4761/#3687 et #4664.
