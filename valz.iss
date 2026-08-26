; Inno Setup script for valz desktop.
;
; This file is a TEMPLATE -- the user must install Inno Setup themselves
; (https://jrsoftware.org/isinfo.php) and compile with:
;   ISCC valz.iss
;
; It is not auto-invoked by build_desktop.ps1. The build script ships a
; portable zip instead, which is enough for 3-4 Discord testers and needs
; no installer chain. Compile this only if a real installer is wanted.

[Setup]
AppName=valz
AppVersion=0.2.0
AppPublisher=Monster Hunter (IDX-Thesis) by Pixelvanta
DefaultDirName={autopf}\valz
DefaultGroupName=valz
Compression=lzma2
SolidCompression=yes
OutputBaseFilename=valz-setup
; no privileges required: payload goes under Program Files but user data
; is read from %LOCALAPPDATA%\valz at runtime (see desktop.py)
PrivilegesRequired=lowest

[Files]
; bundle the onedir output, not the zip
Source: "dist\valz\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\valz"; Filename: "{app}\valz.exe"

[Run]
Filename: "{app}\valz.exe"; Description: "Launch valz"; Flags: nowait postinstall skipifsilent
