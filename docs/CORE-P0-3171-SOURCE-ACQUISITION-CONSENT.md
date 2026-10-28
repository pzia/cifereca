# Consentement canonique d'acquisition — #3171

`LibraryService.set_canonical_acquisition_consent(account, source, allowed=...)`
persiste une préférence explicitement choisie pour une `SourceKey` exacte :
identité, fournisseur opaque, credential, compte, branche, édition, droit et
type achat/prêt. Aucun appel fournisseur n'est effectué. Une autorisation
exige une référence positive attestée déjà admise par le Core et un compte
lié au profil et activé. La révocation reste possible lorsque la source est
désactivée ou que le droit n'est plus actif.

Le lecteur `_canonical_persisted_consent(account)` est raccordé au point
d'extension de la Sync existante. Il relit SQLite à chaque appel, y compris
après livraison : aucun cache de permissions. La valeur JSON versionnée
`cifereca.source.acquisition-consent/1` inclut toutes les coordonnées exactes
et un booléen strict. Sa clé SHA-256 utilise une sérialisation JSON sans
ambiguïté de séparateurs. Une valeur manquante, ancienne, malformée ou d'une
autre portée n'autorise rien. Les configurations Kobo/Numilog historiques ne
sont pas automatiquement converties en consentements canoniques.

La lecture vérifie compte/connexion/profil, source activée, état positif,
preuve fournisseur, caractère synchronisable et contexte Core courant.
La transaction de persistance est annulée sur rebind du contexte. La lecture
est bornée ; un dépassement de budget ne rend aucune autorisation partielle.
Les références de transfert ponctuelles, notices et preuves historiques
n'accordent aucune permission d'acquisition.

**Limite de cette tranche :** elle fournit persistance et lecture Core, pas
un nouveau parcours IHM ni l'admission d'une offre fournisseur. Sans callback
Core de livraison enregistrée, la Sync demeure en lecture seule. Avant tout
préflight privé/POST/GET, le routage réel doit utiliser cette permission et
vérifier offre actuelle, session, droit et résultat final. Les tests utilisent
SQLite et le vrai orchestrateur Sync avec livraison synthétique ; aucune
recette réseau/ADEPT réelle n'est revendiquée. #3171 et #3252 restent ouverts.
