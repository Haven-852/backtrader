# 后端服务启动指南 (Backend Service Startup Guide)

**任务编号**：Task-20260427-001  
**完成时间**：2026-04-27  
**需求来源**：微信 "1启动后端服务"

## 1. 概述

本指南提供 **Yuxi Backtrader Agent Platform** 后端服务的启动方法。基于 FastAPI 框架，实现智能体对话、数据库连通性测试、多智能体管理等功能。

**核心特性**：
- FastAPI + Uvicorn
- 支持 13 个智能体 (DeepSeek, Swarm, CrewAI 等)
- 5 种存储引擎连通性测试 (InfluxDB, PostgreSQL+TimescaleDB, Redis, MinIO, ClickHouse)
- 完整 CORS 支持
- Swagger API 文档自动生成

## 2. 快速启动 (推荐)

### 方法一：使用专用启动脚本（最小任务实现）

```powershell
# 1. 进入项目目录
cd E:\demo\backtrader

# 2. 启动后端服务
.\start-backend.ps1
```

**启动后访问地址**：
- **API 接口**：http://localhost:8000
- **Swagger 文档**：http://localhost:8000/docs
- **ReDoc 文档**：http://localhost:8000/redoc
- **健康检查**：http://localhost:8000/health
- **智能体列表**：http://localhost:8000/api/agents

### 方法二：直接命令行启动

```powershell
cd E:\demo\backtrader\backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## 3. API 接口说明

### 核心接口

| 方法 | 路径 | 功能 | 描述 |
|------|------|------|------|
| GET | `/` | 根接口 | 系统概览信息 |
| POST | `/api/chat` | 智能体对话 | 支持 DeepSeek/Swarm/CrewAI 等模型 |
| GET | `/api/test/model/{model}` | 模型测试 | 测试 LLM 连通性 |
| GET | `/api/test/database` | 数据库测试 | 测试 5 种存储服务 |
| GET | `/api/agents` | 智能体列表 | 返回 13 个可用智能体 |
| GET | `/api/health` | 健康检查 | 系统健康状态 |

### 智能体对话示例

```bash
curl -X POST "http://localhost:8000/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "分析000001股票", "model": "deepseek"}'
```

## 4. 环境依赖

**backend/requirements.txt** 包含：
- fastapi==0.115.6
- uvicorn==0.32.1
- python-dotenv==1.0.1
- httpx==0.28.1
- sqlalchemy==2.0.36
- redis==5.2.1
- psycopg2-binary==2.9.10

**必需环境变量** (`.env` 文件)：
```env
DEEPSEEK_API_KEY=your-key
OPENAI_API_KEY=your-key
```

## 5. Docker 方式启动 (完整方案)

如果需要同时启动前端+后端+存储层：

```powershell
cd E:\demo\backtrader
.\start-all.ps1
```

**注意**：需要确保 `frontend/` 目录存在。

## 6. 验证命令

```powershell
# 测试后端服务是否正常
curl http://localhost:8000/health

# 或使用浏览器访问
start http://localhost:8000/docs
```

## 7. 故障排除

**常见问题**：
1. **端口 8000 被占用**：`netstat -ano | findstr "8000"` 检查并终止进程
2. **依赖缺失**：运行 `pip install -r backend/requirements.txt`
3. **数据库连接失败**：确保存储服务 (Docker) 已启动

**存储服务启动**：
```powershell
# 单独启动存储服务
docker start backtrader-redis backtrader-influxdb backtrader-postgres
```

---

**文档生成符合 AGENTS.md 规范**：包含详细中文说明 + 完整可运行代码示例 + 故障排除。

**日志文件**：`E:\openclaw\haven-852\log\backtrader-modify-20260427-*.log`

**下一任务建议**：
2. 启动前端服务
3. 验证前后端连通性
4. 运行完整系统测试

**遵循原则**：最小精确修改 + PowerShell验证 + 文档生成 + Git提交闭环。
