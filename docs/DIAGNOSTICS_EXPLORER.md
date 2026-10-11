# Diagnostics P0 — explorateur #1013 et première ergonomie #1011

L'explorateur « Journaux par composant » est inséré dans la **page Diagnostics
existante** ; il n'installe aucune nouvelle page parallèle. Il consomme
uniquement `context.broker("diagnostics").events()`, livré par #1010.

## Explorer
La première consultation est déclenchée lors du premier affichage de la page,
dans le broker `tasks` ; le résultat revient via `GLib.idle_add`, sans lecture
de journaux sur le thread GTK. Les requêtes obsolètes ne remplacent pas les
plus récentes. Un clic sur « Actualiser » reste local, sans synchronisation,
connexion à Paris/Kobo, lancement ADEPT ni réseau implicite.

Les contrôles disponibles : source (toutes / événements Core / journal
applicatif), gravité, période (tout / 1 h / 24 h / 7 j / 30 j), préfixe module
(validé par Entrée), recherche texte **dans la page visible uniquement**,
tri chronologique local, Précédent / Suivant (50 événements/page), sélection
et copie d'événements en JSON expurgé.

La hiérarchie regroupe chaque **module + source**, affiche compte et gravité,
puis l'horodatage, le message, la provenance et l'ID d'événement pseudonymisé.
Les détails se déplient selon les trois vues de #1012. La copie n'utilise
que les champs publics et refait l'expurgation, sans exposer les journaux bruts.

**Important :** un ID d'événement n'est pas un ID de corrélation de tâche.
Le broker actuel n'offre pas de corrélation fiable ; la page **n'infère pas**
que deux événements parlant du même fournisseur ou du même livre sont issus
d'une seule transaction. Cette limite reste visible dans l'export.

Les états de sources (alimentée, vide, désactivée, indisponible, tronquée)
restent visibles même si aucun événement ne correspond aux filtres.
Une page vide n'est jamais présentée comme « pas d'erreur ». Les données
sont bornées/rotatées en amont par le Core : l'explorateur ne prétend pas
retrouver un historique complet. Un filtrage texte n'interroge pas les pages
non chargées.

## Ergonomie initiale #1011
La synthèse système reste en tête, suivie du nouvel explorateur ; les détails
techniques sont désormais **repliés par défaut**. Le rapport support est dans
un panneau repliable à défilement indépendant (320 px), avec commandes
Agrandir (560 px), Réduire, Retour à la ligne, sélection de texte et copie.
La vérification active demeure une opération séparée et explicitement
déclenchée par l'utilisateur.

## Limites
Les tests automatiques couvrent le contrat Python pur et la structure
des composants GTK, mais **ne valent pas recette visuelle**. Le rôle UX doit
encore vérifier clavier/focus, lecture écran, petites et grandes fenêtres
GTK supportées, réactivité et très grands journaux **sans utiliser les archives
GTK historiques comme fixtures CI**.

Aucun nouveau workflow GitHub Actions, pas de lecture de fichier directe
depuis ce plugin, aucune IA ni transfert de données hors de Cifereca.
