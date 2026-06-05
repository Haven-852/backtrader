"""
PostgreSQL 市场数据 ORM 模型 —— 全部 30 张表

来源: schema.py DDL 定义，与线上 PostgreSQL 表结构一一对应
兼容: SQLAlchemy 2.x + asyncpg

表分类:
    ├── 基础参照 (ref_*)       6 张
    ├── 财务报表 (fin_*)       6 张
    ├── 日/周/月 K线          4 张 (含 bars_daily)
    ├── 盘前/复权/竞价        3 张
    ├── 资金/两融/大宗/龙虎   5 张
    ├── 指数/权重             2 张
    ├── 基金/ETF              3 张
    └── 研报一致预期          1 张
"""

from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


# ─────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────

def _fmt(val: Any) -> Any:
    """统一格式化：date → iso 字符串，保证 JSON 可序列化"""
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return val


def _row_to_dict(row, columns: list[str]) -> dict[str, Any]:
    """将 ORM 实例转为 dict"""
    return {c: _fmt(getattr(row, c, None)) for c in columns}


# ═════════════════════════════════════════════════════════════════════
# 一、基础参照数据 (6 张)
# ═════════════════════════════════════════════════════════════════════

class RefTradeCal(Base):
    """交易日历"""
    __tablename__ = "ref_trade_cal"

    exchange = Column(String(10), primary_key=True, nullable=False, comment="交易所 SSE/SZSE/BJ")
    cal_date = Column(Date, primary_key=True, nullable=False, comment="日历日期")
    is_open = Column(SmallInteger, nullable=False, comment="1=开市 0=休市")
    pretrade_date = Column(Date, comment="上一个交易日")

    _cols = ["exchange", "cal_date", "is_open", "pretrade_date"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class RefStockBasic(Base):
    """证券主档"""
    __tablename__ = "ref_stock_basic"

    ts_code = Column(String(16), primary_key=True, comment="股票代码")
    symbol = Column(String(10), nullable=False, comment="交易代码")
    name = Column(String(50), comment="股票名称")
    area = Column(String(20), comment="地区")
    industry = Column(String(50), comment="行业")
    cnspell = Column(String(30), comment="拼音缩写")
    market = Column(String(10), comment="市场类型")
    list_date = Column(Date, comment="上市日期")
    list_status = Column(String(1), comment="上市状态 L/D/P")
    delist_date = Column(Date, comment="退市日期")
    is_hs = Column(String(1), comment="是否沪深港通")
    act_name = Column(String(200), comment="公司全称")
    act_ent_type = Column(String(50), comment="企业类型")

    _cols = [
        "ts_code", "symbol", "name", "area", "industry", "cnspell",
        "market", "list_date", "list_status", "delist_date",
        "is_hs", "act_name", "act_ent_type",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class RefStockNamechange(Base):
    """股票更名记录"""
    __tablename__ = "ref_stock_namechange"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    name = Column(String(50), comment="股票名称")
    start_date = Column(Date, primary_key=True, nullable=False, comment="开始日期")
    end_date = Column(Date, comment="结束日期")
    ann_date = Column(Date, comment="公告日期")
    change_reason = Column(String(200), comment="变更原因")

    _cols = ["ts_code", "name", "start_date", "end_date", "ann_date", "change_reason"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class RefNewShare(Base):
    """新股发行"""
    __tablename__ = "ref_new_share"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    sub_code = Column(String(12), primary_key=True, nullable=False, comment="申购代码")
    name = Column(String(100), comment="名称")
    ipo_date = Column(Date, comment="上网发行日期")
    issue_date = Column(Date, comment="上市日期")
    amount = Column(Numeric(20, 4), comment="发行总量")
    market_amount = Column(Numeric(20, 4), comment="上网发行量")
    price = Column(Numeric(16, 4), comment="发行价格")
    pe = Column(Numeric(16, 4), comment="市盈率")
    limit_amount = Column(Numeric(20, 4), comment="申购上限")
    funds = Column(Numeric(20, 4), comment="募集资金")
    ballot = Column(Numeric(16, 4), comment="中签率")

    _cols = [
        "ts_code", "sub_code", "name", "ipo_date", "issue_date",
        "amount", "market_amount", "price", "pe",
        "limit_amount", "funds", "ballot",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class RefStkLimitDaily(Base):
    """涨跌停价（每日）"""
    __tablename__ = "ref_stk_limit_daily"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    up_limit = Column(Numeric(12, 4), comment="涨停价")
    down_limit = Column(Numeric(12, 4), comment="跌停价")

    _cols = ["ts_code", "trade_date", "up_limit", "down_limit"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class RefSuspendD(Base):
    """停牌信息（每日）"""
    __tablename__ = "ref_suspend_d"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    suspend_type = Column(String(10), comment="停牌类型")
    suspend_reason = Column(String(200), comment="停牌原因")

    _cols = ["ts_code", "trade_date", "suspend_type", "suspend_reason"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


# ═════════════════════════════════════════════════════════════════════
# 二、财务报表 (6 张)
# ═════════════════════════════════════════════════════════════════════

class FinIncome(Base):
    """利润表"""
    __tablename__ = "fin_income"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    ann_date = Column(Date, comment="公告日期")
    f_ann_date = Column(Date, comment="实际公告日期")
    end_date = Column(Date, primary_key=True, nullable=False, comment="报告期")
    report_type = Column(String(1), primary_key=True, comment="报表类型")
    comp_type = Column(String(1), comment="公司类型")
    end_type = Column(String(1), comment="报告期类型")
    # 收入
    total_revenue = Column(Numeric(20, 4))
    revenue = Column(Numeric(20, 4))
    int_income = Column(Numeric(20, 4))
    prem_earned = Column(Numeric(20, 4))
    comm_income = Column(Numeric(20, 4))
    n_commis_income = Column(Numeric(20, 4))
    n_oth_income = Column(Numeric(20, 4))
    n_oth_b_income = Column(Numeric(20, 4))
    prem_income = Column(Numeric(20, 4))
    out_prem = Column(Numeric(20, 4))
    une_prem_reser = Column(Numeric(20, 4))
    reins_income = Column(Numeric(20, 4))
    n_sec_tb_income = Column(Numeric(20, 4))
    n_sec_uw_income = Column(Numeric(20, 4))
    n_asset_mg_income = Column(Numeric(20, 4))
    oth_b_income = Column(Numeric(20, 4))
    fv_value_chg = Column(Numeric(20, 4))
    invest_income = Column(Numeric(20, 4))
    ass_invest_income = Column(Numeric(20, 4))
    forex_gain = Column(Numeric(20, 4))
    # 成本与费用
    total_cogs = Column(Numeric(20, 4))
    oper_cost = Column(Numeric(20, 4))
    int_exp = Column(Numeric(20, 4))
    comm_exp = Column(Numeric(20, 4))
    biz_tax_surchg = Column(Numeric(20, 4))
    sell_exp = Column(Numeric(20, 4))
    admin_exp = Column(Numeric(20, 4))
    fin_exp = Column(Numeric(20, 4))
    assets_impair_loss = Column(Numeric(20, 4))
    # 利润
    oper_profit = Column(Numeric(20, 4))
    total_profit = Column(Numeric(20, 4))
    income_tax = Column(Numeric(20, 4))
    n_income = Column(Numeric(20, 4))
    n_income_attr_p = Column(Numeric(20, 4))
    minority_gain = Column(Numeric(20, 4))
    oth_compr_income = Column(Numeric(20, 4))
    t_compr_income = Column(Numeric(20, 4))
    compr_inc_attr_p = Column(Numeric(20, 4))
    compr_inc_attr_m_s = Column(Numeric(20, 4))
    # EPS
    ebit = Column(Numeric(20, 4))
    ebitda = Column(Numeric(20, 4))
    basic_eps = Column(Numeric(16, 4))
    diluted_eps = Column(Numeric(16, 4))
    # 其他
    insurance_exp = Column(Numeric(20, 4))
    undist_profit = Column(Numeric(20, 4))
    distable_profit = Column(Numeric(20, 4))
    update_flag = Column(String(3))

    _cols = [
        "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
        "comp_type", "end_type",
        "total_revenue", "revenue", "int_income", "prem_earned", "comm_income",
        "n_commis_income", "n_oth_income", "n_oth_b_income", "prem_income",
        "out_prem", "une_prem_reser", "reins_income", "n_sec_tb_income",
        "n_sec_uw_income", "n_asset_mg_income", "oth_b_income", "fv_value_chg",
        "invest_income", "ass_invest_income", "forex_gain",
        "total_cogs", "oper_cost", "int_exp", "comm_exp", "biz_tax_surchg",
        "sell_exp", "admin_exp", "fin_exp", "assets_impair_loss",
        "oper_profit", "total_profit", "income_tax", "n_income",
        "n_income_attr_p", "minority_gain", "oth_compr_income", "t_compr_income",
        "compr_inc_attr_p", "compr_inc_attr_m_s",
        "ebit", "ebitda", "basic_eps", "diluted_eps",
        "insurance_exp", "undist_profit", "distable_profit", "update_flag",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class FinBalancesheet(Base):
    """资产负债表"""
    __tablename__ = "fin_balancesheet"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    ann_date = Column(Date, comment="公告日期")
    f_ann_date = Column(Date, comment="实际公告日期")
    end_date = Column(Date, primary_key=True, nullable=False, comment="报告期")
    report_type = Column(String(1), primary_key=True, comment="报表类型")
    comp_type = Column(String(1), comment="公司类型")
    end_type = Column(String(1), comment="报告期类型")
    # 资产
    total_assets = Column(Numeric(20, 4))
    total_cur_assets = Column(Numeric(20, 4))
    money_cap = Column(Numeric(20, 4))
    trad_asset = Column(Numeric(20, 4))
    notes_receiv = Column(Numeric(20, 4))
    accounts_receiv = Column(Numeric(20, 4))
    oth_receiv = Column(Numeric(20, 4))
    pre_payment = Column(Numeric(20, 4))
    div_receiv = Column(Numeric(20, 4))
    int_receiv = Column(Numeric(20, 4))
    inventories = Column(Numeric(20, 4))
    amor_exp = Column(Numeric(20, 4))
    nca_within_1y = Column(Numeric(20, 4))
    sett_rsrv = Column(Numeric(20, 4))
    loanto_oth_bank_fi = Column(Numeric(20, 4))
    premium_receiv = Column(Numeric(20, 4))
    reinsur_receiv = Column(Numeric(20, 4))
    reinsur_res_receiv = Column(Numeric(20, 4))
    pur_resale_fa = Column(Numeric(20, 4))
    oth_cur_assets = Column(Numeric(20, 4))
    total_nca = Column(Numeric(20, 4))
    fa_avail_for_sale = Column(Numeric(20, 4))
    htm_invest = Column(Numeric(20, 4))
    lt_eqt_invest = Column(Numeric(20, 4))
    invest_real_estate = Column(Numeric(20, 4))
    time_deposits = Column(Numeric(20, 4))
    oth_assets = Column(Numeric(20, 4))
    lt_rec = Column(Numeric(20, 4))
    fix_assets = Column(Numeric(20, 4))
    cip = Column(Numeric(20, 4))
    const_materials = Column(Numeric(20, 4))
    fixed_assets_disp = Column(Numeric(20, 4))
    produc_bio_assets = Column(Numeric(20, 4))
    oil_and_gas_assets = Column(Numeric(20, 4))
    intan_assets = Column(Numeric(20, 4))
    r_and_d = Column(Numeric(20, 4))
    goodwill = Column(Numeric(20, 4))
    lt_amor_exp = Column(Numeric(20, 4))
    defer_tax_assets = Column(Numeric(20, 4))
    decr_in_disbur = Column(Numeric(20, 4))
    # 负债
    total_liab = Column(Numeric(20, 4))
    total_cur_liab = Column(Numeric(20, 4))
    st_borrow = Column(Numeric(20, 4))
    pay_borrow = Column(Numeric(20, 4))
    trad_liab = Column(Numeric(20, 4))
    notes_payable = Column(Numeric(20, 4))
    acct_payable = Column(Numeric(20, 4))
    adv_receipts = Column(Numeric(20, 4))
    int_payable = Column(Numeric(20, 4))
    div_payable = Column(Numeric(20, 4))
    oth_payable = Column(Numeric(20, 4))
    acc_exp = Column(Numeric(20, 4))
    deferred_inc = Column(Numeric(20, 4))
    st_bonds_payable = Column(Numeric(20, 4))
    payable_to_reinsurer = Column(Numeric(20, 4))
    rsrv_insur_cont = Column(Numeric(20, 4))
    acting_trading_sec = Column(Numeric(20, 4))
    acting_uw_sec = Column(Numeric(20, 4))
    non_cur_liab_due_1y = Column(Numeric(20, 4))
    oth_cur_liab = Column(Numeric(20, 4))
    total_ncl = Column(Numeric(20, 4))
    lt_borrow = Column(Numeric(20, 4))
    bonds_payable = Column(Numeric(20, 4))
    lt_payable = Column(Numeric(20, 4))
    specific_payables = Column(Numeric(20, 4))
    estimated_liab = Column(Numeric(20, 4))
    defer_tax_liab = Column(Numeric(20, 4))
    defer_inc_non_cur_liab = Column(Numeric(20, 4))
    oth_ncl = Column(Numeric(20, 4))
    # 权益
    total_hldr_eqy_exc_min_int = Column(Numeric(20, 4))
    minority_int = Column(Numeric(20, 4))
    total_hldr_eqy_inc_min_int = Column(Numeric(20, 4))
    cap_stk = Column(Numeric(20, 4))
    cap_rese = Column(Numeric(20, 4))
    special_rese = Column(Numeric(20, 4))
    surplus_rese = Column(Numeric(20, 4))
    retained_earnings = Column(Numeric(20, 4))
    prov_nom_risks = Column(Numeric(20, 4))
    oth_eqt_tools = Column(Numeric(20, 4))
    oth_eqt_tools_p_shr = Column(Numeric(20, 4))
    oth_compreh_income = Column(Numeric(20, 4))
    update_flag = Column(String(3))

    _cols = [
        "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
        "comp_type", "end_type",
        "total_assets", "total_cur_assets", "money_cap", "trad_asset",
        "notes_receiv", "accounts_receiv", "oth_receiv", "pre_payment",
        "div_receiv", "int_receiv", "inventories", "amor_exp",
        "nca_within_1y", "sett_rsrv", "loanto_oth_bank_fi", "premium_receiv",
        "reinsur_receiv", "reinsur_res_receiv", "pur_resale_fa", "oth_cur_assets",
        "total_nca", "fa_avail_for_sale", "htm_invest", "lt_eqt_invest",
        "invest_real_estate", "time_deposits", "oth_assets", "lt_rec",
        "fix_assets", "cip", "const_materials", "fixed_assets_disp",
        "produc_bio_assets", "oil_and_gas_assets", "intan_assets",
        "r_and_d", "goodwill", "lt_amor_exp", "defer_tax_assets", "decr_in_disbur",
        "total_liab", "total_cur_liab", "st_borrow", "pay_borrow", "trad_liab",
        "notes_payable", "acct_payable", "adv_receipts", "int_payable",
        "div_payable", "oth_payable", "acc_exp", "deferred_inc",
        "st_bonds_payable", "payable_to_reinsurer", "rsrv_insur_cont",
        "acting_trading_sec", "acting_uw_sec", "non_cur_liab_due_1y",
        "oth_cur_liab", "total_ncl", "lt_borrow", "bonds_payable",
        "lt_payable", "specific_payables", "estimated_liab", "defer_tax_liab",
        "defer_inc_non_cur_liab", "oth_ncl",
        "total_hldr_eqy_exc_min_int", "minority_int",
        "total_hldr_eqy_inc_min_int", "cap_stk", "cap_rese", "special_rese",
        "surplus_rese", "retained_earnings", "prov_nom_risks",
        "oth_eqt_tools", "oth_eqt_tools_p_shr", "oth_compreh_income",
        "update_flag",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class FinCashflow(Base):
    """现金流量表"""
    __tablename__ = "fin_cashflow"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    ann_date = Column(Date, comment="公告日期")
    f_ann_date = Column(Date, comment="实际公告日期")
    end_date = Column(Date, primary_key=True, nullable=False, comment="报告期")
    report_type = Column(String(1), primary_key=True, comment="报表类型")
    comp_type = Column(String(1), comment="公司类型")
    end_type = Column(String(1), comment="报告期类型")
    # 经营活动
    net_profit = Column(Numeric(20, 4))
    finan_exp = Column(Numeric(20, 4))
    c_fr_sale_sg = Column(Numeric(20, 4))
    recp_tax_rends = Column(Numeric(20, 4))
    n_depos_incr_fi = Column(Numeric(20, 4))
    n_incr_loans_cb = Column(Numeric(20, 4))
    n_inc_loans_oth_bank = Column(Numeric(20, 4))
    prem_fr_orig_contr = Column(Numeric(20, 4))
    n_incr_insured_dep = Column(Numeric(20, 4))
    n_reinsur_prem = Column(Numeric(20, 4))
    n_incr_disp_tfa = Column(Numeric(20, 4))
    ifc_cashincr = Column(Numeric(20, 4))
    n_incr_disp_faas = Column(Numeric(20, 4))
    n_incr_loans_oth_bank_f = Column(Numeric(20, 4))
    n_cap_incr_repur = Column(Numeric(20, 4))
    c_fr_oth_operate_a = Column(Numeric(20, 4))
    c_inf_fr_operate_a = Column(Numeric(20, 4))
    # 经营活动流出
    c_paid_goods_s = Column(Numeric(20, 4))
    c_paid_to_for_empl = Column(Numeric(20, 4))
    c_paid_for_taxes = Column(Numeric(20, 4))
    n_incr_clt_loan_adv = Column(Numeric(20, 4))
    n_incr_loans_cb_clt = Column(Numeric(20, 4))
    c_pay_claims_orig_inco = Column(Numeric(20, 4))
    pay_handling_chrg = Column(Numeric(20, 4))
    pay_comm_insur_plcy = Column(Numeric(20, 4))
    oth_cash_pay_oper_act = Column(Numeric(20, 4))
    st_cash_out_act = Column(Numeric(20, 4))
    n_cashflow_act = Column(Numeric(20, 4))
    # 投资活动
    c_recp_return_invest = Column(Numeric(20, 4))
    c_recp_income_invest = Column(Numeric(20, 4))
    c_recp_disp_fiolta = Column(Numeric(20, 4))
    c_recp_disp_sobu = Column(Numeric(20, 4))
    c_inf_fr_invest_act = Column(Numeric(20, 4))
    c_paid_acq_const_fiolta = Column(Numeric(20, 4))
    c_paid_invest = Column(Numeric(20, 4))
    c_paid_acq_sobu = Column(Numeric(20, 4))
    c_inf_pay_invest_act = Column(Numeric(20, 4))
    oth_cash_recp_ral_inv_act = Column(Numeric(20, 4))
    oth_cash_pay_ral_inv_act = Column(Numeric(20, 4))
    n_cashflow_inv_act = Column(Numeric(20, 4))
    # 筹资活动
    c_recp_borrow = Column(Numeric(20, 4))
    c_proc_issue_bonds = Column(Numeric(20, 4))
    c_recp_cap_contrib = Column(Numeric(20, 4))
    c_incr_cash_pub = Column(Numeric(20, 4))
    c_recp_cap_othcash = Column(Numeric(20, 4))
    c_inf_fr_finance_act = Column(Numeric(20, 4))
    c_prepay_amt_borr = Column(Numeric(20, 4))
    c_pay_dist_dpcp_int_exp = Column(Numeric(20, 4))
    incr_cash_cash_equ_dm = Column(Numeric(20, 4))
    c_inf_pay_finance_act = Column(Numeric(20, 4))
    oth_cash_recp_ral_fnc_act = Column(Numeric(20, 4))
    oth_cash_pay_ral_fnc_act = Column(Numeric(20, 4))
    n_cashflow_fnc_act = Column(Numeric(20, 4))
    # 汇率与净额
    eff_fx_chg_cash = Column(Numeric(20, 4))
    n_cash = Column(Numeric(20, 4))
    n_cash_equ_beg = Column(Numeric(20, 4))
    n_cash_equ_end = Column(Numeric(20, 4))
    # 补充
    c_conv_debt_into_eq = Column(Numeric(20, 4))
    c_use_fund_cash = Column(Numeric(20, 4))
    free_cashflow = Column(Numeric(20, 4))
    update_flag = Column(String(3))

    _cols = [
        "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
        "comp_type", "end_type",
        "net_profit", "finan_exp", "c_fr_sale_sg", "recp_tax_rends",
        "n_depos_incr_fi", "n_incr_loans_cb", "n_inc_loans_oth_bank",
        "prem_fr_orig_contr", "n_incr_insured_dep", "n_reinsur_prem",
        "n_incr_disp_tfa", "ifc_cashincr", "n_incr_disp_faas",
        "n_incr_loans_oth_bank_f", "n_cap_incr_repur",
        "c_fr_oth_operate_a", "c_inf_fr_operate_a",
        "c_paid_goods_s", "c_paid_to_for_empl", "c_paid_for_taxes",
        "n_incr_clt_loan_adv", "n_incr_loans_cb_clt",
        "c_pay_claims_orig_inco", "pay_handling_chrg", "pay_comm_insur_plcy",
        "oth_cash_pay_oper_act", "st_cash_out_act", "n_cashflow_act",
        "c_recp_return_invest", "c_recp_income_invest",
        "c_recp_disp_fiolta", "c_recp_disp_sobu", "c_inf_fr_invest_act",
        "c_paid_acq_const_fiolta", "c_paid_invest", "c_paid_acq_sobu",
        "c_inf_pay_invest_act",
        "oth_cash_recp_ral_inv_act", "oth_cash_pay_ral_inv_act",
        "n_cashflow_inv_act",
        "c_recp_borrow", "c_proc_issue_bonds", "c_recp_cap_contrib",
        "c_incr_cash_pub", "c_recp_cap_othcash", "c_inf_fr_finance_act",
        "c_prepay_amt_borr", "c_pay_dist_dpcp_int_exp",
        "incr_cash_cash_equ_dm", "c_inf_pay_finance_act",
        "oth_cash_recp_ral_fnc_act", "oth_cash_pay_ral_fnc_act",
        "n_cashflow_fnc_act",
        "eff_fx_chg_cash", "n_cash", "n_cash_equ_beg", "n_cash_equ_end",
        "c_conv_debt_into_eq", "c_use_fund_cash", "free_cashflow",
        "update_flag",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class FinFinaIndicator(Base):
    """财务指标"""
    __tablename__ = "fin_fina_indicator"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    ann_date = Column(Date, comment="公告日期")
    end_date = Column(Date, primary_key=True, nullable=False, comment="报告期")
    # 每股
    eps = Column(Numeric(16, 4))
    dt_eps = Column(Numeric(16, 4))
    total_profit_eps = Column(Numeric(16, 4))
    oper_profit_eps = Column(Numeric(16, 4))
    basic_eps = Column(Numeric(16, 4))
    diluted_eps = Column(Numeric(16, 4))
    # 盈利
    roe = Column(Numeric(16, 4))
    roe_waa = Column(Numeric(16, 4))
    roe_dt = Column(Numeric(16, 4))
    roa = Column(Numeric(16, 4))
    npta = Column(Numeric(16, 4))
    roic = Column(Numeric(16, 4))
    roe_yearly = Column(Numeric(16, 4))
    roa_yearly = Column(Numeric(16, 4))
    roa2_yearly = Column(Numeric(16, 4))
    # 运营
    grossprofit_margin = Column(Numeric(16, 4))
    netprofit_margin = Column(Numeric(16, 4))
    debt_to_assets = Column(Numeric(16, 4))
    assets_turn = Column(Numeric(16, 4))
    inv_turn = Column(Numeric(16, 4))
    ar_turn = Column(Numeric(16, 4))
    # 成长
    or_yoy = Column(Numeric(16, 4))
    op_yoy = Column(Numeric(16, 4))
    ebt_yoy = Column(Numeric(16, 4))
    netprofit_yoy = Column(Numeric(16, 4))
    dt_netprofit_yoy = Column(Numeric(16, 4))
    ocf_yoy = Column(Numeric(16, 4))
    # 偿债
    current_ratio = Column(Numeric(16, 4))
    quick_ratio = Column(Numeric(16, 4))
    equity_ratio = Column(Numeric(16, 4))
    # 估值
    pe = Column(Numeric(16, 4))
    pe_ttm = Column(Numeric(16, 4))
    pb = Column(Numeric(16, 4))
    ps = Column(Numeric(16, 4))
    ps_ttm = Column(Numeric(16, 4))
    dv_ratio = Column(Numeric(16, 4))
    dv_ttm = Column(Numeric(16, 4))
    total_share = Column(Numeric(20, 4))
    float_share = Column(Numeric(20, 4))
    free_share = Column(Numeric(20, 4))
    total_mv = Column(Numeric(20, 4))
    circ_mv = Column(Numeric(20, 4))
    update_flag = Column(String(3))

    _cols = [
        "ts_code", "ann_date", "end_date",
        "eps", "dt_eps", "total_profit_eps", "oper_profit_eps",
        "basic_eps", "diluted_eps",
        "roe", "roe_waa", "roe_dt", "roa", "npta", "roic",
        "roe_yearly", "roa_yearly", "roa2_yearly",
        "grossprofit_margin", "netprofit_margin", "debt_to_assets",
        "assets_turn", "inv_turn", "ar_turn",
        "or_yoy", "op_yoy", "ebt_yoy", "netprofit_yoy",
        "dt_netprofit_yoy", "ocf_yoy",
        "current_ratio", "quick_ratio", "equity_ratio",
        "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
        "total_share", "float_share", "free_share", "total_mv", "circ_mv",
        "update_flag",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class FinForecast(Base):
    """业绩预告"""
    __tablename__ = "fin_forecast"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    ann_date = Column(Date, primary_key=True, nullable=False, comment="公告日期")
    end_date = Column(Date, primary_key=True, nullable=False, comment="报告期")
    type = Column(String(10), comment="预告类型")
    p_change_min = Column(Numeric(20, 4), comment="净利润变动下限(%)")
    p_change_max = Column(Numeric(20, 4), comment="净利润变动上限(%)")
    net_profit_min = Column(Numeric(20, 4), comment="净利润下限")
    net_profit_max = Column(Numeric(20, 4), comment="净利润上限")
    last_parent_net = Column(Numeric(20, 4), comment="上年同期净利润")
    first_ann_date = Column(Date, comment="首次公告日")
    summary = Column(Text, comment="业绩摘要")
    change_reason = Column(Text, comment="变动原因")

    _cols = [
        "ts_code", "ann_date", "end_date", "type",
        "p_change_min", "p_change_max", "net_profit_min", "net_profit_max",
        "last_parent_net", "first_ann_date", "summary", "change_reason",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class FinDividend(Base):
    """分红送股"""
    __tablename__ = "fin_dividend"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    end_date = Column(Date, primary_key=True, nullable=False, comment="分红年度")
    ann_date = Column(Date, comment="公告日期")
    div_proc = Column(String(30), comment="分红进度")
    stk_div = Column(Numeric(16, 4), comment="每股送转")
    stk_bo_rate = Column(Numeric(16, 4), comment="每股送股")
    stk_so_rate = Column(Numeric(16, 4), comment="每股转增")
    cash_div = Column(Numeric(16, 4), comment="每股分红")
    cash_div_tax = Column(Numeric(16, 4), comment="每股分红（税后）")
    record_date = Column(Date, comment="股权登记日")
    ex_date = Column(Date, comment="除权除息日")
    pay_date = Column(Date, comment="派息日")
    div_listdate = Column(Date, comment="红股上市日")
    imp_ann_date = Column(Date, comment="预案公告日")
    base_date = Column(Date, comment="基准日")
    base_share = Column(Numeric(20, 4), comment="基准股本")
    update_flag = Column(String(3))

    _cols = [
        "ts_code", "end_date", "ann_date", "div_proc",
        "stk_div", "stk_bo_rate", "stk_so_rate",
        "cash_div", "cash_div_tax",
        "record_date", "ex_date", "pay_date", "div_listdate",
        "imp_ann_date", "base_date", "base_share", "update_flag",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


# ═════════════════════════════════════════════════════════════════════
# 三、日/周/月 K线 (4 张: stock_daily / weekly / monthly / bars_daily)
# ═════════════════════════════════════════════════════════════════════

class StockDaily(Base):
    """股票日K线"""
    __tablename__ = "stock_daily"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    open = Column(Numeric(12, 4))
    high = Column(Numeric(12, 4))
    low = Column(Numeric(12, 4))
    close = Column(Numeric(12, 4))
    pre_close = Column(Numeric(12, 4))
    change = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(12, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))

    _cols = [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class StockWeekly(Base):
    """股票周K线"""
    __tablename__ = "stock_weekly"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    open = Column(Numeric(12, 4))
    high = Column(Numeric(12, 4))
    low = Column(Numeric(12, 4))
    close = Column(Numeric(12, 4))
    pre_close = Column(Numeric(12, 4))
    change = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(12, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))

    _cols = [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class StockMonthly(Base):
    """股票月K线"""
    __tablename__ = "stock_monthly"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    open = Column(Numeric(12, 4))
    high = Column(Numeric(12, 4))
    low = Column(Numeric(12, 4))
    close = Column(Numeric(12, 4))
    pre_close = Column(Numeric(12, 4))
    change = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(12, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))

    _cols = [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class BarsDaily(Base):
    """通用日线（通用 symbol + time 主键，用于策略回测输出）"""
    __tablename__ = "bars_daily"

    time = Column(DateTime, primary_key=True, nullable=False, comment="时间戳")
    symbol = Column(String(16), primary_key=True, nullable=False, comment="标的代码")
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(BigInteger)
    amount = Column(Float)
    turnover = Column(Float)

    _cols = ["time", "symbol", "open", "high", "low", "close", "volume", "amount", "turnover"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


# ═════════════════════════════════════════════════════════════════════
# 四、盘前股本 / 复权 / 竞价 (3 张)
# ═════════════════════════════════════════════════════════════════════

class StockDailyBasic(Base):
    """盘前股本/每日指标"""
    __tablename__ = "stock_daily_basic"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    close = Column(Numeric(12, 4))
    turnover_rate = Column(Numeric(12, 4))
    turnover_rate_f = Column(Numeric(12, 4))
    volume_ratio = Column(Numeric(12, 4))
    pe = Column(Numeric(16, 4))
    pe_ttm = Column(Numeric(16, 4))
    pb = Column(Numeric(16, 4))
    ps = Column(Numeric(16, 4))
    ps_ttm = Column(Numeric(16, 4))
    dv_ratio = Column(Numeric(12, 4))
    dv_ttm = Column(Numeric(12, 4))
    total_share = Column(Numeric(20, 4))
    float_share = Column(Numeric(20, 4))
    free_share = Column(Numeric(20, 4))
    total_mv = Column(Numeric(20, 4))
    circ_mv = Column(Numeric(20, 4))

    _cols = [
        "ts_code", "trade_date", "close", "turnover_rate", "turnover_rate_f",
        "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm",
        "dv_ratio", "dv_ttm", "total_share", "float_share", "free_share",
        "total_mv", "circ_mv",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class StockAdjFactor(Base):
    """复权因子"""
    __tablename__ = "stock_adj_factor"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    adj_factor = Column(Numeric(16, 8), comment="复权因子")

    _cols = ["ts_code", "trade_date", "adj_factor"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class StockAuctionDaily(Base):
    """集合竞价日聚合"""
    __tablename__ = "stock_auction_daily"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    open_price = Column(Numeric(12, 4))
    preclose_price = Column(Numeric(12, 4))
    auction_vol = Column(BigInteger)
    auction_amount = Column(Numeric(20, 4))
    bid_vol_total = Column(BigInteger)
    ask_vol_total = Column(BigInteger)
    gap_pct = Column(Numeric(12, 4))

    _cols = [
        "ts_code", "trade_date", "open_price", "preclose_price",
        "auction_vol", "auction_amount", "bid_vol_total", "ask_vol_total", "gap_pct",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


# ═════════════════════════════════════════════════════════════════════
# 五、资金 / 两融 / 大宗 / 龙虎榜 (5 张)
# ═════════════════════════════════════════════════════════════════════

class StockMoneyflowDaily(Base):
    """资金流向日数据"""
    __tablename__ = "stock_moneyflow_daily"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    buy_sm_vol = Column(BigInteger)
    buy_sm_amount = Column(Numeric(20, 4))
    sell_sm_vol = Column(BigInteger)
    sell_sm_amount = Column(Numeric(20, 4))
    buy_md_vol = Column(BigInteger)
    buy_md_amount = Column(Numeric(20, 4))
    sell_md_vol = Column(BigInteger)
    sell_md_amount = Column(Numeric(20, 4))
    buy_lg_vol = Column(BigInteger)
    buy_lg_amount = Column(Numeric(20, 4))
    sell_lg_vol = Column(BigInteger)
    sell_lg_amount = Column(Numeric(20, 4))
    buy_elg_vol = Column(BigInteger)
    buy_elg_amount = Column(Numeric(20, 4))
    sell_elg_vol = Column(BigInteger)
    sell_elg_amount = Column(Numeric(20, 4))
    net_mf_vol = Column(BigInteger)
    net_mf_amount = Column(Numeric(20, 4))

    _cols = [
        "ts_code", "trade_date",
        "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
        "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
        "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
        "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
        "net_mf_vol", "net_mf_amount",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class StockTopListDaily(Base):
    """龙虎榜日数据"""
    __tablename__ = "stock_top_list_daily"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    name = Column(String(100))
    close = Column(Numeric(12, 4))
    pct_change = Column(Numeric(12, 4))
    turnover_rate = Column(Numeric(12, 4))
    amount = Column(Numeric(20, 4))
    l_sell = Column(Numeric(20, 4))
    l_buy = Column(Numeric(20, 4))
    l_amount = Column(Numeric(20, 4))
    net_amount = Column(Numeric(20, 4))
    net_rate = Column(Numeric(12, 4))
    amount_rate = Column(Numeric(12, 4))
    float_values = Column(Numeric(20, 4))
    reason = Column(String(200))

    _cols = [
        "ts_code", "trade_date", "name", "close", "pct_change", "turnover_rate",
        "amount", "l_sell", "l_buy", "l_amount", "net_amount",
        "net_rate", "amount_rate", "float_values", "reason",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class MarginSummaryDaily(Base):
    """两融汇总日数据（按交易所）"""
    __tablename__ = "margin_summary_daily"

    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    exchange_id = Column(String(4), primary_key=True, nullable=False, comment="交易所 SSE/SZSE")
    rzye = Column(Numeric(20, 4), comment="融资余额")
    rzmre = Column(Numeric(20, 4), comment="融资买入额")
    rzche = Column(Numeric(20, 4), comment="融资偿还额")
    rqye = Column(Numeric(20, 4), comment="融券余额")
    rqmcl = Column(Numeric(20, 4), comment="融券卖出量")
    rzrqye = Column(Numeric(20, 4), comment="两融余额")

    _cols = ["trade_date", "exchange_id", "rzye", "rzmre", "rzche", "rqye", "rqmcl", "rzrqye"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class MarginDetailDaily(Base):
    """两融明细日数据（按股票）"""
    __tablename__ = "margin_detail_daily"

    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    name = Column(String(100))
    rzye = Column(Numeric(20, 4), comment="融资余额")
    rqye = Column(Numeric(20, 4), comment="融券余额")
    rzmre = Column(Numeric(20, 4), comment="融资买入额")
    rqyl = Column(Numeric(20, 4), comment="融券余量")
    rzche = Column(Numeric(20, 4), comment="融资偿还额")
    rqchl = Column(Numeric(20, 4), comment="融券偿还量")
    rqmcl = Column(Numeric(20, 4), comment="融券卖出量")
    rzrqye = Column(Numeric(20, 4), comment="两融余额")

    _cols = [
        "trade_date", "ts_code", "name",
        "rzye", "rqye", "rzmre", "rqyl", "rzche", "rqchl", "rqmcl", "rzrqye",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class StockBlockTrade(Base):
    """大宗交易"""
    __tablename__ = "stock_block_trade"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    price = Column(Numeric(16, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))
    buyer = Column(String(200))
    seller = Column(String(200))

    _cols = ["ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


# ═════════════════════════════════════════════════════════════════════
# 六、指数 / 权重 (2 张)
# ═════════════════════════════════════════════════════════════════════

class IndexDaily(Base):
    """指数日线行情"""
    __tablename__ = "index_daily"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="指数代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    open = Column(Numeric(12, 4))
    high = Column(Numeric(12, 4))
    low = Column(Numeric(12, 4))
    close = Column(Numeric(12, 4))
    pre_close = Column(Numeric(12, 4))
    change = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(12, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))

    _cols = [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class IndexWeight(Base):
    """指数成分权重"""
    __tablename__ = "index_weight"

    index_code = Column(String(16), primary_key=True, nullable=False, comment="指数代码")
    con_code = Column(String(16), primary_key=True, nullable=False, comment="成分股代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    weight = Column(Numeric(12, 6), comment="权重")

    _cols = ["index_code", "con_code", "trade_date", "weight"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


# ═════════════════════════════════════════════════════════════════════
# 七、基金 / ETF (3 张)
# ═════════════════════════════════════════════════════════════════════

class FundDaily(Base):
    """基金日行情"""
    __tablename__ = "fund_daily"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="基金代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    open = Column(Numeric(12, 4))
    high = Column(Numeric(12, 4))
    low = Column(Numeric(12, 4))
    close = Column(Numeric(12, 4))
    pre_close = Column(Numeric(12, 4))
    change = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(12, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))

    _cols = [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class FundNav(Base):
    """基金净值"""
    __tablename__ = "fund_nav"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="基金代码")
    nav_date = Column(Date, primary_key=True, nullable=False, comment="净值日期")
    unit_nav = Column(Numeric(16, 6), comment="单位净值")
    accum_nav = Column(Numeric(16, 6), comment="累计净值")
    adj_nav = Column(Numeric(16, 6), comment="复权净值")

    _cols = ["ts_code", "nav_date", "unit_nav", "accum_nav", "adj_nav"]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


class EtfDaily(Base):
    """ETF日行情"""
    __tablename__ = "etf_daily"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="ETF代码")
    trade_date = Column(Date, primary_key=True, nullable=False, comment="交易日期")
    open = Column(Numeric(12, 4))
    high = Column(Numeric(12, 4))
    low = Column(Numeric(12, 4))
    close = Column(Numeric(12, 4))
    pre_close = Column(Numeric(12, 4))
    change = Column(Numeric(12, 4))
    pct_chg = Column(Numeric(12, 4))
    vol = Column(BigInteger)
    amount = Column(Numeric(20, 4))

    _cols = [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


# ═════════════════════════════════════════════════════════════════════
# 八、研报一致预期 (1 张)
# ═════════════════════════════════════════════════════════════════════

class BrokerReportConsensus(Base):
    """券商一致预期"""
    __tablename__ = "broker_report_consensus"

    ts_code = Column(String(16), primary_key=True, nullable=False, comment="股票代码")
    calc_date = Column(Date, primary_key=True, nullable=False, comment="计算日期")
    buy_count = Column(Integer)
    overweight_count = Column(Integer)
    hold_count = Column(Integer)
    underweight_count = Column(Integer)
    sell_count = Column(Integer)
    total_count = Column(Integer)
    consensus_score = Column(Numeric(6, 4))
    avg_target = Column(Numeric(12, 4))
    avg_upside = Column(Numeric(12, 4))

    _cols = [
        "ts_code", "calc_date",
        "buy_count", "overweight_count", "hold_count",
        "underweight_count", "sell_count", "total_count",
        "consensus_score", "avg_target", "avg_upside",
    ]

    def to_dict(self) -> dict[str, Any]:
        return _row_to_dict(self, self._cols)


# ═════════════════════════════════════════════════════════════════════
# 模型注册表（方便批量操作）
# ═════════════════════════════════════════════════════════════════════

ALL_MODELS = [
    # 参照
    RefTradeCal, RefStockBasic, RefStockNamechange, RefNewShare,
    RefStkLimitDaily, RefSuspendD,
    # 财务
    FinIncome, FinBalancesheet, FinCashflow,
    FinFinaIndicator, FinForecast, FinDividend,
    # K线
    StockDaily, StockWeekly, StockMonthly, BarsDaily,
    # 盘前/复权/竞价
    StockDailyBasic, StockAdjFactor, StockAuctionDaily,
    # 资金/两融/龙虎
    StockMoneyflowDaily, StockTopListDaily,
    MarginSummaryDaily, MarginDetailDaily, StockBlockTrade,
    # 指数/权重
    IndexDaily, IndexWeight,
    # 基金/ETF
    FundDaily, FundNav, EtfDaily,
    # 研报
    BrokerReportConsensus,
]

# 表名 → 模型 映射
TABLE_MODEL_MAP = {m.__tablename__: m for m in ALL_MODELS}
