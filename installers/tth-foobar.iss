; TuneThatHue foobar2000 component - installer.
;
; The idiomatic way to install a foobar2000 component is to double-click the
; .fb2k-component file (foobar installs it itself). This installer exists for
; people who prefer a normal setup .exe; it drops the DLL straight into the
; user-components folder of foobar2000 v2.
#include "common.iss"

[Setup]
AppId={{8F3A2C11-5D4E-4B90-9C77-TTHFOOBAR001}
AppName=TuneThatHue component for foobar2000
AppVersion={#TTHVersion}
AppPublisher={#TTHPublisher}
AppPublisherURL={#TTHUrl}
AppSupportURL={#TTHSupportUrl}
AppUpdatesURL={#TTHUpdatesUrl}
VersionInfoCopyright={#TTHCopyright}
DefaultDirName={userappdata}\foobar2000-v2\user-components\foo_tunethathue
DisableDirPage=no
DefaultGroupName=TuneThatHue
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=TuneThatHue-foobar2000-{#TTHVersion}-setup
SetupIconFile={#TTHIcon}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The component is x64 and only loads in 64-bit foobar2000 2.x.
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\foobar\foo_tunethathue.dll"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\foobar\README.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Code]
// foobar keeps components in the profile; if it is running it holds the DLL
// open, so ask the user to close it rather than failing mid-copy.
function InitializeSetup(): Boolean;
var
  Wnd: HWND;
begin
  Result := True;
  Wnd := FindWindowByClassName('{6B1A8F4C-4C4C-4C4C-4C4C-4C4C4C4C4C4C}');
  if FindWindowByWindowName('foobar2000') <> 0 then
  begin
    if MsgBox('foobar2000 appears to be running.' + #13#10 + #13#10 +
              'Close it before continuing, otherwise the component file cannot be replaced.' + #13#10 + #13#10 +
              'Continue anyway?', mbConfirmation, MB_YESNO) = IDNO then
      Result := False;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption :=
      'The TuneThatHue component is installed.' + #13#10 + #13#10 +
      'Start foobar2000, then open File > Preferences > Playback > DSP Manager and move ' +
      '"TuneThatHue (send audio to daemon)" into the active DSP chain. Use "Configure selected" ' +
      'to set the daemon IP and port and to test the connection.' + #13#10 + #13#10 +
      'A DSP only runs while it is in the active chain.' + #13#10 + #13#10 +
      '{#TTHDaemonNote}';
end;
