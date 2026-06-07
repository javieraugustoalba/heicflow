$ErrorActionPreference = "Stop"

$ProjectRoot = (Get-Location).Path
$ZipName = "heicflow_azure_deploy_ready_local.zip"
$ZipPath = Join-Path $ProjectRoot $ZipName
$Stage = Join-Path $env:TEMP "heicflow_azure_stage"

Write-Host "Project root: $ProjectRoot" -ForegroundColor Cyan

if (Test-Path $Stage) {
    Remove-Item $Stage -Recurse -Force
}

New-Item -ItemType Directory -Path $Stage | Out-Null

if (Test-Path $ZipPath) {
    Remove-Item $ZipPath -Force
}

$ExcludedDirs = @(
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".idea",
    ".vscode",
    "node_modules",
    "instance"
)

$ExcludedFiles = @(
    ".env",
    ".env.local",
    ".env.production",
    "app.db",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.zip",
    "*.bak"
)

function Is-ExcludedPath {
    param([string]$FullPath)

    $relative = Resolve-Path -LiteralPath $FullPath | ForEach-Object {
        $_.Path.Substring($ProjectRoot.Length).TrimStart("\", "/")
    }

    foreach ($dir in $ExcludedDirs) {
        if ($relative -eq $dir -or $relative.StartsWith("$dir\") -or $relative.StartsWith("$dir/")) {
            return $true
        }
    }

    foreach ($pattern in $ExcludedFiles) {
        if ((Split-Path $FullPath -Leaf) -like $pattern) {
            return $true
        }
    }

    return $false
}

Write-Host "Copying clean project files..." -ForegroundColor Cyan

Get-ChildItem -Path $ProjectRoot -Recurse -File | ForEach-Object {
    if (Is-ExcludedPath $_.FullName) {
        return
    }

    $relativePath = $_.FullName.Substring($ProjectRoot.Length).TrimStart("\", "/")
    $destination = Join-Path $Stage $relativePath
    $destinationDir = Split-Path $destination -Parent

    if (!(Test-Path $destinationDir)) {
        New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
    }

    Copy-Item $_.FullName $destination -Force
}

Write-Host "Creating ZIP..." -ForegroundColor Cyan

Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -Force

Write-Host ""
Write-Host "ZIP created successfully:" -ForegroundColor Green
Write-Host $ZipPath -ForegroundColor Green
Write-Host ""
Write-Host "This ZIP excludes .env, .venv, app.db, caches, logs and old zips." -ForegroundColor Yellow