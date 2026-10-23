#!/usr/bin/env bash
# Explicit local tester install for the BnF-Prêt read-only UI (#2055).
# No BnF request, SSO, browser launch or download is triggered by this script.
set -euo pipefail

usage() {
  echo "Usage: bash scripts/test-bnf-pret-local.sh [--package-only]"
  echo "Builds the official plugin ZIP; by default installs/enables it using cifereca-cli."
}

if [[ $# -gt 1 || (${1:-} != "" && ${1:-} != "--package-only" && ${1:-} != "--help") ]]; then
  usage >&2; exit 2
fi
if [[ ${1:-} == "--help" ]]; then usage; exit 0; fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
python="${CIFERECA_BNF_PYTHON:-python3}"
if ! command -v "$python" >/dev/null 2>&1; then
  echo "Python 3 absent : configurer CIFERECA_BNF_PYTHON." >&2; exit 2
fi
if [[ ${1:-} != "--package-only" ]] && ! command -v cifereca-cli >/dev/null 2>&1; then
  echo "cifereca-cli introuvable : installer/activer d'abord le Core Cifereca." >&2; exit 2
fi
version="$("$python" - "$root/src/plugins/provider-bnf-pret/cifereca-plugin.json" <<'PY'
import json
import sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert manifest["package"]["id"] == "org.cifereca.provider.bnf-pret"
print(manifest["package"]["version"])
PY
)"
archive="$root/dist/cifereca-plugin-provider-bnf-pret-${version}.zip"

"$python" "$root/scripts/package-plugin.py" "$root/src/plugins/provider-bnf-pret" --out "$root/dist"
if [[ ! -f "$archive" ]]; then
  echo "Le packaging n'a pas produit le ZIP attendu." >&2; exit 2
fi
if [[ ${1:-} == "--package-only" ]]; then
  echo "ZIP prêt : $archive"
  echo "Installer dans Cifereca via Plugins → Installer un ZIP."
  exit 0
fi

# Both writes are explicit: this script is run voluntarily to install + enable.
cifereca-cli plugin install "$archive" --replace
cifereca-cli plugin enable ui.page.bnf-pret
cifereca-cli plugin diagnose ui.page.bnf-pret
cat <<'DONE'
BnF Prêt Web installé et activé.
Redémarrer l'application puis ouvrir Configuration → BnF Prêt (lecture expérimentale).
Chromium/Playwright ne sont pas installés par le packaging.
Préparer Chromium dans Plugins → Browser Recorder → Installer Chromium…
Aucun emprunt ni téléchargement n'est actuellement automatisé.
DONE
