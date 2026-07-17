# Brain-Agent — Windows-11-Installation (Bank-Testausrollung).
# Einmalig ausfuehren:  powershell -ExecutionPolicy Bypass -File install.ps1
# OFFLINE-FIRST: alle Assets liegen im Bundle; Netz wird nur angefasst, wenn
# ein Asset fehlt (Bank-Firewall kann PyPI/CDNs blocken).
# Keine Admin-Rechte noetig (Ausnahme: Firewall-Regel + ODBC-MSI, s. Hinweise).
param(
    [string]$MacminiIp = "",
    [string]$ModelId = "",
    [switch]$Silent
)
$ErrorActionPreference = "Stop"
$Bundle = $PSScriptRoot
$Data = if ($env:BRAIN_DATA_DIR) { $env:BRAIN_DATA_DIR } else { Join-Path $env:LOCALAPPDATA "BrainAgent" }
$Py = Join-Path $Bundle "python\python.exe"

Write-Host "== Brain-Agent Installation ==" -ForegroundColor Cyan
Write-Host "Bundle: $Bundle"
Write-Host "Daten:  $Data"

if (-not (Test-Path $Py)) { throw "python\python.exe fehlt im Bundle." }

# ---- 1. Datenverzeichnis aus app\ befuellen (config.json nie ueberschreiben)
New-Item -ItemType Directory -Force -Path $Data | Out-Null
robocopy (Join-Path $Bundle "app") $Data /E /NFL /NDL /NJH /NJS /XF config.json searxng_settings.yml | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy app -> $Data fehlgeschlagen (rc=$LASTEXITCODE)" }
$cfgPath = Join-Path $Data "config.json"
$freshConfig = -not (Test-Path $cfgPath)
if ($freshConfig) { Copy-Item (Join-Path $Bundle "app\config.json") $cfgPath }
$sxPath = Join-Path $Data "searxng_settings.yml"
if (-not (Test-Path $sxPath)) { Copy-Item (Join-Path $Bundle "app\searxng_settings.yml") $sxPath }
Copy-Item (Join-Path $Bundle "app\.version") (Join-Path $Data ".brain-bundle-version") -Force

# ---- 2. Mac-mini-Endpunkt abfragen + config.json patchen
if ($freshConfig) {
    if (-not $MacminiIp -and -not $Silent) {
        $MacminiIp = Read-Host "IP des Mac mini M4 (oMLX-Server) [192.168.1.214]"
    }
    if (-not $MacminiIp) { $MacminiIp = "192.168.1.214" }
    if (-not $ModelId -and -not $Silent) {
        $ModelId = Read-Host "Modell-ID auf dem Mac mini (z.B. gemma-4-26B-it-qat-oQ4-fp16, leer = spaeter in den Einstellungen setzen)"
    }
    $patch = @"
import json, secrets, sys
cfg_path, data_dir, ip, model = sys.argv[1:5]
cfg = json.load(open(cfg_path, encoding='utf-8'))
prov = cfg['providers']['Lokal']
prov['base_url'] = prov['base_url'].replace('MACMINI_IP', ip)
if model:
    cfg['default_model'] = model
    prov['default_model'] = model
if cfg['auth'].get('jwt_secret') == 'JWT_SECRET_PLACEHOLDER':
    cfg['auth']['jwt_secret'] = secrets.token_hex(32)
mp = cfg.setdefault('mempalace', {})
if mp.get('palace_path') in ('', 'PALACE_PATH_PLACEHOLDER'):
    import os
    mp['palace_path'] = os.path.join(data_dir, 'mempalace')
json.dump(cfg, open(cfg_path, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
sx = data_dir + '\\searxng_settings.yml'
try:
    t = open(sx, encoding='utf-8').read()
    if 'SEARXNG_SECRET_PLACEHOLDER' in t:
        open(sx, 'w', encoding='utf-8').write(
            t.replace('SEARXNG_SECRET_PLACEHOLDER', secrets.token_hex(32)))
except FileNotFoundError:
    pass
print('config.json gepatcht: Mac mini =', ip, '| Modell =', model or '(offen)')
"@
    & $Py -c $patch $cfgPath $Data $MacminiIp $ModelId
} else {
    Write-Host "config.json existiert bereits — wird NICHT ueberschrieben." -ForegroundColor Yellow
}

# ---- 3. Venvs fuer SearXNG + crawl4ai (offline aus dem Bundle)
function New-BrainVenv([string]$name, [string]$sitePayload, [string]$onlineFallback) {
    $venv = Join-Path $Data $name
    $venvPy = Join-Path $venv "Scripts\python.exe"
    if (Test-Path $venvPy) {
        Write-Host "$name existiert bereits — uebersprungen."
        return
    }
    Write-Host "Erzeuge $name..."
    & $Py -m venv $venv
    if (Test-Path $sitePayload) {
        Write-Host "  -> kopiere vorgepackte Pakete (offline)..."
        robocopy $sitePayload (Join-Path $venv "Lib\site-packages") /E /NFL /NDL /NJH /NJS | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy $sitePayload fehlgeschlagen" }
    } else {
        Write-Host "  !! Bundle-Payload fehlt ($sitePayload) — ONLINE-Fallback (braucht PyPI-Zugriff)..." -ForegroundColor Yellow
        & $venvPy -m pip install $onlineFallback.Split(" ")
        if ($LASTEXITCODE -ne 0) { throw "pip-Fallback fuer $name fehlgeschlagen" }
    }
}
New-BrainVenv ".venv_searxng" (Join-Path $Bundle "venv-site\searxng") "-r $Data\searxng\requirements.txt"
New-BrainVenv ".venv_crawl4ai" (Join-Path $Bundle "venv-site\crawl4ai") "crawl4ai==0.8.6 playwright==1.60.0 playwright-stealth==2.0.3"

# ---- 4. Playwright-Chromium (offline aus dem Bundle entpacken)
$mspw = Join-Path $env:LOCALAPPDATA "ms-playwright"
$revFile = Join-Path $Bundle "browsers\revisions.txt"
if (Test-Path $revFile) {
    $revs = @{}
    Get-Content $revFile | ForEach-Object {
        $k, $v = $_ -split "=", 2
        if ($k -and $v) { $revs[$k.Trim()] = $v.Trim() }
    }
    $targets = @(
        @{ zip = "chromium-win64.zip"; dir = "chromium-$($revs['chromium'])" },
        @{ zip = "chromium-headless-shell-win64.zip"; dir = "chromium_headless_shell-$($revs['chromium_headless_shell'])" }
    )
    foreach ($t in $targets) {
        $dest = Join-Path $mspw $t.dir
        $marker = Join-Path $dest "INSTALLATION_COMPLETE"
        if (Test-Path $marker) {
            Write-Host "$($t.dir) bereits installiert — uebersprungen."
            continue
        }
        $zip = Join-Path $Bundle "browsers\$($t.zip)"
        if (-not (Test-Path $zip)) {
            Write-Host "  !! $($t.zip) fehlt im Bundle — ONLINE-Fallback: playwright install chromium" -ForegroundColor Yellow
            & (Join-Path $Data ".venv_crawl4ai\Scripts\python.exe") -m playwright install chromium
            break
        }
        Write-Host "Entpacke $($t.zip) -> $dest ..."
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        Expand-Archive -Path $zip -DestinationPath $dest -Force
        New-Item -ItemType File -Force -Path $marker | Out-Null
    }
} else {
    Write-Host "browsers\revisions.txt fehlt — ONLINE-Fallback: playwright install chromium" -ForegroundColor Yellow
    & (Join-Path $Data ".venv_crawl4ai\Scripts\python.exe") -m playwright install chromium
}

# ---- 5. Erreichbarkeit Mac mini pruefen
$cfgNow = Get-Content $cfgPath -Raw | ConvertFrom-Json
$baseUrl = $cfgNow.providers.Lokal.base_url
try {
    $r = Invoke-WebRequest -Uri "$baseUrl/models" -TimeoutSec 5 -UseBasicParsing
    Write-Host "Mac mini erreichbar: $baseUrl (HTTP $($r.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "WARNUNG: Mac mini unter $baseUrl NICHT erreichbar — IP/Firewall pruefen (oMLX muss auf 0.0.0.0:8000 lauschen)." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "== Installation abgeschlossen ==" -ForegroundColor Cyan
Write-Host "Start:  BrainAgent.bat   |   Stopp:  stop.bat"
Write-Host "Web-UI: http://localhost:8420  (initial admin / admin — Passwort aendern!)"
Write-Host ""
Write-Host "Optionale Schritte (Admin-Rechte):"
Write-Host " - LAN-Freigabe: netsh advfirewall firewall add rule name=""BrainAgent"" dir=in action=allow protocol=TCP localport=8420"
Write-Host " - MSSQL (db_query): 'ODBC Driver 17 for SQL Server' MSI installieren."
