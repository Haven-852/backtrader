# start-all.ps1
# Check docker-compose containers, then start local frontend + backend.
# ASCII-only: avoids PowerShell 5.x UTF-8 without BOM parse issues.

$ErrorActionPreference = "Continue"
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = Get-Location | Select-Object -ExpandProperty Path }
Set-Location $ProjectRoot

Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "  AIquant Backtrader - start-all" -ForegroundColor Cyan
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "ProjectRoot: $ProjectRoot" -ForegroundColor Gray
Write-Host ""

# Containers from docker-compose.yml (container_name)
# Local FE/BE do not need compose frontend/backend/nginx; still report them.
$Containers = @(
    @{ Name = "backtrader-postgres";   Service = "postgres";   Need = $true;  Port = "15432" },
    @{ Name = "backtrader-redis";      Service = "redis";      Need = $true;  Port = "16379" },
    @{ Name = "backtrader-influxdb";   Service = "influxdb";   Need = $true;  Port = "8086" },
    @{ Name = "backtrader-minio";      Service = "minio";      Need = $true;  Port = "9000/9001" },
    @{ Name = "backtrader-clickhouse"; Service = "clickhouse"; Need = $false; Port = "18123" },
    @{ Name = "backtrader-frontend";   Service = "frontend";   Need = $false; Port = "5173(docker)" },
    @{ Name = "backtrader-backend";    Service = "backend";    Need = $false; Port = "8000(docker)" },
    @{ Name = "backtrader-nginx";      Service = "nginx";      Need = $false; Port = "8080" }
)

function Test-DockerEngine {
    try {
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
        return $true
    } catch {
        return $false
    }
}

function Get-ContainerState([string]$Name) {
    # Returns: running | exited | missing | unknown
    try {
        $status = docker inspect -f "{{.State.Status}}" $Name 2>$null
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($status)) {
            return "missing"
        }
        return $status.Trim()
    } catch {
        return "unknown"
    }
}

# ----- 1) Docker + container check -----
Write-Host "[1/3] Check Docker containers (docker-compose.yml)" -ForegroundColor Yellow
Write-Host ""

$dockerOk = Test-DockerEngine
if (-not $dockerOk) {
    Write-Host "  [WARN] Docker engine is NOT running (or docker CLI missing)." -ForegroundColor Red
    Write-Host "         Start Docker Desktop if you need Postgres/Redis/etc." -ForegroundColor Gray
    Write-Host "         Will still start frontend + backend." -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "  [OK] Docker engine is running" -ForegroundColor Green
    Write-Host ""
    Write-Host ("  {0,-26} {1,-12} {2,-10} {3}" -f "CONTAINER", "SERVICE", "NEED", "STATUS") -ForegroundColor DarkGray
    Write-Host ("  {0,-26} {1,-12} {2,-10} {3}" -f ("-" * 26), ("-" * 12), ("-" * 10), ("-" * 18)) -ForegroundColor DarkGray

    $needDown = @()
    foreach ($c in $Containers) {
        $state = if ($dockerOk) { Get-ContainerState $c.Name } else { "unknown" }
        $needTag = if ($c.Need) { "yes" } else { "optional" }

        if ($state -eq "running") {
            Write-Host ("  {0,-26} {1,-12} {2,-10} " -f $c.Name, $c.Service, $needTag) -NoNewline
            Write-Host ("already running  (port {0})" -f $c.Port) -ForegroundColor Green
        } elseif ($state -eq "missing") {
            Write-Host ("  {0,-26} {1,-12} {2,-10} " -f $c.Name, $c.Service, $needTag) -NoNewline
            Write-Host ("NOT started       (port {0})" -f $c.Port) -ForegroundColor $(if ($c.Need) { "Red" } else { "DarkYellow" })
            if ($c.Need) { $needDown += $c.Service }
        } else {
            Write-Host ("  {0,-26} {1,-12} {2,-10} " -f $c.Name, $c.Service, $needTag) -NoNewline
            Write-Host ("$state            (port {0})" -f $c.Port) -ForegroundColor Yellow
            if ($c.Need) { $needDown += $c.Service }
        }
    }

    Write-Host ""
    if ($needDown.Count -gt 0) {
        Write-Host "  [INFO] Required storage containers not running: $($needDown -join ', ')" -ForegroundColor Yellow
        Write-Host "         Tip: docker compose -f docker-compose.storage.yml up -d $($needDown -join ' ')" -ForegroundColor Gray
        Write-Host "         Data persists on host: data\docker\ (survives Docker Desktop wipe)" -ForegroundColor Gray
    } else {
        Write-Host "  [OK] All required storage containers are running" -ForegroundColor Green
    }
    Write-Host ""
}

# ----- 2) Backend -----
Write-Host "[2/3] Start backend (FastAPI :8000)" -ForegroundColor Yellow
$BackendScript = Join-Path $ProjectRoot "start-backend.ps1"
if (-not (Test-Path $BackendScript)) {
    Write-Host "  [ERROR] start-backend.ps1 not found" -ForegroundColor Red
} else {
    Start-Process powershell -WorkingDirectory $ProjectRoot -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-File", $BackendScript
    )
    Write-Host "  [OK] Backend window opened -> http://localhost:8000" -ForegroundColor Green
    Write-Host "       Docs: http://localhost:8000/docs" -ForegroundColor Gray
}
Write-Host ""

# ----- 3) Frontend -----
Write-Host "[3/3] Start frontend (Vite :5173)" -ForegroundColor Yellow
$WebDir = Join-Path $ProjectRoot "web"
if (-not (Test-Path $WebDir)) {
    Write-Host "  [ERROR] web/ directory not found" -ForegroundColor Red
} else {
    $NodeModules = Join-Path $WebDir "node_modules"
    $feCmd = @"
Set-Location '$WebDir'
Write-Host '=== Frontend (Vite) ===' -ForegroundColor Cyan
if (-not (Test-Path 'node_modules')) {
  Write-Host 'npm install ...' -ForegroundColor Yellow
  npm install
}
Write-Host 'npm run dev ...' -ForegroundColor Cyan
npm run dev
"@
    Start-Process powershell -WorkingDirectory $WebDir -ArgumentList @(
        "-NoExit",
        "-ExecutionPolicy", "Bypass",
        "-Command", $feCmd
    )
    if (Test-Path $NodeModules) {
        Write-Host "  [OK] Frontend window opened (node_modules present)" -ForegroundColor Green
    } else {
        Write-Host "  [OK] Frontend window opened (will npm install then npm run dev)" -ForegroundColor Green
    }
    Write-Host "       UI: http://localhost:5173" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "  Done. Two new windows: backend + frontend." -ForegroundColor Green
Write-Host "  Frontend : http://localhost:5173" -ForegroundColor White
Write-Host "  Backend  : http://localhost:8000" -ForegroundColor White
Write-Host "  API Docs : http://localhost:8000/docs" -ForegroundColor White
Write-Host "=============================================================" -ForegroundColor Cyan
