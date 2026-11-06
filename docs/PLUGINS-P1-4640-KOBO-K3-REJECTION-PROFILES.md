# P1 #4640 — Kobo K3 : raisons des rejets corrélées aux formats (06/11/2026)

Le relevé utilisateur a établi **K3 : 2 candidats / 0 admissible**, avec des causes individuelles agrégées. Le diagnostic `candidate_shape_counts` introduit entretemps distingue source, média, source_format et handling, mais ne dit pas **quelle forme a été rejetée pour quel motif**.

La présente tranche ajoute `candidate_rejection_profile_counts`, calculé **pendant la même boucle de qualification**. Pour chaque candidat rejeté, une clé agrège :

`<source>:<media_format>:<source_format>:<handling>|<motif_de_rejet>`

Les quatre composantes de profil utilisent exclusivement des valeurs **fermées** : `library_sync`, `content_access_book`, `epub`, `kepub`, `pdf`, `acsm`, `flepub`, `unknown`, `epub3`, `epub3fl`, `http_plain`, `adobe_adept`, `kobo_kdrm` selon le champ, ou `missing`/`other`. Les motifs proviennent de constantes locales du code d'admission. Aucune valeur source non reconnue, URL, signature, identifiant de produit, compte ni jeton ne peut intégrer la clé. **Maximum 50 candidats**, et donc maximum 50 observations de rejet avant agrégation.

La corrélation est émise seulement après le passage complet ; un conflit de scope abortif ne divulgue aucun compte partiel, conformément aux compteurs existants. Aucun candidat n'est réessayé ni téléchargé. Les cas K1 avec deux candidats admissibles ne reçoivent pas de faux rejet ; K2 avec un seul candidat conserve son motif individuel.

## Recette

Tests synthétiques : deux profils distincts et deux causes distinctes ; deux candidats de **même profil** avec rejets différents ; formats/source inconnus contenant des marqueurs privés et vérification de leur réduction à `other`; ordre inverse des candidats ; absence de décision d'achat ou téléchargement.

**Limite :** ces compteurs permettent de qualifier K3 lors de sa prochaine observation ciblée, sans prouver une route exécutable ni autoriser de format inconnu. Les droits d'origine, le KDRM et les règles de sélection Core restent inchangés ; #4640 reste P1 actif.
