# P0 #5069 — fermer les arêtes inverses release → transaction → snapshot

Le plan commun 24 h + minimum 2 protège désormais le journal d'une transaction qui est encore référencé par une release Core retenue. Le marqueur `.cifereca-transaction.json` est ouvert avec des descripteurs ancrés `O_NOFOLLOW`, taille bornée 4 KiB et refus des hardlinks. Un marqueur présent mais malformé ou pointant vers un journal absent bloque la qualification de purge ; l'absence d'un marqueur sur une release legacy reste admise.

Les snapshots de `transactions/<id>/plugins` référencent également leur transaction parente. Le graphe est bidirectionnel : un journal conservé protège son snapshot de restauration ; un snapshot conservé protège le répertoire parent sans lequel il n'existe plus.

Recette synthétique : sept transactions anciennes dont la plus vieille est encore attachée à `current` ; référence conservée même au-delà du minimum de deux ; snapshot associé protégé ; lien symbolique vers une donnée privée et journal absent refusés ; installations legacy acceptées.

Il s'agit de **décisions conservatrices**, et non d'un apply. Les familles plugin/backups/staging doivent encore être reliées et nettoyées sous lease partagé avec preuve d'ownership, journal d'audit et reprise crash-safe. `safe_to_apply=false` reste en vigueur.