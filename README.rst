AIquant · Backtrader Agent Platform
====================================

基于 **Backtrader** 生态扩展的 **A 股量化智能体平台**（v3.0）。

本仓库在经典 Python 回测库之上，增加了完整的 **Web 控制台 + FastAPI 后端 + 多存储引擎 + 大模型工具调用**，
用于：行情采集与看板、自然语言查库/建表、策略分析、模拟交易、知识库沉淀，以及面向 LightGBM 等模型的训练数据流水线。

- 前端：Vue 3 + Vite + Tailwind（``web/``）
- 后端：FastAPI（``backend/`` → ``routers/`` → ``server/`` → ``data_layer/``）
- 存储：PostgreSQL / TimescaleDB、Redis、InfluxDB、MinIO（可选 ClickHouse）
- 大模型：DeepSeek / OpenAI / Ollama / Anthropic / Grok 等（可在界面保存多套配置）


这是做什么的？
--------------

把「看盘、补数、查库、对话、策略、纸面交易、知识沉淀」放在同一个本地平台里：

1. **大模型对话**  
   多模型聊天；可调用 SQL / Datahubco 补数 / 模拟盘 / 板块与竞价等 **工具（skills）**，避免模型瞎编行情。

2. **行情看板 / 行情中心**  
   ETF、板块、期货主连等可视化看板；支持挑选式与连续采集。

3. **存储层状态**  
   查看各后端连通性；预览 PostgreSQL 表（滚动加载）；**用自然语言 + 大模型创建自定义表**（``u_*``）；导出 CSV / Markdown；自定义表可在线增改行。

4. **策略分析**  
   选股与策略相关前后端能力（结构回撤、盘中转折等模块随版本演进）。

5. **模拟交易（纸面）**  
   账户、持仓、盘中盯盘与告警推送（企业微信 / PushPlus 等，见 ``backend/.env.example``）。

6. **知识库**  
   规则、实验、外部方法、市场记忆等结构化沉淀，供复盘与对话引用。

7. **数据与训练**  
   Datahubco / Tushare 多 Token 分级采集；全市场股票池 ``ref_market_universe``；
   训练表 ``ml_train_daily``（特征 P01–P11、标签 ``y_high`` / ``y_low``、``train_ready``）及体检修复工具。


目录结构（简）
--------------

::

  backtrader/
  ├── web/                 # Vue3 前端
  ├── backend/             # FastAPI 入口、venv、.env
  ├── routers/             # HTTP 路由
  ├── server/              # 业务服务（对话、存储、纸面、知识库…）
  ├── data_layer/          # 采集器、Schema、DB 管理
  ├── tools/               # 大模型可调用的技能包与 HANDBOOK
  ├── agents/              # 量化智能体相关
  ├── doc/                 # 文档与战法笔记
  ├── scripts/             # 运维脚本（如 PG 备份）
  ├── docker-compose.yml   # 应用 + 存储编排
  ├── docker-compose.storage.yml
  ├── start-all.ps1        # Windows 一键启动前后端
  └── start-backend.ps1


环境要求
--------

- Windows 10/11（当前启动脚本以 PowerShell 为主）
- Python 3.10+（推荐使用 ``backend/venv``）
- Node.js 18+（前端）
- Docker Desktop（PostgreSQL / Redis / InfluxDB / MinIO 等）
- （可选）Ollama，用于本地大模型


快速开始
--------

1. 克隆仓库
~~~~~~~~~~~

::

  git clone git@github.com:Haven-852/backtrader.git
  cd backtrader

若 HTTPS 访问 GitHub 不稳定，请优先使用 SSH（本机需配置 Deploy Key 或个人 SSH Key）。

2. 配置环境变量
~~~~~~~~~~~~~~~

::

  copy backend\.env.example backend\.env

编辑 ``backend/.env``，至少填写：

- ``DEEPSEEK_API_KEY``（或其它厂商 Key）
- ``TUSHARE_TOKEN_*`` / ``DATAHUBCO_API_KEY``（按你实际数据源）
- ``POSTGRES_*``、``REDIS_*``、``INFLUX_*``（与 docker-compose 一致即可）

**切勿**把填好密钥的 ``.env`` 提交到 Git。仓库只保留 ``backend/.env.example`` 模板。

3. 启动存储容器
~~~~~~~~~~~~~~~

::

  docker compose -f docker-compose.storage.yml up -d

常用端口（宿主机）：

- PostgreSQL / Timescale：``15432``
- Redis：``16379``
- InfluxDB：``8086``
- MinIO：``9000`` / ``9001``

数据目录默认落在 ``data/docker/``，尽量避免 Docker 虚拟盘重置丢数据。

4. 一键启动前后端（推荐）
~~~~~~~~~~~~~~~~~~~~~~~~~

::

  .\start-all.ps1

将打开两个窗口：

- 后端 FastAPI：默认文档见控制台输出（常见 ``http://127.0.0.1:8000`` 或 ``8010``）
- 前端 Vite：``http://localhost:5173``

前端 ``vite`` 代理默认指向 ``http://127.0.0.1:8010``（见 ``web/vite.config.ts``）。
若后端端口不同，请改代理或设置 ``BACKEND_PORT`` 后重新执行 ``start-backend.ps1``。

5. 手动启动（可选）
~~~~~~~~~~~~~~~~~~~

后端::

  .\start-backend.ps1
  # 或
  cd backend
  .\venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8010 --app-dir .

前端::

  cd web
  npm install
  npm run dev


界面怎么用？
------------

打开 ``http://localhost:5173`` 后，左侧导航对应：

大模型对话
~~~~~~~~~~

- 保存厂商 / 模型名 / API Key（写入内部配置表，不在存储预览里展示）。
- 对话中可挂载工具：查库、补数、纸面账户、竞价筛选等。
- 工具选用说明见 ``tools/HANDBOOK.md``。

行情看板 / 行情中心
~~~~~~~~~~~~~~~~~~~~

- 查看板块、ETF、期货等行情组件。
- 缺数时优先走 Datahubco 相关工具补数入库，再刷新看板。

存储层状态
~~~~~~~~~~

- 查看 PostgreSQL / Influx / Redis / MinIO 连通性。
- 左侧选表 → 右侧预览（首屏约 50 行，滚动加载更多）。
- **用大模型创建自定义表**：自然语言描述 → 生成 DDL → 写入 PostgreSQL（表名 ``u_*``）。
- **导出 CSV / Markdown**；自定义表可新增行、按主键编辑。
- 仅允许删除自己登记过的自定义表，系统表只读。

策略分析 / 模拟交易 / 知识库
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- 策略：选股与分析流程。
- 模拟交易：纸面下单、盯盘、告警（Webhook / 企微 / PushPlus）。
- 知识库：规则、实验、外部方法、市场记忆等。


常用开发接口
------------

后端启动后可访问：

- 健康检查：``GET /health``
- OpenAPI 文档：``/docs``、``/redoc``
- 对话：``/chat/*``
- 存储：``/storage/*``（含 ``/storage/ai-create-table``、``/storage/export/{name}``）
- 行情：``/market/*``、``/dash/*``
- 策略 / 纸面 / 知识库：``/strategy/*``、``/paper/*``、``/kb/*``
- 技能：``/skills/*``


数据与工具流水线（摘要）
------------------------

更完整的工具表见 ``tools/HANDBOOK.md``。常见路径：

1. ``fill-market-universe`` → 写入 ``ref_market_universe``
2. ``fill_ml_train_daily`` / ``fill_batch`` → 写入 ``ml_train_daily``（遵守 P01–P11）
3. ``sanitize_ml_train_daily`` → 体检与修复；训练只用 ``train_ready=1``
4. 自定义分析表 → 存储层 AI 建表或 ``create-custom-table``，再用 ``query-exec``


经典 Backtrader 库
------------------

仓库仍包含原 **backtrader** Python 回测引擎源码与 ``setup.py``，可用于传统脚本回测，例如::

  from datetime import datetime
  import backtrader as bt

  class SmaCross(bt.SignalStrategy):
      def __init__(self):
          sma1, sma2 = bt.ind.SMA(period=10), bt.ind.SMA(period=30)
          crossover = bt.ind.CrossOver(sma1, sma2)
          self.signal_add(bt.SIGNAL_LONG, crossover)

  cerebro = bt.Cerebro()
  cerebro.addstrategy(SmaCross)
  # ... 添加 data feed 后 cerebro.run()

平台 Web/Agent 能力与经典库并存：日常量化工作台走 ``web`` + ``backend``；纯回测脚本可直接 ``import backtrader``。


安全提示
--------

- 密钥只放在本地 ``backend/.env``，提交前确认 ``.gitignore`` 已忽略 ``.env``。
- 推送到 GitHub 时若 HTTPS ``443`` 超时，改用 SSH：``git@github.com:Haven-852/backtrader.git``。
- 曾误提交过的 API Key 请在对应平台 **轮换作废**。


许可证
------

本项目沿用仓库根目录 ``LICENSE``（GNU GPL v3）。原 backtrader 社区资源与条款请同时尊重上游项目约定。


相关链接
--------

- 本仓库：https://github.com/Haven-852/backtrader
- 工具手册：``tools/HANDBOOK.md``
- 环境模板：``backend/.env.example``
