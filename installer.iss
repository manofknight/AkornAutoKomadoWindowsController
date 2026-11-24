[Setup]
AppName=AKORN Controller
AppVersion=1.0.0
DefaultDirName={autopf}\AKORN Controller
DefaultGroupName=AKORN Controller
OutputDir=installer
OutputBaseFilename=AkornControllerSetup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\AkornController.exe

[Files]
Source: "dist\AkornController.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "profile.default.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\AKORN Controller"; Filename: "{app}\AkornController.exe"
Name: "{autodesktop}\AKORN Controller"; Filename: "{app}\AkornController.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Run]
Filename: "{app}\AkornController.exe"; Description: "Launch AKORN Controller"; Flags: nowait postinstall skipifsilent
