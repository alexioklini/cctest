# Brain-Agent — Setup-Engine (Stage 1). Wird von setup.exe (installer.nsi) mit
# Parametern aufgerufen, laeuft aber auch standalone in einer PowerShell.
# Aufgaben: Manifest besorgen (Offline-Payload-Zip/-Ordner ODER Online-BaseUrl),
# Komponenten sha256-verifiziert beschaffen, Delta gegen die Receipt-Datei
# (components.json) bestimmen, atomar-ish anwenden (.prev-Swap + Rollback),
# danach install.ps1 des Bundles ausfuehren.
# Kompatibilitaet: Windows PowerShell 5.1 (Win11-Standard), kein Admin.
param(
    [ValidateSet("install", "update")] [string]$Mode = "install",
    [ValidateSet("offline", "online")] [string]$Source = "offline",
    [string]$Payload = "",      # offline: Payload-Zip ODER Ordner mit manifest.json + Komponenten-Zips
    [string]$BaseUrl = "https://github.com/alexioklini/cctest/releases/latest/download",
    [string]$InstallDir = "",
    [int]$UpdateDeps = 1,       # 0 = nur App aktualisieren, geaenderte Deps behalten
    [int]$Minimal = 0,          # 1 = optionale Komponenten (websearch/qdrant/hfcache) weglassen
    [string]$MacminiIp = "",
    [string]$ModelId = "",
    [switch]$Silent
)
$ErrorActionPreference = "Stop"
try {
    [Net.ServicePointManager]::SecurityProtocol = `
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {}
Add-Type -AssemblyName System.IO.Compression.FileSystem

if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA "BrainAgent\bundle" }
$ManifestNames = @("manifest.json", "BrainAgent-win-manifest.json")
$TmpRoot = if ($env:TEMP) { $env:TEMP } else { [IO.Path]::GetTempPath() }
$Staging = Join-Path $TmpRoot ("brainagent-setup-" + (Get-Date -Format "yyyyMMdd-HHmmss"))

function Say([string]$msg, [string]$color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Fail([string]$msg) {
    Write-Host "FEHLER: $msg" -ForegroundColor Red
    if (-not $Silent) { Write-Host "(Fenster kann geschlossen werden)" }
    exit 1
}
function Get-Sha([string]$path) { (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLowerInvariant() }

function Download-File([string]$url, [string]$dest, [switch]$Quiet) {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue   # Win11 liefert curl.exe mit
    if ($curl) {
        $cargs = @("-f", "-L", "--retry", "3", "--retry-delay", "2", "-o", $dest, $url)
        if ($Quiet) { $cargs = @("-s") + $cargs }
        & $curl.Source @cargs
        if ($LASTEXITCODE -ne 0) { throw "Download fehlgeschlagen: $url (curl rc=$LASTEXITCODE)" }
    } else {
        $old = $ProgressPreference; $ProgressPreference = "SilentlyContinue"
        try { Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing } finally { $ProgressPreference = $old }
    }
    if (-not (Test-Path $dest)) { throw "Download fehlgeschlagen: $url" }
}

# Entpacken mit Overwrite (Expand-Archive kann das auf 5.1 nicht sauber),
# Zip-Slip-Guard und \\?\-Prefix als Langpfad-Versicherung.
function Extract-Zip([string]$zipPath, [string]$destRoot) {
    $zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $destFull = [IO.Path]::GetFullPath($destRoot + [IO.Path]::DirectorySeparatorChar)
        foreach ($e in $zip.Entries) {
            if ($e.FullName.EndsWith("/")) { continue }
            $rel = $e.FullName -replace "/", "\"
            $target = [IO.Path]::GetFullPath((Join-Path $destRoot $rel))
            if (-not $target.StartsWith($destFull, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Unsicherer Zip-Eintrag: $($e.FullName)"
            }
            $dir = Split-Path $target -Parent
            # \\?\-Langpfad-Versicherung NUR unter Windows (auf anderen Hosts
            # waere das Praefix ein relativer Pfad) — greift z. B. bei sehr
            # langen Benutzernamen/InstallDirs jenseits MAX_PATH.
            $isWin = ([IO.Path]::DirectorySeparatorChar -eq "\")
            if ($isWin -and ($dir.Length -ge 248)) { $dir = "\\?\" + $dir }
            [void][IO.Directory]::CreateDirectory($dir)
            if ($isWin -and ($target.Length -ge 248)) { $target = "\\?\" + $target }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $target, $true)
        }
    } finally { $zip.Dispose() }
}

function Read-ZipEntryText([string]$zipPath, [string[]]$names) {
    $zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        foreach ($n in $names) {
            $e = $zip.Entries | Where-Object { $_.FullName -eq $n } | Select-Object -First 1
            if ($e) {
                $sr = New-Object IO.StreamReader($e.Open())
                try { return $sr.ReadToEnd() } finally { $sr.Dispose() }
            }
        }
        return $null
    } finally { $zip.Dispose() }
}

function Extract-OneEntry([string]$zipPath, [string]$name, [string]$dest) {
    $zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
        $e = $zip.Entries | Where-Object { $_.FullName -eq $name } | Select-Object -First 1
        if (-not $e) { return $false }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($e, $dest, $true)
        return $true
    } finally { $zip.Dispose() }
}

Say "== Brain-Agent Setup (Stage 1) ==" "Cyan"
Say "Modus: $Mode | Quelle: $Source | Ziel: $InstallDir"
New-Item -ItemType Directory -Force -Path $Staging | Out-Null

# ---------------------------------------------------------- 1. Manifest holen
$manifestPath = Join-Path $Staging "manifest.json"
$payloadZip = $null
$payloadDir = $null
if ($Source -eq "offline") {
    if (-not $Payload) {
        # Standalone-Komfort: Payload neben dem Skript suchen
        $here = Split-Path $PSCommandPath -Parent
        $cand = Get-ChildItem -Path $here -Filter "BrainAgent-*-payload*.zip" -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | Select-Object -First 1
        if ($cand) { $Payload = $cand.FullName }
    }
    if (-not $Payload -or -not (Test-Path $Payload)) {
        Fail "Offline-Quelle: keine Payload-Datei gefunden. -Payload <BrainAgent-...-payload.zip> angeben (oder Ordner mit manifest.json + Komponenten-Zips)."
    }
    $item = Get-Item $Payload
    if ($item.PSIsContainer) {
        $payloadDir = $item.FullName
        $src = $ManifestNames | ForEach-Object { Join-Path $payloadDir $_ } |
            Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $src) { Fail "Kein manifest.json im Payload-Ordner $payloadDir." }
        Copy-Item $src $manifestPath
    } else {
        $payloadZip = $item.FullName
        $txt = Read-ZipEntryText $payloadZip $ManifestNames
        if (-not $txt) { Fail "Kein manifest.json im Payload-Zip $payloadZip." }
        Set-Content -Path $manifestPath -Value $txt -Encoding UTF8
    }
} else {
    $BaseUrl = $BaseUrl.TrimEnd("/")
    Say "Lade Manifest: $BaseUrl/BrainAgent-win-manifest.json"
    Download-File "$BaseUrl/BrainAgent-win-manifest.json" $manifestPath -Quiet
}
$man = (Get-Content $manifestPath -Raw -Encoding UTF8) | ConvertFrom-Json
if (-not $man.version -or -not $man.components) { Fail "Manifest unvollstaendig oder ungueltig." }
Say "Paket-Version: $($man.version)" "Green"

# ----------------------------------------------------- 2. Ist-Zustand ermitteln
$appVerFile = Join-Path $InstallDir "app\.version"
$existing = Test-Path $appVerFile
$curVer = ""
if ($existing) { $curVer = (Get-Content $appVerFile -TotalCount 1).Trim() }
if ($Mode -eq "update" -and -not $existing) {
    Fail "Keine bestehende Installation unter $InstallDir — bitte Neuinstallation waehlen."
}
if ($existing) { Say "Vorhandene Installation: v$curVer" }

$receiptPath = Join-Path $InstallDir "components.json"
$receipt = $null
if (($Mode -eq "update") -and (Test-Path $receiptPath)) {
    try { $receipt = (Get-Content $receiptPath -Raw -Encoding UTF8) | ConvertFrom-Json } catch { $receipt = $null }
}
function Get-ReceiptSha([string]$name) {
    if (-not $receipt -or -not $receipt.installed) { return $null }
    $p = $receipt.installed.PSObject.Properties[$name]
    if ($p) { return $p.Value }
    return $null
}
$receiptSkipped = @()
if ($receipt -and $receipt.skipped) { $receiptSkipped = @($receipt.skipped) }

# --------------------------------------------- 3. Komponenten-Delta bestimmen
$toApply = @()          # Manifest-Objekte, die beschafft + entpackt werden
$changedNames = @()     # davon: bereits vorhanden gewesene (fuer Venv-Recreate)
$final = @{}            # name -> sha  fuer die neue Receipt-Datei
$finalSkipped = @()
$removeDirs = @()       # Neuinstallation Minimal: Altbestand optionaler Komponenten
foreach ($c in $man.components) {
    $name = [string]$c.name
    $dirs = @($c.dirs)
    $present = $false
    foreach ($d in $dirs) { if (Test-Path (Join-Path $InstallDir $d)) { $present = $true } }
    $oldSha = Get-ReceiptSha $name

    if ($Mode -eq "install") {
        # Neuinstallation: Receipt ignorieren, alles Gewollte frisch setzen.
        if ((-not [bool]$c.required) -and ($Minimal -eq 1)) {
            $finalSkipped += $name
            if ($present) { $removeDirs += $dirs }
            Say "  - $name  uebersprungen (Minimal-Profil)"
            continue
        }
        $toApply += $c
        if ($present) { $changedNames += $name }
        continue
    }

    # Update:
    $wasSkipped = ($receiptSkipped -contains $name)
    if ($wasSkipped -and -not [bool]$c.required) {
        $finalSkipped += $name
        Say "  - $name  nicht installiert (Minimal-Profil) — bleibt weg"
        continue
    }
    if ((-not [bool]$c.required) -and (-not $present) -and (-not $oldSha)) {
        # Neue optionale Komponente eines spaeteren Releases: Wahl respektieren.
        $finalSkipped += $name
        Say "  - $name  (neu, optional) — nicht installiert; bei Bedarf Neuinstallation ausfuehren"
        continue
    }
    if ($oldSha -and ($oldSha -eq $c.sha256) -and $present) {
        $final[$name] = $c.sha256
        Say "  - $name  unveraendert — uebersprungen"
        continue
    }
    if (($name -ne "app") -and ($UpdateDeps -eq 0) -and $present) {
        $final[$name] = $oldSha
        Say "  ! $name  hat sich geaendert, wird aber NICHT aktualisiert ('Abhaengigkeiten aktualisieren' ist abgewaehlt)" "Yellow"
        continue
    }
    $toApply += $c
    if ($present) { $changedNames += $name }
}

if ($toApply.Count -eq 0) {
    Say ""
    Say "Alles aktuell (v$($man.version)) — nichts zu tun." "Green"
    Remove-Item -Recurse -Force $Staging -ErrorAction SilentlyContinue
    exit 0
}
Say ""
Say ("Wird installiert/aktualisiert: " + (($toApply | ForEach-Object { $_.name }) -join ", "))

# ------------------------------------------- 4. Beschaffen + sha256-verifizieren
foreach ($c in $toApply) {
    $dest = Join-Path $Staging $c.file
    if ($payloadDir) {
        $src = Join-Path $payloadDir $c.file
        if (-not (Test-Path $src)) {
            Fail "Komponente '$($c.name)' fehlt im Payload-Ordner ($($c.file)). Volles Payload verwenden oder 'Abhaengigkeiten aktualisieren' abwaehlen."
        }
        Copy-Item $src $dest
    } elseif ($payloadZip) {
        if (-not (Extract-OneEntry $payloadZip $c.file $dest)) {
            Fail "Komponente '$($c.name)' fehlt im Payload-Zip ($($c.file)). Volles Payload verwenden oder 'Abhaengigkeiten aktualisieren' abwaehlen."
        }
    } else {
        Say ("Lade " + $c.file + " (" + [math]::Round($c.size / 1MB) + " MB)...")
        Download-File "$BaseUrl/$($c.file)" $dest
    }
    $sha = Get-Sha $dest
    if ($sha -ne $c.sha256) { Fail "SHA256-Mismatch bei '$($c.name)': erwartet $($c.sha256), erhalten $sha." }
    Say "  ok: $($c.name) verifiziert"
}

# --------------------------------- 5. Laufenden Server stoppen (falls vorhanden)
if ($existing) {
    $stopBat = Join-Path $InstallDir "stop.bat"
    if (Test-Path $stopBat) {
        Say "Stoppe laufenden Brain-Agent (falls aktiv)..."
        & cmd.exe /c "`"$stopBat`"" | Out-Null
        Start-Sleep -Seconds 2
    }
}

# --------------------------------------- 6. Anwenden (.prev-Swap mit Rollback)
$renamed = @()
try {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    foreach ($dirsToDrop in $removeDirs) {
        $p = Join-Path $InstallDir $dirsToDrop
        if (Test-Path $p) { Say "Entferne $dirsToDrop (Minimal-Profil)..."; Remove-Item -Recurse -Force $p }
    }
    foreach ($c in $toApply) {
        foreach ($d in @($c.dirs)) {
            $orig = Join-Path $InstallDir $d
            if (Test-Path $orig) {
                $prev = "$orig.prev-setup"
                if (Test-Path $prev) { Remove-Item -Recurse -Force $prev }
                Rename-Item -Path $orig -NewName (Split-Path $prev -Leaf)
                $renamed += @{ orig = $orig; prev = $prev }
            }
        }
        Say "Entpacke $($c.name)..."
        # Hinweis: Top-Level-Dateien der app-Komponente (install.ps1, *.bat, README)
        # werden direkt ueberschrieben — nur die dirs laufen ueber den .prev-Swap.
        Extract-Zip (Join-Path $Staging $c.file) $InstallDir
        $final[[string]$c.name] = $c.sha256
    }
} catch {
    Say "FEHLER beim Anwenden: $($_.Exception.Message)" "Red"
    Say "Stelle vorherigen Stand wieder her..." "Yellow"
    foreach ($r in $renamed) {
        if (Test-Path $r.orig) { Remove-Item -Recurse -Force $r.orig -ErrorAction SilentlyContinue }
        if (Test-Path $r.prev) { Rename-Item -Path $r.prev -NewName (Split-Path $r.orig -Leaf) -ErrorAction SilentlyContinue }
    }
    Remove-Item -Recurse -Force $Staging -ErrorAction SilentlyContinue
    Fail "Anwenden fehlgeschlagen — vorheriger Stand wiederhergestellt."
}
foreach ($r in $renamed) {
    if (Test-Path $r.prev) { Remove-Item -Recurse -Force $r.prev -ErrorAction SilentlyContinue }
}

# ------------------------------------ 7. Receipt + Registry-Version schreiben
$receiptObj = [ordered]@{
    schema    = 1
    version   = $man.version
    installed = $final
    skipped   = @($finalSkipped | Sort-Object -Unique)
}
$receiptObj | ConvertTo-Json -Depth 5 | Set-Content -Path $receiptPath -Encoding UTF8

# Nur pflegen, wenn setup.exe den Uninstall-Key angelegt hat (Standalone-/
# Portable-Nutzung soll keine Registry-Eintraege erzeugen). try/catch: ein
# Registry-Problem darf eine sonst fertige Installation nicht mehr kippen.
try {
    $unKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainAgent"
    if (Test-Path $unKey) {
        Set-ItemProperty -Path $unKey -Name "DisplayName" -Value "Brain-Agent $($man.version)"
        Set-ItemProperty -Path $unKey -Name "DisplayVersion" -Value ([string]$man.version)
    }
} catch {
    Say "Hinweis: Registry-Version konnte nicht gesetzt werden ($($_.Exception.Message))" "Yellow"
}

# ----------------------------------------------- 8. install.ps1 des Bundles
$installPs = Join-Path $InstallDir "install.ps1"
if (-not (Test-Path $installPs)) { Fail "install.ps1 fehlt im Bundle (app-Komponente unvollstaendig?)." }
$psArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $installPs)
if ($MacminiIp) { $psArgs += @("-MacminiIp", $MacminiIp) }
if ($ModelId) { $psArgs += @("-ModelId", $ModelId) }
if ($Silent) { $psArgs += "-Silent" }
if ($Mode -eq "update") { $psArgs += "-Update" }
$skipAll = @($finalSkipped | Sort-Object -Unique)
if ($skipAll.Count) { $psArgs += @("-SkipComponents", ($skipAll -join ",")) }
$recreate = @()
# Venvs neu aufbauen, wenn ihr Site-Payload ODER das Bundle-Python (auf das
# ihre pyvenv.cfg zeigt) ausgetauscht wurde.
if (($changedNames -contains "websearch") -or ($changedNames -contains "python")) {
    $recreate += @("searxng", "crawl4ai")
}
if ($recreate.Count) { $psArgs += @("-RecreateVenvs", ($recreate -join ",")) }
& powershell.exe @psArgs
if ($LASTEXITCODE -ne 0) { Fail "install.ps1 fehlgeschlagen (rc=$LASTEXITCODE)." }

Remove-Item -Recurse -Force $Staging -ErrorAction SilentlyContinue
Say ""
if ($Mode -eq "update") {
    Say "== Update auf v$($man.version) abgeschlossen ==" "Cyan"
} else {
    Say "== Installation v$($man.version) abgeschlossen ==" "Cyan"
}
Say "Start: BrainAgent.bat (Startmenue: 'Brain-Agent starten')"
exit 0
