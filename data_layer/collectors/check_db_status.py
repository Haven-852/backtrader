# -*- coding: utf-8 -*-
"""Check database status - list all tables and row counts"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv; load_dotenv()
from data_layer.config import config
from data_layer.db_manager import storage_manager
import pandas as pd
from sqlalchemy import text

engine = storage_manager.get_postgres_engine()
if not engine:
    print("ERROR: DB not connected")
    sys.exit(1)

with engine.connect() as conn:
    tables_df = pd.read_sql(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename",
        conn
    )
    
    print("=" * 70)
    print(f"{'Table':<35s} {'Rows':>10s}  {'Sample Data'}")
    print("=" * 70)
    
    total_all = 0
    for tbl in tables_df['tablename']:
        try:
            cnt_df = pd.read_sql(f'SELECT COUNT(*) as n FROM "{tbl}"', conn)
            n = int(cnt_df.iloc[0, 0])
            total_all += n
            
            # Get sample columns and a few rows
            cols_df = pd.read_sql(
                f"SELECT column_name FROM information_schema.columns WHERE table_name = '{tbl}' AND table_schema = 'public' ORDER BY ordinal_position",
                conn
            )
            cols = list(cols_df['column_name'])
            col_preview = ', '.join(cols[:5])
            if len(cols) > 5:
                col_preview += f' ... ({len(cols)} cols total)'
            
            # Sample first 2 rows
            try:
                sample = pd.read_sql(f'SELECT * FROM "{tbl}" LIMIT 2', conn)
                if not sample.empty:
                    sample_text = sample.to_string(max_rows=2, max_cols=4, index=False).replace('\n', ' | ')
                    sample_text = sample_text[:120]
                else:
                    sample_text = '(empty)'
            except:
                sample_text = '(error)'
            
            print(f"  {tbl:<33s} {n:>10,}  [{col_preview}]")
            print(f"    {'':33s} {'':>10s}  {sample_text}")
            
        except Exception as e:
            print(f"  {tbl:<33s} {'ERROR':>10s}  {str(e)[:80]}")
    
    print("=" * 70)
    print(f"  TOTAL                                    {total_all:>10,} rows across {len(tables_df)} tables")
    print("=" * 70)

# Also try to get date ranges for key tables
print("\n" + "=" * 70)
print("KEY TABLE DATE RANGES")
print("=" * 70)

date_tables = [
    ("stock_daily", "trade_date"),
    ("stock_daily_basic", "trade_date"),
    ("stock_daily_factor", "trade_date"),
    ("stock_moneyflow_daily", "trade_date"),
    ("stock_moneyflow_hsgt", "trade_date"),
    ("index_daily", "trade_date"),
    ("etf_daily", "trade_date"),
    ("stk_limit_daily", "trade_date"),
    ("fin_income", "end_date"),
    ("fin_balancesheet", "end_date"),
    ("fin_cashflow", "end_date"),
    ("fin_fina_indicator", "end_date"),
    ("fin_dividend", "end_date"),
    ("fin_forecast", "end_date"),
    ("fin_express", "end_date"),
]

with engine.connect() as conn:
    for tbl, date_col in date_tables:
        try:
            r = pd.read_sql(f'SELECT COUNT(*) as n, MIN("{date_col}") as min_d, MAX("{date_col}") as max_d FROM "{tbl}"', conn)
            if not r.empty and r.iloc[0,0] > 0:
                print(f"  {tbl:<30s} {date_col:<15s} {r.iloc[0,'min_d']} ~ {r.iloc[0,'max_d']}  ({r.iloc[0,0]:,} rows)")
        except Exception as e:
            # table doesn't exist, skip
            pass

print("\nDone!")
