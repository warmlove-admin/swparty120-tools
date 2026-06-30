$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
New-Item -ItemType Directory -Force -Path "data" | Out-Null
& "$env:WINDIR\System32\OpenSSH\ssh.exe" -N -T -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -R 80:127.0.0.1:8000 nokey@localhost.run 2>&1 | Tee-Object -FilePath "data\localhost-run.log"
