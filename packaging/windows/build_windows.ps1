param(
    [ValidateSet("windows-cpu-x64", "windows-cuda-x64", "all")]
    [string]$Variant = "all",
    [string]$Version = "1.0.1",
    [string]$ISCC = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$BuildBase = if ($env:DAVINCI_ASR_BUILD_ROOT) {
    $env:DAVINCI_ASR_BUILD_ROOT
} else {
    Join-Path $env:LOCALAPPDATA "HEIBA\DaVinciASRBuild"
}
$BuildRoot = [IO.Path]::GetFullPath((Join-Path $BuildBase "windows"))
$DistRoot = if ($env:DAVINCI_ASR_DIST_ROOT) {
    [IO.Path]::GetFullPath($env:DAVINCI_ASR_DIST_ROOT)
} else {
    [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE "Documents\DaVinciASR-Releases"))
}
if ($BuildRoot.StartsWith($ProjectRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "DAVINCI_ASR_BUILD_ROOT must be outside the Resolve Utility source tree."
}
Set-Location $ProjectRoot

function Invoke-Checked {
    param([string]$FilePath, [string[]]$Arguments)
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

function Build-Runtime {
    param([string]$RuntimeVariant)

    $LockFile = Join-Path $ProjectRoot "requirements\locks\$RuntimeVariant.txt"
    if (-not (Test-Path $LockFile)) {
        throw "Missing hashed platform lock: $LockFile"
    }

    $VariantRoot = Join-Path $BuildRoot $RuntimeVariant
    $Venv = Join-Path $VariantRoot "venv"
    $Python = Join-Path $Venv "Scripts\python.exe"
    $PyInstaller = Join-Path $Venv "Scripts\pyinstaller.exe"

    if (Test-Path $VariantRoot) {
        Remove-Item -LiteralPath $VariantRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $VariantRoot | Out-Null

    Invoke-Checked "py.exe" @("-3.12", "-c", "import sys; assert sys.version_info[:2] == (3, 12)")
    Invoke-Checked "py.exe" @("-3.12", "-m", "venv", $Venv)
    Invoke-Checked $Python @("-m", "pip", "install", "--disable-pip-version-check", "--require-hashes", "-r", $LockFile)
    $TestsRoot = Join-Path $ProjectRoot "tests"
    if (Test-Path $TestsRoot) {
        Invoke-Checked $Python @("-m", "unittest", "discover", "-s", $TestsRoot, "-v")
    }

    $PyInstallerArgs = @(
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name", "DaVinci ASR",
        "--distpath", (Join-Path $VariantRoot "dist"),
        "--workpath", (Join-Path $VariantRoot "work"),
        "--specpath", $VariantRoot,
        "--collect-all", "transformers",
        "--collect-all", "jieba",
        "--collect-all", "nagisa",
        "--collect-all", "modelscope_hub",
        "--collect-all", "omnivad",
        "--add-data", "$(Join-Path $ProjectRoot 'models\manifest.json');models",
        (Join-Path $ProjectRoot "runtime\main.py")
    )
    Invoke-Checked $PyInstaller $PyInstallerArgs
    Invoke-Checked (Join-Path $VariantRoot "dist\DaVinci ASR\DaVinci ASR.exe") @("--data-root", (Join-Path $VariantRoot "smoke-data"), "doctor", "--cpu")
}

$Targets = if ($Variant -eq "all") {
    @("windows-cpu-x64", "windows-cuda-x64")
} else {
    @($Variant)
}

foreach ($Target in $Targets) {
    Build-Runtime $Target
}

if ($Variant -eq "all") {
    if (-not (Test-Path $ISCC)) {
        throw "Inno Setup compiler not found: $ISCC"
    }
    New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null
    Invoke-Checked $ISCC @(
        "/DAppVersion=$Version",
        "/DSourceRoot=$ProjectRoot",
        "/DRuntimeBuildRoot=$BuildRoot",
        "/DOutputRoot=$DistRoot",
        (Join-Path $PSScriptRoot "DaVinciASR.iss")
    )
    Write-Host "Installer: $(Join-Path $DistRoot 'DaVinciASRSetup.exe')"
}
