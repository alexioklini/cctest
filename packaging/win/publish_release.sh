#!/usr/bin/env bash
# Veroeffentlicht die Windows-Artefakte als GitHub-Release (Repo alexioklini/cctest,
# public) — die Online-Quelle des setup.exe. BEWUSST nur manuell aufzurufen;
# build_win.sh ruft das NIE automatisch.
#
#   ./publish_release.sh              Release win-v<VERSION> anlegen + Assets hochladen
#   ./publish_release.sh --portable   zusaetzlich das grosse portable Zip anhaengen
#
# Hochgeladen werden: BrainAgent-setup.exe, BrainAgent-win-manifest.json, alle
# im Manifest referenzierten Komponenten-Zips (content-addressed — unveraenderte
# Komponenten haben denselben Namen wie im Vorrelease) und beide Payload-Zips.
#
# WICHTIG fuer den Online-Modus des setup.exe: die Default-URL zeigt auf
# releases/LATEST/download — das Windows-Release muss also das neueste Release
# des Repos sein. Gibt es andersartige Releases, im Assistenten stattdessen die
# explizite URL verwenden:
#   https://github.com/alexioklini/cctest/releases/download/win-v<VERSION>
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DIST="$HERE/dist"
COMP="$DIST/components"
MANIFEST="$COMP/BrainAgent-win-manifest.json"
VERSION="$(grep '^VERSION' "$REPO/brain.py" | head -1 | cut -d'"' -f2)"
TAG="win-v${VERSION}"
SLUG="alexioklini/cctest"

command -v gh >/dev/null 2>&1 || { echo "ERROR: gh (GitHub CLI) fehlt" >&2; exit 1; }
[[ -f "$MANIFEST" ]] || { echo "ERROR: $MANIFEST fehlt — erst build_win.sh laufen lassen" >&2; exit 1; }
[[ -f "$DIST/BrainAgent-setup.exe" ]] || { echo "ERROR: BrainAgent-setup.exe fehlt (makensis installiert?)" >&2; exit 1; }

MAN_VER="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$MANIFEST")"
if [[ "$MAN_VER" != "$VERSION" ]]; then
  echo "ERROR: Manifest-Version ($MAN_VER) != brain.py VERSION ($VERSION) — Build veraltet?" >&2
  exit 1
fi

ASSETS=("$DIST/BrainAgent-setup.exe" "$MANIFEST"
        "$DIST/BrainAgent-${VERSION}-payload.zip"
        "$DIST/BrainAgent-${VERSION}-payload-app-only.zip")
while read -r f; do
  [[ -f "$COMP/$f" ]] || { echo "ERROR: Komponente $f fehlt in $COMP" >&2; exit 1; }
  ASSETS+=("$COMP/$f")
done < <(python3 -c 'import json,sys
for c in json.load(open(sys.argv[1]))["components"]: print(c["file"])' "$MANIFEST")
if [[ "${1:-}" == "--portable" ]]; then
  ASSETS+=("$DIST/BrainAgent-${VERSION}-win-x64.zip")
fi

echo "==> Release $TAG auf $SLUG — ${#ASSETS[@]} Assets"
if ! gh release view "$TAG" --repo "$SLUG" >/dev/null 2>&1; then
  gh release create "$TAG" --repo "$SLUG" \
    --title "Brain-Agent ${VERSION} (Windows x64)" \
    --notes "Windows-11-Deployment ${VERSION}. Installation/Update: BrainAgent-setup.exe (Quelle: dieses Release oder Payload-Zip). Details: packaging/win/README-Abschnitt im Bundle."
fi
gh release upload "$TAG" --repo "$SLUG" --clobber "${ASSETS[@]}"
echo "==> Fertig: https://github.com/$SLUG/releases/tag/$TAG"
