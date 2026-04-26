# Yuxi Vue3 Agent Platform 一键启动脚本
# 所有服务运行在 Docker 容器中

Write-Host "🚀 启动 语析 · Yuxi Backtrader Agent Platform (Vue3版)" -ForegroundColor Cyan
Write-Host "=============================================================" -ForegroundColor Cyan

# 检查 Docker 是否运行
try {
    docker info | Out-Null
    Write-Host "✅ Docker 引擎运行正常" -ForegroundColor Green
} catch {
    Write-Host "❌ Docker 未运行！请先启动 Docker Desktop" -ForegroundColor Red
    exit 1
}

# 确保数据目录存在
if (-not (Test-Path "data\influxdb")) {
    New-Item -ItemType Directory -Path "data\influxdb" -Force | Out-Null
    Write-Host "📁 创建数据目录" -ForegroundColor Yellow
}

Write-Host "`n📦 构建并启动所有容器 (Frontend + Backend + Storage)..." -ForegroundColor Yellow
Write-Host "这可能需要几分钟来下载镜像..." -ForegroundColor Gray

# 启动所有服务
docker compose up -d --build

Write-Host "`n⏳ 等待服务启动 (10秒)..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

Write-Host "`n📊 服务状态:" -ForegroundColor Cyan
docker compose ps

Write-Host "`n🌐 访问地址:" -ForegroundColor Green
Write-Host "  前端 (Vue3)     : http://localhost:5173" -ForegroundColor White
Write-Host "  生产前端 (Nginx): http://localhost:8080" -ForegroundColor White
Write-Host "  后端 API         : http://localhost:8000" -ForegroundColor White
Write-Host "  API 文档         : http://localhost:8000/docs" -ForegroundColor White
Write-Host "  InfluxDB         : http://localhost:8086" -ForegroundColor White
Write-Host "  MinIO Console    : http://localhost:9001 (user: quant, pass: backtrader123)" -ForegroundColor White

Write-Host "`n✅ 启动完成！" -ForegroundColor Green
Write-Host "   Vue3 前端已完全替代旧版 HTML，实现智能体对话、连通性测试、全容器化运行。" -ForegroundColor Gray
Write-Host "`n💡 使用方法：" -ForegroundColor Cyan
Write-Host "   1. 打开 http://localhost:5173" -ForegroundColor White
Write-Host "   2. 点击「一键测试全部」验证连通性" -ForegroundColor White
Write-Host "   3. 在对话框中输入交易相关问题" -ForegroundColor White
Write-Host "`n📖 详细文档请查看: doc\Vue3-Yuxi-Frontend-容器化部署.md" -ForegroundColor Gray
