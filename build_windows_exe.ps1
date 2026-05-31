$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildEnv = if ($env:SLEAP_BUILD_ENV) { $env:SLEAP_BUILD_ENV } else { Join-Path $ScriptDir ".build_venv" }
$Python = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "" }

if (!$Python) {
  foreach ($Candidate in @("python3.13", "python3.12", "python3.11", "python")) {
    if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
      $Python = $Candidate
      break
    }
  }
}

if (!$Python) {
  throw "Could not find Python 3.11+. Install Python first, then rerun this script."
}

& $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 'Python 3.11+ required, found '+sys.version.split()[0])"

if (!(Test-Path $BuildEnv)) {
  Write-Host "Creating build environment: $BuildEnv"
  & $Python -m venv $BuildEnv
}

$BuildPython = Join-Path $BuildEnv "Scripts\python.exe"
if (!(Test-Path $BuildPython)) {
  throw "Could not find build Python at $BuildPython"
}

& $BuildPython -m pip install --upgrade pip wheel pyinstaller

Push-Location $ScriptDir
try {
  & $BuildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name SLEAP-Pipeline-Manager `
    --add-data "gl_sync;gl_sync" `
    "gl_sync\sleap_pipeline_gui.py"
} finally {
  Pop-Location
}

$Exe = Join-Path $ScriptDir "dist\SLEAP-Pipeline-Manager.exe"
if (!(Test-Path $Exe)) {
  throw "Build finished but exe was not found at $Exe"
}

Write-Host ""
Write-Host "Built:"
Write-Host "  $Exe"
Write-Host ""
Write-Host "Double-click this exe to open the pipeline GUI."
Write-Host "Note: SLEAP itself is not bundled. Install it with:"
Write-Host "  powershell -ExecutionPolicy Bypass -File gl_sync\install_local_gui.ps1"
