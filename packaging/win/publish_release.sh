#!/usr/bin/env bash
# Veroeffentlicht die Windows-Artefakte als GitHub-Release (Repo alexioklini/cctest,
# public) — die Online-Quelle des setup.exe. BEWUSST nur manuell aufzurufen;
# build_win.sh ruft das NIE automatisch.
#
#   ./publish_release.sh              Release win-v<VERSION> anlegen + Online-Delta-Assets
#   ./publish_release.sh --airgap     zusaetzlich das grosse Airgap-payload.zip (~2 GB)
#   ./publish_release.sh --portable   zusaetzlich das portable win-x64.zip (NUR wenn <2 GiB!)
#
# DEFAULT-Upload (der Online-Modus des setup.exe braucht genau das): BrainAgent-setup.exe,
# BrainAgent-win-manifest.json, alle im Manifest referenzierten Komponenten-Zips
# (content-addressed — unveraenderte Komponenten haben denselben Namen wie im
# Vorrelease) und das kleine payload-app-only.zip. Alle unter dem GitHub-Limit.
#
# GITHUB-ASSET-LIMIT: 2 GiB PRO DATEI (harte Grenze — GitHub weist groessere Assets
# ab). Genau deshalb existiert die Komponenten-Zerlegung. Das portable win-x64.zip
# (~2 GB Tree, ein Stueck) und das Airgap-payload.zip liegen nah an bzw. ueber der
# Grenze — darum opt-in. Der Default bleibt bewusst schlank + immer < Limit; dieses
# Skript prueft jedes Asset vorab und bricht fail-loud ab, falls eins zu gross ist.
#
# WICHTIG fuer den Online-Modus des setup.exe: die Default-URL zeigt auf
# releases/LATEST/download — dieses Release wird darum mit --latest markiert (muss
# das neueste Release des Repos sein). Alternativ im Assistenten die explizite URL:
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

WANT_AIRGAP=0; WANT_PORTABLE=0
for arg in "$@"; do
  case "$arg" in
    --airgap)   WANT_AIRGAP=1 ;;
    --portable) WANT_PORTABLE=1 ;;
    *) echo "ERROR: unbekannte Option '$arg' (erlaubt: --airgap, --portable)" >&2; exit 1 ;;
  esac
done

# Online-Delta-Default: setup.exe + Manifest + alle Komponenten-Zips + das kleine
# app-only-Payload. Alle garantiert < 2 GiB (der Online-Modus braucht genau das).
ASSETS=("$DIST/BrainAgent-setup.exe" "$MANIFEST"
        "$DIST/BrainAgent-${VERSION}-payload-app-only.zip")
while read -r f; do
  [[ -f "$COMP/$f" ]] || { echo "ERROR: Komponente $f fehlt in $COMP" >&2; exit 1; }
  ASSETS+=("$COMP/$f")
done < <(python3 -c 'import json,sys
for c in json.load(open(sys.argv[1]))["components"]: print(c["file"])' "$MANIFEST")
# Opt-in: grosses Airgap-Vollpaket (nah am Limit) bzw. portabler Tree (evtl. > Limit).
[[ "$WANT_AIRGAP"   == 1 ]] && ASSETS+=("$DIST/BrainAgent-${VERSION}-payload.zip")
[[ "$WANT_PORTABLE" == 1 ]] && ASSETS+=("$DIST/BrainAgent-${VERSION}-win-x64.zip")

# Vorabpruefung: GitHub weist Assets > 2 GiB ab — lieber hier fail-loud als mitten
# im Upload. (Grenze exakt 2^31 Bytes = 2 GiB.)
GH_LIMIT=2147483648
for a in "${ASSETS[@]}"; do
  [[ -f "$a" ]] || { echo "ERROR: Asset fehlt: $a" >&2; exit 1; }
  sz=$(stat -f%z "$a")
  if (( sz > GH_LIMIT )); then
    echo "ERROR: $(basename "$a") ist $((sz/1024/1024)) MB > 2 GiB — GitHub lehnt das ab." >&2
    echo "       (Der Online-Delta-Default bleibt unter der Grenze; nur --portable/--airgap koennen sie reissen.)" >&2
    exit 1
  fi
done

RELEASE_EXISTS=0
echo "==> Release $TAG auf $SLUG — ${#ASSETS[@]} Kandidaten"
if ! gh release view "$TAG" --repo "$SLUG" >/dev/null 2>&1; then
  gh release create "$TAG" --repo "$SLUG" --latest \
    --title "Brain-Agent ${VERSION} (Windows x64)" \
    --notes "Windows-11-Deployment ${VERSION}. Installation/Update: BrainAgent-setup.exe (Quelle: dieses Release oder Payload-Zip). Details: packaging/win/README-Abschnitt im Bundle."
else
  RELEASE_EXISTS=1
  gh release edit "$TAG" --repo "$SLUG" --latest >/dev/null
fi

# Delta-Upload: Komponenten-Zips sind content-addressed (<name>-<sha>.zip) — liegt
# ein Asset mit exakt diesem Namen UND dieser Groesse schon im Release, ist der
# Inhalt identisch und der (Neu-)Upload reine Bandbreitenverschwendung → skip.
# NUR fuer den content-addressed Komponenten-Ordner: setup.exe/manifest.json/die
# Payload-Zips tragen bei GEAENDERTEM Inhalt DENSELBEN Namen, muessen also immer
# neu hoch (--clobber), sonst bliebe ein alter Stand liegen.
declare -A REMOTE_SIZE=()
if [[ "$RELEASE_EXISTS" == 1 ]]; then
  while IFS=$'\t' read -r rn rs; do
    [[ -n "$rn" ]] && REMOTE_SIZE["$rn"]="$rs"
  done < <(gh release view "$TAG" --repo "$SLUG" --json assets \
             -q '.assets[] | "\(.name)\t\(.size)"' 2>/dev/null)
fi

TO_UPLOAD=(); SKIPPED=0
for a in "${ASSETS[@]}"; do
  bn="$(basename "$a")"
  # Skip nur fuer content-addressed Komponenten-Zips (die aus $COMP).
  if [[ "$a" == "$COMP/"* && -n "${REMOTE_SIZE[$bn]:-}" && "${REMOTE_SIZE[$bn]}" == "$(stat -f%z "$a")" ]]; then
    echo "  skip (unveraendert): $bn"
    SKIPPED=$((SKIPPED+1))
  else
    TO_UPLOAD+=("$a")
  fi
done

echo "==> Lade ${#TO_UPLOAD[@]} Assets hoch (${SKIPPED} unveraendert uebersprungen)"
if (( ${#TO_UPLOAD[@]} > 0 )); then
  gh release upload "$TAG" --repo "$SLUG" --clobber "${TO_UPLOAD[@]}"
fi
echo "==> Fertig: https://github.com/$SLUG/releases/tag/$TAG"
