# 工具使用目录手册（Tools Handbook）

> 本手册是大模型对话与技能面板的**总目录**。  
> 当模型缺少行情/数据库等事实数据时，应按下方「选用规则」选择工具，再调用执行。

## 选用规则（给模型）

1. **先看本手册**，确认需要哪一类技能（skill），再选具体工具（tool）。
2. **不要臆造**表名、字段、涨跌幅、成交量；不确定就先 `list-tables` / `info-table`。
3. 查询行情/历史 K 线等落在 PostgreSQL 中的数据 → 使用 **sql-toolkit**。
4. 仅允许只读查询（SELECT / WITH / EXPLAIN / SHOW），禁止写库。
5. 一次优先调用 1～3 个工具，拿到结果后再回答用户。
6. 工具名在 API 中的 function name 格式：`{skill_id}__{tool_id}`，其中 `-` 替换为 `_`。  
   例：`sql-toolkit` + `list-tables` → `sql_toolkit__list_tables`

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

## 扩展说明

新增工具时请：

1. 在 `tools/` 下实现模块  
2. 在本手册增加章节  
3. 在 `tools/__init__.py` 的 `SKILL_REGISTRY` 注册  
4. 在 `routers/skill_router.py` 增加分发  

前端技能面板与对话 Agent 均通过 `/skills` 与本手册同步发现工具。
