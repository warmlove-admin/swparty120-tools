$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $ScriptDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("run_window_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-RunLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value $line
}

try {
    Write-RunLog "START"
    $Python = "C:\Users\USER\1.Claude Code\swparty120-tools\tools\stock_screen_venv\Scripts\python.exe"
    $Script = Join-Path $ScriptDir "run_daily_screen.py"
    Write-RunLog "Python=$Python"
    Write-RunLog "Script=$Script"

    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $PreviousNativePreference = $null
    $HadNativePreference = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    if ($HadNativePreference) {
        $PreviousNativePreference = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
    }

    & $Python $Script --window 2>&1 | ForEach-Object {
        Write-RunLog ([string]$_)
    }
    $exitCode = $LASTEXITCODE

    if ($HadNativePreference) {
        $PSNativeCommandUseErrorActionPreference = $PreviousNativePreference
    }
    $ErrorActionPreference = $PreviousErrorActionPreference

    Write-RunLog "END exit_code=$exitCode"
    exit $exitCode
} catch {
    Write-RunLog ("ERROR " + $_.Exception.Message)
    Write-RunLog ("STACK " + $_.ScriptStackTrace)
    exit 1
}
