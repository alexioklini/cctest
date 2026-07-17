#!/usr/bin/env bash
# Build BrainAgent Windows x64 bundle for the bank test rollout.
# Runs on macOS — cross-fetches Windows wheels via uv, vendors the patched
# mempalace package, prefetches the ONNX embedding model, the Qdrant Windows
# binary and the Playwright Chromium builds so the install on the Windows
# client works OFFLINE (bank firewall may block PyPI/CDNs); install.ps1 only
# falls back to the network when a bundled asset is missing.
#
# Deployment model: the Windows 11 client runs the FULL server (multi-user,
# 10 test / 70 target users); all LLM inference stays on the Mac mini M4
# (oMLX, OpenAI-compatible, reached over the LAN — provider "Lokal").
#
# Outputs:
#   packaging/win/dist/BrainAgent-<version>-win-x64/                (portable tree)
#   packaging/win/dist/BrainAgent-<version>-win-x64.zip             (portable zip, Handweg)
#   packaging/win/dist/components/<name>-<sha12>.zip + Manifest     (Delta-Updates/Release)
#   packaging/win/dist/BrainAgent-<version>-payload.zip             (Airgap: Vollpaket)
#   packaging/win/dist/BrainAgent-<version>-payload-app-only.zip    (Airgap: nur App-Update)
#   packaging/win/dist/BrainAgent-setup.exe                         (kleiner Bootstrapper, NSIS)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUILD="$HERE/build"
DIST="$HERE/dist"
DOWN="$REPO/packaging/downloads"
REQS="$REPO/packaging/common/requirements.txt"
VERSION="$(grep '^VERSION' "$REPO/brain.py" | head -1 | cut -d'"' -f2)"
OUT_NAME="BrainAgent-${VERSION}-win-x64"
OUT_DIR="$DIST/$OUT_NAME"

# Windows bundle targets Python 3.13 (NOT 3.14): spaCy — the GDPR/PII NER
# scanner, a bank-core feature — has no cp314 wheels anywhere.
WIN_PY_VER="3.13"
WIN_PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260414/cpython-3.13.13%2B20260414-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
PY_TARBALL="$DOWN/python-win-x64-313.tar.gz"

QDRANT_VER="v1.18.2"
QDRANT_URL="https://github.com/qdrant/qdrant/releases/download/${QDRANT_VER}/qdrant-x86_64-pc-windows-msvc.zip"
QDRANT_ZIP="$DOWN/qdrant-${QDRANT_VER}-win-x64.zip"

HF_REPO="onnx-community/embeddinggemma-300m-ONNX"

MEMPALACE_VENV_PKG="$HOME/.mempalace/venv/lib/python3.14/site-packages/mempalace"

UV_X() {  # cross-resolve for the Windows target
  uv pip install --python-platform x86_64-pc-windows-msvc \
                 --python-version "$WIN_PY_VER" "$@"
}

echo "==> Brain Agent v${VERSION} — Windows x64 bundle (cross-built on macOS)"
for t in curl tar rsync zip uv unzip python3 git; do
  command -v "$t" >/dev/null 2>&1 || { echo "ERROR: missing tool: $t" >&2; exit 1; }
done
mkdir -p "$BUILD" "$DIST" "$DOWN"

# ---------------------------------------------------------------- downloads
if [[ ! -f "$PY_TARBALL" ]]; then
  echo "  -> Downloading Windows x64 Python ${WIN_PY_VER}..."
  curl -# -L -o "$PY_TARBALL" "$WIN_PY_URL"
fi
if [[ ! -f "$QDRANT_ZIP" ]]; then
  echo "  -> Downloading Qdrant ${QDRANT_VER} (windows-x64)..."
  curl -# -L -o "$QDRANT_ZIP" "$QDRANT_URL"
fi

# ------------------------------------------------------------ 1. Python 3.13
if [[ ! -f "$BUILD/python/python.exe" ]] \
   || ! grep -q "^${WIN_PY_VER}" "$BUILD/python/.py-ver" 2>/dev/null; then
  echo "  -> Extracting Windows Python ${WIN_PY_VER}..."
  rm -rf "$BUILD/python"
  tar -xzf "$PY_TARBALL" -C "$BUILD"
  echo "$WIN_PY_VER" > "$BUILD/python/.py-ver"
  rm -rf "$BUILD/python/Lib/site-packages" && mkdir -p "$BUILD/python/Lib/site-packages"
fi

# --------------------------------------------- 2. core wheels (cross-install)
SITE="$BUILD/python/Lib/site-packages"
if [[ ! -d "$SITE/mempalace" ]]; then
  echo "  -> Cross-installing core Windows wheels (cp313)..."
  UV_X --target "$SITE" -r "$REQS"
fi

# Vendor the FULLY PATCHED mempalace from the Mac venv over the pip copy —
# the wheel has none of the BRAIN-PATCHes (int8 quantization, model cache, …).
# Pure Python, so the cp314→cp313 move is safe; MLX imports are lazy inside
# the mlx-device branch (verified), Windows runs MEMPALACE_EMBEDDING_DEVICE=cpu.
if [[ ! -d "$MEMPALACE_VENV_PKG" ]]; then
  echo "ERROR: patched mempalace not found at $MEMPALACE_VENV_PKG" >&2
  exit 1
fi
echo "  -> Vendoring BRAIN-PATCHed mempalace from the Mac venv..."
rsync -a --delete --exclude="__pycache__" --exclude="*.pyc" \
  "$MEMPALACE_VENV_PKG/" "$SITE/mempalace/"

# ------------------------------------- 3. web-stack site-packages (offline)
# Cross-installed into standalone dirs; install.ps1 copies them into the
# venvs it creates on the client — zero PyPI access needed there.
VS="$BUILD/venv-site"
if [[ ! -d "$VS/searxng" ]]; then
  echo "  -> Cross-installing SearXNG deps (cp313)..."
  mkdir -p "$VS/searxng"
  UV_X --target "$VS/searxng" -r "$REPO/searxng/requirements.txt"
fi
if [[ ! -d "$VS/crawl4ai" ]]; then
  echo "  -> Cross-installing crawl4ai + playwright (cp313)..."
  mkdir -p "$VS/crawl4ai"
  UV_X --target "$VS/crawl4ai" crawl4ai==0.8.6 playwright==1.60.0 playwright-stealth==2.0.3
fi

# ------------------------------------------ 4. Playwright Chromium (offline)
# Playwright 1.60 serves chromium/win64 as CHROME-FOR-TESTING builds:
#   builds/cft/<browserVersion>/win64/chrome-win64.zip
#   builds/cft/<browserVersion>/win64/chrome-headless-shell-win64.zip
# (verified against the driver's coreBundle.js cftUrl map). The install dir
# names still use the playwright revision (chromium-<rev>/chrome-win64/...).
BROWSERS_JSON="$VS/crawl4ai/playwright/driver/package/browsers.json"
read -r CHROMIUM_REV HEADLESS_REV CFT_VER <<<"$(python3 - "$BROWSERS_JSON" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
b = {x["name"]: x for x in d["browsers"]}
c = b["chromium"]
h = b.get("chromium-headless-shell", c)
print(c["revision"], h["revision"], c["browserVersion"])
EOF
)"
[[ -n "$CHROMIUM_REV" && -n "$CFT_VER" ]] || {
  echo "ERROR: chromium revision/browserVersion not found in browsers.json" >&2; exit 1; }
echo "  -> Playwright Chromium: CfT $CFT_VER (rev $CHROMIUM_REV, headless shell rev $HEADLESS_REV)"

fetch_browser() {  # $1=path-under-host  $2=output file
  local out="$2" url host ok=""
  [[ -f "$out" ]] && return 0
  for host in "https://cdn.playwright.dev" \
              "https://cdn.playwright.dev/dbazure/download/playwright" \
              "https://playwright.azureedge.net"; do
    url="$host/$1"
    echo "     trying $url"
    if curl -f -# -L -o "$out.part" "$url"; then ok=1; break; fi
  done
  [[ -n "$ok" ]] || { echo "ERROR: could not fetch browser build $1" >&2; exit 1; }
  mv "$out.part" "$out"
}
fetch_browser "builds/cft/${CFT_VER}/win64/chrome-win64.zip" \
              "$DOWN/chromium-${CHROMIUM_REV}-win64.zip"
fetch_browser "builds/cft/${CFT_VER}/win64/chrome-headless-shell-win64.zip" \
              "$DOWN/chromium-headless-shell-${HEADLESS_REV}-win64.zip"

# --------------------------------------------- 5. HF embedding model (ONNX)
# mempalace cpu embedding = EmbeddinggemmaONNX via hf_hub_download; bundle the
# hub-cache layout so HF_HUB_OFFLINE=1 finds it (HF_HOME=<bundle>/hf-cache).
HF_CACHE="$BUILD/hf-cache/hub"
if [[ ! -d "$HF_CACHE/models--onnx-community--embeddinggemma-300m-ONNX" ]]; then
  echo "  -> Prefetching ONNX embedding model ($HF_REPO)..."
  mkdir -p "$HF_CACHE"
  uvx --from 'huggingface_hub[cli]' hf download "$HF_REPO" \
      onnx/model_quantized.onnx onnx/model_quantized.onnx_data tokenizer.json \
      --cache-dir "$HF_CACHE" >/dev/null
fi

# ------------------------------------------------------ 6. assemble the tree
echo "  -> Assembling portable tree..."
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/app"

# rsync -a (statt cp -R) erhaelt die mtimes aus dem Build-Cache — die
# Komponenten-Zips (Abschnitt 10) sind dadurch build-uebergreifend
# byte-stabil und Delta-Updates erkennen "unveraendert".
mkdir -p "$OUT_DIR/python"
rsync -a "$BUILD/python/" "$OUT_DIR/python/"
rm -f "$OUT_DIR/python/.py-ver"

# Brain source — allowlist (v9.365 layout: engine/handlers/server_lib/frontends)
for f in server.py brain.py server_daemons.py execution.py launcher.py \
         pseudonymizer.py; do
  cp "$REPO/$f" "$OUT_DIR/app/$f"
done
cp "$REPO/tools_config.sample.json" "$OUT_DIR/app/tools_config.json"
for d in engine handlers server_lib frontends web crawl4ai diagram_render; do
  rsync -a --exclude="__pycache__" --exclude="*.pyc" --exclude="node_modules" \
    "$REPO/$d/" "$OUT_DIR/app/$d/"
done
# Vendored SearXNG checkout (runs via `python -m searx.webapp` with cwd inside
# the checkout — same as the Mac supervisor; no editable install needed).
rsync -a --exclude="__pycache__" --exclude="*.pyc" --exclude=".git" \
  --exclude="node_modules" --exclude="tests" \
  "$REPO/searxng/" "$OUT_DIR/app/searxng/"

# agents/main skeleton = exactly the tracked files (runtime data is gitignored);
# legacy sample artifacts + migrated memory-item leftovers stay out of the bundle.
( cd "$REPO" && git ls-files agents/main ) \
  | grep -v -e '\.md\.migrated$' -e '^agents/main/artifacts/' \
  | while read -r rel; do
      mkdir -p "$OUT_DIR/app/$(dirname "$rel")"
      cp "$REPO/$rel" "$OUT_DIR/app/$rel"
    done

echo "${VERSION}" > "$OUT_DIR/app/.version"

# Qdrant (local vector DB on the Windows client; Brain only speaks REST to it)
mkdir -p "$OUT_DIR/qdrant"
unzip -qo "$QDRANT_ZIP" -d "$OUT_DIR/qdrant"
[[ -f "$OUT_DIR/qdrant/qdrant.exe" ]] || {
  # some release zips nest a folder — flatten
  found="$(find "$OUT_DIR/qdrant" -name qdrant.exe | head -1)"
  [[ -n "$found" ]] || { echo "ERROR: qdrant.exe not in zip" >&2; exit 1; }
  mv "$found" "$OUT_DIR/qdrant/qdrant.exe"
}

# HF cache, web-stack site-packages, browser builds
mkdir -p "$OUT_DIR/hf-cache"
rsync -a "$BUILD/hf-cache/hub" "$OUT_DIR/hf-cache/"
rsync -a "$VS" "$OUT_DIR/"
mkdir -p "$OUT_DIR/browsers"
cp -p "$DOWN/chromium-${CHROMIUM_REV}-win64.zip" "$OUT_DIR/browsers/chromium-win64.zip"
cp -p "$DOWN/chromium-headless-shell-${HEADLESS_REV}-win64.zip" \
   "$OUT_DIR/browsers/chromium-headless-shell-win64.zip"
cat > "$OUT_DIR/browsers/revisions.txt" <<EOF
chromium=$CHROMIUM_REV
chromium_headless_shell=$HEADLESS_REV
EOF
# Fixe mtime: die Datei liegt in der websearch-Komponente — ohne das wuerde
# jeder Build deren sha kippen und Delta-Updates laufen leer.
touch -t 202601010000 "$OUT_DIR/browsers/revisions.txt"

# ------------------------------------------------------- 7. config.json seed
# Multi-user server on the Windows client: bind 0.0.0.0 (10 test / 70 target
# users reach it over the LAN; firewall rule documented in README). LLMs on
# the Mac mini via provider "Lokal" (oMLX, is_local — install.ps1 patches IP
# + model id). GDPR scanner ON (bank core feature; spaCy NER works on cp313).
# OCR engine none (no MLX on Windows; opt-ins in README). Reranker OFF (needs
# torch/sentence_transformers — not bundled). searxng/crawl4ai ENABLED per
# rollout decision; venvs are created by install.ps1.
cat > "$OUT_DIR/app/config.json" <<'JSON'
{
  "providers": {
    "Lokal": {
      "base_url": "http://MACMINI_IP:8000/v1",
      "type": "openai",
      "api_keys": [{"name": "default", "key": "brain", "usage": "preferred"}],
      "is_local": true,
      "max_concurrent": 1,
      "supports_chat_template_kwargs": true,
      "_comment": "oMLX auf dem Mac mini M4 — install.ps1 ersetzt MACMINI_IP."
    }
  },
  "default_provider": "Lokal",
  "default_model": "",
  "server": {"host": "0.0.0.0", "port": 8420},
  "auth": {
    "enabled": true,
    "registration_enabled": false,
    "default_role": "user",
    "token_expiry_seconds": 86400,
    "jwt_secret": "JWT_SECRET_PLACEHOLDER"
  },
  "telegram": {"enabled": false},
  "ocr": {"engine": "none", "provider": "", "model": ""},
  "searxng": {
    "enabled": true,
    "auto_start": true,
    "url": "http://127.0.0.1:8088",
    "venv_python": ".venv_searxng/Scripts/python.exe",
    "settings_path": "searxng_settings.yml"
  },
  "crawl4ai": {
    "enabled": true,
    "auto_start": true,
    "url": "http://127.0.0.1:8422",
    "venv_python": ".venv_crawl4ai/Scripts/python.exe"
  },
  "gdpr_scanner": {"enabled": true},
  "mempalace": {
    "enabled": true,
    "palace_path": "PALACE_PATH_PLACEHOLDER",
    "embedding_device": "remote",
    "embedding_url": "http://MACMINI_IP:8000",
    "embedding_remote_model": "embeddinggemma-300m-bf16",
    "_embedding_comment": "remote = oMLX /v1/embeddings auf dem Mac mini (vektoridentisch zu MLX, cos=1.0); bei Ausfall latcht der Prozess auf lokales CPU-ONNX (Modell liegt im Bundle-hf-cache). Fuer rein lokales Embedding: embedding_device auf 'cpu' setzen.",
    "kg": {"enabled": false},
    "reranker": {"enabled": false},
    "mine": {"interval_seconds": 1800},
    "chat_sync": {"interval_seconds": 60}
  }
}
JSON

# SearXNG settings template (fresh secret_key per install via install.ps1)
cat > "$OUT_DIR/app/searxng_settings.yml" <<'YML'
# SearXNG override settings for brain-agent (Windows bundle).
use_default_settings: true

server:
  port: 8088
  bind_address: "127.0.0.1"
  secret_key: "SEARXNG_SECRET_PLACEHOLDER"
  limiter: false
  public_instance: false

search:
  formats:
    - html
    - json

# Curated engine pool (see the Mac production file for the measurement log).
engines:
  - name: bing
    disabled: true
  - name: brave
    disabled: false
  - name: google
    disabled: true
  - name: duckduckgo
    disabled: false
  - name: qwant
    disabled: true
  - name: mojeek
    disabled: false
  - name: presearch
    disabled: false
  - name: yep
    disabled: false
YML

# --------------------------------------------------- 8. scripts + README
cp "$HERE/install.ps1" "$OUT_DIR/install.ps1"
cp "$HERE/BrainAgent.bat.tmpl" "$OUT_DIR/BrainAgent.bat"
cp "$HERE/stop.bat.tmpl" "$OUT_DIR/stop.bat"
cp "$HERE/MACMINI_SETUP.md" "$OUT_DIR/MACMINI_SETUP.md"
cp "$HERE/WIN_FOOTPRINT_ANALYSIS.md" "$OUT_DIR/WIN_FOOTPRINT_ANALYSIS.md"

cat > "$OUT_DIR/README.txt" <<README
Brain Agent v${VERSION} — Windows x64 (Bank-Testausrollung)
===========================================================

Architektur: Dieser Windows-Client ist der SERVER (Mehrbenutzer, Web-UI auf
Port 8420). Alle Sprachmodelle UND das Gedaechtnis-Embedding laufen auf dem
Mac mini M4 im LAN (oMLX auf Port 8000) — der Client braucht dorthin
Netzwerkzugriff. Faellt der Mac mini aus, rechnet das Embedding automatisch
lokal weiter (CPU, langsamer); Chat braucht den Mac mini zwingend.

Schnellstart A — setup.exe (empfohlen)
--------------------------------------
1. BrainAgent-setup.exe starten; daneben liegt (airgapped) die Payload-Datei
   BrainAgent-<ver>-payload.zip — der Assistent findet sie automatisch.
   Alternativ Quelle "Online": laedt die Komponenten vom GitHub-Release
   bzw. einem internen Mirror (URL im Assistenten editierbar).
2. Der Assistent erkennt eine BESTEHENDE Installation und bietet dann ein
   Update an: es werden nur GEAENDERTE Komponenten geholt; config.json,
   Chats, Gedaechtnis und brain-env.bat werden nie angefasst. Fuer ein
   reines Versions-Update genuegt airgapped die kleine Datei
   BrainAgent-<ver>-payload-app-only.zip (App-Code ohne Dependencies).
3. "Minimal-Installation" (Checkbox, nur Neuinstallation): Websuche
   (SearXNG+crawl4ai+Chromium), Qdrant und der Embedding-Offline-Fallback
   bleiben weg (~0,9 statt ~2,1 GB) und laufen stattdessen auf dem Mac mini
   — Einrichtung dort: MACMINI_SETUP.md Abschnitt 6; Abwaegung:
   WIN_FOOTPRINT_ANALYSIS.md. Standard bleibt die Voll-Installation.
4. Danach: Startmenue -> "Brain-Agent starten" (oder BrainAgent.bat).
   Browser: http://<dieser-rechner>:8420 (initial admin / admin — Passwort
   sofort aendern). Beenden: stop.bat.
Silent/Automation:
   setup.exe /S [/MODE=install|update] [/SOURCE=offline|online]
            [/PAYLOAD=<zip>] [/URL=<base-url>] [/NODEPS] [/MINIMAL]
Online-Downloads respektieren HTTPS_PROXY (curl.exe).

Schnellstart B — portables Zip (Handweg, vollstaendig offline)
--------------------------------------------------------------
1. Ordner an einen beliebigen Ort entpacken (z. B. C:\\BrainAgent\\).
2. Rechtsklick auf install.ps1 -> "Mit PowerShell ausfuehren"
   (einmalig; fragt die IP des Mac mini und die Modell-ID ab).
   Vollstaendig OFFLINE moeglich — Internet nur als Fallback, falls ein
   Bundle-Asset fehlt.
3. Doppelklick auf BrainAgent.bat  ->  startet Qdrant + Server.
4. Browser: http://<dieser-rechner>:8420  (Login initial: admin / admin —
   Passwort sofort aendern.)
5. Beenden: stop.bat.

Damit andere Rechner im LAN zugreifen koennen (10+ Nutzer), einmalig als
Administrator eine Firewall-Freigabe anlegen:
  netsh advfirewall firewall add rule name="BrainAgent" dir=in action=allow protocol=TCP localport=8420

Datenablage
-----------
%LOCALAPPDATA%\\BrainAgent\\   (Override: Umgebungsvariable BRAIN_DATA_DIR)
Qdrant-Vektordaten: %LOCALAPPDATA%\\BrainAgent\\qdrant-storage\\

Voraussetzungen auf dem Mac mini (oMLX)
---------------------------------------
Vollstaendige Anleitung: MACMINI_SETUP.md (in diesem Ordner). Kurzform:
- oMLX lauscht auf 0.0.0.0:8000, gewuenschtes Chat-Modell geladen.
- Embedding-Modell registriert: mlx-community/embeddinggemma-300m-bf16 in
  ~/.omlx/models/mlx-community/ ablegen, dann im oMLX-Admin "Reload" (oder
  POST /admin/api/reload). Test: POST /v1/embeddings mit model
  "embeddinggemma-300m-bf16" muss 768-dim-Vektoren liefern.
- Was oMLX nicht kann (STT/TTS/Reranker), loest ein Zusatz-Inferencer auf
  dem Mac mini — siehe MACMINI_SETUP.md Abschnitt 4.

Grenzen unter Windows
---------------------
- OCR: kein MLX-OCR (Apple-only). Optionen: ocr.engine="local_vision"
  (Vision-Modell auf dem Mac mini) oder "mistral_ocr" (Cloud) in config.json.
- Sprach-Transkription/TTS: nur ueber einen erreichbaren Audio-Endpoint
  (Mac mini oder Cloud), kein lokales Whisper.
- Interaktives Projekt-Terminal: nicht verfuegbar (kein PTY unter Windows).
- Mermaid-Diagramme: benoetigen Node.js auf PATH (optional).
- MSSQL (db_query): "ODBC Driver 17 for SQL Server" MSI separat installieren
  (Admin-Rechte noetig) — siehe DATA_SOURCES_V2_PLAN.md Anhang B.
README

# CRLF for the .bat files + README
for f in BrainAgent.bat stop.bat README.txt; do
  awk 'BEGIN{RS="\n"; ORS="\r\n"} {print}' "$OUT_DIR/$f" > "$OUT_DIR/$f.crlf"
  mv "$OUT_DIR/$f.crlf" "$OUT_DIR/$f"
done

# --------------------------------------------------------------- 9. zip
echo "  -> Packaging zip..."
rm -f "$DIST/${OUT_NAME}.zip"
(cd "$DIST" && zip -qr "${OUT_NAME}.zip" "$OUT_NAME")

# ------------------------------- 10. Komponenten-Zips + Manifest + Payloads
# Update-Architektur (setup.exe v2): das Bundle wird in 5 Komponenten-Zips
# zerlegt (content-addressed: <name>-<sha12>.zip — unveraenderte Komponenten
# behalten Dateiname UND sha ueber Versionen hinweg, Delta-Updates laden nur
# Geaendertes). manifest.json ist der Vertrag mit setup_stage1.ps1.
echo "  -> Building component zips + manifest..."
COMP="$DIST/components"
mkdir -p "$COMP"
MANIFEST="$COMP/BrainAgent-win-manifest.json"
rm -f "$COMP/.entries"

comp_zip() {  # $1=name  $2..=Pfade relativ zu OUT_DIR (dirs und/oder files)
  local name="$1"; shift
  local tmp="$COMP/.tmp-${name}.zip"
  rm -f "$tmp"
  (cd "$OUT_DIR" && find "$@" \( -type f -o -type l \) | LC_ALL=C sort | zip -X -q "$tmp" -@)
  local sha; sha="$(shasum -a 256 "$tmp" | cut -d' ' -f1)"
  local fname="${name}-${sha:0:12}.zip"
  if [[ -f "$COMP/$fname" ]]; then rm -f "$tmp"; else mv "$tmp" "$COMP/$fname"; fi
  echo "$name|$fname|$sha|$(stat -f%z "$COMP/$fname")" >> "$COMP/.entries"
  echo "     $name -> $fname ($(du -sh "$COMP/$fname" | cut -f1))"
}
# Die Pfadlisten muessen zu den "dirs" im Manifest passen (Swap-Ziele von
# setup_stage1.ps1); Top-Level-Skripte reisen in der app-Komponente mit.
comp_zip app       app install.ps1 BrainAgent.bat stop.bat README.txt MACMINI_SETUP.md WIN_FOOTPRINT_ANALYSIS.md
comp_zip python    python
comp_zip websearch venv-site browsers
comp_zip qdrant    qdrant
comp_zip hfcache   hf-cache

python3 - "$COMP/.entries" "$MANIFEST" "$VERSION" <<'EOF'
import json, sys
entries_path, manifest_path, version = sys.argv[1:4]
META = {  # name -> (required, dirs, title)
    "app":       (True,  ["app"],                    "Brain-Agent Programmcode + Skripte"),
    "python":    (True,  ["python"],                 "Python 3.13 Runtime + Bibliotheken"),
    "websearch": (False, ["venv-site", "browsers"],  "Websuche lokal (SearXNG + crawl4ai + Chromium)"),
    "qdrant":    (False, ["qdrant"],                 "Qdrant Vektor-DB lokal"),
    "hfcache":   (False, ["hf-cache"],               "Embedding-Offline-Fallback (ONNX)"),
}
components = []
for line in open(entries_path, encoding="utf-8"):
    if not line.strip():
        continue
    name, fname, sha, size = line.strip().split("|")
    required, dirs, title = META[name]
    components.append({"name": name, "file": fname, "sha256": sha, "size": int(size),
                       "required": required, "dirs": dirs, "title": title})
assert len(components) == len(META), "component set mismatch"
json.dump({"product": "BrainAgent", "schema": 1, "version": version,
           "components": components},
          open(manifest_path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
print("     manifest:", manifest_path)
EOF
rm -f "$COMP/.entries"

# Payload-Dateien (die "zweite Datei" neben setup.exe fuer den Airgap-Weg):
#   -payload.zip           komplett (Erstinstallation + Deps-Updates)
#   -payload-app-only.zip  nur App — das leichtgewichtige Versions-Update
echo "  -> Building payload zips..."
PAYSTAGE="$COMP/.paystage"
rm -rf "$PAYSTAGE"; mkdir -p "$PAYSTAGE"
cp "$MANIFEST" "$PAYSTAGE/manifest.json"
APP_FILE=""
while IFS='|' read -r name fname; do
  ln "$COMP/$fname" "$PAYSTAGE/$fname"
  [[ "$name" == "app" ]] && APP_FILE="$fname"
done < <(python3 -c 'import json,sys
for c in json.load(open(sys.argv[1]))["components"]: print(c["name"] + "|" + c["file"])' "$MANIFEST")
rm -f "$DIST/BrainAgent-${VERSION}-payload.zip" "$DIST/BrainAgent-${VERSION}-payload-app-only.zip"
(cd "$PAYSTAGE" && zip -0 -X -q "$DIST/BrainAgent-${VERSION}-payload.zip" manifest.json *.zip)
(cd "$PAYSTAGE" && zip -0 -X -q "$DIST/BrainAgent-${VERSION}-payload-app-only.zip" manifest.json "$APP_FILE")
rm -rf "$PAYSTAGE"

# --------------------------------------------------- 11. setup.exe (klein)
# Versionsunabhaengiger Bootstrapper (~2 MB): enthaelt nur setup_stage1.ps1;
# Nutzdaten kommen aus Payload-Datei oder Online-Quelle. Muss nur neu verteilt
# werden, wenn sich die Installer-LOGIK aendert (SETUP_VERSION in installer.nsi).
if command -v makensis >/dev/null 2>&1; then
  echo "  -> Building setup.exe (NSIS bootstrapper)..."
  makensis -V2 -DVERSION="$VERSION" -DSTAGE1="$HERE/setup_stage1.ps1" \
           -DOUTFILE="$DIST/BrainAgent-setup.exe" \
           "$HERE/installer.nsi"
else
  echo "  !! makensis not found — skipping setup.exe (brew install makensis)"
fi

ZIP_SIZE=$(du -sh "$DIST/${OUT_NAME}.zip" | cut -f1)
DIR_SIZE=$(du -sh "$OUT_DIR" | cut -f1)
echo "==> Done:"
echo "    $OUT_DIR  ($DIR_SIZE uncompressed)"
echo "    $DIST/${OUT_NAME}.zip  ($ZIP_SIZE)  [portabler Handweg]"
echo "    $DIST/BrainAgent-${VERSION}-payload.zip  ($(du -sh "$DIST/BrainAgent-${VERSION}-payload.zip" | cut -f1))  [Airgap: Vollpaket]"
echo "    $DIST/BrainAgent-${VERSION}-payload-app-only.zip  ($(du -sh "$DIST/BrainAgent-${VERSION}-payload-app-only.zip" | cut -f1))  [Airgap: nur App-Update]"
echo "    $COMP/  (Komponenten + Manifest fuer GitHub-Release/Mirror)"
[[ -f "$DIST/BrainAgent-setup.exe" ]] && \
  echo "    $DIST/BrainAgent-setup.exe  ($(du -sh "$DIST/BrainAgent-setup.exe" | cut -f1))"
