# Portée du propriétaire Kobo (#3171, #3252)

La recherche de propriétaire appelle `IdentityBroker.accounts(identity_id)`
avec l'identité du compte de session, enregistrée par le Core. L'argument
omis sélectionne les comptes du profil affiché et ne convient pas à une
synchronisation individuelle ou globale. Une identité vide/invalide reste
non attestée ; aucun repli sur le sélecteur n'est autorisé.

Le résultat doit toujours attester un propriétaire unique pour le compte
Kobo et le credential exacts. Les doublons, changements de broker, de
credential, de compte et de génération restent refusés. Changer seulement
le profil affiché ne change pas cette autorité. Cette règle s'applique aux
observations, aux fences de pagination et aux offres/acquisitions existantes.

`tests/test_owner_independent_selector.py` couvre les trois identités
synthétiques Hugues/Héloïse/Alexandre, tous les ordres de lecture et sélecteurs,
un changement pendant le manifeste, et les refus avant GET. Les autres tests
de session couvrent les vrais rebinds. Aucun test fournisseur live n'est revendiqué.

Handoff A : le Core reste responsable de l'affectation initiale de l'Account
de session, de l'admission sous verrou et de la publication des origines.
Le contrat public n'est pas modifié et l'inventaire reste positif/partiel.
