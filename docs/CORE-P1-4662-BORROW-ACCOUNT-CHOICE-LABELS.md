# P1 #4662 — libellés des choix d'emprunt multiprofils

Le preview Core `BorrowForIdentityPreview` peut contenir plusieurs offres
pour un prêteur unique (deux comptes bibliothèque, docbases ou collections
exactes). Des libellés identiques « Emprunter avec Alice » ne constituent
pas un menu suffisamment explicite, même si les `option_id` sont distincts.

La projection `BorrowForIdentityOption.label` préserve le texte simple
lorsqu'il n'y a qu'une offre et, en présence de plusieurs offres portant le
même libellé, ajoute :

- le nom court du compte **lorsqu'il est affichable et distinct** ;
- sinon « Compte bibliothèque · choix 1/N, 2/N… » selon l'ordre stable
  des options exactes, jamais l'ordre d'arrivée des sources.

Les noms de type identifiant personnel (courriel, URL, long identifiant
numérique), credentials, barcode et `unique_id` ne sont pas affichés
comme désambiguïsation. Les coordonnées exactes demeurent internes, les
`option_id` sont opaques, et la liste conserve un choix explicite
`requires_explicit_choice=True` pour toute pluralité.

**Aucun droit nouveau.** Le libellé n'est ni une attestation d'emprunt ni une
licence de copie inter-profils. Tous les éléments restent `executable=False`;
les étapes Core de confirmation, revalidation, prêt et transfert de #4662
demeurent à implémenter. Tests hors réseau : deux comptes sous un profil,
noms distincts, noms interdits, ordre inversé, plusieurs collections,
absence de fuite et cas d'une offre unique.
