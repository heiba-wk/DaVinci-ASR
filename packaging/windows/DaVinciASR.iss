#ifndef AppVersion
  #define AppVersion "1.0.0-dev"
#endif

#ifndef SourceRoot
  #define SourceRoot "..\.."
#endif

#ifndef OutputRoot
  #define OutputRoot "..\..\dist"
#endif

#ifndef RuntimeBuildRoot
  #define RuntimeBuildRoot AddBackslash(SourceRoot) + "build\windows"
#endif

#define AppName "DaVinci ASR"
#define Publisher "HEIBA"
#define RuntimeRoot "{localappdata}\HEIBA\DaVinciASR\runtime"
#define ResolveScriptRoot "{commonappdata}\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility\DaVinci ASR"
#define ModelsRoot "{#ResolveScriptRoot}\models"

[Setup]
AppId={{D2E542E1-56B4-4A66-88A0-35254C3E7D3D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={#RuntimeRoot}
DisableDirPage=yes
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir={#OutputRoot}
OutputBaseFilename=DaVinciASRSetup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}

[Files]
Source: "{#SourceRoot}\DaVinci ASR\DaVinci ASR.lua"; DestDir: "{#ResolveScriptRoot}"; Flags: ignoreversion
Source: "{#SourceRoot}\DaVinci ASR\config\setting.json"; DestDir: "{#ResolveScriptRoot}\config"; Flags: onlyifdoesntexist
Source: "{#SourceRoot}\DaVinci ASR\render_preset\render_to_wav.xml"; DestDir: "{#ResolveScriptRoot}\render_preset"; Flags: ignoreversion
Source: "{#SourceRoot}\LICENSE"; DestDir: "{#RuntimeRoot}"; Flags: ignoreversion
Source: "{#SourceRoot}\THIRD_PARTY_NOTICES.md"; DestDir: "{#RuntimeRoot}"; Flags: ignoreversion
Source: "{#RuntimeBuildRoot}\windows-cuda-x64\dist\DaVinci ASR\*"; DestDir: "{#RuntimeRoot}"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: HasNvidiaGPU
Source: "{#RuntimeBuildRoot}\windows-cpu-x64\dist\DaVinci ASR\*"; DestDir: "{#RuntimeRoot}"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: not HasNvidiaGPU

[Dirs]
Name: "{#ModelsRoot}"; Permissions: users-modify
Name: "{#ResolveScriptRoot}\audio_temp"; Permissions: users-modify
Name: "{#ResolveScriptRoot}\config"; Permissions: users-modify
Name: "{#ResolveScriptRoot}\temp"; Permissions: users-modify
Name: "{localappdata}\HEIBA\DaVinciASR\ipc"
Name: "{localappdata}\HEIBA\DaVinciASR\logs"
Name: "{localappdata}\HEIBA\DaVinciASR\cache"
Name: "{localappdata}\HEIBA\DaVinciASR\settings"
Name: "{localappdata}\HEIBA\DaVinciASR\temp"

[Code]
var
  NvidiaDetected: Boolean;
  NvidiaDetectionComplete: Boolean;

function ResolveInstalled(): Boolean;
begin
  Result := DirExists(ExpandConstant('{pf}\Blackmagic Design\DaVinci Resolve')) or
            DirExists(ExpandConstant('{commonappdata}\Blackmagic Design\DaVinci Resolve'));
end;

function HasNvidiaGPU(): Boolean;
var
  ResultCode: Integer;
  PowerShell: String;
  Arguments: String;
begin
  if NvidiaDetectionComplete then begin
    Result := NvidiaDetected;
    exit;
  end;
  NvidiaDetectionComplete := True;
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Arguments := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ' +
    '"if (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match ''NVIDIA'' }) { exit 0 } else { exit 1 }"';
  NvidiaDetected := Exec(PowerShell, Arguments, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and
                    (ResultCode = 0);
  Result := NvidiaDetected;
end;

function InitializeSetup(): Boolean;
begin
  Result := ResolveInstalled();
  if not Result then
    MsgBox('DaVinci Resolve was not detected. Install DaVinci Resolve before DaVinci ASR.', mbError, MB_OK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    if HasNvidiaGPU() then
      Log('Installed windows-cuda-x64 bundled Runtime')
    else
      Log('Installed windows-cpu-x64 bundled Runtime');
  end;
end;

// Models, settings, cache, logs, and generated subtitles are deliberately not
// listed in [UninstallDelete]. Upgrades and uninstall preserve user data.
