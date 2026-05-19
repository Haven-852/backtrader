# -*- coding: utf-8 -*-
"""
Resume fin_forecast from stock_index 2100
"""
import sys, os, time, logging, json
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv; load_dotenv()
import pandas as pd
import numpy as np

from data_layer.db_manager import storage_manager
from data_layer.collectors.tushare_collector import TushareCollector

DELAY = 0.75
FIN_START = "20210101"
FIN_END = "20251231"
PROGRESS_FILE = "pull_v3_progress.json"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [resume] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler("resume_forecast.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('resume')

collector = TushareCollector()
engine = storage_manager.get_postgres_engine()

def load_progress():
    with open(PROGRESS_FILE, 'r') as f:
        return json.load(f)

def save_progress(p):
    p["last_update"] = datetime.now().isoformat()
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(p, f, indent=2)

def safe_save(df, table_name):
    if df is None or df.empty:
        return 0
    try:
        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.where(pd.notnull(df), None)
        try:
            df.to_sql(table_name, engine, if_exists="append", index=False, method="multi", chunksize=500)
            return len(df)
        except Exception:
            count = 0
            for _, row in df.iterrows():
                try:
                    pd.DataFrame([row]).to_sql(table_name, engine, if_exists="append", index=False, method=None)
                    count += 1
                except Exception:
                    pass
            return count
    except Exception as e:
        logger.error(f"Save FAILED: {str(e)[:150]}")
        return 0

progress = load_progress()

# Get stocks
stocks_df = pd.read_sql("SELECT DISTINCT ts_code FROM ref_stock_basic ORDER BY ts_code", engine)
stocks = list(stocks_df['ts_code'])
logger.info(f"Total stocks: {len(stocks)}")

step_name = "fin_forecast_all"
table_name = "fin_forecast"

if step_name in progress.get("completed_steps", []):
    logger.info(f"[SKIP] {step_name} already completed")
    sys.exit(0)

start_idx = progress["stock_index"].get(step_name, 0)
total_stocks = len(stocks)
total = 0

logger.info(f"[{step_name}] Resuming from stock index {start_idx}/{total_stocks}")
logger.info(f"[{step_name}] Target table: {table_name}")

errors = 0
for i in range(start_idx, total_stocks):
    ts = stocks[i]
    time.sleep(DELAY)
    try:
        df = collector.get_forecast(ts, FIN_START, FIN_END)
        n = safe_save(df, table_name)
        total += n
        if (i + 1) % 50 == 0 or (i + 1) == total_stocks:
            logger.info(f"[{step_name}] {i+1}/{total_stocks} stocks, {total} rows | {ts} / errors: {errors}")
    except Exception as e:
        errors += 1
        msg = str(e)[:120]
        logger.error(f"[{step_name}] {ts} ERROR: {msg}")
        if errors > 20:
            logger.critical(f"Too many errors ({errors}), stopping")

    if (i + 1) % 50 == 0:
        progress["stock_index"][step_name] = i + 1
        save_progress(progress)

# Mark as complete
progress["completed_steps"].append(step_name)
save_progress(progress)
logger.info(f"[{step_name}] COMPLETE: {total} rows from {total_stocks} stocks, errors: {errors}")
