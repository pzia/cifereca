# Core P1 #3121 — état de session inerte pendant une action de prêt

Le bail `LoanActionScopeLease` contrôlait déjà que `session.accounts`
n'appelle pas un getter fournisseur. Pourtant `session.connected`,
`session.provider_id`, `session.credential_id`, `session._epoch` et
`session._closed` étaient encore lus par `getattr` avant/après I/O.

Ces noms sont **des valeurs mémoire initialisées par `ProviderSession`**,
pas des opérations fournisseur. L'admission et sa revalidation lisent
maintenant l'instance via `object.__getattribute__(session,"__dict__")`,
sans exécuter les getters ni descripteurs d'un sous-type. Tout shadowing
par descriptor ou surcharge de `__getattribute__` conduit à un refus
conservateur ; une session sans dictionnaire d'instance vérifiable ne peut
pas autoriser une action.

Le stockage du bail utilise la même lecture inerte pour l'epoch. Cela
protège la validation avant **et** après réseau contre les propriétés
dynamiques ayant des effets de bord, sans nouvelle requête, droits, POST,
ADEPT ni changement du contrat public.

Tests : état standard BnF connecté ; cinq propriétés getter hostiles,
un proxy `__getattribute__`, déconnexion en mémoire et injection de
getter après capture. Fixture BnF locale sans fournisseur réel.

Ce bail demeure une **condition nécessaire mais non suffisante** à
l'emprunt/retour/renouvellement : permissions, consentement, checkpoints
anti-rejeu et vérification de la réponse restent propres à chaque action.
