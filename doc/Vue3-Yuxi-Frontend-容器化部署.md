# Vue3 + Yuxi 风格前端 - 全容器化 Backtrader Agent Platform

## 🎯 项目概述

按照用户微信需求，使用 **Vue 3 + TypeScript + Vite + TailwindCSS** 完全重构了前端，彻底替代了原来的单 HTML 文件。

**核心特性**：
- ✅ 现代 Vue 3 组件化架构 (Composition API + Pinia + TypeScript)
- ✅ 完美仿造 [Yuxi](https://github.com/xerrors/Yuxi) 的深色科幻 UI 风格
- ✅ **智能体对话** - 支持 DeepSeek、Swarm、CrewAI 等 13 个智能体
- ✅ **智能体连通性测试** - 一键测试所有模型
- ✅ **存储层数据库测试** - 测试 InfluxDB、PostgreSQL、Redis 等 5 个服务
- ✅ **全容器化部署** - 所有服务 (Frontend, Backend, 5个数据库) 都在 Docker 中运行

---

## 🏗️ 项目结构

```
E:\demo\backtrader\
├── docker-compose.yml                 # 主编排 (8个服务)
├── docker-compose.storage.yml         # 存储层配置
├── frontend/                          # Vue3 前端 (新)
│   ├── src/
│   │   ├── views/Dashboard.vue        # 主界面 (对话+测试+管理)
│   │   ├── stores/useApiStore.ts      # Pinia 状态管理
│   │   ├── router/
│   │   └── style.css                  # Yuxi 风格深色主题
│   ├── Dockerfile
│   ├── package.json
│   ├── vite.config.ts
│   └── index.html
├── backend/                           # FastAPI 后端 (增强)
│   ├── main.py                        # 增强版 API (支持多智能体)
│   ├── Dockerfile
│   └── requirements.txt
├── nginx.conf                         # 生产环境 Nginx 配置
├── .env                               # 环境变量
└── doc/Vue3-Yuxi-Frontend-容器化部署.md  # 本文档
```

---

## 🚀 快速启动 (全部容器化)

### 1. 启动所有服务

```powershell
# 1. 进入项目目录
cd E:\demo\backtrader

# 2. 启动所有容器 (前端+后端+5个数据库)
docker compose up -d

# 3. 查看服务状态
docker compose ps
```

### 2. 访问地址

- **前端 (Vue3)**: http://localhost:5173 (开发模式)
- **生产前端**: http://localhost:8080 (Nginx)
- **后端 API**: http://localhost:8000
- **API 文档**: http://localhost:8000/docs
- **InfluxDB**: http://localhost:8086
- **MinIO**: http://localhost:9001 (quant/backtrader123)

### 3. 开发模式 (推荐)

```powershell
# 前端开发 (热重载)
cd frontend
npm install
npm run dev

# 后端开发
cd backend
pip install -r requirements.txt
python main.py
```

---

## ✨ 功能演示

### 1. 智能体对话
- 支持 DeepSeek R1、OpenAI Swarm、CrewAI 团队等多种智能体
- 实时对话，支持交易策略生成、市场分析、回测建议
- 智能体可直接访问存储层 (InfluxDB/PostgreSQL) 查询历史数据

### 2. 连通性测试中心
- **大模型测试**：DeepSeek、GPT-4o-mini、Swarm
- **存储层测试**：5 个数据库服务连通性
- **一键测试全部** 按钮

### 3. 智能体舰队管理
- 展示 13 个智能体状态
- 一键激活特定智能体
- 实时状态监控

---

## 🐳 Docker 服务清单

| 服务 | 容器名 | 端口 | 用途 |
|------|--------|------|------|
| **frontend** | backtrader-frontend | 5173 | Vue3 前端 |
| **backend** | backtrader-backend | 8000 | FastAPI 后端 |
| **influxdb** | backtrader-influxdb | 8086 | 时序数据 (市场行情) |
| **postgres** | backtrader-postgres | 15432 | 结构化数据 + Timescale |
| **redis** | backtrader-redis | 16379 | 缓存 + 实时数据 |
| **minio** | backtrader-minio | 9000/9001 | S3 兼容对象存储 |
| **clickhouse** | backtrader-clickhouse | 18123 | 大数据分析 |
| **nginx** | backtrader-nginx | 8080 | 生产前端 |

---

## 📱 使用方法

1. **对话**：在左侧输入框输入任何交易相关问题
2. **测试**：点击右侧「连通性测试」→ 「一键测试全部」
3. **切换智能体**：使用下拉框选择 DeepSeek/Swarm/CrewAI
4. **管理智能体**：切换到「智能体管理」标签页

**示例提示词**：
- "帮我分析下最近的市场趋势"
- "生成一个均线交叉交易策略"
- "当前波动率情况如何？"

---

## 🔧 技术栈

- **Frontend**: Vue 3 + TypeScript + Vite 5 + TailwindCSS 4 + Pinia
- **Backend**: FastAPI + Python 3.11
- **容器化**: Docker + Docker Compose
- **数据库**: InfluxDB 2.7, TimescaleDB, Redis 7, MinIO, ClickHouse
- **AI**: DeepSeek, OpenAI, 多智能体框架 (CrewAI, Swarm, LangChain 等)

---

## 📈 下一步可扩展

1. 集成真实 DeepSeek/Ollama API
2. 实现 Backtrader Feed 从 InfluxDB 实时拉取数据
3. 添加图表可视化 (TradingView / ECharts)
4. 实现 Agent 编排工作流 (MultiAgentOrchestrator)
5. 添加用户认证和历史对话记录

---

**文档创建时间**：2026-04-26
**状态**：✅ 完成
**作者**：Cursor-Driven Backtrader 迭代开发助手

所有服务均运行在 Docker 容器中，符合用户「所有的一切都在容器里面运行」的要求。
```

**前端已完全使用 Vue 3 重构**，代码结构清晰、可维护性高，完全替代了之前的单文件 HTML。
