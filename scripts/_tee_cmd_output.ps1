param(
    [Parameter(Mandatory = $true)][string]$CommandFile,
    [Parameter(Mandatory = $true)][string]$RunLog,
    [string]$CommandArgument = ""
)

$logDirectory = Split-Path -Parent $RunLog
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
if ((Test-Path -LiteralPath $RunLog) -and (Get-Item -LiteralPath $RunLog).Length -ne 0) {
    throw "refusing to overwrite a non-empty invocation log: $RunLog"
}

# Keep native cmd/tau2 output visible while retaining an attempt-local log.
# Invoke the batch file directly from PowerShell.  Wrapping it in ``cmd /c
# call`` causes a second percent-expansion pass, which corrupts valid FOR
# variables such as ``%%A`` in the called wrapper.
# CommandArgument is an already validated task:trial:attempt token; it is passed
# as one positional argument so the called wrapper can forward the explicit
# authorization exactly once.
if ([string]::IsNullOrWhiteSpace($CommandArgument)) {
    & $CommandFile 2>&1 | Tee-Object -FilePath $RunLog
} else {
    if ($CommandArgument -notmatch '^\d+:\d+:[1-9]\d*$') {
        throw "CommandArgument must have the form task_id:trial:failed_attempt"
    }
    & $CommandFile $CommandArgument 2>&1 | Tee-Object -FilePath $RunLog
}
$exitCode = $LASTEXITCODE
exit $exitCode
