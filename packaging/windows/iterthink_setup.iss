; Inno Setup 6 script: bundles a pre-built Flet Windows app + optional Ollama setup.
;
; Before compiling:
;   1. Run `flet build windows` from the repository root (on Windows).
;   2. Confirm the folder below contains the built app (recursive copy).
;   3. Confirm {#MyAppExeName} matches the .exe name under build\windows (see Flet output).
;
; Ollama: Official docs (https://docs.ollama.com/windows) document OllamaSetup.exe /DIR=...
; for a custom install path. They do not document Inno-style /VERYSILENT for the GUI
; installer; enterprise-style silent deployment may require the standalone zip or
; install.ps1 — verify current Ollama release notes before relying on silent flags.
;
; https://ollama.com/install.ps1 is the official PowerShell installer used below.

#define MyAppName "Iterthink"
#define MyAppVersion "0.1.1"
#define MyAppPublisher "Iterthink"
; Path from this .iss file (packaging\windows\) to Flet output at repo root:
#define MyFletBuildDir "..\..\build\windows"
; Align with Flet output under build\windows (from [project].name "iterthink"); adjust if the built .exe name differs:
#define MyAppExeName "iterthink.exe"

[Setup]
AppId={{A8B9C0D1-E2F3-4567-8901-23456789ABCD}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\..\dist\installer
OutputBaseFilename={#MyAppName}_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "installollama"; Description: "Install or update Ollama using the official install.ps1 (recommended; network required)"; GroupDescription: "Prerequisites:"; Flags: checkedonce

[Files]
Source: "{#MyFletBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; Official script: https://ollama.com/install.ps1 (see https://docs.ollama.com/windows)
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""irm https://ollama.com/install.ps1 | iex"""; \
  Description: "Install Ollama (official)"; Flags: runasoriginaluser waituntilterminated; Tasks: installollama
