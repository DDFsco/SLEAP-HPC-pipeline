$ErrorActionPreference = "Stop"

$EnvDir = if ($env:SLEAP_GUI_ENV) { $env:SLEAP_GUI_ENV } else { Join-Path $HOME "sleap_gui_env" }
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
  throw "Could not find Python. Install Python 3.11+ first."
}

& $Python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 'Python 3.11+ required, found '+sys.version.split()[0])"

Write-Host "Creating local SLEAP GUI environment at: $EnvDir"
& $Python -m venv $EnvDir

$Py = Join-Path $EnvDir "Scripts\python.exe"
$Pip = Join-Path $EnvDir "Scripts\pip.exe"

if (!(Test-Path $Py)) {
  throw "Could not find venv python at $Py"
}

& $Py -m pip install --upgrade pip wheel "setuptools<82"

if (Get-Command uv -ErrorAction SilentlyContinue) {
  & uv pip install --python $Py "sleap[nn]==1.6.0"
} else {
  & $Pip install "sleap[nn]==1.6.0"
}

& $Py -c "import importlib.util, sys; missing=[n for n in ('sleap','sleap_nn') if importlib.util.find_spec(n) is None]; sys.exit('Missing imports: '+repr(missing) if missing else 0)"

$Sleap = Join-Path $EnvDir "Scripts\sleap.exe"
$SleapLabel = Join-Path $EnvDir "Scripts\sleap-label.exe"

if (Test-Path $Sleap) {
  & $Sleap --help *> $null
  Write-Host "SLEAP GUI command: $Sleap"
} elseif (Test-Path $SleapLabel) {
  & $SleapLabel --help *> $null
  Write-Host "SLEAP GUI command: $SleapLabel"
} else {
  throw "Neither sleap.exe nor sleap-label.exe was found in the venv."
}

Write-Host "Local SLEAP GUI environment verified."
Write-Host "Set sleap_label_cmd to: $Sleap"
