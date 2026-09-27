; LimitSwitch's Windows installer (Inno Setup 6), built by scripts\build_windows.ps1:
;   iscc /DAppVersion=1.0.0 /DSourceDir=<build\LimitSwitch> scripts\LimitSwitch.iss
; Per user, no admin rights. It asks where to install, has boxes for a Start menu entry,
; start at sign-in (both on) and a desktop shortcut (off). The app's in-app updater runs it with
; /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR=<this folder>: it closes the app, replaces the
; files and starts the app again. Saved accounts and settings live in %LOCALAPPDATA%\AccountSwitcher
; and are kept, also by the uninstaller.

#define AppName "LimitSwitch"
#define AppExe "LimitSwitch.exe"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\LimitSwitch"
#endif

[Setup]
AppId={{6B1E0C54-3F7A-4D2B-9E8C-5A1F2D7B4C90}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=doge006
AppPublisherURL=https://github.com/doge006/LimitSwitch
AppSupportURL=https://github.com/doge006/LimitSwitch/issues
AppUpdatesURL=https://github.com/doge006/LimitSwitch/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=no
DisableProgramGroupPage=yes
DisableWelcomePage=yes
PrivilegesRequired=lowest
UsePreviousAppDir=yes
UsePreviousTasks=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\build
OutputBaseFilename=LimitSwitch-Setup
SetupIconFile=..\account_switcher\static\assets\switcher.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
; The app is closed with --quit first (it puts Codex's and Claude Code's settings back); this
; only catches a copy that did not answer.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "startmenu"; Description: "Add to the Start menu"
Name: "startup"; Description: "Start {#AppName} when I sign in"
Name: "desktopicon"; Description: "Add a desktop shortcut"; Flags: unchecked

[InstallDelete]
; An update replaces the app and its Python whole, so no file of an older version is left behind.
Type: filesandordirs; Name: "{app}\account_switcher"
Type: filesandordirs; Name: "{app}\runtime"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Claude Code and Codex usage limits and account switching"; \
    AppUserModelID: "LimitSwitch.App"; Tasks: startmenu
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Start at sign-in, as chosen here. After the first install the app's own setting owns it, so
; a silent update leaves it alone.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; \
    ValueData: """{app}\{#AppExe}"""; Tasks: startup; Check: ChooseStartup

[Run]
Filename: "{app}\{#AppExe}"; Parameters: "--show"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[UninstallRun]
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\LimitSwitch.pyw"" --quit"; WorkingDir: "{app}"; \
    Flags: runhidden waituntilterminated; RunOnceId: "QuitApp"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.runtime"
Type: filesandordirs; Name: "{app}\account_switcher"
Type: filesandordirs; Name: "{app}\runtime"

[Code]
var
  WasInstalled: Boolean;

function ChooseStartup(): Boolean;
begin
  Result := (not WasInstalled) or (not WizardSilent);
end;

procedure QuitRunningCopy();
var
  Python: String;
  Code: Integer;
begin
  Python := ExpandConstant('{app}\runtime\pythonw.exe');
  if FileExists(Python) then
  begin
    Exec(Python, AddQuotes(ExpandConstant('{app}\LimitSwitch.pyw')) + ' --quit', ExpandConstant('{app}'),
         SW_HIDE, ewWaitUntilTerminated, Code);
    Sleep(500); { its process ends just after it says it has quit }
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  WasInstalled := FileExists(ExpandConstant('{app}\{#AppExe}'));
  QuitRunningCopy();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', '{#AppName}');
end;
