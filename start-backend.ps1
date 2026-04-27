# 启动后端服务专用脚本 (Task-20260427-001)
# 严格遵循AGENTS.md最小任务原则：只启动FastAPI后端服务

Write-Host "=== 启动 Yuxi Backtrader 后端服务 (FastAPI) ===" -ForegroundColor Cyan
Write-Host "=============================================================" -ForegroundColor Cyan
Write-Host "任务编号: Task-20260427-001 - 启动后端服务" -ForegroundColor Yellow
Write-Host "当前目录: $(Get-Location)" -ForegroundColor Gray

# 检查Python环境
try {
    $pythonVersion = python --version
    Write-Host "[OK] Python环境正常: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python未安装或不在PATH中" -ForegroundColor Red
    exit 1
}

# 安装依赖（如果需要）
if (-not (Test-Path "backend\venv")) {
    Write-Host "创建虚拟环境并安装依赖..." -ForegroundColor Yellow
    python -m venv backend\venv
    & backend\venv\Scripts\pip install -r backend\requirements.txt
    Write-Host "[OK] 依赖安装完成" -ForegroundColor Green
}

# 切换到backend目录启动服务
Write-Host "`n启动 FastAPI 后端服务 (端口 8000)..." -ForegroundColor Cyan
Write-Host "API文档将可用: http://localhost:8000/docs" -ForegroundColor White
Write-Host "健康检查接口: http://localhost:8000/health" -ForegroundColor White
Write-Host "`n按 Ctrl+C 停止服务" -ForegroundColor Yellow

# 启动后端服务
cd backend
if (Test-Path "..\.env") {
    Copy-Item "..\.env" ".env" -Force
    Write-Host "[OK] 已复制环境变量配置" -ForegroundColor Gray
}

# 使用uvicorn启动
Write-Host "[START] 启动后端服务 (uvicorn)..." -ForegroundColor Cyan
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
