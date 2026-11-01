; Inno Setup script - builds TallySyncConnector-Setup.exe
[Setup]
AppName=Tally Sync Connector
AppVersion=1.0.0
AppPublisher=Rushikesh
DefaultDirName={autopf}\Tally Sync Connector
DefaultGroupName=Tally Sync Connector
OutputDir=Output
OutputBaseFilename=TallySyncConnector-Setup
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\TallySyncConnector.exe

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"

[Files]
Source: "dist\TallySyncConnector.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Tally Sync Connector"; Filename: "{app}\TallySyncConnector.exe"
Name: "{autodesktop}\Tally Sync Connector"; Filename: "{app}\TallySyncConnector.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TallySyncConnector.exe"; Description: "Launch Tally Sync Connector"; Flags: nowait postinstall skipifsilent
