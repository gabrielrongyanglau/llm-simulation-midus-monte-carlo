$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& '.\.venv\Scripts\python.exe' -u .\run_with_recovery.py
if ($LASTEXITCODE -ne 0) { throw 'Collection paused. Saved work retained. Read the error above; rerun the same command after resolving the issue.' }
