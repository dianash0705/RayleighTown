param(
    [ValidateSet("Run", "Register", "Unregister")]
    [string]$Mode = "Run",
    [string]$BackendUrl,
    [string]$OutputDir = "$PSScriptRoot\out",
    [string]$EndpointID,
    [string]$EndpointSecret,
    [string]$ConfigPath = "$PSScriptRoot\agent_config.json",
    [string]$CustomLogPath,
    [string]$TaskName = "GirlMeetsCode-LogUploader",
    [int]$ScheduleIntervalMinutes = 15
)

$ErrorActionPreference = "Stop"
$logID = "0"

function Read-AgentConfig {
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        return @{}
    }
    try {
        return Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    }
    catch {
        throw "Failed to read agent config '$ConfigPath': $($_.Exception.Message)"
    }
}

$config = Read-AgentConfig
if (-not $EndpointID -and $config.endpointID) { $EndpointID = [string]$config.endpointID }
if (-not $EndpointSecret -and $config.endpointSecret) { $EndpointSecret = [string]$config.endpointSecret }
if (-not $BackendUrl -and $config.backendUrl) { $BackendUrl = [string]$config.backendUrl }
if ($config.taskName) { $TaskName = [string]$config.taskName }
if ($config.scheduleIntervalMinutes) { $ScheduleIntervalMinutes = [int]$config.scheduleIntervalMinutes }

if (-not $BackendUrl) { $BackendUrl = "http://localhost:443/api/logs/upload" }

function Get-SourceNameFromJsonLine {
    param(
        [string]$Line
    )

    try {
        $record = $Line | ConvertFrom-Json -AsHashtable -ErrorAction Stop
    }
    catch {
        return $null
    }

    if ($null -eq $record) {
        return $null
    }

    $payload = $record['result']
    if ($payload) {
        foreach ($field in @('SourceName', 'source', 'Source')) {
            if ($payload.ContainsKey($field)) {
                $value = $payload[$field]
                if ($value) { return [string]$value }
            }
        }

        foreach ($field in @('EventChannel', 'Channel', 'EventLogName')) {
            if ($payload.ContainsKey($field)) {
                $value = [string]$payload[$field]
                if ($value -match 'Sysmon') { return 'Microsoft-Windows-Sysmon' }
                if ($value -match 'Security') { return 'windows_security' }
            }
        }
    }

    foreach ($field in @('SourceName', 'source', 'Source')) {
        if ($record.ContainsKey($field)) {
            $value = $record[$field]
            if ($value) { return [string]$value }
        }
    }

    return $null
}

function Invoke-LogUploadRun {
    if (-not $EndpointID -or -not $EndpointSecret) {
        throw "Missing endpoint credentials. Provide -EndpointID and -EndpointSecret, or create '$ConfigPath' (see agent_config.example.json)."
    }

    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

    if ($CustomLogPath) {
        if (-not (Test-Path -LiteralPath $CustomLogPath)) {
            throw "Custom log path not found: $CustomLogPath"
        }

        $logFilePath = (Resolve-Path -LiteralPath $CustomLogPath).Path
        Write-Host "Using custom log file: $logFilePath"
        $createdTempLog = $false
    }
    else {
        $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $logFilePath = Join-Path $OutputDir "security_$timestamp.evtx"

        Write-Host "Exporting Windows Security log..."
        wevtutil epl Security "$logFilePath" /ow:true

        if (-not (Test-Path $logFilePath)) {
            throw "Failed to export Security log."
        }
        $createdTempLog = $true
    }

    Write-Host "Uploading log file to $BackendUrl ..."
    $fileSizeBytes = (Get-Item -LiteralPath $logFilePath).Length
    $fileSizeMb = [math]::Round($fileSizeBytes / 1MB, 1)
    Write-Host ("Upload size: {0} MB" -f $fileSizeMb)

    try {
        $additionalForm = @()

        if ([io.path]::GetExtension($logFilePath).ToLower() -eq '.json') {
            try {
                $firstLine = Get-Content -LiteralPath $logFilePath -TotalCount 1 -ErrorAction Stop
                $sourceName = Get-SourceNameFromJsonLine -Line $firstLine
                if ($sourceName) { $additionalForm += "-F"; $additionalForm += "sourceName=$sourceName" }
            }
            catch {
                # ignore JSON read errors and continue uploading
            }
        }

        $agentHostname = $env:COMPUTERNAME
        $agentIp = $null
        try {
            $agentIp = Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred |
                Where-Object {
                    $_.InterfaceAlias -notmatch 'Loopback' -and
                    $_.IPAddress -notmatch '^169\.254\.' -and
                    $_.IPAddress -ne '127.0.0.1'
                } |
                Select-Object -First 1 -ExpandProperty IPAddress
        }
        catch {
            $agentIp = $null
        }

        $curlArgs = @('-#','-S','-f','-X','POST',"$BackendUrl","-F","endpointID=$EndpointID","-F","endpointSecret=$EndpointSecret","-F","logID=$logID","-F","log_file=@$logFilePath")
        if ($agentHostname) {
            $curlArgs += '-F'
            $curlArgs += "hostname=$agentHostname"
        }
        if ($agentIp) {
            $curlArgs += '-F'
            $curlArgs += "ip=$agentIp"
        }
        if ($additionalForm.Count -gt 0) { $curlArgs += $additionalForm }

        $response = & curl.exe @curlArgs

        if ($LASTEXITCODE -ne 0) {
            throw "Upload failed."
        }

        Write-Host "Upload response:"
        Write-Host $response
    }
    finally {
        if ($createdTempLog -and (Test-Path $logFilePath)) {
            Remove-Item -LiteralPath $logFilePath -Force
            Write-Host "Temporary log deleted: $logFilePath"
        }
    }
}

function Register-LogUploadScheduledTask {
    if (-not $EndpointID -or -not $EndpointSecret) {
        throw "Missing endpoint credentials required for registration."
    }
    if ($ScheduleIntervalMinutes -lt 1) {
        throw "ScheduleIntervalMinutes must be at least 1."
    }

    $scriptPath = $MyInvocation.MyCommand.Path
    if (-not $scriptPath) {
        $scriptPath = $PSCommandPath
    }
    $scriptPath = (Resolve-Path -LiteralPath $scriptPath).Path

    $argumentList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$scriptPath`"",
        "-Mode", "Run",
        "-ConfigPath", "`"$ConfigPath`""
    )
    if ($BackendUrl) {
        $argumentList += @("-BackendUrl", "`"$BackendUrl`"")
    }

    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($argumentList -join " ")

    # Task Scheduler caps RepetitionDuration (~31 days). TimeSpan::MaxValue becomes invalid XML.
    # Daily trigger + 24h repetition (via CIM) runs every N minutes indefinitely.
    $startAt = (Get-Date).AddMinutes(1)
    $trigger = New-ScheduledTaskTrigger -Daily -At $startAt
    $trigger.Repetition = New-CimInstance -ClientOnly `
        -Namespace "Root/Microsoft/Windows/TaskScheduler" `
        -ClassName MSFT_TaskRepetitionPattern `
        -Property @{
            Interval = "PT${ScheduleIntervalMinutes}M"
            Duration = "P1D"
            StopAtDurationEnd = $false
        }

    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -RunLevel Highest `
        -Description "Exports the local Windows Security log and uploads it to the GirlMeetsCode backend every $ScheduleIntervalMinutes minute(s)." | Out-Null

    Write-Host "Registered scheduled task '$TaskName' (every $ScheduleIntervalMinutes minute(s))."
    Write-Host "Task action: powershell.exe $($argumentList -join ' ')"
}

function Unregister-LogUploadScheduledTask {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "Scheduled task '$TaskName' is not registered."
        return
    }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
}

switch ($Mode) {
    "Run" {
        Invoke-LogUploadRun
        Write-Host "Done."
    }
    "Register" {
        Register-LogUploadScheduledTask
        Write-Host "Registration complete. The task will run on its schedule; use -Mode Run for a one-off upload."
    }
    "Unregister" {
        Unregister-LogUploadScheduledTask
        Write-Host "Done."
    }
}
