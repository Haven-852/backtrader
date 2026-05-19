# -*- coding: utf-8 -*-
"""
数据拉取脚本 v2.0 — 使用 collector 正确方法拉取5年数据
限速: ~0.7s/请求（安全 < 120/min）
"""
import sys, os, time, logging, traceback
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv; load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger('data_pull')

from data_layer.config import config
from data_layer.db_manager import storage_manager
from data_layer.collectors.tushare_collector import TushareCollector
import pandas as pd

collector = TushareCollector()
DELAY = 0.75  # seconds between API calls

TARGET = [
    "000001.SZ", "000002.SZ", "600519.SH", "000858.SZ",
    "300750.SZ", "601318.SH", "600036.SH", "000333.SZ",
    "002415.SZ", "601166.SH"
]

def safe_save(df, table_name, method="append"):
    """Save DataFrame to PostgreSQL, handling errors gracefully."""
    if df is None or df.empty:
        return 0
    try:
        engine = storage_manager.get_postgres_engine()
        if engine:
            df.to_sql(table_name, engine, if_exists=method,
                      index=False, method="multi", chunksize=500)
            return len(df)
    except Exception as e:
        logger.error(f"  Save to {table_name} FAILED: {e}")
    return 0

def stats(desc):
    logger.info(f"\n{'='*60}")
    logger.info(f"  {desc}")
    logger.info(f"{'='*60}")

# ══════════════════════════════════════════════════════════════
# Step 1: Schema
# ══════════════════════════════════════════════════════════════
stats("Step 1: Schema")
try:
    engine = storage_manager.get_postgres_engine()
    if engine:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("  PostgreSQL connection OK")
except Exception as e:
    logger.error(f"  DB connection FAILED: {e}")

# ══════════════════════════════════════════════════════════════
# Step 2: Stock List
# ══════════════════════════════════════════════════════════════
stats("Step 2: Stock list")
time.sleep(DELAY)
try:
    from tushare.stock import cons as ct
    pro = collector._get_api("daily")
    if pro:
        stocks = pro.stock_basic(exchange='', list_status='L',
            fields='ts_code,symbol,name,area,industry,list_date')
        if stocks is not None and not stocks.empty:
            safe_save(stocks, "ref_stock_basic", "replace")
            logger.info(f"  Saved {len(stocks)} stocks to ref_stock_basic")
except Exception as e:
    logger.error(f"  Stock list ERROR: {e}")

# ══════════════════════════════════════════════════════════════
# Step 3: Daily bars (2021-05 ~ 2026-05)
# ══════════════════════════════════════════════════════════════
stats("Step 3: Daily bars")
StartD, EndD = "20210501", "20260510"
tot = 0
for ts in TARGET:
    time.sleep(DELAY)
    df = collector.get_daily(ts, start_date=StartD, end_date=EndD)
    n = safe_save(df, "stock_daily")
    tot += n
    logger.info(f"  {ts}: daily {n} rows")
logger.info(f"  Total daily: {tot} rows")

# ══════════════════════════════════════════════════════════════
# Step 4: Daily_basic (2021-05 ~ 2026-05) — use trade_date range
# ══════════════════════════════════════════════════════════════
stats("Step 4: Daily_basic")
tot = 0
for ts in TARGET:
    time.sleep(DELAY)
    df = collector.get_daily_basic(ts_code=ts, start_date=StartD, end_date=EndD)
    n = safe_save(df, "stock_daily_basic")
    tot += n
    logger.info(f"  {ts}: daily_basic {n} rows")
logger.info(f"  Total daily_basic: {tot} rows")

# ══════════════════════════════════════════════════════════════
# Step 5: Adj_factor (full history, no date filter)
# ══════════════════════════════════════════════════════════════
stats("Step 5: Adj_factor")
tot = 0
for ts in TARGET:
    time.sleep(DELAY)
    df = collector.get_adj_factor(ts_code=ts)  # returns full history
    n = safe_save(df, "stock_adj_factor")
    tot += n
    logger.info(f"  {ts}: adj_factor {n} rows")
logger.info(f"  Total adj_factor: {tot} rows")

# ══════════════════════════════════════════════════════════════
# Step 6: stk_limit (2021-05 ~ 2026-05)
# ══════════════════════════════════════════════════════════════
stats("Step 6: stk_limit")
tot = 0
for ts in TARGET:
    time.sleep(DELAY)
    df = collector.get_stk_limit(ts_code=ts, start_date=StartD, end_date=EndD)
    n = safe_save(df, "stk_limit_daily")
    tot += n
    logger.info(f"  {ts}: stk_limit {n} rows")
logger.info(f"  Total stk_limit: {tot} rows")

# ══════════════════════════════════════════════════════════════
# Step 7: Financial — income (2021 ~ 2025 fiscal years)
# ══════════════════════════════════════════════════════════════
# Tushare financial APIs use start_date/end_date for fiscal end_date
# Range: 20210101 ~ 20251231 covers all quarters 2021-2025
FinStart, FinEnd = "20210101", "20251231"
fin_tables = [
    ("income", "fin_income"),
    ("balancesheet", "fin_balancesheet"),
    ("cashflow", "fin_cashflow"),
    ("fina_indicator", "fin_fina_indicator"),
    ("dividend", "fin_dividend"),
    ("forecast", "fin_forecast"),
    ("express", "fin_express"),
]
for method_name, table_name in fin_tables:
    stats(f"Financial: {method_name} -> {table_name}")
    tot = 0
    getter = getattr(collector, f"get_{method_name}", None)
    if getter is None:
        logger.warning(f"  Method get_{method_name} not found, using _call_api")
        for ts in TARGET:
            time.sleep(DELAY)
            if method_name == "dividend":
                df = collector.get_dividend(ts_code=ts, start_date=FinStart, end_date=FinEnd)
            else:
                df = collector._call_api(method_name, method_name,
                    ts_code=ts, start_date=FinStart, end_date=FinEnd)
            n = safe_save(df, table_name)
            tot += n
            logger.debug(f"  {ts}: {n} rows")
    else:
        for ts in TARGET:
            time.sleep(DELAY)
            df = getter(ts, start_date=FinStart, end_date=FinEnd)
            n = safe_save(df, table_name)
            tot += n
            logger.debug(f"  {ts}: {n} rows")
    logger.info(f"  {table_name} total: {tot} rows")

# ══════════════════════════════════════════════════════════════
# Step 8: Moneyflow (2021 ~ 2026)
# ══════════════════════════════════════════════════════════════
stats("Step 8: Moneyflow")
tot = 0
for ts in TARGET:
    time.sleep(DELAY)
    df = collector.get_moneyflow(ts_code=ts, start_date=StartD, end_date=EndD)
    n = safe_save(df, "stock_moneyflow_daily")
    tot += n
    logger.info(f"  {ts}: moneyflow {n} rows")
logger.info(f"  Total moneyflow: {tot} rows")

# ══════════════════════════════════════════════════════════════
# Step 9: Moneyflow HSGT (2021 ~ 2026)
# ══════════════════════════════════════════════════════════════
stats("Step 9: Moneyflow HSGT")
time.sleep(DELAY)
df = collector.get_moneyflow_hsgt(start_date=StartD, end_date=EndD)
n = safe_save(df, "stock_moneyflow_hsgt")
logger.info(f"  HSGT moneyflow: {n} rows")

# ══════════════════════════════════════════════════════════════
# Step 10: Stock factor (pro) — like daily_basic with more fields
# ══════════════════════════════════════════════════════════════
stats("Step 10: Stock factor (stk_factor)")
tot = 0
for ts in TARGET:
    time.sleep(DELAY)
    df = collector.get_stk_factor(ts_code=ts, start_date=StartD, end_date=EndD)
    n = safe_save(df, "stock_daily_factor")
    tot += n
    logger.info(f"  {ts}: stk_factor {n} rows")
logger.info(f"  Total stk_factor: {tot} rows")

# ══════════════════════════════════════════════════════════════
# Step 11: Index daily
# ══════════════════════════════════════════════════════════════
stats("Step 11: Index daily")
IDX = ["000001.SH", "399001.SZ", "399006.SZ", "000688.SH", "000300.SH"]
tot = 0
for ix in IDX:
    time.sleep(DELAY)
    df = collector._call_api("index_daily", "index_daily",
        ts_code=ix, start_date=StartD, end_date=EndD)
    n = safe_save(df, "index_daily")
    tot += n
    logger.info(f"  {ix}: index_daily {n} rows")
logger.info(f"  Total index_daily: {tot} rows")

# ══════════════════════════════════════════════════════════════
# Step 12: ETF daily (fund_daily)
# ══════════════════════════════════════════════════════════════
stats("Step 12: ETF daily")
ETF = ["510050.SH", "510300.SH", "510500.SH", "159919.SZ", "512880.SH"]
tot = 0
for etf in ETF:
    time.sleep(DELAY)
    df = collector._call_api("fund_daily", "fund_daily",
        ts_code=etf, start_date=StartD, end_date=EndD)
    n = safe_save(df, "etf_daily")
    tot += n
    logger.info(f"  {etf}: fund_daily {n} rows")
logger.info(f"  Total fund_daily: {tot} rows")

# ══════════════════════════════════════════════════════════════
# Step 13: Suspend data
# ══════════════════════════════════════════════════════════════
stats("Step 13: Suspend")
time.sleep(DELAY)
df = collector.get_suspend_d(suspend_type="S")
n = safe_save(df, "ref_suspend_d")
logger.info(f"  Suspend: {n} rows")

# ══════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════
stats("SUMMARY — Table Row Counts")
engine = storage_manager.get_postgres_engine()
if engine:
    tables = [
        "stock_daily", "stock_daily_basic", "stock_adj_factor",
        "stk_limit_daily", "stock_daily_factor",
        "fin_income", "fin_balancesheet", "fin_cashflow",
        "fin_fina_indicator", "fin_dividend", "fin_forecast", "fin_express",
        "stock_moneyflow_daily", "stock_moneyflow_hsgt",
        "index_daily", "etf_daily", "ref_suspend_d", "ref_stock_basic"
    ]
    for tbl in tables:
        try:
            cnt = pd.read_sql(f"SELECT COUNT(*) as n FROM {tbl}", engine)
            n = cnt.iloc[0,0] if not cnt.empty else 0
            logger.info(f"  {tbl:<30s} {n:>8} rows")
        except Exception as e:
            logger.warning(f"  {tbl:<30s} N/A: {e}")

logger.info("\n✅ Data pull v2.0 complete!")
