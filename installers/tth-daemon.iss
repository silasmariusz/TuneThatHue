; TuneThatHue daemon installer for Windows.
;
; Installs the daemon, its Python runtime, the bundled ffmpeg and the tray icon, and
; registers a Windows service so it starts at boot with nobody signed in - the same as
; the systemd unit on Linux and the launchd job on macOS.
;
; The service runs under WinSW rather than directly: Windows expects a service to answer
; the service control manager within thirty seconds, which a Python process never does,
; and a bare `sc create` produced a service killed at every start with error 1053.
;
; Because a service runs as LocalSystem, the settings live in ProgramData rather than in
; anyone's AppData - otherwise the daemon would write them where the panel cannot read
; them, and a paired bridge would look unpaired to the person who paired it.
;
; Build:  ISCC.exe installers\tth-daemon.iss

#define AppName     "TuneThatHue"
#define AppVersion  "0.9.2"
#define Publisher   "Silas Mariusz Grzybacz"
#define AppUrl      "https://github.com/silasmariusz/TuneThatHue"
#define Root        ".."

[Setup]
AppId={{8C5A1E42-9D3B-4F17-A2E6-TUNETHATHUE01}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
AppPublisherURL={#AppUrl}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
OutputDir={#Root}\installers\Output
OutputBaseFilename={#AppName}-daemon-{#AppVersion}-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Writing into Program Files needs an administrator; the daemon itself does not.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile={#Root}\resources\tth.ico
UninstallDisplayIcon={app}\resources\tth.ico
DisableProgramGroupPage=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "service"; Description: "Run as a Windows service (starts at boot, no sign-in needed)"; GroupDescription: "How it runs:"
Name: "tray";    Description: "Show the tray icon at sign-in"; GroupDescription: "How it runs:"

[Files]
Source: "{#Root}\python\*";     DestDir: "{app}\python";     Flags: ignoreversion recursesubdirs
Source: "{#Root}\effects\*";    DestDir: "{app}\effects";    Flags: ignoreversion recursesubdirs
Source: "{#Root}\resources\*";  DestDir: "{app}\resources";  Flags: ignoreversion recursesubdirs
Source: "{#Root}\config\*";     DestDir: "{app}\config";     Flags: ignoreversion recursesubdirs
Source: "{#Root}\install\*";    DestDir: "{app}\install";    Flags: ignoreversion recursesubdirs
; The Python runtime and the decoder, assembled before building this installer by
; installers\prepare-windows.ps1. Both are optional at build time so the script can be
; compiled on a machine that has not assembled them yet.
Source: "{#Root}\runtime\python-win\*";     DestDir: "{app}\runtime\python"; Flags: ignoreversion recursesubdirs skipifsourcedoesntexist
Source: "{#Root}\runtime\ffmpeg-AMD64\*";   DestDir: "{app}\runtime\ffmpeg-AMD64"; Flags: ignoreversion skipifsourcedoesntexist
; WinSW, the service host. Windows expects a service to answer the service control
; manager within thirty seconds; a Python process never does, so WinSW answers for it.
Source: "{#Root}\runtime\WinSW-x64.exe";    DestDir: "{app}\runtime"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName} panel"; Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\install\tunethathue_ctl.py"" panel"; IconFilename: "{app}\resources\tth.ico"
Name: "{group}\{#AppName} tray";  Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\install\tunethathue_tray.py"""; IconFilename: "{app}\resources\tth.ico"

[Run]
; Register the service through the same control command the tray and the terminal use,
; so there is one definition of what "installed" means.
Filename: "{app}\runtime\python\python.exe"; Parameters: """{app}\install\tunethathue_ctl.py"" install"; Tasks: service; StatusMsg: "Registering the service..."; Flags: runhidden
; Autostart is registered by the control command rather than by an [Registry] entry,
; and deliberately as the original user. Setup runs elevated, so an HKCU write here
; would land in the hive of whoever approved the prompt - on a machine where somebody
; types an admin password, that is not the person who will be using this.
Filename: "{app}\runtime\python\python.exe"; Parameters: """{app}\install\tunethathue_ctl.py"" autostart"; Tasks: tray; Flags: runhidden runasoriginaluser; StatusMsg: "Setting the tray to start at sign-in..."
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\install\tunethathue_tray.py"""; Tasks: tray; Flags: nowait postinstall skipifsilent runasoriginaluser; Description: "Start the tray icon now"
Filename: "{app}\runtime\python\pythonw.exe"; Parameters: """{app}\install\tunethathue_ctl.py"" panel"; Flags: nowait postinstall skipifsilent runasoriginaluser; Description: "Open the panel"

[UninstallRun]
; No runasoriginaluser here - the uninstall section does not take it. Normally the
; uninstaller runs as the same person, so this clears the entry; when it does not, the
; tray removes its own entry the next time it starts and finds nothing to run.
Filename: "{app}\runtime\python\python.exe"; Parameters: """{app}\install\tunethathue_ctl.py"" noautostart"; Flags: runhidden; RunOnceId: "removeautostart"
Filename: "{app}\runtime\python\python.exe"; Parameters: """{app}\install\tunethathue_ctl.py"" uninstall"; Flags: runhidden; RunOnceId: "removeservice"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
