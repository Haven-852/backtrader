# -*- coding: utf-8 -*-
"""Detailed data inventory v2"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv; load_dotenv()
from data_layer.db_manager import storage_manager
import pandas as pd

engine = storage_manager.get_postgres_engine()

def safe_sql(query):
    return pd.read_sql(query, engine)

with engine.connect() as conn:
    # 1. stock_daily details
    print("=" * 70)
    print("1. stock_daily (日线行情)")
    print("=" * 70)
    r = safe_sql("SELECT MIN(trade_date) min_d, MAX(trade_date) max_d, COUNT(DISTINCT ts_code) stocks, COUNT(*) total FROM stock_daily")
    if not r.empty and r['min_d'].iloc[0] is not None:
        print(f"  日期范围: {r['min_d'].iloc[0]} ~ {r['max_d'].iloc[0]}")
        print(f"  股票数量: {r['stocks'].iloc[0]}")
        print(f"  总行数:   {r['total'].iloc[0]:,}")
        
        stocks = safe_sql("SELECT ts_code, COUNT(*) n, MIN(trade_date) min_d, MAX(trade_date) max_d FROM stock_daily GROUP BY ts_code ORDER BY ts_code")
        print(f"\n  股票明细:")
        for _, row in stocks.iterrows():
            print(f"    {row['ts_code']:<15s} {row['n']:>5} rows  {row['min_d']} ~ {row['max_d']}")
    else:
        print("  (empty)")
    
    # 2. stock_daily_basic
    print("\n" + "=" * 70)
    print("2. stock_daily_basic (每日指标)")
    print("=" * 70)
    r = safe_sql("SELECT MIN(trade_date) min_d, MAX(trade_date) max_d, COUNT(DISTINCT ts_code) stocks, COUNT(*) total FROM stock_daily_basic")
    if not r.empty and r['min_d'].iloc[0] is not None:
        print(f"  日期范围: {r['min_d'].iloc[0]} ~ {r['max_d'].iloc[0]}")
        print(f"  股票数量: {r['stocks'].iloc[0]}")
        print(f"  总行数:   {r['total'].iloc[0]:,}")
        cols = safe_sql("SELECT * FROM stock_daily_basic LIMIT 0")
        print(f"  字段 ({len(cols.columns)}): {', '.join(list(cols.columns))}")
    else:
        print("  (empty)")
    
    # 3. ref_stock_basic
    print("\n" + "=" * 70)
    print("3. ref_stock_basic (股票基本信息)")
    print("=" * 70)
    r = safe_sql("SELECT COUNT(*) n FROM ref_stock_basic")
    print(f"  股票总数: {r['n'].iloc[0]:,}")
    ind = safe_sql("SELECT industry, COUNT(*) n FROM ref_stock_basic GROUP BY industry ORDER BY n DESC LIMIT 15")
    print(f"  前15大行业:")
    for _, row in ind.iterrows():
        print(f"    {row['industry']:<20s} {row['n']:>5} 只")
    
    # 4. Check all tables
    print("\n" + "=" * 70)
    print("4. 完整表清单及行数")
    print("=" * 70)
    tables_df = safe_sql("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    
    total_rows = 0
    has_data = []
    no_data = []
    for tbl in tables_df['tablename']:
        try:
            n = int(safe_sql(f'SELECT COUNT(*) as n FROM "{tbl}"')['n'].iloc[0])
            total_rows += n
            if n > 0:
                has_data.append((tbl, n))
            else:
                no_data.append(tbl)
        except Exception as e:
            no_data.append(f"{tbl} (err)")
    
    print(f"  有数据的表 ({len(has_data)} 个):")
    for tbl, n in has_data:
        print(f"    ✅ {tbl:<35s} {n:>10,} rows")
    
    print(f"\n  空表/未采集 ({len(no_data)} 个):")
    for tbl in no_data:
        print(f"    ❌ {tbl}")
    
    print(f"\n  总行数: {total_rows:,} across {len(tables_df)} tables")

print("\nDone!")
