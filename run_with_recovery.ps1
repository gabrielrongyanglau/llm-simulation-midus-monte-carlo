param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.\.venv\Scripts\python.exe')) { throw 'Run setup.ps1 in the MIDUS_Monte_Carlo_Medium folder first.' }
if ($CheckOnly) {
    & '.\.venv\Scripts\python.exe' -u .\run_with_recovery.py --check-only
} else {
    & '.\.venv\Scripts\python.exe' -u .\run_with_recovery.py
}
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 130) { throw 'Collector paused for review. Read the message above. Saved work is retained.' }
