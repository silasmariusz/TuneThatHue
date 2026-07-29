; TuneThatHue Winamp DSP plugin - installer.
; Finds the Winamp install from the registry and drops the plugin in Plugins\.
#include "common.iss"

[Setup]
AppId={{8F3A2C11-5D4E-4B90-9C77-TTHWINAMP001}
AppName=TuneThatHue plugin for Winamp
AppVersion={#TTHVersion}
AppPublisher={#TTHPublisher}
AppPublisherURL={#TTHUrl}
AppSupportURL={#TTHSupportUrl}
AppUpdatesURL={#TTHUpdatesUrl}
VersionInfoCopyright={#TTHCopyright}
; {app} is the WINAMP folder - resolved in code from the registry.
DefaultDirName={code:GetWinampDir}
DirExistsWarning=no
AppendDefaultDirName=no
DefaultGroupName=TuneThatHue
DisableProgramGroupPage=yes
DisableWelcomePage=no
OutputDir=Output
OutputBaseFilename=TuneThatHue-Winamp-plugin-{#TTHVersion}-setup
SetupIconFile={#TTHIcon}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; The plugin is 32-bit because Winamp is; the installer itself runs anywhere.
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\winamp\dsp_tunethathue.dll"; DestDir: "{app}\Plugins"; Flags: ignoreversion
Source: "..\winamp\README.txt"; DestDir: "{app}\Plugins"; DestName: "dsp_tunethathue-README.txt"; Flags: ignoreversion isreadme

[UninstallDelete]
Type: files; Name: "{app}\Plugins\dsp_tunethathue.ini"

[Code]
// Winamp records its folder in the registry; check every place it may live and
// fall back to the usual Program Files path so the user can still browse.
function GetWinampDir(Param: String): String;
var
  Dir: String;
begin
  if RegQueryStringValue(HKCU, 'Software\Winamp', '', Dir) and (Dir <> '') then
    Result := Dir
  else if RegQueryStringValue(HKLM, 'Software\Winamp', '', Dir) and (Dir <> '') then
    Result := Dir
  else if RegQueryStringValue(HKLM, 'Software\WOW6432Node\Winamp', '', Dir) and (Dir <> '') then
    Result := Dir
  else
    Result := ExpandConstant('{commonpf32}\Winamp');
end;

function InitializeSetup(): Boolean;
var
  Dir: String;
begin
  Dir := GetWinampDir('');
  Result := True;
  // Warn, never block: the user may be installing for a portable Winamp and
  // can point the wizard at the right folder on the next page.
  if not DirExists(Dir) then
    MsgBox('Winamp was not found automatically.' + #13#10 + #13#10 +
           'On the next page, choose your Winamp folder (the one containing Winamp.exe). ' +
           'The plugin will be copied into its Plugins sub-folder.',
           mbInformation, MB_OK);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption :=
      'The TuneThatHue plugin is installed.' + #13#10 + #13#10 +
      'In Winamp open Options > Preferences > Plug-ins > DSP/Effect, select ' +
      '"TuneThatHue (send audio to daemon)", then click Configure to set the daemon ' +
      'IP and port and to test the connection.' + #13#10 + #13#10 +
      '{#TTHDaemonNote}';
end;
