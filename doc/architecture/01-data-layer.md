# 01 - 数据层架构设计 (Data Layer Architecture)

**版本**：v0.1  
**作者**：Cursor-Driven Backtrader 迭代开发助手  
**日期**：2026-04-26  
**适用项目**：`E:\demo\backtrader\`  
**关联架构图**：`assets/data-layer-architecture.png`

## 1. 设计原则

本数据层遵循以下核心原则：
1. **回测与实盘数据一致性**（最重要）
2. **高吞吐 + 低延迟**（支持高频交易）
3. **可扩展性与可维护性**
4. **数据治理闭环**（血缘、质量、版本控制）
5. **成本与性能平衡**

## 2. 整体架构图说明

（参考 `assets/data-layer-architecture.png`）

架构从下到上分为 5 层：
- **数据源层** → **数据接入层** → **数据处理层** → **存储层**（核心） → **数据服务层**

重点突出的**存储层**采用多存储引擎混合架构（Polyglot Persistence），每个引擎负责最适合的工作负载。

## 3. 存储层详细设计（核心）

### 3.1 时序数据库 - InfluxDB
- **用途**：存储高频 Tick 数据、OHLCV K 线（1s~1d 多种粒度）
- **理由**：极高的写入吞吐量、时间序列优化、自动降采样、Retention Policy 管理
- **关键配置**：启用 WAL、设置合理的 shard duration、启用连续查询（CQ）做预聚合

### 3.2 关系型数据库 - PostgreSQL + TimescaleDB
- **用途**：
  - 策略配置、回测记录、订单日志、风控规则
  - 使用 TimescaleDB 扩展存储中低频时序数据
- **理由**：事务支持强、SQL 生态成熟、支持复杂关联查询、PGVector 可支持后续大模型特征向量存储
- **关键表**：`symbols`, `strategies`, `backtests`, `trades`, `features`

### 3.3 缓存层 - Redis
- **用途**：最新行情快照、实时特征计算结果、限价订单簿缓存、分布式锁
- **理由**：亚毫秒级延迟、支持 Pub/Sub 实时推送、适合热数据

### 3.4 数据湖 - MinIO (S3 兼容)
- **用途**：原始行情文件、Parquet 格式的历史数据归档、大模型训练数据集
- **理由**：成本低、支持分区、适合冷数据长期保存

### 3.5 分析仓库 - ClickHouse（推荐）或 AWS Redshift
- **用途**：大规模历史数据分析、特征工程离线计算、报表生成
- **理由**：ClickHouse 在时序 + 分析场景性能极强，适合量化回测场景

### 3.6 向量数据库（未来扩展）
- 使用 PostgreSQL 的 PGVector 扩展或独立 Milvus
- 用于存储大模型生成的行情 embedding、新闻情感向量等

## 4. 数据流设计

- **实时路径**：数据源 → WebSocket → Kafka → 实时处理 → InfluxDB + Redis
- **批量路径**：历史数据 → Spark/Flink 批处理 → Parquet → MinIO → ClickHouse
- **回测专用**：实现**确定性数据重放引擎**，保证回测数据与实盘完全一致（关键！）

## 5. 与现有 backtrader 项目集成方案

1. 在 `E:\demo\backtrader\` 中新增 `data/` 目录
2. 开发 `data_feed.py` 适配器，支持从 InfluxDB/PostgreSQL 读取数据
3. 扩展 `backtrader.feeds` 实现自定义 `InfluxDBData` 和 `PostgresData` feed
4. 特征工程结果通过 Feature Store 统一管理，供策略使用

## 6. 数据治理与监控

- **数据血缘**：使用 OpenLineage 或自定义元数据表
- **数据质量**：集成 Great Expectations 做每日校验
- **监控**：Prometheus + Grafana 监控写入延迟、数据完整性、存储使用率
- **告警**：数据延迟超过 5s、缺失率 > 0.1% 时告警

## 7. 实施优先级（最小闭环）

**Phase 1（当前重点）**：
- 搭建 Kafka + InfluxDB + PostgreSQL 基础
- 实现实时行情接入与清洗
- 开发基础 Backtrader 数据 Feed

**Phase 2**：
- Redis 缓存层 + 特征工程 Pipeline
- 数据质量监控系统

**Phase 3**：
- ClickHouse 分析仓库 + MinIO 数据湖
- 完整数据治理与回测一致性保障

---

**文档生成完成** ✅

文档已成功写入 `E:\demo\backtrader\doc\architecture\01-data-layer.md`。

---

**现在执行 Task-003**：

我即将生成本次修改的日志文件（`backtrader-modify-20260426-XXXX.log`），记录本次文档生成的内容、决策理由，并准备**通过微信以文件形式**发送给你。

请回复 **“生成日志”** 或 **“继续”**，我立即完成最后一步闭环。