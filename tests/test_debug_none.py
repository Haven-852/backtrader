# -*- coding: utf-8 -*-
"""Quick debug: test NONE-returning interfaces with different params"""
import sys, os, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_layer.config import config
from data_layer.collectors.tushare_collector import TushareCollector

collector = TushareCollector()

# Test each NONE interface with multiple param combos
tests = [
    # daily_basic - try with ts_code
    ("daily_basic", {"ts_code": "000001.SZ", "trade_date": "20260509"}),
    ("daily_basic", {"ts_code": "000001.SZ", "start_date": "20260501", "end_date": "20260509"}),
    # adj_factor
    ("adj_factor", {"ts_code": "000001.SZ", "start_date": "20260501", "end_date": "20260509"}),
    ("adj_factor", {"ts_code": "000001.SZ"}),
    # stk_limit  
    ("stk_limit", {"ts_code": "000001.SZ", "start_date": "20260501", "end_date": "20260509"}),
    # forecast
    ("forecast", {"ts_code": "000001.SZ", "ann_date": "20250501"}),
    ("forecast", {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20250501"}),
    # express
    ("express", {"ts_code": "000001.SZ", "ann_date": "20250501"}),
    ("express", {"ts_code": "000001.SZ", "start_date": "20240101", "end_date": "20250501"}),
    # dividend
    ("dividend", {"ts_code": "000001.SZ"}),
    ("dividend", {"ts_code": "000001.SZ", "ann_date": "20250501"}),
    # stk_mins (retry with different freq and date)
    ("stk_mins", {"ts_code": "000001.SZ", "freq": "60min", "trade_date": "20260508"}),
    ("stk_mins", {"ts_code": "000001.SZ", "freq": "5min", "trade_date": "20260508"}),
    # news/anns (direct API call)
    ("news", {"start_date": "20260501", "end_date": "20260510"}),
    ("anns", {"ts_code": "000001.SZ", "start_date": "20260401", "end_date": "20260510"}),
    ("broker_reports", {"start_date": "20260401", "end_date": "20260510"}),
    ("top_list", {"trade_date": "20260508"}),
    ("block_trade", {"start_date": "20260401", "end_date": "20260510"}),
]

for interface, params in tests:
    time.sleep(0.7)
    try:
        api = collector._get_api(interface)
        if not api:
            print(f"  NO_API  {interface:20s} {params}")
            continue
        method = getattr(api, interface)
        df = method(**params)
        if df is not None and not df.empty:
            print(f"  OK      {interface:20s} {len(df):>5} rows  params={params}")
        elif df is not None:
            print(f"  EMPTY   {interface:20s} params={params}")
        else:
            print(f"  NONE    {interface:20s} params={params}")
    except Exception as e:
        msg = str(e)[:150]
        print(f"  ERROR   {interface:20s} {msg}")
