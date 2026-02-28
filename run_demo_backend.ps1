$ErrorActionPreference = "Stop"

$backendPath = Join-Path $PSScriptRoot "backend"
if (-not (Test-Path -LiteralPath $backendPath)) {
    throw "Could not find backend directory at: $backendPath"
}

if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python was not found. Install Python and try again."
}

$venvPath = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Creating local virtual environment at: $venvPath"

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv $venvPath
    }
    else {
        & python -m venv $venvPath
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create virtual environment."
    }
}

function Invoke-Python {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $venvPython @Arguments

    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($Arguments -join ' ')"
    }
}

Push-Location $backendPath
try {
    Write-Host "Installing backend dependencies..."
    Invoke-Python -Arguments @("-m", "pip", "install", "-r", "requirements.txt")

    Write-Host "Running one-time backend setup..."
    Invoke-Python -Arguments @("setup_environment.py")

    Write-Host "Starting backend app..."
    Invoke-Python -Arguments @("app.py")
}
finally {
    Pop-Location
}
