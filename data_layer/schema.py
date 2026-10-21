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

    # 2b. 全市场股票清单（供 fill_batch 等按 offset/limit 切片；含 board_type）
    """
    CREATE TABLE IF NOT EXISTS ref_market_universe (
        ts_code        VARCHAR(16)  PRIMARY KEY,
        symbol         VARCHAR(10)  NOT NULL,
        name           VARCHAR(50),
        area           VARCHAR(20),
        industry       VARCHAR(50),
        market         VARCHAR(10),
        board_type     VARCHAR(16)  NOT NULL,   -- 主板 / 科创板 / 创业板 / 北交所 / ST
        exchange       VARCHAR(8),              -- SH / SZ / BJ
        list_date      DATE,
        list_status    VARCHAR(1)   NOT NULL DEFAULT 'L',
        is_hs          VARCHAR(1),
        updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        "desc"         TEXT,
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

    # 19b. 主营构成
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

    # 19c. 审计意见
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

    # 19d. 财报披露日程
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

    # 19e. 指数周/月线、每日估值、行业分类、基金分红/持仓/复权
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
    """
    CREATE TABLE IF NOT EXISTS fut_daily (
        ts_code      VARCHAR(24)   NOT NULL,
        trade_date   DATE          NOT NULL,
        pre_close    NUMERIC(16,4),
        pre_settle   NUMERIC(16,4),
        open         NUMERIC(16,4),
        high         NUMERIC(16,4),
        low          NUMERIC(16,4),
        close        NUMERIC(16,4),
        settle       NUMERIC(16,4),
        change1      NUMERIC(16,4),
        change2      NUMERIC(16,4),
        vol          NUMERIC(20,4),
        amount       NUMERIC(24,4),
        oi           NUMERIC(20,4),
        oi_chg       NUMERIC(20,4),
        updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (ts_code, trade_date)
    )
    """,

    # 21. 策略选股运行
    """
    CREATE TABLE IF NOT EXISTS strategy_screener_run (
        id           SERIAL PRIMARY KEY,
        asof         DATE NOT NULL,
        ma_period    INT DEFAULT 5,
        below_pct    NUMERIC(8,4) DEFAULT 0,
        filters      JSONB,
        hit_count    INT,
        filled_rows  INT,
        created_at   TIMESTAMPTZ DEFAULT NOW()
    )
    """,

    # 22. 策略选股命中
    """
    CREATE TABLE IF NOT EXISTS strategy_screener_hit (
        run_id       INT NOT NULL,
        ts_code      VARCHAR(16) NOT NULL,
        name         VARCHAR(64),
        market       VARCHAR(16),
        industry     VARCHAR(50),
        is_st        BOOLEAN,
        trade_date   DATE,
        close        NUMERIC(12,4),
        ma5          NUMERIC(12,4),
        below_pct    NUMERIC(12,4),
        setup_type   VARCHAR(8),
        score        NUMERIC(8,2),
        label        VARCHAR(32),
        pct_chg      NUMERIC(12,4),
        reason       TEXT,
        decline_days INT,
        expect_up_days INT,
        PRIMARY KEY (run_id, ts_code)
    )
    """,

    # 20. 行情看板自选股（持续日更名单）
    """
    CREATE TABLE IF NOT EXISTS dash_watchlist (
        ts_code         VARCHAR(16)   PRIMARY KEY,
        name            VARCHAR(64),
        short_name      VARCHAR(64),
        kind            VARCHAR(16)   DEFAULT 'stock',
        industry        VARCHAR(64),
        continuous      BOOLEAN       DEFAULT TRUE,
        created_at      TIMESTAMPTZ   DEFAULT NOW(),
        last_synced_at  TIMESTAMPTZ,
        last_sync_ok    BOOLEAN,
        last_sync_error TEXT
    )
    """,

    # 23. 纸面账户
    """
    CREATE TABLE IF NOT EXISTS paper_account (
        id              SERIAL PRIMARY KEY,
        name            VARCHAR(64)   NOT NULL,
        initial_cash    NUMERIC(20,4) NOT NULL,
        cash            NUMERIC(20,4) NOT NULL,
        equity          NUMERIC(20,4) NOT NULL,
        currency        VARCHAR(8)    DEFAULT 'CNY',
        status          VARCHAR(16)   DEFAULT 'active',
        lookback_guard  BOOLEAN       DEFAULT TRUE,
        t1_asof         DATE,
        created_at      TIMESTAMPTZ   DEFAULT NOW(),
        updated_at      TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # 24. 纸面持仓
    """
    CREATE TABLE IF NOT EXISTS paper_position (
        account_id      INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        ts_code         VARCHAR(16)   NOT NULL,
        quantity        NUMERIC(20,4) NOT NULL DEFAULT 0,
        sellable_qty    NUMERIC(20,4) NOT NULL DEFAULT 0,
        frozen_qty      NUMERIC(20,4) NOT NULL DEFAULT 0,
        avg_cost        NUMERIC(16,4) NOT NULL DEFAULT 0,
        market_price    NUMERIC(16,4),
        market_value    NUMERIC(20,4),
        unrealized_pnl  NUMERIC(20,4),
        updated_at      TIMESTAMPTZ   DEFAULT NOW(),
        PRIMARY KEY (account_id, ts_code)
    )
    """,

    # 25. 纸面委托
    """
    CREATE TABLE IF NOT EXISTS paper_order (
        id              SERIAL PRIMARY KEY,
        account_id      INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        ts_code         VARCHAR(16)   NOT NULL,
        side            VARCHAR(8)    NOT NULL,
        order_type      VARCHAR(16)   DEFAULT 'market',
        quantity        NUMERIC(20,4) NOT NULL,
        filled_qty      NUMERIC(20,4) DEFAULT 0,
        status          VARCHAR(16)   NOT NULL DEFAULT 'pending',
        lookback_guard  BOOLEAN       NOT NULL DEFAULT TRUE,
        exec_price_mode VARCHAR(16)   NOT NULL DEFAULT 'next_open',
        signal_date     DATE,
        exec_date       DATE,
        limit_price     NUMERIC(16,4),
        reason          TEXT,
        reject_reason   TEXT,
        created_at      TIMESTAMPTZ   DEFAULT NOW(),
        updated_at      TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # 26. 纸面成交
    """
    CREATE TABLE IF NOT EXISTS paper_fill (
        id              SERIAL PRIMARY KEY,
        order_id        INT           NOT NULL REFERENCES paper_order(id) ON DELETE CASCADE,
        account_id      INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        ts_code         VARCHAR(16)   NOT NULL,
        side            VARCHAR(8)    NOT NULL,
        quantity        NUMERIC(20,4) NOT NULL,
        price           NUMERIC(16,4) NOT NULL,
        amount          NUMERIC(20,4) NOT NULL,
        commission      NUMERIC(16,4) DEFAULT 0,
        stamp_tax       NUMERIC(16,4) DEFAULT 0,
        trade_date      DATE          NOT NULL,
        created_at      TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # 27. 纸面每日净值
    """
    CREATE TABLE IF NOT EXISTS paper_equity_daily (
        account_id      INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        trade_date      DATE          NOT NULL,
        cash            NUMERIC(20,4) NOT NULL,
        market_value    NUMERIC(20,4) NOT NULL,
        equity          NUMERIC(20,4) NOT NULL,
        daily_return    NUMERIC(12,6),
        created_at      TIMESTAMPTZ   DEFAULT NOW(),
        PRIMARY KEY (account_id, trade_date)
    )
    """,

    # 28. 纸面决策审计（LLM 感知-决策-执行-反馈）
    """
    CREATE TABLE IF NOT EXISTS paper_decision_log (
        id                  SERIAL PRIMARY KEY,
        account_id          INT           NOT NULL REFERENCES paper_account(id) ON DELETE CASCADE,
        session_id          VARCHAR(36),
        user_query          TEXT,
        assistant_summary   TEXT,
        tool_trace          JSONB,
        order_ids           INT[],
        meta                JSONB,
        created_at          TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # 29. 交易盯盘名单（选股层产出）
    """
    CREATE TABLE IF NOT EXISTS trade_watchlist (
        id              SERIAL PRIMARY KEY,
        ts_code         VARCHAR(16)   NOT NULL,
        name            VARCHAR(64),
        source          VARCHAR(32)   NOT NULL,
        reason          TEXT,
        score           NUMERIC(8,2),
        asof            DATE          NOT NULL,
        active          BOOLEAN       DEFAULT TRUE,
        meta            JSONB,
        created_at      TIMESTAMPTZ   DEFAULT NOW(),
        UNIQUE (ts_code, asof, source)
    )
    """,

    # 30. 盘中巡检事件
    """
    CREATE TABLE IF NOT EXISTS trade_scan_event (
        id              SERIAL PRIMARY KEY,
        ts_code         VARCHAR(16)   NOT NULL,
        asof            DATE          NOT NULL,
        event_type      VARCHAR(32)   NOT NULL,
        severity        VARCHAR(8)    NOT NULL DEFAULT 'soft',
        price           NUMERIC(16,4),
        detail          JSONB,
        created_at      TIMESTAMPTZ   DEFAULT NOW()
    )
    """,

    # ─── 交易知识库（机会 ≠ 成交）────────────────────────
    """
    CREATE TABLE IF NOT EXISTS users (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        username    VARCHAR(100) NOT NULL,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_llm_profile (
        id            SERIAL PRIMARY KEY,
        user_id       VARCHAR(64) NOT NULL DEFAULT 'default',
        name          VARCHAR(80) NOT NULL,
        provider      VARCHAR(32) NOT NULL,
        model         VARCHAR(128) NOT NULL,
        api_key       TEXT,
        base_url      VARCHAR(256),
        temperature   NUMERIC(4,2) DEFAULT 0.7,
        max_tokens    INT DEFAULT 4096,
        enabled       BOOLEAN DEFAULT TRUE,
        is_active     BOOLEAN DEFAULT FALSE,
        extra         JSONB,
        created_at    TIMESTAMPTZ DEFAULT NOW(),
        updated_at    TIMESTAMPTZ DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trading_accounts (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name        VARCHAR(100) NOT NULL,
        kind        VARCHAR(20) NOT NULL DEFAULT 'paper',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stocks (
        id            BIGSERIAL PRIMARY KEY,
        symbol        VARCHAR(20) NOT NULL UNIQUE,
        exchange      VARCHAR(20),
        name          VARCHAR(100),
        industry      VARCHAR(100),
        sector        VARCHAR(100),
        listing_date  DATE,
        status        VARCHAR(20) NOT NULL DEFAULT 'active',
        metadata      JSONB,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id    UUID REFERENCES trading_accounts(id) ON DELETE CASCADE,
        name          VARCHAR(255) NOT NULL,
        description   TEXT,
        strategy_type VARCHAR(50),
        timeframe     VARCHAR(30),
        market        VARCHAR(50),
        entry_logic   JSONB,
        exit_logic    JSONB,
        risk_logic    JSONB,
        status        VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_snapshot (
        id                    BIGSERIAL PRIMARY KEY,
        trade_date            DATE NOT NULL,
        snapshot_time         TIMESTAMPTZ,
        index_code            VARCHAR(20),
        index_name            VARCHAR(50),
        close_price           NUMERIC(18,4),
        change_pct            NUMERIC(10,4),
        volume                NUMERIC(24,4),
        turnover              NUMERIC(24,4),
        advancers             INTEGER,
        decliners             INTEGER,
        limit_up_count        INTEGER,
        limit_down_count      INTEGER,
        max_limit_up_streak   INTEGER,
        market_breadth        NUMERIC(10,4),
        volatility            NUMERIC(10,4),
        sentiment_score       NUMERIC(10,4),
        northbound_flow       NUMERIC(24,4),
        main_fund_flow        NUMERIC(24,4),
        hot_sectors           JSONB,
        sector_strength       JSONB,
        market_regime         VARCHAR(30),
        regime_confidence     NUMERIC(5,4),
        raw_data              JSONB,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS opportunity (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id          UUID REFERENCES trading_accounts(id) ON DELETE CASCADE,
        stock_id            BIGINT REFERENCES stocks(id),
        discovered_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        trade_date          DATE,
        opportunity_type    VARCHAR(50),
        trigger_reason      TEXT,
        market_regime       VARCHAR(30),
        sector_strength     NUMERIC(10,4),
        technical_score     NUMERIC(10,4),
        fundamental_score   NUMERIC(10,4),
        sentiment_score     NUMERIC(10,4),
        opportunity_score   NUMERIC(10,4),
        confidence          NUMERIC(5,4),
        status              VARCHAR(30) NOT NULL DEFAULT 'NEW',
        evidence            JSONB,
        source              VARCHAR(50),
        embedding           vector(1024),
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_thesis (
        id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id               UUID REFERENCES trading_accounts(id) ON DELETE CASCADE,
        opportunity_id           UUID REFERENCES opportunity(id),
        stock_id                 BIGINT REFERENCES stocks(id),
        created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        direction                VARCHAR(10),
        thesis_title             VARCHAR(255),
        thesis_text              TEXT,
        market_context           TEXT,
        sector_context           TEXT,
        entry_reason             TEXT,
        entry_conditions         JSONB,
        exit_conditions          JSONB,
        invalidation_conditions  JSONB,
        target_price             NUMERIC(18,4),
        stop_price               NUMERIC(18,4),
        planned_position_pct     NUMERIC(10,4),
        planned_holding_days     INTEGER,
        confidence               NUMERIC(5,4),
        expected_return          NUMERIC(10,4),
        expected_risk            NUMERIC(10,4),
        strategy_id              UUID REFERENCES strategy(id),
        rule_ids                 JSONB,
        evidence                 JSONB,
        embedding                vector(1024),
        status                   VARCHAR(30) NOT NULL DEFAULT 'DRAFT'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_execution (
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        thesis_id             UUID REFERENCES trade_thesis(id) ON DELETE CASCADE,
        account_id            UUID REFERENCES trading_accounts(id) ON DELETE CASCADE,
        stock_id              BIGINT REFERENCES stocks(id),
        side                  VARCHAR(10),
        order_type            VARCHAR(20),
        planned_price         NUMERIC(18,4),
        executed_price        NUMERIC(18,4),
        planned_quantity      NUMERIC(20,4),
        executed_quantity     NUMERIC(20,4),
        planned_position_pct  NUMERIC(10,4),
        actual_position_pct   NUMERIC(10,4),
        fee                   NUMERIC(18,4),
        slippage              NUMERIC(18,6),
        executed_at           TIMESTAMPTZ,
        execution_deviation   JSONB,
        broker                VARCHAR(100),
        order_id              VARCHAR(100),
        notes                 TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_outcome (
        id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        thesis_id                 UUID REFERENCES trade_thesis(id) ON DELETE CASCADE,
        realized_pnl              NUMERIC(20,4),
        return_pct                NUMERIC(10,4),
        benchmark_return_pct      NUMERIC(10,4),
        alpha_pct                 NUMERIC(10,4),
        max_favorable_excursion   NUMERIC(10,4),
        max_adverse_excursion     NUMERIC(10,4),
        holding_days              NUMERIC(10,2),
        exit_reason               VARCHAR(50),
        outcome_type              VARCHAR(30),
        logic_result              VARCHAR(30),
        market_change             TEXT,
        actual_vs_expected        TEXT,
        completed_at              TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attribution (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        outcome_id          UUID REFERENCES trade_outcome(id) ON DELETE CASCADE,
        primary_category    VARCHAR(30),
        secondary_category  VARCHAR(50),
        severity            VARCHAR(20),
        confidence          NUMERIC(5,4),
        evidence            JSONB,
        ai_analysis         TEXT,
        human_verdict       TEXT,
        confirmed           BOOLEAN NOT NULL DEFAULT FALSE,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lesson (
        id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id             UUID REFERENCES trading_accounts(id) ON DELETE CASCADE,
        source_type            VARCHAR(30),
        source_id              UUID,
        title                  VARCHAR(255),
        content                TEXT,
        evidence_summary       TEXT,
        applicable_conditions  JSONB,
        excluded_conditions    JSONB,
        sample_size            INTEGER,
        win_rate               NUMERIC(10,4),
        expectancy             NUMERIC(10,4),
        avg_return             NUMERIC(10,4),
        max_drawdown           NUMERIC(10,4),
        confidence             NUMERIC(5,4),
        status                 VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
        embedding              vector(1024),
        created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id          UUID REFERENCES trading_accounts(id) ON DELETE CASCADE,
        lesson_id           UUID REFERENCES lesson(id),
        strategy_id         UUID REFERENCES strategy(id),
        rule_code           VARCHAR(50) UNIQUE,
        name                VARCHAR(255),
        category            VARCHAR(30),
        description         TEXT,
        conditions          JSONB,
        action              JSONB,
        exceptions          JSONB,
        evidence            JSONB,
        sample_size         INTEGER,
        win_rate            NUMERIC(10,4),
        expectancy          NUMERIC(10,4),
        profit_factor       NUMERIC(10,4),
        max_drawdown        NUMERIC(10,4),
        in_sample_result    JSONB,
        out_sample_result   JSONB,
        status              VARCHAR(30) NOT NULL DEFAULT 'DRAFT',
        confidence          NUMERIC(5,4),
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rule_observable (
        id          VARCHAR(40) PRIMARY KEY,
        label       VARCHAR(120) NOT NULL,
        ops         JSONB NOT NULL,
        hint        TEXT,
        basis       VARCHAR(40) NOT NULL DEFAULT 'note',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_knowledge (
        id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        title              VARCHAR(500),
        source_type        VARCHAR(30),
        author             VARCHAR(255),
        source_url         TEXT,
        published_at       TIMESTAMPTZ,
        content            TEXT,
        summary            TEXT,
        claims             JSONB,
        strategies         JSONB,
        market_scope       JSONB,
        evidence_level     VARCHAR(30),
        credibility_notes  TEXT,
        embedding          vector(1024),
        created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS experiment (
        id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id           UUID REFERENCES trading_accounts(id) ON DELETE CASCADE,
        strategy_name        VARCHAR(255),
        hypothesis           TEXT,
        hypothesis_source    VARCHAR(30),
        source_knowledge_id  UUID REFERENCES external_knowledge(id),
        market               VARCHAR(50),
        timeframe            VARCHAR(30),
        start_date           DATE,
        end_date             DATE,
        train_start          DATE,
        train_end            DATE,
        validation_start     DATE,
        validation_end       DATE,
        test_start           DATE,
        test_end             DATE,
        parameters           JSONB,
        entry_rules          JSONB,
        exit_rules           JSONB,
        risk_rules           JSONB,
        transaction_cost     NUMERIC(10,6),
        slippage             NUMERIC(10,6),
        sample_size          INTEGER,
        win_rate             NUMERIC(10,4),
        expectancy           NUMERIC(10,4),
        profit_factor        NUMERIC(10,4),
        sharpe               NUMERIC(10,4),
        max_drawdown         NUMERIC(10,4),
        result               JSONB,
        overfit_check        JSONB,
        status               VARCHAR(30) NOT NULL DEFAULT 'IDEA',
        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS limit_break_count INTEGER
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS volume_ratio NUMERIC(10,4)
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS turnover_rate NUMERIC(10,4)
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS amplitude NUMERIC(10,4)
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS turnover_change_pct NUMERIC(10,4)
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS up_down_ratio NUMERIC(10,4)
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS turnover_trillion NUMERIC(10,4)
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS index_board JSONB
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS flow_board JSONB
    """,
    """
    ALTER TABLE market_snapshot ADD COLUMN IF NOT EXISTS source VARCHAR(30)
    """,
    """
    ALTER TABLE trade_thesis ADD COLUMN IF NOT EXISTS trade_date DATE
    """,
    """
    ALTER TABLE trade_thesis ADD COLUMN IF NOT EXISTS symbol VARCHAR(32)
    """,
    """
    ALTER TABLE trade_thesis ADD COLUMN IF NOT EXISTS stock_name VARCHAR(100)
    """,
    """
    ALTER TABLE trade_thesis ADD COLUMN IF NOT EXISTS strategy_name VARCHAR(100)
    """,
    """
    ALTER TABLE trade_thesis ADD COLUMN IF NOT EXISTS planned_entry_price NUMERIC(18,4)
    """,
    """
    ALTER TABLE trade_thesis ADD COLUMN IF NOT EXISTS skill_inputs JSONB
    """,
    """
    CREATE TABLE IF NOT EXISTS trade_skill_evaluation (
        thesis_id UUID PRIMARY KEY REFERENCES trade_thesis(id) ON DELETE CASCADE,
        direction_score SMALLINT,
        sector_score SMALLINT,
        stock_score SMALLINT,
        entry_score SMALLINT,
        exit_score SMALLINT,
        position_score SMALLINT,
        stop_score SMALLINT,
        execution_score SMALLINT,
        reasons JSONB,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS sector_code VARCHAR(16)
    """,
    """
    ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS sector_name VARCHAR(64)
    """,
    """
    ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS sector_level VARCHAR(32)
    """,
    """
    ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS phase VARCHAR(20)
    """,
    """
    ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS matched_types JSONB
    """,
    """
    ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS amount_ratio NUMERIC(10,4)
    """,
    """
    ALTER TABLE opportunity ADD COLUMN IF NOT EXISTS main_net NUMERIC(24,4)
    """,
    """
    ALTER TABLE rule ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1
    """,
    """
    ALTER TABLE rule ADD COLUMN IF NOT EXISTS rule_type VARCHAR(30)
    """,
    """
    ALTER TABLE rule ADD COLUMN IF NOT EXISTS scope VARCHAR(30)
    """,
    """
    ALTER TABLE rule ADD COLUMN IF NOT EXISTS check_verdict VARCHAR(30)
    """,
    """
    ALTER TABLE rule ADD COLUMN IF NOT EXISTS check_items JSONB
    """,
    """
    ALTER TABLE rule ADD COLUMN IF NOT EXISTS forward_result JSONB
    """,
    """
    ALTER TABLE rule ADD COLUMN IF NOT EXISTS recent_result JSONB
    """,
    """
    ALTER TABLE rule ADD COLUMN IF NOT EXISTS source_lesson_ids JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS account_id UUID
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS knowledge_type VARCHAR(40)
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS key_findings JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS methodology JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS conditions JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS risks JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS limitations JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS market VARCHAR(40)
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS symbols JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS topics JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS credibility_score NUMERIC(5,4)
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS relevance_score NUMERIC(5,4)
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS applicability JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS hypotheses JSONB
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS status VARCHAR(30) NOT NULL DEFAULT 'CANDIDATE'
    """,
    """
    ALTER TABLE external_knowledge ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS claim_id VARCHAR(40)
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS experiment_code VARCHAR(32)
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS source_experience_id UUID
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS universe VARCHAR(64)
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS benchmark VARCHAR(32)
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS version VARCHAR(16) NOT NULL DEFAULT '1.0'
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS conclusion TEXT
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS failure_reason VARCHAR(40)
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS test_unlocked BOOLEAN NOT NULL DEFAULT FALSE
    """,
    """
    ALTER TABLE experiment ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """,
    """
    CREATE TABLE IF NOT EXISTS experiment_run (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        experiment_id UUID NOT NULL REFERENCES experiment(id) ON DELETE CASCADE,
        version VARCHAR(16) NOT NULL,
        phase VARCHAR(20) NOT NULL,
        run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        parameters JSONB,
        data_snapshot JSONB,
        result JSONB,
        warnings JSONB
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_knowledge_experience (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        external_knowledge_id UUID NOT NULL REFERENCES external_knowledge(id) ON DELETE CASCADE,
        experience_id UUID NOT NULL REFERENCES lesson(id) ON DELETE CASCADE,
        claim_id VARCHAR(40),
        relationship VARCHAR(30) NOT NULL,
        confidence NUMERIC(5,4),
        note TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    ALTER TABLE trade_outcome ADD COLUMN IF NOT EXISTS buy_price NUMERIC(18,4)
    """,
    """
    ALTER TABLE trade_outcome ADD COLUMN IF NOT EXISTS sell_price NUMERIC(18,4)
    """,
    """
    ALTER TABLE attribution ADD COLUMN IF NOT EXISTS redo_choice VARCHAR(40)
    """,
    """
    ALTER TABLE attribution ADD COLUMN IF NOT EXISTS mistake_note TEXT
    """,
    """
    ALTER TABLE attribution ADD COLUMN IF NOT EXISTS dimensions JSONB
    """,
    """
    CREATE TABLE IF NOT EXISTS market_memory (
        id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        trade_date           DATE NOT NULL UNIQUE,
        snapshot_id          BIGINT REFERENCES market_snapshot(id) ON DELETE SET NULL,
        ai_summary           TEXT,
        summary_evidence     JSONB,
        summary_confidence   NUMERIC(5,4),
        narrative            TEXT,
        structure            JSONB,
        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_regime (
        id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        memory_id      UUID REFERENCES market_memory(id) ON DELETE CASCADE,
        trade_date     DATE NOT NULL,
        regime_label   VARCHAR(50) NOT NULL,
        confidence     NUMERIC(5,4),
        evidence       JSONB,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_event (
        id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        memory_id    UUID REFERENCES market_memory(id) ON DELETE CASCADE,
        trade_date   DATE NOT NULL,
        event_time   VARCHAR(8),
        title        TEXT NOT NULL,
        detail       TEXT,
        severity     VARCHAR(20),
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_hypothesis (
        id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        memory_id           UUID REFERENCES market_memory(id) ON DELETE CASCADE,
        trade_date          DATE NOT NULL,
        hypothesis_code     VARCHAR(40),
        statement           TEXT NOT NULL,
        basis               JSONB,
        conditions          JSONB,
        verify_on           DATE,
        status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
        validation_result   JSONB,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intraday_prediction (
        id                  BIGSERIAL PRIMARY KEY,
        experiment_id       VARCHAR(64) NOT NULL,
        stock_code          VARCHAR(16) NOT NULL,
        prediction_time     TIMESTAMPTZ NOT NULL,
        p_up_first          DOUBLE PRECISION,
        p_down_first        DOUBLE PRECISION,
        p_neutral           DOUBLE PRECISION,
        expected_upside_pct DOUBLE PRECISION,
        expected_downside_pct DOUBLE PRECISION,
        signal              VARCHAR(32),
        model_version       VARCHAR(80) NOT NULL,
        feature_version     VARCHAR(40) NOT NULL,
        data_snapshot_id    VARCHAR(80),
        dataset_type        VARCHAR(20),
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intraday_model_run (
        id                  BIGSERIAL PRIMARY KEY,
        experiment_id       VARCHAR(64) NOT NULL,
        model_version       VARCHAR(80) NOT NULL,
        train_start         TIMESTAMPTZ,
        train_end           TIMESTAMPTZ,
        validation_start    TIMESTAMPTZ,
        validation_end      TIMESTAMPTZ,
        test_start          TIMESTAMPTZ,
        test_end            TIMESTAMPTZ,
        parameters          JSONB,
        metrics             JSONB,
        code_version        VARCHAR(40),
        status              VARCHAR(40) NOT NULL,
        created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS intraday_label (
        id                  BIGSERIAL PRIMARY KEY,
        experiment_id       VARCHAR(64) NOT NULL,
        stock_code          VARCHAR(16) NOT NULL,
        bar_time            TIMESTAMPTZ NOT NULL,
        label               VARCHAR(20) NOT NULL,
        label_version       VARCHAR(40) NOT NULL,
        horizon             INT NOT NULL,
        barrier_atr_mult    DOUBLE PRECISION NOT NULL,
        atr_window          INT NOT NULL,
        dataset_type        VARCHAR(20),
        generated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
            PRIMARY KEY (ts_code, trade_date, price, vol, buyer, seller)
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

    # 20. 东财行业板块指数日（dc_index）
    (
        """
        CREATE TABLE IF NOT EXISTS sector_dc_index_daily (
            ts_code        VARCHAR(16)  NOT NULL,
            trade_date     DATE         NOT NULL,
            name           VARCHAR(64),
            leading_name   VARCHAR(64),
            leading_code   VARCHAR(16),
            pct_change     NUMERIC(12,4),
            leading_pct    NUMERIC(12,4),
            total_mv       NUMERIC(20,4),
            turnover_rate  NUMERIC(12,4),
            up_num         INT,
            down_num       INT,
            idx_type       VARCHAR(32),
            industry_level VARCHAR(32),
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 21. 东财行业板块日行情（dc_daily）
    (
        """
        CREATE TABLE IF NOT EXISTS sector_dc_daily (
            ts_code        VARCHAR(16)  NOT NULL,
            trade_date     DATE         NOT NULL,
            open           NUMERIC(16,4),
            high           NUMERIC(16,4),
            low            NUMERIC(16,4),
            close          NUMERIC(16,4),
            change         NUMERIC(16,4),
            pct_change     NUMERIC(12,4),
            vol            BIGINT,
            amount         NUMERIC(24,4),
            swing          NUMERIC(12,4),
            turnover_rate  NUMERIC(12,4),
            category       VARCHAR(32),
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ts_code, trade_date)
        )
        """,
        "trade_date"
    ),

    # 22. 东财行业板块资金流向
    (
        """
        CREATE TABLE IF NOT EXISTS sector_moneyflow_daily (
            trade_date            DATE         NOT NULL,
            content_type          VARCHAR(16)  NOT NULL,
            ts_code               VARCHAR(16)  NOT NULL,
            name                  VARCHAR(64),
            pct_change            NUMERIC(12,4),
            close                 NUMERIC(16,4),
            net_amount            NUMERIC(24,4),
            net_amount_rate       NUMERIC(12,4),
            buy_elg_amount        NUMERIC(24,4),
            buy_elg_amount_rate   NUMERIC(12,4),
            buy_lg_amount         NUMERIC(24,4),
            buy_lg_amount_rate    NUMERIC(12,4),
            buy_md_amount         NUMERIC(24,4),
            buy_md_amount_rate    NUMERIC(12,4),
            buy_sm_amount         NUMERIC(24,4),
            buy_sm_amount_rate    NUMERIC(12,4),
            buy_sm_amount_stock   VARCHAR(64),
            rank_no               INT,
            updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ts_code, trade_date, content_type)
        )
        """,
        "trade_date"
    ),

    # 23. 东财板块成分股
    (
        """
        CREATE TABLE IF NOT EXISTS sector_dc_member (
            trade_date     DATE         NOT NULL,
            ts_code        VARCHAR(16)  NOT NULL,
            con_code       VARCHAR(16)  NOT NULL,
            name           VARCHAR(64),
            close          NUMERIC(16,4),
            pct_chg        NUMERIC(12,4),
            amount         NUMERIC(24,4),
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ts_code, trade_date, con_code)
        )
        """,
        "trade_date"
    ),

    # 24. 东财行业板块层级（成分包含推断：L1→L2→L3）
    (
        """
        CREATE TABLE IF NOT EXISTS sector_dc_hierarchy (
            child_code     VARCHAR(16)  NOT NULL,
            parent_code    VARCHAR(16)  NOT NULL,
            child_level    VARCHAR(32),
            parent_level   VARCHAR(32),
            child_name     VARCHAR(64),
            parent_name    VARCHAR(64),
            score          NUMERIC(8,4),
            method         VARCHAR(32)  DEFAULT 'member_containment',
            asof_date      DATE,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (child_code)
        )
        """,
        None
    ),

    # 24b. 东财行业板块目录（一级/二级/三级代码与名称）
    (
        """
        CREATE TABLE IF NOT EXISTS ref_sector_dc_basic (
            ts_code        VARCHAR(16)  PRIMARY KEY,
            name           VARCHAR(64),
            industry_level VARCHAR(32),
            idx_type       VARCHAR(32)  DEFAULT '行业板块',
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        None
    ),

    # 25. 个股当日/历史分时（1 分钟）
    (
        """
        CREATE TABLE IF NOT EXISTS stock_intraday_mins (
            ts_code        VARCHAR(16)    NOT NULL,
            bar_time       TIMESTAMPTZ    NOT NULL,
            trade_date     DATE           NOT NULL,
            freq           VARCHAR(8)     NOT NULL DEFAULT '1min',
            open           NUMERIC(12,4),
            high           NUMERIC(12,4),
            low            NUMERIC(12,4),
            close          NUMERIC(12,4),
            vol            BIGINT,
            amount         NUMERIC(20,4),
            avg_price      NUMERIC(12,4),
            PRIMARY KEY (bar_time, ts_code)
        )
        """,
        "bar_time"
    ),

    # 26. 北向/港股通持股
    (
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
        "trade_date"
    ),

    # 27. 十大股东（流通/全部）
    (
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
        "end_date"
    ),

    # 28. LightGBM 日频训练特征表（价格/量能/行业/市场 + OHLC 辅助列 + 标签）
    # y_high / y_low 为标签，不可作输入特征；open/high/low/close 仅采集，不作 LightGBM 特征
    (
        """
        CREATE TABLE IF NOT EXISTS ml_train_daily (
            symbol                  VARCHAR(16)   NOT NULL,
            trade_date              DATE          NOT NULL,
            industry_code           VARCHAR(32),
            industry_name           VARCHAR(64),
            -- 价格与趋势
            ret_1                   NUMERIC(16,8),
            ret_3                   NUMERIC(16,8),
            ret_5                   NUMERIC(16,8),
            ret_10                  NUMERIC(16,8),
            ret_20                  NUMERIC(16,8),
            ret_60                  NUMERIC(16,8),
            close_ma5               NUMERIC(16,8),
            close_ma10              NUMERIC(16,8),
            close_ma20              NUMERIC(16,8),
            ma5_ma20                NUMERIC(16,8),
            atr_5_pct               NUMERIC(16,8),
            atr_14_pct              NUMERIC(16,8),
            volatility_5            NUMERIC(16,8),
            volatility_10           NUMERIC(16,8),
            volatility_20           NUMERIC(16,8),
            range_1                 NUMERIC(16,8),
            range_mean_5            NUMERIC(16,8),
            range_mean_20           NUMERIC(16,8),
            upper_shadow            NUMERIC(16,8),
            lower_shadow            NUMERIC(16,8),
            body_pct                NUMERIC(16,8),
            close_location          NUMERIC(16,8),
            high_20_ratio           NUMERIC(16,8),
            low_20_ratio            NUMERIC(16,8),
            -- 量能与换手
            volume_ratio_5          NUMERIC(16,8),
            volume_ratio_20         NUMERIC(16,8),
            amount_ratio_20         NUMERIC(16,8),
            turnover_rate           NUMERIC(16,8),
            turnover_ma5            NUMERIC(16,8),
            turnover_ma20           NUMERIC(16,8),
            turnover_std20          NUMERIC(16,8),
            price_volume_corr20     NUMERIC(16,8),
            log_amount              NUMERIC(16,8),
            log_float_mv            NUMERIC(16,8),
            log_total_mv            NUMERIC(16,8),
            listing_age             NUMERIC(12,4),
            board_type              VARCHAR(16),
            industry_l1             VARCHAR(64),
            beta_60                 NUMERIC(16,8),
            turnover_mean_60        NUMERIC(16,8),
            volatility_mean_60      NUMERIC(16,8),
            limit_touch_count_20    INT,
            -- 市场环境
            hs300_ret_1             NUMERIC(16,8),
            hs300_ret_5             NUMERIC(16,8),
            zz1000_ret_5            NUMERIC(16,8),
            market_volatility_20    NUMERIC(16,8),
            market_amount_ratio     NUMERIC(16,8),
            market_adv_decl_ratio   NUMERIC(16,8),
            market_limit_up_count   INT,
            market_limit_down_count INT,
            size_style_spread       NUMERIC(16,8),
            -- 行业相对
            industry_ret_1          NUMERIC(16,8),
            industry_ret_5          NUMERIC(16,8),
            industry_ret_20         NUMERIC(16,8),
            industry_vol_20         NUMERIC(16,8),
            industry_amount_ratio   NUMERIC(16,8),
            industry_breadth        NUMERIC(16,8),
            stock_excess_ret_5      NUMERIC(16,8),
            industry_rank_ret_5     NUMERIC(16,8),
            stock_rank_industry     NUMERIC(16,8),
            -- 当日现象（供 LightGBM 学习一字/停牌）
            session_flag            VARCHAR(16),
            is_one_word             SMALLINT,
            is_halted               SMALLINT,
            -- 当日 OHLC（采集用，不作 LightGBM 输入特征）
            open                    NUMERIC(12,4),
            high                    NUMERIC(12,4),
            low                     NUMERIC(12,4),
            close                   NUMERIC(12,4),
            -- 训练标签：次日高低相对当日收盘；最后一根允许空
            y_high                  NUMERIC(16,8),
            y_low                   NUMERIC(16,8),
            train_ready             SMALLINT,
            updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, trade_date)
        )
        """,
        "trade_date"
    ),
]

# ─────────────────────────────────────────────────────────────────────
# PostgreSQL 索引 DDL（在创建表之后单独执行）
# ─────────────────────────────────────────────────────────────────────

POSTGRES_INDEX_DDL_LIST = [
    "CREATE INDEX IF NOT EXISTS idx_user_llm_profile_user ON user_llm_profile (user_id, is_active DESC, id)",
    # 交易日历
    "CREATE INDEX IF NOT EXISTS idx_trade_cal_date ON ref_trade_cal (cal_date DESC)",
    # 证券主档
    "CREATE INDEX IF NOT EXISTS idx_stock_industry ON ref_stock_basic (industry)",
    "CREATE INDEX IF NOT EXISTS idx_stock_status ON ref_stock_basic (list_status)",
    "CREATE INDEX IF NOT EXISTS idx_market_universe_board ON ref_market_universe (board_type, list_status)",
    "CREATE INDEX IF NOT EXISTS idx_market_universe_status ON ref_market_universe (list_status, ts_code)",
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
    "CREATE INDEX IF NOT EXISTS idx_mainbz_code ON fin_mainbz (ts_code, end_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_code ON fin_audit (ts_code, end_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_disclosure_end ON fin_disclosure_date (end_date, ts_code)",
    "CREATE INDEX IF NOT EXISTS idx_disclosure_code ON fin_disclosure_date (ts_code, end_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_index_weekly_code ON index_weekly (ts_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_index_monthly_code ON index_monthly (ts_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_index_daily_basic_code ON index_daily_basic (ts_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_index_classify_src ON ref_index_classify (src, level)",
    "CREATE INDEX IF NOT EXISTS idx_fund_div_code ON fund_div (ts_code, ex_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fund_portfolio_code ON fund_portfolio (ts_code, end_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fund_adj_code ON fund_adj (ts_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_share_float_code ON stock_share_float (ts_code, float_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_pledge_stat_code ON stock_pledge_stat (ts_code, end_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_holdertrade_code ON stock_holdertrade (ts_code, ann_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_cyq_perf_code ON stock_cyq_perf (ts_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_cyq_chips_code ON stock_cyq_chips (ts_code, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_report_rc_code ON stock_report_rc (ts_code, report_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ths_member_con ON ths_member (con_code)",
    "CREATE INDEX IF NOT EXISTS idx_cb_daily_code ON cb_daily (ts_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fut_basic_ex ON fut_basic (exchange)",
    # 东财行业板块
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_index_date ON sector_dc_index_daily (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_index_level ON sector_dc_index_daily (trade_date, industry_level)",
    "CREATE INDEX IF NOT EXISTS idx_sector_member_code ON sector_dc_member (ts_code, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_member_con ON sector_dc_member (con_code, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_sector_dc_daily_code ON sector_dc_daily (ts_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sector_hierarchy_parent ON sector_dc_hierarchy (parent_code)",
    "CREATE INDEX IF NOT EXISTS idx_ref_sector_dc_level ON ref_sector_dc_basic (industry_level)",
    # 纸面交易
    "CREATE INDEX IF NOT EXISTS idx_paper_account_status ON paper_account (status)",
    "CREATE INDEX IF NOT EXISTS idx_paper_position_code ON paper_position (ts_code)",
    "CREATE INDEX IF NOT EXISTS idx_paper_order_account ON paper_order (account_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_order_pending ON paper_order (status, exec_date)",
    "CREATE INDEX IF NOT EXISTS idx_paper_fill_account ON paper_fill (account_id, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_equity_account ON paper_equity_daily (account_id, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_decision_account ON paper_decision_log (account_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_paper_decision_session ON paper_decision_log (session_id)",
    "CREATE INDEX IF NOT EXISTS idx_trade_watchlist_asof ON trade_watchlist (asof DESC, active)",
    "CREATE INDEX IF NOT EXISTS idx_trade_watchlist_code ON trade_watchlist (ts_code, asof DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trade_scan_event_asof ON trade_scan_event (asof DESC, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trade_scan_event_code ON trade_scan_event (ts_code, asof, event_type)",
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
    # stock_intraday_mins
    "CREATE INDEX IF NOT EXISTS idx_intraday_mins_code_date ON stock_intraday_mins (ts_code, trade_date, bar_time)",
    "CREATE INDEX IF NOT EXISTS idx_trading_accounts_user ON trading_accounts (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_market_snapshot_date ON market_snapshot (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_market_snapshot_index_date ON market_snapshot (index_code, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_market_snapshot_regime_date ON market_snapshot (market_regime, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_market_snapshot_sentiment_date ON market_snapshot (sentiment_score, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_opportunity_account_date ON opportunity (account_id, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_opportunity_stock ON opportunity (stock_id)",
    "CREATE INDEX IF NOT EXISTS idx_trade_thesis_account ON trade_thesis (account_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_trade_execution_thesis ON trade_execution (thesis_id)",
    "CREATE INDEX IF NOT EXISTS idx_trade_outcome_thesis ON trade_outcome (thesis_id)",
    "CREATE INDEX IF NOT EXISTS idx_attribution_outcome ON attribution (outcome_id)",
    "CREATE INDEX IF NOT EXISTS idx_lesson_account ON lesson (account_id)",
    "CREATE INDEX IF NOT EXISTS idx_rule_account ON rule (account_id)",
    "CREATE INDEX IF NOT EXISTS idx_experiment_account ON experiment (account_id)",
    "CREATE INDEX IF NOT EXISTS idx_market_memory_date ON market_memory (trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_market_regime_date ON market_regime (trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_market_event_memory ON market_event (memory_id, event_time)",
    "CREATE INDEX IF NOT EXISTS idx_market_hypothesis_status ON market_hypothesis (status, verify_on)",
    "CREATE INDEX IF NOT EXISTS idx_intraday_prediction_model ON intraday_prediction (model_version, stock_code, prediction_time)",
    "CREATE INDEX IF NOT EXISTS idx_intraday_model_run_exp ON intraday_model_run (experiment_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_intraday_label_exp ON intraday_label (experiment_id, label_version, stock_code, bar_time)",
    # ml_train_daily 训练表
    "CREATE INDEX IF NOT EXISTS idx_ml_train_daily_date ON ml_train_daily (trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ml_train_daily_industry ON ml_train_daily (industry_code, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ml_train_daily_board ON ml_train_daily (board_type, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ml_train_daily_ready ON ml_train_daily (train_ready, trade_date DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ml_train_daily_date_symbol ON ml_train_daily (trade_date, symbol)",
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
            # 0. 扩展：部分知识库表依赖 pgvector / gen_random_uuid
            for ext_sql in (
                "CREATE EXTENSION IF NOT EXISTS vector",
                'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
                "CREATE EXTENSION IF NOT EXISTS pgcrypto",
            ):
                try:
                    conn.execute(text(ext_sql))
                    conn.commit()
                except Exception as e:
                    logger.warning(f"扩展安装跳过 ({ext_sql}): {e}")
                    conn.rollback()

            # 1. 创建所有表
            for i, ddl in enumerate(POSTGRES_DDL_LIST):
                try:
                    conn.execute(text(ddl))
                    conn.commit()
                except Exception as e:
                    logger.error(f"PostgreSQL 表创建失败 (第{i+1}个): {e}")
                    success = False
                    conn.rollback()

            # 2. 创建索引
            for idx_ddl in POSTGRES_INDEX_DDL_LIST:
                try:
                    conn.execute(text(idx_ddl))
                    conn.commit()
                except Exception as e:
                    logger.warning(f"PostgreSQL 索引创建失败: {e}")
                    conn.rollback()

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
                    conn.rollback()
                    continue

                # 3. 转换为 Hypertable（时间列为空则保持普通表）
                try:
                    table_name = ddl.strip().split("CREATE TABLE IF NOT EXISTS ")[1].split(" ")[0].strip()
                    if not time_col:
                        continue
                    hypertable_sql = (
                        f"SELECT create_hypertable('{table_name}', '{time_col}', if_not_exists => TRUE)"
                    )
                    conn.execute(text(hypertable_sql))
                    conn.commit()
                except Exception as e:
                    logger.error(f"create_hypertable 失败 ({table_name}): {e}")
                    success = False
                    conn.rollback()

            # 4. 创建索引
            for idx_ddl in TIMESCALEDB_INDEX_DDL_LIST:
                try:
                    conn.execute(text(idx_ddl))
                    conn.commit()
                except Exception as e:
                    logger.warning(f"TimescaleDB 索引创建失败: {e}")
                    conn.rollback()

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
