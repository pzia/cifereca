# #514 — Acquisition archivée : résultat non-erreur

Le Core ignore un manifest Kobo `archived=true` avant recherche de candidats,
réconciliation et acquisition. Le compteur `skipped_archived` est distinct des
échecs, attentions et téléchargements ; il suit les checkpoints, bilans par
compte, diagnostics, CLI et résumé GTK. Aucun droit ni Placement n'est retiré.
Un reçu local complet reste complet, ses octets et sa provenance sont conservés.

Un `409 archived_book` rencontré pendant une acquisition devient le même
résultat non-erreur. La tentative effective reste comptée une fois. Son état
persisté empêche un nouvel essai tant qu'un manifest frais n'atteste pas
explicitement `archived=false`. Le Core ne restaure jamais le livre à distance.
Les anciens `attention_code=archived_book` sont reconnus sans migration destructive.

## Contrat générique additif #3252

`ProviderRightObservation.delivery_unavailable_reason` vaut `''` par défaut
(anciens producteurs compatibles), ou `'archived'` avec `delivery='unavailable'`
et une observation `provider_response` référencée et datée. Ce motif qualifie
la livraison, pas le droit : `state='active'` reste possible ; aucune offre,
licence, origine ou réception n'en découle. Une indisponibilité sans motif
reste une attente générique, jamais un archivage inféré.

Un `delivery='unavailable'` connu n'appelle pas le préflight d'offre sélectionnée
pendant cette Sync. Une réponse sélectionnée exacte et attestée indisponible
est acceptée comme fait négatif de livraison, sans faux échec fournisseur.
Le plan et l'exécuteur comptent `skipped_archived`, sauf présence déjà attestée
par OriginLink et Placement revérifié (qui reste `present_attested`). Les mêmes
gardes compte/session/consentement restent applicables. La prochaine Sync lit
un nouvel inventaire ; aucune indisponibilité n'est un retrait de droit.

**Handoff B** : les adaptateurs Kobo doivent émettre ce motif typé dans leurs
snapshots manifest et sélection. La tranche Plugins #3657 émet déjà
`unavailable` mais sans motif ; cela empêche les tentatives inutiles sans
suffire au compteur canonique d'archivés. #514 reste ouvert jusqu'à réception
de cette adaptation et des gates finales. Aucun fournisseur réel exercé par
les tests synthétiques Core.
