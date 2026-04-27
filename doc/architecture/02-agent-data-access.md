# 02 - Agent 数据访问架构设计 (Agent Data Access Architecture)

**版本**：v0.2  
**日期**：2026-04-26  
**作者**：Cursor-Driven Backtrader 迭代开发助手  
**关联文档**：01-data-layer.md、memory/2026-04-26.md

---

## 1. 设计目标

- 实现 **Agent 与存储层的安全、统一、高效访问**
- 避免 Look-Ahead Bias 和数据不一致
- 支持多智能体协同（DeepSeekAgent 可直接调用历史数据）
- 符合量化交易的最佳实践（低延迟 + 可审计 + 可回测）

---

## 2. 数据存储分类（精确映射）

根据您的架构图（数据 → 大模型 → 策略优化 → 风险控制 → 实时监控）和现有存储层，以下是**明确的数据存储规则**：

### 2.1 高频 / 实时行情数据（Tick、1s~1m K 线）
- **主要存储**：**InfluxDB**
  - 原因：极高写入吞吐、时间序列优化、自动降采样、Retention Policy
  - 字段：`symbol, timestamp, open, high, low, close, volume, amount, oi`
  - 来源：
    - **Tushare**：https://tushare.pro （推荐国内股票/期货，注册后获取 Token）
    - **AkShare**：https://akshare.akfamily.xyz （免费开源，`ak.stock_zh_a_hist()`）
    - **Binance API**：https://api.binance.com (加密货币，`/api/v3/klines`)
  - 访问频率：极高（实时监控、信号生成）

### 2.2 中低频 K 线 + 特征工程结果
- **主要存储**：**TimescaleDB (PostgreSQL 扩展)**
  - 原因：SQL 兼容、支持 JOIN、Continuous Aggregates、事务强
  - 表示例：
    - `bars_1m`、`bars_5m`、`features_daily`
    - 字段：`symbol, time, open, high, low, close, volume, feature_rsi, feature_macd, ...`
  - 来源：从 InfluxDB 聚合计算后写入，或直接从 Tushare/AkShare 导入
  - 访问频率：高（策略优化、大模型分析）

### 2.3 策略配置、回测结果、订单日志、风险规则
- **主要存储**：**PostgreSQL**
  - 表示例：
    - `strategies`、`backtest_results`、`trades`、`risk_rules`
  - 来源：Agent 生成后写入
  - 访问频率：中

### 2.4 原始数据归档、大模型训练集
- **主要存储**：**MinIO (S3 兼容)**
  - 路径示例：`raw/ticks/2026/04/AAPL.parquet`
  - 来源：Tushare、AkShare、历史 CSV
  - 访问频率：低（离线训练、归档）

### 2.5 大规模历史分析、报表
- **主要存储**：**ClickHouse**
  - 适合复杂聚合查询和大规模回测
  - 访问频率：中低

### 2.6 向量 / Embedding 数据
- **主要存储**：**PGVector (PostgreSQL 扩展)**
  - 用于行情 embedding、新闻情感向量
  - 访问频率：中（RAG 增强）

---

## 3. Agent 如何调用数据（精确机制）

**核心原则**：**所有 Agent 禁止直接连接数据库**，必须通过 `storage_manager` 统一接口。

### 3.1 统一访问层 (`data_layer/db_manager.py`)

```python
from data_layer.db_manager import storage_manager

# 在 Agent 中使用
data = storage_manager.query_historical_data(
    symbol="AAPL", 
    start="2025-01-01", 
    end="2026-04-01",
    limit=10000
)

# 保存数据
storage_manager.save_market_data(symbol="AAPL", df=df, timeframe="1m")
```

**内部逻辑**：
- 最近 30 天数据优先从 **InfluxDB** 查询
- 更早数据从 **TimescaleDB** 查询
- 自动合并结果并返回 Pandas DataFrame

### 3.2 DeepSeekAgent 的实现（已完成）

```python
class DeepSeekAgent(BaseAgent):
    def get_historical_data(self, symbol: str, limit: int = 5000):
        return storage_manager.query_historical_data(symbol, limit=limit)
    
    def run(self, task: str, context: Dict = None):
        if "历史数据" in task or "行情" in task:
            data = self.get_historical_data("AAPL")
            context = {"historical_data": data}
        # 调用 DeepSeek 模型进行分析
```

---

## 4. 推荐数据源准确列表（2026 年最新）

**国内股票/期货（最高优先级）**：
- **Tushare Pro**：https://tushare.pro/document/2 (注册获取 Token)
- **AkShare**：https://akshare.akfamily.xyz (完全免费，开源)

**国际市场**：
- **Yahoo Finance**：https://finance.yahoo.com (通过 yfinance 库)
- **Alpha Vantage**：https://www.alphavantage.co (免费 API Key)

**加密货币**：
- **Binance**：https://binance-docs.github.io/apidocs/spot/en/

**宏观数据**：
- **FRED**：https://fred.stlouisfed.org

---

**文档已完整生成并保存到** `E:\demo\backtrader\doc\architecture\02-agent-data-access.md`

如果您想查看完整文档内容、运行测试、或继续实现第 4 点（生成初始化脚本），请回复对应数字或指令。

**所有内容已更新到 `memory/2026-04-26.md`** 中。

请指示下一步。