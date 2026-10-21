# 工具使用目录手册（Tools Handbook）

> 本手册是大模型对话与技能面板的**总目录**。  
> 当模型缺少行情/数据库等事实数据时，应按下方「选用规则」选择工具，再调用执行。

## 选用规则（给模型）

1. **先看本手册**，确认需要哪一类技能（skill），再选具体工具（tool）。
2. **有专用技能就直接调**，不要先 `list-tables` 探路。  
   - 测试某日高低能不能被 A/B/C/D **及其组合**打中 → 先 `t0_intraday__load_day`，再 `t0_intraday__hit_test`。结论看 `combo_ranking`。不要用 SQL 自己算高低来代替第二步。  
   - 盘中做T卖点 / 四法状态 → `method-*` 或 `combo-run`。  
   - 纸面账户盘中盈亏 → `paper_trade__get_intraday_quotes` 或 `get_account`/`mark`（1 分钟分时末价）。没有分时就报缺，不要用日线收盘/成本价估算。  
   - 需要个股/指数/ETF/板块行情或分钟分时且可能缺库 → **datahubco-data**（`get-bars` / `get-mins`；分时先 Redis 再 PG，缺则补数入库）。  
   - **批量拉取今日全部主板 1 分钟分时（LightGBM 训练）** → `datahubco_data__fill_mainboard_mins`：先 `action=status`，再 `action=fill`（用 `limit`/`offset` 分批），最后 `action=index` 取 codes；单票 Redis key=`intraday:{ts_code}:{YYYYMMDD}`。不要逐只 `get-mins` 代替全市场，不要编造分时。  
   - **全市场股票清单** → 先 `datahubco_data__fill_market_universe` `action=fill`，写入 `ref_market_universe`（约 5575 只，含 board_type），并同步 `ref_stock_basic`。  
   - **写入训练表 ml_train_daily** → `datahubco_data__fill_ml_train_daily`：先 `check_env`，再 `fill_complete`（单票）或 `fill_batch`+`limit`/`offset`（从 `ref_market_universe` 读池）。采集遵守 P01–P11。训练只用 `train_ready=1`。不要一次拉全市场进内存。  
   - **用户自定义表** → 存储层「AI 建表」或 `sql_toolkit__create_custom_table`；表名 `u_*`，建好后 `list-tables` / `query-exec` 可用。存储层预览先 50 行，滚动继续加载。  
   - **训练表体检与修复** → `datahubco_data__sanitize_ml_train_daily`：先 `catalog` 看问题与办法，再 `audit` 看数量，最后 `fix`。会把 y_high/y_low 改成次日高低/当日收盘，补截面，标记一字/停牌，插入停牌占位行使序列连续。训练只用 `train_ready=1`。  
   - **期货主连报价 / 行情中心**（黄金原油橡胶股指等）→ **futures-market**（`get-quotes` / `ensure-and-get` / `sync-quotes`）。缺数或日期不对先补数入库，不要编造价格。  
   - 某一天整批东财二级/一级/三级行业行情缺失 → `datahubco_data__fill_sector_gap`（`trade_date`，`level` 默认东财二级行业）。不要逐只用 `get-bars` 代替，也不要编造涨跌幅。
   - 某一个东财板块缺少一段历史行情、上涨家数或回测用的市场环境 → `datahubco_data__fill_sector_history`（`ts_code`、`start_date`、`end_date`）。只调用这一次，不要逐日补，也不要编造涨跌幅。
   - 要在全部东财二级行业上验证规则，而多数板块缺少整段行情 → `datahubco_data__fill_market_history`（`start_date`、`end_date`）。只调用这一次。已有较长历史的板块不要覆盖，名称对不上的不要编造。  
   - 某个板块的成分股缺失 → `datahubco_data__get_sector_members`（`ts_code` 如 BK1277.DC，`write_back=true`）。成分股的日线缺失 → 对缺少的代码调用 `datahubco_data__get_bars`（`dataset=stock_daily`）。系统发现缺口时会点名工具，只调用被点名的工具。
   - 某个交易日整批个股资金流向缺失 → `datahubco_data__get_fund_data`（`dataset=moneyflow`，`trade_date` 用 8 位日期，`source=datahubco`，`write_back=true`）。不要传 `ts_code`。不要编造主力净额。  
   - 东财行业板块成分 / 剔除 ST·停牌 → `datahubco_data__get_sector_members` → `datahubco_data__filter_st_suspend`。  
   - **今日市场记忆草稿** → `kb_market__today`（技能名：知识库-市场记忆-今日）。只生成草稿，不要声称已写入知识库。  
   - **盘前 09:20–09:25 主板竞价强买** → `auction_screen__mainboard_buy`（昨日强势∪涨停池，成交额 P95）。不要编造量额。  
   - **东财二级行业分时情绪** → `sector_intraday__l2_tone`（先日线 Top，再合成成分分时）。不要编造涨跌幅。  
   - **A 股收盘复盘** → `skill-market-daily-review`（先 `load-skill` / `build-prompt`）。  
   - **A 股个股尽调档案** → `ill-a-share-stock-dossier`。  
   - **期货 DeepView 研判** → `skill-futures-deepview-analyst`。  
   - 代码写成 `600118` 时补全为 `600118.SH`。
3. **不要臆造**表名、字段、涨跌幅、成交量。只有「没有专用技能、且不知道表结构」时，才用 `list-tables` / `info-table`。
4. 临时自定义查询（财务、资金流、日线筛选等）→ **sql-toolkit**。仅允许只读（SELECT / WITH / EXPLAIN / SHOW）。
5. 一次只调真正需要的 1～2 个工具，拿到结果后必须用中文给出结论，不要继续无意义查库。
6. 工具名在 API 中的 function name 格式：`{skill_id}__{tool_id}`，其中 `-` 替换为 `_`。  
   例：`t0-intraday` + `combo-run` → `t0_intraday__combo_run`；`datahubco-data` + `get-bars` → `datahubco_data__get_bars`

---

## 技能：sql-toolkit（SQL 数据库工具箱）

**何时用**：需要查库内股票/指数/ETF/财务/资金流等结构化数据；需要确认有哪些表、字段。

| 工具 ID | 中文名 | 用途 | 主要参数 |
|---------|--------|------|----------|
| `list-tables` | 列出所有表 | 不知道有哪些表时先调用 | 无 |
| `info-table` | 查看表信息 | 确认某表字段/类型 | `table_names`（逗号分隔） |
| `list-custom-tables` | 自定义表目录 | 用户 AI 建表产物（`u_*`） | 无 |
| `create-custom-table` | AI 建表 | 自然语言描述 → 大模型设计并创建 PG 表 | `prompt`，可选 `dry_run` |
| `delete-custom-table` | 删除自定义表 | **仅**可删自己建的 `u_*`；系统表一律拒绝 | `table_name` |
| `query-check` | SQL 校验 | 执行前检查语法与只读安全 | `query` |
| `query-exec` | 执行 SQL | 执行 SELECT 并返回结果 | `query`，可选 `limit` |
| `query-nl` | 自然语言查询 | 用中文描述需求（依赖较强） | `question` |

**常用表提示**（以实际 `list-tables` 为准）：

- `stock_daily` / `stock_weekly` / `stock_monthly` — 个股 K 线  
- `index_daily` — 指数日线  
- `etf_daily` — ETF 日线  
- `ref_stock_basic` — 股票主档（名称、行业）  
- `stock_moneyflow_daily` — 资金流向  

**推荐流程**：`list-tables` → `info-table` → `query-exec`

---

## 技能：datahubco-data（Datahubco 行情补数）

**何时用**：需要个股/指数/ETF/板块行情；库可能缺数或想强制从 Datahubco 读。不要先 `list-tables`。

| 工具 ID | 中文名 | 用途 | 主要参数 |
|---------|--------|------|----------|
| `list-datasets` | 列出数据集 | 看支持的 dataset / 表 / API | 无 |
| `coverage` | 覆盖检查 | 只查 PG 是否已有数据（不拉远端） | `ts_code`，可选 `dataset` / 日期；分钟用 `trade_date` |
| `get-bars` | 获取行情 | 日线/周线/月线/板块；也可 `dataset=stock_mins` | `ts_code`，可选 `dataset`/`limit`/`source`/`write_back` |
| `fill-sector-gap` | 补板块缺日 | 把指定交易日的东财行业板块涨跌和成交额写入库 | `trade_date`，可选 `level`（默认东财二级行业） |
| `get-mins` | 获取分钟分时 | **1 分钟主入口**：Redis → PG → Datahubco 补数写回 | `ts_code`，可选 `trade_date`/`limit`/`source`/`write_back` |
| `fill-mainboard-mins` | 批量主板分时 | 当日全部主板 1 分钟写入 Redis+PG，供 LightGBM 训练 | `action=fill\|status\|index`，可选 `limit`/`offset`/`trade_date` |
| `fill-market-universe` | 全市场股票清单 | Datahubco `stock_basic` 分页写入 `ref_market_universe`（含 board_type）并同步 `ref_stock_basic`；供 fill_batch 读池 | `action=fill\|status\|ensure_schema` |
| `fill-ml-train-daily` | 训练表采集 | 经 Datahubco 补日线→算特征→写 `ml_train_daily`；P01–P11；fill_batch 读 `ref_market_universe` | `action=check_env\|fill_complete\|fill_one\|fill_batch\|local_polish\|check_null\|status` |
| `sanitize-ml-train-daily` | 训练表体检修复 | 检查并修复会搞坏 LightGBM 的问题：次日标签、截面、一字/停牌标记、停牌占位、train_ready | `action=catalog\|audit\|fix` |
| `get-sector-members` | 板块成分股 | 东财行业板块成分（dc_member）；标注 ST/停牌 | `ts_code`（如 BK1035.DC），可选 `trade_date`/`source` |
| `filter-st-suspend` | 剔除ST与停牌 | 从板块或股票列表剔除 ST/*ST 与停牌 | `ts_code` 或 `ts_codes`/`members` |
| `get-fund-data` | 资金与基本面 | 资金/财务/指数/基金；限售解禁/质押/增减持/筹码/研报；同花顺概念成分/可转债/期货主档；每日指标等 → 写入 PG | `dataset`，可选 `ts_code`/`trade_date`/`period` |
| `get-stock-sector-moneyflow` | 个股板块资金 | 个股所属二级/三级行业板块的资金走向 | `ts_code`，可选 `trade_date`/`levels` |
| `ensure-and-get` | 确保并获取 | 快捷 auto 补数（日线或 `dataset=stock_mins`） | `ts_code`，可选 `dataset`/`limit`/`trade_date` |
| `remote-query` | 直接调 API | 任意 Datahubco OpenAPI；分钟可用 `stk_mins`；成分 `dc_member` | `api`，可选 `params`/`write_dataset` |

**source 模式**：

- `auto`（默认）：PG 够用则不调远端；不足则拉取，默认 `write_back=true` 写入后再返回  
- `pg`：只读 PostgreSQL / Timescale  
- `datahubco`：强制远端；`write_back=false` 则只读不入库  

**dataset 常用别名**：`stock_daily`（daily）、`stock_weekly`、`stock_monthly`、`stock_mins`（mins/1min/intraday）、`index_daily`、`etf_daily`、`sector_dc_index`、`sector_dc_daily`

**板块成分流程**：`get-sector-members` → `filter-st-suspend`（或直接对 `filter-st-suspend` 传板块代码）。  
ST 按简称含 ST 判断；停牌优先 `ref_suspend_d`（类型 S），当日无有效日线则推断停牌。

**资金/基本面 dataset**：`top_list`、`top_inst`、`block_trade`、`margin`、`margin_detail`、`moneyflow`、`hk_hold`、`income`、`balancesheet`、`cashflow`、`fina_indicator`、`fina_mainbz`、`fina_audit`、`dividend`、`express`、`disclosure_date`、`forecast`、`top10_holders`、`sector_moneyflow`、`index_basic`、`index_weekly`、`index_monthly`、`index_dailybasic`、`index_classify`、`index_weight`、`fund_basic`、`fund_nav`、`fund_div`、`fund_portfolio`、`fund_adj`、`share_float`、`pledge_stat`、`stk_holdertrade`、`cyq_perf`、`cyq_chips`、`report_rc`、`ths_member`、`cb_basic`、`cb_daily`、`fut_basic`、`daily_basic`、`adj_factor`、`stk_limit`、`limit_list`、`suspend_d`、`new_share`。  
个股二级/三级板块资金用 `get-stock-sector-moneyflow`（依赖 `sector_dc_member` + `sector_moneyflow_daily`）。

函数名：`datahubco_data__get_bars`、`datahubco_data__get_mins`、`datahubco_data__fill_mainboard_mins`、`datahubco_data__get_fund_data`、`datahubco_data__get_stock_sector_moneyflow` 等。

**批量主板分时（训练）**：`fill-mainboard-mins`

1. `action=status` → 看 `universe_total` / `local_ready` / `coverage_pct`
2. `action=fill` → 建议 `limit=200`，按返回的 `next_offset` 循环直到 `has_more=false`
3. `action=index` → 取 `codes`；Redis 索引 key=`intraday:mainboard:{YYYYMMDD}:codes`；单票 bars=`intraday:{ts_code}:{YYYYMMDD}`（含 OHLCV/amount）
4. 也可从 PG `stock_intraday_mins` 按 `trade_date` 批量读

**全市场股票清单**：`fill-market-universe` → 表 `ref_market_universe`

1. `action=fill` → 分页拉 Datahubco `stock_basic`（list_status=L，单页 5000），写入清单并同步 `ref_stock_basic`
2. `action=status` → 看 `listed_rows` / `by_board_type` / `ready_for_fill_batch`
3. 完成后才能用 `fill_batch`（池子从此表读，不再依赖空的主档）

**训练表采集**：`fill-ml-train-daily` → 表 `ml_train_daily`

1. `action=check_env` → 内存≥1.5GB、磁盘≥10GB、PG/训练表就绪
2. `action=fill_complete` `ts_code=000001.SZ` `start_date=2016-01-04` → fill_one + **表内** polish（截面/指数广播/train_ready），不依赖远端全市场 daily
3. `action=fill_one` → 单票特征：次日标签、OHLC 夹紧、一字/停牌标记、停牌占位行、滚动特征不填 0、写 `train_ready`
4. `action=fill_batch` `limit=1~1000` + `offset` 循环（股票池=`ref_market_universe`）；每批成功后自动 `local_polish`
5. `action=local_polish` → 仅表内重算截面/广播/train_ready（全市场采完可再跑一次）
6. `board_type`：主板 / 科创板 / 创业板 / 北交所 / ST
7. **标签**：`y_high`/`y_low` = 次日高低 / 当日收盘 - 1；末日留空且 `train_ready=0`
8. **训练过滤**：`WHERE train_ready = 1`（前 60 根、无标签、薄日、停牌等为 0）
9. 旧表仍可用 `sanitize-ml-train-daily`：`catalog` → `audit` → `fix` 做事后修复

**训练表体检修复**：`sanitize-ml-train-daily` → 表 `ml_train_daily`

1. `action=catalog` → 问题清单与对应修复
2. `action=audit` → 全表计数（截面空值、一字板、标签是否仍是当日公式）
3. `action=fix` → 本地按序修复（改标签、补截面、标一字/停牌、插停牌占位行、打 train_ready）
4. 单步：`fix_labels` / `fill_cross_section` / `mark_session` / `fill_gaps` / `mark_train_ready`
5. `session_flag`∈正常 / 一字涨停 / 一字跌停 / 平盘一字 / 停牌，作为 LightGBM 特征，不要丢掉这些行

---

## 技能：t0-intraday（日内做T策略）

**何时用**：判断某只股票盘中暂时高低、做 T 卖点；或对比无盘口四法。不要猜盘口，不要编造分时。

数据：`stock_intraday_mins` 1 分钟分时。没有分时就报缺数，不要捏造。盘中用 `as_of` 截断，禁止用当日收盘后最高最低当特征。

| 工具 ID | 中文名 | 用途 | 主要参数 |
|---------|--------|------|----------|
| `load-day` | 加载当日全部分时 | **第一步**：从库取当天全部 1 分钟价格和成交量 | `ts_codes`,`trade_date` |
| `hit-test` | 四法组合命中高低测试 | **第二步**：跑 A/B/C/D 再组合成买卖点，对照真实高低打分 | 同上，可选 `hit_tol_pct`、`combos=all` |
| `method-a` | 方法A 回撤+剩余时间 | 只开 A | `ts_codes`，可选 `trade_date` / `as_of` |
| `method-b` | 方法B 冲击衰竭 | 只开 B；`use_clock=true` 为 B+C | 同上 + `use_clock` |
| `method-c` | 方法C U型量时钟 | 同时点放量倍数，不单独下单 | 同上 |
| `method-d` | 方法D 峰岭谷 | 孤立放量 vs 连续放量 | 同上 |
| `combo-run` | 组合对比 | 同一纪律只换信号看成交均价 | `combo`=`a`/`b`/`b_c`/`d_c`/`a_floor_b`/`a_floor_d`/`all` |
| `market-scan` | 全市场分态势扫描 | 近30日日线分上升/下跌/横盘，再测当日组合 | `trade_date`，可选 `collect` |

**测高低命中请：`load-day` → `hit-test`。** 全市场分层用 `market-scan`。不要用 sql-toolkit 代替。  
hit-test 默认测全部组合。结论看 `combo_ranking`；写死到分时图看 `mark_high` / `mark_low`。C 不单独下单。
态势分类只用测试日之前的日线。信号只用截至该分钟的价量。

成交纪律（combo-run）：信号分钟收盘价成交；14:55 强制清完。

函数名：`t0_intraday__method_a` 等。

---

## 技能：futures-market（期货行情中心）

**何时用**：查询行情中心期货主连价格（黄金/原油/橡胶/股指等），检查缺失或日期落后，补数入库，查看日/周/月线。

| 工具 ID | 用途 |
|---------|------|
| `list-catalog` | 分类与合约目录 |
| `get-board` / `get-quotes` | 读看板/报价（默认对齐上一交易日） |
| `check-coverage` | 检查缺失或过旧 |
| `sync-quotes` / `ensure-and-get` | 从 Datahubco 补日线写入 `fut_daily` 并返回报价 |
| `get-kline` | 日/周/月 K 线 |

缺数或日期不对时优先 `ensure-and-get`，不要编造价格。A股指数仍看「行情看板」。  
函数名：`futures_market__get_quotes`、`futures_market__sync_quotes`、`futures_market__ensure_and_get`。

---

## 技能：paper-trade（纸面模拟账户）

**何时用**：模拟账户持仓、下单、盘中盈亏。优先用 1 分钟分时末价；无分时则用上一交易日收盘价计入净值。

| 工具 ID | 用途 |
|---------|------|
| `get-account` / `list-positions` / `mark` | 分时盯市；无分时 → `quote_source=daily_close` 用收盘价计入市值 |
| `get-intraday-quotes` | 指定标的或账户持仓的分时末价 |
| `get-market-brief` | 日线感知（不是盘中价） |

账户净值不可因缺分时只剩现金；做 T 决策仍以分时为准，勿把收盘价当作实时盘中价。  
函数名：`paper_trade__get_intraday_quotes`、`paper_trade__get_account`、`paper_trade__mark`。

---

## 技能：auction-screen（主板集合竞价强买）

**何时用**：盘前看 09:20–09:25 谁在放量抬价；昨日强势/涨停股的竞价筛选。

| 工具 ID | 用途 |
|---------|------|
| `mainboard-buy` | 宇宙=昨日强势主板∪涨停池；竞价额≥P95 且价升 → 推荐 + 近3日 + 规则原因 |

函数名：`auction_screen__mainboard_buy`。强买为量能代理，不是 Level-2 买卖方向。  
若返回 `incomplete=true` / `suggested_tool_calls`：先按列表调 Datahubco 补竞价分钟（`get_mins`+`include_auction`）与涨停榜，再重跑本工具。

---

## 技能：sector-intraday（二级行业分时情绪）

**何时用**：看今日东财二级行业强弱、分时形态、市场情绪。

| 工具 ID | 用途 |
|---------|------|
| `l2-tone` | 日线按\|涨跌幅\|取 Top → 成分分时等权合成；输出近3日与情绪文案 |

函数名：`sector_intraday__l2_tone`。  
缺日线 → `fill_sector_gap`；缺成分 → `get_sector_members`；缺分时 → `get_mins`。工具会在 `suggested_tool_calls` 里写明，模型应先补数再重跑。

---

## 技能：skill-market-daily-review（市场每日复盘）

**何时用**：今日复盘、收盘总结、A股复盘、龙虎榜/北向资金复盘。源码：`tools/skill-market-daily-review`（QuantSkills）。

| 工具 ID | 用途 |
|---------|------|
| `load-skill` | 读取 SKILL.md 工作流 |
| `list-resources` / `read-resource` | 读模板与接口映射 |
| `build-prompt` | 按日期/关注点拼任务提示 |
| `validate-report` | 校验复盘 md 章节完整性 |

依赖上游 Pandadata；本平台可用 datahubco-data / sql-toolkit 取数并标注来源。函数名：`skill_market_daily_review__load_skill` 等。

---

## 技能：ill-a-share-stock-dossier（A股个股档案）

**何时用**：个股体检、尽调、基本面/质押解禁减持报告。源码：`tools/ill-a-share-stock-dossier`（上游 `skill-a-share-stock-dossier`）。

| 工具 ID | 用途 |
|---------|------|
| `load-skill` | 读取尽调流水线与风险规则 |
| `list-resources` / `read-resource` | 如 `references/dossier-guide.md` |
| `build-prompt` | `fields.symbol` 如 `600519.SH` |

函数名：`ill_a_share_stock_dossier__build_prompt` 等。

---

## 技能：skill-futures-deepview-analyst（期货 DeepView 分析）

**何时用**：席位博弈、基差/期限结构、仓单库存、跨期套利。源码：`tools/skill-futures-deepview-analyst`。

| 工具 ID | 用途 |
|---------|------|
| `load-skill` | 读取 DeepView 接口地图 |
| `list-resources` / `read-resource` | 如 `references/analysis-playbook.md` |
| `build-prompt` | `fields.variety` / `fields.focus` |

函数名：`skill_futures_deepview_analyst__load_skill` 等。

---

## 扩展说明

新增工具时请：

1. 在 `tools/` 下实现模块  
2. 在本手册增加章节  
3. 在 `tools/__init__.py` 的 `SKILL_REGISTRY` 注册  
4. 在 `routers/skill_router.py` / `tools/executor.py` 增加分发  

前端技能面板与对话 Agent 均通过 `/skills` 与本手册同步发现工具。
