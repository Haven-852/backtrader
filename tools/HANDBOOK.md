# 工具使用目录手册（Tools Handbook）

> 本手册是大模型对话与技能面板的**总目录**。  
> 当模型缺少行情/数据库等事实数据时，应按下方「选用规则」选择工具，再调用执行。

## 选用规则（给模型）

1. **先看本手册**，确认需要哪一类技能（skill），再选具体工具（tool）。
2. **有专用技能就直接调**，不要先 `list-tables` 探路。  
   - 测试某日高低能不能被 A/B/C/D **及其组合**打中 → 先 `t0_intraday__load_day`，再 `t0_intraday__hit_test`。结论看 `combo_ranking`。不要用 SQL 自己算高低来代替第二步。  
   - 盘中做T卖点 / 四法状态 → `method-*` 或 `combo-run`。  
   - 需要个股/指数/ETF/板块行情或分钟分时且可能缺库 → **datahubco-data**（`get-bars` / `get-mins`，默认 PG 优先，缺则补数入库）。  
   - 东财行业板块成分 / 剔除 ST·停牌 → `datahubco_data__get_sector_members` → `datahubco_data__filter_st_suspend`。  
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
| `get-mins` | 获取分钟分时 | **1 分钟主入口**：库优先，缺则 Datahubco/实时源→入库 | `ts_code`，可选 `trade_date`/`limit`/`source`/`write_back` |
| `get-sector-members` | 板块成分股 | 东财行业板块成分（dc_member）；标注 ST/停牌 | `ts_code`（如 BK1035.DC），可选 `trade_date`/`source` |
| `filter-st-suspend` | 剔除ST与停牌 | 从板块或股票列表剔除 ST/*ST 与停牌 | `ts_code` 或 `ts_codes`/`members` |
| `ensure-and-get` | 确保并获取 | 快捷 auto 补数（日线或 `dataset=stock_mins`） | `ts_code`，可选 `dataset`/`limit`/`trade_date` |
| `remote-query` | 直接调 API | 任意 Datahubco OpenAPI；分钟可用 `stk_mins`；成分 `dc_member` | `api`，可选 `params`/`write_dataset` |

**source 模式**：

- `auto`（默认）：PG 够用则不调远端；不足则拉取，默认 `write_back=true` 写入后再返回  
- `pg`：只读 PostgreSQL / Timescale  
- `datahubco`：强制远端；`write_back=false` 则只读不入库  

**dataset 常用别名**：`stock_daily`（daily）、`stock_weekly`、`stock_monthly`、`stock_mins`（mins/1min/intraday）、`index_daily`、`etf_daily`、`sector_dc_index`、`sector_dc_daily`

**板块成分流程**：`get-sector-members` → `filter-st-suspend`（或直接对 `filter-st-suspend` 传板块代码）。  
ST 按简称含 ST 判断；停牌优先 `ref_suspend_d`（类型 S），当日无有效日线则推断停牌。

函数名：`datahubco_data__get_bars`、`datahubco_data__get_mins`、`datahubco_data__get_sector_members`、`datahubco_data__filter_st_suspend` 等。

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
