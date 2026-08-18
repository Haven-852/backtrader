# PostgreSQL 市场数据 30 张表数据字典

**版本**：v1.0  
**来源**：`data_layer/storage/models.py`（与 `schema.py` DDL 一一对应）  
**存储**：PostgreSQL + TimescaleDB（时序表按 `trade_date` 分区）  

---

## 总览

| 分类 | 表数量 | 表名 |
|------|--------|------|
| 一、基础参照 | 6 | `ref_trade_cal, ref_stock_basic, ref_stock_namechange, ref_new_share, ref_stk_limit_daily, ref_suspend_d` |
| 二、财务报表 | 6 | `fin_income, fin_balancesheet, fin_cashflow, fin_fina_indicator, fin_forecast, fin_dividend` |
| 三、日/周/月 K 线 | 4 | `stock_daily, stock_weekly, stock_monthly, bars_daily` |
| 四、盘前 / 复权 / 竞价 | 3 | `stock_daily_basic, stock_adj_factor, stock_auction_daily` |
| 五、资金 / 两融 / 大宗 / 龙虎 | 5 | `stock_moneyflow_daily, stock_top_list_daily, margin_summary_daily, margin_detail_daily, stock_block_trade` |
| 六、指数 / 权重 | 2 | `index_daily, index_weight` |
| 七、基金 / ETF | 3 | `fund_daily, fund_nav, etf_daily` |
| 八、研报一致预期 | 1 | `broker_report_consensus` |

**说明**：
- 主键列在 ORM 中标注 `primary_key=True`，多为 `(ts_code, trade_date)` 或 `(ts_code, end_date, report_type)`。
- 财务宽表字段与 [Tushare Pro 财务接口](https://tushare.pro/document/2) 字段名一致。
- 行情类 `open/high/low/close/vol/amount` 与 Tushare `daily` 接口语义相同。

---

## 一、基础参照（6 张）

### `ref_trade_cal` — 交易日历

各交易所交易日历，判断某日是否开市、上一交易日。

| 列名 | 含义 |
|------|------|
| `exchange` | 交易所代码（SSE/SZSE/BJ） |
| `cal_date` | 日历日期 |
| `is_open` | 1=开市 0=休市 |
| `pretrade_date` | 上一个交易日 |

### `ref_stock_basic` — 证券主档

全市场股票主档：代码、名称、行业、上市状态等静态信息。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `symbol` | 交易代码（不含交易所后缀，如 000001） |
| `name` | 名称 |
| `area` | 地区 |
| `industry` | 所属行业 |
| `cnspell` | 拼音缩写（便于搜索） |
| `market` | 市场类型（主板/创业板/科创板等） |
| `list_date` | 上市日期 |
| `list_status` | 上市状态（L上市 D退市 P暂停） |
| `delist_date` | 退市日期 |
| `is_hs` | 是否沪深港通标的 |
| `act_name` | 公司全称 |
| `act_ent_type` | 企业类型 |

### `ref_stock_namechange` — 股票更名记录

股票历史更名记录及变更原因。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `name` | 名称 |
| `start_date` | 开始日期 |
| `end_date` | 结束日期 |
| `ann_date` | 公告日期 |
| `change_reason` | 变更/变动原因 |

### `ref_new_share` — 新股发行

新股发行（IPO）信息：申购代码、发行价、中签率等。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `sub_code` | 申购代码 |
| `name` | 名称 |
| `ipo_date` | 上网发行日期 |
| `issue_date` | 上市日期 |
| `amount` | 发行总量（万股） |
| `market_amount` | 上网发行量 |
| `price` | 发行价格 |
| `pe` | 发行市盈率 |
| `limit_amount` | 申购上限 |
| `funds` | 募集资金 |
| `ballot` | 中签率 |

### `ref_stk_limit_daily` — 涨跌停价（每日）

每日涨跌停价格，用于回测涨跌停约束。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `up_limit` | 涨停价 |
| `down_limit` | 跌停价 |

### `ref_suspend_d` — 停牌信息（每日）

每日停复牌信息及停牌原因。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `suspend_type` | 停牌类型 |
| `suspend_reason` | 停牌原因 |

---

## 二、财务报表（6 张）

### `fin_income` — 利润表

上市公司利润表（按报告期），收入、成本、利润、EPS 等。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `ann_date` | 公告日期 |
| `f_ann_date` | 实际公告日期 |
| `end_date` | 报告期 |
| `report_type` | 报表类型（合并/母公司等） |
| `comp_type` | 公司类型 |
| `end_type` | 报告期类型（1一季报/2半年报/3三季报/4年报） |
| `total_revenue` | 营业总收入 |
| `revenue` | 营业收入 |
| `int_income` | 利息收入 |
| `prem_earned` | 已赚保费 |
| `comm_income` | 手续费及佣金收入 |
| `n_commis_income` | 手续费及佣金净收入 |
| `n_oth_income` | 其他经营净收益 |
| `n_oth_b_income` | 加:其他业务净收益 |
| `prem_income` | 保险业务收入 |
| `out_prem` | 减:分出保费 |
| `une_prem_reser` | 提取未到期责任准备金 |
| `reins_income` | 其中:分保费收入 |
| `n_sec_tb_income` | 代理买卖证券业务净收入 |
| `n_sec_uw_income` | 证券承销业务净收入 |
| `n_asset_mg_income` | 受托客户资产管理业务净收入 |
| `oth_b_income` | 其他业务收入 |
| `fv_value_chg` | 加:公允价值变动净收益 |
| `invest_income` | 加:投资净收益 |
| `ass_invest_income` | 其中:对联营企业和合营企业的投资收益 |
| `forex_gain` | 加:汇兑净收益 |
| `total_cogs` | 营业总成本 |
| `oper_cost` | 减:营业成本 |
| `int_exp` | 减:利息支出 |
| `comm_exp` | 减:手续费及佣金支出 |
| `biz_tax_surchg` | 减:营业税金及附加 |
| `sell_exp` | 减:销售费用 |
| `admin_exp` | 减:管理费用 |
| `fin_exp` | 减:财务费用 |
| `assets_impair_loss` | 减:资产减值损失 |
| `oper_profit` | 营业利润 |
| `total_profit` | 利润总额 |
| `income_tax` | 所得税费用 |
| `n_income` | 净利润 |
| `n_income_attr_p` | 归属于母公司股东的净利润 |
| `minority_gain` | 少数股东损益 |
| `oth_compr_income` | 其他综合收益 |
| `t_compr_income` | 综合收益总额 |
| `compr_inc_attr_p` | 归属于母公司股东的综合收益总额 |
| `compr_inc_attr_m_s` | 归属于少数股东的综合收益总额 |
| `ebit` | 息税前利润 |
| `ebitda` | 息税折旧摊销前利润 |
| `basic_eps` | 基本每股收益 |
| `diluted_eps` | 稀释每股收益 |
| `insurance_exp` | 减:保险业务支出 |
| `undist_profit` | 年初未分配利润 |
| `distable_profit` | 可分配利润 |
| `update_flag` | 更新标识（1最新） |

### `fin_balancesheet` — 资产负债表

上市公司资产负债表，资产、负债、股东权益科目。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `ann_date` | 公告日期 |
| `f_ann_date` | 实际公告日期 |
| `end_date` | 报告期 |
| `report_type` | 报表类型（合并/母公司等） |
| `comp_type` | 公司类型 |
| `end_type` | 报告期类型（1一季报/2半年报/3三季报/4年报） |
| `total_assets` | 资产总计 |
| `total_cur_assets` | 流动资产合计 |
| `money_cap` | 货币资金 |
| `trad_asset` | 交易性金融资产 |
| `notes_receiv` | 应收票据 |
| `accounts_receiv` | 应收账款 |
| `oth_receiv` | 其他应收款 |
| `pre_payment` | 预付款项 |
| `div_receiv` | 应收股利 |
| `int_receiv` | 应收利息 |
| `inventories` | 存货 |
| `amor_exp` | 待摊费用 |
| `nca_within_1y` | 一年内到期的非流动资产 |
| `sett_rsrv` | 结算备付金 |
| `loanto_oth_bank_fi` | 拆出资金 |
| `premium_receiv` | 应收保费 |
| `reinsur_receiv` | 应收分保账款 |
| `reinsur_res_receiv` | 应收分保合同准备金 |
| `pur_resale_fa` | 买入返售金融资产 |
| `oth_cur_assets` | 其他流动资产 |
| `total_nca` | 非流动资产合计 |
| `fa_avail_for_sale` | 可供出售金融资产 |
| `htm_invest` | 持有至到期投资 |
| `lt_eqt_invest` | 长期股权投资 |
| `invest_real_estate` | 投资性房地产 |
| `time_deposits` | 定期存款 |
| `oth_assets` | 其他资产 |
| `lt_rec` | 长期应收款 |
| `fix_assets` | 固定资产 |
| `cip` | 在建工程 |
| `const_materials` | 工程物资 |
| `fixed_assets_disp` | 固定资产清理 |
| `produc_bio_assets` | 生产性生物资产 |
| `oil_and_gas_assets` | 油气资产 |
| `intan_assets` | 无形资产 |
| `r_and_d` | 研发支出 |
| `goodwill` | 商誉 |
| `lt_amor_exp` | 长期待摊费用 |
| `defer_tax_assets` | 递延所得税资产 |
| `decr_in_disbur` | 发放贷款及垫款 |
| `total_liab` | 负债合计 |
| `total_cur_liab` | 流动负债合计 |
| `st_borrow` | 短期借款 |
| `pay_borrow` | 向中央银行借款 |
| `trad_liab` | 交易性金融负债 |
| `notes_payable` | 应付票据 |
| `acct_payable` | 应付账款 |
| `adv_receipts` | 预收款项 |
| `int_payable` | 应付利息 |
| `div_payable` | 应付股利 |
| `oth_payable` | 其他应付款 |
| `acc_exp` | 预提费用 |
| `deferred_inc` | 递延收益 |
| `st_bonds_payable` | 应付短期债券 |
| `payable_to_reinsurer` | 应付分保账款 |
| `rsrv_insur_cont` | 保险合同准备金 |
| `acting_trading_sec` | 代理买卖证券款 |
| `acting_uw_sec` | 代理承销证券款 |
| `non_cur_liab_due_1y` | 一年内到期的非流动负债 |
| `oth_cur_liab` | 其他流动负债 |
| `total_ncl` | 非流动负债合计 |
| `lt_borrow` | 长期借款 |
| `bonds_payable` | 应付债券 |
| `lt_payable` | 长期应付款 |
| `specific_payables` | 专项应付款 |
| `estimated_liab` | 预计负债 |
| `defer_tax_liab` | 递延所得税负债 |
| `defer_inc_non_cur_liab` | 递延收益-非流动负债 |
| `oth_ncl` | 其他非流动负债 |
| `total_hldr_eqy_exc_min_int` | 股东权益合计(不含少数股东权益) |
| `minority_int` | 少数股东权益 |
| `total_hldr_eqy_inc_min_int` | 股东权益合计(含少数股东权益) |
| `cap_stk` | 股本 |
| `cap_rese` | 资本公积金 |
| `special_rese` | 专项储备 |
| `surplus_rese` | 盈余公积金 |
| `retained_earnings` | 未分配利润 |
| `prov_nom_risks` | 一般风险准备 |
| `oth_eqt_tools` | 其他权益工具 |
| `oth_eqt_tools_p_shr` | 其他权益工具(优先股) |
| `oth_compreh_income` | 其他综合收益 |
| `update_flag` | 更新标识（1最新） |

### `fin_cashflow` — 现金流量表

上市公司现金流量表，经营/投资/筹资活动现金流。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `ann_date` | 公告日期 |
| `f_ann_date` | 实际公告日期 |
| `end_date` | 报告期 |
| `report_type` | 报表类型（合并/母公司等） |
| `comp_type` | 公司类型 |
| `end_type` | 报告期类型（1一季报/2半年报/3三季报/4年报） |
| `net_profit` | 净利润 |
| `finan_exp` | 财务费用 |
| `c_fr_sale_sg` | 销售商品、提供劳务收到的现金 |
| `recp_tax_rends` | 收到的税费返还 |
| `n_depos_incr_fi` | 客户存款和同业存放款项净增加额 |
| `n_incr_loans_cb` | 向中央银行借款净增加额 |
| `n_inc_loans_oth_bank` | 向其他金融机构拆入资金净增加额 |
| `prem_fr_orig_contr` | 收到原保险合同保费取得的现金 |
| `n_incr_insured_dep` | 保户储金净增加额 |
| `n_reinsur_prem` | 收到再保业务现金净额 |
| `n_incr_disp_tfa` | 处置交易性金融资产净增加额 |
| `ifc_cashincr` | 收取利息和手续费净增加额 |
| `n_incr_disp_faas` | 处置可供出售金融资产净增加额 |
| `n_incr_loans_oth_bank_f` | 拆入资金净增加额 |
| `n_cap_incr_repur` | 回购业务资金净增加额 |
| `c_fr_oth_operate_a` | 收到其他与经营活动有关的现金 |
| `c_inf_fr_operate_a` | 经营活动现金流入小计 |
| `c_paid_goods_s` | 购买商品、接受劳务支付的现金 |
| `c_paid_to_for_empl` | 支付给职工以及为职工支付的现金 |
| `c_paid_for_taxes` | 支付的各项税费 |
| `n_incr_clt_loan_adv` | 客户贷款及垫款净增加额 |
| `n_incr_loans_cb_clt` | 存放央行和同业款项净增加额 |
| `c_pay_claims_orig_inco` | 支付原保险合同赔付款项的现金 |
| `pay_handling_chrg` | 支付手续费的现金 |
| `pay_comm_insur_plcy` | 支付保单红利的现金 |
| `oth_cash_pay_oper_act` | 支付其他与经营活动有关的现金 |
| `st_cash_out_act` | 经营活动现金流出小计 |
| `n_cashflow_act` | 经营活动产生的现金流量净额 |
| `c_recp_return_invest` | 收回投资收到的现金 |
| `c_recp_income_invest` | 取得投资收益收到的现金 |
| `c_recp_disp_fiolta` | 处置固定资产等收回的现金净额 |
| `c_recp_disp_sobu` | 处置子公司及其他营业单位收到的现金净额 |
| `c_inf_fr_invest_act` | 投资活动现金流入小计 |
| `c_paid_acq_const_fiolta` | 购建固定资产等支付的现金 |
| `c_paid_invest` | 投资支付的现金 |
| `c_paid_acq_sobu` | 取得子公司及其他营业单位支付的现金净额 |
| `c_inf_pay_invest_act` | 投资活动现金流出小计 |
| `oth_cash_recp_ral_inv_act` | 收到其他与投资活动有关的现金 |
| `oth_cash_pay_ral_inv_act` | 支付其他与投资活动有关的现金 |
| `n_cashflow_inv_act` | 投资活动产生的现金流量净额 |
| `c_recp_borrow` | 取得借款收到的现金 |
| `c_proc_issue_bonds` | 发行债券收到的现金 |
| `c_recp_cap_contrib` | 吸收投资收到的现金 |
| `c_incr_cash_pub` | 子公司吸收少数股东投资收到的现金 |
| `c_recp_cap_othcash` | 收到其他与筹资活动有关的现金 |
| `c_inf_fr_finance_act` | 筹资活动现金流入小计 |
| `c_prepay_amt_borr` | 偿还债务支付的现金 |
| `c_pay_dist_dpcp_int_exp` | 分配股利、利润或偿付利息支付的现金 |
| `incr_cash_cash_equ_dm` | 子公司支付给少数股东的股利、利润 |
| `c_inf_pay_finance_act` | 筹资活动现金流出小计 |
| `oth_cash_recp_ral_fnc_act` | 收到其他与筹资活动有关的现金 |
| `oth_cash_pay_ral_fnc_act` | 支付其他与筹资活动有关的现金 |
| `n_cashflow_fnc_act` | 筹资活动产生的现金流量净额 |
| `eff_fx_chg_cash` | 汇率变动对现金的影响 |
| `n_cash` | 现金及现金等价物净增加额 |
| `n_cash_equ_beg` | 期初现金及现金等价物余额 |
| `n_cash_equ_end` | 期末现金及现金等价物余额 |
| `c_conv_debt_into_eq` | 债务转为资本 |
| `c_use_fund_cash` | 现金的期末余额 |
| `free_cashflow` | 企业自由现金流量 |
| `update_flag` | 更新标识（1最新） |

### `fin_fina_indicator` — 财务指标

财务指标汇总：ROE、PE、PB、周转率、同比增长等。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `ann_date` | 公告日期 |
| `end_date` | 报告期 |
| `eps` | 每股收益 |
| `dt_eps` | 稀释每股收益 |
| `total_profit_eps` | 每股利润总额 |
| `oper_profit_eps` | 每股营业利润 |
| `basic_eps` | 基本每股收益 |
| `diluted_eps` | 稀释每股收益 |
| `roe` | 净资产收益率 |
| `roe_waa` | 加权平均净资产收益率 |
| `roe_dt` | 净资产收益率(扣除非经常损益) |
| `roa` | 总资产报酬率 |
| `npta` | 总资产净利润 |
| `roic` | 投入资本回报率 |
| `roe_yearly` | 年化净资产收益率 |
| `roa_yearly` | 年化总资产报酬率 |
| `roa2_yearly` | 年化总资产净利率 |
| `grossprofit_margin` | 销售毛利率 |
| `netprofit_margin` | 销售净利率 |
| `debt_to_assets` | 资产负债率 |
| `assets_turn` | 总资产周转率 |
| `inv_turn` | 存货周转率 |
| `ar_turn` | 应收账款周转率 |
| `or_yoy` | 营业收入同比增长率 |
| `op_yoy` | 营业利润同比增长率 |
| `ebt_yoy` | 利润总额同比增长率 |
| `netprofit_yoy` | 归属母公司净利润同比增长率 |
| `dt_netprofit_yoy` | 扣非净利润同比增长率 |
| `ocf_yoy` | 经营活动现金流同比增长率 |
| `current_ratio` | 流动比率 |
| `quick_ratio` | 速动比率 |
| `equity_ratio` | 产权比率 |
| `pe` | 市盈率 |
| `pe_ttm` | 市盈率（TTM） |
| `pb` | 市净率 |
| `ps` | 市销率 |
| `ps_ttm` | 市销率（TTM） |
| `dv_ratio` | 股息率（%） |
| `dv_ttm` | 股息率（TTM，%） |
| `total_share` | 总股本（万股） |
| `float_share` | 流通股本（万股） |
| `free_share` | 自由流通股本（万股） |
| `total_mv` | 总市值（万元） |
| `circ_mv` | 流通市值（万元） |
| `update_flag` | 更新标识（1最新） |

### `fin_forecast` — 业绩预告

业绩预告：预告类型、净利润变动区间、摘要与原因。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `ann_date` | 公告日期 |
| `end_date` | 报告期 |
| `type` | 预告类型（预增/预减/扭亏等） |
| `p_change_min` | 净利润变动下限（%） |
| `p_change_max` | 净利润变动上限（%） |
| `net_profit_min` | 预告净利润下限 |
| `net_profit_max` | 预告净利润上限 |
| `last_parent_net` | 上年同期归母净利润 |
| `first_ann_date` | 首次公告日 |
| `summary` | 业绩摘要 |
| `change_reason` | 变动原因 |

### `fin_dividend` — 分红送股

分红送转方案：每股分红、送股、除权除息日等。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `end_date` | 分红年度 |
| `ann_date` | 公告日期 |
| `div_proc` | 分红进度（预案/实施等） |
| `stk_div` | 每股送转 |
| `stk_bo_rate` | 每股送股比例 |
| `stk_so_rate` | 每股转增比例 |
| `cash_div` | 每股分红（税前） |
| `cash_div_tax` | 每股分红（税后） |
| `record_date` | 股权登记日 |
| `ex_date` | 除权除息日 |
| `pay_date` | 派息日 |
| `div_listdate` | 红股上市日 |
| `imp_ann_date` | 预案公告日 |
| `base_date` | 基准日 |
| `base_share` | 基准股本 |
| `update_flag` | 更新标识（1最新） |

---

## 三、日/周/月 K 线（4 张）

### `stock_daily` — 股票日K线

A股日K线（全市场按日存储），OHLCV 及涨跌幅。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价/当日收盘 |
| `pre_close` | 昨收价 |
| `change` | 涨跌额 |
| `pct_chg` | 涨跌幅（%） |
| `vol` | 成交量（手） |
| `amount` | 成交额（千元） |

### `stock_weekly` — 股票周K线

A股周K线。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价/当日收盘 |
| `pre_close` | 昨收价 |
| `change` | 涨跌额 |
| `pct_chg` | 涨跌幅（%） |
| `vol` | 成交量（手） |
| `amount` | 成交额（千元） |

### `stock_monthly` — 股票月K线

A股月K线。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价/当日收盘 |
| `pre_close` | 昨收价 |
| `change` | 涨跌额 |
| `pct_chg` | 涨跌幅（%） |
| `vol` | 成交量（手） |
| `amount` | 成交额（千元） |

### `bars_daily` — 通用日线（通用 symbol + time 主键，用于策略回测输出）

通用日线表（symbol+time），多用于策略回测输出或内部聚合。

| 列名 | 含义 |
|------|------|
| `time` | 时间戳 |
| `symbol` | 标的代码 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价/当日收盘 |
| `volume` | 成交量 |
| `amount` | 成交额 |
| `turnover` | 换手率 |

---

## 四、盘前 / 复权 / 竞价（3 张）

### `stock_daily_basic` — 盘前股本/每日指标

每日估值与股本指标：PE/PB/换手率/市值等（Tushare daily_basic）。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `close` | 收盘价/当日收盘 |
| `turnover_rate` | 换手率（%） |
| `turnover_rate_f` | 换手率（自由流通股本，%） |
| `volume_ratio` | 量比 |
| `pe` | 市盈率 |
| `pe_ttm` | 市盈率（TTM） |
| `pb` | 市净率 |
| `ps` | 市销率 |
| `ps_ttm` | 市销率（TTM） |
| `dv_ratio` | 股息率（%） |
| `dv_ttm` | 股息率（TTM，%） |
| `total_share` | 总股本（万股） |
| `float_share` | 流通股本（万股） |
| `free_share` | 自由流通股本（万股） |
| `total_mv` | 总市值（万元） |
| `circ_mv` | 流通市值（万元） |

### `stock_adj_factor` — 复权因子

每日复权因子，用于计算前复权/后复权价格。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `adj_factor` | 复权因子（用于前/后复权） |

### `stock_auction_daily` — 集合竞价日聚合

集合竞价日汇总：开盘价、竞价量、买卖盘量、跳空幅度。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `open_price` | 集合竞价开盘价 |
| `preclose_price` | 集合竞价前收盘价 |
| `auction_vol` | 集合竞价成交量 |
| `auction_amount` | 集合竞价成交额 |
| `bid_vol_total` | 竞价买盘总量 |
| `ask_vol_total` | 竞价卖盘总量 |
| `gap_pct` | 竞价跳空幅度（%） |

---

## 五、资金 / 两融 / 大宗 / 龙虎（5 张）

### `stock_moneyflow_daily` — 资金流向日数据

个股资金流向：小/中/大/特大单买卖及净流入。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `buy_sm_vol` | 小单买入量（手） |
| `buy_sm_amount` | 小单买入额（万元） |
| `sell_sm_vol` | 小单卖出量 |
| `sell_sm_amount` | 小单卖出额 |
| `buy_md_vol` | 中单买入量 |
| `buy_md_amount` | 中单买入额 |
| `sell_md_vol` | 中单卖出量 |
| `sell_md_amount` | 中单卖出额 |
| `buy_lg_vol` | 大单买入量 |
| `buy_lg_amount` | 大单买入额 |
| `sell_lg_vol` | 大单卖出量 |
| `sell_lg_amount` | 大单卖出额 |
| `buy_elg_vol` | 特大单买入量 |
| `buy_elg_amount` | 特大单买入额 |
| `sell_elg_vol` | 特大单卖出量 |
| `sell_elg_amount` | 特大单卖出额 |
| `net_mf_vol` | 净流入量（手） |
| `net_mf_amount` | 净流入额（万元） |

### `stock_top_list_daily` — 龙虎榜日数据

龙虎榜：上榜原因、买卖席位金额、净买入等。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `name` | 名称 |
| `close` | 收盘价/当日收盘 |
| `pct_change` | 涨跌幅（%） |
| `turnover_rate` | 换手率（%） |
| `amount` | 成交额（千元） |
| `l_sell` | 龙虎榜卖出额 |
| `l_buy` | 龙虎榜买入额 |
| `l_amount` | 龙虎榜成交额 |
| `net_amount` | 龙虎榜净买入额 |
| `net_rate` | 龙虎榜净买额占比 |
| `amount_rate` | 龙虎榜成交额占比 |
| `float_values` | 流通市值 |
| `reason` | 上榜原因 |

### `margin_summary_daily` — 两融汇总日数据（按交易所）

融资融券市场汇总（按交易所 SSE/SZSE）。

| 列名 | 含义 |
|------|------|
| `trade_date` | 交易日期 |
| `exchange_id` | 交易所（SSE/SZSE） |
| `rzye` | 融资余额 |
| `rzmre` | 融资买入额 |
| `rzche` | 融资偿还额 |
| `rqye` | 融券余额 |
| `rqmcl` | 融券卖出量 |
| `rzrqye` | 融资融券余额 |

### `margin_detail_daily` — 两融明细日数据（按股票）

个股融资融券明细：融资余额、融券余量等。

| 列名 | 含义 |
|------|------|
| `trade_date` | 交易日期 |
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `name` | 名称 |
| `rzye` | 融资余额 |
| `rqye` | 融券余额 |
| `rzmre` | 融资买入额 |
| `rqyl` | 融券余量 |
| `rzche` | 融资偿还额 |
| `rqchl` | 融券偿还量 |
| `rqmcl` | 融券卖出量 |
| `rzrqye` | 融资融券余额 |

### `stock_block_trade` — 大宗交易

大宗交易：成交价、成交量、买卖方。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `price` | 成交价格 |
| `vol` | 成交量（万股） |
| `amount` | 成交金额（万元） |
| `buyer` | 买方营业部/机构 |
| `seller` | 卖方营业部/机构 |

---

## 六、指数 / 权重（2 张）

### `index_daily` — 指数日线行情

指数日行情（如沪深300、创业板指等）。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价/当日收盘 |
| `pre_close` | 昨收价 |
| `change` | 涨跌额 |
| `pct_chg` | 涨跌幅（%） |
| `vol` | 成交量（手） |
| `amount` | 成交额（千元） |

### `index_weight` — 指数成分权重

指数成分股权重（用于指数复制与归因）。

| 列名 | 含义 |
|------|------|
| `index_code` | 指数代码 |
| `con_code` | 成分股代码 |
| `trade_date` | 交易日期 |
| `weight` | 成分权重（%） |

---

## 七、基金 / ETF（3 张）

### `fund_daily` — 基金日行情

公募基金/场内基金日行情。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价/当日收盘 |
| `pre_close` | 昨收价 |
| `change` | 涨跌额 |
| `pct_chg` | 涨跌幅（%） |
| `vol` | 成交量（手） |
| `amount` | 成交额（千元） |

### `fund_nav` — 基金净值

基金单位净值、累计净值、复权净值。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `nav_date` | 净值日期 |
| `unit_nav` | 单位净值 |
| `accum_nav` | 累计净值 |
| `adj_nav` | 复权单位净值 |

### `etf_daily` — ETF日行情

ETF 日行情。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `trade_date` | 交易日期 |
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价/当日收盘 |
| `pre_close` | 昨收价 |
| `change` | 涨跌额 |
| `pct_chg` | 涨跌幅（%） |
| `vol` | 成交量（手） |
| `amount` | 成交额（千元） |

---

## 八、研报一致预期（1 张）

### `broker_report_consensus` — 券商一致预期

券商研报一致预期：评级分布、目标价、上涨空间。

| 列名 | 含义 |
|------|------|
| `ts_code` | 证券代码（Tushare 标准，如 000001.SZ） |
| `calc_date` | 计算日期 |
| `buy_count` | 买入评级家数 |
| `overweight_count` | 增持评级家数 |
| `hold_count` | 中性评级家数 |
| `underweight_count` | 减持评级家数 |
| `sell_count` | 卖出评级家数 |
| `total_count` | 评级总家数 |
| `consensus_score` | 一致预期评分 |
| `avg_target` | 一致预期目标价 |
| `avg_upside` | 一致预期上涨空间（%） |

---
