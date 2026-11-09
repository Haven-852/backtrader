# LightGBM「高低点」训练方案（学习版）

## 1. 先分清两条线（不要混用）

| 维度 | 工作台「日内高低点 · 5 分钟 · LightGBM」 | `ml_train_daily` 日频训练表 |
|------|------------------------------------------|-----------------------------|
| 数据 | 1 分钟 → 5 分钟 K 线（`stock_intraday_mins` / Redis） | 每股每日一行（千万级） |
| 标签 | 三分类：未来 3 根 5 分钟 K 内先触 **上/下 ATR 障碍** 或中性 | 回归：`y_high`、`y_low` = **次日**最高/最低相对**当日收盘**的收益 |
| 代码 | `server/intraday_turning_service.py`，API `POST /strategy/intraday/train` | 采集/体检：`tools/ml_train_toolkit.py`、`ml_train_sanitize_toolkit.py`；训练脚本见 `scripts/train_ml_daily_lgbm.py` |
| 你做的 43 列验证 | 不适用（那是日频宽表） | **直接对应** |

UI 截图里的「5 分钟 + LightGBM」走的是 **第一条**；你整理的 PE/PB/市值清洗清单走的是 **第二条**。下面分别给出划分与训练要点，再合并「训练前检查清单」。

---

## 2. 日频 `ml_train_daily`：预测「次日高低」（与你验证的表一致）

### 2.1 样本与标签（必须理解）

- **一行** = 某股票在某个 **T 日收盘后** 可观测到的全部特征。
- **标签**（已在库里定义，训练前用 `sanitize-ml-train-daily` 审计）：
  - `y_high = next_day_high / close_T - 1`
  - `y_low  = next_day_low  / close_T - 1`
- **禁止**把 `open/high/low/close` 当特征（绝对价格无截面意义）；**禁止**把 `y_*` 当特征。
- **筛选**：`WHERE train_ready = 1 AND y_high IS NOT NULL AND y_low IS NOT NULL`（每票最后一天、`train_ready=0` 的行不进训练）。

### 2.2 特征集（与仓库对齐 + 你的扩展）

仓库默认 LightGBM 特征列表见 `tools/ml_train_toolkit.py` 的 `FEATURE_COLS`（已含 `log_total_mv`、`session_flag`、`board_type` 等，**不含** OHLC）。

若你在宽表里 **额外 JOIN** 了 `pe_ttm`、`pb`、`circ_mv`（你验证的 43 列场景），在入模前做你总结的清洗：

1. `pe_ttm`、`pb`：负值 → NaN；**仅在训练集行上**算 P1/P99 再 `clip`（验证/测试用训练集分位数，避免泄漏）。
2. `total_mv` / `circ_mv`：若表内已是 `log_total_mv` 则直接用；否则 `log(正值)`。
3. `session_flag`、`board_type`：整数映射或 LightGBM `categorical_feature`（你的 5 类分布已验证可用）。

**不要**把 `train_ready`、`symbol`、`trade_date` 当特征。

### 2.3 训练集 / 验证集 / 测试集划分（推荐）

金融时序 **禁止** `train_test_split(shuffle=True)`。

**推荐：按 `trade_date` 全局时间切分**（同一天所有股票进同一折）：

```
|←—— train 70% 日期 ——→|← val 15% →|← test 15% →|
```

- 实现：对所有 `trade_date` 排序，按时间分位取 `t_val`、`t_test`。
- `train`: `trade_date < t_val`
- `validation`: `t_val <= trade_date < t_test`
- `test`: `trade_date >= t_test`

可选 **embargo**（日频通常 0～1 个交易日即可）：若担心滚动特征在切分边界上的细微耦合，可把 `trade_date == t_val` 附近 1 天标为 `embargo` 丢弃。分钟线三分类里用「标签窗口跨边界」的 embargo 更严格，见 `assign_splits()`。

**不推荐**（初学阶段）：按股票随机切分 —— 同一时期市场状态会同时出现在 train/test，指标会偏乐观。

### 2.4 模型形式

- **两个回归器**（实践中最清晰）：
  - `model_high`: 预测 `y_high`
  - `model_low`: 预测 `y_low`
- LightGBM 参数起点（回归）：
  - `objective='regression'` 或 `'huber'`（对极端标签更稳）
  - `learning_rate=0.05`, `num_leaves=63`, `min_data_in_leaf=200`（日频样本大，叶节点样本数要大一些）
  - `feature_fraction=0.8`, `bagging_fraction=0.8`, `bagging_freq=1`
  - `verbosity=-1`, `seed=42`
- **早停**：`valid_sets=[dval]`, `early_stopping(stopping_rounds=50)`，监控 `l2` 或 `huber`。

类别列：`board_type`、`session_flag`、`industry_l1` 可传 `categorical_feature`（字符串列需先 `astype('category')`）。

### 2.5 评估指标（除了 RMSE）

| 指标 | 含义 |
|------|------|
| MAE / RMSE | 整体误差 |
| 分位 MAE | 按 `|y_high|` 分桶，看大波动日是否更差 |
| 方向命中率 | `sign(pred_y_high)` 与真实次日是否上冲（业务向） |
| 特征重要性 | `gain` 前 20，检查是否被 `session_flag`、市值 dominate |

**通过标准（学习阶段）**：验证集 RMSE 优于「永远预测训练集 y 中位数」的基线；测试集与验证集量级接近（不过拟合）。

### 2.6 取数 SQL（模板）

```sql
SELECT *
FROM ml_train_daily
WHERE train_ready = 1
  AND y_high IS NOT NULL
  AND y_low IS NOT NULL
  AND trade_date >= :start_date
  AND trade_date <= :end_date
ORDER BY trade_date, symbol;
```

大表建议 **按年或按月** 分批读入内存，或在 PostgreSQL 侧先 `COPY` 到 parquet 再训练。

### 2.7 训练前流水线（顺序）

1. `sanitize-ml-train-daily` → `action=audit`，确认 `y_high` 已是次日公式、非旧版日内振幅。
2. `train_ready=1` 筛选。
3. Python 侧特征清洗（PE/PB 等，分位数仅 fit train）。
4. 时间切分 → 训练双模型 → 保存 `data/lgbm_daily_yhigh.txt` / `ylow.txt`。
5. 在 **test 日期段** 上报 MAE 与基线对比。

参考实现：`scripts/train_ml_daily_lgbm.py`（可 `--dry-run` 看划分与行数）。

---

## 3. 5 分钟「日内转折点」：平台已封装（UI 对应项）

### 3.1 任务定义

- 5 分钟 bar，**horizon=3**（未来 15 分钟），障碍 = **0.5 × ATR(14)**。
- 标签三分类：`UP_FIRST` / `DOWN_FIRST` / `NEUTRAL`（`AMBIGUOUS` 丢弃）。
- 特征只用 **当前 bar 及历史**（`add_price_features` + 指数/板块上下文）。

### 3.2 数据门槛（代码内常量）

- 单票至少约 **2 个交易日、400 根 5 分钟 bar**；训练集 **≥300**  labeled 行；测试 **≥200** 行、**≥8** 个交易日（否则状态 `NEED_MORE_DATA`）。

### 3.3 划分方式（可直接学）

`assign_splits()`：**按 bar 时间戳** 排序后 70% / 15% / 15%；若标签结束时间 `label_end_time` 跨过 val/test 边界，该行标为 **`embargo`** 丢弃，防止「训练集样本的标签偷看验证段行情」。

### 3.4 怎么跑

1. 先用 `datahubco_data__fill_mainboard_mins` 补全分钟线。
2. 前端策略分析 → 日内高低点 → 训练，或：

```http
POST /strategy/intraday/train
Content-Type: application/json

{"ts_codes": ["603032.SH", "002727.SZ"], "config": {}}
```

3. 返回中含 `label_share`、`class_metrics`、模拟成交 `expectancy`（与 RSI/VWAP 基线对比）。

**注意**：第一版 **不允许** 在测试集上改 `horizon` / `barrier_mult` 再挑参（服务内写死校验）。

---

## 4. 常见坑（LightGBM + 金融）

1. **负 PE 入模** → 虚假单调性（你已处理 ✅）。
2. **在全量数据上算 quantile clip** → 测试泄漏；只在 train fit。
3. **shuffle 划分** → 同一交易日进 train 和 test。
4. **用旧版 y_high=(high-open)/open** → 学的是当日形态，不是次日（必须 sanitize）。
5. **分钟线任务用日频 PE** → 频率不一致；5 分钟模型不要混日频估值列，除非明确对齐到「前一交易日已知」。

---

## 5. 建议学习顺序（约 1～2 天）

1. 读 `ml_train_sanitize_toolkit.py` 顶部「为什么 LightGBM 会学歪」清单。
2. 跑通 `scripts/train_ml_daily_lgbm.py --dry-run`（看日期切分与特征列）。
3. 小规模 `--limit-days 120` 真训练，看 val/test RMSE 与基线。
4. 读 `intraday_turning_service.py` 的 `add_labels` + `assign_splits` + `leak_probe`。
5. 补 2～3 只股票分钟线，调 `POST /strategy/intraday/train`，对比日频回归 vs 分钟三分类的产品含义。

---

## 6. 一句话对照

- **你的清洗清单** → 服务 **日频次日 y_high/y_low 回归**。
- **UI「5 分钟 LightGBM」** → 服务 **盘中 15 分钟三分类转折点**，划分与训练已在 `intraday_turning_service.train()` 内完成。

两者都叫「高低点」，**标签定义不同**，训练脚本不可互换。
