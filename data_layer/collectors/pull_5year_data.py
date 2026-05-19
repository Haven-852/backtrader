# -*- coding: utf-8 -*-
"""
数据拉取主脚本 v1.0
从 Tushare 拉取最近5年数据 (2021-2026)，存入对应数据库表
限速: 0.6s/请求 (120次/分钟以内)
"""
import sys, os, time, logging
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger('data_pull')

from data_layer.config import config
from data_layer.db_manager import storage_manager
from data_layer.collectors.tushare_collector import TushareCollector

# Init
collector = TushareCollector()

# ─── Step 1: Create tables ───────────────────────────────────
logger.info("=" * 60)
logger.info("STEP 1: Ensuring database schema...")
logger.info("=" * 60)
if storage_manager.ensure_schema():
    logger.info("Schema ready")
else:
    logger.warning("Schema creation failed - tables may not exist yet, continuing anyway")

# ─── Step 2: Pull A-share stock list ─────────────────────────
logger.info("=" * 60)
logger.info("STEP 2: Getting stock list...")
logger.info("=" * 60)
time.sleep(0.6)
stocks_df = collector.get_daily(ts_code="000001.SZ", start_date="20210501", end_date="20210507")
# Get stock list from index_basic or from reference table
time.sleep(0.6)
api = collector._get_api("daily")
stock_basic = None
try:
    # Try to get stock list
    from tushare.stock import cons as ct
    stock_basic = api.stock_basic(exchange='', list_status='L',
                                   fields='ts_code,symbol,name,area,industry,list_date')
    if stock_basic is not None and not stock_basic.empty:
        logger.info(f"Got stock list: {len(stock_basic)} stocks")
        # Save to PostgreSQL
        engine = storage_manager.get_postgres_engine()
        if engine:
            stock_basic.to_sql("ref_stock_basic", engine, if_exists="replace",
                               index=False, method="multi", chunksize=1000)
            logger.info("Stock list saved to ref_stock_basic")
except Exception as e:
    logger.warning(f"Could not get stock list: {e}")

# Define target stocks (top 10 liquid for now, expandable)
TARGET_STOCKS = [
    "000001.SZ", "000002.SZ", "600519.SH", "000858.SZ",
    "300750.SZ", "601318.SH", "600036.SH", "000333.SZ",
    "002415.SZ", "601166.SH"
]

# ─── Step 3: Pull daily bars (5 years) ───────────────────────
logger.info("=" * 60)
logger.info("STEP 3: Pulling daily bars (2021-2026)...")
logger.info("=" * 60)

START = "20210501"
END = "20260510"

total_daily = 0
for ts_code in TARGET_STOCKS:
    time.sleep(0.6)
    try:
        df = collector.get_daily(ts_code=ts_code, start_date=START, end_date=END)
        if df is not None and not df.empty:
            engine = storage_manager.get_postgres_engine()
            if engine:
                df.to_sql("stock_daily", engine, if_exists="append",
                          index=False, method="multi", chunksize=1000)
            total_daily += len(df)
            logger.info(f"  {ts_code}: {len(df)} rows saved")
        else:
            logger.warning(f"  {ts_code}: no data")
    except Exception as e:
        logger.error(f"  {ts_code} ERROR: {e}")
logger.info(f"Daily bars total: {total_daily} rows")

# ─── Step 4: Pull daily_basic (5 years) ──────────────────────
logger.info("=" * 60)
logger.info("STEP 4: Pulling daily_basic (2021-2026)...")
logger.info("=" * 60)

total_basic = 0
for ts_code in TARGET_STOCKS:
    time.sleep(0.6)
    try:
        df = collector.get_daily_basic(ts_code=ts_code, start_date=START, end_date=END)
        if df is not None and not df.empty:
            engine = storage_manager.get_postgres_engine()
            if engine:
                df.to_sql("stock_daily_basic", engine, if_exists="append",
                          index=False, method="multi", chunksize=500)
            total_basic += len(df)
            logger.info(f"  {ts_code}: {len(df)} rows")
        else:
            logger.warning(f"  {ts_code}: no daily_basic data")
    except Exception as e:
        logger.error(f"  {ts_code} daily_basic ERROR: {e}")
logger.info(f"Daily_basic total: {total_basic} rows")

# ─── Step 5: Pull adj_factor ─────────────────────────────────
logger.info("=" * 60)
logger.info("STEP 5: Pulling adj_factor...")
logger.info("=" * 60)

total_adj = 0
for ts_code in TARGET_STOCKS:
    time.sleep(0.6)
    try:
        df = collector.get_adj_factor(ts_code=ts_code)
        if df is not None and not df.empty:
            engine = storage_manager.get_postgres_engine()
            if engine:
                df.to_sql("stock_adj_factor", engine, if_exists="append",
                          index=False, method="multi", chunksize=1000)
            total_adj += len(df)
            logger.info(f"  {ts_code}: {len(df)} rows")
    except Exception as e:
        logger.error(f"  {ts_code} adj_factor ERROR: {e}")
logger.info(f"Adj_factor total: {total_adj} rows")

# ─── Step 6: Pull financial statements ───────────────────────
logger.info("=" * 60)
logger.info("STEP 6: Pulling financial data (2021-2026)...")
logger.info("=" * 60)

for interface, table, desc in [
    ("income", "fin_income", "income statement"),
    ("balancesheet", "fin_balancesheet", "balance sheet"),
    ("cashflow", "fin_cashflow", "cash flow"),
    ("fina_indicator", "fin_fina_indicator", "financial indicators"),
    ("dividend", "fin_dividend_d", "dividend"),
]:
    total = 0
    logger.info(f"--- {desc} ({interface}) ---")
    for ts_code in TARGET_STOCKS:
        time.sleep(0.6)
        try:
            df = collector._call_api(interface, interface,
                ts_code=ts_code, start_date="20210101", end_date="20260101")
            if df is not None and not df.empty:
                engine = storage_manager.get_postgres_engine()
                if engine:
                    df.to_sql(table, engine, if_exists="append",
                              index=False, method="multi", chunksize=500)
                total += len(df)
                logger.debug(f"  {ts_code}: {len(df)} rows")
            else:
                logger.debug(f"  {ts_code}: no {interface} data")
        except Exception as e:
            logger.error(f"  {ts_code} {interface} ERROR: {e}")
    logger.info(f"  {desc} total: {total} rows")

# ─── Step 7: Pull moneyflow ──────────────────────────────────
logger.info("=" * 60)
logger.info("STEP 7: Pulling moneyflow (2021-2026)...")
logger.info("=" * 60)

total_mf = 0
for ts_code in TARGET_STOCKS:
    time.sleep(0.6)
    try:
        df = collector.get_moneyflow(ts_code=ts_code, start_date=START, end_date=END)
        if df is not None and not df.empty:
            engine = storage_manager.get_postgres_engine()
            if engine:
                df.to_sql("stock_moneyflow_daily", engine, if_exists="append",
                          index=False, method="multi", chunksize=500)
            total_mf += len(df)
            logger.info(f"  {ts_code}: {len(df)} rows")
    except Exception as e:
        logger.error(f"  {ts_code} moneyflow ERROR: {e}")
logger.info(f"Moneyflow total: {total_mf} rows")

# ─── Step 8: Pull index daily ────────────────────────────────
logger.info("=" * 60)
logger.info("STEP 8: Pulling index daily (major indices)...")
logger.info("=" * 60)

INDEX_CODES = ["000001.SH", "399001.SZ", "399006.SZ", "000688.SH", "000300.SH"]
total_idx = 0
for idx_code in INDEX_CODES:
    time.sleep(0.7)
    try:
        df = collector._call_api("index_daily", "index_daily",
            ts_code=idx_code, start_date=START, end_date=END)
        if df is not None and not df.empty:
            engine = storage_manager.get_postgres_engine()
            if engine:
                df.to_sql("index_daily", engine, if_exists="append",
                          index=False, method="multi", chunksize=1000)
            total_idx += len(df)
            logger.info(f"  {idx_code}: {len(df)} rows")
        else:
            logger.warning(f"  {idx_code}: no data")
    except Exception as e:
        logger.error(f"  {idx_code} ERROR: {e}")
logger.info(f"Index daily total: {total_idx} rows")

# ─── Step 9: Pull fund daily ─────────────────────────────────
logger.info("=" * 60)
logger.info("STEP 9: Pulling ETF daily data...")
logger.info("=" * 60)

ETF_CODES = ["510050.SH", "510300.SH", "510500.SH", "159919.SZ", "512880.SH"]
total_etf = 0
for etf_code in ETF_CODES:
    time.sleep(0.7)
    try:
        df = collector._call_api("fund_daily", "fund_daily",
            ts_code=etf_code, start_date=START, end_date=END)
        if df is not None and not df.empty:
            engine = storage_manager.get_postgres_engine()
            if engine:
                df.to_sql("etf_daily", engine, if_exists="append",
                          index=False, method="multi", chunksize=1000)
            total_etf += len(df)
            logger.info(f"  {etf_code}: {len(df)} rows")
        else:
            logger.warning(f"  {etf_code}: no data")
    except Exception as e:
        logger.error(f"  {etf_code} ERROR: {e}")
logger.info(f"ETF daily total: {total_etf} rows")

# ─── Step 10: Pull suspend_d ─────────────────────────────────
logger.info("=" * 60)
logger.info("STEP 10: Pulling suspend/resume data...")
logger.info("=" * 60)
time.sleep(0.7)
try:
    df = collector.get_suspend_d()
    if df is not None and not df.empty:
        engine = storage_manager.get_postgres_engine()
        if engine:
            df.to_sql("ref_suspend_d", engine, if_exists="append",
                      index=False, method="multi", chunksize=1000)
        logger.info(f"Suspend data: {len(df)} rows")
except Exception as e:
    logger.error(f"Suspend ERROR: {e}")

# ─── Step 11: Moneyflow HSGT ─────────────────────────────────
logger.info("=" * 60)
logger.info("STEP 11: Pulling HSGT moneyflow...")
logger.info("=" * 60)
time.sleep(0.7)
try:
    df = collector.get_moneyflow_hsgt(start_date=START, end_date=END)
    if df is not None and not df.empty:
        engine = storage_manager.get_postgres_engine()
        if engine:
            df.to_sql("stock_moneyflow_hsgt", engine, if_exists="append",
                      index=False, method="multi", chunksize=500)
        logger.info(f"HSGT moneyflow: {len(df)} rows")
except Exception as e:
    logger.error(f"HSGT ERROR: {e}")

# ─── Summary ─────────────────────────────────────────────────
logger.info("=" * 60)
logger.info("DATA PULL COMPLETE")
logger.info("=" * 60)

# Verify stored data
engine = storage_manager.get_postgres_engine()
if engine:
    import pandas as pd
    tables = [
        "stock_daily", "stock_daily_basic", "stock_adj_factor",
        "fin_income", "fin_balancesheet", "fin_cashflow", "fin_fina_indicator",
        "fin_dividend_d", "stock_moneyflow_daily", "stock_moneyflow_hsgt",
        "index_daily", "etf_daily", "ref_suspend_d", "ref_stock_basic"
    ]
    logger.info("\nTable Row Counts:")
    for tbl in tables:
        try:
            cnt = pd.read_sql(f"SELECT COUNT(*) as n FROM {tbl}", engine)
            n = cnt.iloc[0,0] if not cnt.empty else 0
            logger.info(f"  {tbl:<30s} {n:>8} rows")
        except Exception as e:
            logger.info(f"  {tbl:<30s} TABLE NOT FOUND: {e}")

logger.info("\nDone! Data has been stored in PostgreSQL/TimescaleDB.")
logger.info(f"Database: {config.postgres['host']}:{config.postgres['port']}/{config.postgres['database']}")
