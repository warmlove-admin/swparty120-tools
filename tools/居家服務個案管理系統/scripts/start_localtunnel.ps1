$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:npm_config_cache = Join-Path (Get-Location) "tools_bin\npm-cache"
New-Item -ItemType Directory -Force -Path "data" | Out-Null
npx.cmd --yes localtunnel --port 8001 --local-host 127.0.0.1 2>&1 | Tee-Object -FilePath "data\localtunnel.log"
