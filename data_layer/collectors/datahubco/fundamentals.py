"""
Datahubco 资金 / 基本面补数（→ PostgreSQL）

覆盖：
  - stock_top_list_daily      top_list 龙虎榜
  - stock_block_trade         block_trade 大宗交易
  - margin_summary_daily      margin 两融汇总
  - margin_detail_daily       margin_detail 两融明细
  - stock_moneyflow_daily     moneyflow 个股资金流
  - stock_hk_hold             hk_hold 北向/港股通持股
  - fin_cashflow              cashflow 现金流量表
  - fin_income                income 利润表
  - fin_balancesheet          balancesheet 资产负债表
  - fin_fina_indicator        fina_indicator ROE/EPS 等
  - fin_mainbz                fina_mainbz 主营构成
  - fin_audit                 fina_audit 审计意见
  - fin_dividend              dividend 分红送转
  - fin_express               express 业绩快报
  - fin_disclosure_date       disclosure_date 财报披露日程
  - fin_forecast              forecast 业绩预告
  - stock_top10_holders       top10_floatholders（网关无 top10_holders 时用流通股东）
  - sector_moneyflow_daily    moneyflow_ind_dc 板块资金（含个股 L2/L3）
  - stock_daily_basic         daily_basic 每日指标
  - stock_adj_factor          adj_factor 复权因子
  - ref_stk_limit_daily       stk_limit 涨跌停价
  - stock_limit_list_daily    limit_list_d 涨跌停榜
  - ref_suspend_d             suspend_d 停复牌
  - stock_top_inst_daily      top_inst 龙虎榜席位
  - ref_new_share             new_share 新股
  - ref_index_basic           index_basic 指数主档
  - index_weekly              index_weekly 指数周线
  - index_monthly             index_monthly 指数月线
  - index_daily_basic         index_dailybasic 指数每日估值/市值
  - ref_index_classify        index_classify 行业分类（申万等）
  - index_weight              index_weight 指数成分权重
  - ref_fund_basic            fund_basic 基金主档
  - fund_nav                  fund_nav 基金净值
  - fund_div                  fund_div 基金分红
  - fund_portfolio            fund_portfolio 基金持仓
  - fund_adj                  fund_adj 基金复权因子
  - stock_share_float         share_float 限售解禁
  - stock_pledge_stat         pledge_stat 股权质押统计
  - stock_holdertrade         stk_holdertrade 股东增减持
  - stock_cyq_perf            cyq_perf 筹码获利/成本分布摘要
  - stock_cyq_chips           cyq_chips 筹码分布明细
  - stock_report_rc           report_rc 券商研报
  - ths_member                ths_member 同花顺概念成分
  - cb_basic                  cb_basic 可转债基础
  - cb_daily                  cb_daily 可转债日线
  - fut_basic                 fut_basic 期货主档
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import text as sa_text

from data_layer.db_manager import storage_manager

from .client import DatahubcoClient
from .selective import SelectiveDataFetcher

logger = logging.getLogger("collector.datahubco.fundamentals")

# dataset → 元数据
FUND_DATASETS: Dict[str, Dict[str, Any]] = {
    "top_list": {
        "table": "stock_top_list_daily",
        "api": "top_list",
        "desc": "龙虎榜",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "market_or_stock",
    },
    "block_trade": {
        "table": "stock_block_trade",
        "api": "block_trade",
        "desc": "大宗交易",
        "conflict": "ts_code, trade_date, price, vol, buyer, seller",
        "date_col": "trade_date",
        "scope": "market_or_stock",
        "replace_mode": "day_stock",  # 同日多笔：按日+股删后插
    },
    "margin": {
        "table": "margin_summary_daily",
        "api": "margin",
        "desc": "融资融券汇总（交易所）",
        "conflict": "trade_date, exchange_id",
        "date_col": "trade_date",
        "scope": "market",
    },
    "margin_detail": {
        "table": "margin_detail_daily",
        "api": "margin_detail",
        "desc": "融资融券明细（个股）",
        "conflict": "trade_date, ts_code",
        "date_col": "trade_date",
        "scope": "stock",
    },
    "moneyflow": {
        "table": "stock_moneyflow_daily",
        "api": "moneyflow",
        "desc": "个股资金流向",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock",
    },
    "hk_hold": {
        "table": "stock_hk_hold",
        "api": "hk_hold",
        "desc": "北向/港股通持股",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock_or_market",
    },
    "cashflow": {
        "table": "fin_cashflow",
        "api": "cashflow",
        "desc": "现金流量表",
        "conflict": "ts_code, end_date, report_type",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "income": {
        "table": "fin_income",
        "api": "income",
        "desc": "利润表",
        "conflict": "ts_code, end_date, report_type",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "balancesheet": {
        "table": "fin_balancesheet",
        "api": "balancesheet",
        "desc": "资产负债表",
        "conflict": "ts_code, end_date, report_type",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "fina_indicator": {
        "table": "fin_fina_indicator",
        "api": "fina_indicator",
        "desc": "财务指标（ROE/EPS 等）",
        "conflict": "ts_code, end_date",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "fina_mainbz": {
        "table": "fin_mainbz",
        "api": "fina_mainbz",
        "desc": "主营构成（产品/地区/行业）",
        "conflict": "ts_code, end_date, bz_item",
        "date_col": "end_date",
        "scope": "stock",
        "fin_simple": True,
    },
    "fina_audit": {
        "table": "fin_audit",
        "api": "fina_audit",
        "desc": "审计意见",
        "conflict": "ts_code, end_date, audit_agency",
        "date_col": "end_date",
        "scope": "stock",
        "fin_simple": True,
    },
    "dividend": {
        "table": "fin_dividend",
        "api": "dividend",
        "desc": "分红送转",
        "conflict": "ts_code, end_date, div_proc, ann_date",
        "date_col": "end_date",
        "scope": "stock",
        "fin_simple": True,
    },
    "express": {
        "table": "fin_express",
        "api": "express",
        "desc": "业绩快报",
        "conflict": "ts_code, ann_date, end_date",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "disclosure_date": {
        "table": "fin_disclosure_date",
        "api": "disclosure_date",
        "desc": "财报披露日程",
        "conflict": "ts_code, end_date",
        "date_col": "end_date",
        "scope": "market_or_stock",
    },
    "forecast": {
        "table": "fin_forecast",
        "api": "forecast",
        "desc": "业绩预告",
        "conflict": "ts_code, ann_date, end_date",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "top10_holders": {
        "table": "stock_top10_holders",
        "api": "top10_floatholders",  # 网关暂无 top10_holders
        "api_fallback": ["top10_holders"],
        "desc": "十大股东（优先流通股东 top10_floatholders）",
        "conflict": "ts_code, end_date, holder_name",
        "date_col": "end_date",
        "scope": "stock",
        "fin": True,
    },
    "sector_moneyflow": {
        "table": "sector_moneyflow_daily",
        "api": "moneyflow_ind_dc",
        "desc": "东财行业/概念板块资金流向",
        "conflict": "ts_code, trade_date, content_type",
        "date_col": "trade_date",
        "scope": "market",
    },
    "daily_basic": {
        "table": "stock_daily_basic",
        "api": "daily_basic",
        "desc": "每日指标（PE/PB/换手/市值）",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock_or_market",
    },
    "adj_factor": {
        "table": "stock_adj_factor",
        "api": "adj_factor",
        "desc": "复权因子",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock_or_market",
    },
    "stk_limit": {
        "table": "ref_stk_limit_daily",
        "api": "stk_limit",
        "desc": "涨跌停价",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock_or_market",
    },
    "limit_list": {
        "table": "stock_limit_list_daily",
        "api": "limit_list_d",
        "desc": "涨跌停榜（涨停/跌停/炸板）",
        "conflict": "trade_date, ts_code, limit_type",
        "date_col": "trade_date",
        "scope": "market",
    },
    "suspend_d": {
        "table": "ref_suspend_d",
        "api": "suspend_d",
        "desc": "停复牌",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock_or_market",
    },
    "top_inst": {
        "table": "stock_top_inst_daily",
        "api": "top_inst",
        "desc": "龙虎榜席位（营业部）",
        "conflict": "trade_date, ts_code, exalter, side",
        "date_col": "trade_date",
        "scope": "market_or_stock",
    },
    "new_share": {
        "table": "ref_new_share",
        "api": "new_share",
        "desc": "新股发行",
        "conflict": "ts_code, sub_code",
        "date_col": "ipo_date",
        "scope": "market",
    },
    "index_basic": {
        "table": "ref_index_basic",
        "api": "index_basic",
        "desc": "指数主档",
        "conflict": "ts_code",
        "date_col": "list_date",
        "scope": "market",
        "ref": True,
    },
    "index_weekly": {
        "table": "index_weekly",
        "api": "index_weekly",
        "desc": "指数周线",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock",
        "bars": True,
    },
    "index_monthly": {
        "table": "index_monthly",
        "api": "index_monthly",
        "desc": "指数月线",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock",
        "bars": True,
    },
    "index_dailybasic": {
        "table": "index_daily_basic",
        "api": "index_dailybasic",
        "desc": "指数每日估值/市值",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock_or_market",
    },
    "index_classify": {
        "table": "ref_index_classify",
        "api": "index_classify",
        "desc": "行业分类（申万等）",
        "conflict": "index_code, src",
        "date_col": None,
        "scope": "market",
        "ref": True,
    },
    "index_weight": {
        "table": "index_weight",
        "api": "index_weight",
        "desc": "指数成分权重",
        "conflict": "index_code, con_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock",
    },
    "fund_basic": {
        "table": "ref_fund_basic",
        "api": "fund_basic",
        "desc": "基金主档",
        "conflict": "ts_code",
        "date_col": "found_date",
        "scope": "market",
        "ref": True,
    },
    "fund_nav": {
        "table": "fund_nav",
        "api": "fund_nav",
        "desc": "基金净值",
        "conflict": "ts_code, nav_date",
        "date_col": "nav_date",
        "scope": "stock",
    },
    "fund_div": {
        "table": "fund_div",
        "api": "fund_div",
        "desc": "基金分红",
        "conflict": "ts_code, ann_date, ex_date",
        "date_col": "ex_date",
        "scope": "stock",
        "fin_simple": True,
    },
    "fund_portfolio": {
        "table": "fund_portfolio",
        "api": "fund_portfolio",
        "desc": "基金持仓",
        "conflict": "ts_code, end_date, symbol",
        "date_col": "end_date",
        "scope": "stock",
        "fin_simple": True,
    },
    "fund_adj": {
        "table": "fund_adj",
        "api": "fund_adj",
        "desc": "基金复权因子",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock",
    },
    "share_float": {
        "table": "stock_share_float",
        "api": "share_float",
        "desc": "限售解禁",
        "conflict": "ts_code, float_date, holder_name",
        "date_col": "float_date",
        "scope": "stock",
        "fin_simple": True,
    },
    "pledge_stat": {
        "table": "stock_pledge_stat",
        "api": "pledge_stat",
        "desc": "股权质押统计",
        "conflict": "ts_code, end_date",
        "date_col": "end_date",
        "scope": "stock",
        "fin_simple": True,
    },
    "stk_holdertrade": {
        "table": "stock_holdertrade",
        "api": "stk_holdertrade",
        "desc": "股东增减持",
        "conflict": "ts_code, ann_date, holder_name, in_de",
        "date_col": "ann_date",
        "scope": "stock",
        "fin_simple": True,
    },
    "cyq_perf": {
        "table": "stock_cyq_perf",
        "api": "cyq_perf",
        "desc": "筹码获利/成本分布摘要",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock_or_market",
    },
    "cyq_chips": {
        "table": "stock_cyq_chips",
        "api": "cyq_chips",
        "desc": "筹码分布明细",
        "conflict": "ts_code, trade_date, price",
        "date_col": "trade_date",
        "scope": "stock",
    },
    "report_rc": {
        "table": "stock_report_rc",
        "api": "report_rc",
        "desc": "券商研报",
        "conflict": "ts_code, report_date, org_name, report_title",
        "date_col": "report_date",
        "scope": "stock_or_market",
    },
    "ths_member": {
        "table": "ths_member",
        "api": "ths_member",
        "desc": "同花顺概念成分",
        "conflict": "ts_code, con_code",
        "date_col": None,
        "scope": "market_or_stock",
        "ref": True,
    },
    "cb_basic": {
        "table": "cb_basic",
        "api": "cb_basic",
        "desc": "可转债基础信息",
        "conflict": "ts_code",
        "date_col": "list_date",
        "scope": "market",
        "ref": True,
    },
    "cb_daily": {
        "table": "cb_daily",
        "api": "cb_daily",
        "desc": "可转债日线",
        "conflict": "ts_code, trade_date",
        "date_col": "trade_date",
        "scope": "stock_or_market",
    },
    "fut_basic": {
        "table": "fut_basic",
        "api": "fut_basic",
        "desc": "期货主档",
        "conflict": "ts_code",
        "date_col": "list_date",
        "scope": "market",
        "ref": True,
    },
}

EXTRA_DDL = [
    """
    CREATE TABLE IF NOT EXISTS stock_hk_hold (
        ts_code     VARCHAR(16)  NOT NULL,
        trade_date  DATE         NOT NULL,
        code        VARCHAR(16),
        name        VARCHAR(64),
        vol         NUMERIC(24,4),
        ratio       NUMERIC(12,6),
        exchange    VARCHAR(8),
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_top10_holders (
        ts_code            VARCHAR(16)  NOT NULL,
        ann_date           DATE,
        end_date           DATE         NOT NULL,
        holder_name        VARCHAR(200) NOT NULL,
        hold_amount        NUMERIC(24,4),
        hold_ratio         NUMERIC(12,6),
        hold_float_ratio   NUMERIC(12,6),
        hold_change        NUMERIC(24,4),
        holder_type        VARCHAR(64),
        holder_kind        VARCHAR(32)  DEFAULT 'float',
        updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, end_date, holder_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_limit_list_daily (
        trade_date      DATE         NOT NULL,
        ts_code         VARCHAR(16)  NOT NULL,
        limit_type      VARCHAR(8)   NOT NULL DEFAULT '',
        name            VARCHAR(64),
        industry        VARCHAR(64),
        close           NUMERIC(12,4),
        pct_chg         NUMERIC(12,4),
        amount          NUMERIC(24,4),
        limit_amount    NUMERIC(24,4),
        float_mv        NUMERIC(24,4),
        total_mv        NUMERIC(24,4),
        turnover_ratio  NUMERIC(12,4),
        fd_amount       NUMERIC(24,4),
        first_time      VARCHAR(16),
        last_time       VARCHAR(16),
        open_times      NUMERIC(12,4),
        up_stat         VARCHAR(32),
        limit_times     NUMERIC(12,4),
        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, ts_code, limit_type)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_top_inst_daily (
        trade_date  DATE         NOT NULL,
        ts_code     VARCHAR(16)  NOT NULL,
        exalter     VARCHAR(128) NOT NULL,
        side        VARCHAR(8)   NOT NULL DEFAULT '',
        buy         NUMERIC(24,4),
        buy_rate    NUMERIC(12,4),
        sell        NUMERIC(24,4),
        sell_rate   NUMERIC(12,4),
        net_buy     NUMERIC(24,4),
        reason      TEXT,
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (trade_date, ts_code, exalter, side)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_daily_basic (
        ts_code         VARCHAR(16)   NOT NULL,
        trade_date      DATE          NOT NULL,
        close           NUMERIC(12,4),
        turnover_rate   NUMERIC(12,4),
        turnover_rate_f NUMERIC(12,4),
        volume_ratio    NUMERIC(12,4),
        pe              NUMERIC(16,4),
        pe_ttm          NUMERIC(16,4),
        pb              NUMERIC(16,4),
        ps              NUMERIC(16,4),
        ps_ttm          NUMERIC(16,4),
        dv_ratio        NUMERIC(12,4),
        dv_ttm          NUMERIC(12,4),
        total_share     NUMERIC(20,4),
        float_share     NUMERIC(20,4),
        free_share      NUMERIC(20,4),
        total_mv        NUMERIC(20,4),
        circ_mv         NUMERIC(20,4),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_adj_factor (
        ts_code     VARCHAR(16)  NOT NULL,
        trade_date  DATE         NOT NULL,
        adj_factor  NUMERIC(16,8),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ref_stk_limit_daily (
        ts_code     VARCHAR(16)  NOT NULL,
        trade_date  DATE         NOT NULL,
        up_limit    NUMERIC(12,4),
        down_limit  NUMERIC(12,4),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ref_suspend_d (
        ts_code         VARCHAR(16)  NOT NULL,
        trade_date      DATE         NOT NULL,
        suspend_type    VARCHAR(10),
        suspend_reason  VARCHAR(200),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ref_new_share (
        ts_code        VARCHAR(16)  NOT NULL,
        sub_code       VARCHAR(12)  NOT NULL,
        name           VARCHAR(100),
        ipo_date       DATE,
        issue_date     DATE,
        amount         NUMERIC(20,4),
        market_amount  NUMERIC(20,4),
        price          NUMERIC(16,4),
        pe             NUMERIC(16,4),
        limit_amount   NUMERIC(20,4),
        funds          NUMERIC(20,4),
        ballot         NUMERIC(16,4),
        PRIMARY KEY (ts_code, sub_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fin_mainbz (
        ts_code     VARCHAR(16)   NOT NULL,
        end_date    DATE          NOT NULL,
        bz_item     VARCHAR(200)  NOT NULL DEFAULT '',
        bz_code     VARCHAR(16),
        bz_sales    NUMERIC(24,4),
        bz_profit   NUMERIC(24,4),
        bz_cost     NUMERIC(24,4),
        curr_type   VARCHAR(16),
        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, end_date, bz_item)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fin_audit (
        ts_code       VARCHAR(16)   NOT NULL,
        ann_date      DATE,
        end_date      DATE          NOT NULL,
        audit_result  TEXT,
        audit_fees    NUMERIC(20,4),
        audit_agency  VARCHAR(200)  NOT NULL DEFAULT '',
        audit_sign    VARCHAR(200),
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, end_date, audit_agency)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fin_disclosure_date (
        ts_code      VARCHAR(16)  NOT NULL,
        ann_date     DATE,
        end_date     DATE         NOT NULL,
        pre_date     DATE,
        actual_date  DATE,
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, end_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS index_weekly (
        ts_code     VARCHAR(16)  NOT NULL,
        trade_date  DATE         NOT NULL,
        open        NUMERIC(12,4),
        high        NUMERIC(12,4),
        low         NUMERIC(12,4),
        close       NUMERIC(12,4),
        pre_close   NUMERIC(12,4),
        change      NUMERIC(12,4),
        pct_chg     NUMERIC(12,4),
        vol         BIGINT,
        amount      NUMERIC(20,4),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS index_monthly (
        ts_code     VARCHAR(16)  NOT NULL,
        trade_date  DATE         NOT NULL,
        open        NUMERIC(12,4),
        high        NUMERIC(12,4),
        low         NUMERIC(12,4),
        close       NUMERIC(12,4),
        pre_close   NUMERIC(12,4),
        change      NUMERIC(12,4),
        pct_chg     NUMERIC(12,4),
        vol         BIGINT,
        amount      NUMERIC(20,4),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS index_daily_basic (
        ts_code          VARCHAR(16)  NOT NULL,
        trade_date       DATE         NOT NULL,
        total_mv         NUMERIC(24,4),
        float_mv         NUMERIC(24,4),
        total_share      NUMERIC(24,4),
        float_share      NUMERIC(24,4),
        free_share       NUMERIC(24,4),
        turnover_rate    NUMERIC(12,4),
        turnover_rate_f  NUMERIC(12,4),
        pe               NUMERIC(16,4),
        pe_ttm           NUMERIC(16,4),
        pb               NUMERIC(16,4),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ref_index_classify (
        index_code     VARCHAR(16)   NOT NULL,
        industry_name  VARCHAR(100),
        level          VARCHAR(8),
        industry_code  VARCHAR(16),
        is_pub         VARCHAR(4),
        parent_code    VARCHAR(16),
        src            VARCHAR(32)   NOT NULL DEFAULT 'SW2021',
        updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (index_code, src)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_div (
        ts_code       VARCHAR(16)  NOT NULL,
        ann_date      DATE         NOT NULL,
        ex_date       DATE         NOT NULL DEFAULT '1970-01-01',
        imp_anndate   DATE,
        base_date     DATE,
        div_proc      VARCHAR(30),
        record_date   DATE,
        pay_date      DATE,
        earpay_date   DATE,
        net_ex_date   DATE,
        div_cash      NUMERIC(16,6),
        base_unit     NUMERIC(16,6),
        ear_distr     NUMERIC(20,4),
        ear_amount    NUMERIC(20,4),
        account_date  DATE,
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, ann_date, ex_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_portfolio (
        ts_code          VARCHAR(16)  NOT NULL,
        end_date         DATE         NOT NULL,
        symbol           VARCHAR(16)  NOT NULL,
        ann_date         DATE,
        mkv              NUMERIC(24,4),
        amount           NUMERIC(24,4),
        stk_mkv_ratio    NUMERIC(12,6),
        stk_float_ratio  NUMERIC(12,6),
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, end_date, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fund_adj (
        ts_code     VARCHAR(16)  NOT NULL,
        trade_date  DATE         NOT NULL,
        adj_factor  NUMERIC(16,8),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_share_float (
        ts_code      VARCHAR(16)   NOT NULL,
        ann_date     DATE,
        float_date   DATE          NOT NULL,
        float_share  NUMERIC(24,4),
        float_ratio  NUMERIC(12,6),
        holder_name  VARCHAR(200)  NOT NULL DEFAULT '',
        share_type   VARCHAR(64),
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, float_date, holder_name)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_pledge_stat (
        ts_code         VARCHAR(16)  NOT NULL,
        end_date        DATE         NOT NULL,
        pledge_count    NUMERIC(12,4),
        unrest_pledge   NUMERIC(20,4),
        rest_pledge     NUMERIC(20,4),
        total_share     NUMERIC(20,4),
        pledge_ratio    NUMERIC(12,6),
        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, end_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_holdertrade (
        ts_code       VARCHAR(16)   NOT NULL,
        ann_date      DATE          NOT NULL,
        holder_name   VARCHAR(200)  NOT NULL DEFAULT '',
        holder_type   VARCHAR(16),
        in_de         VARCHAR(8)    NOT NULL DEFAULT '',
        change_vol    NUMERIC(24,4),
        change_ratio  NUMERIC(12,6),
        after_share   NUMERIC(24,4),
        after_ratio   NUMERIC(12,6),
        avg_price     NUMERIC(16,4),
        total_share   NUMERIC(24,4),
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, ann_date, holder_name, in_de)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_cyq_perf (
        ts_code      VARCHAR(16)  NOT NULL,
        trade_date   DATE         NOT NULL,
        his_low      NUMERIC(16,4),
        his_high     NUMERIC(16,4),
        cost_5pct    NUMERIC(16,4),
        cost_15pct   NUMERIC(16,4),
        cost_50pct   NUMERIC(16,4),
        cost_85pct   NUMERIC(16,4),
        cost_95pct   NUMERIC(16,4),
        weight_avg   NUMERIC(16,4),
        winner_rate  NUMERIC(12,4),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_cyq_chips (
        ts_code     VARCHAR(16)  NOT NULL,
        trade_date  DATE         NOT NULL,
        price       NUMERIC(16,4) NOT NULL,
        percent     NUMERIC(12,6),
        PRIMARY KEY (ts_code, trade_date, price)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_report_rc (
        ts_code       VARCHAR(16)   NOT NULL,
        report_date   DATE          NOT NULL,
        org_name      VARCHAR(100)  NOT NULL DEFAULT '',
        report_title  VARCHAR(300)  NOT NULL DEFAULT '',
        name          VARCHAR(64),
        report_type   VARCHAR(32),
        classify      VARCHAR(64),
        author_name   VARCHAR(200),
        quarter       VARCHAR(16),
        op_rt         NUMERIC(20,4),
        op_pr         NUMERIC(20,4),
        tp            NUMERIC(20,4),
        np            NUMERIC(20,4),
        eps           NUMERIC(16,4),
        pe            NUMERIC(16,4),
        rd            NUMERIC(16,4),
        roe           NUMERIC(16,4),
        ev_ebitda     NUMERIC(16,4),
        rating        VARCHAR(32),
        max_price     NUMERIC(16,4),
        min_price     NUMERIC(16,4),
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, report_date, org_name, report_title)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ths_member (
        ts_code    VARCHAR(16)  NOT NULL,
        con_code   VARCHAR(16)  NOT NULL,
        con_name   VARCHAR(64),
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, con_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cb_basic (
        ts_code           VARCHAR(16)  PRIMARY KEY,
        bond_full_name    VARCHAR(200),
        bond_short_name   VARCHAR(64),
        cb_type           VARCHAR(16),
        cb_code           VARCHAR(16),
        stk_code          VARCHAR(16),
        stk_short_name    VARCHAR(64),
        maturity          NUMERIC(8,2),
        par               NUMERIC(12,4),
        issue_price       NUMERIC(12,4),
        issue_size        NUMERIC(24,4),
        remain_size       NUMERIC(24,4),
        value_date        DATE,
        maturity_date     DATE,
        coupon_rate       NUMERIC(12,4),
        add_rate          NUMERIC(12,4),
        pay_per_year      NUMERIC(8,2),
        list_date         DATE,
        delist_date       DATE,
        exchange          VARCHAR(8),
        conv_start_date   DATE,
        conv_end_date     DATE,
        conv_stop_date    DATE,
        first_conv_price  NUMERIC(16,4),
        conv_price        NUMERIC(16,4),
        rate_clause       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cb_daily (
        ts_code     VARCHAR(16)  NOT NULL,
        trade_date  DATE         NOT NULL,
        pre_close   NUMERIC(12,4),
        open        NUMERIC(12,4),
        high        NUMERIC(12,4),
        low         NUMERIC(12,4),
        close       NUMERIC(12,4),
        change      NUMERIC(12,4),
        pct_chg     NUMERIC(12,4),
        vol         NUMERIC(20,4),
        amount      NUMERIC(20,4),
        PRIMARY KEY (ts_code, trade_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fut_basic (
        ts_code           VARCHAR(24)  PRIMARY KEY,
        symbol            VARCHAR(32),
        exchange          VARCHAR(16),
        name              VARCHAR(64),
        fut_code          VARCHAR(16),
        multiplier        NUMERIC(16,4),
        trade_unit        VARCHAR(32),
        per_unit          NUMERIC(16,4),
        quote_unit        VARCHAR(64),
        quote_unit_desc   VARCHAR(64),
        d_mode_desc       VARCHAR(64),
        list_date         DATE,
        delist_date       DATE,
        d_month           VARCHAR(16),
        last_ddate        DATE
    )
    """,
]

# 大宗交易：旧表 PK=(ts_code,trade_date) 无法存同日多笔，迁移到明细唯一键
BLOCK_TRADE_MIGRATE = [
    "ALTER TABLE stock_block_trade DROP CONSTRAINT IF EXISTS stock_block_trade_pkey",
    "ALTER TABLE stock_block_trade DROP CONSTRAINT IF EXISTS \"441_stock_block_trade_pkey\"",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_block_trade_detail
    ON stock_block_trade (ts_code, trade_date, price, vol, buyer, seller)
    """,
]

# 分红：旧 PK=(ts_code,end_date) 无法存同报告期多进度（预案/实施），扩到明细键
DIVIDEND_MIGRATE = [
    "ALTER TABLE fin_dividend DROP CONSTRAINT IF EXISTS fin_dividend_pkey",
    "UPDATE fin_dividend SET div_proc = COALESCE(NULLIF(TRIM(div_proc), ''), '') WHERE div_proc IS NULL OR TRIM(div_proc) = ''",
    "UPDATE fin_dividend SET ann_date = end_date WHERE ann_date IS NULL AND end_date IS NOT NULL",
    "ALTER TABLE fin_dividend ALTER COLUMN div_proc SET DEFAULT ''",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_fin_dividend_detail
    ON fin_dividend (ts_code, end_date, div_proc, ann_date)
    """,
]


def _parse_dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series.astype(str), errors="coerce").dt.date


def _to_ymd(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    raw = str(s).strip().replace("-", "")[:8]
    if len(raw) != 8 or not raw.isdigit():
        return None
    return raw


def _parse_date(s: Optional[str]) -> Optional[date]:
    ymd = _to_ymd(s)
    if not ymd:
        return None
    return datetime.strptime(ymd, "%Y%m%d").date()


class FundDataFetcher:
    """资金 / 基本面：Datahubco 拉取 + PostgreSQL upsert"""

    def __init__(self, client: Optional[DatahubcoClient] = None):
        self.client = client or DatahubcoClient()
        self._engine = None
        self._tables_ready = False
        self._selective: Optional[SelectiveDataFetcher] = None

    @property
    def engine(self):
        if self._engine is None:
            self._engine = storage_manager.get_postgres_engine()
        return self._engine

    @property
    def selective(self) -> SelectiveDataFetcher:
        if self._selective is None:
            self._selective = SelectiveDataFetcher(client=self.client)
        return self._selective

    def ensure_tables(self) -> None:
        if self._tables_ready or not self.engine:
            return
        with self.engine.begin() as conn:
            for ddl in EXTRA_DDL:
                try:
                    conn.execute(sa_text(ddl))
                except Exception as e:
                    logger.debug("fund DDL skip/warn: %s", e)
            for ddl in BLOCK_TRADE_MIGRATE:
                try:
                    conn.execute(sa_text(ddl))
                except Exception as e:
                    logger.debug("block_trade migrate: %s", e)
            for ddl in DIVIDEND_MIGRATE:
                try:
                    conn.execute(sa_text(ddl))
                except Exception as e:
                    logger.debug("dividend migrate: %s", e)
        self._tables_ready = True

    def list_datasets(self) -> List[Dict[str, Any]]:
        out = []
        for k, meta in FUND_DATASETS.items():
            out.append({
                "dataset": k,
                "table": meta["table"],
                "api": meta["api"],
                "desc": meta["desc"],
                "scope": meta.get("scope"),
            })
        return out

    def resolve_asof(self, trade_date: Optional[str] = None) -> Dict[str, Any]:
        if trade_date:
            d = _parse_date(trade_date)
            if d:
                return {"asof_date": d, "ymd": d.strftime("%Y%m%d")}
        ctx = self.selective.session_context()
        asof: date = ctx["asof_date"]
        return {"asof_date": asof, "ymd": asof.strftime("%Y%m%d"), "session": ctx}

    # ─── 统一入口 ─────────────────────────────────────

    def get(
        self,
        dataset: str,
        ts_code: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        trade_date: Optional[str] = None,
        period: Optional[str] = None,
        source: str = "auto",
        write_back: bool = True,
        limit: int = 500,
        content_type: str = "行业",
    ) -> Dict[str, Any]:
        try:
            meta = self._resolve_meta(dataset)
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        source = (source or "auto").strip().lower()
        if source in ("remote", "datahub", "dh"):
            source = "datahubco"
        if source not in ("auto", "pg", "datahubco"):
            return {"ok": False, "error": f"无效 source={source}"}

        self.ensure_tables()
        asof = self.resolve_asof(trade_date or end_date)
        code = self._norm_code(ts_code) if ts_code else None
        start = _parse_date(start_date)
        end = _parse_date(end_date) or asof["asof_date"]
        if start is None and not period and meta.get("fin"):
            # 财务报表默认近 2 年（网关对大区间会拒）
            start = date(end.year - 1, 1, 1)
        elif start is None and not period and meta.get("fin_simple"):
            # 解禁/质押/增减持/分红/持仓等历史较长：不默认截断，按 ts_code 全量可读
            pass
        elif start is None and not period and meta.get("api") == "disclosure_date":
            # 披露日程默认最近报告期（如 0331/0630/0930/1231）
            end = self._nearest_report_end(end)
            start = end
        elif start is None and meta.get("bars"):
            start = end - timedelta(days=400 if meta.get("api") == "index_monthly" else 120)
        elif start is None and meta.get("api") in ("fund_nav", "fund_adj", "index_dailybasic", "cyq_perf", "cb_daily"):
            start = end - timedelta(days=30)
        elif start is None and meta.get("api") == "cyq_chips":
            # 筹码明细按单日
            start = end
        elif start is None and meta.get("api") == "report_rc":
            start = end - timedelta(days=14)
        elif start is None and meta.get("api") == "index_weight":
            # 权重多为月末快照；默认回看约一季，随后按月末补拉
            start = end - timedelta(days=100)
        elif start is None and meta.get("api") == "new_share":
            start = end - timedelta(days=180)
        elif start is None and not trade_date and meta.get("date_col") == "trade_date":
            start = end - timedelta(days=30)

        limit = max(1, min(int(limit or 500), 5000))
        table = meta["table"]

        if source == "pg":
            df = self._read_pg(meta, code, start, end, period, limit)
            return self._ok(meta, code, df, "pg", False, 0, limit)

        if source == "datahubco":
            remote = self._fetch_remote(
                meta, code, start, end, asof["ymd"], period, content_type
            )
            upserted = 0
            if write_back and remote is not None and not remote.empty:
                upserted = self._upsert(meta, remote)
            df = remote
            if write_back and upserted:
                pg = self._read_pg(meta, code, start, end, period, limit)
                if pg is not None and not pg.empty:
                    df = pg
            return self._ok(
                meta, code, df,
                "datahubco+pg" if upserted else "datahubco",
                True, upserted, limit,
            )

        # auto
        cover = self._pg_count(meta, code, start, end, period)
        need = cover <= 0 or (
            meta.get("date_col") == "trade_date"
            and trade_date
            and cover <= 0
        )
        # 区间模式：行数过少也补
        if not need and start and end and meta.get("date_col") == "trade_date":
            expect = max(1, int((end - start).days * 0.5))
            need = cover < min(expect, 5)

        fetched = False
        upserted = 0
        if need:
            remote = self._fetch_remote(
                meta, code, start, end, asof["ymd"], period, content_type
            )
            fetched = True
            if write_back and remote is not None and not remote.empty:
                upserted = self._upsert(meta, remote)
            df = self._read_pg(meta, code, start, end, period, limit)
            if df is None or df.empty:
                df = remote
            return self._ok(
                meta, code, df,
                "datahubco+pg" if upserted else ("datahubco" if fetched else "pg"),
                fetched, upserted, limit,
            )

        df = self._read_pg(meta, code, start, end, period, limit)
        return self._ok(meta, code, df, "pg", False, 0, limit)

    def get_stock_sector_moneyflow(
        self,
        ts_code: str,
        trade_date: Optional[str] = None,
        levels: Optional[Sequence[str]] = None,
        source: str = "auto",
        write_back: bool = True,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        查个股所属二级/三级行业板块的资金走向。
        依赖 sector_dc_member + sector_dc_index_daily + sector_moneyflow_daily。
        """
        code = self._norm_code(ts_code)
        if not code:
            return {"ok": False, "error": "缺少 ts_code"}
        levels = list(levels or ("东财二级行业", "东财三级行业"))
        asof = self.resolve_asof(trade_date)
        d: date = asof["asof_date"]
        ymd = asof["ymd"]
        source = (source or "auto").strip().lower()
        if source in ("remote", "datahub", "dh"):
            source = "datahubco"

        self.ensure_tables()
        upserted = 0
        notes: List[str] = []

        # 必要时补板块资金全日
        if source in ("auto", "datahubco"):
            try:
                from .sectors import sector_fetcher

                sector_fetcher.ensure_tables()
                need_mf = self._sector_mf_day_count(d) <= 0
                if source == "datahubco" or need_mf:
                    try:
                        mf = sector_fetcher.fetch_moneyflow(ymd, content_type="行业")
                        if write_back and mf is not None and not mf.empty:
                            upserted += int(sector_fetcher.upsert_moneyflow(mf))
                        elif mf is None or mf.empty:
                            notes.append("moneyflow_ind_dc 无数据或接口不可用")
                    except Exception as e:
                        notes.append(f"板块资金远端失败: {e}")
            except Exception as e:
                notes.append(f"sector_fetcher 不可用: {e}")

        sectors = self._find_stock_sectors(code, d, levels)
        if not sectors and source != "pg":
            # 尝试用当日成分表再查一遍（可能 asof 无成分）
            sectors = self._find_stock_sectors_any_recent(code, levels, lookback=30)

        if not sectors:
            return {
                "ok": True,
                "ts_code": code,
                "trade_date": d.isoformat(),
                "levels": list(levels),
                "sectors": [],
                "moneyflow": [],
                "upserted": upserted,
                "count": 0,
                "note": "未在 sector_dc_member 中找到该股所属二级/三级板块；请先同步板块成分。",
                "notes": notes,
            }

        sector_codes = [s["sector_code"] for s in sectors]
        # 成分日可能与 asof 不一致：先用 asof，再回退到成分所在日 / 最近有资金的日期
        mf_dates = sorted({
            _parse_date(s.get("trade_date")) or d for s in sectors
        } | {d})
        mf_df = pd.DataFrame()
        for md in sorted(mf_dates, reverse=True):
            mf_df = self._read_sector_moneyflow(sector_codes, md)
            if mf_df is not None and not mf_df.empty:
                d = md
                break
        if mf_df is None or mf_df.empty:
            mf_df = self._read_sector_moneyflow_latest(sector_codes)
        records = self._df_records(mf_df, limit=limit)

        # 合并层级信息
        level_map = {s["sector_code"]: s for s in sectors}
        for r in records:
            sc = str(r.get("ts_code") or "")
            info = level_map.get(sc) or {}
            r["industry_level"] = info.get("industry_level")
            r["sector_name"] = info.get("sector_name") or r.get("name")

        return {
            "ok": True,
            "ts_code": code,
            "trade_date": d.isoformat(),
            "levels": list(levels),
            "sectors": sectors,
            "moneyflow": records,
            "count": len(records),
            "upserted": upserted,
            "source_used": "datahubco+pg" if upserted else "pg",
            "notes": notes or None,
        }

    # ─── fetch ────────────────────────────────────────

    def _fetch_remote(
        self,
        meta: Dict[str, Any],
        ts_code: Optional[str],
        start: Optional[date],
        end: date,
        asof_ymd: str,
        period: Optional[str],
        content_type: str,
    ) -> pd.DataFrame:
        api = meta["api"]
        name = meta.get("name") or ""
        kwargs: Dict[str, Any] = {}

        if meta.get("fin_simple"):
            if not ts_code:
                raise ValueError(f"{api} 需要 ts_code")
            kwargs["ts_code"] = ts_code
            if period:
                kwargs["period"] = _to_ymd(period) or period
            if api == "fina_mainbz":
                # content_type: P产品 / D地区 / I行业；也接受中文
                ct = (content_type or "P").strip()
                mapping = {"产品": "P", "地区": "D", "行业": "I", "p": "P", "d": "D", "i": "I"}
                kwargs["type"] = mapping.get(ct, mapping.get(ct.lower(), ct.upper() if ct.upper() in ("P", "D", "I") else "P"))
            # fund_portfolio：网关拒显式大 limit，不传 limit 由接口默认返回

        elif meta.get("fin"):
            if ts_code:
                kwargs["ts_code"] = ts_code
            if period:
                kwargs["period"] = _to_ymd(period) or period
            else:
                # 网关拒过大区间：按年切片后合并
                return self._fetch_fin_chunked(api, ts_code, start, end, meta)

        elif api == "disclosure_date":
            # 接口按报告期 end_date 拉全市场（或单票）
            ed = _to_ymd(period) if period else None
            if not ed:
                ed = self._nearest_report_end(end or date.today()).strftime("%Y%m%d")
            kwargs["end_date"] = ed
            if ts_code:
                kwargs["ts_code"] = ts_code

        elif api == "index_basic":
            # content_type 可传市场 SSE/SZSE/CSI/SW 等
            ct = (content_type or "").strip()
            if ct and ct not in ("行业", "概念") and len(ct) <= 16:
                kwargs["market"] = ct.upper() if ct.upper() in (
                    "SSE", "SZSE", "CSI", "SW", "MSCI", "OTH", "CNI", "CICC", "NH",
                ) or ct.isalpha() else ct
            if ts_code:
                kwargs["ts_code"] = ts_code
            kwargs["limit"] = 8000

        elif api == "fund_basic":
            # content_type 可传市场 E场内 / O场外
            ct = (content_type or "").strip().upper()
            if ct in ("E", "O"):
                kwargs["market"] = ct
            elif ct in ("场内", "ETF"):
                kwargs["market"] = "E"
            elif ct in ("场外", "OF"):
                kwargs["market"] = "O"
            if ts_code:
                kwargs["ts_code"] = ts_code
            kwargs["limit"] = 8000

        elif api == "index_classify":
            # content_type: SW2021 / SW2014；或 L1/L2/L3 表示层级
            ct = (content_type or "SW2021").strip()
            if ct.upper() in ("L1", "L2", "L3"):
                kwargs["level"] = ct.upper()
                kwargs["src"] = "SW2021"
            else:
                kwargs["src"] = ct or "SW2021"
                # period 可附带层级
                if period and str(period).upper() in ("L1", "L2", "L3"):
                    kwargs["level"] = str(period).upper()

        elif api in ("index_weekly", "index_monthly", "fund_nav", "fund_adj"):
            if not ts_code:
                raise ValueError(f"{api} 需要 ts_code")
            kwargs["ts_code"] = ts_code
            if start and end:
                kwargs["start_date"] = start.strftime("%Y%m%d")
                kwargs["end_date"] = end.strftime("%Y%m%d")
            else:
                kwargs["end_date"] = end.strftime("%Y%m%d")
                kwargs["start_date"] = (end - timedelta(days=30)).strftime("%Y%m%d")

        elif api == "index_dailybasic":
            if ts_code:
                kwargs["ts_code"] = ts_code
                if start and end:
                    kwargs["start_date"] = start.strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                else:
                    kwargs["trade_date"] = asof_ymd
            else:
                kwargs["trade_date"] = asof_ymd
                kwargs["limit"] = 8000

        elif api == "index_weight":
            if not ts_code:
                raise ValueError("index_weight 需要 ts_code（指数代码，如 000300.SH）")
            # 接口字段 index_code；区间常为空，按月末 trade_date 补拉
            return self._fetch_index_weight(ts_code, start, end, asof_ymd, meta)

        elif api == "cyq_perf":
            if ts_code:
                kwargs["ts_code"] = ts_code
                if start and end and (end - start).days > 0:
                    kwargs["start_date"] = start.strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                else:
                    kwargs["trade_date"] = asof_ymd
            else:
                kwargs["trade_date"] = asof_ymd
                kwargs["limit"] = 8000
                self.client.timeout = max(getattr(self.client, "timeout", 30), 90)

        elif api == "cyq_chips":
            if not ts_code:
                raise ValueError("cyq_chips 需要 ts_code")
            kwargs["ts_code"] = ts_code
            kwargs["trade_date"] = asof_ymd if not (start and end and start == end) else start.strftime("%Y%m%d")

        elif api == "report_rc":
            if ts_code:
                kwargs["ts_code"] = ts_code
            s = start or (end - timedelta(days=14))
            kwargs["start_date"] = s.strftime("%Y%m%d")
            kwargs["end_date"] = end.strftime("%Y%m%d")
            # 不传超大 limit，网关对研报有默认上限

        elif api == "ths_member":
            if ts_code:
                kwargs["ts_code"] = ts_code
            else:
                kwargs["limit"] = 8000

        elif api == "cb_basic":
            if ts_code:
                kwargs["ts_code"] = ts_code
            kwargs["limit"] = 8000

        elif api == "cb_daily":
            if ts_code:
                kwargs["ts_code"] = ts_code
                if start and end:
                    kwargs["start_date"] = start.strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                else:
                    kwargs["trade_date"] = asof_ymd
            else:
                kwargs["trade_date"] = asof_ymd
                kwargs["limit"] = 8000

        elif api == "fut_basic":
            ct = (content_type or "").strip().upper()
            if ct in ("DCE", "SHFE", "CZCE", "CFFEX", "INE", "GFEX"):
                kwargs["exchange"] = ct
            if ts_code:
                kwargs["ts_code"] = ts_code
            kwargs["limit"] = 8000

        elif api == "margin":
            kwargs["trade_date"] = asof_ymd if not start else (end.strftime("%Y%m%d"))
            # 若给了区间，按日拉取较慢；优先单日
            if start and (end - start).days > 0:
                frames = []
                d = start
                while d <= end:
                    try:
                        part = self.client.query(api, trade_date=d.strftime("%Y%m%d"))
                        if part is not None and not part.empty:
                            frames.append(part)
                    except Exception as e:
                        logger.debug("margin %s: %s", d, e)
                    d += timedelta(days=1)
                df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                return self._normalize(df, meta)

        elif api == "sector_moneyflow" or api == "moneyflow_ind_dc":
            kwargs["trade_date"] = asof_ymd
            kwargs["content_type"] = content_type or "行业"

        elif api in ("top_list", "block_trade", "hk_hold", "top_inst"):
            if api in ("top_list", "top_inst"):
                # 网关要求必填 trade_date；区间则按日循环
                if start and end and (end - start).days > 0:
                    frames = []
                    d = start
                    while d <= end:
                        try:
                            part = self.client.query(api, trade_date=d.strftime("%Y%m%d"))
                            if part is not None and not part.empty:
                                frames.append(part)
                        except Exception as e:
                            logger.debug("%s %s: %s", api, d, e)
                        d += timedelta(days=1)
                    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                    out = self._normalize(df, meta)
                    if ts_code and not out.empty and "ts_code" in out.columns:
                        out = out.loc[out["ts_code"].astype(str).str.upper() == ts_code.upper()]
                    return out.reset_index(drop=True)
                kwargs["trade_date"] = asof_ymd
                if ts_code:
                    # 全日拉取后过滤（单日 + 个股）
                    pass
            elif api == "hk_hold":
                if ts_code and start and end:
                    kwargs["ts_code"] = ts_code
                    kwargs["start_date"] = start.strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                elif ts_code:
                    kwargs["ts_code"] = ts_code
                    kwargs["start_date"] = (end - timedelta(days=14)).strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                else:
                    # 全日：优先沪深北向；空则回退不限交易所（含港股通）
                    frames = []
                    for ex in ("SH", "SZ"):
                        try:
                            part = self.client.query(
                                api, trade_date=asof_ymd, exchange=ex
                            )
                            if part is not None and not part.empty:
                                frames.append(part)
                        except Exception as e:
                            logger.debug("hk_hold %s: %s", ex, e)
                    if not frames:
                        try:
                            part = self.client.query(api, trade_date=asof_ymd)
                            if part is not None and not part.empty:
                                frames.append(part)
                        except Exception as e:
                            logger.debug("hk_hold all: %s", e)
                    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                    return self._normalize(df, meta)
            else:  # block_trade
                if ts_code and start and end:
                    kwargs["ts_code"] = ts_code
                    kwargs["start_date"] = start.strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                elif ts_code:
                    kwargs["ts_code"] = ts_code
                    kwargs["trade_date"] = asof_ymd
                else:
                    kwargs["trade_date"] = asof_ymd

        elif api == "moneyflow" and not ts_code:
            # 全日资金流向：接口按 trade_date 返回全市场，默认只给 5000 行，抬高上限一次取完。
            kwargs["trade_date"] = asof_ymd
            kwargs["limit"] = 8000
            self.client.timeout = max(getattr(self.client, "timeout", 30), 90)
        elif api in ("moneyflow", "margin_detail"):
            if not ts_code:
                raise ValueError(f"{api} 需要 ts_code")
            kwargs["ts_code"] = ts_code
            if start and end:
                kwargs["start_date"] = start.strftime("%Y%m%d")
                kwargs["end_date"] = end.strftime("%Y%m%d")
            else:
                kwargs["trade_date"] = asof_ymd

        elif api in ("daily_basic", "adj_factor"):
            if ts_code:
                kwargs["ts_code"] = ts_code
                if start and end:
                    kwargs["start_date"] = start.strftime("%Y%m%d")
                    kwargs["end_date"] = end.strftime("%Y%m%d")
                else:
                    kwargs["trade_date"] = asof_ymd
            else:
                # 全日：按日拉（量大，区间默认只拉 end/asof 一日）
                if start and end and (end - start).days > 0 and (end - start).days <= 7:
                    frames = []
                    d = start
                    while d <= end:
                        try:
                            part = self.client.query(api, trade_date=d.strftime("%Y%m%d"))
                            if part is not None and not part.empty:
                                frames.append(part)
                        except Exception as e:
                            logger.debug("%s %s: %s", api, d, e)
                        d += timedelta(days=1)
                    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                    return self._normalize(df, meta)
                kwargs["trade_date"] = asof_ymd
                kwargs["limit"] = 8000
                self.client.timeout = max(getattr(self.client, "timeout", 30), 90)

        elif api == "stk_limit":
            if ts_code:
                kwargs["ts_code"] = ts_code
            kwargs["trade_date"] = asof_ymd if not (start and end and start == end) else start.strftime("%Y%m%d")
            if start and end and (end - start).days > 0:
                frames = []
                d = start
                while d <= end:
                    try:
                        part_kw = {"trade_date": d.strftime("%Y%m%d")}
                        if ts_code:
                            part_kw["ts_code"] = ts_code
                        part = self.client.query(api, **part_kw)
                        if part is not None and not part.empty:
                            frames.append(part)
                    except Exception as e:
                        logger.debug("stk_limit %s: %s", d, e)
                    d += timedelta(days=1)
                df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
                return self._normalize(df, meta)

        elif api == "limit_list_d":
            # content_type 可传 U/D/Z；空则三类都拉
            types = []
            ct = (content_type or "").strip().upper()
            if ct in ("U", "D", "Z"):
                types = [ct]
            else:
                types = ["U", "D", "Z"]
            days = []
            if start and end and (end - start).days > 0:
                d = start
                while d <= end:
                    days.append(d)
                    d += timedelta(days=1)
            else:
                days = [end]
            frames = []
            for d in days:
                for kind in types:
                    try:
                        part = self.client.query(
                            api, trade_date=d.strftime("%Y%m%d"), limit_type=kind
                        )
                        if part is not None and not part.empty:
                            part = part.copy()
                            if "limit" not in part.columns and "limit_type" not in part.columns:
                                part["limit"] = kind
                            frames.append(part)
                    except Exception as e:
                        logger.debug("limit_list_d %s %s: %s", d, kind, e)
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            out = self._normalize(df, meta)
            if ts_code and not out.empty and "ts_code" in out.columns:
                out = out.loc[out["ts_code"].astype(str).str.upper() == ts_code.upper()]
            return out.reset_index(drop=True)

        elif api == "suspend_d":
            if ts_code:
                kwargs["ts_code"] = ts_code
            if start and end:
                kwargs["start_date"] = start.strftime("%Y%m%d")
                kwargs["end_date"] = end.strftime("%Y%m%d")
            else:
                kwargs["trade_date"] = asof_ymd

        elif api == "new_share":
            s = start or (end - timedelta(days=180))
            kwargs["start_date"] = s.strftime("%Y%m%d")
            kwargs["end_date"] = end.strftime("%Y%m%d")

        elif api == "top10_floatholders":
            if not ts_code:
                raise ValueError("top10_holders 需要 ts_code")
            kwargs["ts_code"] = ts_code
            if period:
                kwargs["period"] = _to_ymd(period) or period
            elif start and end:
                kwargs["start_date"] = start.strftime("%Y%m%d")
                kwargs["end_date"] = end.strftime("%Y%m%d")
            else:
                # 最近年报期末
                kwargs["period"] = f"{end.year - 1}1231" if end.month < 4 else f"{end.year}0630"

        # 尝试主 API，失败则 fallback
        apis = [api] + list(meta.get("api_fallback") or [])
        # top10: 先试正式名再流通
        if name == "top10_holders" or meta.get("table") == "stock_top10_holders":
            apis = ["top10_holders", "top10_floatholders"]

        last_err = None
        df = pd.DataFrame()
        used_api = api
        for a in apis:
            try:
                if a == "moneyflow_ind_dc":
                    df = self.client.query(a, trade_date=asof_ymd, content_type=content_type or "行业")
                else:
                    use_kwargs = dict(kwargs)
                    if a == "top10_holders" and "start_date" in use_kwargs:
                        # top10_holders 常只认 period
                        use_kwargs.pop("start_date", None)
                        use_kwargs.pop("end_date", None)
                        if "period" not in use_kwargs:
                            use_kwargs["period"] = f"{end.year}1231"
                    df = self.client.query(a, **use_kwargs)
                used_api = a
                if df is not None and not df.empty:
                    break
                # 空结果也算成功（如无业绩预告）
                if df is not None:
                    break
            except Exception as e:
                last_err = e
                logger.warning("fetch %s failed: %s", a, e)
                continue

        if (df is None or df.empty) and last_err and apis and used_api not in apis:
            raise RuntimeError(str(last_err))

        out = self._normalize(df if df is not None else pd.DataFrame(), meta)
        if not out.empty and meta.get("table") == "stock_top10_holders":
            out["holder_kind"] = "float" if used_api == "top10_floatholders" else "total"
        # top_list / top_inst 全日拉取后按个股过滤
        if (
            not out.empty
            and ts_code
            and meta.get("api") in ("top_list", "top_inst")
            and "ts_code" in out.columns
        ):
            out = out.loc[out["ts_code"].astype(str).str.upper() == ts_code.upper()].reset_index(drop=True)
        return out

    def _fetch_index_weight(
        self,
        ts_code: str,
        start: Optional[date],
        end: date,
        asof_ymd: str,
        meta: Dict[str, Any],
    ) -> pd.DataFrame:
        """指数权重多为月末快照；按区间 start/end 的月末 trade_date 逐日拉取。"""
        index_code = ts_code.upper()
        frames: List[pd.DataFrame] = []

        # 先试区间（多数窗口会空）
        if start and end and (end - start).days > 0:
            try:
                part = self.client.query(
                    "index_weight",
                    index_code=index_code,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
                if part is not None and not part.empty:
                    return self._normalize(part, meta)
            except Exception as e:
                logger.debug("index_weight range: %s", e)

        # 月末候选
        ends = self._month_end_dates(start or (end - timedelta(days=100)), end)
        # 同时尝试 asof 本身
        ymds = {d.strftime("%Y%m%d") for d in ends}
        ymds.add(asof_ymd)
        ymds.add(end.strftime("%Y%m%d"))
        for ymd in sorted(ymds, reverse=True):
            try:
                part = self.client.query("index_weight", index_code=index_code, trade_date=ymd)
                if part is not None and not part.empty:
                    frames.append(part)
                    # 有一个月末成功即可（全成分）；若区间跨多月可继续
                    if start is None or (end - (start or end)).days <= 40:
                        break
            except Exception as e:
                logger.debug("index_weight %s %s: %s", index_code, ymd, e)
        if not frames:
            return pd.DataFrame()
        return self._normalize(pd.concat(frames, ignore_index=True), meta)

    @staticmethod
    def _month_end_dates(start: date, end: date) -> List[date]:
        """生成 [start, end] 内每月最后一天（日历月末）。"""
        out: List[date] = []
        y, m = start.year, start.month
        while True:
            if m == 12:
                last = date(y, 12, 31)
                ny, nm = y + 1, 1
            else:
                last = date(y, m + 1, 1) - timedelta(days=1)
                ny, nm = y, m + 1
            if last >= start and last <= end:
                out.append(last)
            if date(ny, nm, 1) > end:
                break
            y, m = ny, nm
        return out

    def _fetch_fin_chunked(
        self,
        api: str,
        ts_code: Optional[str],
        start: Optional[date],
        end: date,
        meta: Dict[str, Any],
    ) -> pd.DataFrame:
        if not ts_code:
            return pd.DataFrame()
        start = start or date(end.year - 1, 1, 1)
        frames: List[pd.DataFrame] = []
        y = start.year
        while y <= end.year:
            s = max(start, date(y, 1, 1)).strftime("%Y%m%d")
            e = min(end, date(y, 12, 31)).strftime("%Y%m%d")
            try:
                part = self.client.query(api, ts_code=ts_code, start_date=s, end_date=e)
                if part is not None and not part.empty:
                    frames.append(part)
            except Exception as err:
                logger.warning("%s %s %s-%s: %s", api, ts_code, s, e, err)
                # 再试 period 年报/中报
                for p in (f"{y}1231", f"{y}0630", f"{y}0930", f"{y}0331"):
                    try:
                        part = self.client.query(api, ts_code=ts_code, period=p)
                        if part is not None and not part.empty:
                            frames.append(part)
                    except Exception:
                        pass
            y += 1
        if not frames:
            return pd.DataFrame()
        return self._normalize(pd.concat(frames, ignore_index=True), meta)

    def _normalize(self, df: pd.DataFrame, meta: Dict[str, Any]) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        use = df.copy()
        use.columns = [str(c).lower() for c in use.columns]

        for col in (
            "trade_date", "ann_date", "f_ann_date", "end_date", "first_ann_date",
            "ipo_date", "issue_date", "record_date", "ex_date", "pay_date",
            "div_listdate", "imp_ann_date", "base_date", "pre_date", "actual_date",
            "float_date", "report_date", "value_date", "maturity_date",
            "list_date", "delist_date", "conv_start_date", "conv_end_date",
            "conv_stop_date", "last_ddate", "found_date", "due_date",
        ):
            if col in use.columns:
                use[col] = _parse_dates(use[col])

        if "ts_code" in use.columns:
            use["ts_code"] = use["ts_code"].astype(str).str.upper()

        # 三大报表：report_type 不可空；varchar(1) 字段清洗 nan
        if meta["table"] in ("fin_cashflow", "fin_income", "fin_balancesheet"):
            if "report_type" not in use.columns:
                use["report_type"] = "1"
            else:
                use["report_type"] = (
                    use["report_type"].astype(object)
                    .where(pd.notnull(use["report_type"]), "1")
                    .map(lambda x: "1" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:1])
                )
            for col in ("comp_type", "end_type", "update_flag"):
                if col in use.columns:
                    use[col] = use[col].astype(object).where(pd.notnull(use[col]), None)
                    use[col] = use[col].map(
                        lambda x: None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:1]
                    )

        # 资产负债表：接口字段名 → 库表列名
        if meta["table"] == "fin_balancesheet":
            bs_rename = {
                "prepayment": "pre_payment",
                "undistr_porfit": "retained_earnings",
                "st_borr": "st_borrow",
                "lt_borr": "lt_borrow",
                "cb_borr": "pay_borrow",
                "trading_fl": "trad_liab",
                "bond_payable": "bonds_payable",
                "oth_comp_income": "oth_compreh_income",
                "ordin_risk_reser": "prov_nom_risks",
            }
            use = use.rename(columns={k: v for k, v in bs_rename.items() if k in use.columns})

        # 分红：stk_co_rate → stk_so_rate；补齐 PK 列
        if meta["table"] == "fin_dividend":
            if "stk_so_rate" not in use.columns and "stk_co_rate" in use.columns:
                use = use.rename(columns={"stk_co_rate": "stk_so_rate"})
            if "div_proc" not in use.columns:
                use["div_proc"] = ""
            else:
                use["div_proc"] = (
                    use["div_proc"].astype(object)
                    .where(pd.notnull(use["div_proc"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:30])
                )
            if "ann_date" in use.columns and "end_date" in use.columns:
                use["ann_date"] = use["ann_date"].where(pd.notnull(use["ann_date"]), use["end_date"])

        # 业绩快报：接口 diluted_roe / perf_summary → 库表 weighted_roe / performance_summary
        if meta["table"] == "fin_express":
            ex_rename = {
                "diluted_roe": "weighted_roe",
                "perf_summary": "performance_summary",
            }
            use = use.rename(columns={k: v for k, v in ex_rename.items() if k in use.columns})

        # 主营构成
        if meta["table"] == "fin_mainbz":
            if "bz_item" not in use.columns:
                use["bz_item"] = ""
            else:
                use["bz_item"] = (
                    use["bz_item"].astype(object)
                    .where(pd.notnull(use["bz_item"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:200])
                )
            for c in ("bz_sales", "bz_profit", "bz_cost"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 审计意见
        if meta["table"] == "fin_audit":
            if "audit_agency" not in use.columns:
                use["audit_agency"] = ""
            else:
                use["audit_agency"] = (
                    use["audit_agency"].astype(object)
                    .where(pd.notnull(use["audit_agency"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:200])
                )
            if "audit_fees" in use.columns:
                use["audit_fees"] = pd.to_numeric(use["audit_fees"], errors="coerce")

        # forecast: type 字段与 SQL 保留字冲突已用 type 列名
        if meta["table"] == "fin_forecast" and "type" in use.columns:
            pass

        # block_trade vol
        if "vol" in use.columns:
            use["vol"] = pd.to_numeric(use["vol"], errors="coerce")

        # hk_hold
        if meta["table"] == "stock_hk_hold":
            if "ts_code" not in use.columns and "code" in use.columns:
                # code 可能是 600519，需补后缀
                use["ts_code"] = use["code"].map(self._norm_code)
            for c in ("vol", "ratio"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # moneyflow numeric
        if meta["table"] == "stock_moneyflow_daily":
            for c in use.columns:
                if c not in ("ts_code", "trade_date"):
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # sector moneyflow
        if meta["table"] == "sector_moneyflow_daily":
            if "content_type" not in use.columns:
                use["content_type"] = "行业"
            if "rank" in use.columns and "rank_no" not in use.columns:
                use = use.rename(columns={"rank": "rank_no"})

        # 涨跌停榜：接口字段 limit → limit_type
        if meta["table"] == "stock_limit_list_daily":
            if "limit" in use.columns and "limit_type" not in use.columns:
                use = use.rename(columns={"limit": "limit_type"})
            if "limit_type" in use.columns:
                use["limit_type"] = (
                    use["limit_type"].astype(object)
                    .where(pd.notnull(use["limit_type"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:8])
                )
            for c in (
                "close", "pct_chg", "amount", "limit_amount", "float_mv", "total_mv",
                "turnover_ratio", "fd_amount", "open_times", "limit_times",
            ):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 停复牌：suspend_timing → suspend_reason
        if meta["table"] == "ref_suspend_d":
            if "suspend_reason" not in use.columns and "suspend_timing" in use.columns:
                use["suspend_reason"] = use["suspend_timing"]
            if "suspend_type" in use.columns:
                use["suspend_type"] = use["suspend_type"].astype(object).where(
                    pd.notnull(use["suspend_type"]), None
                )
                use["suspend_type"] = use["suspend_type"].map(
                    lambda x: None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:10]
                )

        # 龙虎榜席位
        if meta["table"] == "stock_top_inst_daily":
            if "side" not in use.columns:
                use["side"] = ""
            else:
                use["side"] = (
                    use["side"].astype(object)
                    .where(pd.notnull(use["side"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:8])
                )
            if "exalter" in use.columns:
                use["exalter"] = (
                    use["exalter"].astype(object)
                    .where(pd.notnull(use["exalter"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:128])
                )
            for c in ("buy", "buy_rate", "sell", "sell_rate", "net_buy"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 每日指标 / 复权 / 涨跌停价
        if meta["table"] in ("stock_daily_basic", "stock_adj_factor", "ref_stk_limit_daily"):
            for c in use.columns:
                if c not in ("ts_code", "trade_date"):
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 新股
        if meta["table"] == "ref_new_share":
            if "sub_code" not in use.columns and "ts_code" in use.columns:
                use["sub_code"] = use["ts_code"].astype(str).str.replace(r"\..*$", "", regex=True)
            for c in ("amount", "market_amount", "price", "pe", "limit_amount", "funds", "ballot"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 指数主档日期
        if meta["table"] == "ref_index_basic":
            for col in ("base_date", "list_date", "exp_date"):
                if col in use.columns:
                    use[col] = _parse_dates(use[col])

        # 指数周/月线
        if meta["table"] in ("index_weekly", "index_monthly"):
            for c in ("open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 指数每日估值
        if meta["table"] == "index_daily_basic":
            for c in use.columns:
                if c not in ("ts_code", "trade_date"):
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 行业分类
        if meta["table"] == "ref_index_classify":
            if "src" not in use.columns:
                use["src"] = "SW2021"
            else:
                use["src"] = (
                    use["src"].astype(object)
                    .where(pd.notnull(use["src"]), "SW2021")
                    .map(lambda x: "SW2021" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:32])
                )
            if "index_code" in use.columns:
                use["index_code"] = use["index_code"].astype(str).str.upper()

        # 指数权重
        if meta["table"] == "index_weight":
            if "index_code" in use.columns:
                use["index_code"] = use["index_code"].astype(str).str.upper()
            if "con_code" in use.columns:
                use["con_code"] = use["con_code"].astype(str).str.upper()
            if "weight" in use.columns:
                use["weight"] = pd.to_numeric(use["weight"], errors="coerce")

        # 基金主档
        if meta["table"] == "ref_fund_basic":
            for col in ("found_date", "due_date", "list_date", "issue_date", "delist_date"):
                if col in use.columns:
                    use[col] = _parse_dates(use[col])
            for c in ("issue_amount", "m_fee", "c_fee"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 基金净值
        if meta["table"] == "fund_nav":
            if "nav_date" in use.columns:
                use["nav_date"] = _parse_dates(use["nav_date"])
            for c in ("unit_nav", "accum_nav", "adj_nav"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 基金分红
        if meta["table"] == "fund_div":
            for col in (
                "ann_date", "imp_anndate", "base_date", "record_date", "ex_date",
                "pay_date", "earpay_date", "net_ex_date", "account_date",
            ):
                if col in use.columns:
                    use[col] = _parse_dates(use[col])
            if "ann_date" not in use.columns:
                use["ann_date"] = use["ex_date"] if "ex_date" in use.columns else None
            if "ex_date" not in use.columns:
                use["ex_date"] = use["ann_date"]
            # PK 不可空
            if "ann_date" in use.columns:
                use["ann_date"] = use["ann_date"].where(
                    pd.notnull(use["ann_date"]),
                    use["ex_date"] if "ex_date" in use.columns else None,
                )
            if "ex_date" in use.columns:
                use["ex_date"] = use["ex_date"].where(
                    pd.notnull(use["ex_date"]),
                    use["ann_date"] if "ann_date" in use.columns else date(1970, 1, 1),
                )
            for c in ("div_cash", "base_unit", "ear_distr", "ear_amount"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 基金持仓
        if meta["table"] == "fund_portfolio":
            if "symbol" in use.columns:
                use["symbol"] = use["symbol"].astype(str).str.upper()
            for c in ("mkv", "amount", "stk_mkv_ratio", "stk_float_ratio"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 基金复权
        if meta["table"] == "fund_adj":
            if "adj_factor" in use.columns:
                use["adj_factor"] = pd.to_numeric(use["adj_factor"], errors="coerce")

        # 限售解禁
        if meta["table"] == "stock_share_float":
            if "holder_name" not in use.columns:
                use["holder_name"] = ""
            else:
                use["holder_name"] = (
                    use["holder_name"].astype(object)
                    .where(pd.notnull(use["holder_name"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:200])
                )
            if "float_date" in use.columns and "ann_date" in use.columns:
                use["float_date"] = use["float_date"].where(pd.notnull(use["float_date"]), use["ann_date"])
            for c in ("float_share", "float_ratio"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 股权质押
        if meta["table"] == "stock_pledge_stat":
            for c in ("pledge_count", "unrest_pledge", "rest_pledge", "total_share", "pledge_ratio"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 股东增减持
        if meta["table"] == "stock_holdertrade":
            if "holder_name" not in use.columns:
                use["holder_name"] = ""
            else:
                use["holder_name"] = (
                    use["holder_name"].astype(object)
                    .where(pd.notnull(use["holder_name"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:200])
                )
            if "in_de" not in use.columns:
                use["in_de"] = ""
            else:
                use["in_de"] = (
                    use["in_de"].astype(object)
                    .where(pd.notnull(use["in_de"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:8])
                )
            for c in ("change_vol", "change_ratio", "after_share", "after_ratio", "avg_price", "total_share"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 筹码摘要
        if meta["table"] == "stock_cyq_perf":
            for c in use.columns:
                if c not in ("ts_code", "trade_date"):
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 筹码明细
        if meta["table"] == "stock_cyq_chips":
            for c in ("price", "percent"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")
            if "price" in use.columns:
                use = use.dropna(subset=["price"])

        # 券商研报
        if meta["table"] == "stock_report_rc":
            if "org_name" not in use.columns:
                use["org_name"] = ""
            else:
                use["org_name"] = (
                    use["org_name"].astype(object)
                    .where(pd.notnull(use["org_name"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:100])
                )
            if "report_title" not in use.columns:
                use["report_title"] = ""
            else:
                use["report_title"] = (
                    use["report_title"].astype(object)
                    .where(pd.notnull(use["report_title"]), "")
                    .map(lambda x: "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)[:300])
                )
            for c in (
                "op_rt", "op_pr", "tp", "np", "eps", "pe", "rd", "roe",
                "ev_ebitda", "max_price", "min_price",
            ):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 同花顺概念成分
        if meta["table"] == "ths_member":
            if "con_code" in use.columns:
                use["con_code"] = use["con_code"].astype(str).str.upper()

        # 可转债基础
        if meta["table"] == "cb_basic":
            for c in (
                "maturity", "par", "issue_price", "issue_size", "remain_size",
                "coupon_rate", "add_rate", "pay_per_year", "first_conv_price", "conv_price",
            ):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 可转债日线
        if meta["table"] == "cb_daily":
            for c in ("pre_close", "open", "high", "low", "close", "change", "pct_chg", "vol", "amount"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 期货主档
        if meta["table"] == "fut_basic":
            for c in ("multiplier", "per_unit"):
                if c in use.columns:
                    use[c] = pd.to_numeric(use[c], errors="coerce")

        # 去重
        conflict = [x.strip() for x in meta["conflict"].split(",")]
        have = [c for c in conflict if c in use.columns]
        if have:
            use = use.drop_duplicates(subset=have, keep="last")

        return use.reset_index(drop=True)

    # ─── upsert / read ────────────────────────────────

    def _upsert(self, meta: Dict[str, Any], df: pd.DataFrame) -> int:
        if df is None or df.empty or not self.engine:
            return 0
        self.ensure_tables()
        table = meta["table"]
        cols = self._table_cols(table)
        use_cols = [c for c in cols if c in df.columns]
        if not use_cols:
            return 0

        # 大宗：先确保已迁移掉旧 PK，再按明细 upsert / replace
        if meta.get("replace_mode") == "day_stock" and "trade_date" in df.columns:
            return self._replace_block_trade(df)

        conflict = meta["conflict"]
        pk = [x.strip() for x in conflict.split(",")]
        # 若 conflict 列不全在 df，退化为简单 insert ignore
        if any(c not in df.columns for c in pk):
            logger.warning("upsert %s missing pk cols %s", table, pk)
            return 0

        rows = df[use_cols].where(pd.notnull(df[use_cols]), None)
        for c in rows.columns:
            if str(rows[c].dtype) == "Int64":
                rows[c] = rows[c].astype(object).where(rows[c].notna(), None)
        # 彻底清掉 float nan / pandas NA，避免写入 varchar 变成 "nan"
        records = []
        for rec in rows.to_dict(orient="records"):
            clean = {}
            for k, v in rec.items():
                if v is None:
                    clean[k] = None
                elif isinstance(v, float) and pd.isna(v):
                    clean[k] = None
                else:
                    try:
                        if pd.isna(v):
                            clean[k] = None
                            continue
                    except (ValueError, TypeError):
                        pass
                    clean[k] = v
            records.append(clean)

        placeholders = ", ".join(f":{c}" for c in use_cols)
        col_list = ", ".join(use_cols)
        updates = ", ".join(f"{c}=EXCLUDED.{c}" for c in use_cols if c not in pk)
        if "updated_at" not in use_cols and table in (
            "stock_hk_hold", "stock_top10_holders", "sector_moneyflow_daily",
            "stock_limit_list_daily", "stock_top_inst_daily",
            "fin_mainbz", "fin_audit", "fin_disclosure_date",
            "ref_index_classify", "fund_div", "fund_portfolio",
            "stock_share_float", "stock_pledge_stat", "stock_holdertrade",
            "stock_report_rc", "ths_member",
        ):
            updates = (updates + ", " if updates else "") + "updated_at=CURRENT_TIMESTAMP"
        if not updates:
            sql = f"""
            INSERT INTO {table} ({col_list})
            VALUES ({placeholders})
            ON CONFLICT ({', '.join(pk)}) DO NOTHING
            """
        else:
            sql = f"""
            INSERT INTO {table} ({col_list})
            VALUES ({placeholders})
            ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}
            """
        try:
            with self.engine.begin() as conn:
                conn.execute(sa_text(sql), records)
            return len(records)
        except Exception as e:
            if table == "stock_block_trade":
                logger.warning("block_trade upsert fallback replace: %s", e)
                return self._replace_block_trade(df)
            logger.error("upsert %s failed: %s", table, e)
            raise

    def _replace_block_trade(self, df: pd.DataFrame) -> int:
        if df.empty or not self.engine:
            return 0
        self.ensure_tables()
        cols = ["ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller"]
        use_cols = [c for c in cols if c in df.columns]
        rows = df[use_cols].copy()
        if "vol" in rows.columns:
            rows["vol"] = pd.to_numeric(rows["vol"], errors="coerce").round().astype("Int64")
        if "price" in rows.columns:
            rows["price"] = pd.to_numeric(rows["price"], errors="coerce").round(4)
        dedupe_cols = [c for c in ("ts_code", "trade_date", "price", "vol", "buyer", "seller") if c in rows.columns]
        if dedupe_cols:
            rows = rows.drop_duplicates(subset=dedupe_cols, keep="last")
        records = []
        for rec in rows.to_dict(orient="records"):
            clean = {}
            for k, v in rec.items():
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    clean[k] = None
                elif hasattr(v, "item"):
                    try:
                        clean[k] = v.item()
                    except Exception:
                        clean[k] = None if pd.isna(v) else v
                else:
                    clean[k] = v
            records.append(clean)
        if not records:
            return 0
        placeholders = ", ".join(f":{c}" for c in use_cols)
        col_list = ", ".join(use_cols)
        sql = f"""
        INSERT INTO stock_block_trade ({col_list})
        VALUES ({placeholders})
        ON CONFLICT (ts_code, trade_date, price, vol, buyer, seller)
        DO UPDATE SET amount = EXCLUDED.amount
        """
        try:
            with self.engine.begin() as conn:
                conn.execute(sa_text(sql), records)
            return len(records)
        except Exception as e:
            # 唯一约束名因 hypertable 分片而不同：退化为逐行 upsert / ignore
            logger.warning("block_trade batch upsert failed, row-wise: %s", e)
            n = 0
            with self.engine.begin() as conn:
                for rec in records:
                    try:
                        conn.execute(sa_text(sql), [rec])
                        n += 1
                    except Exception:
                        try:
                            conn.execute(
                                sa_text(
                                    f"INSERT INTO stock_block_trade ({col_list}) "
                                    f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                                ),
                                [rec],
                            )
                            n += 1
                        except Exception:
                            pass
            return n

    def _table_cols(self, table: str) -> List[str]:
        mapping = {
            "stock_top_list_daily": [
                "ts_code", "trade_date", "name", "close", "pct_change", "turnover_rate",
                "amount", "l_sell", "l_buy", "l_amount", "net_amount", "net_rate",
                "amount_rate", "float_values", "reason",
            ],
            "stock_block_trade": [
                "ts_code", "trade_date", "price", "vol", "amount", "buyer", "seller",
            ],
            "margin_summary_daily": [
                "trade_date", "exchange_id", "rzye", "rzmre", "rzche",
                "rqye", "rqmcl", "rzrqye",
            ],
            "margin_detail_daily": [
                "trade_date", "ts_code", "name", "rzye", "rqye", "rzmre",
                "rqyl", "rzche", "rqchl", "rqmcl", "rzrqye",
            ],
            "stock_moneyflow_daily": [
                "ts_code", "trade_date",
                "buy_sm_vol", "buy_sm_amount", "sell_sm_vol", "sell_sm_amount",
                "buy_md_vol", "buy_md_amount", "sell_md_vol", "sell_md_amount",
                "buy_lg_vol", "buy_lg_amount", "sell_lg_vol", "sell_lg_amount",
                "buy_elg_vol", "buy_elg_amount", "sell_elg_vol", "sell_elg_amount",
                "net_mf_vol", "net_mf_amount",
            ],
            "stock_hk_hold": [
                "ts_code", "trade_date", "code", "name", "vol", "ratio", "exchange",
            ],
            "fin_cashflow": [
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
                "c_recp_return_invest", "c_recp_income_invest", "c_recp_disp_fiolta",
                "c_recp_disp_sobu", "c_inf_fr_invest_act",
                "c_paid_acq_const_fiolta", "c_paid_invest", "c_paid_acq_sobu",
                "c_inf_pay_invest_act", "oth_cash_recp_ral_inv_act",
                "oth_cash_pay_ral_inv_act", "n_cashflow_inv_act",
                "c_recp_borrow", "c_proc_issue_bonds", "c_recp_cap_contrib",
                "c_incr_cash_pub", "c_recp_cap_othcash", "c_inf_fr_finance_act",
                "c_prepay_amt_borr", "c_pay_dist_dpcp_int_exp",
                "incr_cash_cash_equ_dm", "c_inf_pay_finance_act",
                "oth_cash_recp_ral_fnc_act", "oth_cash_pay_ral_fnc_act",
                "n_cashflow_fnc_act", "eff_fx_chg_cash", "n_cash",
                "n_cash_equ_beg", "n_cash_equ_end",
                "c_conv_debt_into_eq", "c_use_fund_cash", "free_cashflow",
                "update_flag",
            ],
            "fin_income": [
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
            ],
            "fin_balancesheet": [
                "ts_code", "ann_date", "f_ann_date", "end_date", "report_type",
                "comp_type", "end_type",
                "total_assets", "total_cur_assets", "money_cap", "trad_asset",
                "notes_receiv", "accounts_receiv", "oth_receiv", "pre_payment",
                "div_receiv", "int_receiv", "inventories", "amor_exp", "nca_within_1y",
                "sett_rsrv", "loanto_oth_bank_fi", "premium_receiv", "reinsur_receiv",
                "reinsur_res_receiv", "pur_resale_fa", "oth_cur_assets",
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
            ],
            "fin_fina_indicator": [
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
            ],
            "fin_mainbz": [
                "ts_code", "end_date", "bz_item", "bz_code",
                "bz_sales", "bz_profit", "bz_cost", "curr_type",
            ],
            "fin_audit": [
                "ts_code", "ann_date", "end_date",
                "audit_result", "audit_fees", "audit_agency", "audit_sign",
            ],
            "fin_dividend": [
                "ts_code", "end_date", "ann_date", "div_proc",
                "stk_div", "stk_bo_rate", "stk_so_rate",
                "cash_div", "cash_div_tax",
                "record_date", "ex_date", "pay_date", "div_listdate",
                "imp_ann_date", "base_date", "base_share", "update_flag",
            ],
            "fin_express": [
                "ts_code", "ann_date", "end_date",
                "revenue", "operate_profit", "total_profit", "n_income",
                "total_assets", "total_hldr_eqy_exc_min_int",
                "diluted_eps", "weighted_roe", "yoy_net_profit",
                "performance_summary", "is_audit", "update_flag",
            ],
            "fin_disclosure_date": [
                "ts_code", "ann_date", "end_date", "pre_date", "actual_date",
            ],
            "fin_forecast": [
                "ts_code", "ann_date", "end_date", "type",
                "p_change_min", "p_change_max", "net_profit_min", "net_profit_max",
                "last_parent_net", "first_ann_date", "summary", "change_reason",
            ],
            "stock_top10_holders": [
                "ts_code", "ann_date", "end_date", "holder_name",
                "hold_amount", "hold_ratio", "hold_float_ratio", "hold_change",
                "holder_type", "holder_kind",
            ],
            "sector_moneyflow_daily": [
                "trade_date", "content_type", "ts_code", "name", "pct_change", "close",
                "net_amount", "net_amount_rate",
                "buy_elg_amount", "buy_elg_amount_rate",
                "buy_lg_amount", "buy_lg_amount_rate",
                "buy_md_amount", "buy_md_amount_rate",
                "buy_sm_amount", "buy_sm_amount_rate",
                "buy_sm_amount_stock", "rank_no",
            ],
            "stock_daily_basic": [
                "ts_code", "trade_date", "close", "turnover_rate", "turnover_rate_f",
                "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm",
                "dv_ratio", "dv_ttm", "total_share", "float_share", "free_share",
                "total_mv", "circ_mv",
            ],
            "stock_adj_factor": ["ts_code", "trade_date", "adj_factor"],
            "ref_stk_limit_daily": ["ts_code", "trade_date", "up_limit", "down_limit"],
            "stock_limit_list_daily": [
                "trade_date", "ts_code", "limit_type", "name", "industry",
                "close", "pct_chg", "amount", "limit_amount", "float_mv", "total_mv",
                "turnover_ratio", "fd_amount", "first_time", "last_time",
                "open_times", "up_stat", "limit_times",
            ],
            "ref_suspend_d": ["ts_code", "trade_date", "suspend_type", "suspend_reason"],
            "stock_top_inst_daily": [
                "trade_date", "ts_code", "exalter", "side",
                "buy", "buy_rate", "sell", "sell_rate", "net_buy", "reason",
            ],
            "ref_new_share": [
                "ts_code", "sub_code", "name", "ipo_date", "issue_date",
                "amount", "market_amount", "price", "pe", "limit_amount", "funds", "ballot",
            ],
            "ref_index_basic": [
                "ts_code", "name", "fullname", "market", "publisher", "index_type",
                "category", "base_date", "base_point", "list_date", "weight_rule",
                "exp_date",
            ],
            "index_weekly": [
                "ts_code", "trade_date", "open", "high", "low", "close",
                "pre_close", "change", "pct_chg", "vol", "amount",
            ],
            "index_monthly": [
                "ts_code", "trade_date", "open", "high", "low", "close",
                "pre_close", "change", "pct_chg", "vol", "amount",
            ],
            "index_daily_basic": [
                "ts_code", "trade_date", "total_mv", "float_mv",
                "total_share", "float_share", "free_share",
                "turnover_rate", "turnover_rate_f", "pe", "pe_ttm", "pb",
            ],
            "ref_index_classify": [
                "index_code", "industry_name", "level", "industry_code",
                "is_pub", "parent_code", "src",
            ],
            "index_weight": [
                "index_code", "con_code", "trade_date", "weight",
            ],
            "ref_fund_basic": [
                "ts_code", "name", "management", "fund_type",
                "found_date", "due_date", "list_date", "issue_date",
                "list_status", "issue_amount", "m_fee", "c_fee",
                "bench_index", "invest_type",
            ],
            "fund_nav": [
                "ts_code", "nav_date", "unit_nav", "accum_nav", "adj_nav",
            ],
            "fund_div": [
                "ts_code", "ann_date", "ex_date", "imp_anndate", "base_date",
                "div_proc", "record_date", "pay_date", "earpay_date", "net_ex_date",
                "div_cash", "base_unit", "ear_distr", "ear_amount", "account_date",
            ],
            "fund_portfolio": [
                "ts_code", "end_date", "symbol", "ann_date",
                "mkv", "amount", "stk_mkv_ratio", "stk_float_ratio",
            ],
            "fund_adj": [
                "ts_code", "trade_date", "adj_factor",
            ],
            "stock_share_float": [
                "ts_code", "ann_date", "float_date", "float_share", "float_ratio",
                "holder_name", "share_type",
            ],
            "stock_pledge_stat": [
                "ts_code", "end_date", "pledge_count", "unrest_pledge",
                "rest_pledge", "total_share", "pledge_ratio",
            ],
            "stock_holdertrade": [
                "ts_code", "ann_date", "holder_name", "holder_type", "in_de",
                "change_vol", "change_ratio", "after_share", "after_ratio",
                "avg_price", "total_share",
            ],
            "stock_cyq_perf": [
                "ts_code", "trade_date", "his_low", "his_high",
                "cost_5pct", "cost_15pct", "cost_50pct", "cost_85pct", "cost_95pct",
                "weight_avg", "winner_rate",
            ],
            "stock_cyq_chips": [
                "ts_code", "trade_date", "price", "percent",
            ],
            "stock_report_rc": [
                "ts_code", "report_date", "org_name", "report_title", "name",
                "report_type", "classify", "author_name", "quarter",
                "op_rt", "op_pr", "tp", "np", "eps", "pe", "rd", "roe",
                "ev_ebitda", "rating", "max_price", "min_price",
            ],
            "ths_member": [
                "ts_code", "con_code", "con_name",
            ],
            "cb_basic": [
                "ts_code", "bond_full_name", "bond_short_name", "cb_type", "cb_code",
                "stk_code", "stk_short_name", "maturity", "par", "issue_price",
                "issue_size", "remain_size", "value_date", "maturity_date",
                "coupon_rate", "add_rate", "pay_per_year", "list_date", "delist_date",
                "exchange", "conv_start_date", "conv_end_date", "conv_stop_date",
                "first_conv_price", "conv_price", "rate_clause",
            ],
            "cb_daily": [
                "ts_code", "trade_date", "pre_close", "open", "high", "low", "close",
                "change", "pct_chg", "vol", "amount",
            ],
            "fut_basic": [
                "ts_code", "symbol", "exchange", "name", "fut_code", "multiplier",
                "trade_unit", "per_unit", "quote_unit", "quote_unit_desc",
                "d_mode_desc", "list_date", "delist_date", "d_month", "last_ddate",
            ],
        }
        return mapping.get(table, [])

    def _read_pg(
        self,
        meta: Dict[str, Any],
        ts_code: Optional[str],
        start: Optional[date],
        end: Optional[date],
        period: Optional[str],
        limit: int,
    ) -> pd.DataFrame:
        if not self.engine:
            return pd.DataFrame()
        table = meta["table"]
        date_col = meta.get("date_col")
        where: List[str] = []
        params: Dict[str, Any] = {"lim": int(limit)}
        code_col = "index_code" if table in ("index_weight", "ref_index_classify") else "ts_code"
        if ts_code and code_col in self._table_cols(table):
            where.append(f"{code_col} = :c")
            params["c"] = ts_code
        if date_col:
            if period and date_col in (
                "end_date", "nav_date", "trade_date", "ann_date", "float_date", "report_date",
            ):
                where.append(f"{date_col} = :p")
                params["p"] = _parse_date(period)
            else:
                if start:
                    where.append(f"{date_col} >= :s")
                    params["s"] = start
                if end:
                    where.append(f"{date_col} <= :e")
                    params["e"] = end
        wh = (" WHERE " + " AND ".join(where)) if where else ""
        order = f" ORDER BY {date_col} DESC" if date_col else ""
        sql = f"SELECT * FROM {table}{wh}{order} LIMIT :lim"
        try:
            df = pd.read_sql(sa_text(sql), self.engine, params=params)
            if not df.empty and date_col and date_col in df.columns:
                df = df.sort_values(date_col).reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning("read %s: %s", table, e)
            return pd.DataFrame()

    def _pg_count(
        self,
        meta: Dict[str, Any],
        ts_code: Optional[str],
        start: Optional[date],
        end: Optional[date],
        period: Optional[str],
    ) -> int:
        if not self.engine:
            return 0
        table = meta["table"]
        date_col = meta.get("date_col")
        where: List[str] = []
        params: Dict[str, Any] = {}
        code_col = "index_code" if table in ("index_weight", "ref_index_classify") else "ts_code"
        if ts_code and table not in ("margin_summary_daily",) and code_col in (self._table_cols(table) or [code_col]):
            where.append(f"{code_col} = :c")
            params["c"] = ts_code
        if date_col:
            if period and date_col in (
                "end_date", "nav_date", "trade_date", "ann_date", "float_date", "report_date",
            ):
                where.append(f"{date_col} = :p")
                params["p"] = _parse_date(period)
            else:
                if start:
                    where.append(f"{date_col} >= :s")
                    params["s"] = start
                if end:
                    where.append(f"{date_col} <= :e")
                    params["e"] = end
        wh = (" WHERE " + " AND ".join(where)) if where else ""
        try:
            with self.engine.connect() as conn:
                return int(
                    conn.execute(sa_text(f"SELECT COUNT(1) FROM {table}{wh}"), params).scalar()
                    or 0
                )
        except Exception:
            return 0

    def _sector_mf_day_count(self, d: date) -> int:
        if not self.engine:
            return 0
        try:
            with self.engine.connect() as conn:
                return int(
                    conn.execute(
                        sa_text(
                            "SELECT COUNT(1) FROM sector_moneyflow_daily WHERE trade_date=:d"
                        ),
                        {"d": d},
                    ).scalar()
                    or 0
                )
        except Exception:
            return 0

    def _find_stock_sectors(
        self,
        ts_code: str,
        trade_date: date,
        levels: Sequence[str],
    ) -> List[Dict[str, Any]]:
        if not self.engine:
            return []
        from sqlalchemy import bindparam

        lv_list = list(levels)
        stmt = sa_text(
            """
            SELECT m.ts_code AS sector_code,
                   COALESCE(i.name, m.name) AS sector_name,
                   i.industry_level,
                   m.trade_date
            FROM sector_dc_member m
            LEFT JOIN sector_dc_index_daily i
              ON i.ts_code = m.ts_code AND i.trade_date = m.trade_date
            WHERE m.con_code = :c
              AND m.trade_date = :d
              AND i.industry_level IN :lv
            ORDER BY i.industry_level, m.ts_code
            """
        ).bindparams(bindparam("lv", expanding=True))
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    stmt,
                    {"c": ts_code, "d": trade_date, "lv": lv_list},
                ).fetchall()
            return [
                {
                    "sector_code": r[0],
                    "sector_name": r[1],
                    "industry_level": r[2],
                    "trade_date": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("find sectors: %s", e)
            return []

    def _find_stock_sectors_any_recent(
        self,
        ts_code: str,
        levels: Sequence[str],
        lookback: int = 30,
    ) -> List[Dict[str, Any]]:
        if not self.engine:
            return []
        from sqlalchemy import bindparam

        stmt = sa_text(
            """
            SELECT DISTINCT ON (m.ts_code)
                   m.ts_code AS sector_code,
                   COALESCE(i.name, m.name) AS sector_name,
                   i.industry_level,
                   m.trade_date
            FROM sector_dc_member m
            LEFT JOIN sector_dc_index_daily i
              ON i.ts_code = m.ts_code AND i.trade_date = m.trade_date
            WHERE m.con_code = :c
              AND m.trade_date >= CURRENT_DATE - :lb
              AND i.industry_level IN :lv
            ORDER BY m.ts_code, m.trade_date DESC
            """
        ).bindparams(bindparam("lv", expanding=True))
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    stmt,
                    {"c": ts_code, "lb": lookback, "lv": list(levels)},
                ).fetchall()
            return [
                {
                    "sector_code": r[0],
                    "sector_name": r[1],
                    "industry_level": r[2],
                    "trade_date": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning("find sectors recent: %s", e)
            return []

    def _read_sector_moneyflow(
        self,
        sector_codes: Sequence[str],
        trade_date: date,
    ) -> pd.DataFrame:
        if not self.engine or not sector_codes:
            return pd.DataFrame()
        from sqlalchemy import bindparam

        stmt = sa_text(
            """
            SELECT * FROM sector_moneyflow_daily
            WHERE trade_date = :d AND ts_code IN :codes
            ORDER BY net_amount DESC NULLS LAST
            """
        ).bindparams(bindparam("codes", expanding=True))
        try:
            return pd.read_sql(
                stmt,
                self.engine,
                params={"d": trade_date, "codes": list(sector_codes)},
            )
        except Exception as e:
            logger.warning("read sector mf: %s", e)
            return pd.DataFrame()

    def _read_sector_moneyflow_latest(
        self,
        sector_codes: Sequence[str],
    ) -> pd.DataFrame:
        if not self.engine or not sector_codes:
            return pd.DataFrame()
        from sqlalchemy import bindparam

        stmt = sa_text(
            """
            SELECT m.*
            FROM sector_moneyflow_daily m
            INNER JOIN (
                SELECT ts_code, MAX(trade_date) AS trade_date
                FROM sector_moneyflow_daily
                WHERE ts_code IN :codes
                GROUP BY ts_code
            ) t ON m.ts_code = t.ts_code AND m.trade_date = t.trade_date
            ORDER BY m.net_amount DESC NULLS LAST
            """
        ).bindparams(bindparam("codes", expanding=True))
        try:
            return pd.read_sql(
                stmt,
                self.engine,
                params={"codes": list(sector_codes)},
            )
        except Exception as e:
            logger.warning("read sector mf latest: %s", e)
            return pd.DataFrame()

    # ─── helpers ──────────────────────────────────────

    def _resolve_meta(self, dataset: str) -> Dict[str, Any]:
        key = (dataset or "").strip().lower()
        aliases = {
            "龙虎榜": "top_list",
            "toplist": "top_list",
            "大宗": "block_trade",
            "大宗交易": "block_trade",
            "两融": "margin_detail",
            "融资融券": "margin_detail",
            "margin_summary": "margin",
            "北向": "hk_hold",
            "北向持股": "hk_hold",
            "hsgt": "hk_hold",
            "资金流": "moneyflow",
            "资金流向": "moneyflow",
            "现金流量表": "cashflow",
            "利润表": "income",
            "资产负债表": "balancesheet",
            "财务指标": "fina_indicator",
            "roe": "fina_indicator",
            "eps": "fina_indicator",
            "主营构成": "fina_mainbz",
            "mainbz": "fina_mainbz",
            "审计意见": "fina_audit",
            "audit": "fina_audit",
            "分红": "dividend",
            "分红送转": "dividend",
            "业绩快报": "express",
            "披露日程": "disclosure_date",
            "财报披露": "disclosure_date",
            "disclosure": "disclosure_date",
            "业绩预告": "forecast",
            "十大股东": "top10_holders",
            "top10": "top10_holders",
            "top10_floatholders": "top10_holders",
            "板块资金": "sector_moneyflow",
            "moneyflow_ind_dc": "sector_moneyflow",
            "每日指标": "daily_basic",
            "复权因子": "adj_factor",
            "涨跌停价": "stk_limit",
            "涨跌停榜": "limit_list",
            "limit_list_d": "limit_list",
            "停复牌": "suspend_d",
            "suspend": "suspend_d",
            "龙虎榜席位": "top_inst",
            "营业部": "top_inst",
            "新股": "new_share",
            "ipo": "new_share",
            "指数主档": "index_basic",
            "index_info": "index_basic",
            "指数周线": "index_weekly",
            "指数月线": "index_monthly",
            "指数估值": "index_dailybasic",
            "指数每日指标": "index_dailybasic",
            "index_daily_basic": "index_dailybasic",
            "行业分类": "index_classify",
            "申万": "index_classify",
            "sw": "index_classify",
            "指数权重": "index_weight",
            "成分权重": "index_weight",
            "基金主档": "fund_basic",
            "基金净值": "fund_nav",
            "基金分红": "fund_div",
            "基金持仓": "fund_portfolio",
            "fund_hold": "fund_portfolio",
            "基金复权": "fund_adj",
            "fund_adj_factor": "fund_adj",
            "限售解禁": "share_float",
            "解禁": "share_float",
            "股权质押": "pledge_stat",
            "质押": "pledge_stat",
            "股东增减持": "stk_holdertrade",
            "增减持": "stk_holdertrade",
            "holdertrade": "stk_holdertrade",
            "筹码": "cyq_perf",
            "筹码分布": "cyq_perf",
            "获利盘": "cyq_perf",
            "筹码明细": "cyq_chips",
            "研报": "report_rc",
            "券商研报": "report_rc",
            "同花顺成分": "ths_member",
            "概念成分": "ths_member",
            "可转债": "cb_basic",
            "可转债基础": "cb_basic",
            "可转债日线": "cb_daily",
            "期货主档": "fut_basic",
            "期货": "fut_basic",
        }
        key = aliases.get(key, key)
        if key not in FUND_DATASETS:
            raise ValueError(
                f"未知 dataset={dataset}，可选: {', '.join(FUND_DATASETS)}"
            )
        return {"name": key, **FUND_DATASETS[key]}

    @staticmethod
    def _nearest_report_end(d: date) -> date:
        """取 <= d 的最近季报期末（0331/0630/0930/1231）。"""
        ends = [
            date(d.year, 3, 31), date(d.year, 6, 30),
            date(d.year, 9, 30), date(d.year, 12, 31),
            date(d.year - 1, 12, 31), date(d.year - 1, 9, 30),
            date(d.year - 1, 6, 30), date(d.year - 1, 3, 31),
        ]
        past = [e for e in ends if e <= d]
        return max(past) if past else date(d.year - 1, 12, 31)

    def _norm_code(self, ts_code: Optional[str]) -> Optional[str]:
        if not ts_code:
            return None
        raw = str(ts_code).strip().upper()
        if not raw:
            return None
        if "." in raw:
            return raw
        if raw.startswith("BK"):
            return f"{raw}.DC"
        if raw.startswith(("5", "6", "9")):
            return f"{raw}.SH"
        if raw.startswith(("0", "1", "2", "3")):
            return f"{raw}.SZ"
        return raw

    @staticmethod
    def _df_records(df: Optional[pd.DataFrame], limit: int = 200) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []
        use = df.head(int(limit)).copy()
        out: List[Dict[str, Any]] = []
        for _, row in use.iterrows():
            item: Dict[str, Any] = {}
            for k in use.columns:
                v = row[k]
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    item[k] = None
                elif isinstance(v, (date, datetime)):
                    item[k] = v.isoformat()[:19] if isinstance(v, datetime) else v.isoformat()
                elif hasattr(v, "item"):
                    try:
                        item[k] = v.item()
                    except Exception:
                        item[k] = float(v) if pd.notna(v) else None
                elif pd.isna(v):
                    item[k] = None
                else:
                    item[k] = v
            out.append(item)
        return out

    def _ok(
        self,
        meta: Dict[str, Any],
        ts_code: Optional[str],
        df: Optional[pd.DataFrame],
        source_used: str,
        fetched: bool,
        upserted: int,
        limit: int,
    ) -> Dict[str, Any]:
        records = self._df_records(df, limit=limit)
        return {
            "ok": True,
            "dataset": meta.get("name"),
            "table": meta["table"],
            "api": meta["api"],
            "ts_code": ts_code,
            "source_used": source_used,
            "fetched_remote": fetched,
            "upserted": upserted,
            "count": len(records),
            "columns": list(df.columns) if df is not None and not df.empty else [],
            "data": records,
            "latest": records[-1] if records else None,
            "desc": meta.get("desc"),
        }


fund_data_fetcher = FundDataFetcher()
