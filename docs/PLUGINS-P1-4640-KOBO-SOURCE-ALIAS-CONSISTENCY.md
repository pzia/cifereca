# P1 #4640 — alias source Kobo : une seule route attestée

## Écart de contrat

Le descripteur Cifereca vérifie une sélection en utilisant l'attribut
`candidate.source` lorsqu'il est présent, sinon `candidate.kobo.source`.
La recette KDRM `scoped_kdrm_source()` utilise
`candidate.kobo.source`. KVault produit actuellement l'attribut imbriqué
`kobo.source`; **ce patch ne modifie pas KVault**.

Une réponse inattendue exposant les deux attributs avec des valeurs
contradictoires pouvait donc conduire à une admission locale fondée sur
une route différente de celle lue ensuite par le runtime. Ce n'est
**pas** une preuve que K1, K2 ou K3 présentent effectivement cette anomalie.

## Politique Cifereca

- Si `source` et `kobo.source` sont tous deux explicitement présents,
  ils doivent être des valeurs **strictement identiques**.
- Tout écart refuse le **descripteur entier** avec la catégorie locale
  `candidate_source_conflict` : pas de sélection opportuniste d'un frère
  pourtant admissible, pas de profil partiel, pas de GET.
- La recette KDRM rejette également un candidat directement présenté avec
  un alias contradictoire (défense indépendante).
- Une source racine **absente** conserve le comportement et l'ID d'offre
  existants ; une source racine identique n'affecte pas l'admission.
- Aucun élargissement de la matrice `media_format/source_format` ni des
  droits ; `unknown/flepub` K2 reste rejeté.

Tests synthétiques : alias discordants dans les deux sens, un `None`
explicite, changement de casse, K1 candidat sain + frère conflictuel
dans les deux ordres, source absente/identique, K2 `unknown/flepub`,
journal provider expurgé, aucun POST délégué ni GET de contenu.

Ce garde-fou ne remplace pas l'observation ciblée attendue pour les
trois références réelles. Ne pas redemander le ZIP terrain déjà traité.
