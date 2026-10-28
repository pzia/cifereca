# P0 #3228 — précontrôle local avant matérialisation BnF-Prêt

## Origine et limite de preuve

Recette utilisateur du 27/10/2026 : deux téléchargements BnF-Prêt réussis,
dont un ouvrage déjà présent auparavant sous `<identité>/manual/`. Ni la
présence du livre ni un titre identique n'attestent une même édition, licence
Adobe ou provenance BnF. L'ancien garde `bnf_auto_ref` ne couvre que les
réceptions automatiques déjà liées au prêt exact.

Cette tranche **n'est pas un moteur de rapprochement** : son observation
ne décide jamais de l'autorisation ni d'un veto permanent de GET. Depuis le
blueprint #3171 du 28/10/2026, un candidat bibliographique *plausible* reste
un indice descriptif, non une présence fournisseur attestée.

## API générique

`core.local_candidate_screen.screen_local_candidates` ne connaît aucun
fournisseur, ne lit ni ne modifie les EPUB/PDF, ne fait aucun appel réseau et
n'utilise ni stockage privé de plugin ni droit déduit du titre ou du SHA.

- Entrée : identité locale explicite, titre affiché (indice uniquement),
  répertoires locaux autorisés, acquisitions complètes et notices locales
  déjà projetées par le Core.
- Un titre équivalent dans un reçu ou une notice dont le chemin référencé
  se situe lexicalement dans un de ces répertoires produit
  `needs_reconciliation / local_candidate_found`.
- Une preuve illisible, ambiguë ou dépassant le budget produit
  `needs_reconciliation / local_evidence_unavailable`.
- `none_observed` signifie seulement qu'aucun **indice indexé** n'a été
  rencontré. Cela ne prouve **jamais** l'absence d'un fichier physique et
  `acquisition_authorized` reste systématiquement `False`.

Le résultat ne contient **aucun chemin, titre, identité, SHA ni détail SQL**.
Il n'est pas un reçu, ne rapproche rien dans SQLite, et ne change pas
les droits temporaires d'un prêt en propriété permanente.

## Adaptation BnF

Après le garde original `bnf_auto_ref` (dont la vérification physique
SHA/placement est conservée), l'acquisition automatique BnF demande une
projection des acquisitions `manual` et des notices locales de l'identité,
puis applique la primitive générique aux seules branches
`<identité>/manual/` configurées. Cela reste sous le verrou d'acquisition
existant, avec validation de la génération de consentement et du contexte
compte/session après inspection des références.

Un candidat sûr `local_candidate_found` est **conservé comme indice mais
ne bloque plus** l'acquisition déjà explicitement consentie du prêt courant :
l'action ACSM signée reste soumise aux gardes indépendants du fournisseur,
compte, identité, politique activée, session, droit actif et pipeline ADEPT.
Aucun livre manuel n'est adopté, supprimé ou déplacé sur la seule
correspondance de titre. Une inspection `local_evidence_unavailable`
(lecture impossible, symlink/scan incomplet ou incohérence) demeure bloquante
avec `attention_required / bnf_local_evidence_unavailable`. Un prêt différent
sans indice reste lui aussi éligible aux mêmes autorisations préexistantes.

## À livrer avant fermeture #3228 / #3171 / #2797

1. Recenser sans fuite ni surcharge les EPUB/PDF totalement non indexés dans
   la bibliothèque de l'identité, et vérifier leur présence physique par
   une méthode sans suivi de symlink, avec budgets explicites.
2. Produire une liste privée de candidats à sélectionner et confirmer dans
   l'interface, avec comparaison de contenu SHA, édition et licence ADEPT ;
   **aucune relation ne s'infère automatiquement d'un titre**.
3. Versionner un contrat de preuves/capacités dans `cifereca.plugin_api`
   et consommer les adaptations des différents fournisseurs, sans nouveau
   branchement métier dans le moteur de décision générique.
4. Confirmer une association déclarée ou attestée au bon périmètre avant
   de marquer « déjà local », et préserver les fichiers à leurs chemins
   actuels. Renouveler une licence de prêt seulement sur raison établie.
5. Vérifier en vraie recette BnF (compte exact et consentement) qu'une deuxième
   synchronisation ne matérialise pas un candidat déjà lié ; absence de
   mutation d'une autre identité, y compris sous des titres identiques.

Les tests de cette tranche sont synthétiques et n'attestent aucun prêt BnF
réel. Aucun comportement métier Numilog n'est modifié.
