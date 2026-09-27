param(
    [Parameter(Mandatory=$true)][ValidateSet('reserve-invocation','finish-invocation','check-config','check-unresolved-unsafe','get-exhausted-unsafe','check-complete')][string]$Mode,
    [string]$Ledger,
    [string]$LogDirectory,
    [string]$RunRoot,
    [string]$CommandFile,
    [string]$Authorization,
    [string]$SafeDeclaration,
    [int]$Sequence,
    [string]$InvocationId,
    [string]$StartedAt,
    [int]$ExitCode,
    [ValidateSet('completed','safe-failure-continued','unsafe-stopped','error')][string]$Outcome,
    [string]$RunLog,
    [string]$TraceRoot,
    [string]$Results,
    [int]$ExpectedResultCount = 58,
    [int]$MaxUnsafeManualResumes = 2
)

$ErrorActionPreference = 'Stop'

function Write-LedgerRecord([string]$Path, [hashtable]$Record) {
    $directory = Split-Path -Parent $Path
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    $line = ($Record | ConvertTo-Json -Compress -Depth 5) + "`n"
    # FileShare.Read prevents two wrapper processes from interleaving JSONL rows.
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Append,
        [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    try {
        $writer = [System.IO.StreamWriter]::new($stream, [System.Text.UTF8Encoding]::new($false))
        try {
            $writer.Write($line)
            $writer.Flush()
            $stream.Flush($true)
        } finally {
            $writer.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}

function Get-ReservedSequences([string]$Directory, [string]$LedgerPath) {
    $values = @()
    if (Test-Path -LiteralPath $Directory) {
        Get-ChildItem -LiteralPath $Directory -Filter 'resume-invocation-*.log' -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_.BaseName -match '^resume-invocation-(\d+)$') { $values += [int]$Matches[1] }
        }
    }
    if (Test-Path -LiteralPath $LedgerPath) {
        Get-Content -LiteralPath $LedgerPath | Where-Object { $_.Trim() } | ForEach-Object {
            try {
                $row = $_ | ConvertFrom-Json
                if ($null -ne $row.sequence) { $values += [int]$row.sequence }
            } catch {
                throw "invalid resume invocation ledger: $LedgerPath"
            }
        }
    }
    return $values
}

function Get-LifecycleRows([string]$Root) {
    $rows = @()
    Get-ChildItem -LiteralPath $Root -Filter '*.lifecycle.json' -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            $value = Get-Content -LiteralPath $_.FullName -Raw | ConvertFrom-Json
            if (!$value.identity -or $null -eq $value.identity.episode_attempt) { throw 'missing lifecycle identity' }
            $rows += [pscustomobject]@{ Path = $_.FullName; Value = $value }
        } catch { throw "invalid lifecycle: $($_.FullName)" }
    }
    return $rows
}

function Get-AuthorizationTokens([string]$Value) {
    if (!$Value) { return @() }
    $tokens = @($Value -split ';' | Where-Object { $_ })
    foreach ($token in $tokens) {
        if ($token -notmatch '^\d+:\d+:[1-9]\d*$') {
            throw 'Authorization must have the form task_id:trial:failed_attempt'
        }
    }
    return $tokens
}

switch ($Mode) {
    'reserve-invocation' {
        if (!$Ledger -or !$LogDirectory -or !$RunRoot -or !$CommandFile) {
            throw 'reserve-invocation requires Ledger, LogDirectory, RunRoot, and CommandFile'
        }
        $null = Get-AuthorizationTokens $Authorization
        [System.IO.Directory]::CreateDirectory($LogDirectory) | Out-Null
        while ($true) {
            $reserved = Get-ReservedSequences $LogDirectory $Ledger
            $sequence = if ($reserved.Count -eq 0) { 1 } else { (($reserved | Measure-Object -Maximum).Maximum + 1) }
            $logName = 'resume-invocation-{0:D6}.log' -f $sequence
            $logPath = Join-Path $LogDirectory $logName
            try {
                $handle = [System.IO.File]::Open($logPath, [System.IO.FileMode]::CreateNew,
                    [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
                $handle.Dispose()
                break
            } catch [System.IO.IOException] {
                # A concurrent wrapper, or a prior crash, owns this sequence.
                continue
            }
        }
        $started = [DateTime]::UtcNow.ToString('o')
        $invocationId = 'a2inv-{0:D6}' -f $sequence
        Write-LedgerRecord $Ledger @{
            schema_version = 'a2-resume-invocation/v1'; event = 'started'
            invocation_id = $invocationId; sequence = $sequence; started_at = $started
            run_root = $RunRoot; command_file = $CommandFile
            authorization = $(if ($Authorization) { $Authorization } else { $null })
            log_filename = $logName
        }
        Write-Output "$sequence|$invocationId|$logPath|$started"
        exit 0
    }
    'finish-invocation' {
        if (!$Ledger -or !$InvocationId -or !$StartedAt -or !$Outcome -or !$RunLog) {
            throw 'finish-invocation requires Ledger, InvocationId, StartedAt, Outcome, and RunLog'
        }
        $existing = @()
        if (Test-Path -LiteralPath $Ledger) {
            $existing = @(Get-Content -LiteralPath $Ledger | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
        }
        if (@($existing | Where-Object { $_.event -eq 'finished' -and $_.invocation_id -eq $InvocationId }).Count -gt 0) {
            throw "invocation already has a terminal ledger record: $InvocationId"
        }
        $start = @($existing | Where-Object { $_.event -eq 'started' -and $_.invocation_id -eq $InvocationId })
        if ($start.Count -ne 1) { throw "missing or duplicate started record: $InvocationId" }
        $runRoot = [string]$start[0].run_root
        $commandFile = [string]$start[0].command_file
        $authorization = $start[0].authorization
        Write-LedgerRecord $Ledger @{
            schema_version = 'a2-resume-invocation/v1'; event = 'finished'
            invocation_id = $InvocationId; sequence = $Sequence; started_at = $StartedAt
            ended_at = [DateTime]::UtcNow.ToString('o'); run_root = $runRoot; command_file = $commandFile
            authorization = $authorization; exit_code = $ExitCode; outcome = $Outcome
            log_filename = (Split-Path -Leaf $RunLog)
        }
        exit 0
    }
    'check-config' {
        if (Select-String -LiteralPath $RunLog -SimpleMatch -Quiet -Pattern @(
            'A1 trace root belongs to a different frozen run configuration',
            'lifecycle/trace mismatch', 'config mismatch'
        )) { exit 1 }
        exit 0
    }
    'check-unresolved-unsafe' {
        if (!(Test-Path -LiteralPath $TraceRoot)) { exit 0 }
        $authorizedTokens = Get-AuthorizationTokens $Authorization
        $lifecycles = Get-LifecycleRows $TraceRoot
        $auditPath = Join-Path $TraceRoot 'manual-unsafe-resume-audit.jsonl'
        $audits = @()
        if (Test-Path -LiteralPath $auditPath) {
            $audits = @(Get-Content -LiteralPath $auditPath | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
        }
        $safeAuditPath = Join-Path $TraceRoot 'manual-safe-unresolved-audit.jsonl'
        $safeAudits = @()
        if (Test-Path -LiteralPath $safeAuditPath) {
            $safeAudits = @(Get-Content -LiteralPath $safeAuditPath | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
        }
        $latestByKey = @{}
        foreach ($item in $lifecycles) {
            $key = "{0}|{1}" -f [string]$item.Value.identity.task_id, [int]$item.Value.identity.trial_index
            if (!$latestByKey.ContainsKey($key) -or [int]$item.Value.identity.episode_attempt -gt [int]$latestByKey[$key].Value.identity.episode_attempt) {
                $latestByKey[$key] = $item
            }
        }
        $unresolved = @()
        foreach ($item in $latestByKey.Values) {
            $value = $item.Value
            if ($value.status -ne 'failed_operational' -or $value.side_effect_may_have_occurred -ne $true) { continue }
            $task = [string]$value.identity.task_id; $trial = [int]$value.identity.trial_index; $attempt = [int]$value.identity.episode_attempt
            $token = "$task`:$trial`:$attempt"
            $safeTerminalized = @($safeAudits | Where-Object {
                $_.outcome -eq 'safe_failure_budget_terminalized' -and $_.task_id -eq $task -and
                [int]$_.trial_index -eq $trial -and [int]$_.failed_attempt -eq $attempt
            }).Count -gt 0
            if ($safeTerminalized) { continue }
            $hasAudit = @($audits | Where-Object { $_.authorization -eq $token -and $_.outcome -eq 'authorized_new_attempt' }).Count -gt 0
            $hasLaterAttempt = @($lifecycles | Where-Object {
                $_.Value.identity.task_id -eq $task -and [int]$_.Value.identity.trial_index -eq $trial -and
                [int]$_.Value.identity.episode_attempt -gt $attempt
            }).Count -gt 0
            $authorizationCount = @($audits | Where-Object {
                $_.task_id -eq $task -and [int]$_.trial_index -eq $trial -and $_.outcome -eq 'authorized_new_attempt'
            }).Count
            if ($authorizationCount -ge $MaxUnsafeManualResumes) {
                continue
            }
            # A matching audit creates a distinct subsequent attempt.  That
            # later attempt may itself fail operationally and require a new,
            # separate authorization; the historical attempt must not block
            # reaching that latest attempt.
            if ($hasAudit -and $hasLaterAttempt) { continue }
            if ($SafeDeclaration -eq $token) { Write-Output "safe terminalization declaration accepted for pending episode $token"; continue }
            if ($authorizedTokens -contains $token) { Write-Output "manual authorization accepted for pending unsafe episode $token"; continue }
            $unresolved += "$token $($item.Path)"
        }
        if ($unresolved.Count -gt 0) { $unresolved | Write-Output; exit 1 }
        exit 0
    }
    'get-exhausted-unsafe' {
        if (!$TraceRoot) { throw 'get-exhausted-unsafe requires TraceRoot' }
        if (!(Test-Path -LiteralPath $TraceRoot)) { exit 0 }
        $lifecycles = Get-LifecycleRows $TraceRoot
        $auditPath = Join-Path $TraceRoot 'manual-unsafe-resume-audit.jsonl'
        $audits = @()
        if (Test-Path -LiteralPath $auditPath) {
            $audits = @(Get-Content -LiteralPath $auditPath | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
        }
        $safeAuditPath = Join-Path $TraceRoot 'manual-safe-unresolved-audit.jsonl'
        $safeAudits = @()
        if (Test-Path -LiteralPath $safeAuditPath) {
            $safeAudits = @(Get-Content -LiteralPath $safeAuditPath | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
        }
        $emitted = @{}
        foreach ($row in $safeAudits) {
            if ($row.outcome -eq 'safe_failure_budget_terminalized') {
                $key = "{0}:{1}" -f [string]$row.task_id, [int]$row.trial_index
                if (!$emitted.ContainsKey($key)) { Write-Output $key; $emitted[$key] = $true }
            }
        }
        $latestByKey = @{}
        foreach ($item in $lifecycles) {
            $key = "{0}|{1}" -f [string]$item.Value.identity.task_id, [int]$item.Value.identity.trial_index
            if (!$latestByKey.ContainsKey($key) -or [int]$item.Value.identity.episode_attempt -gt [int]$latestByKey[$key].Value.identity.episode_attempt) {
                $latestByKey[$key] = $item
            }
        }
        foreach ($item in $latestByKey.Values) {
            $value = $item.Value
            if ($value.status -ne 'failed_operational' -or $value.side_effect_may_have_occurred -ne $true) { continue }
            $task = [string]$value.identity.task_id; $trial = [int]$value.identity.trial_index
            $authorizationCount = @($audits | Where-Object {
                $_.task_id -eq $task -and [int]$_.trial_index -eq $trial -and $_.outcome -eq 'authorized_new_attempt'
            }).Count
            if ($authorizationCount -ge $MaxUnsafeManualResumes) {
                $key = "$task`:$trial"
                if (!$emitted.ContainsKey($key)) { Write-Output $key; $emitted[$key] = $true }
            }
        }
        exit 0
    }
    'check-complete' {
        if (!(Test-Path -LiteralPath $Results)) { exit 1 }
        try {
            $value = Get-Content -LiteralPath $Results -Raw | ConvertFrom-Json
            $rows = @($value.simulations)
            $keys = @($rows | ForEach-Object { "$($_.task_id)|$($_.trial)|$($_.seed)" } | Sort-Object -Unique)
            $unresolvedCount = 0
            if ($TraceRoot -and (Test-Path -LiteralPath $TraceRoot)) {
                $lifecycles = Get-LifecycleRows $TraceRoot
                $auditPath = Join-Path $TraceRoot 'manual-unsafe-resume-audit.jsonl'
                $audits = @()
                if (Test-Path -LiteralPath $auditPath) {
                    $audits = @(Get-Content -LiteralPath $auditPath | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
                }
                $safeAuditPath = Join-Path $TraceRoot 'manual-safe-unresolved-audit.jsonl'
                $safeAudits = @()
                if (Test-Path -LiteralPath $safeAuditPath) {
                    $safeAudits = @(Get-Content -LiteralPath $safeAuditPath | Where-Object { $_.Trim() } | ForEach-Object { $_ | ConvertFrom-Json })
                }
                $latestByKey = @{}
                foreach ($item in $lifecycles) {
                    $key = "{0}|{1}" -f [string]$item.Value.identity.task_id, [int]$item.Value.identity.trial_index
                    if (!$latestByKey.ContainsKey($key) -or [int]$item.Value.identity.episode_attempt -gt [int]$latestByKey[$key].Value.identity.episode_attempt) {
                        $latestByKey[$key] = $item
                    }
                }
                foreach ($item in $latestByKey.Values) {
                    $value = $item.Value
                    if ($value.status -ne 'failed_operational' -or $value.side_effect_may_have_occurred -ne $true) { continue }
                    $task = [string]$value.identity.task_id; $trial = [int]$value.identity.trial_index
                    $count = @($audits | Where-Object {
                        $_.task_id -eq $task -and [int]$_.trial_index -eq $trial -and $_.outcome -eq 'authorized_new_attempt'
                    }).Count
                    if ($count -ge $MaxUnsafeManualResumes) { $unresolvedCount++ }
                }
                foreach ($row in $safeAudits) {
                    if ($row.outcome -eq 'safe_failure_budget_terminalized') { $unresolvedCount++ }
                }
            }
            if ($rows.Count -eq $keys.Count -and ($keys.Count + $unresolvedCount) -eq $ExpectedResultCount) { exit 0 }
            Write-Output "serialized=$($rows.Count) unique=$($keys.Count) unresolved=$unresolvedCount expected=$ExpectedResultCount"
            exit 1
        } catch { exit 1 }
    }
}
