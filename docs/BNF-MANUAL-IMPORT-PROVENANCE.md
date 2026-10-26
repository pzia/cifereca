# Import ADEPT des prêts BnF : source, action et preuve

Un nouvel import BnF consenti conserve trois dimensions distinctes :

- **Source observée** : BnF Prêt, identité, credential, compte emprunteur,
  identifiant du prêt et identifiant de la ressource exacts.
- **Action** : import ACSM manuel via ADEPT, dans le ledger et la requête DRM.
- **Attestation** : fichier local validé ; aucun reçu ni nouveau droit BnF.

Le fichier est rangé dans `<identité>/bnf-pret/acsm-<sha256 ACSM>/…`.
L'empreinte ACSM reste la clé d'idempotence. Un réimport BnF vérifie le format
et l'empreinte de l'artefact existant ; une modification ne relance pas ADEPT.
Les anciens fichiers sous `manual` restent à leur chemin et ne reçoivent
aucune provenance BnF sur la seule base du contenu ou du titre.

Le ledger manuel peut conserver `metadata.bnf_observed_loan`, une liaison
**locale d'affichage**, écrite seulement après contrôle du fichier indexé et
du placement BnF de la bonne identité. Une transaction SQLite interdit de
remplacer le premier compte/prêt lié au même import. Cette donnée ne remplace
ni `entitlements`, ni une preuve de fulfillment du fournisseur.

La Bibliothèque rapproche cette liaison et un droit de prêt **déjà existant**
sur les cinq coordonnées exactes. Une liaison absente, ambiguë, incomplète ou
incohérente conserve les lignes séparées. Le rapprochement est une projection
SQLite sans accès au fichier, au fournisseur ou à ADEPT. Le scan et le
redémarrage conservent la liaison du ledger ; aucun déplacement n'est lancé.
L'acquisition automatique consentie BnF conserve les mêmes distinctions.

Tests : `tests/test_bnf_import_library_link.py`. Le runtime ADEPT est doublé ;
SQLite, les artefacts EPUB/PDF, le scan et la projection sont réels.
