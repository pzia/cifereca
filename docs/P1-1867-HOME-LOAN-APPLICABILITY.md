# P1 #1867 — Accueil : prêts uniquement lorsque leur source est possible

## Défaut reproduit (fixture synthétique)

Le Core publie `known_loans=None` tant qu'aucune observation de prêt
n'existe pour les comptes d'une identité. L'ancien Accueil transformait ce
**manque de mesure** en « Inventaire des prêts à actualiser », y compris
pour un profil *uniquement Kobo/Numilog* (sources de livres achetés), où
aucun fournisseur ne prend en charge les prêts. Cet avertissement
pouvait déclencher une synchronisation inutile et faire croire à une
échéance de restitution.

## Projection locale et réversible

Le plugin Accueil utilise **uniquement les fournisseurs enregistrés
dans la projection publique `dashboard_state.providers`**, et non les
origines historiques `statistics.sources` des fichiers.

Quand cette liste est **présente, non vide et exclusivement composée de
fournisseurs dont le contrat courant ne prévoit pas les prêts**
(Kobo, Numilog, catalogues publics connus et fichiers locaux), et
**qu'aucune observation historique de prêt ni capacité `loans`
explicite ne contredit ce constat**, la page affiche :

> Aucune source de prêt configurée

Elle ne propose alors **pas** l'alerte « Inventaire des prêts à
actualiser ». Les droits distants d'achat et fichiers locaux
restent dans **deux compteurs distincts**. Aucun « zéro prêt » n'est
fabriqué ; une source de catalogue n'est ni un prêt, ni un achat, ni
un artefact local.

Le comportement existant est **conservé** pour Paris, les mélanges
Paris/Kobo/Numilog, les sources inconnues, les listes de fournisseurs
absentes ou corrompues, une capacité de prêt déclarée, une observation
historique de prêts, et les erreurs de synchronisation d'achats.
Une origine d'artefact « Paris » dans le catalogue local n'est **pas**
une preuve qu'un compte Paris est configuré actuellement.

Aucune consultation de fournisseur, écriture, synchronisation, prêt,
retour, acquisition ou téléchargement au rendu. Ce lot est limité au
plugin Accueil et à ses tests GTK existants ; il ne modifie pas le
calcul des prêts en Core ni les contrats des providers.

## Recette

- Numilog seul et Kobo seul : 0/1/N livres possédés, droits distants
  sans fichier local ; aucune fausse alerte de prêt.
- Mix Paris/Kobo/Numilog : prêt réel et échéance conservés ; avertissement
  de fraîcheur encore disponible quand il est fondé.
- Fournisseur non reconnu ou liste d'accounts absente : ne pas conclure
  qu'aucune source de prêt n'existe.
- Réinitialisation/observation de prêt antérieure : ne pas supprimer
  une observation de prêt en changeant la liste des stores.
- E2E GTK existant (fixture Accueil 1366/1600) : vérifier texte,
  allocation visible, absence d'alertes trompeuses, navigation inerte
  et aucun appel fournisseur.

## Restant à #1867

Regroupement visuel « Mes achats / Catalogue » pour Kobo et Numilog,
vérification réelle des profils mixtes et déclinaison dans Recherche,
Bibliothèque et statistiques ; ne pas fermer le ticket sur ce seul lot.
