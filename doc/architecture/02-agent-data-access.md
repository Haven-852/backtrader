# 02 - Agent 数据访问架构设计 (Agent Data Access Architecture)

**版本**：v0.3  
**日期**：2026-04-30  
**关联文档**：01-data-layer.md  

---

## 1. 设计目标

- 实现 **Agent 与存储层的安全、统一、高效访问**
- 避免 Look-Ahead Bias 和数据不一致
- 支持多智能体协同（DeepSeekAgent 可直接调用历史数据）
- 符合量化交易的最佳实践（低延迟 + 可审计 + 可回测）
- **Tushare Pro** 作为境内股/基/ETF 主数据源时，库表字段与 [Tushare 官方接口文档](https://tushare.pro/document/2) 保持一致（下列字段名以接口为准，若官方变更需同步迁移）

---

## 2. 数据存储分类（精确映射）

### 2.1 高频 / 实时行情数据（Tick、1s~1m K 线）

- **主要存储**：**InfluxDB**
  - Bucket / Measurement 命名：`market_cn` / `stock_bar_1m`、`etf_bar_1m` 等
  - Tags：`ts_code`, `freq`（如 `1m`/`5m`）
  - Fields：`open`, `high`, `low`, `close`, `vol`, `amount`（与 Tushare 分钟线接口字段语义对齐）
  - 来源：**Tushare** `stk_mins` / ETF 分钟等（需对应权限）

### 2.2 中低频 K 线、资金流、日频横截面

- **主要存储**：**TimescaleDB（PostgreSQL 扩展）**
  - Hypertable：`trade_date`（或 `nav_date`）为时间维度，`ts_code` 为业务键
  - 原因：SQL、JOIN、Continuous Aggregates、压缩策略成熟

### 2.3 主数据、财务报送、指数/基金主档、公告元数据

- **主要存储**：**PostgreSQL（普通表）**
  - 原因：强约束、外键、缓慢变化维度（SCD）

### 2.4 热点报价、限流计数、会话与检索缓存

- **主要存储**：**Redis**
  - Key 示例：`quote:latest:{ts_code}`、`tushare:ratelimit:remaining`、`rag:doc:{ann_id}`

### 2.5 原始抓取正文、附件、批量导出 Parquet

- **主要存储**：**MinIO（S3 兼容）**
  - Bucket：`backtrader-data`（可与现有配置一致）
  - 路径示例：`tushare/raw/news/{yyyy}/{mm}/{id}.json`、`tushare/raw/ann/{ts_code}/{ann_date}/{title_slug}.html`

### 2.6 可选：大规模只做 OLAP 的镜像表

- **ClickHouse**：可对 Timescale 中部分 hypertable 做离线副本（非必选）

### 2.7 可选：向量检索（RAG）

- **PGVector**：表 `doc_embedding(doc_id, model, embedding)`，与 PostgreSQL 中共存元数据

---

## 3. Agent 如何调用数据（精确机制）

**核心原则**：**所有 Agent 禁止直接连接数据库**，必须通过 `storage_manager` 统一接口。

### 3.1 统一访问层 (`data_layer/db_manager.py`)

```python
from data_layer.db_manager import storage_manager

data = storage_manager.query_historical_data(
    symbol="000001.SZ",
    start="2025-01-01",
    end="2026-04-01",
    limit=10000,
)
storage_manager.save_market_data(symbol="000001.SZ", df=df, timeframe="1m")
```

**内部逻辑建议**：

- 最近窗口高频：**InfluxDB**
- 日频及以上.history：**TimescaleDB**
- 元数据 / 财务：**PostgreSQL**

---

## 4. 推荐数据源（境内）

- **Tushare Pro**：https://tushare.pro/document/2  
- **AkShare**：补充免费源（条款与稳定性自建评估）

---

## 5. Tushare 全量数据域：七类业务 × 存储映射

下列按你在架构中定义的 **七大类** 展开。每张表均标注 **对应 Tushare 接口名**（便于对照文档字段）；**列定义与官方接口返回列一致**，迁移脚本实现时建议「自动生成列」或从文档同步。

### 5.1 存储总览（决策表）

| 数据大类 | PostgreSQL（维度/财报/元数据） | TimescaleDB（Hypertable） | InfluxDB | Redis | MinIO |
|---------|-------------------------------|---------------------------|----------|-------|-------|
| 一、基础参照 | ✓ 日历、证券主档、更名等 | ✓ 涨跌停价、停牌（按日） | — | 缓存 | 原始 JSON 归档 |
| 二、股票行情 | — | ✓ 日/周/月线、每日指标、复权因子 | ✓ 分钟线 | 最新 OHLC 缓存 | Parquet 导出 |
| 三、财务与基本面 | ✓ 三大表、财务指标、预告快报 | 可选：按 `ann_date` 做 hypertable | — | — | PDF/公告附件 |
| 四、资金与行为 | — | ✓ 资金流向、龙虎榜、两融明细等 | — | 榜单缓存 | — |
| 五、指数行业主题 | ✓ 指数/行业/概念主档 | ✓ 指数日线、成分权重 | — | — | — |
| 六、基金与 ETF | ✓ 基金/ETF 基础信息 | ✓ 净值、基金/ETF 行情 | ✓ ETF 分钟（若有） | — | — |
| 七、新闻与公告 | ✓ 公告索引、新闻索引 | — | — | 热点键 | ✓ 正文/HTML |

---

### 5.2 一、基础参照（几乎所有策略都要）

#### Tushare 接口 → 逻辑表 → 存储

| Tushare 接口 | 存储 | 表名（建议） | 说明 |
|-------------|------|-------------|------|
| `trade_cal` | PostgreSQL | `ref_trade_cal` | 字段：`exchange`, `cal_date`, `is_open`, `pretrade_date` |
| `stock_basic` | PostgreSQL | `ref_stock_basic` | 字段：`ts_code`, `symbol`, `name`, `area`, `industry`, `cnspell`, `market`, `list_date`, `list_status`, `delist_date`, `is_hs`, `act_name`, `act_ent_type`（以文档为准） |
| `namechange` | PostgreSQL | `ref_stock_namechange` | 字段：`ts_code`, `name`, `start_date`, `end_date`, `ann_date`, `change_reason` |
| `new_share` | PostgreSQL | `ref_new_share` | 字段：`symbol`, `name`, `ipo_date`, `issue_date`, `amount`, `market`, `price`, `pe`, `limit_amount`, `funds`, `ballot` |
| `stk_limit` | TimescaleDB | `ref_stk_limit_daily` | 字段：`trade_date`, `ts_code`, `up_limit`, `down_limit`；唯一建议：`(trade_date, ts_code)` |
| `suspend_d` | TimescaleDB | `ref_suspend_d` | 字段：`ts_code`, `trade_date`, `suspend_timing`, `suspend_type` |

**Redis**：`ref:latest_trade_cal:{exchange}` 缓存当日是否开市。  
**MinIO**（可选）：`tushare/raw/ref/{interface}/{batch_id}.json` 保留接口原始响应便于审计。

---

### 5.3 二、股票行情（核心）

| Tushare 接口 | 存储 | 表名 / Measurement | 说明 |
|-------------|------|-------------------|------|
| `daily` | TimescaleDB | `stock_daily` | 字段：`ts_code`, `trade_date`, `open`, `high`, `low`, `close`, `pre_close`, `change`, `pct_chg`, `vol`, `amount` |
| `weekly` | TimescaleDB | `stock_weekly` | 字段：同周线接口文档 |
| `monthly` | TimescaleDB | `stock_monthly` | 字段：同月线接口文档 |
| `daily_basic` | TimescaleDB | `stock_daily_basic` | 字段：`ts_code`, `trade_date`, `close`, `turnover_rate`, `turnover_rate_f`, `volume_ratio`, `pe`, `pe_ttm`, `pb`, `ps`, `ps_ttm`, `dv_ratio`, `dv_ttm`, `total_share`, `float_share`, `free_share`, `total_mv`, `circ_mv` |
| `adj_factor` | TimescaleDB | `stock_adj_factor` | 字段：`ts_code`, `trade_date`, `adj_factor` |
| `stk_mins`（或文档中对应分钟接口） | InfluxDB | Measurement：`stock_bar_mins` | Tag：`ts_code`, `freq`；Field：`open`,`high`,`low`,`close`,`vol`,`amount`；Time：接口提供的时间戳 |

**Redis**：`stock:last_bar:daily:{ts_code}` 缓存最新一日 OHLCV。  
**MinIO**：批量历史导出 `exports/stock_daily/{yyyy}.parquet`。

---

### 5.4 三、财务与基本面（选股与风控）

财报类接口列为 **宽表**，字段与 Tushare 完全一致；以下为 **主键与索引建议**。

| Tushare 接口 | 存储 | 表名（建议） | 主键 / 唯一约束建议 |
|-------------|------|-------------|---------------------|
| `income` | PostgreSQL | `fin_income` | `(ts_code, end_date, report_type, ann_date)` 或文档推荐维度 |
| `balancesheet` | PostgreSQL | `fin_balancesheet` | 同上模式 |
| `cashflow` | PostgreSQL | `fin_cashflow` | 同上模式 |
| `fina_indicator` | PostgreSQL | `fin_fina_indicator` | `(ts_code, end_date, ann_date)` |
| `forecast` | PostgreSQL | `fin_forecast` | 按接口主键字段 |
| `express` | PostgreSQL | `fin_express` | 按接口主键字段 |
| `dividend` | PostgreSQL | `fin_dividend` | 按接口主键字段 |

**注意**：财务数据用于回测时需配合 **`ann_date` / `f_ann_date`** 做 **时点可得性**（避免前视偏差）；Agent 层必须统一封装「截至某日可见财报」的查询。

**MinIO**：年报/半年报 PDF（若从其他渠道补齐）与 `fin_*` 行通过 `doc_ref` 关联。

---

### 5.5 四、资金与「筹码 / 行为」类（增强 alpha）

| Tushare 接口 | 存储 | 表名（建议） | 字段说明（与文档一致） |
|-------------|------|-------------|------------------------|
| `moneyflow` | TimescaleDB | `stock_moneyflow_daily` | `ts_code`, `trade_date`, 及各档买卖 `*_vol` / `*_amount`, `net_mf_vol`, `net_mf_amount` 等 |
| `moneyflow_hsgt`（若使用） | TimescaleDB | `hsgt_moneyflow_daily` | 按接口列 |
| `top_list` | TimescaleDB | `stock_top_list_daily` | `trade_date`, `ts_code`, `name`, `close`, `pct_chg`, `turnover_rate`, `amount`, `l_sell`, `l_buy`, `l_amount`, `net_amount`, `net_rate`, `amount_rate`, `float_values`, `reason` |
| `top_inst`（若使用） | TimescaleDB | `stock_top_inst_daily` | 按接口列 |
| `margin` | TimescaleDB | `margin_summary_daily` | `trade_date`, `exchange_id`, `rzye`, `rzmre`, `rzche`, `rqye`, `rqmcl`, `rzrqye`, `rqyl` 等 |
| `margin_detail` | TimescaleDB | `margin_detail_daily` | `trade_date`, `ts_code`, `name`, `rzye`, `rqye`, `rzmre`, `rqyl`, `rzche`, `rqmcl`, `rqchl` |
| `block_trade`（大宗） | TimescaleDB | `stock_block_trade` | 按接口列 |
| `pledge_stat` / `pledge_detail`（质押） | PostgreSQL 或 TimescaleDB | `pledge_*` | 更新频率较低可用 PostgreSQL；若按日快照可 hypertable |

**Redis**：`top_list:{trade_date}` 缓存当日榜单摘要。

---

### 5.6 五、指数、行业与主题（组合与归因）

| Tushare 接口 | 存储 | 表名（建议） |
|-------------|------|-------------|
| `index_basic` | PostgreSQL | `ref_index_basic` |
| `index_daily` | TimescaleDB | `index_daily` |
| `index_weight` | TimescaleDB | `index_weight`（`index_code`, `con_code`, `trade_date`, `weight`） |
| `index_classify` / `index_member`（或当前文档中等价接口） | PostgreSQL | `ref_index_member` |
| `ths_index` / `ths_member`（同花顺概念） | PostgreSQL | `ref_ths_index`, `ref_ths_member` |
| `sw_industry` / `index_member_all`（申万等，以文档为准） | PostgreSQL | `ref_sw_industry`, `ref_sw_member` |

指数日线字段与 `index_daily` 文档：`ts_code`, `trade_date`, `close`, `open`, `high`, `low`, `pre_close`, `change`, `pct_chg`, `vol`, `amount`。

---

### 5.7 六、基金与 ETF（三类资产）

| Tushare 接口 | 存储 | 表名（建议） |
|-------------|------|-------------|
| `fund_basic` | PostgreSQL | `ref_fund_basic` |
| `fund_nav` | TimescaleDB | `fund_nav`（`ts_code`, `nav_date`, `unit_nav`, `accum_nav`, `accum_div`, `net_asset`, `total_netasset`, `adj_nav`, `ann_date`） |
| `fund_daily` | TimescaleDB | `fund_daily`（行情字段同股票日线语义） |
| `fund_portfolio`（持仓） | PostgreSQL | `fund_portfolio`（季报维度，按接口主键） |
| `fund_share`（若使用） | TimescaleDB 或 PostgreSQL | `fund_share` |
| `etf_basic` | PostgreSQL | `ref_etf_basic` |

ETF 日线若走独立接口则单独 hypertable `etf_daily`；若与 `fund_daily` 代码空间一致，可用 `asset_class` 区分。

**InfluxDB**：ETF/基金分钟线（若有权限）→ `etf_bar_mins` / `fund_bar_mins`。

---

### 5.8 七、新闻与公告（大模型文本侧）

| Tushare 接口 | 存储 | 结构 |
|-------------|------|------|
| `anns` / `ann`（公告，以文档接口命名为准） | PostgreSQL | `news_ann`：`ts_code`, `ann_date`, `ann_type`, `title`, `url`, `bucket_key`（MinIO 对象键）, `raw_hash` |
| 新闻资讯类接口（深度/长篇/快讯等，权限独立） | PostgreSQL | `news_article`：`article_id`, `datetime`, `title`, `content_url` 或 `content_preview`, `src`, `channels` |
| 正文与附件 | MinIO | `tushare/raw/news/...`、`tushare/raw/ann/...` |
| 解析后的纯文本（供 LLM） | MinIO 或 PostgreSQL `TEXT` | 大正文优先对象存储 |
| **PGVector**（可选） | PostgreSQL | `news_ann_embedding(ann_id, chunk_id, embedding)` |

**Redis**：`ann:by_symbol:{ts_code}` 最近 N 条公告 ID；`llm:summary:{doc_id}` 缓存摘要。

---

## 6. 实施顺序建议（工程）

1. **PostgreSQL**：`ref_trade_cal`、`ref_stock_basic`、`ref_index_basic`、`ref_fund_basic`、`ref_etf_basic`  
2. **TimescaleDB**：`stock_daily`、`stock_adj_factor`、`stock_daily_basic`  
3. 扩展：`fin_*`、`stock_moneyflow_daily`、`index_daily`  
4. **InfluxDB**：分钟线接入后再建 retention policy  
5. **MinIO + PostgreSQL 索引表**：公告与新闻正文落地  

---

## 7. 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.2 | 2026-04-26 | 初版 Agent 访问与数据源列表 |
| v0.3 | 2026-04-30 | 增补 Tushare 七大类全量域：接口—表—库映射及字段对齐说明 |
