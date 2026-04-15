param(
    [string]$BackupDir = ".\backups"
)

if (-not $env:MONGO_URI) {
    throw "MONGO_URI is required"
}

if (-not $env:MONGO_DB_NAME) {
    throw "MONGO_DB_NAME is required"
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$outDir = Join-Path $BackupDir "mongo-$($env:MONGO_DB_NAME)-$timestamp"
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

Write-Host "Creating backup at $outDir"
mongodump --uri="$($env:MONGO_URI)" --db="$($env:MONGO_DB_NAME)" --out="$outDir"
Write-Host "Backup completed"
