$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvDir = if ($env:SLEAP_GUI_ENV) { $env:SLEAP_GUI_ENV } else { Join-Path $HOME "sleap_gui_env" }
$VenvPython = Join-Path $EnvDir "Scripts\python.exe"

if (Test-Path $VenvPython) {
  $Python = $VenvPython
} elseif ($env:PYTHON_BIN) {
  $Python = $env:PYTHON_BIN
} else {
  $Python = ""
  foreach ($Candidate in @("python3.13", "python3.12", "python3.11", "python")) {
    if (Get-Command $Candidate -ErrorAction SilentlyContinue) {
      $Python = $Candidate
      break
    }
  }
}

if (!$Python) {
  throw "Could not find Python. Run: powershell -ExecutionPolicy Bypass -File gl_sync/install_local_gui.ps1"
}

& $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 'Python 3.11+ required, found '+sys.version.split()[0]+'. Run: powershell -ExecutionPolicy Bypass -File gl_sync/install_local_gui.ps1')"

& $Python (Join-Path $ScriptDir "gl_sync\sleap_pipeline_gui.py")
