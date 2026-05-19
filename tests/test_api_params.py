# -*- coding: utf-8 -*-
"""Quick debug: test each API individually to see what's returned"""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from data_layer.collectors.tushare_collector import TushareCollector

c = TushareCollector()
TOKEN = os.getenv("TUSHARE_TOKEN_BASIC")

import tushare as ts
pro = ts.pro_api(TOKEN)
pro._DataApi__http_url = "http://tsy.xiaodefa.cn"

tests = [
    # (api_method_name, params_dict)
    ("daily_basic", {"ts_code": "000001.SZ", "start_date": "20250501", "end_date": "20250510"}),
    ("daily_basic", {"ts_code": "000001.SZ", "trade_date": "20250508"}),
    ("adj_factor", {"ts_code": "000001.SZ"}),
    ("adj_factor", {"ts_code": "000001.SZ", "trade_date": "20250508"}),
    ("income", {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20250101"}),
    ("income", {"ts_code": "000001.SZ", "ann_date": "20240401"}),
    ("income", {"ts_code": "000001.SZ", "end_date": "20241231"}),
    ("balancesheet", {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20250101"}),
    ("cashflow", {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20250101"}),
    ("fina_indicator", {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20250101"}),
    ("dividend", {"ts_code": "000001.SZ", "start_date": "20210101", "end_date": "20260101"}),
    ("moneyflow", {"ts_code": "000001.SZ", "start_date": "20250501", "end_date": "20250510"}),
    ("moneyflow", {"ts_code": "000001.SZ", "trade_date": "20250508"}),
    ("index_daily", {"ts_code": "000001.SH", "start_date": "20250501", "end_date": "20250510"}),
    ("index_daily", {"ts_code": "000001.SH", "trade_date": "20250508"}),
    ("fund_daily", {"ts_code": "510050.SH", "start_date": "20250501", "end_date": "20250510"}),
    ("fund_daily", {"ts_code": "510050.SH", "trade_date": "20250508"}),
    ("suspend_d", {"ts_code": "000001.SZ"}),
    ("suspend_d", {"suspend_type": "S"}),
    ("moneyflow_hsgt", {"start_date": "20250501", "end_date": "20250510"}),
    ("moneyflow_hsgt", {"trade_date": "20250508"}),
    ("stk_limit", {"ts_code": "000001.SZ", "start_date": "20250501", "end_date": "20250510"}),
    ("stk_limit", {"ts_code": "000001.SZ", "trade_date": "20250508"}),
]

print("=" * 80)
print("Testing individual Tushare API calls directly")
print("=" * 80)

for api_name, params in tests:
    time.sleep(0.7)
    try:
        func = getattr(pro, api_name, None)
        if func is None:
            print(f"[{api_name}] {params} -> API METHOD NOT FOUND")
            continue
        
        df = func(**params)
        if df is None:
            print(f"[{api_name}] {params} -> NONE")
        elif df.empty:
            print(f"[{api_name}] {params} -> EMPTY DataFrame")
        else:
            print(f"[{api_name}] {params} -> {len(df)} rows, columns: {list(df.columns)[:8]}...")
            print(f"   Sample: {df.iloc[0].to_dict()}")
    except Exception as e:
        err = str(e)
        if len(err) > 150:
            err = err[:150] + "..."
        print(f"[{api_name}] {params} -> ERROR: {err}")

print("\nDone!")
