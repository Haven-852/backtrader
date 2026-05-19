"""
Schema 管理器 - 所有建表 DDL 统一管理
严格按照 architecture/02-agent-data-access.md 第8节完整清单实现

包含:
- PostgreSQL 普通表 (20张): 主数据、财务、新闻、公告、政策、研报、互动
- TimescaleDB Hypertable (19张): 日/周/月K线、股本、竞价、资金、两融、指数、基金、ETF

使用方式:
    from data_layer.schema import SchemaManager
    from sqlalchemy import create_engine
    engine = create_engine(config.get_postgres_url())
    SchemaManager.ensure_all_tables(engine)
"""
import logging
from typing import Optional
from sqlalchemy import Engine, text

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# 所有 PostgreSQL DDL 语句 (CREATE TABLE IF NOT EXISTS)
# ─────────────────────────────────────────────────────────────────────

POSTGRES_DDL_LIST = [
    # ─── 基础参照数据 ───────────────────────────────────────────────

    # 1. 交易日历
    """
    CREATE TABLE IF NOT EXISTS ref_trade_cal (
        exchange       VARCHAR(10)  NOT NULL,   -- SSE / SZSE / BJ
        cal_date       DATE         NOT NULL,
        is_open        SMALLINT     NOT NULL,   -- 1=开市, 0=休市
        pretrade_date  DATE,
        PRIMARY KEY (exchange, cal_date)
    )
    """,

    # 2. 证券主档
    """
    CREATE TABLE IF NOT EXISTS ref_stock_basic (
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
    )
    """,

    # 3. 股票更名记录
    """
    CREATE TABLE IF NOT EXISTS ref_stock_namechange (
        ts_code        VARCHAR(16)  NOT NULL,
        name           VARCHAR(50),
        start_date     DATE         NOT NULL,
        end_date       DATE,
        ann_date       DATE,
        change_reason  VARCHAR(200),
        PRIMARY KEY (ts_code, start_date)
    )
    """,

    # 4. 新股发行
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

    # 5. 指数主档
    """
    CREATE TABLE IF NOT EXISTS ref_index_basic (
        ts_code        VARCHAR(16)  PRIMARY KEY,
        name           VARCHAR(100),
        fullname       VARCHAR(200),
        market         VARCHAR(10),
        publisher      VARCHAR(100),
        index_type     VARCHAR(30),
        category       VARCHAR(30),
        base_date      DATE,
        base_point     NUMERIC(16,4),
        list_date      DATE,
        weight_rule    VARCHAR(20),
        desc           TEXT,
        exp_date       DATE
    )
    """,

    # 6. ETF主档
    """
    CREATE TABLE IF NOT EXISTS ref_etf_basic (
        ts_code        VARCHAR(16)  PRIMARY KEY,
        name           VARCHAR(100),
        management     VARCHAR(100),            -- 管理人
        fund_type      VARCHAR(20),             -- ETF 类型（股票/债券/商品/跨境）
        bench_index    VARCHAR(50),             -- 跟踪指数
        list_date      DATE,
        issue_date     DATE,
        list_status    VARCHAR(1),              -- L上市 D退市
        found_date     DATE,
        invest_type    VARCHAR(50),
        custodian      VARCHAR(100),
        m_fee          NUMERIC(12,4),           -- 管理费
        c_fee          NUMERIC(12,4),           -- 托管费
        unit_total     NUMERIC(20,4)            -- 总份额
    )
    """,

    # 7. 基金主档
    """
    CREATE TABLE IF NOT EXISTS ref_fund_basic (
        ts_code        VARCHAR(16)  PRIMARY KEY,
        name           VARCHAR(100),
        management     VARCHAR(100),            -- 管理人
        fund_type      VARCHAR(20),
        found_date     DATE,
        due_date       DATE,
        list_date      DATE,
        issue_date     DATE,
        list_status    VARCHAR(1),
        issue_amount   NUMERIC(20,4),
        m_fee          NUMERIC(12,4),
        c_fee          NUMERIC(12,4),
        bench_index    VARCHAR(50),
        invest_type    VARCHAR(50)
    )
    """,

    # 8. 政策法规库
    """
    CREATE TABLE IF NOT EXISTS ref_policy_law (
        id             BIGSERIAL     PRIMARY KEY,
        policy_id      VARCHAR(64)   UNIQUE NOT NULL,
        title          TEXT          NOT NULL,
        dept           VARCHAR(100),
        policy_type    VARCHAR(30),
        pub_date       DATE          NOT NULL,
        effective_date DATE,
        status         VARCHAR(10)   DEFAULT 'active',
        industry_tags  TEXT[],
        topic_tags     TEXT[],
        url            TEXT,
        bucket_key     VARCHAR(256),
        content_full   TEXT,
        content_summary TEXT,
        impact_rating  SMALLINT,
        created_at     TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # ─── 新闻资讯 ───────────────────────────────────────────────────

    # 9. 新闻资讯
    """
    CREATE TABLE IF NOT EXISTS news_article (
        id             BIGSERIAL     PRIMARY KEY,
        article_id     VARCHAR(64)   UNIQUE,
        ts_code        VARCHAR(16),
        title          TEXT          NOT NULL,
        source         VARCHAR(100),
        news_type      VARCHAR(20),
        channels       VARCHAR(200),
        publish_time   TIMESTAMPTZ   NOT NULL,
        sentiment      NUMERIC(5,4),
        importance     SMALLINT,
        url            TEXT,
        bucket_key     VARCHAR(256),
        content_preview TEXT,
        raw_hash       VARCHAR(64),
        created_at     TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # 10. 公司公告
    """
    CREATE TABLE IF NOT EXISTS news_ann (
        id             BIGSERIAL     PRIMARY KEY,
        ann_id         VARCHAR(64)   UNIQUE NOT NULL,
        ts_code        VARCHAR(16)   NOT NULL,
        ann_date       DATE          NOT NULL,
        ann_type       VARCHAR(20),
        title          TEXT          NOT NULL,
        url            TEXT,
        bucket_key     VARCHAR(256),
        file_size      BIGINT,
        file_format    VARCHAR(10),
        raw_hash       VARCHAR(64),
        parsed_text    TEXT,
        key_topics     TEXT[],
        created_at     TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # ─── 董秘互动 ───────────────────────────────────────────────────

    # 11. 董秘互动回复
    """
    CREATE TABLE IF NOT EXISTS board_secretary_interact (
        id             BIGSERIAL     PRIMARY KEY,
        interact_id    VARCHAR(64)   UNIQUE NOT NULL,
        ts_code        VARCHAR(16)   NOT NULL,
        company_name   VARCHAR(100),
        platform       VARCHAR(20),
        question       TEXT          NOT NULL,
        question_time  TIMESTAMPTZ   NOT NULL,
        answer         TEXT,
        answer_time    TIMESTAMPTZ,
        is_answered    BOOLEAN       DEFAULT FALSE,
        questioner     VARCHAR(50),
        tags           TEXT[],
        sentiment_q    NUMERIC(5,4),
        sentiment_a    NUMERIC(5,4),
        created_at     TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # ─── 券商研报 ───────────────────────────────────────────────────

    # 12. 券商研报
    """
    CREATE TABLE IF NOT EXISTS broker_report (
        id             BIGSERIAL     PRIMARY KEY,
        report_id      VARCHAR(64)   UNIQUE NOT NULL,
        ts_code        VARCHAR(16)   NOT NULL,
        stock_name     VARCHAR(100),
        broker         VARCHAR(100),
        analyst        VARCHAR(100),
        analyst_level  VARCHAR(50),
        report_title   TEXT          NOT NULL,
        report_type    VARCHAR(30),
        rating         VARCHAR(20),
        rating_change  VARCHAR(20),
        target_price   NUMERIC(12,4),
        current_price  NUMERIC(12,4),
        upside_pct     NUMERIC(12,4),
        eps_forecast_0 NUMERIC(12,4),
        eps_forecast_1 NUMERIC(12,4),
        eps_forecast_2 NUMERIC(12,4),
        pe_forecast_0  NUMERIC(12,4),
        pe_forecast_1  NUMERIC(12,4),
        publish_date   DATE          NOT NULL,
        report_url     TEXT,
        bucket_key     VARCHAR(256),
        summary        TEXT,
        core_view      TEXT,
        key_risks      TEXT,
        file_size      BIGINT,
        created_at     TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # ─── 财务数据 ───────────────────────────────────────────────────

    # 13. 利润表
    """
    CREATE TABLE IF NOT EXISTS fin_income (
        ts_code         VARCHAR(16)  NOT NULL,
        ann_date        DATE,
        f_ann_date      DATE,
        end_date        DATE         NOT NULL,
        report_type     VARCHAR(1),
        comp_type       VARCHAR(1),
        end_type        VARCHAR(1),
        -- 核心收入指标
        total_revenue   NUMERIC(20,4),
        revenue         NUMERIC(20,4),
        int_income      NUMERIC(20,4),
        prem_earned     NUMERIC(20,4),
        comm_income     NUMERIC(20,4),
        n_commis_income NUMERIC(20,4),
        n_oth_income    NUMERIC(20,4),
        n_oth_b_income  NUMERIC(20,4),
        prem_income     NUMERIC(20,4),
        out_prem        NUMERIC(20,4),
        une_prem_reser  NUMERIC(20,4),
        reins_income    NUMERIC(20,4),
        n_sec_tb_income NUMERIC(20,4),
        n_sec_uw_income NUMERIC(20,4),
        n_asset_mg_income NUMERIC(20,4),
        oth_b_income    NUMERIC(20,4),
        fv_value_chg    NUMERIC(20,4),
        invest_income   NUMERIC(20,4),
        ass_invest_income NUMERIC(20,4),
        forex_gain      NUMERIC(20,4),
        -- 成本与费用
        total_cogs      NUMERIC(20,4),
        oper_cost       NUMERIC(20,4),
        int_exp         NUMERIC(20,4),
        comm_exp        NUMERIC(20,4),
        biz_tax_surchg  NUMERIC(20,4),
        sell_exp        NUMERIC(20,4),
        admin_exp       NUMERIC(20,4),
        fin_exp         NUMERIC(20,4),
        assets_impair_loss NUMERIC(20,4),
        -- 利润
        oper_profit     NUMERIC(20,4),
        total_profit    NUMERIC(20,4),
        income_tax      NUMERIC(20,4),
        n_income        NUMERIC(20,4),
        n_income_attr_p NUMERIC(20,4),
        minority_gain   NUMERIC(20,4),
        oth_compr_income NUMERIC(20,4),
        t_compr_income  NUMERIC(20,4),
        compr_inc_attr_p NUMERIC(20,4),
        compr_inc_attr_m_s NUMERIC(20,4),
        -- EPS
        ebit            NUMERIC(20,4),
        ebitda          NUMERIC(20,4),
        basic_eps       NUMERIC(16,4),
        diluted_eps     NUMERIC(16,4),
        -- 元数据
        insurance_exp   NUMERIC(20,4),
        undist_profit   NUMERIC(20,4),
        distable_profit NUMERIC(20,4),
        update_flag     VARCHAR(3),
        PRIMARY KEY (ts_code, end_date, report_type)
    )
    """,

    # 14. 资产负债表
    """
    CREATE TABLE IF NOT EXISTS fin_balancesheet (
        ts_code            VARCHAR(16)  NOT NULL,
        ann_date           DATE,
        f_ann_date         DATE,
        end_date           DATE         NOT NULL,
        report_type        VARCHAR(1),
        comp_type          VARCHAR(1),
        end_type           VARCHAR(1),
        -- 资产
        total_assets       NUMERIC(20,4),
        total_cur_assets   NUMERIC(20,4),
        money_cap          NUMERIC(20,4),
        trad_asset         NUMERIC(20,4),
        notes_receiv       NUMERIC(20,4),
        accounts_receiv    NUMERIC(20,4),
        oth_receiv         NUMERIC(20,4),
        pre_payment        NUMERIC(20,4),
        div_receiv         NUMERIC(20,4),
        int_receiv         NUMERIC(20,4),
        inventories        NUMERIC(20,4),
        amor_exp           NUMERIC(20,4),
        nca_within_1y      NUMERIC(20,4),
        sett_rsrv          NUMERIC(20,4),
        loanto_oth_bank_fi NUMERIC(20,4),
        premium_receiv     NUMERIC(20,4),
        reinsur_receiv     NUMERIC(20,4),
        reinsur_res_receiv NUMERIC(20,4),
        pur_resale_fa      NUMERIC(20,4),
        oth_cur_assets     NUMERIC(20,4),
        total_nca          NUMERIC(20,4),
        fa_avail_for_sale  NUMERIC(20,4),
        htm_invest         NUMERIC(20,4),
        lt_eqt_invest      NUMERIC(20,4),
        invest_real_estate NUMERIC(20,4),
        time_deposits      NUMERIC(20,4),
        oth_assets         NUMERIC(20,4),
        lt_rec             NUMERIC(20,4),
        fix_assets         NUMERIC(20,4),
        cip                NUMERIC(20,4),
        const_materials    NUMERIC(20,4),
        fixed_assets_disp  NUMERIC(20,4),
        produc_bio_assets  NUMERIC(20,4),
        oil_and_gas_assets NUMERIC(20,4),
        intan_assets       NUMERIC(20,4),
        r_and_d            NUMERIC(20,4),
        goodwill           NUMERIC(20,4),
        lt_amor_exp        NUMERIC(20,4),
        defer_tax_assets   NUMERIC(20,4),
        decr_in_disbur     NUMERIC(20,4),
        -- 负债
        total_liab         NUMERIC(20,4),
        total_cur_liab     NUMERIC(20,4),
        st_borrow          NUMERIC(20,4),
        pay_borrow         NUMERIC(20,4),
        trad_liab          NUMERIC(20,4),
        notes_payable      NUMERIC(20,4),
        acct_payable       NUMERIC(20,4),
        adv_receipts       NUMERIC(20,4),
        int_payable        NUMERIC(20,4),
        div_payable        NUMERIC(20,4),
        oth_payable        NUMERIC(20,4),
        acc_exp            NUMERIC(20,4),
        deferred_inc       NUMERIC(20,4),
        st_bonds_payable   NUMERIC(20,4),
        payable_to_reinsurer NUMERIC(20,4),
        rsrv_insur_cont    NUMERIC(20,4),
        acting_trading_sec NUMERIC(20,4),
        acting_uw_sec      NUMERIC(20,4),
        non_cur_liab_due_1y NUMERIC(20,4),
        oth_cur_liab       NUMERIC(20,4),
        total_ncl          NUMERIC(20,4),
        lt_borrow          NUMERIC(20,4),
        bonds_payable      NUMERIC(20,4),
        lt_payable         NUMERIC(20,4),
        specific_payables  NUMERIC(20,4),
        estimated_liab     NUMERIC(20,4),
        defer_tax_liab     NUMERIC(20,4),
        defer_inc_non_cur_liab NUMERIC(20,4),
        oth_ncl            NUMERIC(20,4),
        -- 权益
        total_hldr_eqy_exc_min_int NUMERIC(20,4),
        minority_int       NUMERIC(20,4),
        total_hldr_eqy_inc_min_int NUMERIC(20,4),
        cap_stk            NUMERIC(20,4),
        cap_rese           NUMERIC(20,4),
        special_rese       NUMERIC(20,4),
        surplus_rese       NUMERIC(20,4),
        retained_earnings  NUMERIC(20,4),
        prov_nom_risks     NUMERIC(20,4),
        oth_eqt_tools      NUMERIC(20,4),
        oth_eqt_tools_p_shr NUMERIC(20,4),
        oth_compreh_income NUMERIC(20,4),
        update_flag        VARCHAR(3),
        PRIMARY KEY (ts_code, end_date, report_type)
    )
    """,

    # 15. 现金流量表
    """
    CREATE TABLE IF NOT EXISTS fin_cashflow (
        ts_code             VARCHAR(16)  NOT NULL,
        ann_date            DATE,
        f_ann_date          DATE,
        end_date            DATE         NOT NULL,
        report_type         VARCHAR(1),
        comp_type           VARCHAR(1),
        end_type            VARCHAR(1),
        -- 经营活动
        net_profit          NUMERIC(20,4),
        finan_exp           NUMERIC(20,4),
        c_fr_sale_sg        NUMERIC(20,4),
        recp_tax_rends      NUMERIC(20,4),
        n_depos_incr_fi     NUMERIC(20,4),
        n_incr_loans_cb     NUMERIC(20,4),
        n_inc_loans_oth_bank NUMERIC(20,4),
        prem_fr_orig_contr  NUMERIC(20,4),
        n_incr_insured_dep  NUMERIC(20,4),
        n_reinsur_prem      NUMERIC(20,4),
        n_incr_disp_tfa     NUMERIC(20,4),
        ifc_cashincr        NUMERIC(20,4),
        n_incr_disp_faas    NUMERIC(20,4),
        n_incr_loans_oth_bank_f NUMERIC(20,4),
        n_cap_incr_repur    NUMERIC(20,4),
        c_fr_oth_operate_a  NUMERIC(20,4),
        c_inf_fr_operate_a  NUMERIC(20,4),
        -- 经营活动现金流出
        c_paid_goods_s      NUMERIC(20,4),
        c_paid_to_for_empl  NUMERIC(20,4),
        c_paid_for_taxes    NUMERIC(20,4),
        n_incr_clt_loan_adv NUMERIC(20,4),
        n_incr_loans_cb_clt NUMERIC(20,4),
        c_pay_claims_orig_inco NUMERIC(20,4),
        pay_handling_chrg   NUMERIC(20,4),
        pay_comm_insur_plcy NUMERIC(20,4),
        oth_cash_pay_oper_act NUMERIC(20,4),
        st_cash_out_act     NUMERIC(20,4),
        n_cashflow_act      NUMERIC(20,4),
        -- 投资活动
        c_recp_return_invest NUMERIC(20,4),
        c_recp_income_invest NUMERIC(20,4),
        c_recp_disp_fiolta  NUMERIC(20,4),
        c_recp_disp_sobu    NUMERIC(20,4),
        c_inf_fr_invest_act NUMERIC(20,4),
        c_paid_acq_const_fiolta NUMERIC(20,4),
        c_paid_invest       NUMERIC(20,4),
        c_paid_acq_sobu     NUMERIC(20,4),
        c_inf_pay_invest_act NUMERIC(20,4),
        oth_cash_recp_ral_inv_act NUMERIC(20,4),
        oth_cash_pay_ral_inv_act NUMERIC(20,4),
        n_cashflow_inv_act  NUMERIC(20,4),
        -- 筹资活动
        c_recp_borrow       NUMERIC(20,4),
        c_proc_issue_bonds  NUMERIC(20,4),
        c_recp_cap_contrib  NUMERIC(20,4),
        c_incr_cash_pub     NUMERIC(20,4),
        c_recp_cap_othcash  NUMERIC(20,4),
        c_inf_fr_finance_act NUMERIC(20,4),
        c_prepay_amt_borr   NUMERIC(20,4),
        c_pay_dist_dpcp_int_exp NUMERIC(20,4),
        incr_cash_cash_equ_dm NUMERIC(20,4),
        c_inf_pay_finance_act NUMERIC(20,4),
        oth_cash_recp_ral_fnc_act NUMERIC(20,4),
        oth_cash_pay_ral_fnc_act NUMERIC(20,4),
        n_cashflow_fnc_act  NUMERIC(20,4),
        -- 汇率影响与净额
        eff_fx_chg_cash     NUMERIC(20,4),
        n_cash              NUMERIC(20,4),
        n_cash_equ_beg      NUMERIC(20,4),
        n_cash_equ_end      NUMERIC(20,4),
        -- 补充资料
        c_conv_debt_into_eq NUMERIC(20,4),
        c_use_fund_cash     NUMERIC(20,4),
        free_cashflow       NUMERIC(20,4),
        update_flag         VARCHAR(3),
        PRIMARY KEY (ts_code, end_date, report_type)
    )
    """,

    # 16. 财务指标
    """
    CREATE TABLE IF NOT EXISTS fin_fina_indicator (
        ts_code         VARCHAR(16)  NOT NULL,
        ann_date        DATE,
        end_date        DATE         NOT NULL,
        -- 每股指标
        eps             NUMERIC(16,4),
        dt_eps          NUMERIC(16,4),
        total_profit_eps NUMERIC(16,4),
        oper_profit_eps NUMERIC(16,4),
        basic_eps       NUMERIC(16,4),
        diluted_eps     NUMERIC(16,4),
        -- 盈利能力
        roe             NUMERIC(16,4),
        roe_waa         NUMERIC(16,4),
        roe_dt          NUMERIC(16,4),
        roa             NUMERIC(16,4),
        npta            NUMERIC(16,4),
        roic            NUMERIC(16,4),
        roe_yearly      NUMERIC(16,4),
        roa_yearly      NUMERIC(16,4),
        roa2_yearly     NUMERIC(16,4),
        -- 运营能力
        grossprofit_margin NUMERIC(16,4),
        netprofit_margin   NUMERIC(16,4),
        debt_to_assets     NUMERIC(16,4),
        assets_turn        NUMERIC(16,4),
        inv_turn           NUMERIC(16,4),
        ar_turn            NUMERIC(16,4),
        -- 成长能力
        or_yoy          NUMERIC(16,4),
        op_yoy          NUMERIC(16,4),
        ebt_yoy         NUMERIC(16,4),
        netprofit_yoy   NUMERIC(16,4),
        dt_netprofit_yoy NUMERIC(16,4),
        ocf_yoy         NUMERIC(16,4),
        -- 偿债能力
        current_ratio   NUMERIC(16,4),
        quick_ratio     NUMERIC(16,4),
        equity_ratio    NUMERIC(16,4),
        -- 估值指标
        pe              NUMERIC(16,4),
        pe_ttm          NUMERIC(16,4),
        pb              NUMERIC(16,4),
        ps              NUMERIC(16,4),
        ps_ttm          NUMERIC(16,4),
        dv_ratio        NUMERIC(16,4),
        dv_ttm          NUMERIC(16,4),
        total_share     NUMERIC(20,4),
        float_share     NUMERIC(20,4),
        free_share      NUMERIC(20,4),
        total_mv        NUMERIC(20,4),
        circ_mv         NUMERIC(20,4),
        update_flag     VARCHAR(3),
        PRIMARY KEY (ts_code, end_date)
    )
    """,

    # 17. 业绩预告
    """
    CREATE TABLE IF NOT EXISTS fin_forecast (
        ts_code         VARCHAR(16)  NOT NULL,
        ann_date        DATE         NOT NULL,
        end_date        DATE         NOT NULL,
        type            VARCHAR(10),
        p_change_min    NUMERIC(20,4),
        p_change_max    NUMERIC(20,4),
        net_profit_min  NUMERIC(20,4),
        net_profit_max  NUMERIC(20,4),
        last_parent_net NUMERIC(20,4),
        first_ann_date  DATE,
        summary         TEXT,
        change_reason   TEXT,
        PRIMARY KEY (ts_code, ann_date, end_date)
    )
    """,

    # 18. 业绩快报
    """
    CREATE TABLE IF NOT EXISTS fin_express (
        ts_code         VARCHAR(16)  NOT NULL,
        ann_date        DATE         NOT NULL,
        end_date        DATE         NOT NULL,
        revenue         NUMERIC(20,4),
        operate_profit  NUMERIC(20,4),
        total_profit    NUMERIC(20,4),
        n_income        NUMERIC(20,4),
        total_assets    NUMERIC(20,4),
        total_hldr_eqy_exc_min_int NUMERIC(20,4),
        diluted_eps     NUMERIC(16,4),
        weighted_roe    NUMERIC(16,4),
        yoy_net_profit  NUMERIC(16,4),
        performance_summary TEXT,
        is_audit        SMALLINT,
        update_flag     VARCHAR(3),
        PRIMARY KEY (ts_code, ann_date, end_date)
    )
    """,

    # 19. 分红送股
    """
    CREATE TABLE IF NOT EXISTS fin_dividend (
        ts_code         VARCHAR(16)  NOT NULL,
        end_date        DATE         NOT NULL,
        ann_date        DATE,
        div_proc        VARCHAR(30),
        stk_div         NUMERIC(16,4),
        stk_bo_rate     NUMERIC(16,4),
        stk_so_rate     NUMERIC(16,4),
        cash_div        NUMERIC(16,4),
        cash_div_tax    NUMERIC(16,4),
        record_date     DATE,
        ex_date         DATE,
        pay_date        DATE,
        div_listdate    DATE,
        imp_ann_date    DATE,
        base_date       DATE,
        base_share      NUMERIC(20,4),
        update_flag     VARCHAR(3),
        PRIMARY KEY (ts_code, end_date)
    )
    """,
]

# ─────────────────────────────────────────────────────────────────────
# 所有 TimescaleDB DDL + Hypertable 创建语句
# ─────────────────────────────────────────────────────────────────────

# 每个元素: (create_table_sql, hypertable_main_column)
TIMESCALEDB_DDL_LIST = [
    # 1. 股票日K线
    (
        """
        CREATE TABLE IF NOT EXISTS stock_daily (
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
        )
        """,
        "trade_date"
    ),

    # 2. 股票周K线
    (
        """
        CREATE TABLE IF NOT EXISTS stock_weekly (
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
        )
        """,
        "trade_date"
    ),

    # 3. 股票月K线
    (
        """
        CREATE TABLE IF NOT EXISTS stock_monthly (
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
        )
        """,
        "trade_date"
    ),

    # 4. 盘前股本/每日指标
    (
        """
        CREATE TABLE IF NOT EXISTS stock_daily_basic (
            ts_code        VARCHAR(16)   NOT NULL,
            trade_date     DATE          NOT NULL,
            close          NUMERIC(12,4),
            turnover_rate  NUMERIC(12,4),
            turnover_rate_f NUMERIC(12,4),
            volume_ratio   NUMERIC(12,4),
            pe             NUMERIC(16,4),
            pe_ttm         NUMERIC(16,4),
            pb             NUMERIC(16,4),
            ps             NUMERIC(16,4),
            ps_ttm         NUMERIC(16,4),
            dv_ratio       NUMERIC(12,4),
            dv_ttm         NUMERIC(12,4),
            total_share    NUMERIC(20,4),
            float_share    NUMERIC(20,4),
            free_share     NUMERIC(20,4),
            total_mv       NUMERIC(20,4),
            circ_mv        NUMERIC(20,4),
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 5. 复权因子
    (
        """
        CREATE TABLE IF NOT EXISTS stock_adj_factor (
            ts_code        VARCHAR(16)  NOT NULL,
            trade_date     DATE         NOT NULL,
            adj_factor     NUMERIC(16,8),
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 6. 集合竞价日聚合
    (
        """
        CREATE TABLE IF NOT EXISTS stock_auction_daily (
            ts_code         VARCHAR(16)  NOT NULL,
            trade_date      DATE         NOT NULL,
            open_price      NUMERIC(12,4),
            preclose_price  NUMERIC(12,4),
            auction_vol     BIGINT,
            auction_amount  NUMERIC(20,4),
            bid_vol_total   BIGINT,
            ask_vol_total   BIGINT,
            gap_pct         NUMERIC(12,4),
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 7. 资金流向
    (
        """
        CREATE TABLE IF NOT EXISTS stock_moneyflow_daily (
            ts_code         VARCHAR(16)  NOT NULL,
            trade_date      DATE         NOT NULL,
            buy_sm_vol      BIGINT,
            buy_sm_amount   NUMERIC(20,4),
            sell_sm_vol     BIGINT,
            sell_sm_amount  NUMERIC(20,4),
            buy_md_vol      BIGINT,
            buy_md_amount   NUMERIC(20,4),
            sell_md_vol     BIGINT,
            sell_md_amount  NUMERIC(20,4),
            buy_lg_vol      BIGINT,
            buy_lg_amount   NUMERIC(20,4),
            sell_lg_vol     BIGINT,
            sell_lg_amount  NUMERIC(20,4),
            buy_elg_vol     BIGINT,
            buy_elg_amount  NUMERIC(20,4),
            sell_elg_vol    BIGINT,
            sell_elg_amount NUMERIC(20,4),
            net_mf_vol      BIGINT,
            net_mf_amount   NUMERIC(20,4),
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 8. 龙虎榜
    (
        """
        CREATE TABLE IF NOT EXISTS stock_top_list_daily (
            ts_code         VARCHAR(16)  NOT NULL,
            trade_date      DATE         NOT NULL,
            name            VARCHAR(100),
            close           NUMERIC(12,4),
            pct_change      NUMERIC(12,4),
            turnover_rate   NUMERIC(12,4),
            amount          NUMERIC(20,4),
            l_sell          NUMERIC(20,4),
            l_buy           NUMERIC(20,4),
            l_amount        NUMERIC(20,4),
            net_amount      NUMERIC(20,4),
            net_rate        NUMERIC(12,4),
            amount_rate     NUMERIC(12,4),
            float_values    NUMERIC(20,4),
            reason          VARCHAR(200),
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 9. 两融汇总
    (
        """
        CREATE TABLE IF NOT EXISTS margin_summary_daily (
            trade_date      DATE         NOT NULL,
            exchange_id     VARCHAR(4)   NOT NULL,   -- SSE / SZSE
            rzye            NUMERIC(20,4),           -- 融资余额
            rzmre           NUMERIC(20,4),           -- 融资买入额
            rzche           NUMERIC(20,4),           -- 融资偿还额
            rqye            NUMERIC(20,4),           -- 融券余额
            rqmcl           NUMERIC(20,4),           -- 融券卖出量
            rzrqye          NUMERIC(20,4),           -- 两融余额
            PRIMARY KEY (trade_date, exchange_id)
        )
        """,
        "trade_date"
    ),

    # 10. 两融明细
    (
        """
        CREATE TABLE IF NOT EXISTS margin_detail_daily (
            trade_date      DATE         NOT NULL,
            ts_code         VARCHAR(16)  NOT NULL,
            name            VARCHAR(100),
            rzye            NUMERIC(20,4),
            rqye            NUMERIC(20,4),
            rzmre           NUMERIC(20,4),
            rqyl            NUMERIC(20,4),
            rzche           NUMERIC(20,4),
            rqchl           NUMERIC(20,4),
            rqmcl           NUMERIC(20,4),
            rzrqye          NUMERIC(20,4),
            PRIMARY KEY (trade_date, ts_code)
        )
        """,
        "trade_date"
    ),

    # 11. 大宗交易
    (
        """
        CREATE TABLE IF NOT EXISTS stock_block_trade (
            ts_code         VARCHAR(16)  NOT NULL,
            trade_date      DATE         NOT NULL,
            price           NUMERIC(16,4),
            vol             BIGINT,
            amount          NUMERIC(20,4),
            buyer           VARCHAR(200),
            seller          VARCHAR(200),
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 12. 指数日线
    (
        """
        CREATE TABLE IF NOT EXISTS index_daily (
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
        )
        """,
        "trade_date"
    ),

    # 13. 指数成分权重
    (
        """
        CREATE TABLE IF NOT EXISTS index_weight (
            index_code      VARCHAR(16)  NOT NULL,
            con_code        VARCHAR(16)  NOT NULL,
            trade_date      DATE         NOT NULL,
            weight          NUMERIC(12,6),
            PRIMARY KEY (index_code, con_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 14. 基金净值
    (
        """
        CREATE TABLE IF NOT EXISTS fund_nav (
            ts_code         VARCHAR(16)  NOT NULL,
            nav_date        DATE         NOT NULL,
            unit_nav        NUMERIC(16,6),
            accum_nav       NUMERIC(16,6),
            adj_nav         NUMERIC(16,6),
            PRIMARY KEY (ts_code, nav_date)
        )
        """,
        "nav_date"
    ),

    # 15. 基金日行情
    (
        """
        CREATE TABLE IF NOT EXISTS fund_daily (
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
        )
        """,
        "trade_date"
    ),

    # 16. ETF日行情
    (
        """
        CREATE TABLE IF NOT EXISTS etf_daily (
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
        )
        """,
        "trade_date"
    ),

    # 17. 券商一致预期
    (
        """
        CREATE TABLE IF NOT EXISTS broker_report_consensus (
            ts_code           VARCHAR(16)   NOT NULL,
            calc_date         DATE          NOT NULL,
            buy_count         INT,
            overweight_count  INT,
            hold_count        INT,
            underweight_count INT,
            sell_count        INT,
            total_count       INT,
            consensus_score   NUMERIC(6,4),
            avg_target        NUMERIC(12,4),
            avg_upside        NUMERIC(12,4),
            PRIMARY KEY (ts_code, calc_date)
        )
        """,
        "calc_date"
    ),

    # 18. 涨跌停价
    (
        """
        CREATE TABLE IF NOT EXISTS ref_stk_limit_daily (
            ts_code        VARCHAR(16)  NOT NULL,
            trade_date     DATE         NOT NULL,
            up_limit       NUMERIC(12,4),
            down_limit     NUMERIC(12,4),
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 19. 停牌信息
    (
        """
        CREATE TABLE IF NOT EXISTS ref_suspend_d (
            ts_code        VARCHAR(16)  NOT NULL,
            trade_date     DATE         NOT NULL,
            suspend_type   VARCHAR(10),
            suspend_reason VARCHAR(200),
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),
]

# ─────────────────────────────────────────────────────────────────────
# PostgreSQL 索引 DDL（在创建表之后单独执行）
# ─────────────────────────────────────────────────────────────────────

POSTGRES_INDEX_DDL_LIST = [
    # 交易日历
    "CREATE INDEX IF NOT EXISTS idx_trade_cal_date ON ref_trade_cal (cal_date DESC)",
    # 证券主档
    "CREATE INDEX IF NOT EXISTS idx_stock_industry ON ref_stock_basic (industry)",
    "CREATE INDEX IF NOT EXISTS idx_stock_status ON ref_stock_basic (list_status)",
    # 股票更名
    "CREATE INDEX IF NOT EXISTS idx_namechange_code ON ref_stock_namechange (ts_code, start_date DESC)",
    # 新股发行
    "CREATE INDEX IF NOT EXISTS idx_newshare_date ON ref_new_share (ipo_date)",
    # 指数主档
    "CREATE INDEX IF NOT EXISTS idx_index_type ON ref_index_basic (index_type)",
    # ETF主档
    "CREATE INDEX IF NOT EXISTS idx_etf_basic_type ON ref_etf_basic (fund_type)",
    "CREATE INDEX IF NOT EXISTS idx_etf_basic_status ON ref_etf_basic (list_status)",
    # 基金主档
    "CREATE INDEX IF NOT EXISTS idx_fund_basic_type ON ref_fund_basic (fund_type)",
    # 政策法规
    "CREATE INDEX IF NOT EXISTS idx_policy_date ON ref_policy_law (pub_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_policy_dept ON ref_policy_law (dept)",
    "CREATE INDEX IF NOT EXISTS idx_policy_type ON ref_policy_law (policy_type)",
    # 新闻资讯
    "CREATE INDEX IF NOT EXISTS idx_news_publish ON news_article (publish_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_news_code ON news_article (ts_code) WHERE ts_code IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_news_type ON news_article (news_type)",
    # 公告
    "CREATE INDEX IF NOT EXISTS idx_ann_date ON news_ann (ann_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ann_code ON news_ann (ts_code, ann_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ann_type ON news_ann (ann_type)",
    # 董秘互动
    "CREATE INDEX IF NOT EXISTS idx_interact_code ON board_secretary_interact (ts_code, question_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_interact_time ON board_secretary_interact (answer_time DESC) WHERE is_answered",
    # 券商研报
    "CREATE INDEX IF NOT EXISTS idx_report_date ON broker_report (publish_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_report_code ON broker_report (ts_code, publish_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_report_broker ON broker_report (broker, publish_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_report_rating ON broker_report (rating)",
    # 财务-利润表
    "CREATE INDEX IF NOT EXISTS idx_income_code ON fin_income (ts_code, end_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_income_ann ON fin_income (ann_date)",
    # 财务-资产负债表
    "CREATE INDEX IF NOT EXISTS idx_balancesheet_code ON fin_balancesheet (ts_code, end_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_balancesheet_ann ON fin_balancesheet (ann_date)",
    # 财务-现金流量表
    "CREATE INDEX IF NOT EXISTS idx_cashflow_code ON fin_cashflow (ts_code, end_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_cashflow_ann ON fin_cashflow (ann_date)",
    # 财务-指标
    "CREATE INDEX IF NOT EXISTS idx_fina_indicator_code ON fin_fina_indicator (ts_code, end_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fina_indicator_ann ON fin_fina_indicator (ann_date)",
    # 财务-预告
    "CREATE INDEX IF NOT EXISTS idx_forecast_code ON fin_forecast (ts_code, ann_date DESC)",
    # 财务-快报
    "CREATE INDEX IF NOT EXISTS idx_express_code ON fin_express (ts_code, ann_date DESC)",
    # 财务-分红
    "CREATE INDEX IF NOT EXISTS idx_dividend_code ON fin_dividend (ts_code, end_date DESC)",
]

# TimescaleDB Hypertable 专用索引
TIMESCALEDB_INDEX_DDL_LIST = [
    # stock_daily
    "CREATE INDEX IF NOT EXISTS idx_daily_code ON stock_daily (ts_code, trade_date DESC)",
    # stock_weekly
    "CREATE INDEX IF NOT EXISTS idx_weekly_code ON stock_weekly (ts_code, trade_date DESC)",
    # stock_monthly
    "CREATE INDEX IF NOT EXISTS idx_monthly_code ON stock_monthly (ts_code, trade_date DESC)",
    # stock_daily_basic
    "CREATE INDEX IF NOT EXISTS idx_daily_basic_code ON stock_daily_basic (ts_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_daily_basic_total_mv ON stock_daily_basic (trade_date, total_mv)",
    # stock_adj_factor
    "CREATE INDEX IF NOT EXISTS idx_adj_factor_code ON stock_adj_factor (ts_code, trade_date DESC)",
    # stock_auction_daily
    "CREATE INDEX IF NOT EXISTS idx_auction_code ON stock_auction_daily (ts_code, trade_date DESC)",
    # stock_moneyflow_daily
    "CREATE INDEX IF NOT EXISTS idx_moneyflow_code ON stock_moneyflow_daily (ts_code, trade_date DESC)",
    # stock_top_list_daily
    "CREATE INDEX IF NOT EXISTS idx_toplist_code ON stock_top_list_daily (ts_code, trade_date DESC)",
    # margin_summary_daily
    "CREATE INDEX IF NOT EXISTS idx_margin_summary_exchange ON margin_summary_daily (exchange_id, trade_date DESC)",
    # margin_detail_daily
    "CREATE INDEX IF NOT EXISTS idx_margin_detail_code ON margin_detail_daily (ts_code, trade_date DESC)",
    # stock_block_trade
    "CREATE INDEX IF NOT EXISTS idx_blocktrade_code ON stock_block_trade (ts_code, trade_date DESC)",
    # index_daily
    "CREATE INDEX IF NOT EXISTS idx_index_daily_code ON index_daily (ts_code, trade_date DESC)",
    # index_weight
    "CREATE INDEX IF NOT EXISTS idx_index_weight_code ON index_weight (index_code, trade_date DESC)",
    # fund_nav
    "CREATE INDEX IF NOT EXISTS idx_fund_nav_code ON fund_nav (ts_code, nav_date DESC)",
    # fund_daily
    "CREATE INDEX IF NOT EXISTS idx_fund_daily_code ON fund_daily (ts_code, trade_date DESC)",
    # etf_daily
    "CREATE INDEX IF NOT EXISTS idx_etf_daily_code ON etf_daily (ts_code, trade_date DESC)",
    # broker_report_consensus
    "CREATE INDEX IF NOT EXISTS idx_consensus_code ON broker_report_consensus (ts_code, calc_date DESC)",
    # ref_stk_limit_daily
    "CREATE INDEX IF NOT EXISTS idx_stk_limit_code ON ref_stk_limit_daily (ts_code, trade_date DESC)",
    # ref_suspend_d
    "CREATE INDEX IF NOT EXISTS idx_suspend_code ON ref_suspend_d (ts_code, trade_date DESC)",
]


class SchemaManager:
    """
    Schema 管理器
    统一管理所有 PostgreSQL 和 TimescaleDB 表的创建
    """

    @classmethod
    def ensure_postgres_tables(cls, engine: Engine) -> bool:
        """
        创建所有 PostgreSQL 普通表（不含 Hypertable 转换）

        Args:
            engine: SQLAlchemy Engine 实例

        Returns:
            bool: 全部创建成功返回 True
        """
        success = True
        with engine.connect() as conn:
            # 1. 创建所有表
            for i, ddl in enumerate(POSTGRES_DDL_LIST):
                try:
                    conn.execute(text(ddl))
                    conn.commit()
                except Exception as e:
                    logger.error(f"PostgreSQL 表创建失败 (第{i+1}个): {e}")
                    success = False

            # 2. 创建索引
            for idx_ddl in POSTGRES_INDEX_DDL_LIST:
                try:
                    conn.execute(text(idx_ddl))
                    conn.commit()
                except Exception as e:
                    logger.warning(f"PostgreSQL 索引创建失败: {e}")

        if success:
            logger.info(f"✅ PostgreSQL: {len(POSTGRES_DDL_LIST)} 张表 + {len(POSTGRES_INDEX_DDL_LIST)} 个索引已确保")
        return success

    @classmethod
    def ensure_timescaledb_tables(cls, engine: Engine) -> bool:
        """
        创建所有 TimescaleDB Hypertable 表

        Args:
            engine: SQLAlchemy Engine 实例

        Returns:
            bool: 全部创建成功返回 True
        """
        success = True
        with engine.connect() as conn:
            # 1. 确认 TimescaleDB 扩展已安装
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
                conn.commit()
                logger.info("TimescaleDB 扩展已确认")
            except Exception as e:
                logger.error(f"TimescaleDB 扩展安装失败: {e}")
                return False

            # 2. 创建 Hypertable 表
            for i, (ddl, time_col) in enumerate(TIMESCALEDB_DDL_LIST):
                try:
                    conn.execute(text(ddl))
                    conn.commit()
                except Exception as e:
                    logger.error(f"TimescaleDB 表创建失败 (第{i+1}个): {e}")
                    success = False
                    continue

                # 3. 转换为 Hypertable（从 DDL 中提取表名）
                try:
                    table_name = ddl.strip().split("CREATE TABLE IF NOT EXISTS ")[1].split(" ")[0].strip()
                    hypertable_sql = (
                        f"SELECT create_hypertable('{table_name}', '{time_col}', if_not_exists => TRUE)"
                    )
                    conn.execute(text(hypertable_sql))
                    conn.commit()
                except Exception as e:
                    logger.error(f"create_hypertable 失败 ({table_name}): {e}")
                    success = False

            # 4. 创建索引
            for idx_ddl in TIMESCALEDB_INDEX_DDL_LIST:
                try:
                    conn.execute(text(idx_ddl))
                    conn.commit()
                except Exception as e:
                    logger.warning(f"TimescaleDB 索引创建失败: {e}")

        if success:
            logger.info(
                f"✅ TimescaleDB: {len(TIMESCALEDB_DDL_LIST)} 张 Hypertable "
                f"+ {len(TIMESCALEDB_INDEX_DDL_LIST)} 个索引已确保"
            )
        return success

    @classmethod
    def ensure_all_tables(cls, engine: Engine) -> bool:
        """
        创建所有数据库表（PostgreSQL + TimescaleDB）

        Args:
            engine: SQLAlchemy Engine 实例

        Returns:
            bool: 全部创建成功返回 True
        """
        logger.info("=" * 60)
        logger.info("开始初始化全部数据库 Schema...")
        logger.info("=" * 60)

        pg_ok = cls.ensure_postgres_tables(engine)
        ts_ok = cls.ensure_timescaledb_tables(engine)

        total_tables = len(POSTGRES_DDL_LIST) + len(TIMESCALEDB_DDL_LIST)
        logger.info(f"Schema 初始化完成: {total_tables} 张表 (Pg={pg_ok}, TS={ts_ok})")

        return pg_ok and ts_ok

    @classmethod
    def table_exists(cls, engine: Engine, table_name: str) -> bool:
        """
        检查指定表是否存在

        Args:
            engine: SQLAlchemy Engine 实例
            table_name: 表名

        Returns:
            bool
        """
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT EXISTS ("
                        "  SELECT FROM information_schema.tables "
                        "  WHERE table_schema = 'public' AND table_name = :name"
                        ")"
                    ),
                    {"name": table_name}
                )
                return result.scalar()
        except Exception as e:
            logger.warning(f"检查表 {table_name} 存在性失败: {e}")
            return False
