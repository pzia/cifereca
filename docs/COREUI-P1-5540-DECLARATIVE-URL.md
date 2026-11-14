# #5540 — URL de compte fournisseur déclarative (contrat opt-in)

## Contrat

La dataclass publique `AccountOptionField` accepte
`value_type="url"` et `url_schemes=("https",)` par défaut.
Un plugin **peut** explicitement déclarer `("https", "http")`
pour un service local administré par le lecteur. Ce nouveau type ne
modifie aucun champ `text` ou `unsigned_integer` existant, notamment
les options URL historiques de Paris, Kobo et Gallica.

Le rendu GTK continue d'utiliser une `EntryRow` générique et le pipeline
de validation `build_settings_draft` sauvegarde la valeur exacte sous
`providers.accounts[].options` pour la connexion sélectionnée. L'écran
Paramètres et la validation **ne contactent pas** l'hôte, ne connectent pas
de compte, ne synchronisent ni ne téléchargent. L'URL déclarée n'est ni une
preuve de quota/permission ni une attestation de confiance dans son contenu.

## Validation

Le type URL exige une URL absolue, syntaxiquement bien formée, de longueur
1–2048, sur un hôte DNS ASCII explicite. HTTP doit être autorisé
individuellement dans la déclaration. Les composants `userinfo`, query
et fragment (souvent porteurs de secrets), port hors limites, caractères de
contrôle, chemins ambigus avec backslash, échappements invalides et caractères
de contrôle double-encodés sont refusés. IPv6 et les URL portant une
authentification en query **ne sont pas encore dans ce premier schéma**.

Si un fournisseur exige une URL munie d'un token, la valeur doit être
représentée par un champ `CredentialField(secret=True)` dédié ; il ne faut
pas détourner l'URL visible. Les défauts déclaratifs non vides doivent eux
aussi réussir le contrôle. Un ancien champ volontairement vide reste vide ;
une option nouvelle non configurée reçoit son défaut déclaré.

La validation échoue **avant écriture** sur une valeur illégitime et ne
journalise pas la saisie. Le tests couvrent deux identités et connexions,
absence de valeur legacy, HTTP localhost opt-in, données historiques
opaques, schémas invalides et URL mal typées. Aucune preuve réseau ni
permission fournisseur n'est déduite.

## Reliquat du ticket parent

#5540 reste ouvert : déclarations réelles par fournisseur et leur matrice,
opt-in distinct du statut connecté, prêts observés/quota annoncé, comptes
liés et recette GTK multi-profil. Ce lot constitue seulement un nouveau
type de schéma additif validé sur données synthétiques.
