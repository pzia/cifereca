# Recette isolée : Ubuntu 24.04 / AppArmor (#3646)

La recette échoue explicitement si Bubblewrap ne peut pas construire son
confinement. Ne pas lancer Cifereca hors sandbox, partager le réseau runtime,
désactiver AppArmor, ni modifier `kernel.apparmor_restrict_unprivileged_userns`.
La configuration de production et ses liens ne sont pas à modifier.

## Diagnostic sans changement de l'hôte

Depuis le dépôt à jour, en utilisateur normal, **pas sous sudo** :

```sh
python3 scripts/recette-doctor.py --require-apparmor --kernel-audit
```

L'outil ne lit aucune configuration Cifereca. Il relève versions OS/kernel,
Bubblewrap/parser, état AppArmor accessible, sysctls et namespaces ; il tente
un vrai bootstrap `--unshare-all` avec `/usr`, `/bin`, `/lib`, `/lib64` en
lecture seule, `/proc`, `/dev`, `/tmp` et le cache du chargeur requis. Le
programme synthétique vérifie namespaces distincts, absence du HOME hôte,
racine non inscriptible, interfaces réseau limitées à loopback, capabilities
effectives nulles et `no_new_privs`. Une seconde sonde teste `--ro-bind-fd`
sur un lien synthétique, y compris la conservation de son atime.

Avec `--require-apparmor`, l'absence de restriction globale à **1**, d'AppArmor
actif, ou du label enfant `unpriv_bwrap` en **enforce**, fait échouer le
diagnostic. Une sonde négative supplémentaire démarre une sandbox identique,
puis tente, dans son enfant, `unshare(CLONE_NEWUSER)` suivi de
`unshare(CLONE_NEWNS)` : un nouveau user namespace peut redonner des
capabilities locales même après `--cap-drop ALL`. Il faut observer un refus
de permission (`EPERM` ou `EACCES`) à l'une de ces deux étapes, sous le même
profil enfant en enforce. Le résultat conserve l'étape, errno et la cause
originale dans `checks.child_capability_result`. Une opération autorisée,
une limite atteinte, un syscall non disponible ou une preuve illisible ne
constitue pas ce refus et fait échouer le doctor. Aucun montage, mapping UID,
changement de profil ou fichier hôte n'est effectué par cette sonde ; ses
namespaces synthétiques disparaissent avec le processus. Le refus observé
ne permet pas d'attribuer seul la cause à AppArmor : consulter l'audit noyau.

Code retour **0** = prérequis synthétiques vérifiés ; **2** =
refus/non-vérifié, avec commande, code et stderr originaux dans le JSON. La
répétition ne modifie ni profil, ni sysctl, ni données de production ; seuls
des fichiers temporaires synthétiques sont créés et supprimés. Ne pas traiter
le succès de cette sonde comme une recette applicative ou une validation de
la copie utilisateur.

Les journaux noyau ou `aa-status` peuvent être interdits à l'utilisateur.
Le script conserve cet échec, sans élévation automatique. Un administrateur
peut compléter le relevé, localement :

```sh
cat /etc/os-release
bwrap --version
apparmor_parser --version
sudo aa-status
sysctl kernel.apparmor_restrict_unprivileged_userns kernel.unprivileged_userns_clone
sysctl user.max_user_namespaces user.max_net_namespaces
sudo journalctl -k -b --no-pager | grep -E 'apparmor=.*(bwrap|userns|uid_map)'
unshare --user --map-root-user true
```

Le dernier test est un diagnostic distinct : le profil de `/usr/bin/bwrap`
ne donne pas de permission à `/usr/bin/unshare`. Son échec ne prouve donc pas
l'échec de Bubblewrap correctement profilé. Ne pas publier les journaux de
l'hôte sans les examiner.

| Observation réelle | Interprétation / action |
| --- | --- |
| `loopback: Failed RTM_NEWADDR: Operation not permitted` | Vérifier les événements AppArmor correspondants, notamment `net_admin` au bootstrap. Ne pas ajouter `--share-net`. |
| `uid_map`, `setpcap` ou `userns_create` refusé | Examiner profil appliqué, état enforce et restriction userns. Ne pas conclure à une cause unique sans audit : conteneur/seccomp/limite de namespaces peuvent aussi interdire l'opération. |
| `--ro-bind-fd` absent de `bwrap --help` | Version incompatible avec la lecture sans changement d'atime des liens de production. Bubblewrap 0.9.0 sans backport est insuffisant ; utiliser une version maintenue fournissant cette fonction (0.10+ ou backport distributeur), puis relancer la sonde. Aucun remplacement par `readlink` direct sur production. |
| `unpriv_bwrap` absent, complain ou unconfined | Permissions enfant non démontrées : échec explicite, ne pas commencer la recette. |
| `child_capability_denial_unverified` | Bootstrap et état initial ne suffisent pas : l'enfant a pu créer ses namespaces ou le refus de permission n'est pas attesté. Examiner le résultat exact et les overrides locaux ; ne pas élargir les permissions pour faire passer le test. |
| Sonde réussie, recette refusée | Examiner l'erreur réelle de recette : permissions/FS, source concurrente ou lien dangereux restent des refus indépendants. |

## Profil restrictif : voie distribuée, revue administrateur

Ubuntu explique que son profil userns par défaut permet la création du
namespace mais refuse les capabilities suivantes. Une simple règle `userns,`
dans un profil permissif ne constitue pas notre solution définitive.

Le profil upstream **bwrap-userns-restrict** sépare deux phases : Bubblewrap
peut utiliser les capabilities nécessaires à son bootstrap (montages,
UID mapping, loopback) ; les exécutables lancés sont empilés sous
**unpriv_bwrap**, qui refuse les capabilities. Les règles de fichiers/réseau
restent volontairement larges pour les consommateurs de Bubblewrap ; le
confinement des fichiers et du réseau de la recette reste imposé par ses
montages et namespaces. Ce profil n'est donc pas une politique de chemins
Cifereca et n'autorise pas de privilèges hôte à l'application. Il affecte tous
les usages de `/usr/bin/bwrap`, y compris Flatpak et les bootstrap imbriqués :
évaluer ces consommateurs avant installation.

Ne pas adapter arbitrairement le profil upstream master (actuellement ABI
différente) à AppArmor 4.0. Utiliser uniquement celui du paquet Ubuntu de
l'hôte, vérifier son contenu complet et les inclusions locales. Le projet ne
fournit pas d'installateur privilégié ni de profil maison non validé : si ce
profil distribué est absent, incompatible ou insuffisant, **arrêt explicite**
et intervention administrateur/distributeur. Pas de bascule permissive.

Installation proposée, **à exécuter uniquement par l'administrateur de la
machine de recette**, après revue. Les commandes ne sont jamais exécutées
par `recette.py` ou le doctor :

```sh
sudo apt-get update
sudo apt-get install bubblewrap apparmor-profiles apparmor-utils python3-venv dbus-x11
profile=/usr/share/apparmor/extra-profiles/bwrap-userns-restrict
target=/etc/apparmor.d/bwrap-userns-restrict
test -f "$profile" || exit 1
cat "$profile"
sha256sum "$profile"
sudo grep -R -nE '/usr/bin/bwrap|profile (bwrap|unpriv_bwrap)' /etc/apparmor.d
sudo apparmor_parser --skip-kernel-load --skip-cache "$profile"
```

Consigner le hash et les profils existants. Refuser `flags=(unconfined)`,
`default_allow`, `complain`, l'absence de refus des capabilities enfant, ou
une inclusion locale qui les réautorise. Examiner notamment
`/etc/apparmor.d/local/bwrap-userns-restrict` et `local/unpriv_bwrap`.
Un autre profil attaché au même binaire doit être arbitré avant chargement ;
ne pas écraser ni décharger automatiquement la politique d'un tiers.

Si aucun profil concurrent n'existe et la revue est satisfaisante :

```sh
# Idempotent pour le même contenu ; une différence existante impose l'arrêt.
if sudo test -e "$target"; then
    sudo cmp -- "$profile" "$target" || exit 1
else
    sudo install -o root -g root -m 0644 -- "$profile" "$target"
fi
sudo apparmor_parser --replace "$target"
sudo aa-status
```

Le chargement est ciblé ; ne pas recharger tout AppArmor. En utilisateur
normal, refaire le doctor avec validation de syntaxe, puis le cycle réel :

```sh
python3 scripts/recette-doctor.py --require-apparmor --profile /etc/apparmor.d/bwrap-userns-restrict
python3 scripts/recette.py --ref main --recipe-root "/media/$USER/KDATAS/cifereca-tests" --target "/media/$USER/KDATAS/cifereca-tests/T1" --no-start
python3 scripts/recette.py verify "/media/$USER/KDATAS/cifereca-tests/T1"
"/media/$USER/KDATAS/cifereca-tests/T1/run.sh"
"/media/$USER/KDATAS/cifereca-tests/T1/stop.sh"
python3 scripts/recette.py destroy "/media/$USER/KDATAS/cifereca-tests/T1"
```

Si un test échoue, conserver la cause et **ne pas lancer hors confinement**.
Pour revenir après un **nouveau** profil installé par cette procédure : arrêter
ses consommateurs, vérifier que le fichier a toujours le hash relevé, puis
`sudo apparmor_parser --remove "$target"` et supprimer uniquement ce nouveau
fichier (`sudo rm -- "$target"`). Ne pas retirer un profil préexistant ou
modifié depuis. Le retour à l'absence de permission dédiée signifie que la
recette doit être à nouveau bloquée, pas que l'hôte doit être assoupli.

Un ancien contournement `flags=(unconfined)` doit être retiré/remplacé par
l'administrateur : relever le fichier exact et son profil, arrêter les
consommateurs, décharger **ce fichier seulement**, l'archiver hors de
`/etc/apparmor.d`, puis charger la politique restrictive validée. Ne pas
conserver les deux attachments. Vérifier ensuite restriction globale à 1,
`aa-status`, doctor et recette. Le projet ne touche pas automatiquement à ce
fichier inconnu ni à une éventuelle configuration sysctl de l'hôte.

## Matrice et preuves

| Environnement | Contrôle / statut attendu |
| --- | --- |
| CI sans privilèges ou conteneur avec procfs réduit | Tests déterministes du diagnostic ; refus structuré des sondes impossibles. Aucune validation hôte revendiquée. |
| Runner Ubuntu 24.04 administrable | Profil distribué vérifié/chargé en enforce, restriction globale conservée à 1, doctor et gate recette obligatoires. Montage tmpfs privé sous `/media` préparé par CI ; application lancée sans sudo. |
| Ubuntu 24.04 utilisateur, AppArmor 4.0.1, bwrap 0.9.0 | Non validé par les seules versions : sonde userns et fonction `--ro-bind-fd` obligatoires. Sans cette fonction/backport, blocage attendu avant copie des symlinks. |
| Volume externe utilisateur `/media/.../KDATAS` | Modes privés, flock/rename/exec, copie/config/SQLite/liens, confinement et destruction à démontrer sur le FS réel. tmpfs CI ne prouve pas ce volume. |

La gate existante de PR conserve les tests de recette système obligatoires ;
elle ne désactive plus le sysctl global. Le test monté pilote la recette depuis
l'utilisateur hôte, conformément au parcours réel, au lieu d'exiger un second
bootstrap depuis un enfant déjà privé de capabilities. Aucun privilège
supplémentaire n'est accordé à cet enfant pour faire passer ce test.

Pour réception #3646/#3642, consigner SHA main, versions, hash/profil chargé,
valeur sysctl avant/après, sorties doctor et cycle complet sur données
synthétiques puis sur la copie autorisée. Ne pas prétendre avoir exécuté le
cycle sur la machine utilisateur avant d'en avoir les résultats réels.

Références primaires : [Ubuntu 24.04 release notes, user namespaces](https://documentation.ubuntu.com/release-notes/24.04/),
[AppArmor Ubuntu](https://documentation.ubuntu.com/security/security-features/privilege-restriction/apparmor/),
[syntaxe Noble](https://manpages.ubuntu.com/manpages/noble/man5/apparmor.d.5.html),
[user namespaces Linux](https://man7.org/linux/man-pages/man7/user_namespaces.7.html),
[profil upstream](https://gitlab.com/apparmor/apparmor/-/blob/master/profiles/apparmor/profiles/extras/bwrap-userns-restrict).
