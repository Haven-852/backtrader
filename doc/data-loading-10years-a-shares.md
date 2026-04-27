# A股10年历史数据 + 工作日分钟实时数据加载方案

**版本**：v1.0  
**日期**：2026-04-27  
**作者**：Cursor-Driven Backtrader 迭代开发助手  
**遵循文档**：`architecture/02-agent-data-access.md`

---

## 1. 需求概述

按照用户微信指令完成以下两个核心任务：

### 任务1：加载最近10年A股股票**最全信息**
- 从数据源（AkShare为主，Tushare为备选）获取所有A股股票列表
- 获取每只股票**最完整的10年历史日线数据**（含开高低收、成交量、成交额、换手率等）
- 按照 `02-agent-data-access.md` 架构正确写入存储层

### 任务2：实现每个**工作日的分钟实时数据**写入
- 针对主流股票/指数，获取工作日分钟级（1分钟/5分钟）K线数据
- 写入高频存储引擎（InfluxDB）

---

## 2. 架构遵循 (02-agent-data-access.md)

### 2.1 数据存储分类（已精确实现）
- **高频/实时行情数据 (1m K线)** → **InfluxDB** (高吞吐、时间序列优化)
  - 字段：`symbol, timestamp, open, high, low, close, volume, amount`
- **中低频K线 (日线)** → **TimescaleDB (PostgreSQL扩展)**
  - 使用 hypertable 优化时序查询
  - 支持后续特征工程、JOIN等复杂分析
- **统一访问层** → `storage_manager` (所有Agent禁止直连DB)

### 2.2 数据源优先级
1. **AkShare** (当前主力，免费开源)
   - `stock_zh_a_hist()` - 日线
   - `stock_zh_a_hist_min_em()` / `stock_zh_a_minute()` - 分钟线  
   - `stock_info_a_code_name()` - 全市场A股列表
2. **Tushare Pro** (Token未配置，备选)
   - 需要在 `.env` 中配置 `TUSHARE_TOKEN`

---

## 3. 核心实现文件

### 3.1 `data_layer/collectors/akshare_collector.py` (核心增强)

```python
# 新增方法
def get_minute(self, symbol: str, period: str = "1", ...) -> pd.DataFrame:
    """获取分钟级数据 - 写入InfluxDB"""
    df = ak.stock_zh_a_hist_min_em(...)  # 或备用接口

def get_all_a_stocks(self) -> List[dict]:
    """获取全市场A股列表，用于批量10年数据加载"""
    df = ak.stock_info_a_code_name()
```

### 3.2 `data_layer/collectors/collector_manager.py` (业务编排)

```python
def load_10years_a_shares(self, max_stocks: int = 50) -> Dict:
    """批量加载10年A股数据 + 写入storage_manager"""
    stocks = self.akshare.get_all_a_stocks()[:max_stocks]
    for stock in stocks:
        df = self.akshare.get_daily(stock['symbol'])  # 10年数据
        storage_manager.save_market_data(symbol, df, timeframe="daily")

def load_minute_data_for_trading_days(self, symbol=None, days=5) -> Dict:
    """工作日分钟数据写入 (仅交易日执行)"""
    df_1m = self.akshare.get_minute(symbol, period="1")
    storage_manager.save_market_data(symbol, df_1m, timeframe="1m")
```

### 3.3 `data_layer/db_manager.py` (存储实现)

```python
def save_market_data(self, symbol: str, df: pd.DataFrame, timeframe: str = "1m"):
    """完全重构 - 真实数据库写入"""
    if timeframe in ["1m", "5m", ...]:  # 高频
        # 写入 InfluxDB v2 (measurement=market_data)
        write_api.write(bucket="market_data", ...)
    else:  # daily
        # 写入 TimescaleDB (自动创建 hypertable)
        df.to_sql(f'bars_{timeframe}', engine, ...)
```

### 3.4 `data_layer/data_loader.py` (统一入口)

```bash
# 使用方式
cd E:\demo\backtrader
python data_layer\data_loader.py --task all          # 执行全部
python data_layer\data_loader.py --task 10years --stocks 30
python data_layer\data_loader.py --task minute
python data_layer\data_loader.py --task log
```

---

## 4. 使用步骤

### 步骤1：环境准备（已完成）
```powershell
# 存储服务已在运行
cd E:\demo\backtrader
docker-compose ps
# 确认 InfluxDB, postgres(timescaledb), redis, minio 均正常
```

### 步骤2：执行数据加载
```powershell
# 推荐方式 - 执行完整流程
python data_layer\data_loader.py --task all

# 或分步执行
python data_layer\data_loader.py --task 10years --stocks 20
python data_layer\data_loader.py --task minute
```

### 步骤3：在Agent中使用数据
```python
from data_layer.db_manager import storage_manager
from agents.deepseek_agent import DeepSeekAgent

# 在DeepSeekAgent中使用 (符合架构)
data = storage_manager.query_historical_data(
    symbol="000001", 
    start="2016-01-01", 
    end="2026-04-27",
    limit=10000
)

agent = DeepSeekAgent()
result = agent.run("分析上证指数10年趋势", context={"historical_data": data})
```

---

## 5. 当前状态 & 注意事项

### ✅ 已完成
- [x] Tushare Token缺失提示机制
- [x] AkShare 10年日线 + 分钟数据采集器
- [x] 全市场A股股票列表获取
- [x] StorageManager真实数据库写入 (InfluxDB + TimescaleDB)
- [x] DataLoader统一入口脚本
- [x] 完整中文文档 + 日志生成
- [x] 存储层连接测试通过

### ⚠️ 需要用户确认
1. **Tushare Token**：当前使用AkShare免费接口。如需更完整的基本面数据（财报、机构持仓等），请提供Tushare Pro Token。
2. **数据量控制**：首次运行建议使用 `--stocks 20`，避免API限流。
3. **Docker服务**：确保所有存储容器正常运行。

---

## 6. 日志文件

本次操作已生成详细日志文件：
- 路径：`E:\openclaw\haven-852\log\backtrader-modify-*.log`
- 内容：包含所有修改前后的代码片段、验证结果、架构遵循说明

**该日志文件可直接通过微信发送给用户**。

---

## 7. 下一步建议

1. 配置 `TUSHARE_TOKEN` 后可扩展基本面数据采集
2. 实现定时任务（工作日 9:00 自动更新分钟数据）
3. 在Vue3前端增加「数据加载进度」可视化面板
4. 开发特征工程管道（基于TimescaleDB Continuous Aggregates）

---

**文档生成时间**：2026-04-27  
**Git提交准备就绪**：所有修改均为**最小精确修改**，符合「Cursor-Driven Backtrader迭代开发助手」工作规范。

如需继续迭代或调整参数，请随时指示。
