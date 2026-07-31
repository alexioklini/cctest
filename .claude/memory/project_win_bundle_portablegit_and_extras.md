---
name: project_win_bundle_portablegit_and_extras
description: "v9.377 Win-Bundle — externe Voraussetzungen mitgeliefert (Node/mermaid/yt-dlp gebündelt, Tesseract/LibreOffice/R Installer beigelegt); MinGit→PortableGit weil MinGit KEIN bash.exe hat; Build braucht 7z. Build erfolgreich, Win11-Live-Test offen."
metadata: 
  node_type: memory
  type: project
  originSessionId: 19c5122c-9d41-4c49-9f0c-8ce978dafd36
  modified: 2026-07-18T18:13:01.495Z
---

**v9.377.0** (2026-07-18): Externe Voraussetzungen ins Win11-Bundle geholt (User: "warum nicht auch die externen Voraussetzungen ins Win11 bundle mitnehmen (bis auf odbc)"). Gruppe 4 der KOMPONENTEN_MATRIX war komplett "extern/manuell"; jetzt nach Redistribution-Fähigkeit aufgeteilt.

**Zwei neue optionale Komponenten** (`required=false` → Minimal-Profil + App-only-Update überspringen sie; `setup_stage1.ps1` entscheidet manifest-generisch, KEIN NSIS-Eingriff):
- **`tools`** (~161 MB gezippt): Node-24.18-LTS portable + `yt-dlp.exe` + **win32-x64 cross-installiertes mermaid-cli**. Das Repo-`diagram_render/node_modules` ist macOS-gebaut (`@napi-rs/canvas-darwin-arm64`) und auf Win nicht lauffähig → `npm install --os=win32 --cpu=x64 --no-audit --no-fund` + `PUPPETEER_SKIP_DOWNLOAD=1` zieht `@napi-rs/canvas-win32-x64-msvc` und lässt das macOS-Chromium weg. render_diagram nutzt am Client das OHNEHIN geladene Playwright-Chromium via `PUPPETEER_EXECUTABLE_PATH` (kein Browser-Zweitdownload).
- **`installers`** (~481 MB): Tesseract-5.4-Setup (UB-Mannheim NSIS), LibreOffice-26.2.4-MSI, R-4.6.1-Setup (CRAN) als ORIGINAL-Installer — keine portablen Assets (System-Registrierung nötig), vom Operator bei Bedarf ausgeführt (`installers\README.txt`).

**NUR ODBC Driver 17 bleibt extern** — Microsoft-EULA verbietet Redistribution.

**KRITISCHER FUND — MinGit hat KEIN bash.exe.** MinGit 2.51.0 liefert nur `usr/bin/sh.exe` (2.5 MB = msys2-bash unter dem Namen sh) + `dash.exe`, KEIN `bash.exe`. `BrainAgent.bat` setzt aber `BRAIN_SHELL_PATH` auf `usr/bin/bash.exe` und führt `bash -l -c` aus → wäre auf dem Client nie resolvt. **Fix: MinGit → PortableGit-2.51.0** (`PortableGit-<ver>-64-bit.7z.exe`, 56 MB, hat echtes `usr/bin/bash.exe`). Latenter Bug seit Commit 745845f4 (MinGit-2.51.0-Commit wurde nie erfolgreich durchgebaut). Zielordner bleibt `mingit/` → install.ps1/BrainAgent.bat/Manifest unverändert.

**Build-Host braucht jetzt `7z`** (`brew install p7zip`) — PortableGit ist self-extracting `.7z.exe`, entpackt via `7z x -y -o<dir>`. Toolcheck in build_win.sh erzwingt es (fail-loud). `npm` fehlend → mermaid-cli wird übersprungen (Warnung, kein Abbruch).

**Heredoc-Falle**: Das README-Heredoc in build_win.sh ist unquoted (`<<README`, wegen `${VERSION}`) → Backticks im Text (```` ```mermaid ````, `` `bash -l -c` ``) wurden als Command-Substitution interpretiert ("bad substitution"). Backticks escapen (`\``) oder Formulierung ohne Backtick.

**Code**: `engine/tools/image_gen._mmdc_invocation` liest `DIAGRAM_RENDER_CLI` (Env-Override des cli.js-Pfads; unset = In-Repo-Pfad = Mac-Status-quo). `BrainAgent.bat` legt `tools\node`+`tools\bin` auf PATH, setzt `DIAGRAM_RENDER_CLI` + `PUPPETEER_EXECUTABLE_PATH` (dynamischer `ms-playwright\chromium-*\chrome-win64\chrome.exe`).

**Build erfolgreich** (2026-07-18, cross auf macOS): 3,5 GB Tree, 8 Komponenten, setup.exe 144K, Payload 2,0 GB. Verifiziert: bash.exe da, win32-Skia (keine darwin-Leftovers), installers vollständig, Manifest required-Flags korrekt.

**OFFEN: Win11-Live-Test** — echter Boot + render_diagram/OCR/execute_command auf Windows-Hardware steht noch aus (wie schon bei [[project_windows_deployment_package]]).

Siehe auch [[feedback_kv_cache_stability]] (image_gen-Edit Mac-neutral), [[feedback_version_two_places]], [[feedback_update_skill_before_push]].
