# -*- coding: utf-8 -*-
"""Quick test: APIs that were untested due to missing collector mappings"""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv; load_dotenv()
from data_layer.config import config
import tushare as ts

token = config.get_tushare_token("basic")
ts.set_token(token)
pro = ts.pro_api()
pro._DataApi__http_url = config.tushare_api_url

tests = [
    ("monthly",      "monthly",      {"ts_code":"000001.SZ","start_date":"20210101","end_date":"20260101"}, "月线"),
    ("weekly",       "weekly",       {"ts_code":"000001.SZ","start_date":"20210101","end_date":"20260101"}, "周线"),
    ("trade_cal",    "trade_cal",    {"exchange":"SSE","start_date":"20210101","end_date":"20251231"}, "交易日历"),
    ("new_share",    "new_share",    {"start_date":"20210101","end_date":"20251231"}, "新股上市"),
    ("namechange",   "namechange",   {"ts_code":"000001.SZ"}, "股票曾用名"),
    ("index_weight", "index_weight", {"index_code":"000300.SH","trade_date":"20260508"}, "指数权重"),
]

print("Testing unmapped APIs...")
for api_name, method_name, params, desc in tests:
    time.sleep(0.7)
    try:
        method = getattr(pro, method_name, None)
        if method:
            df = method(**params)
            if df is not None and not df.empty:
                print(f"  ✅ {api_name:<20s} {desc:<12s} -> {len(df):>6} rows")
            elif df is not None:
                print(f"  ⚠️  {api_name:<20s} {desc:<12s} -> empty DataFrame")
            else:
                print(f"  ⚠️  {api_name:<20s} {desc:<12s} -> None")
        else:
            print(f"  ❌ {api_name:<20s} {desc:<12s} -> API method not found on pro_api")
    except Exception as e:
        print(f"  ❌ {api_name:<20s} {desc:<12s} -> {str(e)[:100]}")

print("Done!")
