# P0 #5073 — Propriété affichée sur résultats de recherche

`gui/query_model.py::result_row` n'interprète plus `source_provider=="numilog"`,
`NUMILOG_LIBRARY`, `source_collection="library"` ni `owned=True`
comme une preuve de propriété.

L'affichage `remote_owned` exige un `SearchOwnedEvidence` immutable,
construit **une seule fois avant la projection GTK** depuis les droits
`entitlements(state="active", entitlement_type="owned")` du registre
canonique Core. Le test croise l'identité actuellement sélectionnée,
le fournisseur, le credential, l'account, l'édition et sa révision.
La source fournit des coordonnées mais ne peut pas construire ce snapshot
dans la GUI ni transformer seule une notice commerciale en droit.

`SearchResult.source_account_id` est un ajout ABI facultatif ; le plugin
Numilog le remplit depuis son **unique Account de session déjà chargé**.
Une notice sans compte exact, un résultat périmé, une révision ou identité
étrangère, un crédit public, ou un registre incomplet aboutissent à
`remote_catalog`. La décision est générique (Numilog, Kobo et source
synthétique). Aucun GET, connexion ou téléchargement n'est effectué lors
de cette projection ; l'autorisation effective d'acquisition reste en plus
soumise aux contrats de téléchargement existants.

La preuve n'est pas persistée dans les caches de recherche ni inférée
des champs libres `metadata`. Une bascule de profil invalide la
comparaison avec le snapshot de l'identité précédente.

Le snapshot épingle aussi les générations Core `_account_context_epoch` et
`_source_context_epoch`. Un compte sélectionné ou une session reconnectée
sous la même identité révoque immédiatement la preuve de la vue précédente.

Les clés d’affichage des résultats ayant un ID fournisseur en collision
incluent aussi le compte et la révision ; permuter des comptes ne réutilise
pas la sélection d’une autre édition. Ces clés ne sont jamais une preuve de droit.
