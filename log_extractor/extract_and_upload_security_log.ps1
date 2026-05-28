param(
    [string]$BackendUrl = "http://localhost:2222/api/logs/upload",
    [string]$OutputDir = "$PSScriptRoot\out",
    [string]$EndpointID = "123",
    [string]$CustomLogPath
)

$ErrorActionPreference = "Stop"
$logID = "0"

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

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

if ($CustomLogPath) {
    if (-not (Test-Path -LiteralPath $CustomLogPath)) {
        throw "Custom log path not found: $CustomLogPath"
    }

    $logFilePath = (Resolve-Path -LiteralPath $CustomLogPath).Path
    Write-Host "Using custom log file: $logFilePath"
}
else {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $logFilePath = Join-Path $OutputDir "security_$timestamp.evtx"

    Write-Host "Exporting Windows Security log..."
    wevtutil epl Security "$logFilePath" /ow:true

    if (-not (Test-Path $logFilePath)) {
        throw "Failed to export Security log."
    }
}
Write-Host "Uploading log file to $BackendUrl ..."
try {
    $additionalForm = @()

    # If the file looks like JSON, try to extract a SourceName field to inform the server
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

    $curlArgs = @('-sS','-f','-X','POST',"$BackendUrl","-F","endpointID=$EndpointID","-F","logID=$logID","-F","log_file=@$logFilePath")
    if ($additionalForm.Count -gt 0) { $curlArgs += $additionalForm }

    $response = & curl.exe @curlArgs

    if ($LASTEXITCODE -ne 0) {
        throw "Upload failed."
    }

    Write-Host "Upload response:"
    Write-Host $response
}
finally {
    if (-not $CustomLogPath -and (Test-Path $logFilePath)) {
        Remove-Item -LiteralPath $logFilePath -Force
        Write-Host "Temporary log deleted: $logFilePath"
    }
}
Write-Host "Done."
