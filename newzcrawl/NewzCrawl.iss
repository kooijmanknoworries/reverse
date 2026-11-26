; ---------------------------------------------------------------------------
;  NewzCrawl — Inno Setup script (Windows 11, x64)
;
;  Builds NewzCrawl-Setup.exe from the PyInstaller output:
;      dist\NewzCrawl.exe   (the frozen app)
;      tools\               (7z + par2cmdline + Unpack)
;      assets\icon.ico      (app icon)
;
;  Build (on the CI Windows runner, after pyinstaller has run):
;      iscc NewzCrawl.iss
;
;  The app is a per-user install (no admin needed). Settings, downloads
;  and the password list are kept in %APPDATA%\NewzCrawl so they survive
;  upgrades and uninstall; the bundled tools stay with the program.
; ---------------------------------------------------------------------------

#define MyAppName "NewzCrawl"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "NewzCrawl"
#define MyAppExeName "NewzCrawl.exe"

[Setup]
; NOTE: AppId identifies this application uniquely. Do not change it
; between releases or Inno will treat new versions as a different app.
AppId={{8E3B1C02-77A4-4C1E-9F5B-2D4F6A8C0011}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={userprograms}\{#MyAppName}
DefaultGroupName={#MyAppName}
; per-user install: no admin rights required
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
OutputDir=dist
OutputBaseFilename=NewzCrawl-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; the WebView2 Runtime ships with Windows 11; nothing extra to install
ShowLanguageDialog=no
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; the frozen application (PyInstaller onefile)
Source: "dist\NewzCrawl.exe"; DestDir: "{app}"; Flags: ignoreversion
; bundled helper tools (7z, par2cmdline, Unpack) — keep the tree intact
Source: "tools\*"; DestDir: "{app}\tools"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; no IconFilename -> Inno uses the icon embedded in NewzCrawl.exe
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; .nzb file association (double-click a .nzb to open it in NewzCrawl)
Root: HKA; Subkey: "Software\Classes\.nzb"; ValueType: string; ValueName: ""; ValueData: "nzbfile"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\nzbfile"; ValueType: string; ValueName: ""; ValueData: "NZB news file"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\nzbfile\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
