# Restitution numérique Paris : licence ADEPT puis vérification PNB

## Parcours dans « Mes emprunts »

Le Core privilégie la restitution de la **licence Adobe effectivement activée**
par `runtime.kobo-adept`, uniquement si le prêt a une provenance et un
profil ADEPT d'origine vérifiés. La confirmation d'un compte rattaché reste
requise (`cifereca-cli paris-adept`).

Pour un prêt ADEPT confirmé :
1. Un checkpoint local opaque (empreinte SHA-256 de provenance) est enregistré
   **avant** toute mutation Adobe.
2. La restitution est demandée une seule fois avec l'ID de licence et le profil
   ADEPT enregistrés dans l'acquisition.
3. Le runtime vérifie que le prêt Adobe est retourné et que le jeton est absent.
4. Le Core relit **sans cache** l'inventaire Paris du compte emprunteur exact.
5. L'état `paris_return_confirmed` n'est affiché que si le prêt est absent
   de l'inventaire Paris complet. Si Paris est encore actif ou indisponible,
   l'état reste `partial`.

**Il n'y a jamais de POST `ReturnDigitalLoan` en réaction automatique à
une restitution ADEPT.** Un POST Paris nécessite une action fournisseur séparée,
explicite, et une nouvelle preuve que le prêt est encore actif
(`return_digital(loan, explicit_retry=True)`). Si une demande PNB a déjà été
effectuée, elle doit être réconciliée avant tout retour Adobe.

## Sécurité des interruptions et reprises

Les checkpoints sont conservés en SQLite et ne contiennent ni identifiant
d'emprunteur, ni jeton ACSM, ni secret, ni identifiant Adobe. Après double clic,
interruption ou redémarrage, **l'activation d'une nouvelle mutation ADEPT est
interdite**. La reprise consulte seulement les inventaires ADEPT/Paris : si la
restitution Adobe ne peut être confirmée, elle demeure bloquée et demande une
vérification manuelle, sans rejouer la commande.

`verify_loan_return(loan)` ne lance aucune première restitution.
Les livres déjà stockés et l'historique ne sont jamais supprimés.

## Présentation des résultats dans Mes emprunts

Un processus terminé sans exception **n'est pas une preuve de restitution**.
La confirmation visuelle est affichée uniquement si :
- le retour ADEPT est confirmé **et** le prêt est absent d'un inventaire Paris complet ;
- ou, pour une action PNB distincte, si l'inventaire Paris confirme l'absence.

Si ADEPT a restitué la licence mais Paris montre encore le prêt, l'interface
indique explicitement « licence restituée, prêt toujours actif à Paris » et
rappelle qu'aucun second retour Paris automatique n'a été fait. Si
l'inventaire est indisponible ou que le profil est ambigu, elle présente
« action requise » et **ne déclare jamais le prêt restitué**.

Une simple réponse `success=true` du fournisseur PNB ou un worker terminé
avec `status=partial` ne génère pas de notification de succès.

## Limites et critères de recette

- Les deux inventaires doivent être complets et concerner le même compte
  emprunteur et la même transaction. `success=true` PNB ne suffit pas.
- En l'absence de preuve ADEPT, le Core indique
  `provider_action_available`, sans envoyer de POST.
- Les différents statuts partiels et les deux actions distinctes demandent
  une finition UX (#980). Une réponse neutre « État de restitution actualisé »
  ne signifie pas « restitué ».
- Qualification réelle demandée : deux connexions Paris et deux profils
  ADEPT, prêt commun ou rattaché, compte encore actif, connexion indisponible,
  interruption entre mutation Adobe et checkpoint, répétitions, absence d'artefact
  supprimé (#976, #982, #984).
