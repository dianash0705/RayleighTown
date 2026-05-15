param(
    [string]$BackendUrl = "http://localhost:2222/api/logs/upload",
    [string]$OutputDir = "$PSScriptRoot\out",
    [string]$EndpointID = "123",
    [string]$CustomLogPath
)

$ErrorActionPreference = "Stop"
$logID = "0"

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
    $response = curl.exe -sS -f -X POST "$BackendUrl" -F "endpointID=$EndpointID" -F "logID=$logID" -F "log_file=@$logFilePath"

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
