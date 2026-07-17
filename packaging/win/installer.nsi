; Brain-Agent — Windows-11-setup.exe (Bank-Testausrollung).
; Cross-kompiliert auf macOS via `brew install makensis`:
;   makensis -DVERSION=... -DSRCDIR=<bundle dir> -DOUTFILE=<setup.exe> installer.nsi
; Kein Admin noetig (RequestExecutionLevel user): installiert nach
; %LOCALAPPDATA%\BrainAgent\bundle, fuehrt install.ps1 aus (fragt Mac-mini-IP),
; legt Startmenue-/Desktop-Verknuepfungen + Uninstaller an.

Unicode true
!ifndef VERSION
  !define VERSION "0.0.0"
!endif

Name "Brain-Agent ${VERSION}"
OutFile "${OUTFILE}"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\BrainAgent\bundle"
; zlib: deutlich schnellerer Build als lzma bei ~2GB Nutzdaten, die zu einem
; grossen Teil bereits komprimiert sind (Chromium-Zips, Wheels, ONNX).
SetCompressor zlib

!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainAgent"

Page directory
Page instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${SRCDIR}\*"

  ; Einmalige Einrichtung (Datenverzeichnis, Venvs, Chromium, Mac-mini-IP).
  ; Laeuft in einem eigenen Konsolenfenster, damit die IP-Abfrage moeglich ist.
  ExecWait 'powershell -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\install.ps1"' $0
  DetailPrint "install.ps1 beendet (Code $0)"

  ; Verknuepfungen
  CreateDirectory "$SMPROGRAMS\Brain-Agent"
  CreateShortCut "$SMPROGRAMS\Brain-Agent\Brain-Agent starten.lnk" "$INSTDIR\BrainAgent.bat" "" "" 0
  CreateShortCut "$SMPROGRAMS\Brain-Agent\Brain-Agent stoppen.lnk" "$INSTDIR\stop.bat" "" "" 0
  CreateShortCut "$SMPROGRAMS\Brain-Agent\Brain-Agent Web-UI.lnk" "http://localhost:8420"
  CreateShortCut "$DESKTOP\Brain-Agent.lnk" "$INSTDIR\BrainAgent.bat" "" "" 0

  ; Uninstaller + Software-Liste (HKCU — kein Admin)
  WriteUninstaller "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "Brain-Agent ${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "Brain-Agent"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; Nur das Bundle entfernen — die DATEN unter %LOCALAPPDATA%\BrainAgent
  ; (Chats, Qdrant-Storage, config.json) bleiben bewusst erhalten.
  ExecWait '"$INSTDIR\stop.bat"'
  RMDir /r "$INSTDIR"
  Delete "$SMPROGRAMS\Brain-Agent\Brain-Agent starten.lnk"
  Delete "$SMPROGRAMS\Brain-Agent\Brain-Agent stoppen.lnk"
  Delete "$SMPROGRAMS\Brain-Agent\Brain-Agent Web-UI.lnk"
  RMDir "$SMPROGRAMS\Brain-Agent"
  Delete "$DESKTOP\Brain-Agent.lnk"
  DeleteRegKey HKCU "${UNINST_KEY}"
SectionEnd
