$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& '.\.venv\Scripts\python.exe' -u -m src.collection --check-only
if ($LASTEXITCODE -ne 0) { throw 'Collection paused. Saved work retained. Read the error above; rerun the same command after resolving the issue.' }
