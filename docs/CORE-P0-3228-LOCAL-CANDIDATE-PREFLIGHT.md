# #3228 — retrait du précontrôle BnF fondé sur les titres locaux

## Décision architecturale #5048 / #5055

Les anciens modules `core/local_candidate_screen.py` et
`core/local_physical_title_candidates.py` ne sont plus exécutés par
la synchronisation BnF : ils ont été supprimés après suppression de leur
dernier appelant de production.

Un titre local, un OPF homonyme ou la présence d'un ancien EPUB manuel ne
prouvent ni une origine, ni un prêt, ni une édition exacte. Un scan illisible
ou un répertoire manuel symlinké ne prouvent pas non plus qu'une acquisition
distincte est interdite. **Ces indices ne doivent ni ouvrir ni bloquer un
GET fournisseur autrement autorisé.**

Les garanties qui demeurent obligatoires avant chaque GET :

- compte, identité, credential et occurrence de prêt exactement liés ;
- consentement explicite pour la source, avec révision et époque stables ;
- prêt courant observé par le provider et offre signée sélectionnée ;
- aucune création d'emprunt par la synchronisation ;
- reçu `bnf_auto_ref` exact : si déjà enregistré, vérifier SHA, bytes,
  placement et identité avant de considérer l'artefact inchangé ;
- déclaration locale explicite : vérifier la présence réellement attestée,
  sans adopter le fichier sur la foi d'un titre ;
- aucun téléchargement répété ou mutation après invalidation de bail ;
- aucun fichier utilisateur déplacé, supprimé ou réécrit automatiquement.

Une origine non matérialisée peut être téléchargée puis dédoublonnée
uniquement sur octets effectivement vérifiés. Une absence dans un inventaire
partiel n'atteste pas la fin d'un prêt.

## Couverture

Les tests de prêt BnF conservent les cas : titre identique sous la même
identité ou une autre, deux titres distincts, ancien EPUB manuel non indexé,
répertoire manuel symlinké, absence de mutation des livres existants,
absence de pseudo-droit persistant et refus d'un reçu exact corrompu.

Les tests spécifiques à l'ancienne analyse de titres/OPF ont été retirés
avec le code mort. La validation du catalogue et des placements provient des
contrats génériques Core, non du nom d'un fournisseur.

L'ensemble des tests est synthétique et ne vaut pas recette avec un compte
BnF réel.
