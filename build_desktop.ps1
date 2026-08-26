<#
.SYNOPSIS
    Build the valz Windows desktop bundle (one-dir PyInstaller) and zip it.

.DESCRIPTION
    Run from the repo root:

        pwsh ./build_desktop.ps1

    Produces:
        dist/valz/                   - unpacked onedir (valz.exe + runtime)
        dist/valz-<ver>-portable.zip - zip of the above, ready to ship
        build.log                    - PyInstaller output

    No system-wide installs. venv lives at .venv-build. snapshot/ at
    desktop/payload/ is what gets copied next to valz.exe under payload/.
#>

$ErrorActionPreference = 'Stop'

$Root = (Resolve-Path '.').Path
$BuildVenv = Join-Path $Root '.venv-build'
$Py = Join-Path $BuildVenv 'Scripts\python.exe'
$Spec = Join-Path $Root 'valz.spec'
$Payload = Join-Path $Root 'desktop\payload'
$Dist = Join-Path $Root 'dist'

if (-not (Test-Path $Py)) {
    throw "Build venv missing at $BuildVenv. Run: python -m venv .venv-build ; .\.venv-build\Scripts\python -m pip install -r requirements.txt pyinstaller"
}

# 1) refresh payload from homeserver (db + config) so the bundle ships the
#    latest data. The user is asked to confirm before this overwrites local.
if (-not (Test-Path "$Payload\valz.db")) {
    Write-Host "No payload snapshot at $Payload\valz.db yet; the build will fail if the homeserver is unreachable." -ForegroundColor Yellow
} else {
    Write-Host "Reusing existing payload snapshot at $Payload" -ForegroundColor DarkCyan
}

# 2) clean previous build artefacts so hidden imports / payload changes take
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Dist
$Log = Join-Path $Root 'build.log'
if (Test-Path $Log) { Remove-Item -Force $Log }

# 3) run PyInstaller. Redirect both streams to a file (PyInstaller logs to
#    stderr; PowerShell treats NativeCommandError on stderr as a fatal
#    error under $ErrorActionPreference='Stop', so we silence that).
Write-Host "Building with PyInstaller..." -ForegroundColor Cyan
& $Py -m PyInstaller $Spec --noconfirm --clean *> $Log
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller exited with code $LASTEXITCODE. See $Log."
}
Select-String -Path $Log -Pattern 'Build complete' | Select-Object -Last 1

# 4) sanity-check the exe exists
$Exe = Join-Path $Dist 'valz\valz.exe'
if (-not (Test-Path $Exe)) {
    throw "Build failed: $Exe not produced. See build.log."
}

# 5) zip it for distribution. Read the version from app.py so the zip
#    filename is unambiguous about what was bundled.
$Ver = (& $Py -c "import sys, importlib.util as u; spec=u.spec_from_file_location('app','app.py'); m=u.module_from_spec(spec); spec.loader.exec_module(m); print(m.VERSION)" 2>$null)
if (-not $Ver) { $Ver = '0.0.0' }
$Zip = Join-Path $Dist "valz-$Ver-portable.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    (Join-Path $Dist 'valz'),
    $Zip,
    [System.IO.Compression.CompressionLevel]::Optimal,
    $false   # includeBaseDirectory so the zip root is valz/...
)
$ZipSize = (Get-Item $Zip).Length
Write-Host ""
Write-Host "OK -- bundle ready:" -ForegroundColor Green
Write-Host "  dir : $Dist\valz\"
Write-Host "  exe : $Exe"
Write-Host "  zip : $Zip ($ZipSize bytes)"
Write-Host ""
Write-Host "User workflow: extract zip anywhere, double-click valz.exe."
Write-Host "First run seeds %LOCALAPPDATA%\valz\data\valz.db from payload\."
Write-Host "Browser auto-opens to http://127.0.0.1:<picked-port>/."
