# 02 - Agent 数据访问架构设计 (Agent Data Access Architecture)

**版本**：v1.0（全量版）
**日期**：2026-05-10
**关联文档**：01-data-layer.md
**适用项目**：`E:\demo\backtrader\`
**权限等级**：Tushare Pro **15000 积分**（顶级权限，覆盖绝大部分接口）

---

## 1. 设计目标与权限说明

### 1.1 设计目标

- 实现 **Agent 与存储层的安全、统一、高效访问**
- 避免 Look-Ahead Bias 和数据不一致
- 支持多智能体协同（DeepSeekAgent / QuantAgent 可直接调用历史数据）
- 覆盖 **盘前→盘中→盘后→财务→新闻→研报→政策** 全链路数据
- 符合量化交易最佳实践（低延迟 + 可审计 + 可回测）

### 1.2 Tushare Pro 15000 积分权限说明

Tushare Pro 积分体系决定了可用接口范围。**15000 积分**为该平台顶级权限等级，覆盖以下全部核心接口：

| 数据大类 | 关键接口 | 所需积分 | 说明 |
|---------|---------|---------|------|
| 基础参照 | `trade_cal`, `stock_basic`, `namechange`, `new_share` | 免费~2000 | 所有等级可用 |
| 股票日/周/月线 | `daily`, `weekly`, `monthly` | 2000~5000 | 日线需积分 |
| **股票分钟线** | `stk_mins` | **8000+** | 高频核心，15000分可获取全量 |
| **每日指标（含股本）** | `daily_basic` | **5000+** | 含流通股本、总市值等 |
| 复权因子 | `adj_factor` | 2000~5000 | 前/后复权计算 |
| 财务三大表 | `income`, `balancesheet`, `cashflow` | 5000+ | 利润/资产/现金流 |
| 财务指标 | `fina_indicator` | 5000+ | ROE/ROA/PE等 |
| 资金流向 | `moneyflow` | 5000+ | 个股/板块资金 |
| 龙虎榜 | `top_list` | 5000+ | 营业部买卖明细 |
| 融资融券 | `margin`, `margin_detail` | 5000+ | 两融余额明细 |
| 质押数据 | `pledge_stat`, `pledge_detail` | 5000+ | 股权质押 |
| 大宗交易 | `block_trade` | 5000+ | 大宗成交明细 |
| 指数数据 | `index_basic`, `index_daily`, `index_weight` | 2000~5000 | 指数日线+成分权重 |
| 申万行业 | `index_classify`, `index_member` | 2000+ | 行业分类 |
| 基金/ETF | `fund_basic`, `fund_nav`, `fund_daily`, `fund_portfolio` | 2000~5000 | 基金净值+持仓 |
| **新闻资讯** | `news`, `major_news` | **5000+** | 即时新闻+要闻 |
| **公司公告** | `anns` | **5000+** | 公司公告索引+全文 |
| **券商研报** | `broker_reports` | **8000~10000+** | 研报摘要与评级 |
| **董秘互动** | `stk_insider` / 互动易 | **需积分** | 投资者问答 |
| **港股通** | `hk_daily`, `hk_mins` | **10000+** | 港股行情（若需） |

> **结论**：15000 积分可覆盖上述所有接口，满足"全量数据"需求。分钟线、研报、新闻公告、股本结构等高频/深度数据均无需额外购买。

---

## 2. 全局数据存储映射（总览）

```
┌────────────────────────────────────────────────────────────────────┐
│                     Agent 统一访问层 (storage_manager)              │
├────────────┬──────────────┬────────────┬──────────────┬────────────┤
│  InfluxDB  │ TimescaleDB  │ PostgreSQL │    Redis     │   MinIO    │
│  (时序)    │  (时序+SQL)  │ (关系型)   │   (缓存)     │ (对象存储) │
├────────────┼──────────────┼────────────┼──────────────┼────────────┤
│ 分钟K线    │ 日/周/月线   │ 主数据     │ 最新行情快照 │ PDF/附件   │
│ 集合竞价   │ 每日股本     │ 财务三大表 │ 限流计数     │ 公告全文   │
│ ETF分钟    │ 资金流向     │ 公告索引   │ 榜单缓存     │ 研报PDF    │
│            │ 龙虎榜       │ 新闻索引   │ 搜索缓存     │ 原始JSON   │
│            │ 两融明细     │ 政策法规库 │              │ Parquet    │
│            │ 指数日线     │ 董秘互动   │              │            │
│            │ 基金净值     │ 券商研报   │              │            │
│            │ ETF日线      │            │              │            │
└────────────┴──────────────┴────────────┴──────────────┴────────────┘
```

### 2.1 存储引擎选择理由

| 存储引擎 | 负责数据 | 选择理由 |
|---------|---------|---------|
| **InfluxDB** | 分钟K线、集合竞价快照、ETF分钟 | 时序写入吞吐极高（百万点/秒），自动降采样，Retention Policy 自动清理 |
| **TimescaleDB** | 日/周/月K线、每日股本、资金流向、龙虎榜、两融、指数日线、基金净值 | PostgreSQL 超表，支持 SQL+JOIN+窗口函数+连续聚合，列压缩比极高 |
| **PostgreSQL** | 证券主档、财务三大表、公告索引、新闻索引、政策法规、董秘互动、券商研报 | ACID 事务、外键约束、SCD（缓慢变化维度）、全文搜索 |
| **Redis** | 最新行情缓存、榜单缓存、限流计数、RAG 检索缓存 | 亚毫秒延迟、Pub/Sub 实时推送、TTL 自动过期 |
| **MinIO (S3)** | 公告PDF/HTML 全文、研报PDF、原始接口 JSON 归档、Parquet 批量导出 | 成本极低、分区存储、与 S3 生态兼容 |

---

## 3. 数据表与结构详细设计

以下按用户需求的 **9 大数据领域** 逐一展开，每种数据明确标注：
- **Tushare 接口名**（15000积分权限覆盖）
- **存储数据库**
- **表名 / Measurement 名**
- **完整字段定义**
- **索引/分区策略**

---

### 3.1 盘前股本情况

**说明**：每日开盘前的股本结构数据，包括总股本、流通股本、自由流通股本、总市值、流通市值等。这是选股、风控（市值因子）、仓位计算的核心输入。

**数据来源**：Tushare `daily_basic` 接口（5000+积分权限）

**存储**：**TimescaleDB**（按 `trade_date` 做 Hypertable）

#### 表：`stock_daily_basic` (TimescaleDB Hypertable)

```sql
CREATE TABLE stock_daily_basic (
    ts_code        VARCHAR(16)   NOT NULL,    -- 股票代码 (000001.SZ)
    trade_date     DATE          NOT NULL,    -- 交易日期
    close          NUMERIC(12,4),             -- 收盘价
    turnover_rate  NUMERIC(12,4),             -- 换手率(%)
    turnover_rate_f NUMERIC(12,4),            -- 自由流通换手率(%)
    volume_ratio   NUMERIC(12,4),             -- 量比
    pe             NUMERIC(16,4),             -- 市盈率（静态）
    pe_ttm         NUMERIC(16,4),             -- 市盈率（TTM）
    pb             NUMERIC(16,4),             -- 市净率
    ps             NUMERIC(16,4),             -- 市销率（静态）
    ps_ttm         NUMERIC(16,4),             -- 市销率（TTM）
    dv_ratio       NUMERIC(12,4),             -- 股息率(%)
    dv_ttm         NUMERIC(12,4),             -- 股息率（TTM %）
    total_share    NUMERIC(20,4),             -- 总股本（万股）★ 核心
    float_share    NUMERIC(20,4),             -- 流通股本（万股）★ 核心
    free_share     NUMERIC(20,4),             -- 自由流通股本（万股）★ 核心
    total_mv       NUMERIC(20,4),             -- 总市值（万元）★ 核心
    circ_mv        NUMERIC(20,4),             -- 流通市值（万元）★ 核心
    PRIMARY KEY (ts_code, trade_date)
);

-- 转换为 TimescaleDB Hypertable
SELECT create_hypertable('stock_daily_basic', 'trade_date');

-- 索引
CREATE INDEX idx_daily_basic_code ON stock_daily_basic (ts_code, trade_date DESC);
CREATE INDEX idx_daily_basic_total_mv ON stock_daily_basic (trade_date, total_mv);
```

**Redis 缓存补充**：`basics:latest:{ts_code}` → JSON `{total_share, float_share, free_share, total_mv, circ_mv, pe_ttm, pb}`（盘前更新，TTL=24h）

**Agent 调用示例**：
```python
# 获取某日盘前股本（避免前视偏差：取最近交易日）
basics = storage_manager.get_latest_basic("000001.SZ", as_of="2025-03-15")
print(basics['total_share'], basics['circ_mv'])
```

---

### 3.2 盘前集合竞价

**说明**：每个交易日 9:15–9:25 的集合竞价阶段数据，包括竞价价格、竞价量、未匹配量等。这是短线策略和开盘跳空策略的关键输入。

**数据来源**：Tushare `stk_mins` 接口（分钟线，8000+积分权限，包含 9:15-9:25 的竞价阶段数据）；或通过 `stk_auction` 接口（若可用）。

**存储**：**InfluxDB**（高频时序，9:25 的快照同时写入 PostgreSQL）

#### Measurement：`stock_auction_min` (InfluxDB)

```
Bucket: market_data
Measurement: stock_auction_min

Tags:
  - ts_code      (例如 "000001.SZ")
  - auction_phase (例如 "call_auction" / "opening_match")

Fields:
  - open         开盘参考价/竞价价
  - high         竞价最高价
  - low          竞价最低价
  - close        竞价最终价（9:25 撮合价）
  - vol          竞价匹配量
  - amount       竞价匹配金额
  - bid_vol      买入申报总量
  - ask_vol      卖出申报总量
  - imbalance    未匹配量（买-卖）

Timestamp: 竞价时间（9:15 ~ 9:25，每分钟或每条快照）
```

**Retention Policy**：保留 30 天高频数据，自动降采样到 1 日粒度后转存 TimescaleDB。

#### 表：`stock_auction_daily` (TimescaleDB Hypertable)

```sql
-- 每日竞价汇总表（从 InfluxDB 聚合或直接采集）
CREATE TABLE stock_auction_daily (
    ts_code        VARCHAR(16)  NOT NULL,
    trade_date     DATE         NOT NULL,
    open_price     NUMERIC(12,4),            -- 9:25 开盘价
    preclose_price NUMERIC(12,4),            -- 前收盘价
    auction_vol    BIGINT,                   -- 集合竞价成交量
    auction_amount NUMERIC(20,4),            -- 集合竞价成交额
    bid_vol_total  BIGINT,                   -- 竞价总买量
    ask_vol_total  BIGINT,                   -- 竞价总卖量
    gap_pct        NUMERIC(12,4),            -- 开盘涨幅(%)
    PRIMARY KEY (ts_code, trade_date)
);

SELECT create_hypertable('stock_auction_daily', 'trade_date');
CREATE INDEX idx_auction_code ON stock_auction_daily (ts_code, trade_date DESC);
```

**Redis 缓存**：`auction:today:{ts_code}` → 当日竞价 JSON 快照（9:26 写入，TTL=4h）

---

### 3.3 ETF 历史分钟

**说明**：ETF（交易型开放式指数基金）的分钟级 K 线数据。ETF 在交易所交易，其分钟线获取方式与股票一致。

**数据来源**：Tushare `stk_mins` 接口（ETF 代码以 `ts_code` 传入，如 `510050.SH`），15000积分权限覆盖。

**存储**：**InfluxDB**（与股票分钟线共用 Bucket，通过 Tag 区分资产类别）

#### Measurement：`etf_bar_mins` (InfluxDB)

```
Bucket: market_data
Measurement: fund_bar_mins

Tags:
  - ts_code      (例如 "510050.SH")
  - freq         (例如 "1min", "5min", "15min", "30min", "60min")
  - asset_class  ("ETF")

Fields:
  - open
  - high
  - low
  - close
  - vol         成交量（手）
  - amount      成交额（元）

Timestamp: 行情时间戳
```

**Retention Policy**：
- 1min 粒度保留 7 天
- 5min 粒度保留 30 天
- 15min 及以上保留 365 天

**降采样规则（InfluxDB Continuous Query）**：
```
// 1min → 5min 聚合
CREATE CONTINUOUS QUERY cq_etf_5min ON market_data
BEGIN
  SELECT first(open) AS open, max(high) AS high, min(low) AS low,
         last(close) AS close, sum(vol) AS vol, sum(amount) AS amount
  INTO market_data.autogen.fund_bar_mins_5min
  FROM market_data.autogen.fund_bar_mins
  GROUP BY time(5m), ts_code
END
```

#### 表：`etf_daily` (TimescaleDB Hypertable，日线）

```sql
CREATE TABLE etf_daily (
    ts_code        VARCHAR(16)  NOT NULL,
    trade_date     DATE         NOT NULL,
    open           NUMERIC(12,4),
    high           NUMERIC(12,4),
    low            NUMERIC(12,4),
    close          NUMERIC(12,4),
    pre_close      NUMERIC(12,4),
    change         NUMERIC(12,4),
    pct_chg        NUMERIC(12,4),
    vol            BIGINT,
    amount         NUMERIC(20,4),
    PRIMARY KEY (ts_code, trade_date)
);

SELECT create_hypertable('etf_daily', 'trade_date');
CREATE INDEX idx_etf_daily_code ON etf_daily (ts_code, trade_date DESC);
```

#### 表：`ref_etf_basic` (PostgreSQL，ETF 主档）

```sql
CREATE TABLE ref_etf_basic (
    ts_code        VARCHAR(16)  PRIMARY KEY,
    name           VARCHAR(100),
    management     VARCHAR(100),           -- 管理人
    fund_type      VARCHAR(20),            -- ETF 类型（股票/债券/商品/跨境）
    bench_index    VARCHAR(50),            -- 跟踪指数
    list_date      DATE,
    issue_date     DATE,
    list_status    VARCHAR(1),             -- L上市 D退市
    found_date     DATE,
    invest_type    VARCHAR(50),
    custodian      VARCHAR(100),
    m_fee          NUMERIC(12,4),          -- 管理费
    c_fee          NUMERIC(12,4),          -- 托管费
    unit_total     NUMERIC(20,4)           -- 总份额
);

CREATE INDEX idx_etf_basic_type ON ref_etf_basic (fund_type);
CREATE INDEX idx_etf_basic_status ON ref_etf_basic (list_status);
```

**Redis 缓存**：`etf:last_bar:{ts_code}` → 最新分钟 OHLCV（TTL=60s）

---

### 3.4 新闻资讯

**说明**：全市场即时新闻、要闻、快讯等文本数据，是 NLP/LLM 驱动的量化策略的关键非结构化输入。

**数据来源**：
- Tushare `news` 接口（即时新闻，5000+积分）
- Tushare `major_news` 接口（要闻精选）
- Tushare `cctv_news` 接口（新闻联播文稿）

**存储**：
- **PostgreSQL**：新闻元数据/索引（结构化检索）
- **MinIO**：新闻全文/HTML 正文（对象存储）
- **PGVector**（可选）：新闻 Embedding 向量（语义检索）

#### 表：`news_article` (PostgreSQL)

```sql
CREATE TABLE news_article (
    id             BIGSERIAL     PRIMARY KEY,
    article_id     VARCHAR(64)   UNIQUE,    -- Tushare 新闻ID
    ts_code        VARCHAR(16),             -- 相关股票（可为 NULL 表示全市场）
    title          TEXT          NOT NULL,  -- 新闻标题
    source         VARCHAR(100),            -- 来源（新华社/证券时报/财联社等）
    news_type      VARCHAR(20),            -- news / major_news / cctv
    channels       VARCHAR(200),           -- 分类标签（如 "宏观,行业,公司"）
    publish_time   TIMESTAMPTZ   NOT NULL, -- 发布时间
    sentiment      NUMERIC(5,4),           -- 情感得分（-1到1，由NLP模型离线计算）
    importance     SMALLINT,               -- 重要性评级（1-5）
    url            TEXT,                    -- 原始URL
    bucket_key     VARCHAR(256),           -- MinIO 对象键（正文存储位置）
    content_preview TEXT,                   -- 正文前512字（快速预览）
    raw_hash       VARCHAR(64),            -- 原始内容 SHA256（去重）
    created_at     TIMESTAMPTZ   DEFAULT NOW(),
    
    -- 全文搜索
    search_vector  TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(content_preview,''))
    ) STORED
);

-- 索引
CREATE INDEX idx_news_publish ON news_article (publish_time DESC);
CREATE INDEX idx_news_code ON news_article (ts_code) WHERE ts_code IS NOT NULL;
CREATE INDEX idx_news_type ON news_article (news_type);
CREATE INDEX idx_news_search ON news_article USING GIN (search_vector);
```

**MinIO 存储路径规范**：
```
tushare/raw/news/{yyyy}/{mm}/{dd}/{article_id}.json       ← 原始JSON
tushare/raw/news/{yyyy}/{mm}/{dd}/{article_id}.html       ← HTML正文
tushare/processed/news/{article_id}_plain.txt             ← 提取后的纯文本（供LLM）
tushare/processed/news/{article_id}_summary.json          ← LLM摘要结果
```

**PGVector（可选扩展）**：
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE news_embedding (
    article_id     BIGINT REFERENCES news_article(id),
    model_name     VARCHAR(64),             -- 如 "bge-large-zh-v1.5"
    chunk_index    SMALLINT DEFAULT 0,      -- 长文分块序号
    embedding      vector(1024),            -- Embedding 向量（维度取决于模型）
    created_at     TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (article_id, model_name, chunk_index)
);

CREATE INDEX idx_news_embed ON news_embedding 
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

**Redis 缓存**：
- `news:latest:10` → 最新10条新闻摘要（TTL=60s）
- `news:by_code:{ts_code}:10` → 某股最新10条新闻（TTL=120s）
- `news:search:{hash(query)}` → 搜索结果缓存（TTL=300s）

---

### 3.5 公司公告

**说明**：上市公司定期公告（年报/半年报/季报）、临时公告（重大事项/股权变动/分红等）。

**数据来源**：Tushare `anns` 接口（5000+积分权限）

**存储**：
- **PostgreSQL**：公告元数据索引
- **MinIO**：公告 PDF/HTML 原始文件 + 解析后的纯文本

#### 表：`news_ann` (PostgreSQL)

```sql
CREATE TABLE news_ann (
    id             BIGSERIAL     PRIMARY KEY,
    ann_id         VARCHAR(64)   UNIQUE NOT NULL,  -- Tushare 公告ID
    ts_code        VARCHAR(16)   NOT NULL,         -- 股票代码
    ann_date       DATE          NOT NULL,         -- 公告日期
    ann_type       VARCHAR(20),                    -- 公告类型：年报/季报/临时/分红/增减持等
    title          TEXT          NOT NULL,         -- 公告标题
    url            TEXT,                            -- 原始公告URL
    bucket_key     VARCHAR(256),                   -- MinIO 对象键
    file_size      BIGINT,                         -- 文件大小（字节）
    file_format    VARCHAR(10),                    -- pdf / html / txt
    raw_hash       VARCHAR(64),                    -- SHA256（去重）
    parsed_text    TEXT,                           -- 解析后的纯文本（若已处理）
    key_topics     TEXT[],                         -- 关键主题标签数组
    created_at     TIMESTAMPTZ   DEFAULT NOW()
);

-- 索引
CREATE INDEX idx_ann_date ON news_ann (ann_date DESC);
CREATE INDEX idx_ann_code ON news_ann (ts_code, ann_date DESC);
CREATE INDEX idx_ann_type ON news_ann (ann_type);
```

**MinIO 路径规范**：
```
tushare/raw/ann/{ts_code}/{ann_date}/{title_slug}.pdf         ← 原始PDF
tushare/raw/ann/{ts_code}/{ann_date}/{title_slug}.html        ← HTML版
tushare/processed/ann/{ann_id}_plain.txt                      ← 提取文本
tushare/processed/ann/{ann_id}_summary.json                   ← LLM摘要
tushare/processed/ann/{ann_id}_financial.json                 ← 财务数据提取（结构化）
```

**Redis 缓存**：
- `ann:latest_by_code:{ts_code}:10` → 某股最新10条公告（TTL=1h）
- `ann:by_date:{date}` → 某日公告列表（TTL=6h）

**定时任务建议**：每日 16:00（收盘后）批量拉取当日公告，17:00 前完成文本提取与 LLM 摘要生成。

---

### 3.6 政策法规库

**说明**：证监会/交易所/央行/财政部等部门发布的政策法规、监管文件、行业标准。这是市场情绪和板块轮动的宏观驱动因子。

**数据来源**：
- Tushare `law` 接口（若平台提供）
- 自建采集：证监会官网、上交所/深交所公告、人民银行、国务院
- AkShare 补充：`akshare` 政策相关接口

**存储**：
- **PostgreSQL**：政策元数据 + 全文索引
- **MinIO**：政策原文 PDF/docx

#### 表：`ref_policy_law` (PostgreSQL)

```sql
CREATE TABLE ref_policy_law (
    id             BIGSERIAL     PRIMARY KEY,
    policy_id      VARCHAR(64)   UNIQUE NOT NULL,  -- 政策编号
    title          TEXT          NOT NULL,         -- 政策标题
    dept           VARCHAR(100),                   -- 发文部门（证监会/央行/财政部等）
    policy_type    VARCHAR(30),                    -- 类型：法律/法规/部门规章/规范性文件/通知
    pub_date       DATE          NOT NULL,         -- 发布日期
    effective_date DATE,                           -- 生效日期
    status         VARCHAR(10)   DEFAULT 'active', -- active / repealed / amended
    industry_tags  TEXT[],                         -- 行业标签数组（如 {"银行","保险","新能源"}）
    topic_tags     TEXT[],                         -- 主题标签（如 {"IPO","减持","信息披露"}）
    url            TEXT,                            -- 原始链接
    bucket_key     VARCHAR(256),                   -- MinIO 对象键
    content_full   TEXT,                           -- 全文内容（若入库）
    content_summary TEXT,                          -- 摘要
    impact_rating  SMALLINT,                       -- 市场影响评级（1-5）
    created_at     TIMESTAMPTZ   DEFAULT NOW(),

    search_vector  TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(content_full,''))
    ) STORED
);

CREATE INDEX idx_policy_date ON ref_policy_law (pub_date DESC);
CREATE INDEX idx_policy_dept ON ref_policy_law (dept);
CREATE INDEX idx_policy_type ON ref_policy_law (policy_type);
CREATE INDEX idx_policy_search ON ref_policy_law USING GIN (search_vector);
CREATE INDEX idx_policy_industry ON ref_policy_law USING GIN (industry_tags);
```

**MinIO 路径**：
```
policies/raw/{dept}/{yyyy}/{policy_id}.pdf
policies/processed/{policy_id}_plain.txt
```

**定时采集**：周度扫描各部门官网，增量入库。重大政策发布后 2 小时内入库。

---

### 3.7 董秘互动回复

**说明**：投资者通过深交所"互动易"、上交所"上证e互动"向上市公司董秘提问，公司董秘公开回复的内容。这是了解公司经营动态、管理层态度的独特信息源。

**数据来源**：
- Tushare `stk_insider`（若平台提供互动易数据）
- AkShare `stock_irm_cninfo` 接口
- 自建爬虫采集互动易/e互动平台

**存储**：
- **PostgreSQL**：问答结构数据（结构化Q&A）
- **MinIO**：长回复原文

#### 表：`board_secretary_interact` (PostgreSQL)

```sql
CREATE TABLE board_secretary_interact (
    id             BIGSERIAL     PRIMARY KEY,
    interact_id    VARCHAR(64)   UNIQUE NOT NULL,   -- 互动ID
    ts_code        VARCHAR(16)   NOT NULL,          -- 股票代码
    company_name   VARCHAR(100),                    -- 公司简称
    platform       VARCHAR(20),                     -- 互动易 / e互动
    question       TEXT          NOT NULL,          -- 投资者提问
    question_time  TIMESTAMPTZ   NOT NULL,          -- 提问时间
    answer         TEXT,                            -- 董秘回复
    answer_time    TIMESTAMPTZ,                     -- 回复时间
    is_answered    BOOLEAN       DEFAULT FALSE,     -- 是否已回复
    questioner     VARCHAR(50),                     -- 提问者（可能匿名）
    tags           TEXT[],                          -- 问题涉及的标签（如"业绩","分红","重组"）
    sentiment_q    NUMERIC(5,4),                    -- 提问情感得分
    sentiment_a    NUMERIC(5,4),                    -- 回复情感得分
    created_at     TIMESTAMPTZ   DEFAULT NOW(),

    search_vector  TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(question,'') || ' ' || coalesce(answer,''))
    ) STORED
);

CREATE INDEX idx_interact_code ON board_secretary_interact (ts_code, question_time DESC);
CREATE INDEX idx_interact_time ON board_secretary_interact (answer_time DESC) WHERE is_answered;
CREATE INDEX idx_interact_search ON board_secretary_interact USING GIN (search_vector);
```

**Agent 调用示例**：
```python
# 查询某公司最近互动回复
replies = storage_manager.query_interact(
    ts_code="000001.SZ",
    start="2025-01-01",
    tags=["分红", "业绩"],
    limit=50
)
```

**定时采集**：每日 18:00 增量拉取当日新增问答，NLP 分析后标记情感与标签。

---

### 3.8 券商研报

**说明**：各大券商发布的研究报告，包含个股推荐评级、目标价、盈利预测、行业分析等。这是获取专业分析师观点的核心数据源。

**数据来源**：Tushare `broker_reports` 接口（8000~10000+积分权限）

**存储**：
- **PostgreSQL**：研报元数据 + 摘要（结构化检索）
- **MinIO**：研报 PDF 原文

#### 表：`broker_report` (PostgreSQL)

```sql
CREATE TABLE broker_report (
    id             BIGSERIAL     PRIMARY KEY,
    report_id      VARCHAR(64)   UNIQUE NOT NULL,   -- 研报ID
    ts_code        VARCHAR(16)   NOT NULL,          -- 股票代码
    stock_name     VARCHAR(100),                    -- 股票名称
    broker         VARCHAR(100),                    -- 券商名称
    analyst        VARCHAR(100),                    -- 分析师姓名
    analyst_level  VARCHAR(50),                     -- 分析师评级（如 "新财富白金"）
    report_title   TEXT          NOT NULL,          -- 研报标题
    report_type    VARCHAR(30),                     -- 类型：个股/行业/策略/宏观/量化
    rating         VARCHAR(20),                     -- 评级：买入/增持/中性/减持/卖出
    rating_change  VARCHAR(20),                     -- 评级变动：首次/调高/维持/调低
    target_price   NUMERIC(12,4),                   -- 目标价
    current_price  NUMERIC(12,4),                   -- 当前价
    upside_pct     NUMERIC(12,4),                   -- 上行空间(%)
    -- 盈利预测
    eps_forecast_0 NUMERIC(12,4),                   -- 当年 EPS 预测
    eps_forecast_1 NUMERIC(12,4),                   -- 次年 EPS 预测
    eps_forecast_2 NUMERIC(12,4),                   -- 第三年 EPS 预测
    pe_forecast_0  NUMERIC(12,4),                   -- 当年 PE 预测
    pe_forecast_1  NUMERIC(12,4),                   -- 次年 PE 预测
    -- 元数据
    publish_date   DATE          NOT NULL,          -- 发布日期
    report_url     TEXT,                            -- 报告链接
    bucket_key     VARCHAR(256),                   -- MinIO 研报 PDF 键
    summary        TEXT,                            -- 摘要
    core_view      TEXT,                            -- 核心观点
    key_risks      TEXT,                            -- 风险提示
    file_size      BIGINT,                          -- 文件大小
    created_at     TIMESTAMPTZ   DEFAULT NOW(),

    search_vector  TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple',
            coalesce(report_title,'') || ' ' ||
            coalesce(summary,'') || ' ' ||
            coalesce(core_view,'')
        )
    ) STORED
);

-- 索引
CREATE INDEX idx_report_date ON broker_report (publish_date DESC);
CREATE INDEX idx_report_code ON broker_report (ts_code, publish_date DESC);
CREATE INDEX idx_report_broker ON broker_report (broker, publish_date DESC);
CREATE INDEX idx_report_rating ON broker_report (rating);
CREATE INDEX idx_report_search ON broker_report USING GIN (search_vector);

-- 研报评级变化跟踪表
CREATE TABLE broker_report_consensus (
    ts_code        VARCHAR(16)   NOT NULL,
    calc_date      DATE          NOT NULL,
    buy_count      INT,                         -- 买入评级数
    overweight_count INT,                        -- 增持评级数
    hold_count     INT,                         -- 中性评级数
    underweight_count INT,                      -- 减持评级数
    sell_count     INT,                         -- 卖出评级数
    total_count    INT,                         -- 总评级数
    consensus_score NUMERIC(6,4),               -- 综合评级分（5=全买入，1=全卖出）
    avg_target     NUMERIC(12,4),               -- 平均目标价
    avg_upside     NUMERIC(12,4),               -- 平均上行空间
    PRIMARY KEY (ts_code, calc_date)
);

SELECT create_hypertable('broker_report_consensus', 'calc_date');
```

**MinIO 路径**：
```
broker_reports/{broker}/{yyyy}/{mm}/{report_id}.pdf
broker_reports/processed/{report_id}_extract.json    ← 结构化提取
broker_reports/processed/{report_id}_summary.json    ← LLM摘要
```

**Redis 缓存**：
- `report:consensus:{ts_code}` → 最新一致预期 JSON（TTL=24h）
- `report:hot:{date}` → 今日热门研报 TOP20（TTL=2h）
- `report:latest_by_code:{ts_code}:5` → 某股最近 5 份研报（TTL=1h）

---

### 3.9 股票历史分钟

**说明**：A 股全市场个股的分钟级 K 线数据（1分钟/5分钟/15分钟/30分钟/60分钟），是高频策略回测的基石。

**数据来源**：Tushare `stk_mins` 接口（8000+积分权限，15000积分可获取全量历史）

**存储**：**InfluxDB**（绝对主力存储，天量写入与查询）

#### Measurement：`stock_bar_mins` (InfluxDB)

```
Bucket: market_data
Measurement: stock_bar_mins

Tags:
  - ts_code      (例如 "000001.SZ")
  - freq         (例如 "1min", "5min", "15min", "30min", "60min")
  - market       ("SH" / "SZ" / "BJ")

Fields:
  - open         开盘价
  - high         最高价
  - low          最低价
  - close        收盘价
  - vol          成交量（手）
  - amount       成交额（元）
  - pre_close    前收盘价（如果有）
  - change       涨跌额
  - pct_chg      涨跌幅(%)

Timestamp: K线起始时间（如 9:31 的 1min 线，时间为 9:31:00）
```

**Retention Policy（数据保留策略）**：
```
名称: rp_stock_mins
- 1min 粒度:  保留 30 天  (写入最频繁，短期热数据)
- 5min 粒度:  保留 180 天 (通过 Continuous Query 聚合)
- 15min 粒度: 保留 365 天
- 30min 粒度: 保留 730 天
- 60min 粒度: 永久保留       (分析级数据)
```

**Continuous Query（自动降采样）**：
```
// 1min → 5min
CREATE CONTINUOUS QUERY cq_stock_5min ON market_data
BEGIN
  SELECT first(open) AS open, max(high) AS high, min(low) AS low,
         last(close) AS close, sum(vol) AS vol, sum(amount) AS amount
  INTO market_data.rp_stock_mins.stock_bar_mins_5min
  FROM market_data.autogen.stock_bar_mins
  WHERE freq = '1min'
  GROUP BY time(5m), ts_code
END

// 1min → 60min
CREATE CONTINUOUS QUERY cq_stock_60min ON market_data
BEGIN
  SELECT first(open) AS open, max(high) AS high, min(low) AS low,
         last(close) AS close, sum(vol) AS vol, sum(amount) AS amount
  INTO market_data.rp_stock_mins.stock_bar_mins_60min
  FROM market_data.autogen.stock_bar_mins
  WHERE freq = '1min'
  GROUP BY time(60m), ts_code
END
```

**日线同步**：每个交易日 15:30 后，从 InfluxDB 分钟线聚合生成日线，同步写入 TimescaleDB `stock_daily` 表。

#### 表：`stock_daily` (TimescaleDB Hypertable，日线汇总）

```sql
CREATE TABLE stock_daily (
    ts_code        VARCHAR(16)  NOT NULL,
    trade_date     DATE         NOT NULL,
    open           NUMERIC(12,4),
    high           NUMERIC(12,4),
    low            NUMERIC(12,4),
    close          NUMERIC(12,4),
    pre_close      NUMERIC(12,4),
    change         NUMERIC(12,4),
    pct_chg        NUMERIC(12,4),
    vol            BIGINT,
    amount         NUMERIC(20,4),
    PRIMARY KEY (ts_code, trade_date)
);

SELECT create_hypertable('stock_daily', 'trade_date',
    chunk_time_interval => INTERVAL '1 month'
);

-- 启用压缩（持仓超过 7 天的 chunk 自动压缩）
ALTER TABLE stock_daily SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'ts_code',
    timescaledb.compress_orderby = 'trade_date DESC'
);

SELECT add_compression_policy('stock_daily', INTERVAL '7 days');

-- 常用查询索引
CREATE INDEX idx_daily_code ON stock_daily (ts_code, trade_date DESC);
```

**Redis 缓存**：
- `stock:last_bar:{ts_code}` → 最新一分钟数据（TTL=120s）
- `stock:last_bar:daily:{ts_code}` → 最新日线数据（TTL=24h）

**MinIO 归档**：
```
exports/stock_mins/{yyyy}/{ts_code}/{freq}.parquet     ← 按月分区导出
exports/stock_daily/{yyyy}/all_stocks.parquet           ← 按年全量导出
```

---

## 4. 辅助表（跨领域共享）

### 4.1 交易日历

```sql
-- PostgreSQL
CREATE TABLE ref_trade_cal (
    exchange       VARCHAR(10)  NOT NULL,  -- SSE / SZSE / BJ
    cal_date       DATE         NOT NULL,
    is_open        SMALLINT     NOT NULL,  -- 1=开市, 0=休市
    pretrade_date  DATE,
    PRIMARY KEY (exchange, cal_date)
);

-- Redis: cal:latest:{exchange} → {is_open, pretrade_date}
```

### 4.2 证券主档

```sql
-- PostgreSQL (SCD Type 2 或定期全量刷新)
CREATE TABLE ref_stock_basic (
    ts_code        VARCHAR(16)  PRIMARY KEY,
    symbol         VARCHAR(10)  NOT NULL,
    name           VARCHAR(50),
    area           VARCHAR(20),
    industry       VARCHAR(50),
    cnspell        VARCHAR(30),
    market         VARCHAR(10),
    list_date      DATE,
    list_status    VARCHAR(1),              -- L / D / P (上市/退市/暂停)
    delist_date    DATE,
    is_hs          VARCHAR(1),              -- 是否沪深港通
    act_name       VARCHAR(200),            -- 公司全称
    act_ent_type   VARCHAR(50)              -- 企业类型
);

CREATE INDEX idx_stock_industry ON ref_stock_basic (industry);
CREATE INDEX idx_stock_status ON ref_stock_basic (list_status);
```

### 4.3 复权因子

```sql
-- TimescaleDB Hypertable
CREATE TABLE stock_adj_factor (
    ts_code        VARCHAR(16)  NOT NULL,
    trade_date     DATE         NOT NULL,
    adj_factor     NUMERIC(16,8),
    PRIMARY KEY (ts_code, trade_date)
);

SELECT create_hypertable('stock_adj_factor', 'trade_date');
```

---

## 5. 所有数据表-存储-来源对照总表

| 序号 | 数据名称 | 主存储 | 辅助存储 | Tushare 接口 | 表/Measurement |
|------|---------|--------|---------|-------------|---------------|
| 1 | **盘前股本情况** | TimescaleDB | Redis（缓存） | `daily_basic` | `stock_daily_basic` |
| 2 | **盘前集合竞价** | InfluxDB | TimescaleDB（日聚合） | `stk_mins` / `stk_auction` | `stock_auction_min` / `stock_auction_daily` |
| 3 | **ETF历史分钟** | InfluxDB | TimescaleDB（日线） | `stk_mins` (ETF代码) | `fund_bar_mins` / `etf_daily` |
| 4 | **新闻资讯** | PostgreSQL | MinIO（全文） / PGVector | `news` / `major_news` | `news_article` + MinIO |
| 5 | **公司公告** | PostgreSQL | MinIO（PDF/全文） | `anns` | `news_ann` + MinIO |
| 6 | **政策法规库** | PostgreSQL | MinIO（原文） | 自建采集 + AkShare | `ref_policy_law` |
| 7 | **董秘互动回复** | PostgreSQL | MinIO | 互动易/e互动/自建 | `board_secretary_interact` |
| 8 | **券商研报** | PostgreSQL | MinIO（PDF） / TimescaleDB（一致预期） | `broker_reports` | `broker_report` + `broker_report_consensus` |
| 9 | **股票历史分钟** | InfluxDB | TimescaleDB（日聚合） / MinIO（归档） | `stk_mins` | `stock_bar_mins` |
| — | 股票日线 | TimescaleDB | Redis（缓存） | `daily` | `stock_daily` |
| — | 复权因子 | TimescaleDB | — | `adj_factor` | `stock_adj_factor` |
| — | 证券主档 | PostgreSQL | — | `stock_basic` | `ref_stock_basic` |
| — | 交易日历 | PostgreSQL | Redis（缓存） | `trade_cal` | `ref_trade_cal` |
| — | 财务三大表+指标 | PostgreSQL | — | `income`/`balancesheet`/`cashflow`/`fina_indicator` | `fin_*` |
| — | 资金流向 | TimescaleDB | Redis（缓存） | `moneyflow` | `stock_moneyflow_daily` |
| — | 龙虎榜 | TimescaleDB | Redis（缓存） | `top_list` | `stock_top_list_daily` |
| — | 融资融券 | TimescaleDB | — | `margin`/`margin_detail` | `margin_*` |
| — | 指数日线 | TimescaleDB | — | `index_daily` | `index_daily` |
| — | 基金净值 | TimescaleDB | — | `fund_nav` | `fund_nav` |
| — | ETF主档 | PostgreSQL | — | `fund_basic` | `ref_etf_basic` |

---

## 6. Agent 调用规范

### 6.1 核心原则

```
┌──────────────────────────────────────────────────┐
│  ⚠️ 所有 Agent 禁止直接连接数据库              │
│  ✅ 必须通过 storage_manager 统一接口调用        │
└──────────────────────────────────────────────────┘
```

### 6.2 统一调用接口（`data_layer/db_manager.py` 扩展）

```python
from data_layer.db_manager import storage_manager

# ============ 分钟线查询 ============
mins = storage_manager.query_minute_bars(
    symbol="000001.SZ",
    freq="5min",
    start="2025-04-01",
    end="2025-04-15",
    limit=10000
)  # → InfluxDB (stock_bar_mins)

# ============ 日线查询（自动复权）============
daily = storage_manager.query_daily(
    symbol="000001.SZ",
    start="2020-01-01",
    end="2025-04-15",
    adj_type="qfq"  # 前复权 / hfq 后复权 / None 不复权
)  # → TimescaleDB (stock_daily + stock_adj_factor)

# ============ 盘前股本 ============
basics = storage_manager.get_latest_basic(
    symbol="000001.SZ",
    as_of="2025-04-15"  # 截止日，取之前最近交易日数据
)  # → TimescaleDB (stock_daily_basic)
# 返回: {total_share, float_share, free_share, total_mv, circ_mv, pe_ttm, pb, ...}

# ============ 集合竞价 ============
auction = storage_manager.query_auction(
    symbol="000001.SZ",
    date="2025-04-15"
)  # → InfluxDB 或 stock_auction_daily

# ============ ETF分钟线 ============
etf_mins = storage_manager.query_etf_minute_bars(
    symbol="510050.SH",
    freq="5min",
    start="2025-04-01",
    end="2025-04-15"
)  # → InfluxDB (fund_bar_mins)

# ============ 新闻查询（全文搜索）============
news = storage_manager.query_news(
    symbol="000001.SZ",       # 可选，不限股票则 None
    keywords=["降息", "降准"],  # 全文搜索关键词
    start="2025-04-01",
    end="2025-04-15",
    limit=50
)  # → PostgreSQL (news_article)

# ============ 公告查询 ============
anns = storage_manager.query_announcements(
    symbol="000001.SZ",
    ann_type="年报",  # 可选
    start="2024-01-01",
    end="2025-04-15",
    limit=20
)  # → PostgreSQL (news_ann)

# ============ 政策法规查询 ============
policies = storage_manager.query_policies(
    dept="证监会",
    topic="减持",
    start="2024-01-01",
    limit=30
)  # → PostgreSQL (ref_policy_law)

# ============ 董秘互动 ============
interact = storage_manager.query_interact(
    symbol="000001.SZ",
    tags=["分红", "业绩"],
    start="2025-01-01",
    limit=50
)  # → PostgreSQL (board_secretary_interact)

# ============ 券商研报 ============
reports = storage_manager.query_broker_reports(
    symbol="000001.SZ",
    rating=["买入", "增持"],
    start="2025-01-01",
    limit=30
)  # → PostgreSQL (broker_report)

# ============ 研报一致预期 ============
consensus = storage_manager.get_report_consensus(
    symbol="000001.SZ"
)  # → TimescaleDB (broker_report_consensus) → Redis 缓存
```

### 6.3 避免前视偏差（Look-Ahead Bias）的强制规则

```python
def get_data_at_point(symbol: str, data_type: str, point_in_time: datetime):
    """
    ⚠️ 关键函数：获取"截至某时点可见"的数据
    
    规则：
    - 行情数据：取 point_in_time 之前的最后一个交易数据
    - 财务数据：取 ann_date <= point_in_time 的最新一份财报
    - 研报数据：取 publish_date <= point_in_time 的研报
    - 股本数据：取 trade_date <= point_in_time 的最新 daily_basic
    - 公告数据：取 ann_date <= point_in_time 的公告
    
    此函数是回测准确性的核心保障！
    """
    pass
```

---

## 7. 实施优先级与路线图

### Phase 1：核心行情底座（第1-2周）

| 优先级 | 任务 | 存储 | 表/Measurement |
|--------|------|------|---------------|
| P0 | 股票日线 + 复权因子 | TimescaleDB | `stock_daily` + `stock_adj_factor` |
| P0 | 证券主档 + 交易日历 | PostgreSQL | `ref_stock_basic` + `ref_trade_cal` |
| P0 | 盘前股本（每日指标） | TimescaleDB | `stock_daily_basic` |

### Phase 2：高频数据接入（第3-4周）

| 优先级 | 任务 | 存储 | 表/Measurement |
|--------|------|------|---------------|
| P0 | **股票历史分钟**（1/5/15/30/60min） | InfluxDB | `stock_bar_mins` |
| P1 | 盘前集合竞价 | InfluxDB + TimescaleDB | `stock_auction_min` + `stock_auction_daily` |
| P1 | ETF 历史分钟 + 日线 | InfluxDB + TimescaleDB | `fund_bar_mins` + `etf_daily` |

### Phase 3：基本面与资金面（第5-6周）

| 优先级 | 任务 | 存储 | 表 |
|--------|------|------|-----|
| P1 | 财务三大表 + 指标 | PostgreSQL | `fin_income` / `fin_balancesheet` / `fin_cashflow` / `fin_fina_indicator` |
| P1 | 资金流向 + 龙虎榜 | TimescaleDB | `stock_moneyflow_daily` / `stock_top_list_daily` |
| P2 | 融资融券 + 大宗交易 | TimescaleDB | `margin_*` / `stock_block_trade` |

### Phase 4：非结构化数据（第7-9周）

| 优先级 | 任务 | 存储 | 表 |
|--------|------|------|-----|
| P1 | **新闻资讯** | PostgreSQL + MinIO | `news_article` + MinIO |
| P1 | **公司公告** | PostgreSQL + MinIO | `news_ann` + MinIO |
| P1 | **券商研报** | PostgreSQL + MinIO | `broker_report` + `broker_report_consensus` |
| P2 | 政策法规库 | PostgreSQL + MinIO | `ref_policy_law` |
| P2 | 董秘互动回复 | PostgreSQL | `board_secretary_interact` |

### Phase 5：高级特性（第10-12周）

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P2 | PGVector 新闻/公告 Embedding | 语义检索，RAG 增强 |
| P2 | InfluxDB Continuous Query | 自动降采样 |
| P2 | TimescaleDB 压缩策略 | 历史数据自动压缩 |
| P3 | MinIO → Parquet 批量导出 | 历史数据归档与分发 |
| P3 | 数据质量监控 (Great Expectations) | 完整性/准确性校验 |

---

## 8. 数据库汇总清单

### PostgreSQL（普通表）

| 序号 | 表名 | 说明 | 数据域 |
|------|------|------|--------|
| 1 | `ref_trade_cal` | 交易日历 | 基础参照 |
| 2 | `ref_stock_basic` | 证券主档 | 基础参照 |
| 3 | `ref_stock_namechange` | 股票更名记录 | 基础参照 |
| 4 | `ref_new_share` | 新股发行 | 基础参照 |
| 5 | `ref_index_basic` | 指数主档 | 指数 |
| 6 | `ref_etf_basic` | ETF主档 | ETF/基金 |
| 7 | `ref_fund_basic` | 基金主档 | 基金 |
| 8 | `ref_policy_law` | 政策法规库 | 政策 |
| 9 | `news_article` | 新闻资讯 | 新闻 |
| 10 | `news_ann` | 公司公告 | 公告 |
| 11 | `board_secretary_interact` | 董秘互动回复 | 互动 |
| 12 | `broker_report` | 券商研报 | 研报 |
| 13 | `fin_income` | 利润表 | 财务 |
| 14 | `fin_balancesheet` | 资产负债表 | 财务 |
| 15 | `fin_cashflow` | 现金流量表 | 财务 |
| 16 | `fin_fina_indicator` | 财务指标 | 财务 |
| 17 | `fin_forecast` | 业绩预告 | 财务 |
| 18 | `fin_express` | 业绩快报 | 财务 |
| 19 | `fin_dividend` | 分红送股 | 财务 |
| 20 | `news_embedding` | 新闻向量（PGVector） | AI/RAG |

### TimescaleDB（Hypertable）

| 序号 | 表名 | 时间维度 | 说明 | 数据域 |
|------|------|---------|------|--------|
| 1 | `stock_daily` | `trade_date` | 股票日K线 | 行情 |
| 2 | `stock_weekly` | `trade_date` | 股票周K线 | 行情 |
| 3 | `stock_monthly` | `trade_date` | 股票月K线 | 行情 |
| 4 | `stock_daily_basic` | `trade_date` | **盘前股本**/每日指标 | 股本 |
| 5 | `stock_adj_factor` | `trade_date` | 复权因子 | 行情 |
| 6 | `stock_auction_daily` | `trade_date` | **集合竞价日聚合** | 竞价 |
| 7 | `stock_moneyflow_daily` | `trade_date` | 资金流向 | 资金 |
| 8 | `stock_top_list_daily` | `trade_date` | 龙虎榜 | 行为 |
| 9 | `margin_summary_daily` | `trade_date` | 两融汇总 | 两融 |
| 10 | `margin_detail_daily` | `trade_date` | 两融明细 | 两融 |
| 11 | `stock_block_trade` | `trade_date` | 大宗交易 | 大宗 |
| 12 | `index_daily` | `trade_date` | 指数日线 | 指数 |
| 13 | `index_weight` | `trade_date` | 指数成分权重 | 指数 |
| 14 | `fund_nav` | `nav_date` | 基金净值 | 基金 |
| 15 | `fund_daily` | `trade_date` | 基金日行情 | 基金 |
| 16 | `etf_daily` | `trade_date` | ETF日行情 | ETF |
| 17 | `broker_report_consensus` | `calc_date` | 券商一致预期 | 研报 |
| 18 | `ref_stk_limit_daily` | `trade_date` | 涨跌停价 | 参照 |
| 19 | `ref_suspend_d` | `trade_date` | 停牌信息 | 参照 |

### InfluxDB

| Measurement | Bucket | Tags | 说明 | Retention |
|-------------|--------|------|------|-----------|
| `stock_bar_mins` | `market_data` | `ts_code`, `freq` | **股票历史分钟** | 1min→30d, 5min→180d, 60min→永久 |
| `fund_bar_mins` | `market_data` | `ts_code`, `freq`, `asset_class` | **ETF历史分钟** | 同上 |
| `stock_auction_min` | `market_data` | `ts_code`, `auction_phase` | **盘前集合竞价** | 30d |

### Redis

| Key 模式 | 类型 | 说明 | TTL |
|----------|------|------|-----|
| `stock:last_bar:{ts_code}` | String(JSON) | 最新分钟行情 | 120s |
| `stock:last_bar:daily:{ts_code}` | String(JSON) | 最新日线 | 24h |
| `basics:latest:{ts_code}` | String(JSON) | 最新股本数据 | 24h |
| `auction:today:{ts_code}` | String(JSON) | 当日竞价快照 | 4h |
| `etf:last_bar:{ts_code}` | String(JSON) | ETF最新行情 | 60s |
| `news:latest:10` | List | 最新10条新闻 | 60s |
| `news:by_code:{ts_code}:10` | List | 某股最新新闻 | 120s |
| `ann:latest_by_code:{ts_code}:10` | List | 某股最新公告 | 1h |
| `report:consensus:{ts_code}` | String(JSON) | 一致预期 | 24h |
| `cal:latest:{exchange}` | String(JSON) | 交易日历 | 24h |
| `tushare:ratelimit:remaining` | String | API限流剩余次数 | 60s |

### MinIO

| Bucket: `backtrader-data` | 说明 |
|--------------------------|------|
| `tushare/raw/news/{yyyy}/{mm}/{dd}/{id}.json` | 新闻原始JSON |
| `tushare/raw/ann/{ts_code}/{ann_date}/{title_slug}.pdf` | 公告原始PDF |
| `broker_reports/{broker}/{yyyy}/{mm}/{report_id}.pdf` | 研报PDF |
| `policies/raw/{dept}/{yyyy}/{policy_id}.pdf` | 政策原文 |
| `exports/stock_mins/{yyyy}/{ts_code}/{freq}.parquet` | 分钟线归档 |
| `exports/stock_daily/{yyyy}/all_stocks.parquet` | 日线年归档 |
| `tushare/processed/news/{article_id}_plain.txt` | 新闻提取文本 |
| `tushare/processed/ann/{ann_id}_summary.json` | 公告LLM摘要 |

---

## 9. 版本记录

| 版本 | 日期 | 说明 |
|------|------|------|
| v0.1 | 2026-04-26 | 初版 Agent 访问与数据源列表 |
| v0.3 | 2026-04-30 | 增补 Tushare 七大类全量域：接口—表—库映射 |
| **v1.0** | **2026-05-10** | **全量版**：新增盘前股本、集合竞价、ETF分钟、新闻资讯、公司公告、政策法规库、董秘互动、券商研报、股票历史分钟 9 大领域完整表结构设计；补充 InfluxDB/Redis/MinIO 详细结构；新增 Phase 实施路线图；新增 Agent 调用规范与避免前视偏差规则 |

---

*文档结束 — 下一步：实现第7节 Phase 1，搭建核心行情底座。*
