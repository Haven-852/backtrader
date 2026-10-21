# backup-postgres.ps1
# Dump Postgres to host disk (survives Docker Desktop wipe).
# Usage: powershell -ExecutionPolicy Bypass -File scripts\backup-postgres.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = (Get-Location).Path }

$BackupRoot = Join-Path $ProjectRoot "data\backups\postgres"
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outFile = Join-Path $BackupRoot "backtrader_$stamp.dump"

Write-Host "Backing up backtrader-postgres -> $outFile" -ForegroundColor Cyan
docker exec -e PGPASSWORD=backtrader123 backtrader-postgres `
  pg_dump -U quant -d backtrader -Fc -f /tmp/backtrader.dump
docker cp backtrader-postgres:/tmp/backtrader.dump $outFile
docker exec backtrader-postgres rm -f /tmp/backtrader.dump

# Keep last 14 dumps
Get-ChildItem $BackupRoot -Filter "backtrader_*.dump" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -Skip 14 |
  ForEach-Object { Remove-Item $_.FullName -Force }

$sizeMb = [math]::Round((Get-Item $outFile).Length / 1MB, 2)
Write-Host "OK: $outFile ($sizeMb MB)" -ForegroundColor Green
Write-Host "Restore example:" -ForegroundColor Gray
Write-Host "  docker exec -i -e PGPASSWORD=backtrader123 backtrader-postgres pg_restore -U quant -d backtrader --clean --if-exists < dump-via-docker-cp" -ForegroundColor DarkGray
