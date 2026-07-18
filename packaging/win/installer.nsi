; Brain-Agent — Windows-11-setup.exe v2 (kleiner Bootstrapper, ~2 MB).
; Cross-kompiliert auf macOS via `brew install makensis`:
;   makensis -DVERSION=<brain-ver> -DSTAGE1=<setup_stage1.ps1> -DOUTFILE=<setup.exe> installer.nsi
;
; Enthaelt KEINE Nutzdaten mehr — nur die Setup-Engine (setup_stage1.ps1).
; Quellen der Programmdaten (Auswahl im Assistenten):
;   - Offline-Paketdatei  BrainAgent-<ver>-payload[.app-only].zip (airgapped)
;   - Online-Download     GitHub-Release (latest) oder interner Mirror (URL editierbar)
; Modi: Neuinstallation ODER Update einer bestehenden Installation (autodetektiert;
; Update laedt nur geaenderte Komponenten — Receipt-Vergleich in setup_stage1.ps1).
; Kein Admin noetig (RequestExecutionLevel user). Daten unter %LOCALAPPDATA%\BrainAgent
; (config.json/Chats/Qdrant) werden von Update UND Deinstallation nie angefasst.
;
; Silent: setup.exe /S [/MODE=install|update] [/SOURCE=offline|online]
;                    [/PAYLOAD=<zip>] [/URL=<base-url>] [/NODEPS] [/MINIMAL] [/D=<instdir>]

Unicode true
!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef SETUP_VERSION
  !define SETUP_VERSION "2.0.0"
!endif
!ifndef STAGE1
  !error "STAGE1 (Pfad zu setup_stage1.ps1) fehlt"
!endif
!ifndef OUTFILE
  !error "OUTFILE fehlt"
!endif
!define DEFAULT_URL "https://github.com/alexioklini/cctest/releases/latest/download"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\BrainAgent"

Name "Brain-Agent Setup"
OutFile "${OUTFILE}"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\BrainAgent\bundle"
SetCompressor /SOLID lzma

VIProductVersion "${SETUP_VERSION}.0"
VIAddVersionKey /LANG=1031 "ProductName" "Brain-Agent Setup"
VIAddVersionKey /LANG=1031 "FileDescription" "Brain-Agent Installations- und Update-Assistent"
VIAddVersionKey /LANG=1031 "FileVersion" "${SETUP_VERSION}"
VIAddVersionKey /LANG=1031 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1031 "LegalCopyright" "Brain-Agent"

!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!ifndef WS_GROUP
  !define WS_GROUP 0x00020000
!endif

Var Dialog
Var RadioInstall
Var RadioUpdate
Var RadioOffline
Var RadioOnline
Var TxtPayload
Var BtnBrowse
Var TxtUrl
Var ChkDeps
Var ChkMinimal
Var ModeSel        ; "install" | "update"
Var SourceSel      ; "offline" | "online"
Var PayloadPath
Var UrlValue
Var DepsFlag       ; "1" | "0"
Var MinimalFlag    ; "0" | "1"
Var HasExisting
Var DefaultsDone

!define MUI_WELCOMEPAGE_TITLE "Brain-Agent einrichten"
!define MUI_WELCOMEPAGE_TEXT "Dieser Assistent installiert Brain-Agent neu oder aktualisiert eine bestehende Installation.$\r$\n$\r$\nProgrammdaten kommen wahlweise aus einer Offline-Paketdatei (airgapped, USB) oder per Online-Download (GitHub-Release bzw. interner Mirror).$\r$\n$\r$\nBenutzerdaten (config.json, Chats, Gedaechtnis) bleiben bei Updates unangetastet."
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom OptionsPageCreate OptionsPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "German"

; ---------------------------------------------------------------- Defaults
; Fuellt NUR leere Variablen (CLI-Optionen aus .onInit haben Vorrang) —
; dadurch aus GUI- UND Silent-Pfad gefahrlos aufrufbar.
Function DetectDefaults
  StrCmp $DefaultsDone "1" ddone
  StrCpy $DefaultsDone "1"
  StrCpy $HasExisting "0"
  IfFileExists "$INSTDIR\app\.version" 0 +2
    StrCpy $HasExisting "1"
  ${If} $ModeSel == ""
    ${If} $HasExisting == "1"
      StrCpy $ModeSel "update"
    ${Else}
      StrCpy $ModeSel "install"
    ${EndIf}
  ${EndIf}
  ${If} $PayloadPath == ""
    FindFirst $0 $1 "$EXEDIR\BrainAgent-*-payload.zip"
    ${If} $1 != ""
      StrCpy $PayloadPath "$EXEDIR\$1"
    ${EndIf}
    FindClose $0
  ${EndIf}
  ${If} $PayloadPath == ""
    FindFirst $0 $1 "$EXEDIR\BrainAgent-*-payload-app-only.zip"
    ${If} $1 != ""
      StrCpy $PayloadPath "$EXEDIR\$1"
    ${EndIf}
    FindClose $0
  ${EndIf}
  ${If} $SourceSel == ""
    ${If} $PayloadPath == ""
      StrCpy $SourceSel "online"
    ${Else}
      StrCpy $SourceSel "offline"
    ${EndIf}
  ${EndIf}
ddone:
FunctionEnd

Function .onInit
  StrCpy $DepsFlag "1"
  StrCpy $MinimalFlag "0"
  StrCpy $UrlValue "${DEFAULT_URL}"
  StrCpy $DefaultsDone "0"
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/MODE=" $0
  IfErrors +2 0
    StrCpy $ModeSel $0
  ClearErrors
  ${GetOptions} $R0 "/SOURCE=" $0
  IfErrors +2 0
    StrCpy $SourceSel $0
  ClearErrors
  ${GetOptions} $R0 "/PAYLOAD=" $0
  IfErrors +2 0
    StrCpy $PayloadPath $0
  ClearErrors
  ${GetOptions} $R0 "/URL=" $0
  IfErrors +2 0
    StrCpy $UrlValue $0
  ClearErrors
  ${GetOptions} $R0 "/NODEPS" $0
  IfErrors +2 0
    StrCpy $DepsFlag "0"
  ClearErrors
  ${GetOptions} $R0 "/MINIMAL" $0
  IfErrors +2 0
    StrCpy $MinimalFlag "1"
  ClearErrors
FunctionEnd

; ---------------------------------------------------------- Options-Seite
Function OptionsPageCreate
  Call DetectDefaults
  !insertmacro MUI_HEADER_TEXT "Installationsart" "Modus, Quelle und Optionen waehlen"
  nsDialogs::Create 1018
  Pop $Dialog
  ${If} $Dialog == error
    Abort
  ${EndIf}

  ${NSD_CreateRadioButton} 0 0u 100% 12u "Neuinstallation"
  Pop $RadioInstall
  ${NSD_AddStyle} $RadioInstall ${WS_GROUP}
  ${NSD_CreateRadioButton} 0 13u 100% 12u "Bestehende Installation aktualisieren (nur geaenderte Komponenten)"
  Pop $RadioUpdate
  ${If} $HasExisting == "0"
    EnableWindow $RadioUpdate 0
  ${EndIf}
  ${If} $ModeSel == "update"
    ${NSD_Check} $RadioUpdate
  ${Else}
    ${NSD_Check} $RadioInstall
  ${EndIf}

  ${NSD_CreateLabel} 0 30u 100% 10u "Quelle der Programmdaten:"
  Pop $0
  ${NSD_CreateRadioButton} 8u 41u 90% 12u "Offline-Paketdatei (airgapped):"
  Pop $RadioOffline
  ${NSD_AddStyle} $RadioOffline ${WS_GROUP}
  ${NSD_CreateText} 16u 54u 70% 12u "$PayloadPath"
  Pop $TxtPayload
  ${NSD_CreateBrowseButton} 88u 54u 10% 12u "..."
  Pop $BtnBrowse
  ${NSD_OnClick} $BtnBrowse OnBrowsePayload
  ${NSD_CreateRadioButton} 8u 69u 90% 12u "Online herunterladen (GitHub-Release oder interner Mirror):"
  Pop $RadioOnline
  ${NSD_CreateText} 16u 82u 82% 12u "$UrlValue"
  Pop $TxtUrl
  ${If} $SourceSel == "online"
    ${NSD_Check} $RadioOnline
  ${Else}
    ${NSD_Check} $RadioOffline
  ${EndIf}

  ${NSD_CreateCheckbox} 0 100u 100% 12u "Abhaengigkeiten mit aktualisieren, falls geaendert (nur bei Update; empfohlen)"
  Pop $ChkDeps
  ${NSD_AddStyle} $ChkDeps ${WS_GROUP}
  ${If} $DepsFlag == "1"
    ${NSD_Check} $ChkDeps
  ${EndIf}
  ${NSD_CreateCheckbox} 0 113u 100% 24u "Minimal-Installation: Websuche, Qdrant, Embedding-Fallback, Host-Werkzeuge (Node/yt-dlp) und beigelegte Installer weglassen (spart ~2,2 GB; nur bei Neuinstallation)"
  Pop $ChkMinimal
  ${If} $MinimalFlag == "1"
    ${NSD_Check} $ChkMinimal
  ${EndIf}

  nsDialogs::Show
FunctionEnd

Function OnBrowsePayload
  nsDialogs::SelectFileDialog open "$PayloadPath" "Payload-Zip (*.zip)|*.zip|Alle Dateien (*.*)|*.*"
  Pop $0
  ${If} $0 != ""
    ${NSD_SetText} $TxtPayload "$0"
    ${NSD_Check} $RadioOffline
    ${NSD_Uncheck} $RadioOnline
  ${EndIf}
FunctionEnd

Function OptionsPageLeave
  ${NSD_GetState} $RadioUpdate $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $ModeSel "update"
  ${Else}
    StrCpy $ModeSel "install"
  ${EndIf}
  ${NSD_GetState} $RadioOnline $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $SourceSel "online"
  ${Else}
    StrCpy $SourceSel "offline"
  ${EndIf}
  ${NSD_GetText} $TxtPayload $PayloadPath
  ${NSD_GetText} $TxtUrl $UrlValue
  ${NSD_GetState} $ChkDeps $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $DepsFlag "1"
  ${Else}
    StrCpy $DepsFlag "0"
  ${EndIf}
  ${NSD_GetState} $ChkMinimal $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $MinimalFlag "1"
  ${Else}
    StrCpy $MinimalFlag "0"
  ${EndIf}
  ${If} $SourceSel == "offline"
  ${AndIf} $PayloadPath == ""
    MessageBox MB_ICONEXCLAMATION "Bitte eine Offline-Paketdatei waehlen (oder Online-Download aktivieren)."
    Abort
  ${EndIf}
  ${If} $SourceSel == "online"
  ${AndIf} $UrlValue == ""
    StrCpy $UrlValue "${DEFAULT_URL}"
  ${EndIf}
FunctionEnd

; ------------------------------------------------------------ Installation
Section "Install"
  Call DetectDefaults
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "/oname=setup_stage1.ps1" "${STAGE1}"

  StrCpy $R9 '-Mode $ModeSel -Source $SourceSel -InstallDir "$INSTDIR" -UpdateDeps $DepsFlag -Minimal $MinimalFlag'
  ${If} $SourceSel == "offline"
    StrCpy $R9 '$R9 -Payload "$PayloadPath"'
  ${Else}
    StrCpy $R9 '$R9 -BaseUrl "$UrlValue"'
  ${EndIf}
  IfSilent 0 +2
    StrCpy $R9 '$R9 -Silent'

  DetailPrint "Starte Setup-Engine (eigenes PowerShell-Fenster; dort laufen Download/Entpacken und die Mac-mini-Abfrage)..."
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PLUGINSDIR\setup_stage1.ps1" $R9' $0
  DetailPrint "Setup-Engine beendet (Code $0)"
  ${If} $0 != 0
    MessageBox MB_ICONSTOP "Installation/Update fehlgeschlagen (Code $0). Details standen im PowerShell-Fenster." /SD IDOK
    Abort
  ${EndIf}

  ; Verknuepfungen
  CreateDirectory "$SMPROGRAMS\Brain-Agent"
  CreateShortCut "$SMPROGRAMS\Brain-Agent\Brain-Agent starten.lnk" "$INSTDIR\BrainAgent.bat" "" "" 0
  CreateShortCut "$SMPROGRAMS\Brain-Agent\Brain-Agent stoppen.lnk" "$INSTDIR\stop.bat" "" "" 0
  CreateShortCut "$SMPROGRAMS\Brain-Agent\Brain-Agent Web-UI.lnk" "http://localhost:8420"
  CreateShortCut "$DESKTOP\Brain-Agent.lnk" "$INSTDIR\BrainAgent.bat" "" "" 0

  ; Uninstaller + Software-Liste (HKCU — kein Admin). DisplayVersion wird von
  ; setup_stage1.ps1 auf die tatsaechlich installierte Manifest-Version gesetzt.
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
