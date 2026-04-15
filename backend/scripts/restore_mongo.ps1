param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFolder
)

if (-not $env:MONGO_URI) {
    throw "MONGO_URI is required"
}

if (-not $env:MONGO_DB_NAME) {
    throw "MONGO_DB_NAME is required"
}

$targetPath = Join-Path $BackupFolder $env:MONGO_DB_NAME
if (-not (Test-Path $targetPath)) {
    throw "Backup folder does not contain expected path: $targetPath"
}

Write-Host "Restoring database $($env:MONGO_DB_NAME) from $targetPath"
mongorestore --uri="$($env:MONGO_URI)" --drop --db="$($env:MONGO_DB_NAME)" "$targetPath"
Write-Host "Restore completed"
