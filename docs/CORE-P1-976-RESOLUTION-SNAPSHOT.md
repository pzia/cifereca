> **Historique — #5842 (13/11/2026) :** les commandes `cifereca-cli paris-adept ...` décrites ci-dessous ont été retirées et ne sont plus exécutables. Les mécanismes Core/SQLite de preuves ADEPT restent disponibles aux traitements autorisés. Un **diagnostic ADEPT multicomptes** générique et hors réseau est prévu en P2 #5841. Ne pas réintroduire ces commandes comme solution de repli.

# P1 #976 — empreinte d'observation locale avant validation ADEPT

## Pourquoi

Les commandes `audit-origin`, `audit-canonical`, `audit-transaction`
et `preview-resolution` réunissent des **observations locales** qui peuvent
changer entre une prévisualisation et une éventuelle décision ultérieure.
Même si un couple `SourceReference/OriginLink` reste présent, la **référence
exacte du reçu Adobe** peut avoir changé ; un simple compteur de liens ne
permet pas de détecter cette modification.

La primitive Core
`snapshot_paris_adept_local_evidence(storage, loan_id, item_id)`
génère une empreinte `sha256:paris-adept-local-evidence-v1` des données
historiques et canoniques **persistées**, strictement sur le prêt Paris
et l'item désignés.

L'entrée privée de hachage contient notamment les reçus historiques du même
item (incluant les éventuels liens de prêt ancien par entitlement) et les
lignes exactes des `SourceReference` et `OriginLink` v14, **y compris les
références de reçus et les métadonnées de l'Artifact rattaché**, pour détecter
toute modification même lorsque les liens restent présents. La lecture ne
rehache **pas les octets du fichier** ; une éventuelle opération métier devra
contrôler son SHA-256 physique indépendamment. Les lignes historiques sont triées : une variation
d'ordre SQL ou de liste ne fait pas varier le résultat.

Seul **le hash** et des compteurs sont retournés ; aucune référence de licence,
jeton ou valeur brute de reçu n'est projetée. Une valeur non sérialisable
bloque l'empreinte au lieu d'être convertie en texte. L'absence de toute
preuve ne produit pas d'empreinte utilisable.

## Contrôle de stabilité dans le checkpoint actif

La façade autonome `validate_paris_adept_local_snapshot`, sans appelant de
production, a été retirée (#5048). Le parcours Core actif
`preview_paris_adept_with_local_checkpoint` relit directement les preuves
avant et après la prévisualisation, puis compare leurs empreintes. Une
empreinte absente ou différente bloque la prévisualisation et masque les
coordonnées privées du candidat.

Une empreinte stable est **un contrôle d'égalité local, jamais un jeton ni une
signature**. Toutes les sorties conservent
`return_authorized=false`, `commit_available=false` et
`adobe_profile_resolution=unknown`. Aucune écriture future ne peut en
déduire une licence Adobe ou un prêt encore actif.

Ces lectures ne sont **pas atomiques** avec les opérations futures, qui
nécessitent une preuve serveur actuelle et une revalidation sous leurs
propres verrous et transactions SQLite. Les anciennes commandes CLI de
diagnostic restent supprimées.
