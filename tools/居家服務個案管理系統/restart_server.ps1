Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Start-Process -FilePath "$PSScriptRoot\.venv\Scripts\python.exe" -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8001" -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
Start-Sleep -Seconds 4
$c = Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
if ($c) { Write-Output "OK: server running at http://127.0.0.1:8001" } else { Write-Output "FAILED" }
