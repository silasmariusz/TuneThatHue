; TuneThatHue SoundRecorder - Windows audio capture, installer.
#include "common.iss"

[Setup]
AppId={{8F3A2C11-5D4E-4B90-9C77-TTHSNDREC001}
AppName=TuneThatHue SoundRecorder
AppVersion={#TTHVersion}
AppPublisher={#TTHPublisher}
AppPublisherURL={#TTHUrl}
AppSupportURL={#TTHSupportUrl}
AppUpdatesURL={#TTHUpdatesUrl}
VersionInfoCopyright={#TTHCopyright}
DefaultDirName={autopf}\TuneThatHue\SoundRecorder
DefaultGroupName=TuneThatHue
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=TuneThatHue-SoundRecorder-{#TTHVersion}-setup
SetupIconFile={#TTHIcon}
UninstallDisplayIcon={app}\TuneThatHue-SoundRecorder.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; x64 app (WASAPI process-loopback needs a modern Windows anyway).
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "Start TuneThatHue SoundRecorder when Windows starts"; GroupDescription: "Additional options:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional options:"; Flags: unchecked

[Files]
Source: "..\soundrecorder\TuneThatHue-SoundRecorder.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\resources\tth.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\soundrecorder\README.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\TuneThatHue SoundRecorder"; Filename: "{app}\TuneThatHue-SoundRecorder.exe"; IconFilename: "{app}\tth.ico"
Name: "{group}\Uninstall TuneThatHue SoundRecorder"; Filename: "{uninstallexe}"
Name: "{autodesktop}\TuneThatHue SoundRecorder"; Filename: "{app}\TuneThatHue-SoundRecorder.exe"; IconFilename: "{app}\tth.ico"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "TuneThatHueSoundRecorder"; ValueData: """{app}\TuneThatHue-SoundRecorder.exe"""; \
  Tasks: startup; Flags: uninsdeletevalue

[Run]
Filename: "{app}\TuneThatHue-SoundRecorder.exe"; Description: "Start TuneThatHue SoundRecorder now"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The settings file is written next to the exe at runtime.
Type: files; Name: "{app}\TuneThatHue-SoundRecorder.ini"

[Code]
// A running instance holds the exe open; ask it to quit before installing over it.
function CloseRunningRecorder(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/IM TuneThatHue-SoundRecorder.exe /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  CloseRunningRecorder();
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    CloseRunningRecorder();
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption :=
      'TuneThatHue SoundRecorder is installed.' + #13#10 + #13#10 +
      'It lives in the system tray. Double-click the tray icon to choose what to capture ' +
      '(the default output, another device, an input like Stereo Mix, or a single application) ' +
      'and to set the daemon address.' + #13#10 + #13#10 +
      '{#TTHDaemonNote}';
end;
