# P1 #4681 / #2385 — Identifiants exacts pour recommandations

Le classement et l'exclusion des livres déjà présents ne doivent pas
confondre deux notices portant le même `(fournisseur, record_id)` dans
des comptes, bases documentaires ou collections différents.

La projection locale `identifier_values()` conserve le comportement de
compatibilité `source:<provider>:<item_id>` **seulement lorsque la notice
ne contient aucune coordonnée de portée de source**. Avec un compte,
une docbase ou une collection renseignés, elle exige une provenance
complète `(source_provider, source_credential, docbase, source_collection
optionnelle, record_id)`. Elle produit une clé opaque stable
`source-exact.v1:SHA256` sur ce tuple, sans afficher de credential ou
d'identifiant privé dans les valeurs utilisées par le moteur.

- Deux credentials ou collections ne se prouvent jamais mutuellement
  « déjà présents » sur le seul `record_id` fournisseur.
- Une source incomplète ne retombe pas sur une clé fournisseur non scopée,
  ni sur l'homonymie titre/auteur pour supprimer une recommandation.
- Les anciens candidats purement bibliographiques sans source restent
  compatibles avec la comparaison par titre/auteur si aucun identifiant
  fort n'est disponible.
- Un ISBN valide et explicitement présent reste un signal bibliographique
  indépendant. **Il ne devient jamais un droit**, une attestation de
  possession numérique ni une autorisation de transfert.
- Les identifiants sont une projection de classement, pas un contrat Core
  de fusion ou d'héritage entre éditions ; la qualification des ISBN propres
  à une manifestation relève de #2385, et la future Notice C de #4552.

Cette tranche n'ajoute pas de lecture de signaux de goût ou de poids
achat/prêt. #4681 reste P1 ouvert pour les préférences attestées,
l'explicabilité et la comparaison de qualité sur corpus multi-profils.
