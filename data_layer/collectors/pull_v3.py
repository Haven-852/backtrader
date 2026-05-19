# -*- coding: utf-8 -*-
"""
数据全量拉取 v3.0 — 覆盖所有有权限的表，支持断点续传
限速: 0.75s/请求（安全 < 120/min）

进度文件: pull_v3_progress.json — 记录每个阶段的完成状态
日志文件: pull_v3.log — 详细日志
"""
import sys, os, time, logging, json, traceback
from datetime import datetime, timedelta
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv; load_dotenv()
import pandas as pd
import numpy as np

from data_layer.config import config
from data_layer.db_manager import storage_manager
from data_layer.collectors.tushare_collector import TushareCollector
from data_layer.collectors.advanced_collector import AdvancedCollector

# ─── Config ───────────────────────────────────────────────────
DELAY = 0.75
START = "20210501"
END = "20260510"
FIN_START = "20210101"
FIN_END = "20251231"
PROGRESS_FILE = "pull_v3_progress.json"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler("pull_v3.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('pull_v3')

# Init
collector = TushareCollector()
adv = AdvancedCollector()
engine = storage_manager.get_postgres_engine()

# ─── Progress management ─────────────────────────────────────
def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {"completed_steps": [], "stock_index": {}, "started": None, "last_update": None}

def save_progress(p):
    p["last_update"] = datetime.now().isoformat()
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(p, f, indent=2)

progress = load_progress()
if not progress.get("started"):
    progress["started"] = datetime.now().isoformat()
    save_progress(progress)

def step_done(name):
    return name in progress.get("completed_steps", [])

def mark_done(name):
    if name not in progress["completed_steps"]:
        progress["completed_steps"].append(name)
    save_progress(progress)

# ─── Helpers ─────────────────────────────────────────────────
def safe_save(df, table_name, method="append"):
    if df is None or df.empty:
        return 0
    try:
        # Clean NaN/Inf values that cause DB errors
        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.where(pd.notnull(df), None)
        
        # Try bulk insert first
        try:
            df.to_sql(table_name, engine, if_exists=method,
                      index=False, method="multi", chunksize=500)
            return len(df)
        except Exception as bulk_err:
            if "duplicate" not in str(bulk_err).lower() and "unique" not in str(bulk_err).lower():
                raise bulk_err
            # Bulk failed due to duplicates — fall through to row-by-row
            pass
        
        # Row-by-row insert, skipping duplicates
        count = 0
        for _, row in df.iterrows():
            try:
                pd.DataFrame([row]).to_sql(table_name, engine, if_exists="append",
                                          index=False, method=None)
                count += 1
            except Exception:
                pass  # duplicate, skip
        return count
    except Exception as e:
        logger.error(f"Save to {table_name} FAILED: {str(e)[:150]}")
        return 0

def get_all_stocks():
    """Get all A-share stocks from ref_stock_basic table"""
    try:
        stocks = pd.read_sql("SELECT DISTINCT ts_code FROM ref_stock_basic ORDER BY ts_code", engine)
        return list(stocks['ts_code'])
    except:
        logger.warning("Cannot read ref_stock_basic, using top 10")
        return [
            "000001.SZ","000002.SZ","600519.SH","000858.SZ",
            "300750.SZ","601318.SH","600036.SH","000333.SZ",
            "002415.SZ","601166.SH"
        ]

def pull_for_stocks(stocks, fetch_fn, table_name, step_name, chunk_size=50):
    """Generic pull for a list of stocks with progress tracking"""
    if step_done(step_name):
        logger.info(f"[SKIP] {step_name} — already completed")
        return 0
    
    si = progress["stock_index"].get(step_name, 0)
    total_stocks = len(stocks)
    total = 0
    
    logger.info(f"[{step_name}] Starting {total_stocks} stocks from index {si} -> {table_name}")
    
    for i in range(si, total_stocks):
        ts = stocks[i]
        time.sleep(DELAY)
        try:
            df = fetch_fn(ts)
            n = safe_save(df, table_name)
            total += n
            if (i + 1) % chunk_size == 0 or (i + 1) == total_stocks:
                logger.info(f"[{step_name}] {i+1}/{total_stocks} stocks, {total} rows so far | {ts}")
        except Exception as e:
            logger.error(f"[{step_name}] {ts} ERROR: {e}")
        
        # Save progress every chunk
        if (i + 1) % chunk_size == 0:
            progress["stock_index"][step_name] = i + 1
            save_progress(progress)
    
    mark_done(step_name)
    logger.info(f"[{step_name}] COMPLETE: {total} rows from {total_stocks} stocks")
    return total

def pull_standalone(fetch_fn, table_name, step_name):
    """Pull standalone data (no per-stock loop)"""
    if step_done(step_name):
        logger.info(f"[SKIP] {step_name} — already completed")
        return 0
    time.sleep(DELAY)
    try:
        df = fetch_fn()
        n = safe_save(df, table_name)
        if n > 0:
            mark_done(step_name)
        logger.info(f"[{step_name}] {n} rows -> {table_name}")
        return n
    except Exception as e:
        logger.error(f"[{step_name}] ERROR: {e}")
        return 0

def pull_batch_for_stocks(stocks, fetch_fn, table_name, step_name, chunk_size=30):
    """Pull data in batch mode (all stocks single call)"""
    if step_done(step_name):
        logger.info(f"[SKIP] {step_name} — already completed")
        return 0
    
    total = 0
    total_stocks = len(stocks)
    si = progress["stock_index"].get(step_name, 0)
    
    logger.info(f"[{step_name}] Starting from stock {si}/{total_stocks}")
    
    for i in range(si, total_stocks, chunk_size):
        batch = stocks[i:i+chunk_size]
        for ts in batch:
            time.sleep(DELAY)
            try:
                df = fetch_fn(ts)
                n = safe_save(df, table_name)
                total += n
            except Exception as e:
                logger.error(f"[{step_name}] {ts} ERROR: {e}")
        
        progress["stock_index"][step_name] = i + len(batch)
        save_progress(progress)
        logger.info(f"[{step_name}] {min(i+chunk_size, total_stocks)}/{total_stocks} stocks, {total} rows")
    
    mark_done(step_name)
    return total

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    logger.info("=" * 70)
    logger.info("PULL V3.0 — Full data collection for all available tables")
    logger.info(f"Started: {datetime.now().isoformat()}")
    logger.info(f"API URL: {config.tushare_api_url}")
    logger.info(f"DB: {config.postgres['host']}:{config.postgres['port']}/{config.postgres['database']}")
    logger.info(f"Completed steps so far: {progress.get('completed_steps', [])}")
    logger.info("=" * 70)
    
    # Get stock list
    ALL_STOCKS = get_all_stocks()
    logger.info(f"Total stocks to process: {len(ALL_STOCKS)}")
    
    stats = {}
    
    # ── Phase 1: Core Market Data (highest priority) ──────────
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 1: Core Market Data")
    logger.info("=" * 60)
    
    stats["daily"] = pull_for_stocks(
        ALL_STOCKS,
        lambda ts: collector.get_daily(ts, start_date=START, end_date=END),
        "stock_daily", "daily_all"
    )
    
    stats["daily_basic"] = pull_for_stocks(
        ALL_STOCKS,
        lambda ts: collector.get_daily_basic(ts_code=ts, start_date=START, end_date=END),
        "stock_daily_basic", "daily_basic_all"
    )
    
    stats["adj_factor"] = pull_for_stocks(
        ALL_STOCKS,
        lambda ts: collector.get_adj_factor(ts_code=ts),
        "stock_adj_factor", "adj_factor_all"
    )
    
    # ── Phase 2: Extended Market Data ─────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 2: Extended Market Data")
    logger.info("=" * 60)
    
    stats["stk_limit"] = pull_for_stocks(
        ALL_STOCKS,
        lambda ts: collector.get_stk_limit(ts_code=ts, start_date=START, end_date=END),
        "ref_stk_limit_daily", "stk_limit_all"
    )
    
    stats["monthly"] = pull_for_stocks(
        ALL_STOCKS,
        lambda ts: collector._call_api("monthly", "monthly", ts_code=ts, start_date=START, end_date=END),
        "stock_monthly", "monthly_all"
    )
    
    stats["weekly"] = pull_for_stocks(
        ALL_STOCKS,
        lambda ts: collector._call_api("weekly", "weekly", ts_code=ts, start_date=START, end_date=END),
        "stock_weekly", "weekly_all"
    )
    
    stats["moneyflow"] = pull_for_stocks(
        ALL_STOCKS,
        lambda ts: collector.get_moneyflow(ts_code=ts, start_date=START, end_date=END),
        "stock_moneyflow_daily", "moneyflow_all"
    )
    
    # ── Phase 3: Financial Data ───────────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 3: Financial Statements")
    logger.info("=" * 60)
    
    fin_tables = [
        ("income", "fin_income", lambda ts: collector.get_income(ts, FIN_START, FIN_END)),
        ("balancesheet", "fin_balancesheet", lambda ts: collector.get_balancesheet(ts, FIN_START, FIN_END)),
        ("cashflow", "fin_cashflow", lambda ts: collector.get_cashflow(ts, FIN_START, FIN_END)),
        ("fina_indicator", "fin_fina_indicator", lambda ts: collector.get_fina_indicator(ts, FIN_START, FIN_END)),
        ("dividend", "fin_dividend", lambda ts: collector.get_dividend(ts_code=ts, start_date=FIN_START, end_date=FIN_END)),
        ("forecast", "fin_forecast", lambda ts: collector.get_forecast(ts, FIN_START, FIN_END)),
        ("express", "fin_express", lambda ts: collector.get_express(ts_code=ts, start_date=FIN_START, end_date=FIN_END)),
    ]
    
    for api_name, table_name, fetch_fn in fin_tables:
        stats[f"fin_{api_name}"] = pull_for_stocks(
            ALL_STOCKS, fetch_fn, table_name, f"fin_{api_name}_all"
        )
    
    # ── Phase 4: Index / ETF / Fund ───────────────────────────
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 4: Index / ETF / Fund Data")
    logger.info("=" * 60)
    
    # Index daily
    IDX_CODES = ["000001.SH","399001.SZ","399006.SZ","000688.SH","000300.SH",
                 "000016.SH","000905.SH","399005.SZ","399673.SZ","000852.SH"]
    stats["index_daily"] = pull_for_stocks(
        IDX_CODES,
        lambda ix: collector._call_api("index_daily","index_daily",ts_code=ix,start_date=START,end_date=END),
        "index_daily", "index_daily_all", chunk_size=5
    )
    
    # Fund basic + ETF daily
    ETF_CODES = ["510050.SH","510300.SH","510500.SH","159919.SZ","512880.SH",
                 "510880.SH","512100.SH","159915.SZ","588000.SH","513100.SH"]
    stats["etf_daily"] = pull_for_stocks(
        ETF_CODES,
        lambda etf: collector._call_api("fund_daily","fund_daily",ts_code=etf,start_date=START,end_date=END),
        "etf_daily", "etf_daily_all", chunk_size=5
    )
    
    stats["fund_nav"] = pull_for_stocks(
        ETF_CODES,
        lambda etf: collector._call_api("fund_nav","fund_nav",ts_code=etf,start_date=START,end_date=END),
        "fund_nav", "fund_nav_all", chunk_size=5
    )
    
    # ── Phase 5: Reference data (single calls) ────────────────
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 5: Reference / Metadata")
    logger.info("=" * 60)
    
    stats["suspend_d"] = pull_standalone(
        lambda: collector.get_suspend_d(),
        "ref_suspend_d", "suspend_d_all"
    )
    
    stats["trade_cal"] = pull_standalone(
        lambda: collector._call_api("trade_cal","trade_cal",exchange="SSE",start_date="20100101",end_date="20261231"),
        "ref_trade_cal", "trade_cal_all"
    )
    
    stats["new_share"] = pull_standalone(
        lambda: collector._call_api("new_share","new_share",start_date="20100101",end_date="20261231"),
        "ref_new_share", "new_share_all"
    )
    
    stats["namechange"] = pull_standalone(
        lambda: collector._call_api("namechange","namechange"),
        "ref_stock_namechange", "namechange_all"
    )
    
    # ── Phase 6: Advanced (margin, auction) ──────────────────
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 6: Margin & Auction Data")
    logger.info("=" * 60)
    
    # Margin summary (daily batch)
    if not step_done("margin_summary_all"):
        total_m = 0
        try:
            for year in [2021, 2022, 2023, 2024, 2025, 2026]:
                for month in range(1, 13):
                    ds = f"{year}{month:02d}01"
                    de = f"{year}{month:02d}28"
                    time.sleep(DELAY)
                    df = adv.get_margin(start=ds, end=de)
                    if df is not None and not df.empty:
                        n = safe_save(df, "margin_summary_daily")
                        total_m += n
            logger.info(f"[margin_summary] {total_m} rows")
        except Exception as e:
            logger.error(f"[margin_summary] ERROR: {e}")
        mark_done("margin_summary_all")
        stats["margin_summary"] = total_m
    else:
        stats["margin_summary"] = "skipped"
    
    # Auction data for top stocks (high volume data, do sample)
    if not step_done("auction_sample"):
        total_a = 0
        sample_stocks = ALL_STOCKS[:100]  # First 100 stocks for auction
        for ts in sample_stocks:
            time.sleep(DELAY)
            try:
                df = collector.get_mins(ts_code=ts, freq="auction", trade_date=datetime.now().strftime("%Y%m%d"))
                if df is not None and not df.empty:
                    n = safe_save(df, "stock_auction_daily")
                    total_a += n
            except Exception as e:
                logger.error(f"[auction] {ts} ERROR: {e}")
        mark_done("auction_sample")
        stats["auction"] = total_a
        logger.info(f"[auction_sample] {total_a} rows from {len(sample_stocks)} stocks")
    else:
        stats["auction"] = "skipped"
    
    # ── Final Summary ─────────────────────────────────────────
    logger.info("\n" + "=" * 70)
    logger.info("PULL V3.0 COMPLETE")
    logger.info("=" * 70)
    
    # Table stats
    tables_check = [
        "stock_daily", "stock_daily_basic", "stock_adj_factor", "stock_monthly",
        "stock_weekly", "stock_moneyflow_daily", "ref_stk_limit_daily",
        "fin_income", "fin_balancesheet", "fin_cashflow", "fin_fina_indicator",
        "fin_dividend", "fin_forecast", "fin_express",
        "index_daily", "etf_daily", "fund_nav",
        "ref_suspend_d", "ref_trade_cal", "ref_new_share", "ref_stock_namechange",
        "margin_summary_daily", "stock_auction_daily", "ref_stock_basic"
    ]
    
    logger.info("\nFinal Table Summary:")
    total_all = 0
    for tbl in tables_check:
        try:
            cnt = pd.read_sql(f'SELECT COUNT(*) as n FROM "{tbl}"', engine)
            n = int(cnt.iloc[0,0])
            total_all += int(n)
            logger.info(f"  {tbl:<30s} {n:>10,} rows")
        except:
            logger.info(f"  {tbl:<30s} {'N/A':>10s}")
    
    logger.info(f"\n  TOTAL: {total_all:,} rows")
    logger.info(f"  Completed steps: {progress['completed_steps']}")
    logger.info(f"  Finished at: {datetime.now().isoformat()}")

if __name__ == "__main__":
    main()
