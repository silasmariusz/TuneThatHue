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
DefaultDirName={code:DefaultComponentDir}
DisableDirPage=no
DefaultGroupName=TuneThatHue
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=TuneThatHue-foobar2000-{#TTHVersion}-setup
SetupIconFile={#TTHIcon}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Both 32-bit and 64-bit foobar2000 are supported, so this runs anywhere; the
; matching DLL is chosen at install time (see [Files] and IsFoobar64).
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; foobar2000 v2 exists as 32-bit and 64-bit, and a component built for the wrong
; one fails to load with "Not a valid Win32 application". Ship both and let
; IsFoobar64 pick, so the user never has to know which build they installed.
Source: "..\foobar\foo_tunethathue-x64.dll"; DestName: "foo_tunethathue.dll"; DestDir: "{app}"; Flags: ignoreversion; Check: IsFoobar64
Source: "..\foobar\foo_tunethathue-x86.dll"; DestName: "foo_tunethathue.dll"; DestDir: "{app}"; Flags: ignoreversion; Check: not IsFoobar64
Source: "..\foobar\README.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Code]
// Decide which build of foobar2000 is installed by reading the PE machine type
// of foobar2000.exe - the install path is not a reliable hint (people put the
// 32-bit build under Program Files, and portable installs live anywhere).
// Pascal Script has no byte-array stream reads, so the file is loaded as a
// string and indexed (1-based: file offset N is S[N+1]).
function ExeIsX64(const Path: String): Boolean;
var
  S: AnsiString;
  PEOff, Machine: Integer;
begin
  Result := False;
  if not FileExists(Path) then exit;
  if not LoadStringFromFile(Path, S) then exit;
  if Length(S) < 64 then exit;
  PEOff := Ord(S[61]) or (Ord(S[62]) shl 8) or (Ord(S[63]) shl 16) or (Ord(S[64]) shl 24);
  if (PEOff <= 0) or (PEOff + 6 > Length(S)) then exit;
  Machine := Ord(S[PEOff + 5]) or (Ord(S[PEOff + 6]) shl 8);   // COFF Machine
  Result := (Machine = $8664);
end;

// A portable foobar2000 keeps its components next to the exe, not in the user
// profile, and ignores whatever is in %APPDATA%. Installing to the default would
// silently do nothing, so find the portable root first and use that instead.
function FoobarDir(): String;
var
  Path: String;
begin
  Result := '';
  Path := '';
  if not RegQueryStringValue(HKLM, 'SOFTWARE\foobar2000', 'InstallDir', Path) then
    RegQueryStringValue(HKCU, 'SOFTWARE\foobar2000', 'InstallDir', Path);
  if (Path <> '') and FileExists(AddBackslash(Path) + 'foobar2000.exe') then
    Result := RemoveBackslash(Path)
  else if FileExists(ExpandConstant('{commonpf64}\foobar2000\foobar2000.exe')) then
    Result := ExpandConstant('{commonpf64}\foobar2000')
  else if FileExists(ExpandConstant('{commonpf32}\foobar2000\foobar2000.exe')) then
    Result := ExpandConstant('{commonpf32}\foobar2000');
end;

function IsPortableFoobar(): Boolean;
var
  Dir: String;
begin
  Dir := FoobarDir();
  Result := (Dir <> '') and FileExists(AddBackslash(Dir) + 'portable_mode_enabled');
end;

// Components must sit in their OWN subfolder (user-components\foo_x\foo_x.dll).
// A DLL dropped loose into user-components is simply not loaded - that is the
// old 1.x layout - and the failure is silent, which makes it hard to diagnose.
function DefaultComponentDir(Param: String): String;
begin
  if IsPortableFoobar() then
    Result := AddBackslash(FoobarDir()) + 'user-components\foo_tunethathue'
  else
    Result := ExpandConstant('{userappdata}\foobar2000-v2\user-components\foo_tunethathue');
end;

function IsFoobar64(): Boolean;
var
  Path: String;
begin
  // Where the player actually is, per its own registry entry; fall back to the
  // usual locations. Unknown -> assume 32-bit, which is the safer guess since
  // it is what a plain "foobar2000" download still installs.
  Path := '';
  if not RegQueryStringValue(HKLM, 'SOFTWARE\foobar2000', 'InstallDir', Path) then
    RegQueryStringValue(HKCU, 'SOFTWARE\foobar2000', 'InstallDir', Path);
  if (Path <> '') and FileExists(AddBackslash(Path) + 'foobar2000.exe') then
    Result := ExeIsX64(AddBackslash(Path) + 'foobar2000.exe')
  else if FileExists(ExpandConstant('{commonpf64}\foobar2000\foobar2000.exe')) then
    Result := ExeIsX64(ExpandConstant('{commonpf64}\foobar2000\foobar2000.exe'))
  else if FileExists(ExpandConstant('{commonpf32}\foobar2000\foobar2000.exe')) then
    Result := ExeIsX64(ExpandConstant('{commonpf32}\foobar2000\foobar2000.exe'))
  else
    Result := False;
end;
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
