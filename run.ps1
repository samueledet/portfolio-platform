# Starts the platform at http://127.0.0.1:8800  (Ctrl+C to stop)
$py = "$env:USERPROFILE\.venvs\portfolio-platform\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
Set-Location $PSScriptRoot
Start-Process "http://127.0.0.1:8800"
& $py -m uvicorn pcp.api:app --host 127.0.0.1 --port 8800
