$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = "C:\Users\USER\1.Claude Code\swparty120-tools\tools\stock_screen_venv\Scripts\python.exe"
$Script = Join-Path $ScriptDir "run_daily_screen.py"

& $Python $Script --once
