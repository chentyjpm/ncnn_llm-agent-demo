#ifndef BundleDir
  #error BundleDir must point to the frozen application
#endif
[Setup]
AppId={{6C8C85AE-7633-4A4F-A02B-95F538EA5828}
AppName=Local Agent
AppVersion=0.3.0
AppPublisher=Local Agent Project
DefaultDirName={localappdata}\Programs\LocalAgent
DefaultGroupName=Local Agent
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=LocalAgent-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\LocalAgent.exe
CloseApplications=yes
[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{autoprograms}\Local Agent"; Filename: "{app}\LocalAgent.exe"
Name: "{autodesktop}\Local Agent"; Filename: "{app}\LocalAgent.exe"
[Run]
Filename: "{app}\LocalAgent.exe"; Description: "Open Local Agent"; Flags: nowait postinstall skipifsilent
; User data under LocalAppData\LocalAgent is retained on uninstall.
