> **Archivé — 30/10/2026 (#3185).** Le tool Maintenance « Réconciliation de bibliothèque » et sa readiness dédiée ont été retirés. Ce document décrit l’ancien parcours uniquement ; il ne définit plus une interface ou un prérequis de Sync. Les contrôles de présence, les audits d’intégrité et la lecture des preuves SQLite conservés par le Core ne sont pas supprimés.

# P0 #2661 — Disponibilité locale de la réconciliation

**Périmètre :** Core/CoreUI, hors réseau, sans mutation et sans réparation.

La réconciliation fonctionne **sans première synchronisation Kobo, Numilog ou
Paris**, et sans authentification fournisseur, lorsque l'identité active,
les racines configurées et le fichier SQLite local sont utilisables. Une
bibliothèque vide/partielle peut être analysée sans fabriquer de droits.

## Activation dans Maintenance

L'action « Préparer l’analyse… » commence désactivée pendant **une courte
vérification asynchrone** (jamais d'interrogation SQLite depuis GTK).
Le Core retourne uniquement un statut/cause fermés, sans noms, chemins,
identifiants ou détails privés :

| Statut | UX |
|---|---|
| `ready_offline` | Bouton actif, « Prêt pour l’analyse hors réseau » |
| `initializing` | Bouton désactivé, « Initialisation de la bibliothèque locale… » |
| `temporarily_busy` | Bouton désactivé, migration locale détectée |
| `blocked` | Bouton désactivé, identité/configuration ou stockage incorrect |
| `indeterminate` | Bouton **actif** pour une tentative contrôlée ; jamais bloqué pour un constat inconnu |

Un contrôle local `lstat` refuse un chemin SQLite absent, relatif ou non
régulier. Le marqueur de migration est vérifié uniquement dans **l'image
SQLite immuable**, `mode=ro&immutable=1`, et seulement si aucun WAL n'est
présent. **Avec WAL**, le journal de migration dans les transactions non
checkpointées ne peut pas être jugé par cette lecture : le statut est
`indeterminate` et la lecture réelle est revalidée par le broker. Un
timeout, lecteur WAL manquant, scan partiel ou conflit de preuves n'est
**pas** assimilé à un besoin de première synchronisation.

L'état est recalculé à l'ouverture/actualisation de la page et lors
d'un changement d'identité. Tant que le stockage s'initialise ou qu'une
migration est détectée, un rappel léger toutes les 3 secondes **déclenche
un worker** sans lire SQLite dans GTK. Fermeture et navigation invalident
les callbacks anciens. La prévisualisation et l'exécution recontrôlent
chacune les préconditions avant de continuer, en plus du contrôle
Core complet de portée/snapshot : aucun bouton activé auparavant ne
constitue une autorisation durable.

La vérification ne déclenche **ni** `opening_sync`, **ni** connexion
fournisseur, **ni** scan EPUB, **ni** cache ou récupération réseau, **ni**
lecture via la connexion SQLite potentiellement écrivable de
`Storage.metadata_get`. Elle n'ouvre pas la bibliothèque pour écriture.
Le test ne prouve pas la cohérence des preuves ; seule l'analyse complète
peut l'établir.

## Recette

Première ouverture sans base, base locale valide sans sync, identité absente,
migration présente et WAL vivant, fichier non régulier/symlink, fermeture
avant réponse, changement d'identité et de SQLite, lecture du résultat
incomplet et absence d'accès réseau. La qualité des liens reste du ressort
de #2565/#2657.
