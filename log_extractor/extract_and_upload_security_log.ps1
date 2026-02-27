param(
    [string]$BackendUrl = "http://localhost:5000/api/logs/upload",
    [string]$OutputDir = "$PSScriptRoot\out"
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFilePath = Join-Path $OutputDir "security_$timestamp.evtx"

Write-Host "Exporting Windows Security log..."
wevtutil epl Security "$logFilePath" /ow:true

if (-not (Test-Path $logFilePath)) {
    throw "Failed to export Security log."
}

Write-Host "Uploading log file to $BackendUrl ..."
try {
    $response = curl.exe -sS -f -X POST "$BackendUrl" -F "log_file=@$logFilePath"

    if ($LASTEXITCODE -ne 0) {
        throw "Upload failed."
    }

    Write-Host "Upload response:"
    Write-Host $response
}
finally {
    if (Test-Path $logFilePath) {
        Remove-Item -LiteralPath $logFilePath -Force
        Write-Host "Temporary log deleted: $logFilePath"
    }
}
Write-Host "Done."
