param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath
)

$ErrorActionPreference = "Stop"

$sourceRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$targetRoot = [System.IO.Path]::GetFullPath($TargetPath)

if ($targetRoot -eq $sourceRoot) {
    throw "Target path cannot be the current project directory."
}

if ($targetRoot.StartsWith($sourceRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Target path cannot be inside the current project directory. Choose a different location."
}

if (-not (Test-Path -LiteralPath $targetRoot)) {
    New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
}

$existingItems = Get-ChildItem -LiteralPath $targetRoot -Force
if ($existingItems.Count -gt 0) {
    Write-Host "Target directory is not empty: $targetRoot"
    $confirmation = Read-Host "Type YES to clean it and continue"

    if ($confirmation -ne "YES") {
        Write-Host "Aborted. No files were changed."
        exit 1
    }

    Get-ChildItem -LiteralPath $targetRoot -Force | Remove-Item -Recurse -Force
}

$excludeDirs = @(".git", ".venv", "__pycache__")
$excludeFiles = @("*.pyc", "*.db")

$robocopyArgs = @(
    $sourceRoot,
    $targetRoot,
    "/E",
    "/R:1",
    "/W:1",
    "/NFL",
    "/NDL",
    "/NP",
    "/NJH",
    "/NJS",
    "/XD"
) + $excludeDirs + @(
    "/XF"
) + $excludeFiles

& robocopy @robocopyArgs | Out-Null

if ($LASTEXITCODE -gt 7) {
    throw "Copy failed with robocopy exit code $LASTEXITCODE"
}

Write-Host "Project copied to: $targetRoot"
Write-Host "Next steps:"
Write-Host "1) cd '$targetRoot'"
Write-Host "2) ./run_demo_backend.ps1"
